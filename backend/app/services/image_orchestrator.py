"""Verified image routing with independent provider budget enforcement.

The provider contracts are deliberately explicit. Muse and Cloudflare image
routes are disabled until an operator verifies both the model capability and the
metering unit used for the configured budget reservation.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass

import requests
from sqlalchemy.orm import Session

from app.core.config import settings
from app.services.provider_budget import ProviderBudgetError, commit, mark_uncertain, release, reserve


class ImageOrchestrationError(RuntimeError):
    def __init__(self, message: str, *, uncertain: bool = False):
        super().__init__(message)
        self.uncertain = uncertain


@dataclass(frozen=True)
class ImageResult:
    data: bytes
    provider: str
    model: str


def _json_image(payload: dict) -> bytes:
    result = payload.get("result") if isinstance(payload, dict) else None
    candidates = [
        (result or {}).get("image") if isinstance(result, dict) else None,
        (result or {}).get("b64_json") if isinstance(result, dict) else None,
        payload.get("image") if isinstance(payload, dict) else None,
        payload.get("b64_json") if isinstance(payload, dict) else None,
    ]
    for value in candidates:
        if isinstance(value, str) and value:
            try:
                return base64.b64decode(value)
            except Exception as exc:
                raise ImageOrchestrationError("Image provider returned invalid base64 data") from exc
    raise ImageOrchestrationError("Image provider returned no image data")


def _muse(prompt: str, image: bytes | None, mime_type: str) -> tuple[bytes, str]:
    if not (settings.MUSE_IMAGE_VERIFIED and settings.MUSE_IMAGE_API_URL and settings.MUSE_API_KEY and settings.MUSE_IMAGE_MODEL):
        raise ImageOrchestrationError("Muse Image is not verified or configured")
    if image is not None and not settings.MUSE_IMAGE_SUPPORTS_EDITING:
        raise ImageOrchestrationError("Muse Image editing capability has not been verified")
    body = {"model": settings.MUSE_IMAGE_MODEL, "prompt": prompt, "response_format": "b64_json"}
    if image is not None:
        body["image"] = f"data:{mime_type};base64,{base64.b64encode(image).decode('ascii')}"
    try:
        response = requests.post(settings.MUSE_IMAGE_API_URL, headers={"Authorization": f"Bearer {settings.MUSE_API_KEY}", "Content-Type": "application/json"}, json=body, timeout=settings.AI_REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        data = response.json()
        return _json_image(data), settings.MUSE_IMAGE_MODEL
    except requests.Timeout as exc:
        raise ImageOrchestrationError("Muse Image request timed out; usage is uncertain", uncertain=True) from exc
    except requests.RequestException as exc:
        raise ImageOrchestrationError("Muse Image request failed") from exc
    except ValueError as exc:
        raise ImageOrchestrationError("Muse Image returned malformed JSON") from exc


def _cloudflare(prompt: str, image: bytes | None) -> tuple[bytes, str]:
    if not (settings.CLOUDFLARE_IMAGE_MODEL_VERIFIED and settings.CLOUDFLARE_ACCOUNT_ID and settings.CLOUDFLARE_API_TOKEN and settings.CLOUDFLARE_IMAGE_PRIMARY_MODEL):
        raise ImageOrchestrationError("Cloudflare image model is not verified or configured")
    if image is not None and not settings.CLOUDFLARE_IMAGE_EDITING_VERIFIED:
        raise ImageOrchestrationError("Cloudflare image editing input schema has not been verified")
    body: dict[str, str] = {"prompt": prompt}
    if image is not None:
        body["image"] = base64.b64encode(image).decode("ascii")
    endpoint = f"https://api.cloudflare.com/client/v4/accounts/{settings.CLOUDFLARE_ACCOUNT_ID}/ai/run/{settings.CLOUDFLARE_IMAGE_PRIMARY_MODEL}"
    try:
        response = requests.post(endpoint, headers={"Authorization": f"Bearer {settings.CLOUDFLARE_API_TOKEN}"}, json=body, timeout=settings.AI_REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        return _json_image(response.json()), settings.CLOUDFLARE_IMAGE_PRIMARY_MODEL
    except requests.Timeout as exc:
        raise ImageOrchestrationError("Cloudflare image request timed out; usage is uncertain", uncertain=True) from exc
    except requests.RequestException as exc:
        raise ImageOrchestrationError("Cloudflare image request failed") from exc
    except ValueError as exc:
        raise ImageOrchestrationError("Cloudflare image response was malformed") from exc


def generate(*, db: Session, request_id: str, prompt: str, image: bytes | None = None, mime_type: str = "image/png") -> ImageResult:
    """Generate/edit via a verified image provider; no text-model fallback."""
    routes: list[tuple[str, int, object]] = []
    if settings.MUSE_IMAGE_VERIFIED and settings.MUSE_IMAGE_REQUEST_COST_USD > 0:
        routes.append(("muse_image", settings.MUSE_IMAGE_REQUEST_COST_USD, lambda: _muse(prompt, image, mime_type)))
    if settings.CLOUDFLARE_IMAGE_MODEL_VERIFIED and settings.CLOUDFLARE_IMAGE_REQUEST_NEURONS > 0:
        routes.append(("cloudflare", settings.CLOUDFLARE_IMAGE_REQUEST_NEURONS, lambda: _cloudflare(prompt, image)))
    if not routes:
        raise ImageOrchestrationError("No verified image provider with configured metering is available")
    last: Exception | None = None
    for provider, units, operation in routes:
        period = "monthly" if provider == "muse_image" else "daily"
        bucket = "image"
        try:
            budget = reserve(db, request_id=f"{request_id}:{provider}", provider=provider, bucket=bucket, period=period, units=units)
        except ProviderBudgetError as exc:
            last = exc
            continue
        try:
            data, model = operation()
            commit(db, budget.id, detail={"operation": "image_edit" if image else "image_generate", "provider": provider})
            return ImageResult(data, provider, model)
        except ImageOrchestrationError as exc:
            if exc.uncertain:
                mark_uncertain(db, budget.id, detail={"failure": str(exc)[:160]})
                raise
            release(db, budget.id, detail={"failure": str(exc)[:160]})
            last = exc
    raise ImageOrchestrationError(str(last or "Image generation failed"))
