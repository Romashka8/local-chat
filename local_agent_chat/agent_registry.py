from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver

AgentBuilder = Callable[..., Any]
ModelFactory = Callable[[], BaseChatModel]
CheckpointerProvider = Callable[[], Awaitable[BaseCheckpointSaver]]


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    id: str
    build: AgentBuilder
    model_factory: ModelFactory


class AgentRegistry:
    """Registry and lifecycle owner for colmpiled agent graphs."""

    def __init__(self, *, checkpointer_provider: CheckpointerProvider) -> None:
        self._checkpointer_provider = checkpointer_provider
        self._definitions: dict[str, AgentDefinition] = {}
        self._graphs: dict[str, Any] = {}
        self._build_lock = asyncio.Lock()

    def register(self, definition: AgentDefinition) -> None:
        if definition.id in self._definitions:
            raise ValueError("Agent already registered: {definiion.id}")
        self._definitions[definition.id] = definition

    async def get(self, agent_id: str) -> Any:
        graph = self._graphs.get(agent_id)
        if graph is not None:
            return graph

        async with self._build_lock:
            graph = self._graphs.get(agent_id)
            if graph is not None:
                return graph

            definition = self._definitions.get(agent_id)
            if definition is None:
                raise KeyError(f"Unknown agent: {agent_id}")

            checkpointer = await self._checkpointer_provider()
            model = definition.model_factory()

            graph = definition.build(
                model=model,
                checkpointer=checkpointer
            )

            self._graphs[agent_id] = graph
            return graph

    def available(self) -> tuple[str, ...]:
        return tuple(self._definitions)

    def clear(self) -> None:
        """Drop compiled graphs before their shared checkpointer is closed."""
        self._graphs.clear()
