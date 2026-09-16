from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AgentContext:
    """Immutable application context injected into one agent run.

    This data is intentionally kept outside LangGraph conversation state. It
    describes who/what owns the run and is made available to tools through
    ToolRuntime without exposing these fields as model-controlled arguments.
    """

    user_id: str
    chat_id: str
    agent_id: str
