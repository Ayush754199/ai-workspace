"""Vision/OCR adapter over the already-configured Gemini pipeline.

``app.services.vision`` was referenced by ``documents.ocr_image`` but never existed,
so any image-only page raised ``ModuleNotFoundError``. Callers swallowed it and the
document silently looked "empty" even though nothing had been read.

This module is deliberately thin:

* it reuses the existing ``google-genai`` dependency and the existing
  provider-credential rotation (``backend.key_rotator``) -- no new SDK, no new key
  handling;
* it raises :class:`OcrUnavailable` when no vision provider is configured or the
  provider fails, so the caller can answer with a controlled 503/flag instead of
  inventing text;
* it never logs image bytes, prompts or provider responses.

A local OCR engine (``pytesseract``) remains the preferred path in
``documents.extract_pdf_ocr``; this module is the fallback and the image-file path.
"""

from __future__ import annotations

from typing import Any

OCR_PROMPT = (
    "Transcribe every character of text visible in this image, preserving reading "
    "order and line breaks. Do not describe the image, do not summarise, and do not "
    "add any commentary. If the image contains no legible text, reply with exactly "
    "NO_TEXT."
)
NO_TEXT_MARKER = "NO_TEXT"


class OcrUnavailable(RuntimeError):
    """Raised when OCR cannot run here; the message is safe to show to a user."""


def _credentials() -> tuple[Any, ...]:
    try:
        from backend.key_rotator import get_provider_credentials
    except ModuleNotFoundError:  # pragma: no cover - running from the repo root
        from key_rotator import get_provider_credentials

    try:
        return tuple(get_provider_credentials("Gemini"))
    except Exception:  # noqa: BLE001 - credential parsing must not crash OCR
        return ()


def is_configured() -> bool:
    """True when a vision-capable provider key is present."""
    return bool(_credentials())


def _image_mime(data: bytes) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


def ocr(data: bytes, *, model: str | None = None) -> str:
    """Transcribe text from an image. Raises :class:`OcrUnavailable` when it cannot."""
    if not data:
        raise OcrUnavailable("The image is empty")
    credentials = _credentials()
    if not credentials:
        raise OcrUnavailable(
            "No vision provider is configured on this server. Add GEMINI_KEY_1 "
            "(or GEMINI_API_KEY) to enable OCR of scanned pages and images."
        )

    from app.core.config import settings

    try:
        from google import genai
        from google.genai import types
    except ModuleNotFoundError as exc:  # pragma: no cover - dependency is pinned
        raise OcrUnavailable("The vision client library is not installed on this server") from exc

    try:
        from backend.key_rotator import call_gemini_model
    except ModuleNotFoundError:  # pragma: no cover
        from key_rotator import call_gemini_model

    selected_model = (model or settings.GEMINI_MODEL).strip()
    mime_type = _image_mime(data)

    def generate(key: str, requested_model: str) -> Any:
        client = genai.Client(api_key=key)
        try:
            return client.models.generate_content(
                model=requested_model,
                contents=[
                    OCR_PROMPT,
                    types.Part.from_bytes(data=data, mime_type=mime_type),
                ],
            )
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()

    try:
        result = call_gemini_model(
            generate,
            endpoint="Gemini/ocr",
            payload={"bytes": len(data), "mime_type": mime_type},
            credentials=credentials,
            text_of=lambda response: getattr(response, "text", None),
        )
    except Exception as exc:  # noqa: BLE001 - normalise to a controlled message
        raise OcrUnavailable("The configured vision provider could not read this image") from exc

    text = (getattr(result, "text", "") or "").strip()
    if not text or text.upper().startswith(NO_TEXT_MARKER):
        return ""
    return text
