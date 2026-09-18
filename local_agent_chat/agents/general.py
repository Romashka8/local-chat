from local_agent_chat.agent_registry import AgentProfile
from local_agent_chat.prompts.general import GENERAL_AGENT_PROMPT


GENERAL_AGENT_PROFILE = AgentProfile(
    id="general",
    label="General",
    description=(
        "Универсальный агент для повседневных задач и работы с "
        "контекстом предыдущих диалогов."
    ),
    default=True
)

__all__ = ["GENERAL_AGENT_PROFILE", "GENERAL_AGENT_PROMPT"]
