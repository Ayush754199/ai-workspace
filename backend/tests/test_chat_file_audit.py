"""Audit tests for in-chat file handling: storage, ownership and real generators.

These never call an AI provider (no quota used). Generation is exercised through
the same ``app.services.documents`` renderers the chat endpoint uses.
"""

from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("ATTACHMENT_STORAGE_DIR", tempfile.mkdtemp(prefix="attach-audit-"))

from app.services import document_store, documents  # noqa: E402


def _docx_bytes() -> bytes:
    return documents.generate_docx("Audit", "# Heading\n\nA paragraph.\n\n- one\n- two")


def _xlsx_bytes() -> bytes:
    return documents.generate_xlsx([{"name": "a", "value": 1}, {"name": "b", "value": 2}], title="Audit")


def _pptx_bytes() -> bytes:
    return documents.generate_pptx("Audit", "## Slide one\n- alpha\n- beta\n## Slide two\n- gamma")


def _pdf_bytes() -> bytes:
    return documents.generate_pdf("Audit", "# Title\n\nBody text for the audit.")


# --- storage -----------------------------------------------------------------


def test_storage_round_trip_and_dedupe(tmp_path, monkeypatch):
    monkeypatch.setenv("ATTACHMENT_STORAGE_DIR", str(tmp_path))
    first = document_store.save_bytes(11, "a.docx", b"same-bytes")
    second = document_store.save_bytes(11, "b.docx", b"same-bytes")
    assert first == second, "identical content should reuse one file"
    assert document_store.load_bytes(first) == b"same-bytes"


def test_storage_is_user_scoped(tmp_path, monkeypatch):
    monkeypatch.setenv("ATTACHMENT_STORAGE_DIR", str(tmp_path))
    owner = document_store.save_bytes(11, "a.txt", b"secret")
    other = document_store.save_bytes(22, "a.txt", b"secret")
    assert owner != other
    assert owner.startswith("11")
    assert other.startswith("22")


def test_storage_path_traversal_blocked(tmp_path, monkeypatch):
    monkeypatch.setenv("ATTACHMENT_STORAGE_DIR", str(tmp_path))
    assert document_store.load_bytes("../../Windows/win.ini") is None
    assert document_store.load_bytes(None) is None


def test_storage_rejects_empty_and_oversized(tmp_path, monkeypatch):
    monkeypatch.setenv("ATTACHMENT_STORAGE_DIR", str(tmp_path))
    with pytest.raises(ValueError):
        document_store.save_bytes(1, "empty.txt", b"")
    monkeypatch.setattr(document_store, "MAX_STORED_BYTES", 4)
    with pytest.raises(ValueError):
        document_store.save_bytes(1, "big.txt", b"12345")


def test_missing_file_returns_none_not_bytes(tmp_path, monkeypatch):
    monkeypatch.setenv("ATTACHMENT_STORAGE_DIR", str(tmp_path))
    assert document_store.load_bytes("11/deadbeef.txt") is None


# --- real generators ---------------------------------------------------------


@pytest.mark.parametrize(
    "name,builder,magic",
    [
        ("docx", _docx_bytes, b"PK"),
        ("xlsx", _xlsx_bytes, b"PK"),
        ("pptx", _pptx_bytes, b"PK"),
        ("pdf", _pdf_bytes, b"%PDF"),
    ],
)
def test_generators_produce_real_files(name, builder, magic):
    data = builder()
    assert data[: len(magic)] == magic, f"{name} output is not a real file"
    assert len(data) > 200


def test_generated_files_are_readable_again():
    from app.services.documents import extract_docx_text, extract_pdf_text

    assert "Heading" in extract_docx_text(_docx_bytes())
    assert "Title" in extract_pdf_text(_pdf_bytes())


def test_scanned_pdf_uses_page_labelled_ocr_fallback(monkeypatch):
    from app.services import documents

    monkeypatch.setattr(documents, "extract_pdf_text", lambda _data: "")
    monkeypatch.setattr(documents, "extract_pdf_ocr", lambda _data: "## Page 2 (OCR)\nScanned invoice total: 42")

    kind, text = documents.extract_any(b"not-used-by-mocked-extractors", "scan.pdf", "application/pdf")

    assert kind == "pdf"
    assert "## Page 2 (OCR)" in text
    assert "Scanned invoice total: 42" in text


def test_extraction_helpers_cover_uploaded_office_files():
    from app.services.documents import extract_pptx_text, extract_xlsx_text

    assert "Slide one" in extract_pptx_text(_pptx_bytes())
    assert "a | 1" in extract_xlsx_text(_xlsx_bytes())


