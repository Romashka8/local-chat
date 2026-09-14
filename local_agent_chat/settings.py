from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    checkpoints_db: Path
    chainlit_db: Path


def load_settings() -> Settings:
    project_root = Path(__file__).resolve().parent.parent

    data_dir = Path(
        os.getenv(
            "APP_DATA_DIR",
            str(project_root / ".local-agent-chat")
        )
    ).expanduser()

    return Settings(
        data_dir=data_dir,
        checkpoints_db=data_dir / "checkpoints.sqlite3",
        chainlit_db=data_dir / "chainlit.sqlite3"
    )
