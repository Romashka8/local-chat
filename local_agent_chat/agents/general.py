from __future__ import annotations

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, MessagesState, StateGraph


def build_general_agent(
        *,
        model: BaseChatModel,
        checkpointer: BaseCheckpointSaver
) -> Any:
    async def call_model(state: MessagesState) -> dict[str, str]:
        response = await model.ainvoke(state["messages"])
        return {"messages": [response]}

    builder = StateGraph(MessagesState)

    builder.add_node("model", call_model)
    builder.add_edge(START, "model")
    builder.add_edge("model", END)

    return builder.compile(
        checkpointer=checkpointer
    )
