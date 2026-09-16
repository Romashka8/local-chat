from __future__ import annotations

from contextlib import asynccontextmanager

import chainlit as cl
from chainlit.server import app
from chainlit.types import ThreadDict

from local_agent_chat.bootstrap import create_application
from local_agent_chat.chainlit_data import create_chainlit_data_layer
from local_agent_chat.runtime import ChatBinding


application = create_application()
chainlit_layer = create_chainlit_data_layer(
    application.settings.chainlit_db
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


def _current_user_id() -> str:
    user = cl.user_session.get("user")
    identifier = getattr(user, "identifier", None)
    if not identifier:
        raise RuntimeError("Authenticated Chainlit user is not available")
    return str(identifier)


def _binding(
    *,
    chat_id: str,
    agent_id: str = "general",
    memory_thread_id: str | None = None,
) -> ChatBinding:
    return ChatBinding(
        user_id=_current_user_id(),
        chat_id=chat_id,
        agent_id=agent_id,
        memory_thread_id=memory_thread_id or chat_id,
    )


def _store_binding(binding: ChatBinding) -> None:
    # Keep only JSON-serializable values in Chainlit's user session. The
    # authenticated user is already restored by Chainlit itself.
    cl.user_session.set("agent_id", binding.agent_id)
    cl.user_session.set("memory_thread_id", binding.memory_thread_id)


def _current_binding() -> ChatBinding:
    chat_id = cl.context.session.thread_id
    agent_id = cl.user_session.get("agent_id") or "general"
    memory_thread_id = (
        cl.user_session.get("memory_thread_id") or chat_id
    )

    return _binding(
        chat_id=chat_id,
        agent_id=str(agent_id),
        memory_thread_id=str(memory_thread_id),
    )


@cl.on_chat_start
async def on_chat_start() -> None:
    binding = _binding(
        chat_id=cl.context.session.thread_id,
    )
    _store_binding(binding)

    await cl.Message(
        content="Чат запущен.",
    ).send()


@cl.on_chat_resume
async def on_chat_resume(thread: ThreadDict) -> None:
    """Reconnect the persisted Chainlit thread to its LangGraph memory."""
    metadata = thread.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}

    chat_id = str(thread["id"])
    binding = _binding(
        chat_id=chat_id,
        agent_id=str(metadata.get("agent_id") or "general"),
        memory_thread_id=str(
            metadata.get("memory_thread_id") or chat_id
        ),
    )
    _store_binding(binding)


@cl.on_message
async def on_message(message: cl.Message) -> None:
    binding = _current_binding()

    # Persist the application binding in the UI thread as metadata. This
    # becomes important once users can select agents or fork/reset memory.
    await chainlit_layer.update_thread(
        binding.chat_id,
        metadata={
            "agent_id": binding.agent_id,
            "memory_thread_id": binding.memory_thread_id,
        },
    )

    response = await application.runtime.run(
        binding=binding,
        text=message.content,
    )

    await cl.Message(
        content=response,
    ).send()


async def cleanup_chat(chat_id: str) -> None:
    await application.delete_chat(chat_id)


chainlit_layer.chat_cleanup = cleanup_chat


async def close_resources() -> None:
    await application.close()


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
