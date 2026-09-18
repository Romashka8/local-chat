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
        raise RuntimeError(
            f"Chat references an unavailable agent: {agent_id!r}"
        )
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


def _message_with_uploaded_files(text: str, filenames: tuple[str, ...]) -> str:
    if not filenames:
        return text

    attachment_note = "\n".join(
        [
            "Files uploaded with this message and available through file tools:",
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

    profile = application.registry.profile(binding.agent_id)
    await cl.Message(
        content=f"Чат запущен. Агент: **{profile.label}**.",
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
        memory_thread_id=str(
            metadata.get("memory_thread_id") or chat_id
        ),
    )
    _store_binding(binding)


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
        },
    )

    uploaded_files = await persist_message_uploads(
        chat_id=binding.chat_id,
        elements=message.elements,
        sandbox=application.files,
    )

    request_text = _message_with_uploaded_files(
        message.content,
        tuple(item.name for item in uploaded_files),
    )

    response = await application.runtime.run(
        binding=binding,
        text=request_text,
    )

    await cl.Message(
        content=response,
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
if getattr(
    app.state,
    "_agent_chat_base_lifespan",
    None,
) is None:
    app.state._agent_chat_base_lifespan = (
        app.router.lifespan_context
    )

    @asynccontextmanager
    async def agent_chat_lifespan(
        chainlit_app,
    ):
        try:
            async with (
                chainlit_app.state._agent_chat_base_lifespan(
                    chainlit_app
                )
            ) as state:
                yield state
        finally:
            cleanup = getattr(
                chainlit_app.state,
                "_agent_chat_close_resources",
                None,
            )

            if cleanup is not None:
                await cleanup()

    app.router.lifespan_context = (
        agent_chat_lifespan
    )


app.state._agent_chat_close_resources = (
    close_resources
)
