"""Export current knowledge chunks to a gzipped JSONL seed.

Render's container filesystem is ephemeral and the study PDFs are NOT committed,
so this seed is how the knowledge base gets populated on a fresh deploy.
"""

import gzip
import json
import sys

sys.path.insert(0, r"C:\Users\aadiu\OneDrive\Desktop\AI Workspace v1.0\backend")

from app.core.database import SessionLocal, engine, Base
from app.models.library import KnowledgeChunk
import app.models.library  # noqa: F401

OUT = r"C:\Users\aadiu\OneDrive\Desktop\AI Workspace v1.0\backend\app\data\knowledge_seed.jsonl.gz"

Base.metadata.create_all(bind=engine)
db = SessionLocal()
rows = db.query(KnowledgeChunk).order_by(KnowledgeChunk.id.asc()).all()
db.close()

import os
os.makedirs(os.path.dirname(OUT), exist_ok=True)

with gzip.open(OUT, "wt", encoding="utf-8") as f:
    for row in rows:
        f.write(json.dumps({
            "title": row.document_title,
            "subject": row.subject,
            "source": row.source_file,
            "index": row.chunk_index,
            "content": row.content,
        }, ensure_ascii=False) + "\n")

size = os.path.getsize(OUT) / 1024 / 1024
print(f"exported {len(rows)} chunks -> {OUT} ({size:.2f} MB gzipped)")