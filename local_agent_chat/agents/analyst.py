from local_agent_chat.agent_registry import AgentProfile
from local_agent_chat.prompts.analyst import ANALYST_AGENT_PROMPT

ANALYST_AGENT_PROFILE = AgentProfile(
    id="analyst",
    label="Analyst",
    description=(
        "Агент-аналитик: формализует задачу, отделяет факты от гипотез и "
        "делает выводы с учётом ограничений данных."
    )
)

__all__ = ["ANALYST_AGENT_PROFILE", "ANALYST_AGENT_PROMPT"]
