from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver

AgentBuilder = Callable[..., Any]
ModelFactory = Callable[[], BaseChatModel]
ToolFactory = Callable[[], Sequence[BaseTool]]
CheckpointerProvider = Callable[[], Awaitable[BaseCheckpointSaver]]


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    id: str
    build: AgentBuilder
    model_factory: ModelFactory
    tools_factory: ToolFactory | None = None


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
            tools = tuple(definition.tools_factory()) if definition.tools_factory is not None else ()

            graph = definition.build(
                model=model,
                checkpointer=checkpointer,
                tools=tools
            )

            self._graphs[agent_id] = graph
            return graph

    def available(self) -> tuple[str, ...]:
        return tuple(self._definitions)

    def clear(self) -> None:
        """Drop compiled graphs before their shared checkpointer is closed."""
        self._graphs.clear()
