from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core import security
from app.core.config import settings
from app.core.database import get_db
from app.db.session import mongo
from app.models.user import User
from app.services.call_service import (
    call_hub, create_join_token, ensure_call_indexes, expire_stale_calls, get_call, public_call, require_livekit,
    transition, utcnow, write_event,
)

router = APIRouter(prefix="/calls", tags=["calls"])


class StartCallRequest(BaseModel):
    participant_ids: list[int] = Field(min_length=1, max_length=12)
    call_type: str = "audio"
    idempotency_key: str | None = Field(default=None, max_length=128)

    @field_validator("call_type")
    @classmethod
    def valid_type(cls, value: str) -> str:
        if value not in {"audio", "video"}:
            raise ValueError("call_type must be audio or video")
        return value


class CallMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class AddCallParticipantRequest(BaseModel):
    participant_id: int = Field(gt=0)


def _authorize(call: dict[str, Any], user: User) -> None:
    if int(user.id) not in [int(item) for item in call.get("participant_ids", [])]:
        raise HTTPException(status_code=403, detail="You are not a participant in this call")


async def _notify(call: dict[str, Any], event: str) -> None:
    await call_hub.send(call.get("participant_ids", []), {"type": event, "call": public_call(call)})


@router.on_event("startup")
async def _startup() -> None:
    await ensure_call_indexes()


@router.post("")
async def start_call(request: StartCallRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_livekit()
    participant_ids = list(dict.fromkeys([int(current_user.id), *request.participant_ids]))
    if len(participant_ids) < 2:
        raise HTTPException(status_code=400, detail="A call needs at least one other participant")
    if len(participant_ids) > settings.LIVEKIT_MAX_PARTICIPANTS:
        raise HTTPException(status_code=400, detail="Too many call participants")
    users = db.query(User).filter(User.id.in_(participant_ids), User.is_active.is_(True)).all()
    if len(users) != len(participant_ids):
        raise HTTPException(status_code=404, detail="One or more call participants were not found")
    await mongo.verify_ready()
    busy_call = await mongo.calls.find_one({"participant_ids": {"$in": participant_ids}, "status": {"$in": ["ringing", "accepted", "active"]}})
    if busy_call:
        raise HTTPException(status_code=409, detail="One or more users are already busy in another call")
    if request.idempotency_key:
        existing = await mongo.calls.find_one({"initiated_by": int(current_user.id), "idempotency_key": request.idempotency_key})
        if existing:
            return public_call(existing)
    call = {
        "call_id": secrets.token_urlsafe(18), "room_name": f"aiw-{secrets.token_urlsafe(18)}",
        "initiated_by": int(current_user.id), "participant_ids": participant_ids,
        "call_type": request.call_type, "status": "ringing", "missed": False,
        "idempotency_key": request.idempotency_key, "created_at": utcnow(), "updated_at": utcnow(),
    }
    await mongo.calls.insert_one(call)
    await write_event(call["call_id"], "ringing", int(current_user.id))
    await call_hub.send([item for item in participant_ids if item != int(current_user.id)], {"type": "call.incoming", "call": public_call(call)})
    return public_call(call)


@router.get("")
async def list_calls(limit: int = Query(default=50, ge=1, le=100), current_user: User = Depends(get_current_user)):
    await mongo.verify_ready()
    await expire_stale_calls(int(current_user.id))
    cursor = mongo.calls.find({"participant_ids": int(current_user.id)}).sort("updated_at", -1).limit(limit)
    return [public_call(item) async for item in cursor]


@router.get("/availability")
async def call_availability(
    user_ids: str = Query(default="", max_length=1000),
    current_user: User = Depends(get_current_user),
):
    """Return busy state for searched users without exposing call metadata."""
    try:
        requested = list(dict.fromkeys(int(value) for value in user_ids.split(",") if value.strip()))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="user_ids must be comma-separated integers") from exc
    requested = [value for value in requested if value != int(current_user.id)][:100]
    if not requested:
        return {}
    await mongo.verify_ready()
    busy: dict[str, bool] = {str(value): False for value in requested}
    cursor = mongo.calls.find({"participant_ids": {"$in": requested}, "status": {"$in": ["ringing", "accepted", "active"]}}, {"participant_ids": 1})
    async for call in cursor:
        for participant_id in call.get("participant_ids", []):
            if str(participant_id) in busy:
                busy[str(participant_id)] = True
    return busy


