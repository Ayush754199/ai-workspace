"""Image AI endpoints that are not image *generation* (see /chat/image)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.user import User
from app.services import image_tools
from app.services.ai_gateway import GatewayUnavailableError
from app.services.chat_service import ChatConcurrencyError, generate_reply
from app.services.feature_flags import require_feature
from sqlalchemy.orm import Session

router = APIRouter(prefix="/images", tags=["images"], dependencies=[Depends(require_feature("images"))])

MAX_UPLOAD = 20 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp"}


async def _read_image(file: UploadFile) -> bytes:
    content_type = (file.content_type or "").lower()
    if content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=415, detail="Only PNG, JPG and WEBP images are supported")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="The uploaded image is empty")
    if len(data) > MAX_UPLOAD:
        raise HTTPException(status_code=413, detail="Image exceeds the 20 MB limit")
    try:
        image_tools.open_image(data)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid image file") from exc
    return data


def _png(payload: bytes) -> Response:
    return Response(content=payload, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.post("/describe")
async def describe(file: UploadFile = File(...), _: User = Depends(get_current_user)):
    data = await _read_image(file)
    return image_tools.describe(image_tools.open_image(data))


@router.post("/understand")
async def understand(
    file: UploadFile = File(...),
    question: str = Form(default=""),
    _: User = Depends(get_current_user),
):
    data = await _read_image(file)
    try:
        answer = image_tools.understand(data, question)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"question": question, "answer": answer}


@router.post("/remove-background")
async def remove_background(
    file: UploadFile = File(...),
    tolerance: int = Form(default=28),
    _: User = Depends(get_current_user),
):
    data = await _read_image(file)
    if not 1 <= tolerance <= 120:
        raise HTTPException(status_code=400, detail="tolerance must be between 1 and 120")
    return _png(image_tools.remove_background(data, tolerance=tolerance))


@router.post("/enhance")
async def enhance(file: UploadFile = File(...), _: User = Depends(get_current_user)):
    data = await _read_image(file)
    return Response(content=image_tools.enhance(data), media_type="image/jpeg")


@router.post("/variations")
async def variations(
    file: UploadFile = File(...),
    instruction: str = Form(default=""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Suggest concrete prompts for variations of an uploaded image.

    The suggestions are model output grounded in a visual description of the
    uploaded file; no image is invented when the vision model is unavailable.
    """
    data = await _read_image(file)
    description = image_tools.describe(image_tools.open_image(data))
    base = instruction.strip() or "Create alternative creative directions for this image."
    prompt = (
        f"Image facts: {description}.\n\n{base}\n\n"
        "Return 4 short prompts (one per line, no numbering) that a text-to-image model could use."
    )
    try:
        answer, provider, _model = await generate_reply(
            db, user_id=user.id, prompt=prompt,
            system_prompt="You write precise image-generation prompts. Output only the prompts.",
        )
    except (GatewayUnavailableError, ChatConcurrencyError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    prompts = [line.strip(" -*0123456789.").strip() for line in answer.splitlines() if line.strip()]
    return {"prompts": [p for p in prompts if p][:6], "provider": provider, "description": description}
