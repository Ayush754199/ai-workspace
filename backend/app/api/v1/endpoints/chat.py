"""Authenticated multimodal OCR and session document memory."""

import asyncio
import io
import base64
import uuid
import json
import os
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from PIL import Image
from pypdf import PdfReader
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.deps import require_legacy_ai_enabled
from app.core.database import get_db
from app.core.config import settings
from app.services.ai_gateway.quota import QuotaService, QuotaError
from app.db.session import mongo
from app.models.user import User, UserFeaturePermission
from app.services.document_generator import GENERATORS
import requests

try:
    from backend.key_rotator import (
        ProviderCredential,
        call_gemini_model,
        call_with_key_rotation,
        gemini_model_chain,
        get_provider_credentials,
        is_retired_model_error,
    )
    from backend.logger import log_connection_failure
except ModuleNotFoundError:
    from key_rotator import (
        ProviderCredential,
        call_gemini_model,
        call_with_key_rotation,
        gemini_model_chain,
        get_provider_credentials,
        is_retired_model_error,
    )
    from logger import log_connection_failure

router = APIRouter(prefix="/chat", tags=["chat"])
# Legacy direct-provider chat paths (/multimodal, /stream, /image) are gated. The
# document export and image endpoints keep working below; only provider calls are
# disabled when LEGACY_DIRECT_AI_ENABLED is false.
legacy_gate = [Depends(require_legacy_ai_enabled)]


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
    credentials = get_provider_credentials("Gemini")
    if not credentials and settings.GEMINI_API_KEY != "default-test-key":
        credentials = (ProviderCredential(settings.GEMINI_API_KEY, "GEMINI_API_KEY"),)
    if not credentials:
        return "Gemini is not configured. Add GEMINI_API_KEY to enable document OCR."

    from google import genai
    from google.genai import types

    contents: list[Any] = [prompt]
    for data, mime_type in attachments or []:
        contents.append(types.Part.from_bytes(data=data, mime_type=mime_type))

    def generate(api_key: str, model: str) -> Any:
        client = genai.Client(api_key=api_key)
        try:
            return client.models.generate_content(model=model, contents=contents)
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()

    response, _model = await asyncio.to_thread(
        call_gemini_model,
        generate,
        endpoint="Gemini/chat",
        payload={"prompt": prompt, "attachment_count": len(attachments or [])},
        credentials=credentials,
        text_of=lambda result: getattr(result, "text", None),
    )
    return response.text or ""


def generate_gemini_image(prompt: str, credentials: tuple[ProviderCredential, ...]) -> bytes:
    """Generate an image through Gemini's native image output API."""
    from google import genai
    from google.genai import types

    def generate(api_key: str) -> bytes:
        client = genai.Client(api_key=api_key)
        try:
            response = client.models.generate_content(
                model=settings.GEMINI_IMAGE_MODEL,
                contents=[prompt],
                config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
            )
            for part in getattr(response, "parts", []) or []:
                inline_data = getattr(part, "inline_data", None)
                data = getattr(inline_data, "data", None) if inline_data else None
                if data:
                    return bytes(data)
            raise RuntimeError("Gemini returned no image data")
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()

    return call_with_key_rotation(
        "Gemini",
        generate,
        endpoint="Gemini/image-generation",
        payload={"model": settings.GEMINI_IMAGE_MODEL, "prompt": prompt},
        credentials=credentials,
    )


def generate_fal_image(prompt: str) -> bytes:
    from backend.fal_media import generate_image
    return generate_image(prompt)


async def load_session_context(session_id: str, user_id: int) -> str:
    if not mongo.enabled:
        return ""
    await mongo.verify_ready()
    try:
        cursor = mongo.chat_history.find({"session_id": session_id, "user_id": user_id, "document_context": {"$exists": True}}).sort("created_at", 1)
        contexts: list[str] = []
        async for record in cursor:
            context = record.get("document_context", "")
            if context:
                contexts.append(f"File: {record.get('file_name', 'document')}\n{context}")
        return "\n\n--- Stored document ---\n\n".join(contexts)
    except Exception as exc:
        log_connection_failure(
            exc,
            endpoint="mongodb://Atlas",
            payload={"collection": "chat_history", "operation": "find", "session_id": session_id, "user_id": user_id},
        )
        raise


