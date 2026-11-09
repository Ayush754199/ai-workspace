"""Membership-protected multi-user room APIs."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_room_member
from app.core.database import get_db
from app.models.room import Message, Room
from app.models.user import User
from app.services.gemini_service import GeminiProvider

router = APIRouter(prefix="/rooms", tags=["rooms"])


class CreateRoomRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    room_type: str = "group"

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value: str) -> str:
        """Whitespace is not a room name."""
        if not value.strip():
            raise ValueError("Room name cannot be empty")
        return value


class MessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=20000)


class InviteRequest(BaseModel):
    identifier: str = Field(min_length=1, max_length=255)


class RoomUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    is_pinned: bool | None = None
    is_archived: bool | None = None


def serialize_message(message: Message) -> dict:
    return {
        "id": message.id,
        "room_id": message.room_id,
        "sender_id": message.sender_id,
        "sender_name": message.sender_name,
        "sender_role": message.sender_role,
        "content": message.content,
        "mention_user_ids": message.mention_user_ids or [],
        "created_at": message.created_at.isoformat() if message.created_at else None,
    }


def serialize_room(room: Room, db: Session) -> dict:
    members = db.query(User).filter(User.id.in_(room.members or [])).all() if room.members else []
    last_message = db.query(Message).filter(Message.room_id == room.id).order_by(Message.id.desc()).first()
    return {
        "id": room.id,
        "name": room.name,
        "room_type": room.room_type,
        "members": [user.id for user in members],
        "member_details": [{"id": user.id, "name": user.full_name, "email": user.email, "role": user.role} for user in members],
        "last_message_at": last_message.created_at.isoformat() if last_message and last_message.created_at else None,
        "created_by": room.created_by,
        "is_pinned": room.is_pinned,
        "is_archived": room.is_archived,
    }


@router.post("")
def create_room(request: CreateRoomRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    room = Room(name=request.name, room_type=request.room_type, members=[current_user.id], created_by=current_user.id)
    db.add(room)
    db.commit()
    db.refresh(room)
    return serialize_room(room, db)


@router.get("")
def list_rooms(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rooms = db.query(Room).filter(Room.is_active.is_(True)).all()
    visible = [room for room in rooms if current_user.id in (room.members or [])]
    visible.sort(key=lambda room: (not room.is_pinned, room.is_archived, -(room.id or 0)))
    return [serialize_room(room, db) for room in visible]


def _owned_room(room_id: int, current_user: User, db: Session) -> Room:
    room = db.query(Room).filter(Room.id == room_id, Room.is_active.is_(True)).first()
    if room is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if room.created_by != current_user.id:
        raise HTTPException(status_code=403, detail="Only the conversation owner can manage it")
    return room


@router.patch("/{room_id}")
def update_room(
    room_id: int,
    request: RoomUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    room = _owned_room(room_id, current_user, db)
    for field, value in request.model_dump(exclude_unset=True).items():
        setattr(room, field, value.strip() if field == "name" and isinstance(value, str) else value)
    db.commit()
    db.refresh(room)
    return serialize_room(room, db)


@router.delete("/{room_id}")
def delete_room(room_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    room = _owned_room(room_id, current_user, db)
    room.is_active = False
    db.commit()
    return {"deleted": True, "room_id": room_id}


@router.get("/{room_id}/messages")
def list_messages(
    room: Room = Depends(require_room_member),
    limit: int = Query(default=50, ge=1, le=100),
    before_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
):
    query = db.query(Message).filter(Message.room_id == room.id)
    if before_id is not None:
        query = query.filter(Message.id < before_id)
    messages = query.order_by(Message.id.desc()).limit(limit).all()
    return [serialize_message(message) for message in reversed(messages)]


@router.post("/{room_id}/messages")
def create_message(
    request: MessageRequest,
    room: Room = Depends(require_room_member),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    content = request.content.strip()
    if not content:
        raise HTTPException(status_code=422, detail="Message cannot be empty")
    mentioned_users = db.query(User).all()
    lowered_content = content.lower()
    mention_ids = [
        user.id for user in mentioned_users
        if f"@{(user.full_name or '').lower().replace(' ', '_')}" in lowered_content
        or f"@{user.email.split('@')[0].lower()}" in lowered_content
    ]
    message = Message(
        room_id=room.id,
        sender_id=current_user.id,
        sender_name=current_user.full_name or current_user.email,
        sender_role=current_user.role or "user",
        content=content,
        mention_user_ids=mention_ids,
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    return serialize_message(message)


@router.post("/{room_id}/members")
def invite_member(
    request: InviteRequest,
    room: Room = Depends(require_room_member),
    db: Session = Depends(get_db),
):
    identifier = request.identifier.strip()
    user = db.query(User).filter((User.email == identifier) | (User.full_name == identifier)).first()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id in (room.members or []):
        raise HTTPException(status_code=409, detail="User is already a room member")
    room.members = [*(room.members or []), user.id]
    db.commit()
    return {"message": "Member invited", "user_id": user.id}


@router.post("/{room_id}/summary")
async def summarize_room(
    room: Room = Depends(require_room_member),
    db: Session = Depends(get_db),
):
    messages = db.query(Message).filter(Message.room_id == room.id).order_by(Message.id.asc()).all()
    transcript = "\n".join(f"{message.sender_name} ({message.sender_role}): {message.content}" for message in messages)
    if not transcript:
        return {"room_id": room.id, "summary": "No messages available.", "key_decisions": []}
    prompt = (
        "Summarize this meeting transcript as strict JSON with keys summary and key_decisions. "
        "key_decisions must be an array of concise decision strings. Do not include markdown.\n\n" + transcript
    )
    result = await GeminiProvider().process_direct_query(prompt, enable_web_search=False)
    raw_summary = result.get("response", "")
    try:
        import json
        structured = json.loads(raw_summary)
        return {"room_id": room.id, "summary": structured.get("summary", raw_summary), "key_decisions": structured.get("key_decisions", [])}
    except (TypeError, ValueError):
        return {"room_id": room.id, "summary": raw_summary, "key_decisions": []}
