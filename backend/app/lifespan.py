"""Single owner of application startup and shutdown work.

Before this module the work was split across three places with different
lifecycles:

* ``app.main`` ran ``ensure_schema()`` at import time,
* a router-level ``@router.on_event("startup")`` in ``endpoints/calls.py`` created
  Mongo indexes (and optionally purged call data), and
* ``@app.on_event("startup")`` on the application re-ran the schema check and the
  media-job recovery.

The router-level hook was the source of the ``RuntimeError: Event loop is closed``
that broke every second ``TestClient``: Starlette gives each client a fresh event
loop, and a Motor client created at import time is bound to the loop that existed
then. It also meant a Mongo outage aborted startup.

Everything now runs inside the ASGI lifespan, in a fixed order, and every step that
touches an optional dependency (MongoDB, media providers) is isolated: failure is
logged and the application still serves requests. SQL remains the authoritative
identity store; nothing here drops or rewrites user data.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import FastAPI

logger = logging.getLogger("app.lifespan")

# Long-running background tasks owned by the lifespan (cancelled on shutdown).
_tasks: dict[str, asyncio.Task] = {}

# Upper bound on the startup MongoDB probe. Requests still fail fast with a 503 and
# a Retry-After header when the cluster is down (app.db.session.verify_ready).
startup_timeout = 2.0


def init_database() -> None:
    """Create missing tables and columns (idempotent, never destructive)."""
    from app.main import ensure_schema

    ensure_schema()


def provision_admin() -> None:
    """Apply the explicit ADMIN_BOOTSTRAP_IDENTIFIER promotion, if configured."""
    from app.core.admin_provisioning import configured_admin_identifier, provision_configured_admin
    from app.core.database import SessionLocal

    if not configured_admin_identifier():
        return
    session = SessionLocal()
    try:
        provision_configured_admin(session)
    except Exception:  # noqa: BLE001 - never block startup on the bootstrap
        session.rollback()
        logger.exception("administrator bootstrap failed")
    finally:
        session.close()


def recover_media_jobs() -> None:
    """Fail and refund media jobs whose worker stopped reporting."""
    from app.api.v1.endpoints.video import recover_interrupted_jobs

    try:
        recover_interrupted_jobs()
    except Exception:  # noqa: BLE001 - legacy rows must not block startup
        logger.exception("media job recovery failed")


def seed_knowledge_base() -> None:
    """Import the gzipped knowledge seed when the chunk table is empty.

    The study PDFs are not committed (405 MB), so the extracted text travels as a
    compressed JSONL seed under backend/app/data/. On a fresh deploy (Render,
    new DB) this populates the knowledge base exactly once; afterwards the DB is
    the source of truth and this is a no-op.
    """
    import gzip
    import json
    from pathlib import Path

    from app.core.database import SessionLocal, engine, Base
    from app.models.library import KnowledgeChunk
    import app.models.library  # noqa: F401

    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        if session.query(KnowledgeChunk.id).first() is not None:
            return  # already populated
        seed = Path(__file__).resolve().parents[1] / "data" / "knowledge_seed.jsonl.gz"
        if not seed.is_file():
            logger.info("knowledge seed file not found; skipping import")
            return
        count = 0
        with gzip.open(seed, "rt", encoding="utf-8") as stream:
            for line in stream:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                session.add(
                    KnowledgeChunk(
                        document_title=str(row.get("title") or "Untitled")[:300],
                        subject=row.get("subject"),
                        source_file=row.get("source"),
                        chunk_index=int(row.get("index") or 0),
                        content=row.get("content") or "",
                        char_count=len(row.get("content") or ""),
                    )
                )
                count += 1
                if count % 1000 == 0:
                    session.commit()
        session.commit()
        logger.info("knowledge base seeded: %d chunks imported", count)
    except Exception:  # noqa: BLE001 - optional knowledge data must not block startup
        session.rollback()
        logger.exception("knowledge base seed failed")
    finally:
        session.close()


async def init_mongo() -> None:
    """Create Mongo indexes when a MongoDB URL is configured.

    MongoDB is an optional mirror of legacy profile/call data. An unreachable
    cluster (Atlas IP allow-list, outage, missing DNS) is logged and skipped so the
    SQL-backed API still starts.

    The startup probe waits at most ``startup_timeout`` seconds. Verification used
    to happen only here with the full per-request timeout, so a dead cluster added
    that delay to *every* boot and hid the request-level 503 behind a startup hang.
    """
    from app.db.session import mongo
    from app.services.call_service import ensure_call_indexes

    if not mongo.enabled:
        logger.info("MongoDB is not configured; starting with SQL storage only")
        return
    try:
        await asyncio.wait_for(mongo.verify_ready(), timeout=startup_timeout)
        await ensure_call_indexes()
    except Exception:  # noqa: BLE001 - optional dependency
        logger.warning("MongoDB is unreachable at startup; continuing in SQL-only mode", exc_info=True)
        mongo.reset_ready_cache()


async def reset_call_data_if_requested() -> None:
    """Emergency one-time call-data purge (CALL_DATA_RESET_ON_STARTUP)."""
    from app.core.config import settings
    from app.db.session import mongo

    if not settings.CALL_DATA_RESET_ON_STARTUP or not mongo.enabled:
        return
    from app.services.call_service import purge_call_data

    try:
        deleted = await purge_call_data()
        logger.warning("Call data reset completed: %s", deleted)
    except Exception:  # noqa: BLE001 - optional maintenance action
        logger.exception("call data reset failed")


async def close_mongo() -> None:
    from app.db.session import mongo

    try:
        await mongo.close()
    except Exception:  # noqa: BLE001 - shutdown must not raise
        logger.exception("MongoDB client close failed")


async def start_call_sweeper() -> None:
    """Close abandoned calls on a timer instead of only on traffic.

    The task is cancelled on shutdown; a missing/short Mongo configuration simply
    makes each tick a no-op.
    """
    from app.db.session import mongo

    if not mongo.enabled:
        return
    from app.services.call_service import sweep_stale_calls_forever

    _tasks["call_sweeper"] = asyncio.create_task(sweep_stale_calls_forever(), name="call-sweeper")


async def stop_call_sweeper() -> None:
    task = _tasks.pop("call_sweeper", None)
    if task is None:
        return
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001 - shutdown must not raise
        pass


async def on_startup() -> None:
    init_database()
    provision_admin()
    recover_media_jobs()
    seed_knowledge_base()
    await init_mongo()
    await reset_call_data_if_requested()
    await start_call_sweeper()


async def on_shutdown() -> None:
    await stop_call_sweeper()
    await close_mongo()


def register(app: FastAPI) -> None:
    """Attach the handlers to an application that was created without a lifespan."""

    @app.on_event("startup")
    async def _startup() -> None:  # pragma: no cover - exercised through the app
        await on_startup()

    @app.on_event("shutdown")
    async def _shutdown() -> None:  # pragma: no cover - exercised through the app
        await on_shutdown()
