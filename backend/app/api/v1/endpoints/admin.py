"""Administrator-only user and usage management endpoints."""

import csv
import io
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin
from app.core.database import get_db
from app.models.user import User
from app.models.room import AdminAuditLog, Message, Room

router = APIRouter(prefix="/admin", tags=["admin"])


class UserStatusUpdate(BaseModel):
    is_active: bool | None = None
    is_admin: bool | None = None
    role: str | None = None


def serialize_user(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "is_active": user.is_active,
        "is_admin": user.is_admin,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


@router.get("/users")
def list_users(_: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    return [serialize_user(user) for user in db.query(User).order_by(User.created_at.desc()).all()]


@router.get("/analytics")
def get_analytics(_: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    total_users = db.query(User).count()
    active_users = db.query(User).filter(User.is_active.is_(True)).count()
    return {
        "total_users": total_users,
        "active_chats": 0,
        "documents_analyzed": 0,
        "excel_exports_generated": 0,
        "active_users": active_users,
    }


@router.patch("/users/{user_id}/status")
def update_user_status(
    user_id: int,
    update: UserStatusUpdate,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == current_admin.id and update.is_active is False:
        raise HTTPException(status_code=400, detail="You cannot disable your own account")
    for field, value in update.model_dump(exclude_unset=True).items():
        setattr(user, field, value)
    db.commit()
    db.refresh(user)
    return serialize_user(user)


def serialize_admin_room(room: Room, db: Session) -> dict:
    members = db.query(User).filter(User.id.in_(room.members or [])).all() if room.members else []
    last_message = db.query(Message).filter(Message.room_id == room.id).order_by(Message.id.desc()).first()
    return {
        "id": room.id,
        "name": room.name,
        "room_type": room.room_type,
        "members": [{"id": user.id, "name": user.full_name, "email": user.email, "role": user.role} for user in members],
        "last_message_at": last_message.created_at.isoformat() if last_message and last_message.created_at else None,
    }


def record_audit(db: Session, admin: User, action: str, room_id: int | None = None) -> None:
    db.add(AdminAuditLog(admin_id=admin.id, action=action, room_id=room_id))
    db.commit()


@router.get("/chats")
def list_all_chats(
    room_name: str | None = Query(default=None),
    username: str | None = Query(default=None),
    role: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    rooms = db.query(Room).filter(Room.is_active.is_(True)).order_by(Room.id.desc()).all()
    results = []
    for room in rooms:
        members = db.query(User).filter(User.id.in_(room.members or [])).all() if room.members else []
        messages = db.query(Message).filter(Message.room_id == room.id).all()
        searchable_users = members + [db.query(User).filter(User.id == message.sender_id).first() for message in messages]
        searchable_users = [user for user in searchable_users if user is not None]
        if room_name and room_name.lower() not in room.name.lower():
            continue
        if username and not any(username.lower() in (user.full_name or "").lower() or username.lower() in user.email.lower() for user in searchable_users):
            continue
        if role and not any((user.role or "").lower() == role.lower() for user in searchable_users):
            continue
        results.append(serialize_admin_room(room, db))
    record_audit(db, current_admin, "list_chats")
    return {"items": results[offset: offset + limit], "total": len(results), "limit": limit, "offset": offset}


@router.get("/chats/{room_id}")
def inspect_chat(
    room_id: int,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    room = db.query(Room).filter(Room.id == room_id).first()
    if room is None:
        raise HTTPException(status_code=404, detail="Room not found")
    members = db.query(User).filter(User.id.in_(room.members or [])).all() if room.members else []
    messages = db.query(Message).filter(Message.room_id == room.id).order_by(Message.id.asc()).all()
    record_audit(db, current_admin, "view_transcript", room.id)
    return {
        **serialize_admin_room(room, db),
        "messages": [{
            "id": message.id,
            "sender_id": message.sender_id,
            "sender_name": message.sender_name,
            "sender_role": message.sender_role,
            "content": message.content,
            "created_at": message.created_at.isoformat() if message.created_at else None,
        } for message in messages],
        "member_count": len(members),
    }


@router.get("/audit/export")
def export_audit_csv(current_admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    logs = db.query(AdminAuditLog).order_by(AdminAuditLog.created_at.desc()).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "admin_id", "action", "room_id", "created_at"])
    for log in logs:
        writer.writerow([log.id, log.admin_id, log.action, log.room_id or "", log.created_at.isoformat() if log.created_at else ""])
    record_audit(db, current_admin, "export_audit_csv")
    output.seek(0)
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=admin-audit.csv"})