async def save_history(record: dict[str, Any]) -> None:
    if mongo.enabled:
        await mongo.verify_ready()
        try:
            await mongo.chat_history.insert_one(record)
        except Exception as exc:
            log_connection_failure(
                exc,
                endpoint="mongodb://Atlas",
                payload={"collection": "chat_history", "operation": "insert_one", **record},
            )
            raise


@router.post("/multimodal", dependencies=legacy_gate)
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


@router.post("/image")
async def image_studio(
    prompt: str = Form(...),
    image: UploadFile | None = File(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Generate an image or use the explicitly configured image-edit Space.

    The endpoint never claims edit support when no compatible Space is configured.
    Provider credentials and endpoint errors remain server-side.
    """
    prompt = prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="A prompt is required")
    request_id = uuid.uuid4().hex
    reservation = None
    try:
        reservation = QuotaService().reserve(
            db, user_id=current_user.id, request_id=request_id,
            provider="cloudflare" if settings.CLOUDFLARE_API_TOKEN else "huggingface",
            model=settings.CLOUDFLARE_IMAGE_PRIMARY_MODEL if settings.CLOUDFLARE_API_TOKEN else settings.HF_IMAGE_API_URL,
            capability="image", units=1,
        )
    except QuotaError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    uploaded_data: bytes | None = None
    if image is not None:
        uploaded_data = await image.read()
        if len(uploaded_data) > MAX_FILE_SIZE:
            QuotaService().release(db, reservation.reservation_id, failure=True)
            raise HTTPException(status_code=413, detail="Image exceeds the 20 MB limit")
        try:
            Image.open(io.BytesIO(uploaded_data)).verify()
        except Exception as exc:
            QuotaService().release(db, reservation.reservation_id, failure=True)
            raise HTTPException(status_code=400, detail="Invalid image file") from exc

    permission = (
        db.query(UserFeaturePermission)
        .filter(
            UserFeaturePermission.user_id == current_user.id,
            UserFeaturePermission.feature == "image_generation",
        )
        .first()
    )
    if not permission or not permission.enabled:
        QuotaService().release(db, reservation.reservation_id, failure=True)
        raise HTTPException(status_code=403, detail="image_generation is disabled for this user")

    credentials = get_provider_credentials("Hugging Face")
    gemini_credentials = get_provider_credentials("Gemini")
    if not gemini_credentials and settings.GEMINI_API_KEY != "default-test-key":
        gemini_credentials = (ProviderCredential(settings.GEMINI_API_KEY, "GEMINI_API_KEY"),)
    image_endpoint = settings.HF_IMAGE_API_URL.strip()
    # Cloudflare's current AI endpoint accepts the account token provisioned for
    # this deployment, including the `cfat_` form shown by its token UI.
    cloudflare_ready = bool(
        settings.CLOUDFLARE_ACCOUNT_ID.strip()
        and settings.CLOUDFLARE_API_TOKEN.strip()
    )
    try:
        from backend.fal_media import configured as fal_configured
        fal_ready = fal_configured()
    except Exception:
        fal_ready = False
    gemini_ready = bool(gemini_credentials)
    if not credentials and not cloudflare_ready and not gemini_ready and not fal_ready:
        QuotaService().release(db, reservation.reservation_id, failure=True)
        raise HTTPException(status_code=503, detail="Image generation is not configured. Set GEMINI_API_KEY (or GEMINI_KEY_1) on the server.")
    if not image_endpoint and not cloudflare_ready and not gemini_ready and not fal_ready:
        QuotaService().release(db, reservation.reservation_id, failure=True)
        raise HTTPException(status_code=503, detail="Image generation is not configured. Set HF_IMAGE_API_URL or Gemini credentials on the server.")
    if image is not None:
        edit_space = settings.HF_IMAGE_EDIT_SPACE.strip()
        if not edit_space:
            QuotaService().release(db, reservation.reservation_id, failure=True)
            raise HTTPException(status_code=503, detail="Image editing is unavailable until an eligible editing Space is configured. Set HF_IMAGE_EDIT_SPACE on the server.")
        data = uploaded_data or b""
        try:
            from gradio_client import Client, handle_file
            from tempfile import NamedTemporaryFile
            with NamedTemporaryFile(delete=False, suffix=os.path.splitext(image.filename or ".png")[1]) as handle:
                handle.write(data)
                temp_path = handle.name
            try:
                def edit(token: str):
                    client = Client(edit_space, token=token)
                    try:
                        inputs = json.loads(settings.HF_IMAGE_EDIT_INPUTS_JSON)
                        values = [handle_file(temp_path) if item == "{image}" else prompt if item == "{prompt}" else item for item in inputs]
                        if settings.HF_IMAGE_EDIT_API_NAME:
                            return client.predict(*values, api_name=settings.HF_IMAGE_EDIT_API_NAME)
                        return client.predict(*values)
                    finally:
                        close = getattr(client, "close", None)
                        if callable(close):
                            close()
                result = call_with_key_rotation("Hugging Face", edit, endpoint=f"https://huggingface.co/spaces/{edit_space}", payload={"operation": "image_edit"}, credentials=credentials)
            finally:
                os.unlink(temp_path)
            output = _gradio_result_bytes(result)
        except HTTPException:
            QuotaService().release(db, reservation.reservation_id, failure=True)
            raise
        except Exception as exc:
            QuotaService().release(db, reservation.reservation_id, failure=True)
            raise HTTPException(status_code=502, detail="Configured image editing Space failed") from exc
    else:
        def generate(token: str):
            if cloudflare_ready:
                endpoint = f"https://api.cloudflare.com/client/v4/accounts/{settings.CLOUDFLARE_ACCOUNT_ID}/ai/run/{settings.CLOUDFLARE_IMAGE_PRIMARY_MODEL}"
                response = requests.post(endpoint, headers={"Authorization": f"Bearer {settings.CLOUDFLARE_API_TOKEN}"}, json={"prompt": prompt}, timeout=180)
                response.raise_for_status()
                payload = response.json()
                encoded = ((payload.get("result") or {}).get("image"))
                if not encoded:
                    raise RuntimeError("Cloudflare returned no image data")
                return base64.b64decode(encoded)
            response = requests.post(image_endpoint, headers={"Authorization": f"Bearer {token}"}, json={"inputs": prompt}, timeout=180)
            response.raise_for_status()
            if "application/json" in response.headers.get("content-type", ""):
                raise RuntimeError("Image provider returned a non-image response")
            return response.content
        try:
            provider_name = "cloudflare" if cloudflare_ready else "huggingface"
            try:
                if cloudflare_ready:
                    output = generate("")
                elif credentials and image_endpoint:
                    output = call_with_key_rotation("Hugging Face", generate, endpoint=image_endpoint, payload={"operation": "image_generation"}, credentials=credentials)
                else:
                    raise RuntimeError("Hugging Face image generation is not configured")
            except Exception:
                # The Cloudflare credential may be an R2 token rather than a
                # Workers AI token. Try HF, then Gemini as the configured image fallback.
                if credentials and image_endpoint:
                    def hf_fallback(token: str) -> bytes:
                        response = requests.post(image_endpoint, headers={"Authorization": f"Bearer {token}"}, json={"inputs": prompt}, timeout=180)
                        response.raise_for_status()
                        if "application/json" in response.headers.get("content-type", ""):
                            raise RuntimeError("Hugging Face returned a non-image response")
                        return response.content
                    try:
                        output = call_with_key_rotation("Hugging Face", hf_fallback, endpoint=image_endpoint, payload={"operation": "image_generation_fallback"}, credentials=credentials)
                        provider_name = "huggingface-fallback"
                    except Exception:
                        if gemini_ready:
                            try:
                                output = generate_gemini_image(prompt, gemini_credentials)
                                provider_name = "gemini-fallback"
                            except Exception:
                                if not fal_ready:
                                    raise
                                output = generate_fal_image(prompt)
                                provider_name = "fal-fallback"
                        elif fal_ready:
                            output = generate_fal_image(prompt)
                            provider_name = "fal-fallback"
                        else:
                            raise
                elif gemini_ready:
                    try:
                        output = generate_gemini_image(prompt, gemini_credentials)
                        provider_name = "gemini"
                    except Exception:
                        if not fal_ready:
                            raise
                        output = generate_fal_image(prompt)
                        provider_name = "fal-fallback"
                elif fal_ready:
                    output = generate_fal_image(prompt)
                    provider_name = "fal"
                else:
                    raise
            QuotaService().commit(db, reservation.reservation_id, detail={"operation": "image_generation", "provider": provider_name})
        except Exception as exc:
            QuotaService().release(db, reservation.reservation_id, failure=True)
            raise HTTPException(status_code=502, detail="Configured image generation provider failed") from exc
    if image is not None:
        QuotaService().commit(db, reservation.reservation_id, detail={"operation": "image_editing", "provider": "huggingface"})
    if not output:
        QuotaService().release(db, reservation.reservation_id, failure=True)
        raise HTTPException(status_code=502, detail="Image provider returned an empty result")
    return StreamingResponse(io.BytesIO(output), media_type="image/png")


def _gradio_result_bytes(result: Any) -> bytes:
    if isinstance(result, (list, tuple)):
        for item in result:
            try:
                return _gradio_result_bytes(item)
            except (TypeError, ValueError):
                continue
        raise ValueError("No image result returned")
    if isinstance(result, dict):
        for key in ("image", "path", "url", "data"):
            if key in result:
                return _gradio_result_bytes(result[key])
        raise ValueError("Unrecognized image result")
    if isinstance(result, (bytes, bytearray)):
        return bytes(result)
    path = getattr(result, "path", None) or getattr(result, "url", None) or result
    if isinstance(path, str) and path.startswith(("http://", "https://")):
        response = requests.get(path, timeout=60)
        response.raise_for_status()
        return response.content
    if isinstance(path, str) and os.path.isfile(path):
        with open(path, "rb") as handle:
            return handle.read()
    raise ValueError("Image provider returned an unsupported result")


import json


class ChatStreamRequest(BaseModel):
    prompt: str
    session_id: str = ""


@router.post("/stream", dependencies=legacy_gate)
def stream_chat(request: ChatStreamRequest, current_user: User = Depends(get_current_user)):
    credentials = get_provider_credentials("Gemini")
    if not credentials and settings.GEMINI_API_KEY != "default-test-key":
        credentials = (ProviderCredential(settings.GEMINI_API_KEY, "GEMINI_API_KEY"),)
    if not credentials:
        raise HTTPException(status_code=503, detail="Gemini is not configured.")

    def event_stream():
        from google import genai

        error: Exception | None = None
        for model in gemini_model_chain():
            client = genai.Client(api_key=credentials[0].value)
            streamed = False
            try:
                for chunk in client.models.generate_content_stream(
                    model=model,
                    contents=request.prompt,
                ):
                    streamed = True
                    text = getattr(chunk, "text", None)
                    if text:
                        yield f"data: {json.dumps({'chunk': text})}\n\n"
                error = None
                break
            except Exception as exc:
                error = exc
                # Only a retired model is worth retrying; anything else is reported.
                if streamed or not is_retired_model_error(exc):
                    break
            finally:
                close = getattr(client, "close", None)
                if callable(close):
                    close()
        if error is not None:
            yield f"data: {json.dumps({'error': str(error)})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


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
