from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.sql import func
from sqlalchemy.types import TypeDecorator

from app.core.database import Base


class MemberList(TypeDecorator):
    """JSON list of user ids that round-trips as a list on every backend.

    SQLite returns whatever was stored in a JSON column verbatim; when a single
    member was written it came back as ``71`` instead of ``[71]``, and a one-member
    room then failed its own membership check.
    """

    impl = JSON
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return []
        if isinstance(value, int):
            return [value]
        return [int(member) for member in value]

    def process_result_value(self, value, dialect):
        if value is None:
            return []
        if isinstance(value, int):
            return [value]
        return [int(member) for member in value]


class Room(Base):
    __tablename__ = "rooms"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(120), nullable=False)
    room_type = Column(String(20), nullable=False, default="group")
    members = Column(MemberList, nullable=False, default=list)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    is_pinned = Column(Boolean, nullable=False, default=False)
    is_archived = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)
    room_id = Column(Integer, ForeignKey("rooms.id"), nullable=False, index=True)
    sender_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    sender_name = Column(String(255), nullable=False)
    sender_role = Column(String(80), nullable=False)
    content = Column(Text, nullable=False)
    mention_user_ids = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)


class AdminAuditLog(Base):
    __tablename__ = "admin_audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    admin_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    action = Column(String(80), nullable=False)
    room_id = Column(Integer, ForeignKey("rooms.id"), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
