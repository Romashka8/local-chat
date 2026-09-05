# simple chainlit test
# how to run: chainlit run app.py -w
# app.py - принимает события UI, /local_agent_chat - реализует приложение
from uuid import uuid4

import chainlit as cl

from local_agent_chat.agent import create_agent
from local_agent_chat.memory import create_checkpointer


checkpointer = create_checkpointer()


@cl.on_chat_start
async def on_chat_start():
    # состояние текущей пользовательской чат-сессии
    thread_id = str(uuid4())

    agent = create_agent(checkpointer=checkpointer)

    cl.user_session.set("agent", agent)
    cl.user_session.set("thread_id", thread_id)

    await cl.Message(
        content="Чат запущен!",
    ).send()


@cl.on_message
async def on_message(message: cl.Message):
    agent = cl.user_session.get("agent")
    thread_id = cl.user_session.get("thread_id")

    response = await agent.ainvoke(message.content, thread_id=thread_id)

    await cl.Message(content=response).send()
