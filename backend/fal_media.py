"""Small server-side adapter for fal's queue API.

The adapter deliberately uses requests instead of exposing a fal credential to
the browser. It accepts the numbered environment variables already used by the
deployment and rotates to the next key when a key is rejected.
"""

from __future__ import annotations

import base64
import os
import time
from typing import Any

import requests


def _keys() -> list[str]:
    values = [os.getenv("FAL_KEY", "").strip()]
    values.extend(os.getenv(f"FAL_AI_TOKEN_{index}", "").strip() for index in range(1, 17))
    return list(dict.fromkeys(value for value in values if value))


def configured() -> bool:
    return bool(_keys())


def _run(model: str, payload: dict[str, Any], *, timeout: float = 300.0) -> dict[str, Any]:
    keys = _keys()
    if not keys:
        raise RuntimeError("FAL_KEY is not configured")
    last_error: Exception | None = None
    for key in keys:
        try:
            response = requests.post(
                f"https://queue.fal.run/{model}",
                headers={"Authorization": f"Key {key}", "Content-Type": "application/json", "X-Fal-Store-IO": "0"},
                json=payload,
                timeout=30,
            )
            response.raise_for_status()
            job = response.json()
            request_id = job.get("request_id")
            status_url = job.get("status_url") or f"https://queue.fal.run/{model}/requests/{request_id}/status"
            response_url = job.get("response_url") or f"https://queue.fal.run/{model}/requests/{request_id}"
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                status = requests.get(status_url, headers={"Authorization": f"Key {key}"}, timeout=30).json()
                if status.get("status") == "COMPLETED":
                    result = requests.get(response_url, headers={"Authorization": f"Key {key}"}, timeout=60)
                    result.raise_for_status()
                    return result.json()
                if status.get("status") in {"FAILED", "CANCELLED"}:
                    raise RuntimeError(status.get("error") or f"fal job {status.get('status').lower()}")
                time.sleep(3)
            raise TimeoutError(f"fal {model} generation timed out")
        except Exception as exc:  # try the next configured key
            last_error = exc
    raise RuntimeError(f"fal {model} generation failed: {last_error}") from last_error


def _file_url(value: Any) -> str | None:
    if isinstance(value, dict):
        if isinstance(value.get("url"), str):
            return value["url"]
        for item in value.values():
            found = _file_url(item)
            if found:
                return found
    if isinstance(value, list):
        for item in value:
            found = _file_url(item)
            if found:
                return found
    return value if isinstance(value, str) and value.startswith(("http://", "https://")) else None


def generate_image(prompt: str) -> bytes:
    result = _run("fal-ai/flux/schnell", {"prompt": prompt, "num_images": 1})
    image_url = _file_url(result.get("images")) or _file_url(result)
    if not image_url:
        raise RuntimeError("fal returned no image URL")
    response = requests.get(image_url, timeout=60)
    response.raise_for_status()
    return response.content


def generate_video(prompt: str, *, image_data: bytes | None = None) -> dict[str, Any]:
    model = "minimax/h3-max-turbo/image-to-video" if image_data is not None else "minimax/h3-max-turbo/text-to-video"
    payload: dict[str, Any] = {"prompt": prompt or "Animate this scene with natural motion"}
    if image_data is not None:
        payload["image_url"] = "data:image/png;base64," + base64.b64encode(image_data).decode("ascii")
    result = _run(model, payload)
    video_url = _file_url(result.get("video")) or _file_url(result)
    if not video_url:
        raise RuntimeError("fal returned no video URL")
    return {"status": "completed", "source": "fal", "output": video_url, "message": "Video generated successfully."}

