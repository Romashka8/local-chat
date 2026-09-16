from __future__ import annotations

from dataclasses import dataclass

from .agent_registry import AgentDefinition, AgentRegistry
from .agents.general import build_general_agent
from .memory import SQLiteAgentMemory
from .models import create_model
from .runtime import AgentRuntime
from .runtime_history import SQLiteRuntimeHistory
from .settings import Settings, load_settings
from .tools.cross_chat_memory import create_cross_chat_memory_tools


@dataclass(frozen=True, slots=True)
class Application:
    settings: Settings
    memory: SQLiteAgentMemory
    history: SQLiteRuntimeHistory
    registry: AgentRegistry
    runtime: AgentRuntime

    async def delete_chat(self, chat_id: str) -> None:
        """Delete application state owned by one chat.
        
        Today chat_id == LangGraph memory_thread_id. Keeping this operation on
        Application gives us one place to expand cleanup when sandboxes, runtime
        history, attachments, etc. are added later.
        """
        await self.history.delete_chat(chat_id)
        await self.memory.delete_thread(chat_id)

    async def close(self) -> None:
        self.registry.clear()
        await self.history.close()
        await self.memory.close()

def create_application() -> Application:
    settings = load_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)

    memory = SQLiteAgentMemory(settings.checkpoints_db)
    history = SQLiteRuntimeHistory(settings.runtime_history_db)
    registry = AgentRegistry(checkpointer_provider=memory.checkpointer)

    registry.register(
        AgentDefinition(
            id="general",
            build=build_general_agent,
            model_factory=create_model,
            tools_factory=lambda: create_cross_chat_memory_tools(history)
        )
    )

    return Application(
        settings=settings,
        memory=memory,
        history=history,
        registry=registry,
        runtime=AgentRuntime(registry, history)
    )
