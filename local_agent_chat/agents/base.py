from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver

from local_agent_chat.agent_context import AgentContext


def build_langchain_agent(
    *,
    agent_id: str,
    model: BaseChatModel,
    checkpointer: BaseCheckpointSaver,
    system_prompt: str,
    tools: Sequence[BaseTool] = ()
) -> Any:
    """Build a reusable LangChain/LangGraph agent from declarative inputs."""
    return create_agent(
        model=model,
        tools=list(tools),
        system_prompt=system_prompt,
        context_schema=AgentContext,
        checkpointer=checkpointer,
        name=agent_id
    )
