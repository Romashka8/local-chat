from __future__ import annotations

from contextlib import asynccontextmanager

import chainlit as cl
from chainlit.server import app, router
from chainlit.types import ThreadDict
from fastapi import HTTPException
from fastapi.responses import FileResponse

from local_agent_chat.bootstrap import create_application
from local_agent_chat.chainlit_data import create_chainlit_data_layer
from local_agent_chat.chainlit_uploads import persist_message_uploads
from local_agent_chat.local_storage import LocalStorageClient
from local_agent_chat.runtime import ChatBinding


application = create_application()
storage = LocalStorageClient(application.settings.blobs_dir)
chainlit_layer = create_chainlit_data_layer(
    application.settings.chainlit_db,
    storage,
)


@cl.data_layer
def data_layer():
    return chainlit_layer


@cl.header_auth_callback
async def local_user(_headers):
    """Single-user local-development authentication.

    This deliberately trusts every request that reaches the Chainlit process.
    Keep the development server bound to localhost. Replace this callback with
    real proxy/OAuth/password authentication before exposing the app remotely.
    """
    return cl.User(
        identifier="local-user",
        metadata={
            "role": "local",
            "provider": "local-header",
        },
    )


@cl.set_chat_profiles
async def chat_profiles(_user):
    """Expose registered application agents as immutable per-chat profiles."""
    return [
        cl.ChatProfile(
            name=profile.id,
            display_name=profile.label,
            markdown_description=profile.description,
            default=profile.default,
        )
        for profile in application.registry.profiles()
    ]


def _current_user_id() -> str:
    user = cl.user_session.get("user")
    identifier = getattr(user, "identifier", None)
    if not identifier:
        raise RuntimeError("Authenticated Chainlit user is not available")
    return str(identifier)


def _require_agent(agent_id: str) -> str:
    if not application.registry.has(agent_id):
        raise RuntimeError(f"Chat references an unavailable agent: {agent_id!r}")
    return agent_id


def _new_chat_agent_id() -> str:
    selected = cl.user_session.get("chat_profile")
    if selected is None:
        return application.registry.default_id
    return _require_agent(str(selected))


def _binding(
    *,
    chat_id: str,
    agent_id: str,
    memory_thread_id: str | None = None,
) -> ChatBinding:
    return ChatBinding(
        user_id=_current_user_id(),
        chat_id=chat_id,
        agent_id=_require_agent(agent_id),
        memory_thread_id=memory_thread_id or chat_id,
    )


def _store_binding(binding: ChatBinding) -> None:
    # Keep only JSON-serializable application values in Chainlit's user session.
    # The selected Chainlit chat_profile is UI state; agent_id is our persisted
    # application binding and remains authoritative after a chat is created.
    cl.user_session.set("agent_id", binding.agent_id)
    cl.user_session.set("memory_thread_id", binding.memory_thread_id)


# Analyzer tool selector: UI actions are per-chat and never affect General.
TOOL_LABELS = {
    "inspect_dataset": "Структура датасета",
    "build_eda_report": "HTML EDA-отчёт",
    "list_knowledge_documents": "Документы базы знаний",
    "search_knowledge": "Поиск по документам",
    "list_chat_files": "Список файлов чата",
    "read_chat_file": "Чтение файла",
    "search_past_chats": "Поиск по прошлым чатам",
    "read_past_chat": "Чтение прошлого диалога",
}


def _selected_tool_for_binding(binding: ChatBinding) -> str | None:
    """Read the validated mode for this request. None = normal automatic tools."""
    if binding.agent_id != "analyst":
        return None
    selected = cl.user_session.get("analyst_selected_tool", "auto")
    if selected == "auto" or selected is None:
        return None
    if selected not in application.registry.available_tools("analyst"):
        return None
    return str(selected)


def _analyst_tool_action() -> list[cl.Action]:
    return [
        cl.Action(
            name="analyst_open_tools",
            label="🛠 Инструменты",
            tooltip="Выбрать доступный инструмент Analyst",
            payload={},
        )
    ]


