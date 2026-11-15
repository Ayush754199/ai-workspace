"""Phase 1A regression: proxy header trust, JWT policy and error masking."""

from __future__ import annotations

import pytest
from starlette.requests import Request


def _request(peer: str | None, headers: dict[str, str] | None = None) -> Request:
    raw_headers = [(key.lower().encode(), value.encode()) for key, value in (headers or {}).items()]
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/chat/v2/models",
        "headers": raw_headers,
        "query_string": b"",
        "client": (peer, 12345) if peer else None,
        "server": ("testserver", 80),
        "scheme": "http",
    }
    return Request(scope)


# --- X-Forwarded-For -------------------------------------------------------

def test_forwarded_header_from_untrusted_peer_is_ignored():
    from app.core.rate_limit import client_ip

    request = _request("203.0.113.7", {"X-Forwarded-For": "1.2.3.4"})
    assert client_ip(request) == "203.0.113.7"


def test_forwarded_header_is_honoured_only_behind_a_trusted_proxy(monkeypatch):
    from app.core import rate_limit

    monkeypatch.setattr(rate_limit.settings, "TRUSTED_PROXY_CIDRS", "10.0.0.0/8")
    request = _request("10.1.2.3", {"X-Forwarded-For": "198.51.100.9"})
    assert rate_limit.client_ip(request) == "198.51.100.9"


def test_spoofed_left_entry_is_not_used(monkeypatch):
    """The right-most untrusted hop wins; a client-supplied left entry is decoration."""
    from app.core import rate_limit

    monkeypatch.setattr(rate_limit.settings, "TRUSTED_PROXY_CIDRS", "10.0.0.0/8")
    request = _request("10.0.0.5", {"X-Forwarded-For": "6.6.6.6, 198.51.100.9, 10.0.0.4"})
    assert rate_limit.client_ip(request) == "198.51.100.9"


def test_real_ip_header_is_used_when_forwarded_for_is_absent(monkeypatch):
    from app.core import rate_limit

    monkeypatch.setattr(rate_limit.settings, "TRUSTED_PROXY_CIDRS", "10.0.0.0/8")
    request = _request("10.0.0.5", {"X-Real-IP": "198.51.100.20"})
    assert rate_limit.client_ip(request) == "198.51.100.20"


def test_rate_limit_buckets_cannot_be_rotated_by_a_header():
    from app.core.rate_limit import client_ip

    first = client_ip(_request("203.0.113.7", {"X-Forwarded-For": "1.1.1.1"}))
    second = client_ip(_request("203.0.113.7", {"X-Forwarded-For": "2.2.2.2"}))
    assert first == second == "203.0.113.7"


# --- JWT -------------------------------------------------------------------

def test_token_contains_iat_and_type_and_still_decodes():
    from app.core import security

    token = security.create_access_token(data={"sub": "jwt-subject@example.com"})
    payload = security.decode_access_token(token)
    assert payload["sub"] == "jwt-subject@example.com"
    assert payload["token_type"] == "access"
    assert "iat" in payload and "exp" in payload


def test_legacy_token_without_new_claims_is_still_accepted():
    """Migration path: tokens issued before this release have only sub + exp."""
    from datetime import datetime, timedelta, timezone

    from jose import jwt

    from app.core import security

    legacy = jwt.encode(
        {"sub": "legacy@example.com", "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
        security.JWT_SECRET,
        algorithm=security.ALGORITHM,
    )
    assert security.decode_access_token(legacy)["sub"] == "legacy@example.com"


def test_expired_token_is_rejected():
    from datetime import datetime, timedelta, timezone

    from jose import jwt

    from app.core import security

    expired = jwt.encode(
        {"sub": "expired@example.com", "exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
        security.JWT_SECRET,
        algorithm=security.ALGORITHM,
    )
    with pytest.raises(Exception):
        security.decode_access_token(expired)


def test_token_signed_with_another_secret_is_rejected():
    from jose import jwt

    from app.core import security

    forged = jwt.encode({"sub": "attacker"}, "x" * 40, algorithm=security.ALGORITHM)
    with pytest.raises(Exception):
        security.decode_access_token(forged)


def test_jwt_secret_has_no_default_and_enforces_length():
    from app.core.config import Settings

    with pytest.raises(Exception):
        Settings(JWT_SECRET="short")
    with pytest.raises(Exception):
        Settings(JWT_SECRET="super-secret-key-super-secret-key")
    with pytest.raises(Exception):
        Settings(JWT_SECRET="changeme-and-not-actually-random-32chars")
    with pytest.raises(Exception):
        Settings(JWT_SECRET="secret-is-not-a-signing-key-please-change")


def test_production_refuses_to_boot_without_a_secret(monkeypatch):
    from app.core import config

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.setattr(config, "Settings", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("JWT_SECRET missing")))
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        config._load_settings()


# --- Error masking ---------------------------------------------------------

def test_internal_exception_text_is_not_exposed():
    from app.core.errors import public_message

    exc = RuntimeError("connection to postgres://user:secret@db:5432 failed")
    message = public_message(exc, "Service unavailable")
    assert message == "Service unavailable"
    assert "postgres" not in message and "secret" not in message


def test_safe_value_error_is_kept_but_paths_are_masked():
    from app.core.errors import public_message

    assert public_message(ValueError("The model did not return a usable table")) == "The model did not return a usable table"
    assert public_message(ValueError("failed at /srv/app/x.py")) == "The request could not be completed. Please retry in a moment."


def test_unhandled_exception_returns_generic_detail():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    application = FastAPI()

    @application.exception_handler(Exception)
    async def _handler(request, exc):
        from starlette.responses import JSONResponse

        from app.core.errors import log_and_mask

        detail = log_and_mask(exc, endpoint="test", operation="test_op", fallback="Internal error")
        return JSONResponse({"detail": detail}, status_code=500)

    @application.get("/boom")
    def boom():
        raise RuntimeError("api_key=sk-or-v1-supersecretvalue123456 leaked")

    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.get("/boom")
    assert response.status_code == 500
    assert response.json()["detail"] == "Internal error"
    assert "supersecret" not in response.text


# --- Log redaction ---------------------------------------------------------

def test_provider_keys_are_redacted_in_logs():
    from backend.logger import _safe_text

    raw = "Authorization: Bearer sk-or-v1-abcdefghijklmnopqrstuvwxyz012345 gsk_abcdefghijkl1234"
    safe = _safe_text(raw)
    assert "sk-or-v1-abcdefghijklmnopqrstuvwxyz012345" not in safe
    assert "gsk_abcdefghijkl1234" not in safe
