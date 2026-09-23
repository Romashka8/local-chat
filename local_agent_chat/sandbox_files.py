from __future__ import annotations

import asyncio
import re
import shutil
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TypeVar

from .file_readers import render_file

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class StoredFile:
    name: str
    size_bytes: int


class SandboxFiles:
    """Persistent, read-only-to-the-agent file storage scoped by Chat.

    Chainlit upload paths are temporary implementation details. Every accepted
    upload is copied into this application-owned sandbox before the agent runs.
    """

    def __init__(self, root: Path, *, max_file_bytes: int, max_chat_bytes: int) -> None:
        self._root = root.resolve()
        self._max_file_bytes = max_file_bytes
        self._max_chat_bytes = max_chat_bytes
        self._locks: dict[str, asyncio.Lock] = {}

    async def _mutate(self, chat_id: str, operation: Callable[[], T]) -> T:
        async with self._locks.setdefault(chat_id, asyncio.Lock()):
            task = asyncio.create_task(asyncio.to_thread(operation))
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                await asyncio.gather(task, return_exceptions=True)
                raise

    def _chat_root(self, chat_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", chat_id):
            raise ValueError("Invalid Chat identifier")
        return self._root / chat_id

    def files_dir(self, chat_id: str) -> Path:
        chat_root = self._chat_root(chat_id)
        if chat_root.is_symlink():
            raise RuntimeError("Chat directory must not be a symbolic link")
        chat_root.mkdir(parents=True, exist_ok=True)

        files = chat_root / "files"
        if files.is_symlink():
            raise RuntimeError("Chat files directory must not be a symbolic link")
        files.mkdir(exist_ok=True)
        return files

    @staticmethod
    def _unique_destination(destination: Path) -> Path:
        candidate = destination
        index = 2
        while candidate.exists() or candidate.is_symlink():
            candidate = destination.with_name(
                f"{destination.stem} ({index}){destination.suffix}"
            )
            index += 1
        return candidate

    async def upload(self, chat_id: str, source: Path, name: str) -> StoredFile:
        return await self._mutate(
            chat_id,
            lambda: self._upload(chat_id, source, name),
        )

    def _upload(self, chat_id: str, source: Path, name: str) -> StoredFile:
        source = source.resolve()
        if not source.is_file():
            raise FileNotFoundError(source)

        size = source.stat().st_size
        if size > self._max_file_bytes:
            raise ValueError("Uploaded file exceeds the per-file limit")

        files = self.files_dir(chat_id)
        safe_name = _safe_filename(name, fallback=source.name)
        destination = self._unique_destination(files / safe_name)

        current_size = sum(
            item.stat().st_size
            for item in files.iterdir()
            if item.is_file() and not item.is_symlink()
        )
        if current_size + size > self._max_chat_bytes:
            raise ValueError("Uploaded files exceed the per-chat storage limit")

        staging = self._chat_root(chat_id) / "staging"
        if staging.is_symlink():
            raise RuntimeError("Chat staging directory must not be a symbolic link")
        staging.mkdir(exist_ok=True)
        temporary = staging / f"upload-{uuid.uuid4().hex}"

        try:
            shutil.copy2(source, temporary)
            copied_size = temporary.stat().st_size
            if copied_size > self._max_file_bytes:
                raise ValueError("Uploaded file exceeds the per-file limit")
            if current_size + copied_size > self._max_chat_bytes:
                raise ValueError("Uploaded files exceed the per-chat storage limit")
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)

        return StoredFile(name=destination.name, size_bytes=destination.stat().st_size)

    async def list_files(self, chat_id: str) -> tuple[StoredFile, ...]:
        return await asyncio.to_thread(self._list_files, chat_id)

    def _list_files(self, chat_id: str) -> tuple[StoredFile, ...]:
        files = self.files_dir(chat_id)
        result = [
            StoredFile(name=path.name, size_bytes=path.stat().st_size)
            for path in files.iterdir()
            if path.is_file() and not path.is_symlink()
        ]
        return tuple(sorted(result, key=lambda item: item.name.casefold()))


    def resolve_file(self, chat_id: str, relative_path: str) -> Path:
        """Resolve one uploaded file inside the current chat sandbox."""
        requested = PurePosixPath(relative_path)
        if requested.is_absolute() or ".." in requested.parts:
            raise ValueError("File path must stay inside the current Chat")
        if len(requested.parts) != 1:
            raise ValueError("Uploaded files are stored at the Chat root")

        files = self.files_dir(chat_id).resolve()
        path = (files / requested.name).resolve()
        if not path.is_relative_to(files):
            raise ValueError("File path escaped the current Chat")
        if path.is_symlink() or not path.is_file():
            raise FileNotFoundError(relative_path)
        return path

    async def read_file(
        self,
        chat_id: str,
        relative_path: str,
        *,
        offset: int = 0,
        limit: int = 200,
    ) -> str:
        return await asyncio.to_thread(
            self._read_file,
            chat_id,
            relative_path,
            offset=offset,
            limit=limit,
        )

    def _read_file(
        self,
        chat_id: str,
        relative_path: str,
        *,
        offset: int,
        limit: int,
    ) -> str:
        path = self.resolve_file(chat_id, relative_path)
        return render_file(path, offset=offset, limit=limit)

    async def read_text(
        self,
        chat_id: str,
        relative_path: str,
        *,
        offset: int = 0,
        limit: int = 200,
    ) -> str:
        # Backward-compatible alias kept for callers from the previous scaffold.
        return await self.read_file(
            chat_id, relative_path, offset=offset, limit=limit
        )

    async def delete_chat(self, chat_id: str) -> None:
        await self._mutate(chat_id, lambda: self._delete_chat(chat_id))

    def _delete_chat(self, chat_id: str) -> None:
        chat_root = self._chat_root(chat_id)
        if chat_root.exists() or chat_root.is_symlink():
            if chat_root.is_symlink():
                chat_root.unlink()
            else:
                shutil.rmtree(chat_root)


def _safe_filename(name: str, *, fallback: str) -> str:
    candidate = Path(str(name).replace("\\", "/")).name.strip()
    candidate = "".join(
        char if char.isprintable() and char not in {"\r", "\n", "\t"} else "_"
        for char in candidate
    ).strip()
    if candidate in {"", ".", ".."}:
        candidate = Path(fallback).name
    return candidate[:240]
