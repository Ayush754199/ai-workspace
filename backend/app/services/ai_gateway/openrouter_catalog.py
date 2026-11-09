"""OpenRouter model discovery without exposing API keys to clients or logs."""

from __future__ import annotations

import json
from urllib.request import Request, urlopen

from app.core.config import settings
from backend.key_rotator import get_provider_credentials


def _is_free(model: dict) -> bool:
    pricing = model.get("pricing") or {}
    # OpenRouter reports prices as decimal strings. A model is accepted only if
    # every advertised billable dimension is zero; missing prices are unknown.
    values = [pricing.get(key) for key in ("prompt", "completion", "request", "image")]
    return bool(values) and all(value is not None and float(value or 0) == 0 for value in values if value is not None)


def discover_free_models(*, timeout: float = 15.0) -> list[dict]:
    credentials = get_provider_credentials("OpenRouter")
    if not credentials:
        raise RuntimeError("OpenRouter is not configured in the backend .env file")
    request = Request(
        f"{settings.OPENROUTER_BASE_URL.rstrip('/')}/models",
        headers={"Authorization": f"Bearer {credentials[0].value}", "Accept": "application/json"},
        method="GET",
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    discovered: list[dict] = []
    for model in payload.get("data", []):
        if not isinstance(model, dict) or not _is_free(model):
            continue
        architecture = model.get("architecture") or {}
        input_modalities = architecture.get("input_modalities") or []
        output_modalities = architecture.get("output_modalities") or []
        if "text" not in output_modalities:
            continue
        discovered.append({
            "provider": "openrouter",
            "model": model.get("id"),
            "name": model.get("name") or model.get("id"),
            "is_free": True,
            "capabilities": ["chat"] + (["image"] if "image" in input_modalities else []),
            "context_length": model.get("context_length"),
            "pricing": model.get("pricing") or {},
        })
    return [item for item in discovered if item["model"]]
