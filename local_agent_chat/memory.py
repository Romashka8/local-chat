from __future__ import annotations

import asyncio
from pathlib import Path

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver


class SQLiteAgentMemory:
    """Owns the persistent LangGraph checkpointer and its SQLite connection."""

    def __init__(self, database: Path) -> None:
        self._database = database
        self._connection: aiosqlite.Connection | None = None
        self._saver: AsyncSqliteSaver | None = None
        self._lock = asyncio.Lock()

    @property
    def database(self) -> Path:
        return self._database

    async def checkpointer(self) -> AsyncSqliteSaver:
        """Return one process-wide saver, opening SQLite lazily on first use."""
        if self._saver is not None:
            return self._saver

        async with self._lock:
            if self._saver is not None:
                return self._saver

            self._database.parent.mkdir(parents=True, exist_ok=True)
            connection = await aiosqlite.connect(self._database)
            saver = AsyncSqliteSaver(connection)

            try:
                await saver.setup()
            except BaseException:
                await connection.close()
                raise

            self._connection = connection
            self._saver = saver
            return saver

    async def delete_thread(self, thread_id: str) -> None:
        """Delete every checkpoint associated with one LangGraph thread."""
        # setup() is intentionally explict: adelete_thread() in the current
        # SQLite saver does not initialize a fresh database on its own.
        saver = await self.checkpointer()
        await saver.setup()
        await saver.adelete_thread(thread_id)

    async def close(self) -> None:
        """Close the owned SQLite connection during application shutdown."""
        async with self._lock:
            connection = self._connection
            self._connection = None
            self._saver = None

            if connection is not None:
                await connection.close()
