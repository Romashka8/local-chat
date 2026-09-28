from local_agent_chat.agent_registry import AgentProfile
from local_agent_chat.prompts.analyst import ANALYST_AGENT_PROMPT

ANALYST_AGENT_PROFILE = AgentProfile(
    id="analyst",
    label="Analyst",
    description=(
        "Агент-аналитик: читает CSV/XLSX, строит EDA в HTML и использует "
        "RAG по загруженным PDF/TXT-методологиям для интерпретации результатов."
    ),
)

__all__ = ["ANALYST_AGENT_PROFILE", "ANALYST_AGENT_PROMPT"]
