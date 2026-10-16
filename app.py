from __future__ import annotations

from contextlib import asynccontextmanager

from chainlit.input_widget import Select, Slider, Switch

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
    settings = _get_analyst_settings()
    if settings["auto"]:
        return None
    selected = settings["tool"]
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


# Analyst-specific settings live in the UI session and are persisted as thread
# metadata. They never modify General or mutate a shared graph's settings.
DEFAULT_ANALYST_SETTINGS = {
    "tool": "inspect_dataset",
    "auto": True,
    "steps": True,
    "temperature": 0.2,
}


def _clean_analyst_settings(data: dict | None) -> dict:
    data = data if isinstance(data, dict) else {}
    available = set(application.registry.available_tools("analyst"))
    tool = data.get("tool", DEFAULT_ANALYST_SETTINGS["tool"])
    if tool not in available:
        tool = next(iter(application.registry.available_tools("analyst")), "")
    raw_temp = data.get("temperature", 0.2)
    try:
        temperature = round(float(raw_temp), 1)
    except (TypeError, ValueError, OverflowError):
        temperature = 0.2
    if not 0 <= temperature <= 1:
        temperature = 0.2
    return {
        "tool": tool,
        "auto": data.get("auto", True) if isinstance(data.get("auto", True), bool) else True,
        "steps": data.get("steps", True) if isinstance(data.get("steps", True), bool) else True,
        "temperature": temperature,
    }


def _get_analyst_settings() -> dict:
    return _clean_analyst_settings(cl.user_session.get("analyst_settings"))


async def _send_analyst_settings() -> None:
    data = _get_analyst_settings()
    names = application.registry.available_tools("analyst")
    await cl.ChatSettings([
        Select(
            id="analyst_tool",
            label="Analyst — Инструмент",
            values=list(names),
            initial_value=data["tool"],
        ),
        Switch(id="analyst_auto", label="Автоматический выбор tools", initial=data["auto"]),
        Switch(id="analyst_steps", label="Показывать вызовы инструментов", initial=data["steps"]),
        Slider(
            id="analyst_temperature", label="Temperature",
            min=0, max=1, step=0.1, initial=data["temperature"],
        ),
    ]).send()


def _new_analyst_panel() -> cl.CustomElement:
    # JSX is loaded by Chainlit from public/elements/AnalystPanel.jsx.
    # The server owns the canonical settings; the browser sends only candidate values.
    return cl.CustomElement(
        name="AnalystPanel",
        display="inline",
        props={
            "settings": _get_analyst_settings(),
            "tools": [
                {"value": name, "label": TOOL_LABELS.get(name, name)}
                for name in application.registry.available_tools("analyst")
            ],
        },
    )


async def _send_analyst_panel() -> None:
    panel = _new_analyst_panel()
    await cl.Message(
        content="**Панель Analyst** — выбери инструмент и параметры, затем нажми «Применить».",
        elements=[panel],
        actions=_analyst_tool_action(),
    ).send()
    cl.user_session.set("analyst_panel", panel)


async def _refresh_analyst_panel() -> None:
    panel = cl.user_session.get("analyst_panel")
    if panel is not None:
        panel.props["settings"] = _get_analyst_settings()
        await panel.update()


@cl.action_callback("analyst_save_panel")
async def analyst_save_panel(action: cl.Action) -> None:
    binding = _require_analyst_chat()
    if binding is None:
        return
    if not isinstance(action.payload, dict):
        return
    requested = action.payload
    # Do not accept arbitrary model/tool IDs or settings from the browser.
    if requested.get("tool") not in application.registry.available_tools("analyst"):
        await cl.Message(content="Неизвестный инструмент Analyst.").send()
        return
    if not isinstance(requested.get("auto"), bool) or not isinstance(requested.get("steps"), bool):
        await cl.Message(content="Некорректные параметры Analyst.").send()
        return
    data = _clean_analyst_settings(requested)
    cl.user_session.set("analyst_settings", data)
    await _persist_analyst_settings(binding)
    await _refresh_analyst_panel()
    await _send_analyst_settings()
    await cl.Message(
        content=(
            "Настройки Analyst применены: "
            f"режим **{'Auto' if data['auto'] else _analyst_tool_label(data['tool'])}**, "
            f"шаги **{'включены' if data['steps'] else 'скрыты'}**, "
            f"temperature **{data['temperature']:.1f}**."
        ),
    ).send()


