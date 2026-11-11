"""LiveKit call persistence, authorization and short-lived token creation.

This module deliberately keeps provider credentials and room grants out of the
HTTP layer.  MongoDB is the source of truth for invitations and call history;
the in-process hub is only the low-latency notification path.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException

from app.core.config import settings
from app.db.session import mongo
from app.models.user import User


CALL_STATES = {"ringing", "accepted", "active", "rejected", "cancelled", "ended", "missed"}
TERMINAL_STATES = {"rejected", "cancelled", "ended", "missed"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else value


def public_call(document: dict[str, Any]) -> dict[str, Any]:
    result = dict(document)
    result.pop("_id", None)
    for key in ("created_at", "updated_at", "started_at", "ended_at"):
        result[key] = _iso(result.get(key))
    return result


class CallHub:
    """Best-effort in-process signalling hub, backed by Mongo persistence."""

    def __init__(self) -> None:
        self._connections: dict[int, set[Any]] = {}

    def add(self, user_id: int, websocket: Any) -> None:
        self._connections.setdefault(user_id, set()).add(websocket)

    def remove(self, user_id: int, websocket: Any) -> None:
        sockets = self._connections.get(user_id)
        if not sockets:
            return
        sockets.discard(websocket)
        if not sockets:
            self._connections.pop(user_id, None)

    async def send(self, user_ids: list[int], event: dict[str, Any]) -> None:
        for user_id in set(user_ids):
            for websocket in list(self._connections.get(user_id, ())):
                try:
                    await websocket.send_json(event)
                except Exception:
                    self.remove(user_id, websocket)


call_hub = CallHub()


async def ensure_call_indexes() -> None:
    if not mongo.enabled:
        return
    await mongo.calls.create_index("call_id", unique=True)
    await mongo.calls.create_index([("participant_ids", 1), ("updated_at", -1)])
    await mongo.calls.create_index([("status", 1), ("created_at", 1)])
    await mongo.call_events.create_index([("call_id", 1), ("created_at", 1)])
    await mongo.call_events.create_index("event_id", unique=True)
    await mongo.call_messages.create_index([("call_id", 1), ("created_at", 1)])
    await mongo.call_messages.create_index("message_id", unique=True)


def require_livekit() -> None:
    missing = [name for name, value in (
        ("LIVEKIT_URL", settings.LIVEKIT_URL),
        ("LIVEKIT_API_KEY", settings.LIVEKIT_API_KEY),
        ("LIVEKIT_API_SECRET", settings.LIVEKIT_API_SECRET),
    ) if not value]
    if missing:
        raise HTTPException(status_code=503, detail=f"LiveKit calling is not configured on this server. Missing server variables: {', '.join(missing)}")


async def create_join_token(call: dict[str, Any], user: User) -> str:
    require_livekit()
    try:
        from livekit import api
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="LiveKit server SDK is not installed") from exc
    grants = api.VideoGrants(
        room_join=True,
        room=call["room_name"],
        can_publish=True,
        can_subscribe=True,
        can_publish_data=True,
    )
    token = api.AccessToken(
        settings.LIVEKIT_API_KEY,
        settings.LIVEKIT_API_SECRET,
    ).with_ttl(timedelta(seconds=settings.LIVEKIT_TOKEN_TTL_SECONDS)).with_identity(str(user.id)).with_name(
        user.full_name or user.email or str(user.id)
    ).with_grants(grants)
    return token.to_jwt()


async def get_call(call_id: str) -> dict[str, Any] | None:
    if not mongo.enabled:
        raise HTTPException(status_code=503, detail="Call persistence is not configured")
    await mongo.verify_ready()
    return await mongo.calls.find_one({"call_id": call_id})


async def expire_stale_calls(user_id: int) -> None:
    """Mark unanswered invitations missed when a client next checks history."""
    if not mongo.enabled:
        return
    cutoff = utcnow() - timedelta(seconds=settings.LIVEKIT_CALL_RING_TIMEOUT_SECONDS)
    await mongo.calls.update_many(
        {"participant_ids": user_id, "status": "ringing", "created_at": {"$lt": cutoff}},
        {"$set": {"status": "missed", "missed": True, "ended_at": utcnow(), "updated_at": utcnow(), "terminal_reason": "timeout"}},
    )


async def cleanup_stale_calls() -> None:
    """Close abandoned call records so availability reflects real calls.

    A browser crash can prevent the normal ``/end`` request and a missed
    LiveKit webhook.  Only records with old activity are cleaned up; recent
    calls remain busy.  This is intentionally bounded and never removes
    history, allowing admins to audit the original call lifecycle.
    """
    if not mongo.enabled:
        return
    now = utcnow()
    ring_cutoff = now - timedelta(seconds=settings.LIVEKIT_CALL_RING_TIMEOUT_SECONDS)
    stale_cutoff = now - timedelta(seconds=settings.LIVEKIT_STALE_CALL_TIMEOUT_SECONDS)
    await mongo.calls.update_many(
        {"status": "ringing", "created_at": {"$lt": ring_cutoff}},
        {"$set": {
            "status": "missed", "missed": True, "ended_at": now,
            "updated_at": now, "terminal_reason": "timeout",
        }},
    )
    await mongo.calls.update_many(
        {"status": {"$in": ["accepted", "active"]}, "updated_at": {"$lt": stale_cutoff}},
        {"$set": {
            "status": "ended", "ended_at": now, "updated_at": now,
            "terminal_reason": "stale_cleanup",
        }},
    )


async def purge_call_data() -> dict[str, int]:
    """Delete only calling records, never users, chats, or auth data."""
    if not mongo.enabled:
        return {"calls": 0, "call_events": 0, "call_messages": 0}
    deleted: dict[str, int] = {}
    for name in ("calls", "call_events", "call_messages"):
        result = await getattr(mongo, name).delete_many({})
        deleted[name] = int(result.deleted_count)
    return deleted


async def write_event(call_id: str, event: str, actor_id: int, payload: dict[str, Any] | None = None) -> None:
    now = utcnow()
    await mongo.call_events.insert_one({
        "event_id": secrets.token_urlsafe(18), "call_id": call_id, "event": event,
        "actor_id": actor_id, "payload": payload or {}, "created_at": now,
    })


async def transition(call: dict[str, Any], status: str, actor_id: int, *, reason: str | None = None) -> dict[str, Any]:
    if status not in CALL_STATES:
        raise HTTPException(status_code=400, detail="Unsupported call state")
    current = call.get("status")
    allowed = {
        "ringing": {"accepted", "active", "rejected", "cancelled", "missed"},
        "accepted": {"active", "ended"}, "active": {"ended"},
    }
    if current in TERMINAL_STATES:
        if current == status:
            return call
        raise HTTPException(status_code=409, detail="Call has already ended")
    if status not in allowed.get(current, set()):
        raise HTTPException(status_code=409, detail=f"Cannot change call from {current} to {status}")
    now = utcnow()
    update: dict[str, Any] = {"status": status, "updated_at": now}
    if status == "accepted": update["accepted_at"] = now
    if status in {"active"} and not call.get("started_at"): update["started_at"] = now
    if status in TERMINAL_STATES:
        update["ended_at"] = now
        started = call.get("started_at") or call.get("accepted_at")
        if isinstance(started, datetime):
            # Mongo deployments may deserialize legacy UTC timestamps as
            # naive datetimes. Normalize them before subtracting from the
            # timezone-aware value returned by utcnow().
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            update["duration_seconds"] = max(0, int((now - started).total_seconds()))
        update["terminal_reason"] = reason or status
    await mongo.calls.update_one({"call_id": call["call_id"], "status": current}, {"$set": update})
    await write_event(call["call_id"], status, actor_id, {"reason": reason} if reason else {})
    call.update(update)
    return call
