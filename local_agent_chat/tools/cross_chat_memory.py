from __future__ import annotations

from collections.abc import Sequence

from langchain.tools import ToolRuntime, tool
from langchain_core.tools import BaseTool

from local_agent_chat.agent_context import AgentContext
from local_agent_chat.runtime_history import SQLiteRuntimeHistory, StoredTurn


SEARCH_RESULT_TEXT_LIMIT = 700
READ_TURN_TEXT_LIMIT = 1800


def create_cross_chat_memory_tools(
    history: SQLiteRuntimeHistory,
) -> Sequence[BaseTool]:
    """Create read-only tools for episodic memory across conversations."""

    @tool
    async def search_past_chats(
        query: str,
        runtime: ToolRuntime[AgentContext],
    ) -> str:
        """Search the user's OTHER chats for relevant past conversations.

        Use this when the user refers to something discussed earlier, asks to
        continue previous work, or when prior decisions/context could materially
        improve the answer. The current chat is excluded automatically.

        Returns a small ranked set of matching turns. If one looks useful, call
        read_past_chat with its turn_id to inspect surrounding context.
        """
        results = await history.search(
            user_id=runtime.context.user_id,
            current_chat_id=runtime.context.chat_id,
            query=query,
            limit=5,
        )

        if not results:
            return "No relevant turns were found in other chats."

        lines = [
            "Relevant turns from other chats:",
        ]
        for index, item in enumerate(results, start=1):
            lines.extend(
                [
                    "",
                    (
                        f"[{index}] turn_id={item.turn_id} "
                        f"chat_id={item.chat_id} "
                        f"agent={item.agent_id} "
                        f"created_at={item.created_at}"
                    ),
                    f"User: {_clip(item.user_text, SEARCH_RESULT_TEXT_LIMIT)}",
                    (
                        "Assistant: "
                        f"{_clip(item.assistant_text, SEARCH_RESULT_TEXT_LIMIT)}"
                    ),
                ]
            )

        lines.append(
            "\nUse read_past_chat(turn_id=<id>) for a bounded context window "
            "around a promising result."
        )
        return "\n".join(lines)

    @tool
    async def read_past_chat(
        turn_id: int,
        runtime: ToolRuntime[AgentContext],
    ) -> str:
        """Read surrounding turns from another chat after a memory search.

        Pass a turn_id returned by search_past_chats. Access is restricted to
        the current authenticated user's history, and the current chat cannot be
        read through this tool.
        """
        turns = await history.read_around(
            user_id=runtime.context.user_id,
            current_chat_id=runtime.context.chat_id,
            turn_id=turn_id,
            before=2,
            after=2,
        )

        if not turns:
            return (
                "That turn is unavailable, belongs to the current chat, or "
                "is not part of this user's history."
            )

        return _render_turns(turns)

    return (search_past_chats, read_past_chat)


def _render_turns(turns: Sequence[StoredTurn]) -> str:
    first = turns[0]
    lines = [
        (
            f"Context from past chat {first.chat_id} "
            f"(agent={first.agent_id}):"
        )
    ]

    for turn in turns:
        lines.extend(
            [
                "",
                f"turn_id={turn.turn_id} created_at={turn.created_at}",
                f"User: {_clip(turn.user_text, READ_TURN_TEXT_LIMIT)}",
                f"Assistant: {_clip(turn.assistant_text, READ_TURN_TEXT_LIMIT)}",
            ]
        )

    return "\n".join(lines)


def _clip(text: str, limit: int) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "…"