def _analyst_tool_menu() -> list[cl.Action]:
    tool_names = application.registry.available_tools("analyst")
    options = [("auto", "Автоматически (все инструменты)")]
    options.extend((name, TOOL_LABELS.get(name, name)) for name in tool_names)
    return [
        cl.Action(
            name="analyst_select_tool",
            label=label,
            tooltip=f"Режим: {name}",
            payload={"tool": name},
        )
        for name, label in options
    ]


def _analyst_tool_label(name: str | None) -> str:
    if name is None or name == "auto":
        return "Автоматически (все инструменты)"
    return TOOL_LABELS.get(name, name)


def _analyst_selected_from_metadata(metadata: dict) -> str:
    name = metadata.get("analyst_selected_tool", "auto")
    if isinstance(name, str) and (name == "auto" or name in application.registry.available_tools("analyst")):
        return name
    return "auto"


def _require_analyst_chat() -> ChatBinding | None:
    binding = _current_binding()
    return binding if binding.agent_id == "analyst" else None


def _message_with_uploaded_files(text: str, filenames: tuple[str, ...]) -> str:
    if not filenames:
        return text

    attachment_note = "\n".join(
        [
            "Files are already attached to THIS message and stored in the current chat.",
            "Do not ask the user to upload them again. If the request depends on their",
            "contents, use list_chat_files/read_chat_file before answering:",
            *(f"- {name}" for name in filenames),
        ]
    )
    stripped = text.strip()
    if stripped:
        return f"{stripped}\n\n{attachment_note}"
    return attachment_note


def _current_binding() -> ChatBinding:
    chat_id = cl.context.session.thread_id
    agent_id = cl.user_session.get("agent_id")
    memory_thread_id = cl.user_session.get("memory_thread_id") or chat_id

    if agent_id is None:
        raise RuntimeError("Chat agent binding is not initialized")

    return _binding(
        chat_id=chat_id,
        agent_id=str(agent_id),
        memory_thread_id=str(memory_thread_id),
    )


@cl.on_chat_start
async def on_chat_start() -> None:
    binding = _binding(
        chat_id=cl.context.session.thread_id,
        agent_id=_new_chat_agent_id(),
    )
    _store_binding(binding)
    cl.user_session.set("analyst_selected_tool", "auto")

    profile = application.registry.profile(binding.agent_id)
    await cl.Message(
        content=f"Чат запущен. Агент: **{profile.label}**.",
        actions=_analyst_tool_action() if binding.agent_id == "analyst" else [],
    ).send()


@cl.on_chat_resume
async def on_chat_resume(thread: ThreadDict) -> None:
    """Reconnect a persisted Chainlit thread to its immutable agent binding."""
    metadata = thread.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}

    chat_id = str(thread["id"])
    persisted_agent = metadata.get("agent_id")

    # Legacy chats created before AgentDefinition v2 have no agent metadata and
    # are attached to the default agent. Unknown persisted ids fail explicitly
    # instead of silently changing the behaviour of an existing conversation.
    agent_id = (
        application.registry.default_id
        if persisted_agent is None
        else _require_agent(str(persisted_agent))
    )

    binding = _binding(
        chat_id=chat_id,
        agent_id=agent_id,
        memory_thread_id=str(metadata.get("memory_thread_id") or chat_id),
    )
    _store_binding(binding)
    if binding.agent_id == "analyst":
        cl.user_session.set(
            "analyst_selected_tool", _analyst_selected_from_metadata(metadata)
        )
    else:
        cl.user_session.set("analyst_selected_tool", "auto")


@cl.action_callback("analyst_open_tools")
async def analyst_open_tools(_action: cl.Action) -> None:
    # Check the authoritative agent binding, never trust button payloads.
    binding = _require_analyst_chat()
    if binding is None:
        return
    selected = _selected_tool_for_binding(binding)
    await cl.Message(
        content=(
            "**Инструменты Analyst**\n\n"
            f"Текущий режим: **{_analyst_tool_label(selected)}**. "
            "Выбери инструмент для следующих сообщений. "
            "В ручном режиме LLM получит только выбранный tool."
        ),
        actions=_analyst_tool_menu(),
    ).send()


