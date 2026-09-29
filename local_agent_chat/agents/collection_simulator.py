from local_agent_chat.agent_registry import AgentProfile
from local_agent_chat.prompts.collection_simulator import COLLECTION_SIMULATOR_PROMPT


COLLECTION_SIMULATOR_PROFILE = AgentProfile(
    id="collection-simulator",
    label="Collection Simulator",
    description=(
        "Учебный агент симуляции collection: показывает маршрутизацию портфеля, "
        "строит план коммуникаций и объясняет решения по клиенту."
    ),
)


__all__ = ["COLLECTION_SIMULATOR_PROFILE", "COLLECTION_SIMULATOR_PROMPT"]
