"""Persistent AI chat: conversations, messages and attachments.

This is separate from the collaboration `rooms` model: rooms are team channels,
these tables back the personal AI chat workspace (history, context, attachments).
"""

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.sql import func

from app.core.database import Base


class AiConversation(Base):
    __tablename__ = "ai_conversations"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String(200), nullable=False, default="New chat")
    model = Column(String(160), nullable=True)
    provider = Column(String(80), nullable=True)
    system_prompt = Column(Text, nullable=True)
    mode = Column(String(40), nullable=False, default="chat")
    is_pinned = Column(Boolean, nullable=False, default=False)
    project_name = Column(String(120), nullable=True, index=True)
    project_pinned = Column(Boolean, nullable=False, default=False)
    is_archived = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AiMessage(Base):
    __tablename__ = "ai_messages"
    id = Column(Integer, primary_key=True)
    conversation_id = Column(Integer, ForeignKey("ai_conversations.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    role = Column(String(20), nullable=False)  # user | assistant | system
    content = Column(Text, nullable=False, default="")
    provider = Column(String(80), nullable=True)
    model = Column(String(160), nullable=True)
    status = Column(String(30), nullable=False, default="complete")  # streamed | complete | failed
    request_id = Column(String(160), nullable=True, index=True)
    attachments = Column(JSON, nullable=False, default=list)
    citations = Column(JSON, nullable=False, default=list)
    meta = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class AiAttachment(Base):
    __tablename__ = "ai_attachments"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    conversation_id = Column(Integer, ForeignKey("ai_conversations.id"), nullable=True, index=True)
    filename = Column(String(255), nullable=False)
    content_type = Column(String(120), nullable=False)
    kind = Column(String(20), nullable=False)  # image | pdf | text | audio | video | document
    size_bytes = Column(Integer, nullable=False, default=0)
    extracted_text = Column(Text, nullable=False, default="")
    storage_path = Column(String(400), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