def test_csv_generator_round_trip():
    data = documents.generate_csv([{"name": "a", "value": 1}])
    assert data.decode("utf-8-sig").splitlines()[0] == "name,value"


# --- HTTP: upload, list, download authorization -------------------------------


def _register(client, email: str):
    import uuid

    client.post(
        "/auth/register",
        json={
            "email": email,
            "mobile_number": "+9198765" + str(uuid.uuid4().int)[:5],
            "password": "StrongPass123",
            "full_name": "File Auditor",
        },
    )
    login = client.post("/auth/login", json={"identifier": email, "password": "StrongPass123"})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


@pytest.fixture
def http_client():
    from fastapi.testclient import TestClient

    os.environ.setdefault("ATTACHMENT_STORAGE_DIR", tempfile.mkdtemp(prefix="attach-http-"))
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.mark.parametrize(
    "filename,mime,builder",
    [
        ("audit.pdf", "application/pdf", _pdf_bytes),
        ("audit.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", _docx_bytes),
        ("audit.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", _xlsx_bytes),
        ("audit.pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation", _pptx_bytes),
        ("audit.csv", "text/csv", lambda: b"name,value\na,1\n"),
        ("audit.png", "image/png", lambda: _png_bytes()),
    ],
)
def test_upload_extract_and_download(http_client, filename, mime, builder):
    headers = _register(http_client, f"upload-{filename.replace('.', '-')}@example.com")
    data = builder()
    uploaded = http_client.post(
        "/api/v1/chat/v2/attachments",
        files={"file": (filename, io.BytesIO(data), mime)},
        headers=headers,
    )
    assert uploaded.status_code == 200, uploaded.text
    body = uploaded.json()
    assert body["size_bytes"] == len(data)
    if mime.startswith("image/"):
        # OCR only runs when a vision provider is configured; no text is invented.
        assert body["has_text"] in {True, False}
    else:
        assert body["has_text"] is True, f"{filename} produced no extracted text"

    listed = http_client.get("/api/v1/chat/v2/attachments", headers=headers)
    assert listed.status_code == 200
    assert body["id"] in [row["id"] for row in listed.json()]

    download = http_client.get(f"/api/v1/chat/v2/attachments/{body['id']}/download", headers=headers)
    assert download.status_code == 200
    assert download.content == data


def test_office_upload_with_generic_browser_mime(http_client):
    headers = _register(http_client, "generic-mime@example.com")
    uploaded = http_client.post(
        "/api/v1/chat/v2/attachments",
        files={
            "file": (
                "generic.docx",
                io.BytesIO(_docx_bytes()),
                "application/octet-stream",
            )
        },
        headers=headers,
    )
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["has_text"] is True


def test_unsupported_extension_is_rejected(http_client):
    headers = _register(http_client, "reject-me@example.com")
    response = http_client.post(
        "/api/v1/chat/v2/attachments",
        files={"file": ("evil.exe", io.BytesIO(b"MZ..."), "application/octet-stream")},
        headers=headers,
    )
    assert response.status_code == 415


def test_download_forbidden_for_another_user(http_client):
    owner = _register(http_client, "owner-files@example.com")
    intruder = _register(http_client, "intruder-files@example.com")
    uploaded = http_client.post(
        "/api/v1/chat/v2/attachments",
        files={"file": ("private.txt", io.BytesIO(b"owner only"), "text/plain")},
        headers=owner,
    ).json()
    attempt = http_client.get(f"/api/v1/chat/v2/attachments/{uploaded['id']}/download", headers=intruder)
    assert attempt.status_code == 404
    assert attempt.content != b"owner only"


def test_orphaned_file_reports_gone_not_fake_bytes(http_client):
    headers = _register(http_client, "orphan-files@example.com")
    uploaded = http_client.post(
        "/api/v1/chat/v2/attachments",
        files={"file": ("orphan.txt", io.BytesIO(b"to be deleted"), "text/plain")},
        headers=headers,
    ).json()
    from app.services import document_store

    for path in Path(document_store.storage_root()).rglob("*.txt"):
        path.unlink()
    response = http_client.get(f"/api/v1/chat/v2/attachments/{uploaded['id']}/download", headers=headers)
    assert response.status_code == 410


def _png_bytes() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (10, 20, 30)).save(buffer, format="PNG")
    return buffer.getvalue()
