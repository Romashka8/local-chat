from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast

from chainlit.data.sql_alchemy import SQLAlchemyDataLayer
from chainlit.data.utils import queue_until_user_message
from chainlit.types import ThreadDict

ChatCleanup = Callable[[str], Awaitable[None]]


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    identifier TEXT UNIQUE NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    "createdAt" TEXT
);

CREATE TABLE IF NOT EXISTS threads (
    id TEXT PRIMARY KEY,
    "createdAt" TEXT,
    name TEXT,
    "userId" TEXT,
    "userIdentifier" TEXT,
    tags TEXT,
    metadata TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY("userId") REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS steps (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    "threadId" TEXT NOT NULL,
    "parentId" TEXT,
    streaming INTEGER NOT NULL DEFAULT 0,
    "waitForAnswer" INTEGER,
    "isError" INTEGER DEFAULT 0,
    metadata TEXT NOT NULL DEFAULT '{}',
    tags TEXT,
    input TEXT,
    output TEXT,
    "createdAt" TEXT,
    command TEXT,
    start TEXT,
    end TEXT,
    generation TEXT NOT NULL DEFAULT '{}',
    "showInput" TEXT,
    language TEXT,
    indent INTEGER,
    "defaultOpen" INTEGER DEFAULT 0,
    modes TEXT,
    "autoCollapse" INTEGER DEFAULT 0,
    FOREIGN KEY("threadId") REFERENCES threads(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_steps_thread
ON steps("threadId", "createdAt");

CREATE TABLE IF NOT EXISTS elements (
    id TEXT PRIMARY KEY,
    "threadId" TEXT,
    type TEXT,
    url TEXT,
    "chainlitKey" TEXT,
    name TEXT NOT NULL,
    display TEXT,
    "objectKey" TEXT,
    size TEXT,
    page INTEGER,
    language TEXT,
    "forId" TEXT,
    mime TEXT,
    props TEXT NOT NULL DEFAULT '{}',
    "autoPlay" INTEGER,
    "playerConfig" TEXT,
    FOREIGN KEY("threadId") REFERENCES threads(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS feedbacks (
    id TEXT PRIMARY KEY,
    "forId" TEXT NOT NULL,
    "threadId" TEXT,
    value INTEGER NOT NULL,
    comment TEXT,
    FOREIGN KEY("threadId") REFERENCES threads(id) ON DELETE CASCADE
);
"""


def _encode_json_list(value: list[str] | None) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def _decode_json_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, list):
        return [str(item) for item in value]
    if not isinstance(value, str):
        return None

    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        # Backward compatibility if an older local DB contains a plain string.
        return [value] if value else []

    if isinstance(decoded, list):
        return [str(item) for item in decoded]
    return []


def _decode_json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value:
        return {}

    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return {}

    return decoded if isinstance(decoded, dict) else {}


class SQLiteChainlitDataLayer(SQLAlchemyDataLayer):
    """SQLite-backed Chainlit persistence for local/single-process use.

    Chainlit's generic SQLAlchemy layer passes Python lists for fields such as
    thread/step ``tags``. PostgreSQL can persist those through array-capable
    schemas, while our lightweight SQLite schema stores them as TEXT. This
    adapter owns that compatibility boundary: JSON on disk, native Python
    objects at the Chainlit API boundary.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.chat_cleanup: ChatCleanup | None = None
        self._step_locks: dict[str, asyncio.Lock] = {}

    def _step_lock(self, step_id: str) -> asyncio.Lock:
        return self._step_locks.setdefault(step_id, asyncio.Lock())

    async def update_thread(
        self,
        thread_id: str,
        name: str | None = None,
        user_id: str | None = None,
        metadata: dict | None = None,
        tags: list[str] | None = None,
    ) -> None:
        """Persist thread tags as JSON because SQLite has no array type."""
        encoded_tags = _encode_json_list(tags)

        # The parent method is typed for list[str], but it only forwards this
        # value as a SQL bind parameter. For SQLite our serialized JSON string
        # is the correct storage representation.
        await super().update_thread(
            thread_id=thread_id,
            name=name,
            user_id=user_id,
            metadata=metadata,
            tags=cast(Any, encoded_tags),
        )

    @staticmethod
    def _encode_step_for_sqlite(step_dict):
        payload = dict(step_dict)
        tags = payload.get("tags")
        if isinstance(tags, list):
            payload["tags"] = json.dumps(tags, ensure_ascii=False)
        return payload

    @queue_until_user_message()
    async def create_step(self, step_dict):
        """Serialize concurrent writes and adapt JSON-like SQLite fields."""
        step_id = str(step_dict["id"])
        payload = self._encode_step_for_sqlite(step_dict)

        async with self._step_lock(step_id):
            await SQLAlchemyDataLayer.create_step.__wrapped__(self, payload)

    @queue_until_user_message()
    async def update_step(self, step_dict):
        step_id = str(step_dict["id"])
        payload = self._encode_step_for_sqlite(step_dict)

        async with self._step_lock(step_id):
            await SQLAlchemyDataLayer.create_step.__wrapped__(self, payload)

    async def get_all_user_threads(
        self,
        user_id: str | None = None,
        thread_id: str | None = None,
    ) -> list[ThreadDict] | None:
        """Restore SQLite JSON TEXT fields to Chainlit-native structures."""
        threads = await super().get_all_user_threads(
            user_id=user_id,
            thread_id=thread_id,
        )
        if threads is None:
            return None

        for thread in threads:
            thread["tags"] = _decode_json_list(thread.get("tags"))
            thread["metadata"] = _decode_json_dict(thread.get("metadata"))

            for step in thread.get("steps") or []:
                step["tags"] = _decode_json_list(step.get("tags"))
                step["metadata"] = _decode_json_dict(step.get("metadata"))
                step["generation"] = _decode_json_dict(step.get("generation"))

            for element in thread.get("elements") or []:
                if "props" in element:
                    element["props"] = _decode_json_dict(element.get("props"))
                if "playerConfig" in element:
                    element["playerConfig"] = _decode_json_dict(
                        element.get("playerConfig")
                    )

        return threads

    async def delete_step(self, step_id: str):
        try:
            await super().delete_step(step_id)
        finally:
            self._step_locks.pop(step_id, None)

    async def delete_thread(self, thread_id: str):
        """Keep UI history deletion and agent-memory deletion in sync."""
        if self.chat_cleanup is not None:
            await self.chat_cleanup(thread_id)

        await super().delete_thread(thread_id)


def create_chainlit_data_layer(path: Path) -> SQLiteChainlitDataLayer:
    """Create/migrate the local Chainlit UI database and return its data layer."""
    path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA)

    return SQLiteChainlitDataLayer(
        conninfo=f"sqlite+aiosqlite:///{path}",
    )
