from __future__ import annotations

import asyncio
import hashlib
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from .file_readers import extract_document_text
from .sandbox_files import SandboxFiles

_KNOWLEDGE_SUFFIXES = {
    ".txt",
    ".md",
    ".rst",
    ".log",
    ".pdf",
    ".json",
    ".yaml",
    ".yml",
    ".xml",
    ".sql",
    ".py",
}

_SCHEMA = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS knowledge_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    file_name TEXT NOT NULL,
    file_sha256 TEXT NOT NULL,
    indexed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, chat_id, file_name, file_sha256)
);

CREATE INDEX IF NOT EXISTS idx_knowledge_documents_chat
ON knowledge_documents(user_id, chat_id, id);

CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    file_name TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    text TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_chat
ON knowledge_chunks(user_id, chat_id, document_id, chunk_index);

CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_chunks_fts USING fts5(
    text,
    content='knowledge_chunks',
    content_rowid='id',
    tokenize='unicode61'
);

CREATE TRIGGER IF NOT EXISTS knowledge_chunks_fts_insert
AFTER INSERT ON knowledge_chunks BEGIN
    INSERT INTO knowledge_chunks_fts(rowid, text)
    VALUES (new.id, new.text);
END;

CREATE TRIGGER IF NOT EXISTS knowledge_chunks_fts_delete
AFTER DELETE ON knowledge_chunks BEGIN
    INSERT INTO knowledge_chunks_fts(knowledge_chunks_fts, rowid, text)
    VALUES ('delete', old.id, old.text);
END;

CREATE TRIGGER IF NOT EXISTS knowledge_chunks_fts_update
AFTER UPDATE ON knowledge_chunks BEGIN
    INSERT INTO knowledge_chunks_fts(knowledge_chunks_fts, rowid, text)
    VALUES ('delete', old.id, old.text);
    INSERT INTO knowledge_chunks_fts(rowid, text)
    VALUES (new.id, new.text);
