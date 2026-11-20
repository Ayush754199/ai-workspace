"""Knowledge base: server-side chunks from the study PDFs.

These are private AI context — never user-downloadable. Every PDF is split into
~3000-character chunks (with overlap) and stored in SQL so any chat turn can
retrieve the most relevant passages and feed them to the model.

Design notes:
* No raw file bytes are ever returned to the browser: the download endpoint does
  not exist on purpose. The PDFs serve only as the AI's knowledge source.
* Chunking is per-document so a chunk knows its source book/subject for citation.
* Full text is stored in `content` (Postgres/SQLite TEXT) — Render's container
  disk is ephemeral, so nothing depends on on-disk persistence.
"""

from __future__ import annotations

from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.sql import func

from app.core.database import Base


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"

    id = Column(Integer, primary_key=True)
    # Which book/PDF this chunk came from (human title, e.g. "Boericke Pocket Manual").
    document_title = Column(String(300), nullable=False, index=True)
    # Coarse subject bucket: materia-medica | repertory | organon | surgery | ...
    subject = Column(String(120), nullable=True, index=True)
    # Source file name inside study_resources/ (for admin reference, never shipped to UI).
    source_file = Column(String(255), nullable=True)
    chunk_index = Column(Integer, nullable=False, default=0)
    # The actual passage. Kept bounded per chunk (~3000 chars) so retrieval stays cheap.
    content = Column(Text, nullable=False)
    char_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())