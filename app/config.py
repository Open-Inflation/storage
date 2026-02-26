from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _default_convert_concurrency() -> int:
    return max(1, (os.cpu_count() or 1) // 2)


def _load_int_setting(
    name: str,
    default: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        value = default
    else:
        try:
            value = int(raw_value)
        except ValueError as exc:
            raise ValueError(f"{name} must be an integer") from exc

    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must be <= {maximum}")
    return value


@dataclass(frozen=True)
class Settings:
    api_token: str
    storage_dir: Path
    webp_quality: int = 80
    webp_method: int = 2
    max_image_side: int = 0
    max_convert_concurrency: int = field(default_factory=_default_convert_concurrency)


def load_settings() -> Settings:
    api_token = os.getenv("STORAGE_API_TOKEN", "change-me-token")
    storage_dir = Path(os.getenv("STORAGE_DIR", "data/images")).expanduser().resolve()
    webp_quality = _load_int_setting("STORAGE_WEBP_QUALITY", 80, minimum=0, maximum=100)
    webp_method = _load_int_setting("STORAGE_WEBP_METHOD", 2, minimum=0, maximum=6)
    max_image_side = _load_int_setting("STORAGE_MAX_IMAGE_SIDE", 0, minimum=0)
    max_convert_concurrency = _load_int_setting(
        "STORAGE_MAX_CONVERT_CONCURRENCY",
        _default_convert_concurrency(),
        minimum=1,
    )
    return Settings(
        api_token=api_token,
        storage_dir=storage_dir,
        webp_quality=webp_quality,
        webp_method=webp_method,
        max_image_side=max_image_side,
        max_convert_concurrency=max_convert_concurrency,
    )
