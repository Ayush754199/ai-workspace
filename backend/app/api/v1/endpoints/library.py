"""Library / knowledge-base endpoints.

The PDFs are private AI context, never downloadable. These endpoints only let a
client inspect what is indexed and ingest new material server-side (admin or a
trusted ingestion job), plus surface a status summary. There is deliberately no
"download the original PDF" route — the knowledge is for the model, not the user.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, UploadFile, File, Form
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_legacy_ai_enabled
from app.core.database import get_db
from app.models.user import User
from app.models.library import KnowledgeChunk
from app.services import knowledge_base
from app.core import errors as error_utils

router = APIRouter(prefix="/library", tags=["library"], dependencies=[Depends(require_legacy_ai_enabled)])

# Subject buckets inferred from the file name / title for nicer filtering.
SUBJECT_HINTS = [
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


def _subject_for(name: str) -> str:
    low = (name or "").lower()
    for needle, subject in SUBJECT_HINTS:
        if needle in low:
            return subject
    return "general"


def _chunk_text(text: str, size: int = 3000, overlap: int = 300) -> list[str]:
    """Split text into ~size-char chunks with a small overlap."""
    text = (text or "").strip()
    if not text:
        return []
    chunks: list[str] = []
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


@router.get("/status")
def library_status(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return knowledge_base.knowledge_summary(db)


@router.post("/ingest")
async def ingest_pdf(
    file: UploadFile = File(...),
    title: str = Form(default=""),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Ingest one PDF: extract text, chunk it, store. Private — never user-downloadable."""
    data = await file.read()
    if not data:
        raise error_utils.bad_request("The uploaded PDF is empty")
    if len(data) > 40 * 1024 * 1024:
        raise error_utils.bad_request("PDF exceeds the 40 MB limit")

    # Extract text with a pure-python fallback if pypdf is unavailable.
    text = ""
    try:
        import io as _io
        from pypdf import PdfReader

        reader = PdfReader(_io.BytesIO(data))
        pages = []
        for page in reader.pages:
            try:
                pages.append(page.extract_text() or "")
            except Exception:
                continue
        text = "\n\n".join(pages)
    except Exception as exc:
        raise error_utils.bad_request(f"Could not read this PDF: {exc}") from exc

    if not text.strip():
        raise error_utils.bad_request("The PDF contains no extractable text (it may be scanned images).")

    doc_title = (title or file.filename or "Untitled").strip()[:300] or "Untitled"
    subject = _subject_for(file.filename or title)
    chunks = _chunk_text(text)
    if not chunks:
        raise error_utils.bad_request("The PDF yielded no usable text chunks")

    stored = 0
    for index, chunk in enumerate(chunks):
        row = KnowledgeChunk(
            document_title=doc_title,
            subject=subject,
            source_file=file.filename,
            chunk_index=index,
            content=chunk,
            char_count=len(chunk),
        )
        db.add(row)
        stored += 1
    db.commit()
    return {"status": "ok", "title": doc_title, "subject": subject, "chunks_stored": stored}