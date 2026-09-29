from __future__ import annotations

from dataclasses import dataclass

from .agent_registry import AgentDefinition, AgentRegistry
from .agents import (
    ANALYST_AGENT_PROFILE,
    ANALYST_AGENT_PROMPT,
    COLLECTION_SIMULATOR_PROFILE,
    COLLECTION_SIMULATOR_PROMPT,
    GENERAL_AGENT_PROFILE,
    GENERAL_AGENT_PROMPT,
    build_langchain_agent,
)
from .analytics import DataAnalysisService
from .collection import CollectionSimulationService, GreenplumConfig
from .artifacts import ArtifactStore
from .knowledge import SQLiteKnowledgeBase
from .memory import SQLiteAgentMemory
from .models import create_model
from .runtime import AgentRuntime
from .runtime_history import SQLiteRuntimeHistory
from .sandbox_files import SandboxFiles
from .settings import Settings, load_settings
from .tools.analytics import create_analytics_tools
from .tools.chat_files import create_chat_file_tools
from .tools.collection_simulator import create_collection_simulator_tools
from .tools.cross_chat_memory import create_cross_chat_memory_tools
from .tools.knowledge import create_knowledge_tools


@dataclass(frozen=True, slots=True)
class Application:
    settings: Settings
    memory: SQLiteAgentMemory
    history: SQLiteRuntimeHistory
    files: SandboxFiles
    artifacts: ArtifactStore
    knowledge: SQLiteKnowledgeBase
    analytics: DataAnalysisService
    collection: CollectionSimulationService
    registry: AgentRegistry
    runtime: AgentRuntime

    async def delete_chat(self, chat_id: str) -> None:
        """Delete application state owned by one Chat."""
        await self.history.delete_chat(chat_id)
        await self.memory.delete_thread(chat_id)
        # Remove source files/artifacts before the RAG index so a concurrent or
        # subsequent retrieval cannot lazily re-index a chat being deleted.
        await self.files.delete_chat(chat_id)
        await self.knowledge.delete_chat(chat_id)
        await self.collection.delete_chat(chat_id)

    async def close(self) -> None:
        self.registry.clear()
        await self.history.close()
        await self.memory.close()
        await self.collection.close()


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
    artifacts = ArtifactStore(settings.sandboxes_dir)
    knowledge = SQLiteKnowledgeBase(settings.knowledge_db, files)
    analytics = DataAnalysisService(
        files,
        artifacts,
        max_rows=settings.analyst_max_dataset_rows,
        max_columns=settings.analyst_max_columns,
    )
    collection = CollectionSimulationService(
        artifacts,
        settings.collection_sim_state_db,
        mode=settings.collection_sim_mode,
        greenplum=GreenplumConfig(
            host=settings.gp_host,
            port=settings.gp_port,
            database=settings.gp_database,
            user=settings.gp_user,
            password=settings.gp_password,
        ),
    )

    registry = AgentRegistry(checkpointer_provider=memory.checkpointer)

    def shared_tools():
        return (
            *create_cross_chat_memory_tools(history),
            *create_chat_file_tools(files),
        )

    def analyst_tools():
        return (
            *shared_tools(),
            *create_knowledge_tools(knowledge),
            *create_analytics_tools(analytics),
        )

    def collection_tools():
        return (
            *shared_tools(),
            *create_collection_simulator_tools(collection),
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
            tools_factory=analyst_tools,
        )
    )

    registry.register(
        AgentDefinition(
            profile=COLLECTION_SIMULATOR_PROFILE,
            build=build_langchain_agent,
            model_factory=create_model,
            system_prompt=COLLECTION_SIMULATOR_PROMPT,
            tools_factory=collection_tools,
        )
    )

    return Application(
        settings=settings,
        memory=memory,
        history=history,
        files=files,
        artifacts=artifacts,
        knowledge=knowledge,
        analytics=analytics,
        collection=collection,
        registry=registry,
        runtime=AgentRuntime(registry, history),
    )
