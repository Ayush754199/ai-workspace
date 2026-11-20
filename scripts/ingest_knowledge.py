"""One-shot script: extract text from every PDF in study_resources/ and store as
knowledge chunks. Run once (or re-run to rebuild) after any PDF change."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, "/Users/aadiu/OneDrive/Desktop/AI Workspace v1.0/backend")

from app.core.database import SessionLocal, engine, Base
from app.services import knowledge_base
from app.models.library import KnowledgeChunk
from app.core.config import settings
from pypdf import PdfReader
import importlib

# Ensure app.models.library is imported so the table is registered with Base.metadata.
import app.models.library  # noqa: F401  (import registers the table)

RESOURCES_DIR = "/Users/aadiu/OneDrive/Desktop/AI Workspace v1.0/study_resources"


def chunk_text(text: str, size: int = 3000, overlap: int = 300) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = end - overlap
    return chunks


def subject_for(name: str) -> str:
    low = (name or "").lower()
    hints = [
        ("materia", "materia-medica"),
        ("boericke", "materia-medica"),
        ("kent_lectures", "materia-medica"),
        ("allen", "materia-medica"),
        ("choudhury", "materia-medica"),
        ("organon", "organon"),
        ("repertory", "repertory"),
        ("boger", "repertory"),
        ("bailey", "surgery"),
        ("surgery", "surgery"),
        ("dutta", "obg"),
        ("park", "community-medicine"),
        ("pharmacology", "pharmacology"),
        ("pathology", "pathology"),
        ("forensic", "forensic-medicine"),
        ("yoga", "yoga"),
        ("jsps", "syllabus"),
    ]
    for needle, subject in hints:
        if needle in low:
            return subject
    return "general"


def main():
    # Ensure table exists
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        existing = db.query(KnowledgeChunk.id).limit(1).first()
        if existing:
            print("knowledge_chunks already populated; skipping ingest")
            return

        files = [f for f in Path(RESOURCES_DIR).glob("*.pdf") if f.is_file()]
        print(f"Found {len(files)} PDFs")
        total_chunks = 0
        for pdf_path in sorted(files):
            print(f"Ingesting {pdf_path.name} ... ", end="")
            reader = PdfReader(str(pdf_path))
            pages_text = [page.extract_text() or "" for page in reader.pages]
            text = "\n\n".join(pages_text).strip()
            if not text.strip():
                print("SKIP (no text)")
                continue
            chunks = chunk_text(text)
            subject = subject_for(pdf_path.stem)
            title = pdf_path.stem.replace("_", " ").title()
            stored = 0
            for index, chunk in enumerate(chunks):
                db.add(KnowledgeChunk(
                    document_title=title,
                    subject=subject,
                    source_file=pdf_path.name,
                    chunk_index=index,
                    content=chunk,
                    char_count=len(chunk),
                ))
                stored += 1
            db.commit()
            total_chunks += stored
            print(f"{stored} chunks, subject={subject}")

        summary = knowledge_base.knowledge_summary(db)
        print("Ingestion complete")
        print(json.dumps(summary, indent=2))
    finally:
        db.close()


if __name__ == "__main__":
    main()