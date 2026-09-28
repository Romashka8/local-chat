from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    checkpoints_db: Path
    chainlit_db: Path
    runtime_history_db: Path
    knowledge_db: Path
    sandboxes_dir: Path
    blobs_dir: Path
    max_upload_file_bytes: int
    max_chat_files_bytes: int
    analyst_max_dataset_rows: int
    analyst_max_columns: int


def _mb_env(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value * 1024 * 1024


def _positive_int_env(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def load_settings() -> Settings:
    project_root = Path(__file__).resolve().parent.parent

    data_dir = Path(
        os.getenv(
            "APP_DATA_DIR",
            str(project_root / ".local-agent-chat"),
        )
    ).expanduser()

    return Settings(
        data_dir=data_dir,
        checkpoints_db=data_dir / "checkpoints.sqlite3",
        chainlit_db=data_dir / "chainlit.sqlite3",
        runtime_history_db=data_dir / "runtime-history.sqlite3",
        knowledge_db=data_dir / "knowledge.sqlite3",
        sandboxes_dir=data_dir / "sandboxes",
        blobs_dir=data_dir / "blobs",
        max_upload_file_bytes=_mb_env("MAX_UPLOAD_FILE_MB", 20),
        max_chat_files_bytes=_mb_env("MAX_CHAT_FILES_MB", 200),
        analyst_max_dataset_rows=_positive_int_env("ANALYST_MAX_DATASET_ROWS", 200_000),
        analyst_max_columns=_positive_int_env("ANALYST_MAX_COLUMNS", 200),
    )
