from __future__ import annotations

import asyncio
import hmac
from pathlib import Path
from shutil import move
from typing import BinaryIO

from fastapi import Depends, HTTPException, Request, status
from PIL import Image, UnidentifiedImageError
from starlette.concurrency import run_in_threadpool

from app.config import Settings


# --- Dependencies ---
async def get_settings(request: Request) -> Settings:
    """Dependency to get application settings from app.state."""
    return request.app.state.settings


async def get_conversion_slots(request: Request) -> asyncio.Semaphore:
    """Dependency to get the conversion semaphore from app.state."""
    return request.app.state.conversion_slots


async def require_token(
    request: Request, 
    settings: Settings = Depends(get_settings)
) -> None:
    """
    Constant-time comparison for authorization header.
    Expects header: Authorization: Bearer <token>
    """
    authorization = request.headers.get("Authorization") or ""
    expected_value = f"Bearer {settings.api_token}"
    if not hmac.compare_digest(authorization, expected_value):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Invalid token"
        )


# --- Helpers ---
def _validate_image_name(image_name: str) -> str:
    """
    Ensure a plain file name with .webp suffix (lowercase).
    """
    candidate = Path(image_name)
    if candidate.name != image_name or candidate.suffix.lower() != ".webp":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="image_name must be a .webp file name without path segments",
        )
    return image_name


def _save_webp_from_stream(
    source_stream: BinaryIO,
    destination: Path,
    *,
    quality: int = 80,
    method: int = 6,
    max_image_side: int = 0,
) -> None:
    """
    Open an image from a binary stream, optionally thumbnail it, and save as WEBP.
    Runs synchronously; should be delegated to threadpool by caller.
    """
    source_stream.seek(0)
    try:
        with Image.open(source_stream) as image:
            # verify image to catch truncated or invalid content
            image.verify()
    except (UnidentifiedImageError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="Invalid or unsafe image"
        ) from exc

    # Re-open for actual processing because verify() leaves the file in an unusable state
    source_stream.seek(0)
    with Image.open(source_stream) as image:
        if max_image_side and max_image_side > 0:
            image.thumbnail((max_image_side, max_image_side), Image.Resampling.LANCZOS)
        image = image.convert("RGB")
        image.save(destination, format="WEBP", quality=int(quality), method=int(method))


async def _atomic_move_file(
    source: Path, dest: Path, *, overwrite: bool = False
) -> None:
    """
    Try to use atomic Path.replace when possible. Fall back to shutil.move.
    Runs blocking operations in threadpool when called from async context.
    """
    def _sync_move():
        if dest.exists():
            if not overwrite:
                raise FileExistsError(f"Destination exists: {dest}")
            # attempt atomic replace first
            try:
                source.replace(dest)
                return
            except OSError:
                # fall back to move (copy+delete)
                pass
        else:
            try:
                source.replace(dest)
                return
            except OSError:
                # fall back to move
                pass
        # final fallback
        move(str(source), str(dest))

    await run_in_threadpool(_sync_move)
