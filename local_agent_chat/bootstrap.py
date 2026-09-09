from __future__ import annotations

from dataclasses import dataclass

from .agent_registry import AgentDefinition, AgentRegistry
from .agents.general import build_general_agent
from .memory import SQLiteAgentMemory
from .models import create_model
from .runtime import AgentRuntime
from .settings import Settings, load_settings


@dataclass(frozen=True, slots=True)
class Application:
    settings: Settings
    memory: SQLiteAgentMemory
    registry: AgentRegistry
    runtime: AgentRuntime

    async def close(self) -> None:
        self.registry.clear()
        await self.memory.close()


def create_application() -> Application:
    settings = load_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)

    memory = SQLiteAgentMemory(settings.checkpoints_db)

    registry = AgentRegistry(checkpointer_provider=memory.checkpointer)

    registry.register(
        AgentDefinition(
            id="general",
            build=build_general_agent,
            model_factory=create_model
        )
    )

    return Application(
        settings=settings,
        memory=memory,
        registry=registry,
        runtime=AgentRuntime(registry=registry)
    )
