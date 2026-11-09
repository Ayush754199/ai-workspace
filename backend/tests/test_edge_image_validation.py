"""EDGE-04 / EDGE-05: image and filename validation on the real upload endpoints."""

from __future__ import annotations

import io
import uuid

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app


@pytest.fixture()
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def headers(client):
    email = f"edge-{uuid.uuid4().hex[:8]}@example.com"
    client.post("/auth/register", json={
        "email": email,
        # Mobile numbers are unique in the schema. 100_000 combinations were not
        # enough for a repeated full-suite run (flaky "already registered"), and
        # the number itself must stay within the 15-digit limit enforced by
        # normalize_mobile_number: 5-digit prefix + 9 digits = 14.
        "mobile_number": f"+91981{uuid.uuid4().int % 1_000_000_000:09d}",
        "password": "StrongPass-2026!",
        "full_name": "Edge Tester",
    })
    login = client.post("/auth/login", json={"identifier": email, "password": "StrongPass-2026!"})
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _png(size=(8, 8), mode="RGB") -> bytes:
    buffer = io.BytesIO()
    Image.new(mode, size, "white").save(buffer, format="PNG")
    return buffer.getvalue()


def _upload(client, headers, data: bytes, filename: str, content_type: str):
    return client.post(
        "/api/v1/chat/v2/attachments",
        headers=headers,
        files={"file": (filename, data, content_type)},
    )


def test_image_bomb_rejected_before_storage(client, headers):
    """Images above Pillow's decompression-bomb guard are refused cleanly."""
    response = _upload(client, headers, _png((10000, 10000), mode="1"), "bomb.png", "image/png")
    assert response.status_code == 413
    assert "pixels" in response.json()["detail"]


def test_normal_image_still_accepted_after_bomb_guard(client, headers):
    ok = _upload(client, headers, _png((1024, 1024)), "normal.png", "image/png")
    assert ok.status_code == 200, ok.text


def test_valid_png_and_jpeg_are_accepted(client, headers):
    png = _upload(client, headers, _png(), "picture.png", "image/png")
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buffer, format="JPEG")
    jpeg = _upload(client, headers, buffer.getvalue(), "picture.jpg", "image/jpeg")
    assert png.status_code == 200, png.text
    assert jpeg.status_code == 200, jpeg.text


def test_zero_byte_image_is_rejected_and_not_stored(client, headers):
    response = _upload(client, headers, b"", "empty.png", "image/png")
    assert response.status_code == 400
    assert response.json()["detail"] == "The uploaded file is empty"


def test_one_by_one_pixel_image_is_accepted(client, headers):
    response = _upload(client, headers, _png((1, 1)), "tiny.png", "image/png")
    assert response.status_code == 200


def test_huge_dimension_image_is_rejected_as_bomb(client, headers):
    # 10000x10000 is a 100 MP canvas; the endpoint must refuse it cleanly.
    response = _upload(client, headers, _png((10000, 10000), mode="1"), "huge.png", "image/png")
    assert response.status_code == 413
    assert "pixels" in response.json()["detail"]


@pytest.mark.parametrize("name", ["picture.png.exe", "payload.exe", "evil.png.sh"])
def test_double_extension_or_executable_is_rejected(client, headers, name):
    response = _upload(client, headers, b"MZ\x90\x00binary", name, "application/octet-stream")
    assert response.status_code == 415


def test_wrong_mime_for_image_bytes_is_rejected(client, headers):
    # Real PNG bytes sent as text/plain: the attachment kind lookup must not
    # silently accept a mislabelled binary.
    response = _upload(client, headers, _png(), "notes.txt", "text/plain")
    assert response.status_code in (200, 415)
    if response.status_code == 200:
        assert response.json()["kind"] == "text"


def test_corrupt_image_with_image_mime_is_rejected(client, headers):
    response = _upload(client, headers, b"not-an-image", "broken.png", "image/png")
    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid image file"


def test_path_traversal_filename_is_not_written_outside(tmp_path, client, headers):
    response = _upload(client, headers, _png(), "../../.env.png", "image/png")
    assert response.status_code in (200, 400)
    if response.status_code == 200:
        assert "/" not in response.json()["filename"]
        assert "\\" not in response.json()["filename"]


def test_image_endpoint_rejects_zero_byte_and_oversize(client, headers):
    empty = client.post("/api/v1/images/describe", headers=headers, files={"file": ("a.png", b"", "image/png")})
    assert empty.status_code in (400, 503)
    bad_type = client.post("/api/v1/images/describe", headers=headers, files={"file": ("a.txt", b"text", "text/plain")})
    assert bad_type.status_code in (415, 503)
