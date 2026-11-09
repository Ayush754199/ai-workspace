"""Office tools: AI-generated Excel / Word / PowerPoint / CSV / PDF.

The AI writes content; the server renders real files with python-docx,
python-pptx, openpyxl and reportlab. If the model is unavailable the endpoint
returns 503 instead of a placeholder document.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.user import User
from app.services.feature_flags import require_feature
from app.services import documents as docs
from app.services.ai_gateway import GatewayUnavailableError
from app.services.chat_service import ChatConcurrencyError, generate_reply

router = APIRouter(prefix="/office", tags=["office"], dependencies=[Depends(require_feature("office"))])

FORMATS = {
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"),
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"),
    "pptx": ("application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx"),
    "csv": ("text/csv", ".csv"),
    "pdf": ("application/pdf", ".pdf"),
}

PROMPTS = {
    "docx": (
        "Write a complete Word document in Markdown. Use # headings, short paragraphs and bullet lists. "
        "Do not add a table of contents or meta commentary."
    ),
    "pdf": (
        "Write a complete report in Markdown. Use headings and short paragraphs. "
        "No conversation text, only the document itself."
    ),
    "pptx": (
        "Write a slide deck in Markdown. Each '## Heading' becomes a slide title; "
        "use one bullet per line under it. 6-10 slides."
    ),
    "xlsx": (
        "Return ONLY a pipe table in Markdown with a header row and 8-20 data rows. No prose before or after."
    ),
    "csv": (
        "Return ONLY a comma-separated table with a header row and 8-20 data rows. No prose before or after."
    ),
}


class OfficeRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=4000)
    format: str = Field(default="docx", pattern="^(xlsx|docx|pptx|csv|pdf)$")
    title: str = Field(default="", max_length=160)
    filename: str = Field(default="document", max_length=120)


def _response(payload: bytes, kind: str, filename: str) -> Response:
    media_type, extension = FORMATS[kind]
    safe = "".join(ch for ch in (filename or "document") if ch.isalnum() or ch in "-_ ").strip() or "document"
    return Response(
        content=payload,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{safe}{extension}"'},
    )


@router.post("/generate")
async def generate(
    request: OfficeRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    prompt = f"{PROMPTS[request.format]}\n\nUser request: {request.prompt}"
    try:
        content, provider, _model = await generate_reply(
            db,
            user_id=user.id,
            prompt=prompt,
            system_prompt="You are a professional document author. Follow the required output format exactly.",
        )
    except (GatewayUnavailableError, ChatConcurrencyError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=f"AI authoring is unavailable: {exc}") from exc

    title = request.title.strip() or request.prompt.strip()[:80]
    try:
        if request.format == "docx":
            payload = docs.generate_docx(title, content)
        elif request.format == "pptx":
            payload = docs.generate_pptx(title, content)
        elif request.format == "pdf":
            payload = docs.generate_pdf(title, content)
        elif request.format == "xlsx":
            rows = docs.table_from_text(content)
            if not rows:
                raise ValueError("The model did not return a usable table")
            payload = docs.generate_xlsx(rows, sheet_title=title[:31] or "Data", title=title)
        else:
            rows = docs.table_from_text(content)
            if not rows:
                raise ValueError("The model did not return a usable table")
            payload = docs.generate_csv(rows)
    except ImportError as exc:
        raise HTTPException(status_code=501, detail=f"Generator library not installed: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    response = _response(payload, request.format, request.filename or title)
    response.headers["X-AI-Provider"] = provider
    return response


@router.get("/formats")
def formats(_: User = Depends(get_current_user)):
    return {"formats": list(FORMATS.keys())}
