"""Provider-failure contract tests against a local fake OpenRouter server.

No real provider is called: the fake listens on 127.0.0.1 only, and the tests
assert that provider quota stays at zero while the application quota ledger
still behaves correctly.
"""

from __future__ import annotations

import json
import socket
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
import requests

from app.models.gateway import QuotaReservation, UsageLog, UserQuota
from app.models.user import User
from app.services.ai_gateway import providers
from app.services.ai_gateway.providers import GatewayProviderError, OpenRouterProvider
from app.services.ai_gateway.quota import QuotaError, QuotaService
from backend.key_rotator import ProviderCredential


class _State:
    def __init__(self) -> None:
        self.mode = "ok"
        self.requests = 0
        self.stream_started = False
        self.release = False


class FakeOpenRouter(BaseHTTPRequestHandler):
    # The suite runs many tests in one process; reusing a TIME_WAIT socket makes
    # the next connect() fail and the adapter reports a generic connection error
    # instead of the injected 5xx. Allow the address to be rebound immediately.
    allow_reuse_address = True
    state: _State

    def log_message(self, *_args):  # keep the test output clean
        return

    def do_GET(self):
        if self.path.endswith("/models"):
            body = json.dumps({"data": [{"id": "fake/free", "pricing": {"prompt": "0", "completion": "0"}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        self.state.requests += 1
        mode = self.state.mode
        if mode == "stream":
            self._stream()
            return
        if mode == "timeout":
            # Read the request body before stalling, otherwise the client's write
            # completes instantly and only the response wait is slow.
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            time.sleep(1.5)
            self._json(200, {"choices": [{"message": {"content": "late"}}]})
            return
        if mode == "429":
            # Consume the request before responding, as a real HTTP server does;
            # otherwise the client can reset the socket while still sending its body.
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            self._json(429, {"error": {"message": "rate limit"}})
            return
        if mode == "500":
            self._json(500, {"error": {"message": "boom"}})
            return
        if mode == "malformed":
            body = b"{not-json"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._json(200, {"choices": [{"message": {"content": "fake answer"}}]})

    def _json(self, status: int, payload: dict):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        try:
            for piece in ("Hello", " from", " fake"):
                chunk = {"choices": [{"delta": {"content": piece}}]}
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.flush()
                self.state.stream_started = True
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            # Client hung up mid-stream: the server-side outcome is now unknown.
            self.state.release = True


@pytest.fixture()
def fake_openrouter(monkeypatch):
    state = _State()
    handler = type("BoundHandler", (FakeOpenRouter,), {"state": state})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    # Wait until the socket actually accepts before the test makes a request,
    # otherwise a slow start surfaces as "OpenRouter connection failed".
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", server.server_address[1]), timeout=0.2):
                break
        except OSError:
            time.sleep(0.02)
    monkeypatch.setattr(providers, "log_connection_event", lambda *args, **kwargs: "", raising=False)
    monkeypatch.setattr("backend.key_rotator.log_connection_failure", lambda *args, **kwargs: "")
    monkeypatch.setattr("backend.key_rotator.log_connection_event", lambda *args, **kwargs: "")
    yield state, f"http://127.0.0.1:{server.server_address[1]}/v1"
    server.shutdown()
    server.server_close()


def _provider(base_url, timeout=120):
    return OpenRouterProvider((ProviderCredential("not-a-real-key", "OPENROUTER_FAKE_1"),), "fake/free", base_url=base_url)


def _user(db):
    user = User(identifier=f"fake-{uuid.uuid4().hex}", email=f"fake-{uuid.uuid4().hex}@example.com", hashed_password="hash", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_fake_provider_success_contract(fake_openrouter):
    state, base = fake_openrouter
    assert _provider(base).generate("hello") == "fake answer"
    assert state.requests == 1


def test_rate_limit_429_is_retryable_not_a_crash(fake_openrouter):
    state, base = fake_openrouter
    state.mode = "429"
    with pytest.raises(GatewayProviderError) as raised:
        _provider(base).generate("hello")
    assert raised.value.status_code == 429
    assert raised.value.retryable is True
    assert raised.value.uncertain is False


def test_server_error_5xx_is_reported_without_leaking_body(fake_openrouter):
    state, base = fake_openrouter
    state.mode = "500"
    with pytest.raises(GatewayProviderError) as raised:
        _provider(base).generate("hello")
    assert raised.value.status_code == 500
    assert "boom" not in str(raised.value)


def test_malformed_json_is_reported_as_provider_failure(fake_openrouter):
    state, base = fake_openrouter
    state.mode = "malformed"
    with pytest.raises(GatewayProviderError):
        _provider(base).generate("hello")


def test_timeout_is_marked_uncertain(fake_openrouter):
    state, base = fake_openrouter
    state.mode = "timeout"
    provider = _provider(base)
    provider.timeout = 0.3
    with pytest.raises(GatewayProviderError) as raised:
        provider.generate("hello")
    assert raised.value.uncertain is True
    assert raised.value.status_code is None
    assert state.requests >= 1


def test_stream_disconnect_mid_flight_is_detected_by_server(fake_openrouter):
    state, base = fake_openrouter
    state.mode = "stream"
    stream = _provider(base).stream("hello")
    assert next(stream) == "Hello"
    stream.close()
    deadline = time.time() + 3
    while not state.release and time.time() < deadline:
        time.sleep(0.05)
    assert state.stream_started is True


def test_stream_disconnect_marks_reservation_uncertain_in_ledger(db_session):
    from app.services.ai_gateway.quota import ReservationState

    user = _user(db_session)
    service = QuotaService()
    reservation = service.reserve(db_session, user_id=user.id, request_id=uuid.uuid4().hex, provider="openrouter", model="fake/free")
    service.mark_uncertain(db_session, reservation.reservation_id)
    stored = db_session.query(QuotaReservation).filter_by(reservation_id=reservation.reservation_id).one()
    assert stored.state == ReservationState.UNKNOWN
    assert stored.uncertain is True
    quota = db_session.query(UserQuota).filter_by(user_id=user.id).one()
    # Uncertain usage is never silently refunded.
    assert quota.reserved == 1


def test_parallel_last_quota_only_one_request_wins(db_session):
    user = _user(db_session)
    service = QuotaService()
    service.set_limits(db_session, user_id=user.id, daily_limit=1, per_chat_limit=10)
    winner = service.reserve(db_session, user_id=user.id, request_id=uuid.uuid4().hex, provider="openrouter", model="fake/free")
    assert winner.state == "RESERVED"
    with pytest.raises(QuotaError):
        service.reserve(db_session, user_id=user.id, request_id=uuid.uuid4().hex, provider="openrouter", model="fake/free")
    quota = db_session.query(UserQuota).filter_by(user_id=user.id).one()
    assert quota.reserved == 1
    assert quota.daily_used == 0


def test_double_submit_is_idempotent_by_request_id(db_session):
    from app.services.ai_gateway.quota import DuplicateRequest

    user = _user(db_session)
    request_id = uuid.uuid4().hex
    service = QuotaService()
    first = service.reserve(db_session, user_id=user.id, request_id=request_id, provider="openrouter", model="fake/free")
    service.commit(db_session, first.reservation_id)
    with pytest.raises(DuplicateRequest):
        service.reserve(db_session, user_id=user.id, request_id=request_id, provider="openrouter", model="fake/free")
    assert db_session.query(UsageLog).filter_by(request_id=request_id).count() == 1
    assert db_session.query(UserQuota).filter_by(user_id=user.id).one().daily_used == 1


def test_fallback_order_uses_second_provider_after_retryable_failure(db_session, monkeypatch):
    from app.services.ai_gateway.gateway import AIGateway
    from app.services.ai_gateway.registry import ModelSpec
    from datetime import datetime, timezone

    user = _user(db_session)
    registry = (
        ModelSpec("openrouter", "fake/free", True, True, frozenset({"chat"}), availability="available", verified_at=datetime.now(timezone.utc)),
        ModelSpec("groq", "fake-groq", True, True, frozenset({"chat"}), availability="available", verified_at=datetime.now(timezone.utc)),
    )
    monkeypatch.setattr("app.services.ai_gateway.gateway.settings.AI_GATEWAY_MODE", "live")
    monkeypatch.setattr("app.services.ai_gateway.gateway.settings.OPENROUTER_KEY_1", "not-a-real-key")
    monkeypatch.setattr("app.services.ai_gateway.gateway.settings.FREE_PROVIDERS", ["openrouter", "groq"])
    monkeypatch.setattr("app.services.ai_gateway.gateway.get_provider_credentials", lambda name: (ProviderCredential("not-a-real-key", "FAKE_1"),))

    calls = []

    class First:
        def generate(self, prompt, **_kwargs):
            calls.append("openrouter")
            raise GatewayProviderError("rate limited", status_code=429, retryable=True)

    class Second:
        def generate(self, prompt, **_kwargs):
            calls.append("groq")
            return "second route answer"

    gateway = AIGateway(registry=registry)
    monkeypatch.setattr(gateway, "provider", lambda route: First() if route.provider == "openrouter" else Second())
    reservation = QuotaService().reserve(db_session, user_id=user.id, request_id=uuid.uuid4().hex, provider="openrouter", model="fake/free")
    text, route = gateway.generate_with_reservation(db_session, reservation_id=reservation.reservation_id, prompt="hi")
    assert text == "second route answer"
    assert route.provider == "groq"
    assert calls == ["openrouter", "groq"]
    assert db_session.query(UserQuota).filter_by(user_id=user.id).one().daily_used == 1


def test_all_routes_failing_releases_reservation(db_session, monkeypatch):
    from datetime import datetime, timezone

    from app.services.ai_gateway.gateway import AIGateway, GatewayUnavailableError
    from app.services.ai_gateway.registry import ModelSpec

    user = _user(db_session)
    registry = (
        ModelSpec("openrouter", "fake/free", True, True, frozenset({"chat"}), availability="available", verified_at=datetime.now(timezone.utc)),
    )
    monkeypatch.setattr("app.services.ai_gateway.gateway.settings.AI_GATEWAY_MODE", "live")
    monkeypatch.setattr("app.services.ai_gateway.gateway.settings.OPENROUTER_KEY_1", "not-a-real-key")
    monkeypatch.setattr("app.services.ai_gateway.gateway.get_provider_credentials", lambda name: (ProviderCredential("not-a-real-key", "FAKE_1"),))

    class Broken:
        def generate(self, prompt, **_kwargs):
            raise GatewayProviderError("overloaded", status_code=503, retryable=True)

    gateway = AIGateway(registry=registry)
    monkeypatch.setattr(gateway, "provider", lambda route: Broken())
    reservation = QuotaService().reserve(db_session, user_id=user.id, request_id=uuid.uuid4().hex, provider="openrouter", model="fake/free")
    with pytest.raises(GatewayUnavailableError):
        gateway.generate_with_reservation(db_session, reservation_id=reservation.reservation_id, prompt="hi")
    quota = db_session.query(UserQuota).filter_by(user_id=user.id).one()
    assert quota.reserved == 0
    assert quota.daily_used == 0


def test_fake_server_is_local_only(fake_openrouter):
    _state, base = fake_openrouter
    assert requests.Request("GET", base).url.startswith("http://127.0.0.1:")
