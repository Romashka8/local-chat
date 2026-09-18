from __future__ import annotations

import asyncio
import re
import shutil
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TypeVar

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

    async def read_text(
        self,
        chat_id: str,
        relative_path: str,
        *,
        offset: int = 0,
        limit: int = 200,
    ) -> str:
        return await asyncio.to_thread(
            self._read_text,
            chat_id,
            relative_path,
            offset=offset,
            limit=limit,
        )

    def _read_text(
        self,
        chat_id: str,
        relative_path: str,
        *,
        offset: int,
        limit: int,
    ) -> str:
        if offset < 0:
            raise ValueError("offset must be >= 0")
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500 lines")

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

        raw = path.read_bytes()
        if _looks_binary(raw, path.suffix):
            raise ValueError(
                "This reader supports text files only. "
                "Binary/document parsing will be handled by a separate capability."
            )

        text = _decode_text(raw)
        lines = text.splitlines()
        selected = lines[offset : offset + limit]

        if not selected and offset >= len(lines):
            return f"File {path.name!r} has {len(lines)} lines; offset {offset} is past EOF."

        rendered = [
            f"{index + 1}: {line}"
            for index, line in enumerate(selected, start=offset)
        ]
        end = offset + len(selected)
        header = f"File: {path.name} | lines {offset + 1}-{end} of {len(lines)}"
        if end < len(lines):
            header += f" | continue with offset={end}"
        return header + "\n" + "\n".join(rendered)

    async def delete_chat(self, chat_id: str) -> None:
        await self._mutate(chat_id, lambda: self._delete_chat(chat_id))

    def _delete_chat(self, chat_id: str) -> None:
        chat_root = self._chat_root(chat_id)
        if chat_root.exists() or chat_root.is_symlink():
            if chat_root.is_symlink():
                chat_root.unlink()
            else:
                shutil.rmtree(chat_root)


def _decode_text(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("File is not valid UTF-8/UTF-8-SIG/CP1251 text")


def _looks_binary(raw: bytes, suffix: str) -> bool:
    binary_suffixes = {
        ".pdf",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".ppt",
        ".pptx",
        ".parquet",
        ".feather",
        ".pkl",
        ".pickle",
        ".zip",
        ".gz",
        ".7z",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
    }
    if suffix.casefold() in binary_suffixes:
        return True
    sample = raw[:8192]
    if b"\x00" in sample:
        return True
    return False


def _safe_filename(name: str, *, fallback: str) -> str:
    candidate = Path(str(name).replace("\\", "/")).name.strip()
    candidate = "".join(
        char if char.isprintable() and char not in {"\r", "\n", "\t"} else "_"
        for char in candidate
    ).strip()
    if candidate in {"", ".", ".."}:
        candidate = Path(fallback).name
    return candidate[:240]
