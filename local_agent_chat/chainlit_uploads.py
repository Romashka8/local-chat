from __future__ import annotations

from pathlib import Path
from typing import Any

from .sandbox_files import SandboxFiles, StoredFile


async def persist_message_uploads(
    *,
    chat_id: str,
    elements: list[Any] | None,
    sandbox: SandboxFiles,
) -> tuple[StoredFile, ...]:
    """Copy spontaneous Chainlit uploads into application-owned Chat storage."""

    stored: list[StoredFile] = []
    for element in elements or []:
        source = getattr(element, "path", None)
        if not source:
            continue

        source_path = Path(str(source))
        name = getattr(element, "name", None) or source_path.name
        stored.append(
            await sandbox.upload(
                chat_id,
                source_path,
                str(name),
            )
        )

    return tuple(stored)
