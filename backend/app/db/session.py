from __future__ import annotations

import asyncio
import time

from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection

from app.core.config import settings

try:
    from backend.logger import log_connection_failure
except ModuleNotFoundError:
    from logger import log_connection_failure


class MongoCollections:
    def __init__(self) -> None:
        self._ready_at: float = 0.0
        # DB-up marker: cached briefly so per-request pings stay cheap, and a
        # short outage re-opens on the next request.
        self.PING_TIMEOUT_SECONDS = 4.0
        self.READY_TTL_SECONDS = 4.0
        self._ping_lock: asyncio.Lock | None = None
        if not settings.MONGODB_URL:
            self.client = None
            self.database = None
            self.users = None
            self.chat_history = None
            self.calls = None
            self.call_events = None
            return
        try:
            self.client = AsyncIOMotorClient(settings.MONGODB_URL, serverSelectionTimeoutMS=5000)
            self.database = self.client[settings.MONGODB_DATABASE]
            self.users: AsyncIOMotorCollection = self.database["users"]
            self.chat_history: AsyncIOMotorCollection = self.database["chat_history"]
            self.calls: AsyncIOMotorCollection = self.database["calls"]
            self.call_events: AsyncIOMotorCollection = self.database["call_events"]
        except Exception as exc:
            log_connection_failure(
                exc,
                endpoint="mongodb://Atlas",
                payload={"database": settings.MONGODB_DATABASE, "collections": ["users", "chat_history"]},
            )
            raise

    @property
    def enabled(self) -> bool:
        return self.client is not None

    async def verify_ready(self) -> None:
        """Refuse to serve identity writes when the database is unreachable.

        The positive result is cached for a few seconds so every login/register
        does not pay a round-trip, and a transient blip within the cache window
        does not 503 an unrelated request. A negative result is never cached:
        the next request retries immediately.
        """
        if self.client is None:
            return
        now = time.monotonic()
        if self._ready_at and now - self._ready_at < self.READY_TTL_SECONDS:
            return
        if self._ping_lock is None:
            self._ping_lock = asyncio.Lock()
        async with self._ping_lock:
            now = time.monotonic()
            if self._ready_at and now - self._ready_at < self.READY_TTL_SECONDS:
                return
            try:
                await asyncio.wait_for(self.client.admin.command("ping"), timeout=self.PING_TIMEOUT_SECONDS)
            except Exception as exc:  # noqa: BLE001 - fail fast without leaking connection data
                log_connection_failure(
                    exc,
                    endpoint="mongodb://configured-database",
                    payload={"database": settings.MONGODB_DATABASE, "operation": "ping"},
                )
                self._ready_at = 0.0
                raise HTTPException(
                    status_code=503,
                    detail="Database temporarily unavailable. Please try again in a few seconds.",
                    headers={"Retry-After": "3"},
                ) from exc
            self._ready_at = now

    def reset_ready_cache(self) -> None:
        self._ready_at = 0.0

    async def close(self) -> None:
        if self.client is not None:
            try:
                close_client = getattr(self.client, "close", None)
                if callable(close_client):
                    close_client()
                self._ready_at = 0.0
            except Exception as exc:
                log_connection_failure(
                    exc,
                    endpoint="mongodb://Atlas",
                    payload={"database": settings.MONGODB_DATABASE, "operation": "close"},
                )
                raise


mongo = MongoCollections()
