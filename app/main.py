from __future__ import annotations

from io import BytesIO
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError

from app.config import Settings, load_settings


def _check_token(request: Request, settings: Settings) -> None:
    authorization = request.headers.get("Authorization")
    expected_value = f"Bearer {settings.api_token}"
    if authorization != expected_value:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


def _validate_image_name(image_name: str) -> str:
    candidate = Path(image_name)
    if candidate.name != image_name or candidate.suffix.lower() != ".webp":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="image_name must be a .webp file name without path segments",
        )
    return image_name


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or load_settings()
    app_settings.storage_dir.mkdir(parents=True, exist_ok=True)

    app = FastAPI(
        title="Image Storage",
        description="Static image storage with token-protected upload/delete and public serving",
        version="1.0.0",
    )

    def require_token(request: Request) -> None:
        _check_token(request, app_settings)

    @app.get("/healthz")
    async def healthcheck() -> dict[str, str]:
        return {"status": "ok"}

    @app.post(
        "/api/images",
        status_code=status.HTTP_303_SEE_OTHER,
        responses={
            303: {"description": "Image saved. Redirects to public image URL"},
            400: {"description": "Uploaded file is not a valid image"},
            401: {"description": "Invalid token"},
        },
    )
    async def upload_image(
        request: Request,
        file: UploadFile = File(...),
        _: None = Depends(require_token),
    ) -> Response:
        raw_image = await file.read()
        await file.close()

        image_name = f"{uuid4().hex}.webp"
        image_path = app_settings.storage_dir / image_name

        try:
            with Image.open(BytesIO(raw_image)) as image:
                image.convert("RGB").save(image_path, format="WEBP")
        except UnidentifiedImageError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded file is not a valid image",
            ) from exc

        image_url = request.url_for("images", path=image_name)
        return RedirectResponse(url=str(image_url), status_code=status.HTTP_303_SEE_OTHER)

    @app.delete(
        "/api/images/{image_name}",
        status_code=status.HTTP_204_NO_CONTENT,
        responses={401: {"description": "Invalid token"}, 404: {"description": "Image not found"}},
    )
    async def delete_image(
        image_name: str,
        _: None = Depends(require_token),
    ) -> Response:
        validated_name = _validate_image_name(image_name)
        image_path = app_settings.storage_dir / validated_name
        if not image_path.exists():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found")

        image_path.unlink()
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    app.mount("/images", StaticFiles(directory=app_settings.storage_dir), name="images")
    return app


app = create_app()