async def _persist_analyst_settings(binding: ChatBinding) -> None:
    await chainlit_layer.update_thread(
        binding.chat_id,
        metadata={
            "agent_id": binding.agent_id,
            "memory_thread_id": binding.memory_thread_id,
            "analyst_settings": _get_analyst_settings(),
            "analyst_selected_tool": (
                "auto" if _get_analyst_settings()["auto"]
                else _get_analyst_settings()["tool"]
            ),
        },
    )


@cl.on_settings_update
async def on_settings_update(values: dict) -> None:
    binding = _current_binding()
    if binding.agent_id != "analyst":
        return
    data = _clean_analyst_settings({
        "tool": values.get("analyst_tool"),
        "auto": values.get("analyst_auto"),
        "steps": values.get("analyst_steps"),
        "temperature": values.get("analyst_temperature"),
    })
    cl.user_session.set("analyst_settings", data)
    await _persist_analyst_settings(binding)
    await _refresh_analyst_panel()


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
    if binding.agent_id == "analyst":
        cl.user_session.set("analyst_settings", dict(DEFAULT_ANALYST_SETTINGS))
        await _send_analyst_settings()

    profile = application.registry.profile(binding.agent_id)
    await cl.Message(content=f"Чат запущен. Агент: **{profile.label}**.").send()
    if binding.agent_id == "analyst":
        await _send_analyst_panel()


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
        legacy = _analyst_selected_from_metadata(metadata)
        defaults = dict(DEFAULT_ANALYST_SETTINGS)
        if legacy != "auto":
            defaults.update(tool=legacy, auto=False)
        defaults.update(metadata.get("analyst_settings") or {})
        cl.user_session.set("analyst_settings", _clean_analyst_settings(defaults))
        await _send_analyst_settings()
        # Reuse a persisted panel if Chainlit supplied its metadata on resume.
        # Avoid adding a duplicate card each time the thread is reopened.
        restored = False
        for item in reversed(thread.get("elements") or []):
            if not isinstance(item, dict):
                continue
            if item.get("type") != "custom" or item.get("name") != "AnalystPanel":
                continue
            if not item.get("id") or not item.get("forId"):
                continue
            panel = cl.CustomElement(
                name="AnalystPanel", display="inline", id=str(item["id"]),
                for_id=str(item["forId"]),
                props=_new_analyst_panel().props,
            )
            cl.user_session.set("analyst_panel", panel)
            await panel.update()
            restored = True
            break
        if not restored:
            await _send_analyst_panel()
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

    data = _get_analyst_settings()
    if selection == "auto":
        data["auto"] = True
    else:
        data.update(tool=selection, auto=False)
    cl.user_session.set("analyst_settings", data)
    await _persist_analyst_settings(binding)
    await _send_analyst_settings()
    await _refresh_analyst_panel()
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
                "auto" if _get_analyst_settings()["auto"] else _get_analyst_settings()["tool"]
            ) if binding.agent_id == "analyst" else "auto",
            "analyst_settings": _get_analyst_settings() if binding.agent_id == "analyst" else {},
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

    async def show_tool_step(name: str, output: str) -> None:
        step = cl.Step(name=f"Tool: {name}", type="tool")
        step.output = output[:2000] + ("…" if len(output) > 2000 else "")
        await step.send()

    analyst = binding.agent_id == "analyst"
    settings = _get_analyst_settings() if analyst else {}
    response = await application.runtime.run(
        binding=binding,
        text=request_text,
        selected_tool=_selected_tool_for_binding(binding),
        temperature=settings.get("temperature") if analyst else None,
        on_tool_result=show_tool_step if analyst and settings["steps"] else None,
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
