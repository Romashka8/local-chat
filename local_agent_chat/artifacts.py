from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Artifact:
    name: str
    path: Path
    size_bytes: int


class ArtifactStore:
    """Controlled write area for files produced by agents.

    Artifacts live next to uploaded files under the per-chat sandbox, but agent
    tools never receive arbitrary filesystem write access. They can only create
    outputs through narrowly scoped application services such as EDA reporting.
    """

    def __init__(self, sandboxes_root: Path) -> None:
        self._root = sandboxes_root.resolve()
        self._locks: dict[str, asyncio.Lock] = {}

    def _chat_root(self, chat_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", chat_id):
            raise ValueError("Invalid Chat identifier")
        return self._root / chat_id

    def artifacts_dir(self, chat_id: str) -> Path:
        chat_root = self._chat_root(chat_id)
        if chat_root.is_symlink():
            raise RuntimeError("Chat directory must not be a symbolic link")
        chat_root.mkdir(parents=True, exist_ok=True)

        artifacts = chat_root / "artifacts"
        if artifacts.is_symlink():
            raise RuntimeError("Artifacts directory must not be a symbolic link")
        artifacts.mkdir(exist_ok=True)
        return artifacts

    async def write_text(
        self,
        chat_id: str,
        *,
        name: str,
        content: str,
        encoding: str = "utf-8",
    ) -> Artifact:
        async with self._locks.setdefault(chat_id, asyncio.Lock()):
            return await asyncio.to_thread(
                self._write_text,
                chat_id,
                name,
                content,
                encoding,
            )

    def _write_text(
        self,
        chat_id: str,
        name: str,
        content: str,
        encoding: str,
    ) -> Artifact:
        directory = self.artifacts_dir(chat_id)
        safe_name = _safe_filename(name)
        destination = _unique_destination(directory / safe_name)
        temporary = directory / f".artifact-{uuid.uuid4().hex}.tmp"
        try:
            temporary.write_text(content, encoding=encoding)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)

        return Artifact(
            name=destination.name,
            path=destination,
            size_bytes=destination.stat().st_size,
        )

    async def list_artifacts(self, chat_id: str) -> tuple[Artifact, ...]:
        return await asyncio.to_thread(self._list_artifacts, chat_id)

    def _list_artifacts(self, chat_id: str) -> tuple[Artifact, ...]:
        directory = self.artifacts_dir(chat_id)
        items = [
            Artifact(
                name=path.name,
                path=path,
                size_bytes=path.stat().st_size,
            )
            for path in directory.iterdir()
            if path.is_file() and not path.is_symlink()
        ]
        return tuple(sorted(items, key=lambda item: item.name.casefold()))


def _safe_filename(name: str) -> str:
    candidate = Path(str(name).replace("\\", "/")).name.strip()
    candidate = "".join(
        char if char.isprintable() and char not in {"\r", "\n", "\t"} else "_"
        for char in candidate
    ).strip()
    if candidate in {"", ".", ".."}:
        candidate = "artifact.html"
    return candidate[:240]


def _unique_destination(destination: Path) -> Path:
    candidate = destination
    index = 2
    while candidate.exists() or candidate.is_symlink():
        candidate = destination.with_name(
            f"{destination.stem} ({index}){destination.suffix}"
        )
        index += 1
    return candidate
