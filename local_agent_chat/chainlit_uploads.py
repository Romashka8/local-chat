from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from .local_storage import LocalStorageClient
from .sandbox_files import SandboxFiles, StoredFile

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _ResolvedUpload:
    path: Path
    source: str


def _element_mapping(element: Any) -> dict[str, Any]:
    to_dict = getattr(element, "to_dict", None)
    if not callable(to_dict):
        return {}
    try:
        value = to_dict()
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _first_value(element: Any, mapping: dict[str, Any], *names: str) -> Any:
    for name in names:
        value = getattr(element, name, None)
        if value is not None:
            return value
        if name in mapping and mapping[name] is not None:
            return mapping[name]
    return None


def _existing_path(value: Any) -> Path | None:
    if value is None:
        return None
    try:
        path = Path(str(value)).expanduser()
    except (TypeError, ValueError):
        return None
    return path if path.is_file() else None


def _resolve_from_session_files(
    element: Any,
    mapping: dict[str, Any],
    session_files: Mapping[str, Any] | None,
) -> Path | None:
    """Resolve an upload through Chainlit's authoritative websocket file map.

    Chainlit stores every spontaneous upload in ``WebsocketSession.files`` and
    puts that file id into the message element as ``chainlit_key``.  This is a
    stronger current-turn contract than relying on SQL element persistence,
    where local ``path`` is not a durable field.
    """
    if not session_files:
        return None

    keys: list[str] = []
    for value in (
        _first_value(element, mapping, "chainlit_key", "chainlitKey"),
        _first_value(element, mapping, "id"),
    ):
        if value is not None:
            key = str(value)
            if key not in keys:
                keys.append(key)

    for key in keys:
        file_info = session_files.get(key)
        if not isinstance(file_info, Mapping):
            continue
        path = _existing_path(file_info.get("path"))
        if path is not None:
            return path

    return None


def _resolve_uploaded_source(
    element: Any,
    *,
    session_files: Mapping[str, Any] | None,
    storage: LocalStorageClient,
) -> _ResolvedUpload | None:
    mapping = _element_mapping(element)

    # 1. Fast path: Chainlit normally exposes the current upload path directly
    #    on the in-memory Element passed to @cl.on_message.
    path = _existing_path(_first_value(element, mapping, "path"))
    if path is not None:
        return _ResolvedUpload(path=path, source="element.path")

    # 2. Authoritative current-session fallback.  This is important because
    #    element.to_dict()/SQL persistence are not required to retain `path`.
    path = _resolve_from_session_files(element, mapping, session_files)
    if path is not None:
        return _ResolvedUpload(path=path, source="session.files")

    # 3. Persisted UI blob fallback.  Depending on scheduling, create_element
    #    may already have populated object_key/url through our storage client.
    object_key = _first_value(
        element,
        mapping,
        "object_key",
        "objectKey",
    )
    if object_key:
        try:
            path = storage.path_for(str(object_key))
        except ValueError:
            path = None
        if path is not None and path.is_file():
            return _ResolvedUpload(path=path, source="storage.object_key")

    url = _first_value(element, mapping, "url")
    if url:
        parsed_path = unquote(urlparse(str(url)).path)
        prefix = storage.public_prefix + "/"
        if parsed_path.startswith(prefix):
            object_key = parsed_path[len(prefix) :]
            try:
                path = storage.path_for(object_key)
            except ValueError:
                path = None
            if path is not None and path.is_file():
                return _ResolvedUpload(path=path, source="storage.url")

    return None


async def persist_message_uploads(
    *,
    chat_id: str,
    elements: list[Any] | None,
    sandbox: SandboxFiles,
    storage: LocalStorageClient,
    session_files: Mapping[str, Any] | None = None,
) -> tuple[StoredFile, ...]:
    """Copy Chainlit message attachments into the current Chat sandbox.

    A visible attachment must never be silently ignored.  We resolve the bytes
    from Chainlit's in-memory element/session first and from persisted UI blob
    storage second.  If none of those contracts yields a file, fail explicitly
    before the agent runs so UI state cannot diverge from agent state.
    """

    stored: list[StoredFile] = []
    for element in elements or []:
        mapping = _element_mapping(element)
        resolved = _resolve_uploaded_source(
            element,
            session_files=session_files,
            storage=storage,
        )

        name = _first_value(element, mapping, "name")
        fallback_name = resolved.path.name if resolved is not None else "upload"
        filename = str(name or fallback_name)

        if resolved is None:
            logger.error(
                "Unable to resolve Chainlit attachment: chat=%s name=%r "
                "element_type=%s element_id=%r chainlit_key=%r path=%r "
                "object_key=%r url=%r session_file_keys=%r",
                chat_id,
                filename,
                type(element).__name__,
                _first_value(element, mapping, "id"),
                _first_value(element, mapping, "chainlit_key", "chainlitKey"),
                _first_value(element, mapping, "path"),
                _first_value(element, mapping, "object_key", "objectKey"),
                _first_value(element, mapping, "url"),
                tuple(session_files.keys()) if session_files else (),
            )
            raise RuntimeError(
                f"Файл {filename!r} виден в Chainlit, но backend не смог "
                "получить его байты для sandbox агента."
            )

        item = await sandbox.upload(
            chat_id,
            resolved.path,
            filename,
        )
        stored.append(item)
        logger.info(
            "Persisted chat attachment: chat=%s name=%r bytes=%s source=%s",
            chat_id,
            item.name,
            item.size_bytes,
            resolved.source,
        )

    return tuple(stored)
