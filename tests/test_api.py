from __future__ import annotations

import importlib
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image


def _png_bytes(width: int = 40, height: int = 40) -> bytes:
    stream = BytesIO()
    Image.new("RGB", (width, height), color=(12, 200, 50)).save(stream, format="PNG")
    return stream.getvalue()


def _create_app(
    monkeypatch,
    storage_dir: Path,
    *,
    token: str = "test-token",
    max_image_side: int = 0,
) -> FastAPI:
    permanent_dir = storage_dir.parent / f"{storage_dir.name}_permanent"

    monkeypatch.setenv("STORAGE_API_TOKEN", token)
    monkeypatch.setenv("STORAGE_DIR", str(storage_dir))
    monkeypatch.setenv("PERMANENT_DIR", str(permanent_dir))
    monkeypatch.setenv("STORAGE_MAX_IMAGE_SIDE", str(max_image_side))
    monkeypatch.setenv("STORAGE_WEBP_QUALITY", "80")
    monkeypatch.setenv("STORAGE_WEBP_METHOD", "2")
    monkeypatch.setenv("STORAGE_MAX_CONVERT_CONCURRENCY", "1")

    import app.main as app_main

    return importlib.reload(app_main).app


def test_upload_requires_authorization(tmp_path: Path, monkeypatch) -> None:
    app = _create_app(monkeypatch, tmp_path / "temp")
    with TestClient(app) as client:
        response = client.post(
            "/api/images/unauthorized.webp",
            files={"file": ("image.png", _png_bytes(), "image/png")},
        )
    assert response.status_code == 401


def test_upload_converts_to_webp_and_serves_public_url(tmp_path: Path, monkeypatch) -> None:
    storage_dir = tmp_path / "temp"
    app = _create_app(monkeypatch, storage_dir)

    with TestClient(app) as client:
        response = client.post(
            "/api/images/sample.webp",
            headers={"Authorization": "Bearer test-token"},
            files={"file": ("image.png", _png_bytes(), "image/png")},
        )

        assert response.status_code == 201
        location = response.headers["location"]
        parsed = urlparse(location)
        assert parsed.path == "/images/sample.webp"

        stored_file = storage_dir / "sample.webp"
        assert stored_file.exists()

        with Image.open(stored_file) as converted:
            assert converted.format == "WEBP"

        public_response = client.get(parsed.path)
        assert public_response.status_code == 200
        assert public_response.headers["content-type"].startswith("image/webp")

        head_response = client.head(parsed.path)
        assert head_response.status_code == 200
        assert int(head_response.headers["content-length"]) == stored_file.stat().st_size


def test_upload_rejects_invalid_image(tmp_path: Path, monkeypatch) -> None:
    storage_dir = tmp_path / "temp"
    app = _create_app(monkeypatch, storage_dir)

    with TestClient(app) as client:
        response = client.post(
            "/api/images/bad.webp",
            headers={"Authorization": "Bearer test-token"},
            files={"file": ("bad.bin", b"not an image", "application/octet-stream")},
        )

    assert response.status_code == 400
    assert "invalid" in response.json()["detail"].lower()
    assert not list(storage_dir.glob("*.webp"))


def test_upload_resizes_to_max_side_when_configured(tmp_path: Path, monkeypatch) -> None:
    storage_dir = tmp_path / "temp"
    app = _create_app(monkeypatch, storage_dir)

    with TestClient(app) as client:
        response = client.post(
            "/api/images/resized.webp?max_image_side=20",
            headers={"Authorization": "Bearer test-token"},
            files={"file": ("image.png", _png_bytes(width=120, height=60), "image/png")},
        )

    assert response.status_code == 201

    stored_file = storage_dir / "resized.webp"
    with Image.open(stored_file) as converted:
        assert max(converted.size) <= 20


def test_delete_requires_authorization(tmp_path: Path, monkeypatch) -> None:
    storage_dir = tmp_path / "temp"
    storage_dir.mkdir(parents=True, exist_ok=True)
    file_path = storage_dir / "sample.webp"
    file_path.write_bytes(b"test")

    app = _create_app(monkeypatch, storage_dir)
    with TestClient(app) as client:
        response = client.delete("/api/images/sample.webp")

    assert response.status_code == 401
    assert file_path.exists()


def test_delete_removes_image(tmp_path: Path, monkeypatch) -> None:
    storage_dir = tmp_path / "temp"
    app = _create_app(monkeypatch, storage_dir)

    with TestClient(app) as client:
        create_response = client.post(
            "/api/images/delete-me.webp",
            headers={"Authorization": "Bearer test-token"},
            files={"file": ("image.png", _png_bytes(), "image/png")},
        )
        assert create_response.status_code == 201

        delete_response = client.delete(
            "/api/images/delete-me.webp",
            headers={"Authorization": "Bearer test-token"},
        )
        assert delete_response.status_code == 204

        stored_file = storage_dir / "delete-me.webp"
        assert not stored_file.exists()

        public_response = client.get("/images/delete-me.webp")
        assert public_response.status_code == 404


def test_persist_moves_image_to_permanent_storage_and_redirects(tmp_path: Path, monkeypatch) -> None:
    storage_dir = tmp_path / "temp"
    permanent_dir = tmp_path / "temp_permanent"
    app = _create_app(monkeypatch, storage_dir)

    with TestClient(app) as client:
        create_response = client.post(
            "/api/images/keep.webp",
            headers={"Authorization": "Bearer test-token"},
            files={"file": ("image.png", _png_bytes(), "image/png")},
        )
        assert create_response.status_code == 201

        persist_response = client.post(
            "/api/images/keep.webp/persist",
            headers={"Authorization": "Bearer test-token"},
            follow_redirects=False,
        )

        assert persist_response.status_code == 303
        location = persist_response.headers["location"]
        parsed = urlparse(location)
        assert parsed.path == "/images-permanent/keep.webp"

        assert not (storage_dir / "keep.webp").exists()
        assert (permanent_dir / "keep.webp").exists()

        public_response = client.get(parsed.path)
        assert public_response.status_code == 200
        assert public_response.headers["content-type"].startswith("image/webp")

        head_response = client.head(parsed.path)
        assert head_response.status_code == 200
        assert int(head_response.headers["content-length"]) == (permanent_dir / "keep.webp").stat().st_size
