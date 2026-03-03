from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import RedirectResponse
from PIL import Image, ImageFile
from starlette.concurrency import run_in_threadpool
from app.tools import (
    require_token,
    get_conversion_slots,
    get_settings,
    _atomic_move_file,
    _save_webp_from_stream,
    _validate_image_name
)

from app.config import Settings, load_settings

# --- Configuration of PIL / logging ---
# Protect from decompression bombs / extremely large images
Image.MAX_IMAGE_PIXELS = 50_000_000
ImageFile.LOAD_TRUNCATED_IMAGES = False

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger("image_storage")


# --- Lifespan management ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize application resources on startup and clean up on shutdown."""
    # Load settings
    settings = load_settings()
    
    # Create storage directories if they don't exist
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    settings.permanent_storage_dir.mkdir(parents=True, exist_ok=True)
    
    # Store settings and shared resources in app.state
    app.state.settings = settings
    app.state.conversion_slots = asyncio.Semaphore(
        max(1, getattr(settings, "max_convert_concurrency", 2))
    )
    
    LOGGER.info(f"Storage directories initialized: {settings.storage_dir}, {settings.permanent_storage_dir}")
    
    yield
    
    # Cleanup if needed
    LOGGER.info("Shutting down application")


# --- Create FastAPI app with lifespan ---
app = FastAPI(
    title="Image Storage",
    description="Static image storage with token-protected upload/delete and public serving",
    version="1.1.0",
    lifespan=lifespan,
    docs_url="/",
)

# --- Health check endpoint ---
@app.get("/healthz")
async def healthcheck() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok"}


# --- Upload endpoint ---
@app.post(
    "/api/images/{image_name}",
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"description": "Image uploaded"},
        400: {"description": "Invalid image or name"},
        401: {"description": "Invalid token"},
        409: {"description": "Image already exists"},
        500: {"description": "Internal error"},
    },
)
async def upload_image(
    request: Request,
    image_name: str,
    file: UploadFile = File(...),
    _: None = Depends(require_token),
    quality: int = 80,
    method: int = 6,
    max_image_side: int = 0,
    overwrite: bool = False,
    settings: Settings = Depends(get_settings),
    slots: asyncio.Semaphore = Depends(get_conversion_slots),
) -> Response:
    """
    Upload an image as WEBP. Accepts arbitrary input formats and converts to WEBP.
    Query params:
      - quality (int), method (int), max_image_side (int)
      - overwrite (bool) - whether to overwrite existing file
    """
    validated_name = _validate_image_name(image_name)
    dest_path = settings.storage_dir / validated_name

    if dest_path.exists() and not overwrite:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, 
            detail="Image already exists"
        )

    # Use semaphore to limit concurrent image processing
    async with slots:
        try:
            # run the blocking image processing in a threadpool
            await run_in_threadpool(
                _save_webp_from_stream,
                file.file,
                dest_path,
                quality=quality,
                method=method,
                max_image_side=max_image_side,
            )
        except HTTPException:
            raise
        except Exception as exc:
            LOGGER.exception("Failed to save uploaded image %s: %s", validated_name, exc)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
                detail="Failed to save image"
            )

    image_url = request.url_for("images", path=validated_name)
    LOGGER.info("Uploaded image %s -> %s", validated_name, dest_path)
    return Response(
        status_code=status.HTTP_201_CREATED, 
        headers={"Location": str(image_url)}
    )


# --- Persist endpoint ---
@app.post(
    "/api/images/{image_name}/persist",
    status_code=status.HTTP_303_SEE_OTHER,
    responses={
        303: {"description": "Image moved. Redirects to permanent image URL"},
        401: {"description": "Invalid token"},
        404: {"description": "Image not found"},
        409: {"description": "Destination exists"},
        500: {"description": "Failed to move image"},
    },
)
async def persist_image(
    request: Request,
    image_name: str,
    _: None = Depends(require_token),
    overwrite: bool = False,
    settings: Settings = Depends(get_settings),
    slots: asyncio.Semaphore = Depends(get_conversion_slots),
) -> Response:
    """
    Move image from temporary storage to permanent storage.
    If overwrite is False and destination exists -> 409.
    Redirects to the permanent image URL (303).
    """
    validated_name = _validate_image_name(image_name)
    source_path = settings.storage_dir / validated_name
    
    if not source_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail="Image not found"
        )

    destination_path = settings.permanent_storage_dir / validated_name

    try:
        # use semaphore to avoid concurrent moves causing races
        async with slots:
            await _atomic_move_file(source_path, destination_path, overwrite=bool(overwrite))
    except FileExistsError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, 
            detail="Destination already exists"
        )
    except OSError as exc:
        LOGGER.exception("Failed to move %s -> %s: %s", source_path, destination_path, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail="Failed to move image"
        ) from exc

    image_url = request.url_for("images_permanent", path=validated_name)
    LOGGER.info("Persisted image %s -> %s", validated_name, destination_path)
    headers = {"Cache-Control": "no-store"}
    return RedirectResponse(
        url=str(image_url), 
        status_code=status.HTTP_303_SEE_OTHER, 
        headers=headers
    )


# --- Delete endpoint ---
@app.delete(
    "/api/images/{image_name}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        204: {"description": "Deleted"},
        401: {"description": "Invalid token"},
        404: {"description": "Image not found"},
    },
)
async def delete_image(
    image_name: str,
    _: None = Depends(require_token),
    scope: Optional[str] = "temp",  # "temp" or "permanent" or "both"
    settings: Settings = Depends(get_settings),
) -> Response:
    """
    Delete an image. Use scope=temp|permanent|both to control which storage to target.
    Default: temp (temporary storage).
    """
    validated_name = _validate_image_name(image_name)

    deleted_any = False
    errors = []

    if scope in ("temp", "both"):
        temp_path = settings.storage_dir / validated_name
        if temp_path.exists():
            try:
                await run_in_threadpool(temp_path.unlink)
                LOGGER.info("Deleted temp image %s", temp_path)
                deleted_any = True
            except OSError as exc:
                errors.append(str(exc))

    if scope in ("permanent", "both"):
        perm_path = settings.permanent_storage_dir / validated_name
        if perm_path.exists():
            try:
                await run_in_threadpool(perm_path.unlink)
                LOGGER.info("Deleted permanent image %s", perm_path)
                deleted_any = True
            except OSError as exc:
                errors.append(str(exc))

    if not deleted_any:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail="Image not found"
        )

    if errors:
        LOGGER.warning("Errors during deletion of %s: %s", validated_name, errors)

    return Response(status_code=status.HTTP_204_NO_CONTENT)