@cl.action_callback("analyst_select_tool")
async def analyst_select_tool(action: cl.Action) -> None:
    binding = _require_analyst_chat()
    if binding is None:
        return
    selection = action.payload.get("tool")
    allowed = {"auto", *application.registry.available_tools("analyst")}
    if not isinstance(selection, str) or selection not in allowed:
        await cl.Message(content="Недоступный инструмент Analyst.").send()
        return

    cl.user_session.set("analyst_selected_tool", selection)
    await chainlit_layer.update_thread(
        binding.chat_id,
        metadata={
            "agent_id": binding.agent_id,
            "memory_thread_id": binding.memory_thread_id,
            "analyst_selected_tool": selection,
        },
    )
    await cl.Message(
        content=(
            f"Режим Analyst: **{_analyst_tool_label(selection)}**. "
            "Напиши следующий запрос; для возврата к автоматическому "
            "режиму нажми «Инструменты»."
        ),
        actions=_analyst_tool_action(),
    ).send()


@cl.on_message
async def on_message(message: cl.Message) -> None:
    binding = _current_binding()

    # The first persisted user message creates the UI thread. Store the agent
    # binding alongside it so resume never depends on the currently selected
    # profile in the browser.
    await chainlit_layer.update_thread(
        binding.chat_id,
        metadata={
            "agent_id": binding.agent_id,
            "memory_thread_id": binding.memory_thread_id,
            "analyst_selected_tool": (
                cl.user_session.get("analyst_selected_tool", "auto")
                if binding.agent_id == "analyst" else "auto"
            ),
        },
    )

    uploaded_files = await persist_message_uploads(
        chat_id=binding.chat_id,
        elements=message.elements,
        sandbox=application.files,
        storage=storage,
        session_files=getattr(cl.context.session, "files", None),
    )

    request_text = _message_with_uploaded_files(
        message.content,
        tuple(item.name for item in uploaded_files),
    )

    artifacts_before = {
        item.name
        for item in await application.artifacts.list_artifacts(binding.chat_id)
    }

    response = await application.runtime.run(
        binding=binding,
        text=request_text,
        selected_tool=_selected_tool_for_binding(binding),
    )

    artifacts_after = await application.artifacts.list_artifacts(binding.chat_id)
    new_artifacts = [
        item for item in artifacts_after if item.name not in artifacts_before
    ]
    elements = [cl.File(name=item.name, path=str(item.path)) for item in new_artifacts]

    await cl.Message(
        content=response,
        elements=elements,
    ).send()


async def cleanup_chat(chat_id: str) -> None:
    await application.delete_chat(chat_id)


chainlit_layer.chat_cleanup = cleanup_chat


@router.get("/files/{object_key:path}", include_in_schema=False)
async def local_file(object_key: str):
    try:
        path = storage.path_for(object_key)
    except ValueError as error:
        raise HTTPException(status_code=404) from error

    if not path.is_file():
        raise HTTPException(status_code=404)

    return FileResponse(
        path,
        media_type=storage.media_type(object_key),
        filename=path.name,
    )


# Chainlit registers its SPA fallback before loading the user module. Keep this
# route ahead of that catch-all so persisted element URLs return file bytes.
_local_file_route = router.routes.pop()
router.routes.insert(0, _local_file_route)


async def close_resources() -> None:
    await application.close()
    await storage.close()


# Chainlit owns the ASGI lifespan. Wrap it instead of replacing it so our
# application resources are closed after Chainlit finishes its own shutdown.
if getattr(app.state, "_agent_chat_base_lifespan", None) is None:
    app.state._agent_chat_base_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def agent_chat_lifespan(chainlit_app):
        try:
            async with chainlit_app.state._agent_chat_base_lifespan(
                chainlit_app
            ) as state:
                yield state
        finally:
            cleanup = getattr(chainlit_app.state, "_agent_chat_close_resources", None)

            if cleanup is not None:
                await cleanup()

    app.router.lifespan_context = agent_chat_lifespan


app.state._agent_chat_close_resources = close_resources