@router.get("/{call_id}")
async def read_call(call_id: str, current_user: User = Depends(get_current_user)):
    call = await get_call(call_id)
    if not call: raise HTTPException(status_code=404, detail="Call not found")
    _authorize(call, current_user)
    return public_call(call)


@router.get("/{call_id}/messages")
async def list_call_messages(call_id: str, limit: int = Query(default=100, ge=1, le=200), current_user: User = Depends(get_current_user)):
    call = await get_call(call_id)
    if not call: raise HTTPException(status_code=404, detail="Call not found")
    _authorize(call, current_user)
    cursor = mongo.call_messages.find({"call_id": call_id}).sort("created_at", 1).limit(limit)
    return [{key: value.isoformat() if isinstance(value, datetime) else value for key, value in item.items() if key != "_id"} async for item in cursor]


@router.post("/{call_id}/messages")
async def send_call_message(call_id: str, request: CallMessageRequest, current_user: User = Depends(get_current_user)):
    call = await get_call(call_id)
    if not call: raise HTTPException(status_code=404, detail="Call not found")
    _authorize(call, current_user)
    if call.get("status") in {"ended", "rejected", "cancelled", "missed"}:
        raise HTTPException(status_code=409, detail="Call chat is closed")
    message = {"message_id": secrets.token_urlsafe(18), "call_id": call_id, "sender_id": int(current_user.id), "sender_name": current_user.full_name or current_user.email or str(current_user.id), "content": request.content.strip(), "created_at": utcnow()}
    await mongo.call_messages.insert_one(message)
    await write_event(call_id, "message", int(current_user.id), {"message_id": message["message_id"]})
    await call_hub.send(call.get("participant_ids", []), {"type": "call.message", "message": {key: value.isoformat() if isinstance(value, datetime) else value for key, value in message.items()}})
    return {key: value.isoformat() if isinstance(value, datetime) else value for key, value in message.items()}


