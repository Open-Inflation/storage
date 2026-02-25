from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    api_token: str
    storage_dir: Path


def load_settings() -> Settings:
    api_token = os.getenv("STORAGE_API_TOKEN", "change-me-token")
    storage_dir = Path(os.getenv("STORAGE_DIR", "data/images")).expanduser().resolve()
    return Settings(api_token=api_token, storage_dir=storage_dir)
