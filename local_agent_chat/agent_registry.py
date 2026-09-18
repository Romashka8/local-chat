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
class AgentProfile:
    """Presentation-safe metadata for one registred agent."""

    id: str
    label: str
    description: str
    default: bool = False


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    """Declarative definition used to build one reusable agent graph."""

    profile: AgentProfile
    build: AgentBuilder
    model_factory: ModelFactory
    system_prompt: str
    tools_factory: ToolFactory | None = None

    @property
    def id(self) -> str:
        return self.profile.id


class AgentRegistry:
    """Registry and lifecycle owner for colmpiled agent graphs."""

    def __init__(self, *, checkpointer_provider: CheckpointerProvider) -> None:
        self._checkpointer_provider = checkpointer_provider
        self._definitions: dict[str, AgentDefinition] = {}
        self._graphs: dict[str, Any] = {}
        self._default_id: str | None = None
        self._build_lock = asyncio.Lock()

    def register(self, definition: AgentDefinition) -> None:
        agent_id = definition.id.strip()
        if not agent_id:
            raise ValueError("Agent id must not be empty")
        if agent_id != definition.id:
            raise ValueError("Agent id must not contain leading/trailing whitespace")
        if agent_id in self._definitions:
            raise ValueError(f"Agent already registered: {agent_id}")
        if not definition.profile.label.strip():
            raise ValueError(f"Agent label must not be empty: {agent_id}")
        if not definition.system_prompt.strip():
            raise ValueError(f"Agent system prompt must not be empty: {agent_id}")
        if definition.profile.default:
            if self._default_id is not None:
                raise ValueError(
                    "Only one default agent is allowed: "
                    f"{self._default_id!r} is already default"
                )
            self._default_id = agent_id

        self._definitions[agent_id] = definition

    @property
    def default_id(self) -> str:
        if self._default_id is not None:
            return self._default_id
        if self._definitions:
            return next(iter(self._definitions))
        raise RuntimeError("No agents are registered!")

    def profiles(self) -> tuple[AgentProfile, ...]:
        return tuple(definition.profile for definition in self._definitions.values())

    def has(self, agent_id: str) -> AgentProfile:
        return agent_id in self._definitions

    def profile(self, agent_id: str) -> Any:
        definition = self._definitions.get(agent_id)
        if definition is None:
            raise KeyError(f"Unknown agent: {agent_id}")
        return definition.profile

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
                agent_id=definition.id,
                model=model,
                checkpointer=checkpointer,
                system_prompt=definition.system_prompt,
                tools=tools
            )

            self._graphs[agent_id] = graph
            return graph

    def clear(self) -> None:
        """Drop compiled graphs before their shared checkpointer is closed."""
        self._graphs.clear()
