"""An unreachable database must answer 503 quickly instead of hanging."""

from __future__ import annotations

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from app.db import session as mongo_session
from app.main import app


class _HangingAdmin:
    async def command(self, *_args, **_kwargs):
        await asyncio.sleep(30)


class _HangingClient:
    admin = _HangingAdmin()


class _HangingUsers:
    async def find_one(self, *_args, **_kwargs):
        await asyncio.sleep(30)


@pytest.fixture()
def unreachable_mongo(monkeypatch):
    from app.api.v1.endpoints import auth as auth_endpoints

    monkeypatch.setattr(mongo_session, "log_connection_failure", lambda *args, **kwargs: "")
    monkeypatch.setattr(mongo_session.mongo, "client", _HangingClient())
    monkeypatch.setattr(mongo_session.mongo, "users", _HangingUsers())
    monkeypatch.setattr(auth_endpoints, "mongo", mongo_session.mongo)
    yield


def test_mongo_ping_fails_fast_with_503(unreachable_mongo):
    payload = {
        "email": "dbfail@example.com",
        "mobile_number": "+919800000001",
        "password": "StrongPass-2026!",
        "full_name": "DB Fail",
    }
    started = time.monotonic()
    with TestClient(app) as client:
        response = client.post("/auth/register", json=payload)
    elapsed = time.monotonic() - started
    assert response.status_code == 503
    assert "Database temporarily unavailable" in response.json()["detail"]
    assert response.headers.get("retry-after") in {"2", "3", "4"}
    assert 2 <= elapsed <= 8, f"register took {elapsed:.1f}s; must fail fast inside the ping window"
    # No connection string or credentials may be echoed.
    assert "mongodb" not in response.text.lower()


def test_positive_ping_result_is_cached_briefly():
    """A successful ping suppresses repeated pings for the TTL window."""
    import asyncio

    from app.db import session as mongo_session

    calls = {"count": 0}

    class _OkAdmin:
        async def command(self, *_args, **_kwargs):
            calls["count"] += 1
            return {"ok": 1}

    class _OkClient:
        admin = _OkAdmin()

    original_client = mongo_session.mongo.client
    mongo_session.mongo.client = _OkClient()
    mongo_session.mongo.reset_ready_cache()
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(mongo_session.mongo.verify_ready())
        first = calls["count"]
        loop.run_until_complete(mongo_session.mongo.verify_ready())
        assert calls["count"] == first  # served from the cache, no second ping
    finally:
        loop.close()
        mongo_session.mongo.client = original_client
        mongo_session.mongo.reset_ready_cache()


def test_recovery_after_outage_reopens_immediately():
    """A negative result is never cached: the next request retries the ping."""
    import asyncio

    from app.db import session as mongo_session

    state = {"up": False}

    class _ToggleAdmin:
        async def command(self, *_args, **_kwargs):
            if not state["up"]:
                raise RuntimeError("still down")
            return {"ok": 1}

    class _ToggleClient:
        admin = _ToggleAdmin()

    original = mongo_session.mongo.client
    mongo_session.mongo.client = _ToggleClient()
    mongo_session.mongo.reset_ready_cache()
    loop = asyncio.new_event_loop()
    try:
        with pytest.raises(Exception):
            loop.run_until_complete(mongo_session.mongo.verify_ready())
        state["up"] = True
        # The failed ping must not have been cached as "ready".
        loop.run_until_complete(mongo_session.mongo.verify_ready())
    finally:
        loop.close()
        mongo_session.mongo.client = original
        mongo_session.mongo.reset_ready_cache()


def test_login_reports_503_when_database_is_down(unreachable_mongo):
    with TestClient(app) as client:
        response = client.post("/auth/login", json={"identifier": "dbfail@example.com", "password": "whatever"})
    assert response.status_code == 503



def test_concurrent_readiness_checks_share_one_ping(monkeypatch):
    """Requests arriving together should not fan out identical Atlas pings."""
    from app.db import session as mongo_session

    calls = {"count": 0}

    class _SlowAdmin:
        async def command(self, *_args, **_kwargs):
            calls["count"] += 1
            await asyncio.sleep(0.02)
            return {"ok": 1}

    class _SlowClient:
        admin = _SlowAdmin()

    original = mongo_session.mongo.client
    mongo_session.mongo.client = _SlowClient()
    mongo_session.mongo.reset_ready_cache()
    async def concurrent_checks():
        await asyncio.gather(*(mongo_session.mongo.verify_ready() for _ in range(8)))

    try:
        asyncio.run(concurrent_checks())
        assert calls["count"] == 1
    finally:
        mongo_session.mongo.client = original
        mongo_session.mongo.reset_ready_cache()


def test_application_shutdown_closes_mongo_client(monkeypatch):
    """FastAPI lifespan owns the module-level Motor client's lifetime."""
    from fastapi.testclient import TestClient
    from app.db import session as mongo_session
    from app.main import app

    calls = {"count": 0}

    class _ClosableClient:
        def close(self):
            calls["count"] += 1

    monkeypatch.setattr(mongo_session.mongo, "client", _ClosableClient())
    with TestClient(app):
        pass
    assert calls["count"] == 1
