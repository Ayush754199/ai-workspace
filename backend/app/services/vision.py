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

import io
import time
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


def describe(data: bytes, prompt: str = "", *, model: str | None = None) -> str:
    """Ask the vision provider about an image (free-form prompt, not a transcript)."""
    return _generate(
        data, model=model, prompt=prompt or OCR_PROMPT, label="Gemini/ocr-describe"
    )


def ocr(data: bytes, *, model: str | None = None) -> str:
    """Transcribe text from an image. Raises :class:`OcrUnavailable` when it cannot."""
    text = _generate(data, model=model, prompt=OCR_PROMPT, label="Gemini/ocr")
    if not text or text.upper().startswith(NO_TEXT_MARKER):
        return ""
    return text


# Monotonic seconds after which the model chain is not attempted again because every
# model answered 429 for the day. Google's free tier grants quota per (project, model)
# per day, so hammering the chain cannot succeed and the chat file audit used to burn
# minutes per uploaded image on it. Cleared on the first success.
_CHAIN_EXHAUSTED_UNTIL = 0.0
_CHAIN_EXHAUSTION_BACKOFF_SECONDS = 300.0


def _chain_exhausted() -> bool:
    return bool(_CHAIN_EXHAUSTED_UNTIL) and time.monotonic() < _CHAIN_EXHAUSTED_UNTIL


def _generate(data: bytes, *, model: str | None, prompt: str, label: str) -> str:
    global _CHAIN_EXHAUSTED_UNTIL
    if not data:
        raise OcrUnavailable("The image is empty")
    if _has_no_text_layer(data):
        # A blank page (or a shape-only poster) costs nothing to answer: sending it to
        # the provider spent a request and, with the free tier exhausted, a full chain
        # of retries per file.
        return ""
    credentials = _credentials()
    if not credentials:
        raise OcrUnavailable(
            "No vision provider is configured on this server. Add GEMINI_KEY_1 "
            "(or GEMINI_API_KEY) to enable OCR of scanned pages and images."
        )
    if _chain_exhausted():
        raise OcrUnavailable(
            "The vision provider has no quota left right now. Try again later or add another key."
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
                    prompt,
                    types.Part.from_bytes(data=data, mime_type=mime_type),
                ],
            )
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()

    try:
        # The chain tries every model, so it answers ``(result, model)``. Treating the
        # tuple as the response made every successful OCR return "" (OCR looked
        # broken on a healthy provider).
        result, _model = call_gemini_model(
            generate,
            endpoint=label,
            payload={"bytes": len(data), "mime_type": mime_type},
            credentials=credentials,
            text_of=lambda response: getattr(response, "text", None),
        )
    except Exception as exc:  # noqa: BLE001 - normalise to a controlled message
        if _is_daily_quota_error(exc):
            _CHAIN_EXHAUSTED_UNTIL = time.monotonic() + _CHAIN_EXHAUSTION_BACKOFF_SECONDS
        raise OcrUnavailable("The configured vision provider could not read this image") from exc
    _CHAIN_EXHAUSTED_UNTIL = 0.0

    return (getattr(result, "text", "") or "").strip()


def _is_daily_quota_error(exc: BaseException) -> bool:
    """True when the provider said the *daily* free quota for the project is gone."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        text = str(current).lower()
        if "per day" in text or "perdayperproject" in text or "per_day" in text:
            return True
        current = current.__cause__ or current.__context__
    return False


def _has_no_text_layer(data: bytes) -> bool:
    """Cheap, offline pre-check: does this image contain visibly non-background pixels?

    Pure-white/blank scans are common (and were the slowest case, because the provider
    is asked about them and burns quota). PIL is already a dependency; any failure
    returns False so the normal provider path runs unchanged.
    """
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            gray = image.convert("L").resize((96, 96))
            darkest = min(gray.getdata())
            return darkest > 240
    except Exception:  # noqa: BLE001 - never block OCR on a pre-check
        return False
