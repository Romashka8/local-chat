from __future__ import annotations

from dataclasses import dataclass

from .agent_registry import AgentDefinition, AgentRegistry
from .agents import (
    ANALYST_AGENT_PROFILE,
    ANALYST_AGENT_PROMPT,
    GENERAL_AGENT_PROFILE,
    GENERAL_AGENT_PROMPT,
    build_langchain_agent
)
from .memory import SQLiteAgentMemory
from .models import create_model
from .runtime import AgentRuntime
from .runtime_history import SQLiteRuntimeHistory
from .sandbox_files import SandboxFiles
from .settings import Settings, load_settings
from .tools.chat_files import create_chat_file_tools
from .tools.cross_chat_memory import create_cross_chat_memory_tools


@dataclass(frozen=True, slots=True)
class Application:
    settings: Settings
    memory: SQLiteAgentMemory
    history: SQLiteRuntimeHistory
    files: SandboxFiles
    registry: AgentRegistry
    runtime: AgentRuntime

    async def delete_chat(self, chat_id: str) -> None:
        """Delete application state owned by one Chat."""
        await self.history.delete_chat(chat_id)
        await self.memory.delete_thread(chat_id)
        await self.files.delete_chat(chat_id)

    async def close(self) -> None:
        self.registry.clear()
        await self.history.close()
        await self.memory.close()


def create_application() -> Application:
    settings = load_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)

    memory = SQLiteAgentMemory(settings.checkpoints_db)
    history = SQLiteRuntimeHistory(settings.runtime_history_db)
    files = SandboxFiles(
        settings.sandboxes_dir,
        max_file_bytes=settings.max_upload_file_bytes,
        max_chat_bytes=settings.max_chat_files_bytes,
    )

    registry = AgentRegistry(
        checkpointer_provider=memory.checkpointer,
    )

    def shared_tools():
        return (
            *create_cross_chat_memory_tools(history),
            *create_chat_file_tools(files),
        )

    registry.register(
        AgentDefinition(
            profile=GENERAL_AGENT_PROFILE,
            build=build_langchain_agent,
            model_factory=create_model,
            system_prompt=GENERAL_AGENT_PROMPT,
            tools_factory=shared_tools,
        )
    )

    registry.register(
        AgentDefinition(
            profile=ANALYST_AGENT_PROFILE,
            build=build_langchain_agent,
            model_factory=create_model,
            system_prompt=ANALYST_AGENT_PROMPT,
            tools_factory=shared_tools,
        )
    )

    return Application(
        settings=settings,
        memory=memory,
        history=history,
        files=files,
        registry=registry,
        runtime=AgentRuntime(registry, history),
    )
