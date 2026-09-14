from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Awaitable, Callable
from pathlib import Path

from chainlit.data.sql_alchemy import SQLAlchemyDataLayer
from chainlit.data.utils import queue_until_user_message


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


class SQLiteChainlitDataLayer(SQLAlchemyDataLayer):
    """SQLite-backend Chainlit persistence for local/single-process use."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.chat_cleanup: ChatCleanup | None = None
        self._step_locks: dict[str, asyncio.Lock] = {}

    def _step_lock(self, step_id: str) -> asyncio.Lock:
        return self._step_locks.setdefault(step_id, asyncio.Lock())

    @queue_until_user_message()
    async def create_step(self, step_dict):
        """Serialize concurrent writes for the same Chainlit step
        
        The SQLAlchemy data layer itself already knows how to persist a step.
        We unwrap its own deque decorator because this method is already queued.
        """
        step_id = str(step_dict["id"])
        async with self._step_lock(step_id):
            await SQLAlchemyDataLayer.create_step.__wrapped__(self, step_dict)

    @queue_until_user_message()
    async def update_step(self, step_dict):
        step_id = str(step_dict["id"])
        async with self._step_lock(step_id):
            await SQLAlchemyDataLayer.create_step.__wrapped__(self, step_dict)

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
        conninfo=f"sqlite+aiosqlite:///{path}"
    )
