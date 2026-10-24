"""Authenticated multimodal OCR and session document memory."""

import io
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from PIL import Image
from pypdf import PdfReader
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.config import settings
from app.db.session import mongo
from app.models.user import User
from app.services.document_generator import GENERATORS

router = APIRouter(prefix="/chat", tags=["chat"])


class ExportRequest(BaseModel):
    content: str = ""
    data: list[dict[str, Any]] = Field(default_factory=list)
    format: str
    filename: str = "ai_workspace_export"

VISION_INSTRUCTIONS = """You are a careful document vision and OCR assistant.
Read printed and handwritten text, diagrams, tables, symbols, and mathematical formulas accurately.
Preserve meaningful layout in clean Markdown. Mark uncertain text as [unclear] instead of guessing.
When answering a question, use only the supplied document context and clearly state when the document does not contain the answer.
"""
ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp"}
MAX_FILE_SIZE = 20 * 1024 * 1024


def extract_pdf_text(data: bytes) -> str:
    reader = PdfReader(io.BytesIO(data))
    return "\n\n".join(page.extract_text() or "" for page in reader.pages).strip()


async def call_gemini(prompt: str, attachments: list[tuple[bytes, str]] | None = None) -> str:
    if settings.GEMINI_API_KEY == "default-test-key":
        return "Gemini is not configured. Add GEMINI_API_KEY to enable document OCR."

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    contents: list[Any] = [prompt]
    for data, mime_type in attachments or []:
        contents.append(types.Part.from_bytes(data=data, mime_type=mime_type))
    response = client.models.generate_content(model="gemini-2.0-flash", contents=contents)
    return response.text or ""


async def load_session_context(session_id: str, user_id: int) -> str:
    if not mongo.enabled:
        return ""
    cursor = mongo.chat_history.find({"session_id": session_id, "user_id": user_id, "document_context": {"$exists": True}}).sort("created_at", 1)
    contexts: list[str] = []
    async for record in cursor:
        context = record.get("document_context", "")
        if context:
            contexts.append(f"File: {record.get('file_name', 'document')}\n{context}")
    return "\n\n--- Stored document ---\n\n".join(contexts)


async def save_history(record: dict[str, Any]) -> None:
    if mongo.enabled:
        await mongo.chat_history.insert_one(record)


@router.post("/multimodal")
async def multimodal_chat(
    prompt: str = Form(default=""),
    session_id: str = Form(default=""),
    files: list[UploadFile] | None = File(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    del db
    session_id = session_id.strip() or str(uuid.uuid4())
    prompt = prompt.strip()
    uploads = files or []
    attachments: list[tuple[bytes, str]] = []
    document_contexts: list[dict[str, str]] = []

    for upload in uploads:
        content_type = upload.content_type or "application/octet-stream"
        if content_type not in ALLOWED_IMAGE_TYPES and content_type != "application/pdf":
            raise HTTPException(status_code=415, detail=f"Unsupported file type: {content_type}")
        data = await upload.read()
        if len(data) > MAX_FILE_SIZE:
            raise HTTPException(status_code=413, detail="File exceeds the 20 MB limit")
        if content_type in ALLOWED_IMAGE_TYPES:
            try:
                Image.open(io.BytesIO(data)).verify()
            except Exception as exc:
                raise HTTPException(status_code=400, detail="Invalid image file") from exc
            attachments.append((data, content_type))
            document_contexts.append({"file_name": upload.filename or "image", "file_type": content_type})
        else:
            try:
                extracted = extract_pdf_text(data)
            except Exception as exc:
                raise HTTPException(status_code=400, detail="Invalid PDF file") from exc
            if extracted:
                document_contexts.append({"file_name": upload.filename or "document.pdf", "file_type": content_type, "text": extracted})
            else:
                attachments.append((data, content_type))
                document_contexts.append({"file_name": upload.filename or "document.pdf", "file_type": content_type})

    stored_context = await load_session_context(session_id, current_user.id)
    extracted_context = ""
    if document_contexts:
        pdf_text = "\n\n".join(item.get("text", "") for item in document_contexts if item.get("text"))
        extraction_prompt = f"{VISION_INSTRUCTIONS}\nExtract all document content as Markdown.\n{pdf_text}" if pdf_text else f"{VISION_INSTRUCTIONS}\nExtract all content from the attached file(s) as Markdown."
        extracted_context = await call_gemini(extraction_prompt, attachments)
        if pdf_text:
            extracted_context = f"{pdf_text}\n\n{extracted_context}" if extracted_context else pdf_text

    now = datetime.now(timezone.utc)
    base_record: dict[str, Any] = {
        "session_id": session_id,
        "user_id": current_user.id,
        "created_at": now,
        "prompt": prompt,
        "files": [{"file_name": item["file_name"], "file_type": item["file_type"]} for item in document_contexts],
    }

    if extracted_context and not prompt:
        await save_history({**base_record, "document_context": extracted_context, "response": ""})
        return {"session_id": session_id, "response": "Document processed and saved to session memory. Ask me anything about it!", "document_context_saved": True}

    context = "\n\n--- Newly uploaded document ---\n\n" + extracted_context if extracted_context else ""
    context += "\n\n--- Previously stored session documents ---\n\n" + stored_context if stored_context else ""
    if not prompt and not context:
        raise HTTPException(status_code=400, detail="Provide a text prompt or upload a document")
    answer = await call_gemini(f"{VISION_INSTRUCTIONS}\n{context}\n\nUser question: {prompt}")
    await save_history({**base_record, "document_context": extracted_context or None, "response": answer})
    return {"session_id": session_id, "response": answer, "document_context_saved": bool(extracted_context)}


@router.post("/export")
def export_document(request: ExportRequest, _: User = Depends(get_current_user)):
    file_format = request.format.strip().lower().lstrip(".")
    generator_entry = GENERATORS.get(file_format)
    if generator_entry is None:
        raise HTTPException(status_code=400, detail="format must be one of: pdf, csv, docx, xlsx")
    generator, media_type, extension = generator_entry
    filename = "".join(character for character in request.filename.strip() if character.isalnum() or character in "-_ ").strip() or "ai_workspace_export"
    output = generator(request.content, request.data)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}{extension}"'},
    )
