"""Test bootstrap: make the repository root and the backend folder importable.

This replaces the former ``backend/sitecustomize.py`` side effect, which inserted
the repository root into ``sys.path`` for every Python process and caused the
root ``streamlit_app``/``app`` module to shadow the FastAPI ``backend.app``
package.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent

for path in (REPO_ROOT, BACKEND_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

# ---------------------------------------------------------------------------
# Hermetic environment (set BEFORE the application imports its settings)
# ---------------------------------------------------------------------------
# Production has no JWT fallback. Tests supply an explicit disposable secret
# before importing the FastAPI application.
os.environ.setdefault("JWT_SECRET", "test-only-jwt-secret-0123456789abcdef")

# Unit tests must never contact a real provider or a real database. Anything that
# needs a live service installs its own fake; see ``test_outbound_network_guard``.
for _variable in (
    "MONGODB_URL",
    "GEMINI_API_KEY",
    "GEMINI_API_KEYS",
    "OPENROUTER_KEY_1",
    "OPENROUTER_KEY_2",
    "GROQ_API_KEY_1",
    "GROQ_API_KEYS",
    "HF_TOKEN",
    "HF_API_TOKEN",
    "HF_TOKENS",
    "TAVILY_API_KEY_1",
    "TAVILY_API_KEY_2",
    "LIVEKIT_URL",
    "LIVEKIT_API_KEY",
    "LIVEKIT_API_SECRET",
    "CLOUDFLARE_ACCOUNT_ID",
    "CLOUDFLARE_API_TOKEN",
    "FAL_KEY",
    "LTX_API_KEY",
    "POLLINATIONS_API_KEY",
    "ADMIN_BOOTSTRAP_IDENTIFIER",
):
    os.environ.pop(_variable, None)

# The suite runs against an isolated SQLite file so a local/production
# ``backend_app.db`` is never mutated by a test run. The file is created fresh for
# every session (and removed afterwards) so rows left by an earlier -- or a
# partial/failed -- run cannot break an unrelated test (e.g. a leftover
# ``providers.name`` row violating a UNIQUE constraint, or a fixed test email
# already registered).
_SESSION_DB = Path(tempfile.gettempdir()) / f"ai_workspace_phase1_tests_{os.getpid()}.db"
_SESSION_DB.unlink(missing_ok=True)
os.environ["DATABASE_URL"] = f"sqlite:///{_SESSION_DB.as_posix()}"


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001 - pytest hook signature
    """Remove the throw-away database file after the run.

    Best effort: on Windows the SQLAlchemy engine may still hold the file handle,
    and a cleanup failure must never turn a green run into an error.
    """
    try:
        _SESSION_DB.unlink(missing_ok=True)
    except OSError:
        pass

# Attachment bytes go to a throw-away directory, never the repository.
os.environ.setdefault("ATTACHMENT_STORAGE_DIR", tempfile.mkdtemp(prefix="attach-tests-"))


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

    The quota ledger is persistent SQLite state, so a suite that exercises many AI
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

    ``mongo`` would be an AsyncIOMotorClient bound to the event loop that existed
    when ``app.db.session`` was first imported. Starlette's TestClient spins up a
    fresh loop per test, so any real query would fail with "Event loop is closed".
    Tests exercise the SQLite path unless they install their own fake.
    """
    from app.db import session as mongo_session

    monkeypatch.setattr(mongo_session.mongo, "client", None)
    monkeypatch.setattr(mongo_session.mongo, "database", None)
    monkeypatch.setattr(mongo_session.mongo, "users", None)
    monkeypatch.setattr(mongo_session.mongo, "chat_history", None)
    mongo_session.mongo.reset_ready_cache()


@pytest.fixture(autouse=True)
def test_outbound_network_guard(request, monkeypatch):
    """Fail a unit test that tries to reach a real external service.

    Tests that genuinely exercise a live dependency opt out with
    ``@pytest.mark.allow_network``. Blocking here keeps the suite from spending
    provider quota or depending on the machine's internet access, while still
    allowing every test that installs its own fake client.
    """
    if request.node.get_closest_marker("allow_network"):
        yield
        return

    class _Blocked(RuntimeError):
        pass

    from urllib.parse import urlsplit

    def _is_loopback(value) -> bool:
        """A local fake server (127.0.0.1) is not an external service.

        ``test_provider_contract_mock`` starts a ThreadingHTTPServer on
        ``127.0.0.1`` to test provider failures, so blocking loopback made those
        tests fail with the guard's own exception instead of the injected
        provider error.
        """
        if isinstance(value, requests.Request):
            value = value.url
        if isinstance(value, bytes):
            value = value.decode("utf-8", "replace")
        if not isinstance(value, str):
            return False
        host = ""
        try:
            parsed = urlsplit(value)
        except Exception:  # noqa: BLE001 - not a URL: treat as external
            return False
        host = parsed.hostname or ""
        if not host:
            # requests.request(method, url) passes the URL positionally first.
            return False
        host = host.lower()
        return host in {"127.0.0.1", "localhost", "::1", "[::1]"}

    def _make_guard(method: str | None):
        def _guarded(*args, **kwargs):
            url = kwargs.get("url")
            if url is None:
                for candidate in args:
                    if isinstance(candidate, (str, bytes, requests.Request)):
                        url = candidate
                        break
            if url is not None and _is_loopback(url):
                # requests.request(method, url) takes the verb positionally;
                # requests.get/post(url) do not, hence the explicit method here.
                return _real_request(method, *args, **kwargs)
            raise _Blocked(
                "Outbound network access during a unit test. Mock the client, or mark "
                "the test with @pytest.mark.allow_network if it is a true integration test."
            )
        return _guarded

    def _blocked_urlopen(url, *args, **kwargs):
        target = url if isinstance(url, (str, bytes)) else getattr(url, "full_url", url)
        if _is_loopback(target):
            return _real_urlopen(url, *args, **kwargs)
        raise _Blocked(
            "Outbound network access during a unit test. Mock the client, or mark "
            "the test with @pytest.mark.allow_network if it is a true integration test."
        )

    import requests
    import urllib.request as urlrequest

    _real_request = requests.request
    _real_urlopen = urlrequest.urlopen

    monkeypatch.setattr(requests, "request", _make_guard(None))
    monkeypatch.setattr(requests, "get", _make_guard("GET"))
    monkeypatch.setattr(requests, "post", _make_guard("POST"))
    monkeypatch.setattr(urlrequest, "urlopen", _blocked_urlopen)
    yield


def pytest_configure(config):
    config.addinivalue_line("markers", "allow_network: test may contact a real external service")
    config.addinivalue_line("markers", "integration: test needs a live service (MongoDB, provider API)")