END;
"""


@dataclass(frozen=True, slots=True)
class KnowledgeDocument:
    file_name: str
    chunks: int
    indexed_at: str


@dataclass(frozen=True, slots=True)
class KnowledgeHit:
    file_name: str
    chunk_index: int
    text: str
    score: float


class SQLiteKnowledgeBase:
    """Per-chat retrieval index for uploaded knowledge documents.

    v1 intentionally uses SQLite FTS5 instead of a vector database. The public
    tool contract is retrieval-oriented, so the backend can later be replaced by
    embeddings/hybrid search without changing agent definitions or prompts.
    """

    def __init__(self, database: Path, sandbox: SandboxFiles) -> None:
        self._database = database
        self._sandbox = sandbox
        self._lock = asyncio.Lock()
        self._initialized = False

    async def ensure_indexed(self, *, user_id: str, chat_id: str) -> tuple[str, ...]:
        async with self._lock:
            return await asyncio.to_thread(
                self._ensure_indexed_sync,
                user_id,
                chat_id,
            )

    async def list_documents(
        self,
        *,
        user_id: str,
        chat_id: str,
    ) -> tuple[KnowledgeDocument, ...]:
        await self.ensure_indexed(user_id=user_id, chat_id=chat_id)
        return await asyncio.to_thread(self._list_documents_sync, user_id, chat_id)

    async def search(
        self,
        *,
        user_id: str,
        chat_id: str,
        query: str,
        limit: int = 5,
    ) -> tuple[KnowledgeHit, ...]:
        await self.ensure_indexed(user_id=user_id, chat_id=chat_id)
        return await asyncio.to_thread(
            self._search_sync,
            user_id,
            chat_id,
            query,
            limit,
        )

    async def delete_chat(self, chat_id: str) -> None:
        async with self._lock:
            await asyncio.to_thread(self._delete_chat_sync, chat_id)

    def _connect(self) -> sqlite3.Connection:
        self._database.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self._database))
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        if not self._initialized:
            connection.executescript(_SCHEMA)
            connection.commit()
            self._initialized = True
        return connection

    def _ensure_indexed_sync(self, user_id: str, chat_id: str) -> tuple[str, ...]:
        files = self._sandbox._list_files(chat_id)
        indexed: list[str] = []

        with closing(self._connect()) as connection:
            for stored in files:
                suffix = Path(stored.name).suffix.casefold()
                if suffix not in _KNOWLEDGE_SUFFIXES:
                    continue

                path = self._sandbox.resolve_file(chat_id, stored.name)
                digest = _sha256(path)
                existing = connection.execute(
                    """
                    SELECT id
                    FROM knowledge_documents
                    WHERE user_id = ? AND chat_id = ? AND file_name = ? AND file_sha256 = ?
                    """,
                    (user_id, chat_id, stored.name, digest),
                ).fetchone()
                if existing is not None:
                    indexed.append(stored.name)
                    continue

                text = extract_document_text(path)
                chunks = _chunk_text(text)
                if not chunks:
                    continue

                cursor = connection.execute(
                    """
                    INSERT INTO knowledge_documents(user_id, chat_id, file_name, file_sha256)
                    VALUES (?, ?, ?, ?)
                    """,
                    (user_id, chat_id, stored.name, digest),
                )
                document_id = int(cursor.lastrowid)
                connection.executemany(
                    """
                    INSERT INTO knowledge_chunks(
                        document_id, user_id, chat_id, file_name, chunk_index, text
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (document_id, user_id, chat_id, stored.name, index, chunk)
                        for index, chunk in enumerate(chunks)
                    ],
                )
                connection.commit()
                indexed.append(stored.name)

        return tuple(indexed)

    def _list_documents_sync(
        self,
        user_id: str,
        chat_id: str,
    ) -> tuple[KnowledgeDocument, ...]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT d.file_name, d.indexed_at, COUNT(c.id) AS chunks
                FROM knowledge_documents AS d
                JOIN knowledge_chunks AS c ON c.document_id = d.id
                WHERE d.user_id = ? AND d.chat_id = ?
                GROUP BY d.id, d.file_name, d.indexed_at
                ORDER BY d.file_name COLLATE NOCASE
                """,
                (user_id, chat_id),
            ).fetchall()
        return tuple(
            KnowledgeDocument(
                file_name=str(row["file_name"]),
                chunks=int(row["chunks"]),
                indexed_at=str(row["indexed_at"]),
            )
            for row in rows
        )

    def _search_sync(
        self,
        user_id: str,
        chat_id: str,
        query: str,
        limit: int,
    ) -> tuple[KnowledgeHit, ...]:
        match_query = _build_fts_query(query)
        if match_query is None:
            return ()
        safe_limit = max(1, min(int(limit), 8))

        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT
                    c.file_name,
                    c.chunk_index,
                    c.text,
                    bm25(knowledge_chunks_fts) AS score
                FROM knowledge_chunks_fts
                JOIN knowledge_chunks AS c
                  ON c.id = knowledge_chunks_fts.rowid
                WHERE knowledge_chunks_fts MATCH ?
                  AND c.user_id = ?
                  AND c.chat_id = ?
                ORDER BY score ASC, c.id ASC
                LIMIT ?
                """,
                (match_query, user_id, chat_id, safe_limit),
            ).fetchall()

        return tuple(
            KnowledgeHit(
                file_name=str(row["file_name"]),
                chunk_index=int(row["chunk_index"]),
                text=str(row["text"]),
                score=float(row["score"]),
            )
            for row in rows
        )

    def _delete_chat_sync(self, chat_id: str) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                "DELETE FROM knowledge_documents WHERE chat_id = ?",
                (chat_id,),
            )
            connection.commit()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _chunk_text(text: str, *, chunk_size: int = 1800, overlap: int = 250) -> list[str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []

    paragraphs = [
        part.strip() for part in re.split(r"\n{2,}", normalized) if part.strip()
    ]
    chunks: list[str] = []
    current = ""

    for paragraph in paragraphs:
        candidate = paragraph if not current else f"{current}\n\n{paragraph}"
        if len(candidate) <= chunk_size:
            current = candidate
            continue

        if current:
            chunks.append(current)
            tail = current[-overlap:] if overlap else ""
            current = f"{tail}\n\n{paragraph}".strip()
        else:
            start = 0
            while start < len(paragraph):
                end = min(len(paragraph), start + chunk_size)
                chunks.append(paragraph[start:end])
                if end >= len(paragraph):
                    current = ""
                    break
                start = max(end - overlap, start + 1)

        while len(current) > chunk_size:
            chunks.append(current[:chunk_size])
            current = current[max(chunk_size - overlap, 1) :]

    if current:
        chunks.append(current)

    return chunks


def _build_fts_query(query: str) -> str | None:
    tokens = re.findall(r"\w+", query, flags=re.UNICODE)
    tokens = [token for token in tokens if len(token) >= 2][:16]
    if not tokens:
        return None
    escaped = [token.replace('"', '""') for token in tokens]
    return " OR ".join(f'"{token}"' for token in escaped)
