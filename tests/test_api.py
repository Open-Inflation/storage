from __future__ import annotations

from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

from fastapi.testclient import TestClient
from PIL import Image

from app.config import Settings
from app.main import create_app


def _png_bytes() -> bytes:
    stream = BytesIO()
    Image.new("RGB", (40, 40), color=(12, 200, 50)).save(stream, format="PNG")
    return stream.getvalue()


def _create_client(storage_dir: Path, token: str = "test-token") -> TestClient:
    settings = Settings(api_token=token, storage_dir=storage_dir)
    return TestClient(create_app(settings=settings))


def test_upload_requires_authorization(tmp_path: Path) -> None:
    client = _create_client(tmp_path)
    response = client.post("/api/images", files={"file": ("image.png", _png_bytes(), "image/png")})
    assert response.status_code == 401


def test_upload_converts_to_webp_and_redirects(tmp_path: Path) -> None:
    client = _create_client(tmp_path)
    response = client.post(
        "/api/images",
        headers={"Authorization": "Bearer test-token"},
        files={"file": ("image.png", _png_bytes(), "image/png")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    location = response.headers["location"]
    parsed = urlparse(location)
    assert parsed.path.startswith("/images/")
    assert parsed.path.endswith(".webp")

    stored_name = parsed.path.rsplit("/", 1)[1]
    stored_file = tmp_path / stored_name
    assert stored_file.exists()

    with Image.open(stored_file) as converted:
        assert converted.format == "WEBP"

    public_response = client.get(parsed.path)
    assert public_response.status_code == 200
    assert public_response.headers["content-type"].startswith("image/webp")


def test_upload_rejects_invalid_image(tmp_path: Path) -> None:
    client = _create_client(tmp_path)
    response = client.post(
        "/api/images",
        headers={"Authorization": "Bearer test-token"},
        files={"file": ("bad.bin", b"not an image", "application/octet-stream")},
    )
    assert response.status_code == 400
    assert "valid image" in response.json()["detail"]


def test_delete_requires_authorization(tmp_path: Path) -> None:
    client = _create_client(tmp_path)
    file_path = tmp_path / "sample.webp"
    file_path.write_bytes(b"test")

    response = client.delete("/api/images/sample.webp")
    assert response.status_code == 401
    assert file_path.exists()


def test_delete_removes_image(tmp_path: Path) -> None:
    client = _create_client(tmp_path)
    create_response = client.post(
        "/api/images",
        headers={"Authorization": "Bearer test-token"},
        files={"file": ("image.png", _png_bytes(), "image/png")},
        follow_redirects=False,
    )
    image_path = urlparse(create_response.headers["location"]).path
    image_name = image_path.rsplit("/", 1)[1]
    stored_file = tmp_path / image_name
    assert stored_file.exists()

    delete_response = client.delete(
        f"/api/images/{image_name}",
        headers={"Authorization": "Bearer test-token"},
    )
    assert delete_response.status_code == 204
    assert not stored_file.exists()

    public_response = client.get(image_path)
    assert public_response.status_code == 404
