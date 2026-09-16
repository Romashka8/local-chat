from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path

import aiosqlite


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    user_text TEXT NOT NULL,
    assistant_text TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_turns_user_chat_id
ON turns(user_id, chat_id, id);

CREATE INDEX IF NOT EXISTS idx_turns_chat_id
ON turns(chat_id);

CREATE VIRTUAL TABLE IF NOT EXISTS turns_fts USING fts5(
    user_text,
    assistant_text,
    content='turns',
    content_rowid='id',
    tokenize='unicode61'
);

CREATE TRIGGER IF NOT EXISTS turns_fts_insert
AFTER INSERT ON turns BEGIN
    INSERT INTO turns_fts(rowid, user_text, assistant_text)
    VALUES (new.id, new.user_text, new.assistant_text);
END;

CREATE TRIGGER IF NOT EXISTS turns_fts_delete
AFTER DELETE ON turns BEGIN
    INSERT INTO turns_fts(turns_fts, rowid, user_text, assistant_text)
    VALUES ('delete', old.id, old.user_text, old.assistant_text);
END;

CREATE TRIGGER IF NOT EXISTS turns_fts_update
AFTER UPDATE ON turns BEGIN
    INSERT INTO turns_fts(turns_fts, rowid, user_text, assistant_text)
    VALUES ('delete', old.id, old.user_text, old.assistant_text);

    INSERT INTO turns_fts(rowid, user_text, assistant_text)
    VALUES (new.id, new.user_text, new.assistant_text);
