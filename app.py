# simple chainlit test
# how to run: chainlit run app.py -w
# app.py - принимает события UI, /local_agent_chat - реализует приложение

import chainlit as cl

from local_agent_chat.agent import create_agent


@cl.on_chat_start
async def on_chat_start():
    # состояние текущей пользовательской чат-сессии
    agent = create_agent()

    cl.user_session.set("agent", agent)

    await cl.Message(
        content="Чат запущен!",
    ).send()


@cl.on_message
async def on_message(message: cl.Message):
    agent = cl.user_session.get("agent")

    response = await agent.ainvoke(message.content)

    await cl.Message(
        content=response
    ).send()
