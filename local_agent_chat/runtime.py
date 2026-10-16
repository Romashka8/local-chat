from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.messages import HumanMessage, ToolMessage

from .agent_context import AgentContext
from .agent_registry import AgentRegistry
from .runtime_history import SQLiteRuntimeHistory

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ChatBinding:
    user_id: str
    chat_id: str
    agent_id: str
    memory_thread_id: str


class AgentRuntime:
    """Application-level execution boundary for all chat agents."""

    def __init__(self, registry: AgentRegistry, history: SQLiteRuntimeHistory) -> None:
        self._registry = registry
        self._history = history
        self._locks: dict[str, asyncio.Lock] = {}

    def _enter_thread(self, thread_id: str) -> asyncio.Lock:
        lock = self._locks.setdefault(thread_id, asyncio.Lock())
        if lock.locked():
            raise RuntimeError(f"Another Turn is already running for this Chat")
        return lock

    async def run(
        self,
        binding: ChatBinding,
        text: str,
        *,
        selected_tool: str | None = None,
        temperature: float | None = None,
        on_tool_result: Callable[[str, str], Awaitable[None]] | None = None,
    ) -> str:
        async with self._enter_thread(binding.memory_thread_id):
            graph = await self._registry.get(
                binding.agent_id, selected_tool=selected_tool, temperature=temperature,
            )
            input_state = {"messages": [HumanMessage(content=text)]}
            config = {"configurable": {"thread_id": binding.memory_thread_id}}
            context = AgentContext(
                user_id=binding.user_id,
                chat_id=binding.chat_id,
                agent_id=binding.agent_id,
            )
            if on_tool_result is None:
                result = await graph.ainvoke(input_state, config=config, context=context)
            else:
                result = None
                seen_tool_ids: set[str] = set()
                async for state in graph.astream(
                    input_state, config=config, context=context, stream_mode="values"
                ):
                    result = state
                    messages = state.get("messages", [])
                    # Only display tool messages produced after this input turn.
                    last_human = max(
                        (i for i, m in enumerate(messages) if isinstance(m, HumanMessage)),
                        default=-1,
                    )
                    for msg in messages[last_human + 1:]:
                        if isinstance(msg, ToolMessage):
                            msg_id = str(msg.id or msg.tool_call_id)
                            if msg_id not in seen_tool_ids:
                                seen_tool_ids.add(msg_id)
                                await on_tool_result(msg.name or "tool", _message_text(msg))
                if result is None:
                    raise RuntimeError("Agent stream produced no state")

            response = _message_text(result["messages"][-1])

            # Cross-chat retrieval history is an auxiliary index over successful
            # turns. A failure to update it must not hide an answer whose LangGraph
            # state has already been checkpointed successfully.
            try:
                await self._history.append_turn(
                    user_id=binding.user_id,
                    chat_id=binding.chat_id,
                    agent_id=binding.agent_id,
                    user_text=text,
                    assistant_text=response,
                )
            except Exception:
                logger.exception(
                    "Failed to persist runtime turn for cross-chat retrieval: chat=%s",
                    binding.chat_id,
                )

            return response


def _message_text(message: Any) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        if parts:
            return "\n".join(parts)

    return str(content)
