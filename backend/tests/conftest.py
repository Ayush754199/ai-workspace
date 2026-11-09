"""Test bootstrap: make the repository root and the backend folder importable.

This replaces the former ``backend/sitecustomize.py`` side effect, which inserted
the repository root into ``sys.path`` for every Python process and caused the
root ``streamlit_app``/``app`` module to shadow the FastAPI ``backend.app``
package.
"""

from __future__ import annotations

import sys
import os
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent

for path in (REPO_ROOT, BACKEND_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

# Production has no JWT fallback. Tests supply an explicit disposable secret
# before importing the FastAPI application.
os.environ.setdefault("JWT_SECRET", "test-only-jwt-secret-0123456789abcdef")


@pytest.fixture(autouse=True, scope="session")
def _ensure_test_schema():
    """Guarantee the SQLite schema exists for the whole session.

    ``app.main`` calls ``ensure_schema()`` at import time, but a module that
    imports a service directly (``app.services...``) never imports ``app.main``,
    so the ``users``/``quota`` tables could be missing and any later endpoint
    test failed with "no such table: users". Creating the schema once, before
    any test runs, makes table creation independent of test order.
    """
    from app.main import ensure_schema  # noqa: F401  (also builds app settings)

    ensure_schema()
    yield


@pytest.fixture(autouse=True)
def _disable_request_rate_limit():
    """The suite registers/logs in many users in one process; keep the limiter out of it."""
    from app.core import rate_limit as rl

    original = rl.auth_limiter.limit
    rl.auth_limiter.limit = 10_000
    rl.auth_limiter._hits.clear()
    yield
    rl.auth_limiter.limit = original
    rl.auth_limiter._hits.clear()


@pytest.fixture
def db_session():
    from app.main import ensure_schema
    from app.core.database import SessionLocal
    ensure_schema()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def reset_ai_quota():
    """Give a test a clean AI quota window.

    The quota ledger is global SQLite state, so a suite that exercises many AI
    calls (or a previous live run) can leave a user at the daily limit and make
    an unrelated test fail with HTTP 429. This fixture clears the counters and
    releases this user's outstanding reservations; it never changes the
    production limits configured in ``app.core.config``.
    """
    from sqlalchemy import text

    from app.main import ensure_schema
    from app.core.database import SessionLocal

    ensure_schema()
    session = SessionLocal()
    created: list[str] = []
    try:
        def _reset(*emails: str) -> None:
            for email in emails:
                row = session.execute(
                    text("select id from users where email = :e"), {"e": email}
                ).fetchone()
                if row is None:
                    continue
                user_id = row[0]
                session.execute(
                    text("update user_quotas set daily_used = 0, reserved = 0 where user_id = :u"),
                    {"u": user_id},
                )
                # ``chat_quotas`` names its counter ``used``; the others use ``daily_used``.
                session.execute(
                    text("update capability_quotas set daily_used = 0, reserved = 0 where user_id = :u"),
                    {"u": user_id},
                )
                session.execute(
                    text("update chat_quotas set used = 0, reserved = 0 where user_id = :u"),
                    {"u": user_id},
                )
                session.execute(
                    text(
                        "update quota_reservations set state = 'RELEASED' "
                        "where user_id = :u and state = 'RESERVED'"
                    ),
                    {"u": user_id},
                )
            session.commit()

        yield _reset
    finally:
        session.rollback()
        session.close()


@pytest.fixture(autouse=True)
def isolate_mongo(monkeypatch):
    """Disable the module-level Mongo client for every test.

    ``mongo`` is an AsyncIOMotorClient bound to the event loop that existed when
    ``app.db.session`` was first imported. Starlette's TestClient spins up a fresh
    loop per test, so any query would fail with "Event loop is closed". Tests are
    meant to exercise the SQLite path unless they install their own fake.
    """
    from app.db import session as mongo_session

    monkeypatch.setattr(mongo_session.mongo, "client", None)
    monkeypatch.setattr(mongo_session.mongo, "database", None)
    monkeypatch.setattr(mongo_session.mongo, "users", None)
    monkeypatch.setattr(mongo_session.mongo, "chat_history", None)
