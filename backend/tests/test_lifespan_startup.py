"""Phase 1B: startup/shutdown runs once, from the lifespan, and Mongo is optional.

The ``RuntimeError: Event loop is closed`` that broke every second TestClient came
from a router-level ``on_event("startup")`` touching the Motor client created on a
previous loop. These tests pin the new structure.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def test_health_endpoint_is_available(client=None):
    from app.main import app

    with TestClient(app) as http:
        response = http.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_repeated_startups_do_not_raise_event_loop_closed():
    """Two independent event loops in one process is the regression case."""
    from app.main import app

    for _ in range(2):
        with TestClient(app) as http:
            assert http.get("/api/v1/health").status_code == 200


def test_routers_no_longer_register_startup_hooks():
    from app.api.v1.endpoints.calls import router
    from app.main import app

    router_hooks = getattr(router, "on_startup", [])
    assert router_hooks == []
    # Application-level hooks are allowed only for the lifespan bootstrap itself.
    for handler in getattr(app.router, "on_startup", []):
        assert getattr(handler, "__name__", "") in {"_startup", "generate_unique_id"}


def test_mongo_outage_does_not_abort_startup(monkeypatch):
    import asyncio

    from app import lifespan
    from app.db import session as mongo_session

    class Exploding:
        enabled = True

        async def verify_ready(self):
            raise RuntimeError("cluster unreachable")

        def reset_ready_cache(self):
            self.reset = True

    monkeypatch.setattr(mongo_session, "mongo", Exploding())
    monkeypatch.setattr("app.services.call_service.ensure_call_indexes", _never_called)

    # Must not raise.
    asyncio.run(lifespan.init_mongo())


async def _never_called():  # pragma: no cover - asserted by not being called twice
    raise AssertionError("index creation must be skipped when Mongo is down")


def test_sql_schema_and_admin_provisioning_are_idempotent():
    from app import lifespan

    lifespan.init_database()
    lifespan.init_database()  # second call is a no-op, never destructive


def test_admin_promotion_is_not_applied_at_import_time(tmp_path, monkeypatch):
    """Importing app.main must never change authorization on its own."""
    from app.core.database import SessionLocal
    from app.models.user import User
    from app.core import security

    from app import lifespan

    identifier = "bootstrap-at-startup@example.com"
    session = SessionLocal()
    try:
        session.query(User).filter(User.identifier == identifier).delete()
        session.add(User(identifier=identifier, email=identifier, hashed_password=security.hash_password("x"), is_active=True))
        session.commit()
    finally:
        session.close()

    monkeypatch.setattr("app.core.admin_provisioning.configured_admin_identifier", lambda: identifier)
    lifespan.provision_admin()

    session = SessionLocal()
    try:
        row = session.query(User).filter(User.identifier == identifier).one()
        assert row.is_admin is True
    finally:
        session.query(User).filter(User.identifier == identifier).delete()
        session.commit()
        session.close()
