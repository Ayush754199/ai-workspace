"""Server-side Pollinations image and speech adapter.

The secret key is read only from the process environment and is never exposed
to the browser or included in generated URLs.
"""

from __future__ import annotations

from urllib.parse import quote

import requests

from app.core.config import settings


BASE_URL = "https://gen.pollinations.ai"


def configured() -> bool:
    return bool(settings.POLLINATIONS_API_KEY.strip())


def _headers() -> dict[str, str]:
    key = settings.POLLINATIONS_API_KEY.strip()
    if not key:
        raise RuntimeError("POLLINATIONS_API_KEY is not configured")
    return {"Authorization": f"Bearer {key}"}


def generate_image(prompt: str) -> bytes:
    response = requests.get(
        f"{BASE_URL}/image/{quote(prompt, safe='')}",
        headers=_headers(),
        params={"model": settings.POLLINATIONS_IMAGE_MODEL},
        timeout=180,
    )
    response.raise_for_status()
    if not response.content:
        raise RuntimeError("Pollinations returned an empty image")
    return response.content


def edit_image(prompt: str, image_data: bytes, filename: str = "image.png") -> bytes:
    response = requests.post(
        f"{BASE_URL}/v1/images/edits",
        headers=_headers(),
        files={"image": (filename, image_data, "application/octet-stream")},
        data={"prompt": prompt, "model": settings.POLLINATIONS_IMAGE_MODEL},
        timeout=180,
    )
    response.raise_for_status()
    payload = response.json()
    item = (payload.get("data") or [{}])[0]
    encoded = item.get("b64_json") if isinstance(item, dict) else None
    if encoded:
        import base64

        return base64.b64decode(encoded)
    url = item.get("url") if isinstance(item, dict) else None
    if url:
        media = requests.get(url, timeout=60)
        media.raise_for_status()
        return media.content
    raise RuntimeError("Pollinations returned no edited image")


def speak(text: str, voice: str = "nova") -> bytes:
    response = requests.post(
        f"{BASE_URL}/v1/audio/speech",
        headers={**_headers(), "Content-Type": "application/json"},
        json={"model": settings.POLLINATIONS_AUDIO_MODEL, "input": text, "voice": voice},
        timeout=180,
    )
    response.raise_for_status()
    if not response.content:
        raise RuntimeError("Pollinations returned empty audio")
    return response.content
