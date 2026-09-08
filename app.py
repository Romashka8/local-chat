# simple chainlit test
# how to run: chainlit run app.py -w
# app.py - принимает события UI, /local_agent_chat - реализует приложение
import chainlit as cl

from local_agent_chat.bootstrap import create_application
from local_agent_chat.runtime import ChatBinding

application = create_application()


@cl.on_chat_start
async def on_chat_start() -> None:
    chat_id = cl.context.session.thread_id

    binding = ChatBinding(chat_id=chat_id, agent_id="general", memory_thread_id=chat_id)
    cl.user_session.set("chat_binding", binding)

    await cl.Message(content="Чат запущен!").send()


@cl.on_message
async def on_message(message: cl.Message) -> None:
    binding = cl.user_session.get("chat_binding")
    if binding is None:
        raise RuntimeError("Chat binding is not initialized.")

    response = await application.runtime.run(binding=binding, text=message.content)
    await cl.Message(content=response).send()
