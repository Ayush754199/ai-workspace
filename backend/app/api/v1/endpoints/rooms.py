"""Membership-protected multi-user room APIs."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_room_member
from app.core.database import get_db
from app.models.room import Message, Room, RoomInvite
from app.models.user import User
from app.services.gemini_service import GeminiProvider
import secrets

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


@router.get("/users/search")
def search_users(
    q: str = Query(default="", max_length=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return active users matching the invite field without exposing passwords."""
    query = q.strip()
    if not query:
        return []
    pattern = f"%{query}%"
    users = db.query(User).filter(
        User.id != current_user.id,
        User.is_active.is_(True),
        (
            User.email.ilike(pattern)
            | User.full_name.ilike(pattern)
            | User.identifier.ilike(pattern)
            | User.mobile_number.ilike(pattern)
        ),
    ).order_by(User.full_name.asc(), User.email.asc()).limit(10).all()
    return [{"id": user.id, "name": user.full_name, "email": user.email, "role": user.role} for user in users]


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
        or f"@{(user.email or '').split('@')[0].lower()}" in lowered_content
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
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    identifier = request.identifier.strip()
    user = db.query(User).filter((User.email == identifier) | (User.full_name == identifier)).first()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id in (room.members or []):
        raise HTTPException(status_code=409, detail="User is already a room member")
    existing = db.query(RoomInvite).filter(
        RoomInvite.room_id == room.id,
        RoomInvite.invited_user_id == user.id,
        RoomInvite.status == "pending",
    ).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="User already has a pending room invitation")
    db.add(RoomInvite(
        room_id=room.id,
        token=secrets.token_urlsafe(32),
        created_by=current_user.id,
        invited_user_id=user.id,
        status="pending",
    ))
    db.commit()
    return {"message": "Invitation sent", "user_id": user.id, "status": "pending"}


def serialize_invitation(invite: RoomInvite, room: Room, inviter: User | None = None) -> dict:
    return {
        "id": invite.id,
        "room_id": room.id,
        "room_name": room.name,
        "invited_user_id": invite.invited_user_id,
        "created_by": invite.created_by,
        "inviter_name": inviter.full_name if inviter else None,
        "status": invite.status,
        "created_at": invite.created_at.isoformat() if invite.created_at else None,
    }


@router.get("/invitations")
def list_invitations(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    invites = db.query(RoomInvite).filter(
        RoomInvite.invited_user_id == current_user.id,
        RoomInvite.status == "pending",
    ).order_by(RoomInvite.id.desc()).all()
    results = []
    for invite in invites:
        room = db.query(Room).filter(Room.id == invite.room_id, Room.is_active.is_(True)).first()
        if room is None:
            continue
        inviter = db.query(User).filter(User.id == invite.created_by).first()
        results.append(serialize_invitation(invite, room, inviter))
    return results


@router.post("/invitations/{invite_id}/accept")
def accept_invitation(invite_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    invite = db.query(RoomInvite).filter(
        RoomInvite.id == invite_id,
        RoomInvite.invited_user_id == current_user.id,
        RoomInvite.status == "pending",
    ).first()
    if invite is None:
        raise HTTPException(status_code=404, detail="Invitation not found")
    room = db.query(Room).filter(Room.id == invite.room_id, Room.is_active.is_(True)).first()
    if room is None:
        raise HTTPException(status_code=404, detail="Room not found")
    if current_user.id not in (room.members or []):
        room.members = [*(room.members or []), current_user.id]
    invite.status = "accepted"
    db.commit()
    db.refresh(room)
    return serialize_room(room, db)


@router.post("/invitations/{invite_id}/decline")
def decline_invitation(invite_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    invite = db.query(RoomInvite).filter(
        RoomInvite.id == invite_id,
        RoomInvite.invited_user_id == current_user.id,
        RoomInvite.status == "pending",
    ).first()
    if invite is None:
        raise HTTPException(status_code=404, detail="Invitation not found")
    invite.status = "declined"
    db.commit()
    return {"declined": True, "invite_id": invite_id}


@router.delete("/{room_id}/members/{user_id}")
def remove_member(
    user_id: int,
    room: Room = Depends(require_room_member),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if room.created_by != current_user.id:
        raise HTTPException(status_code=403, detail="Only the conversation owner can remove members")
    if user_id == room.created_by:
        raise HTTPException(status_code=400, detail="The conversation owner cannot be removed")
    if user_id not in (room.members or []):
        raise HTTPException(status_code=404, detail="User is not a room member")
    room.members = [member_id for member_id in (room.members or []) if member_id != user_id]
    db.commit()
    return {"removed": True, "user_id": user_id, "room_id": room.id}


@router.delete("/{room_id}/membership")
def leave_room(
    room: Room = Depends(require_room_member),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if room.created_by == current_user.id:
        raise HTTPException(status_code=400, detail="The conversation owner cannot leave the room")
    room.members = [member_id for member_id in (room.members or []) if member_id != current_user.id]
    db.commit()
    return {"left": True, "room_id": room.id}


@router.post("/{room_id}/invite-link")
def create_invite_link(
    room: Room = Depends(require_room_member),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a shareable link that lets an authenticated user join this room."""
    invite = RoomInvite(room_id=room.id, token=secrets.token_urlsafe(32), created_by=current_user.id)
    db.add(invite)
    db.commit()
    return {"token": invite.token, "room_id": room.id}


@router.post("/invites/{token}/accept")
def accept_invite_link(
    token: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    invite = db.query(RoomInvite).filter(RoomInvite.token == token).first()
    if invite is None:
        raise HTTPException(status_code=404, detail="Invite link is invalid or expired")
    room = db.query(Room).filter(Room.id == invite.room_id, Room.is_active.is_(True)).first()
    if room is None:
        raise HTTPException(status_code=404, detail="Room not found")
    if current_user.id not in (room.members or []):
        room.members = [*(room.members or []), current_user.id]
        db.commit()
        db.refresh(room)
    return serialize_room(room, db)


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