@router.post("/{call_id}/participants")
async def add_call_participant(call_id: str, request: AddCallParticipantRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    call = await get_call(call_id)
    if not call: raise HTTPException(status_code=404, detail="Call not found")
    _authorize(call, current_user)
    if call.get("status") in {"ended", "rejected", "cancelled", "missed"}:
        raise HTTPException(status_code=409, detail="Call has ended")
    if request.participant_id in call.get("participant_ids", []):
        return public_call(call)
    participant = db.query(User).filter(User.id == request.participant_id, User.is_active.is_(True)).first()
    if not participant: raise HTTPException(status_code=404, detail="Participant not found")
    busy_call = await mongo.calls.find_one({"participant_ids": request.participant_id, "status": {"$in": ["ringing", "accepted", "active"]}})
    if busy_call: raise HTTPException(status_code=409, detail="Participant is busy in another call")
    call["participant_ids"] = [*call.get("participant_ids", []), request.participant_id]
    call["updated_at"] = utcnow()
    await mongo.calls.update_one({"call_id": call_id}, {"$set": {"participant_ids": call["participant_ids"], "updated_at": call["updated_at"]}})
    await write_event(call_id, "participant_invited", int(current_user.id), {"participant_id": request.participant_id})
    await call_hub.send([request.participant_id], {"type": "call.incoming", "call": public_call(call)})
    return public_call(call)


@router.post("/{call_id}/accept")
async def accept_call(call_id: str, current_user: User = Depends(get_current_user)):
    call = await get_call(call_id)
    if not call: raise HTTPException(status_code=404, detail="Call not found")
    _authorize(call, current_user)
    if call.get("initiated_by") == int(current_user.id): raise HTTPException(status_code=400, detail="Caller cannot accept their own call")
    if call.get("status") == "active":
        # The caller may already be connected while the recipient's ringing
        # UI is still visible; accepting that invitation is idempotent.
        accepted = call
    else:
        accepted = await transition(call, "accepted", int(current_user.id))
    call = accepted
    await _notify(call, "call.accepted")
    return public_call(call)


async def _finish(call_id: str, current_user: User, status: str):
    call = await get_call(call_id)
    if not call: raise HTTPException(status_code=404, detail="Call not found")
    _authorize(call, current_user)
    call = await transition(call, status, int(current_user.id))
    await _notify(call, f"call.{status}")
    return public_call(call)


@router.post("/{call_id}/reject")
async def reject_call(call_id: str, current_user: User = Depends(get_current_user)): return await _finish(call_id, current_user, "rejected")


@router.post("/{call_id}/cancel")
async def cancel_call(call_id: str, current_user: User = Depends(get_current_user)):
    call = await get_call(call_id)
    if not call: raise HTTPException(status_code=404, detail="Call not found")
    _authorize(call, current_user)
    if call.get("initiated_by") != int(current_user.id): raise HTTPException(status_code=403, detail="Only the caller can cancel an unanswered call")
    call = await transition(call, "cancelled", int(current_user.id))
    await _notify(call, "call.cancelled")
    return public_call(call)


@router.post("/{call_id}/end")
async def end_call(call_id: str, current_user: User = Depends(get_current_user)): return await _finish(call_id, current_user, "ended")


@router.post("/{call_id}/join")
async def join_call(call_id: str, current_user: User = Depends(get_current_user)):
    call = await get_call(call_id)
    if not call: raise HTTPException(status_code=404, detail="Call not found")
    _authorize(call, current_user)
    if call.get("status") not in {"accepted", "active"} and call.get("initiated_by") != int(current_user.id):
        raise HTTPException(status_code=409, detail="Accept the call before joining")
    token = await create_join_token(call, current_user)
    if call.get("status") in {"ringing", "accepted"}:
        call = await transition(call, "active", int(current_user.id))
    return {"server_url": settings.LIVEKIT_URL, "participant_token": token, "call": public_call(call)}


@router.websocket("/ws")
async def call_signalling(websocket: WebSocket, token: str = Query(default="")):
    # Prefer the WebSocket subprotocol so the bearer token is not placed in a
    # URL that may be copied into access logs/history. Query-token support is
    # retained only for native clients whose WebSocket API cannot set a custom
    # header or protocol.
    protocol_token = websocket.headers.get("sec-websocket-protocol", "").split(",")[0].strip()
    token = protocol_token or token
    if not token:
        await websocket.close(code=4401); return
    try:
        payload = security.jwt.decode(token, security.JWT_SECRET, algorithms=[security.ALGORITHM])
        identifier = payload.get("sub")
        if not identifier: raise ValueError("missing subject")
        db = next(get_db())
        user = db.query(User).filter((User.identifier == identifier) | (User.email == identifier)).first()
        if not user or not user.is_active: raise ValueError("inactive user")
    except Exception:
        await websocket.close(code=4401); return
    await websocket.accept()
    call_hub.add(int(user.id), websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        call_hub.remove(int(user.id), websocket)
    finally:
        try: db.close()
        except Exception: pass


@router.post("/webhook")
async def livekit_webhook(request: Request, authorization: str | None = Header(default=None)):
    require_livekit()
    body = (await request.body()).decode("utf-8")
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing LiveKit webhook authorization")
    try:
        from livekit import api
        event = api.WebhookReceiver(api.TokenVerifier(settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET)).receive(body, authorization)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid LiveKit webhook signature") from exc
    event_name = getattr(event, "event", "")
    room = getattr(event, "room", None)
    room_name = getattr(room, "name", None) if room else None
    if room_name and mongo.enabled:
        call = await mongo.calls.find_one({"room_name": room_name})
        if call:
            await mongo.calls.update_one({"call_id": call["call_id"]}, {"$set": {"livekit_event": event_name, "updated_at": utcnow()}})
            await write_event(call["call_id"], f"livekit:{event_name}", 0, {"room_name": room_name})
    return {"received": True}
