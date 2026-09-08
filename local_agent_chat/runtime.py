from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage

from .agent_registry import AgentRegistry


@dataclass(frozen=True, slots=True)
class ChatBinding:
    chat_id: str
    agent_id: str
    memory_thread_id: str


class AgentRuntime:
    """Application-level execution boundary for all chat agents."""

    def __init__(self, registry: AgentRegistry) -> None:
        self._registry = registry
        self._locks: dict[str, asyncio.Lock] = {}

    def _enter_thread(self, thread_id: str) -> asyncio.Lock:
        lock = self._locks.setdefault(thread_id, asyncio.Lock())
        if lock.locked():
            raise RuntimeError(f"Another Turn is already running for this Chat")
        return lock

    async def run(self, binding: ChatBinding, text: str) -> str:
        async with self._enter_thread(binding.memory_thread_id):
            graph = await self._registry.get(binding.agent_id)
            result = await graph.ainvoke(
                {"messages": [HumanMessage(content=text)]},
                config={
                    "configurable": {
                        "thread_id": binding.memory_thread_id
                    }
                }
            )
            return _message_text(result["messages"][-1])


def _message_text(message: Any) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts : list[str] = []
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
