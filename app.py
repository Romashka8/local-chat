# simple chainlit test
# how to run: chainlit run app.py -w
# app.py - принимает события UI, /local_agent_chat - реализует приложение
from __future__ import annotations

from contextlib import asynccontextmanager

import chainlit as cl
from chainlit.server import app

from local_agent_chat.bootstrap import create_application
from local_agent_chat.runtime import ChatBinding


application = create_application()


def _current_binding() -> ChatBinding:
    chat_id = cl.context.session.thread_id

    return ChatBinding(chat_id=chat_id, agent_id="general", memory_thread_id=chat_id)


@cl.on_chat_start
async def on_chat_start() -> None:
    binding = _current_binding()
    cl.user_session.set("chat_binding", binding)

    await cl.Message(content="Чат запущен!").send()


@cl.on_chat_resume
async def on_chat_resume(_thread) -> None:
    # NOTE - REMOVE AFTER AUTH ADD!
    # This becomes active once Chainlit data persistence + authentication
    # are configured. The same Chainlit thread_id then reconnects to the
    # same LangGraph memory_thread_id stored in checkpoints.sqlite3.
    cl.user_session.set("chat_binding", _current_binding())


@cl.on_message
async def on_message(message: cl.Message) -> None:
    binding = cl.user_session.get("chat_binding")
    if binding is None:
        raise RuntimeError("Chat binding is not initialized.")

    response = await application.runtime.run(binding=binding, text=message.content)
    await cl.Message(content=response).send()


async def close_resources() -> None:
    await application.close()


# Chainlit owns the ASGI lifespan. Wrap it instead of replacing it so our
# SQLite connection is closed after Chainlit finishes its own shutdown work.
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
