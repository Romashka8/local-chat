from __future__ import annotations

from dataclasses import dataclass

from .agent_registry import AgentDefinition, AgentRegistry
from .agents.general import build_general_agent
from .memory import create_checkpointer
from .models import create_model
from .runtime import AgentRuntime


@dataclass(frozen=True, slots=True)
class Application:
    registry: AgentRegistry
    runtime: AgentRuntime


def create_application() -> Application:
    checkpointer = create_checkpointer()

    registry = AgentRegistry(checkpoiter=checkpointer)
    registry.register(
        AgentDefinition(
            id="general",
            build=build_general_agent,
            model_factory=create_model
        )
    )

    return Application(
        registry=registry,
        runtime=AgentRuntime(registry=registry)
    )
