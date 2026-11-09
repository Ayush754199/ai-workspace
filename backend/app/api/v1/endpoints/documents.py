"""PDF / document endpoints: extract, summarise, Q&A and export."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.api.deps import get_current_user
from app.models.user import User
from app.services import documents as docs
from app.services.feature_flags import require_feature
from app.services.ai_gateway import GatewayUnavailableError
from app.services.chat_service import ChatConcurrencyError, generate_reply
from app.core.database import get_db
from sqlalchemy.orm import Session

router = APIRouter(prefix="/documents", tags=["documents"], dependencies=[Depends(require_feature("documents"))])

MAX_UPLOAD = 25 * 1024 * 1024
ALLOWED = {".pdf", ".docx", ".pptx", ".xlsx", ".csv", ".txt", ".md"}


def _validate(filename: str, data: bytes) -> str:
    lower = (filename or "").lower()
    extension = next((ext for ext in ALLOWED if lower.endswith(ext)), None)
    if extension is None:
        raise HTTPException(status_code=415, detail=f"Unsupported document type. Allowed: {', '.join(sorted(ALLOWED))}")
    if not data:
        raise HTTPException(status_code=400, detail="The uploaded file is empty")
    if len(data) > MAX_UPLOAD:
        raise HTTPException(status_code=413, detail="File exceeds the 25 MB limit")
    return extension


class GenerateRequest(BaseModel):
    title: str = Field(default="", max_length=200)
    content: str = Field(min_length=1, max_length=400_000)
    format: str = Field(default="pdf", pattern="^(pdf|docx|pptx|csv|xlsx)$")
    filename: str = Field(default="document", max_length=120)


class TableRequest(BaseModel):
    title: str = ""
    sheet: str = "Data"
    rows: list[dict] = Field(min_length=1, max_length=5000)


MEDIA_TYPES = {
    "pdf": ("application/pdf", ".pdf"),
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"),
    "pptx": ("application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx"),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"),
    "csv": ("text/csv", ".csv"),
}


def _file_response(payload: bytes, kind: str, filename: str) -> Response:
    media_type, extension = MEDIA_TYPES[kind]
    safe = "".join(ch for ch in (filename or "document") if ch.isalnum() or ch in "-_ ").strip() or "document"
    return Response(
        content=payload,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{safe}{extension}"',
            # The browser can only read the filename when the header is exposed.
            "Access-Control-Expose-Headers": "Content-Disposition",
        },
    )


@router.post("/extract")
async def extract(file: UploadFile = File(...), _: User = Depends(get_current_user)):
    data = await file.read()
    extension = _validate(file.filename or "", data)
    try:
        kind, text = docs.extract_any(data, file.filename or "", file.content_type)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not read the document: {exc}") from exc
    meta = {"pages": docs.pdf_page_count(data)} if extension == ".pdf" else {}
    return {
        "filename": file.filename,
        "kind": kind,
        "characters": len(text),
        "words": len(text.split()),
        **meta,
        "text": text,
        "ocr_unavailable": kind == "image" and not text,
    }


@router.post("/summarize")
async def summarize(
    file: UploadFile = File(...),
    style: str = Form(default="summary"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if style not in {"summary", "bullets", "outline", "key_facts"}:
        raise HTTPException(status_code=400, detail="style must be summary, bullets, outline or key_facts")
    data = await file.read()
    _validate(file.filename or "", data)
    try:
        _, text = docs.extract_any(data, file.filename or "", file.content_type)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not read the document: {exc}") from exc
    if not text.strip():
        raise HTTPException(status_code=422, detail="No extractable text was found in this file (scanned PDFs need OCR).")
    instructions = {
        "summary": "Write a concise summary in 5-8 sentences.",
        "bullets": "Summarise as 6-10 bullet points, each one line.",
        "outline": "Produce a structured outline with headings and nested bullets.",
        "key_facts": "List the key facts, figures and dates with one line each.",
    }[style]
    prompt = f"{instructions}\n\nDocument ({file.filename}):\n\n{text[:60_000]}"
    try:
        answer, provider, _model = await generate_reply(
            db, user_id=user.id, prompt=prompt,
            system_prompt="You are a precise document analyst. Never invent facts that are not in the document.",
        )
    except (GatewayUnavailableError, ChatConcurrencyError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"filename": file.filename, "style": style, "summary": answer, "provider": provider, "characters": len(text)}


@router.post("/ask")
async def ask(
    file: UploadFile = File(...),
    question: str = Form(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not question.strip():
        raise HTTPException(status_code=400, detail="Ask a question about the document")
    data = await file.read()
    _validate(file.filename or "", data)
    try:
        _, text = docs.extract_any(data, file.filename or "", file.content_type)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not read the document: {exc}") from exc
    if not text.strip():
        raise HTTPException(status_code=422, detail="No extractable text was found in this file")
    prompt = (
        "Answer the question using only the document below. "
        "If the answer is not present, say that the document does not contain it.\n\n"
        f"Document ({file.filename}):\n{text[:60_000]}\n\nQuestion: {question}"
    )
    try:
        answer, provider, _model = await generate_reply(
            db, user_id=user.id, prompt=prompt,
            system_prompt="You are a careful document Q&A assistant grounded in the supplied text.",
        )
    except (GatewayUnavailableError, ChatConcurrencyError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"question": question, "answer": answer, "provider": provider}


@router.post("/generate")
def generate(request: GenerateRequest, _: User = Depends(get_current_user)):
    try:
        if request.format == "pptx":
            payload = docs.generate_pptx(request.title, request.content)
        elif request.format == "pdf":
            payload = docs.generate_pdf(request.title, request.content)
        elif request.format == "docx":
            payload = docs.generate_docx(request.title, request.content)
        elif request.format == "xlsx":
            rows = docs.table_from_text(request.content)
            payload = docs.generate_xlsx(rows, sheet_title=request.title or "Data", title=request.title)
        else:
            rows = docs.table_from_text(request.content)
            payload = docs.generate_csv(rows)
    except ImportError as exc:
        raise HTTPException(status_code=501, detail=f"Generator library not installed: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not generate the document: {exc}") from exc
    return _file_response(payload, request.format, request.filename or request.title or "document")


@router.post("/table")
def table(request: TableRequest, _: User = Depends(get_current_user)):
    payload = docs.generate_xlsx(request.rows, sheet_title=request.sheet, title=request.title)
    return _file_response(payload, "xlsx", request.title or request.sheet or "table")
