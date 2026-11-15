"""Phase 1A: internal exception text never reaches an API response."""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from app.core.errors import DEFAULT_MESSAGE, log_and_mask, public_message
from app.main import app

SECRETISH = "postgresql://admin:supersecret@db.internal:5432/app"


def test_public_message_hides_unknown_exception_text():
    assert public_message(RuntimeError(SECRETISH)) == DEFAULT_MESSAGE


def test_public_message_hides_paths_and_urls_even_for_safe_types():
    assert public_message(ValueError(f"failed at {SECRETISH}")) == DEFAULT_MESSAGE
    assert public_message(ValueError("/var/data/secret.txt missing")) == DEFAULT_MESSAGE


def test_public_message_keeps_short_authored_messages():
    assert public_message(ValueError("Quota limits must be positive")) == "Quota limits must be positive"


def test_log_and_mask_logs_but_returns_generic_text(monkeypatch):
    recorded: dict = {}
    import app.core.errors as errors

    def fake_log(error, *, endpoint, payload, function=None):
        recorded["error"] = str(error)
        recorded["endpoint"] = endpoint
        return "{}"

    monkeypatch.setattr(errors, "log_connection_failure", fake_log)
    message = log_and_mask(RuntimeError(SECRETISH), endpoint="/api/v1/documents/extract", operation="extract")
    assert message == DEFAULT_MESSAGE
    assert recorded["error"] == SECRETISH  # diagnostic kept server-side
    assert SECRETISH not in message


def test_unhandled_exception_returns_generic_500():
    """The installed handler must never render the exception message."""
    import asyncio

    from starlette.requests import Request

    handler = app.exception_handlers[Exception]
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/boom",
        "headers": [(b"x-request-id", b"req-123")],
        "query_string": b"",
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 1234),
        "scheme": "http",
        "root_path": "",
    }
    response = asyncio.run(handler(Request(scope), RuntimeError(SECRETISH)))
    body = response.body.decode()
    assert response.status_code == 500
    assert DEFAULT_MESSAGE in body
    assert SECRETISH not in body
    assert response.headers.get("X-Request-Id") == "req-123"


def test_documents_extract_does_not_echo_parser_errors(monkeypatch):
    from app.api.v1.endpoints import documents as documents_module

    def explode(*_args, **_kwargs):
        raise RuntimeError(f"pdf parser crashed reading {SECRETISH}")

    # The real log_and_mask runs: it must record the diagnostic server-side and
    # return a generic message to the client.
    monkeypatch.setattr(documents_module.docs, "extract_any", explode)

    from app.api.deps import get_current_user
    from app.models.user import User

    app.dependency_overrides[documents_module.require_feature("documents")] = lambda: None
    app.dependency_overrides[get_current_user] = lambda: User(
        id=1, identifier="t@example.com", email="t@example.com", hashed_password="x", is_active=True
    )
    try:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            "/api/v1/documents/extract",
            files={"file": ("a.txt", io.BytesIO(b"hello"), "text/plain")},
        )
        assert response.status_code == 400
        assert SECRETISH not in response.text
    finally:
        app.dependency_overrides.clear()