END;
"""

@dataclass(frozen=True, slots=True)
class TurnSearchResult:
    turn_id: int
    chat_id: str
    agent_id: str
    created_at: str
    user_text: str
    assistant_text: str
    score: float


@dataclass(frozen=True, slots=True)
class StoredTurn:
    turn_id: int
    chat_id: str
    agent_id: str
    created_at: str
    user_text: str
    assistant_text: str


class SQLiteRuntimeHistory:
    """Canonical successful-turn history used for cross-chat retrieval.

    This database is application state, not Chainlit UI state. Only completed
    user/assistant turns are recorded; tool logs and LangGraph checkpoints stay
    in their own persistence layers.
    """


    def __init__(self, database: Path) -> None:
        self._database = database
        self._connection: aiosqlite.Connection | None = None
        self._init_lock = asyncio.Lock()

    @property
    def database(self) -> Path:
        return self._database

    async def _connection_or_open(self) -> aiosqlite.Connection:
        if self._connection is not None:
            return self._connection

        async with self._init_lock:
            if self._connection is not None:
                return self._connection

            self._database.parent.mkdir(parents=True, exist_ok=True)
            connection = await aiosqlite.connect(str(self._database))
            connection.row_factory = aiosqlite.Row

            try:
                await connection.execute("PRAGMA journal_mode=WAL")
                await connection.execute("PRAGMA foreign_keys=ON")
                await connection.executescript(SCHEMA)
                await connection.commit()
            except BaseException:
                await connection.close()
                raise

            self._connection = connection
            return connection

    async def append_turn(
        self,
        *,
        user_id: str,
        chat_id: str,
        agent_id: str,
        user_text: str,
        assistant_text: str,
    ) -> int:
        connection = await self._connection_or_open()
        cursor = await connection.execute(
            """
            INSERT INTO turns(
                user_id,
                chat_id,
                agent_id,
                user_text,
                assistant_text
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                user_id,
                chat_id,
                agent_id,
                user_text,
                assistant_text,
            ),
        )
        await connection.commit()

        if cursor.lastrowid is None:
            raise RuntimeError("SQLite did not return an id for the stored turn")
        return int(cursor.lastrowid)

    async def search(
        self,
        *,
        user_id: str,
        current_chat_id: str,
        query: str,
        limit: int = 5,
    ) -> list[TurnSearchResult]:
        match_query = _build_fts_query(query)
        if match_query is None:
            return []

        safe_limit = max(1, min(int(limit), 10))
        connection = await self._connection_or_open()
        cursor = await connection.execute(
            """
            SELECT
                t.id,
                t.chat_id,
                t.agent_id,
                t.created_at,
                t.user_text,
                t.assistant_text,
                bm25(turns_fts, 3.0, 1.0) AS score
            FROM turns_fts
            JOIN turns AS t
              ON t.id = turns_fts.rowid
            WHERE turns_fts MATCH ?
              AND t.user_id = ?
              AND t.chat_id <> ?
            ORDER BY score ASC, t.id DESC
            LIMIT ?
            """,
            (
                match_query,
                user_id,
                current_chat_id,
                safe_limit,
            ),
        )
        rows = await cursor.fetchall()

        return [
            TurnSearchResult(
                turn_id=int(row["id"]),
                chat_id=str(row["chat_id"]),
                agent_id=str(row["agent_id"]),
                created_at=str(row["created_at"]),
                user_text=str(row["user_text"]),
                assistant_text=str(row["assistant_text"]),
                score=float(row["score"]),
            )
            for row in rows
        ]

    async def read_around(
        self,
        *,
        user_id: str,
        current_chat_id: str,
        turn_id: int,
        before: int = 2,
        after: int = 2,
    ) -> list[StoredTurn]:
        """Read a bounded window around one previously retrieved turn."""
        connection = await self._connection_or_open()

        target_cursor = await connection.execute(
            """
            SELECT id, chat_id
            FROM turns
            WHERE id = ? AND user_id = ?
            """,
            (int(turn_id), user_id),
        )
        target = await target_cursor.fetchone()
        if target is None:
            return []

        chat_id = str(target["chat_id"])
        if chat_id == current_chat_id:
            return []

        previous_cursor = await connection.execute(
            """
            SELECT
                id,
                chat_id,
                agent_id,
                created_at,
                user_text,
                assistant_text
            FROM turns
            WHERE user_id = ?
              AND chat_id = ?
              AND id <= ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (
                user_id,
                chat_id,
                int(turn_id),
                max(0, int(before)) + 1,
            ),
        )
        previous_rows = list(reversed(await previous_cursor.fetchall()))

        next_cursor = await connection.execute(
            """
            SELECT
                id,
                chat_id,
                agent_id,
                created_at,
                user_text,
                assistant_text
            FROM turns
            WHERE user_id = ?
              AND chat_id = ?
              AND id > ?
            ORDER BY id ASC
            LIMIT ?
            """,
            (
                user_id,
                chat_id,
                int(turn_id),
                max(0, int(after)),
            ),
        )
        next_rows = await next_cursor.fetchall()

        return [_stored_turn(row) for row in [*previous_rows, *next_rows]]

    async def delete_chat(self, chat_id: str) -> None:
        connection = await self._connection_or_open()
        await connection.execute(
            "DELETE FROM turns WHERE chat_id = ?",
            (chat_id,),
        )
        await connection.commit()

    async def close(self) -> None:
        async with self._init_lock:
            connection = self._connection
            self._connection = None
            if connection is not None:
                await connection.close()


def _build_fts_query(query: str) -> str | None:
    # Build MATCH syntax ourselves instead of sending arbitrary model/user text
    # into FTS5. Quoted tokens prevent operators such as NOT/NEAR from changing
    # query semantics or causing syntax errors.
    tokens = re.findall(r"\w+", query, flags=re.UNICODE)
    tokens = [token for token in tokens if len(token) >= 2][:12]
    if not tokens:
        return None

    escaped = [token.replace('"', '""') for token in tokens]
    return " OR ".join(f'"{token}"' for token in escaped)


def _stored_turn(row: aiosqlite.Row) -> StoredTurn:
    return StoredTurn(
        turn_id=int(row["id"]),
        chat_id=str(row["chat_id"]),
        agent_id=str(row["agent_id"]),
        created_at=str(row["created_at"]),
        user_text=str(row["user_text"]),
        assistant_text=str(row["assistant_text"]),
    )
