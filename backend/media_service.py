"""Cloudinary and Hugging Face media pipeline helpers for NEXUS AI."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

from backend.key_rotator import ProviderRateLimitError, call_with_key_rotation, get_provider_credentials
from backend.logger import log_connection_event, log_connection_failure

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
load_dotenv(Path(__file__).resolve().with_name(".env"), override=False)

CLOUDINARY_CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME", "").strip()
CLOUDINARY_API_KEY = os.getenv("CLOUDINARY_API_KEY", "").strip()
CLOUDINARY_API_SECRET = os.getenv("CLOUDINARY_API_SECRET", "").strip()
HF_TEXT_TO_VIDEO_SPACE = os.getenv("HF_TEXT_TO_VIDEO_SPACE", "Wan-AI/Wan2.1").strip()
HF_TOKEN = os.getenv("HF_TOKEN", "").strip() or os.getenv("HF_TOKEN_1", "").strip()
HF_VIDEO_API_NAME = os.getenv("HF_TEXT_TO_VIDEO_API_NAME", "").strip() or None
LTX_API_KEY = (
    os.getenv("LTX_API_KEY", "").strip()
    or os.getenv("LTX_API_KEY_1", "").strip()
    or os.getenv("LTX_TOKEN_1", "").strip()
)
LTX_API_BASE_URL = os.getenv("LTX_API_BASE_URL", "https://api.ltx.io").strip().rstrip("/")
if LTX_API_BASE_URL.endswith("/v1"):
    LTX_API_BASE_URL = LTX_API_BASE_URL[:-3]


def _ltx_video(prompt: str, *, image_data: bytes | None = None, timeout: float = 180.0) -> dict[str, Any]:
    """Use the official LTX async API when configured, with bounded polling."""
    if not LTX_API_KEY:
        return {"status": "disabled", "message": "LTX_API_KEY is not configured."}
    endpoint = f"{LTX_API_BASE_URL}/v2/{'image-to-video' if image_data else 'text-to-video'}"
    payload: dict[str, Any] = {
        "prompt": prompt.strip(), "model": "ltx-2-3-fast", "duration": 6,
        "resolution": "1280x720", "generate_audio": False,
    }
    if image_data is not None:
        import base64
        payload["image_uri"] = "data:image/png;base64," + base64.b64encode(image_data).decode("ascii")
    headers = {"Authorization": f"Bearer {LTX_API_KEY}", "Content-Type": "application/json"}
    response = requests.post(endpoint, headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    job = response.json()
    job_id = job.get("id")
    if not job_id:
        raise RuntimeError("LTX did not return a job id")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(5)
        status_response = requests.get(f"{endpoint}/{job_id}", headers={"Authorization": f"Bearer {LTX_API_KEY}"}, timeout=30)
        status_response.raise_for_status()
        state = status_response.json()
        if state.get("status") == "completed":
            video_url = ((state.get("result") or {}).get("video_url"))
            if not video_url:
                raise RuntimeError("LTX completed without a video URL")
            return {"status": "completed", "source": "ltx", "output": video_url, "message": "Video generated successfully."}
        if state.get("status") in {"failed", "cancelled"}:
            raise RuntimeError((state.get("error") or "LTX video job failed"))
    raise RuntimeError("LTX video generation timed out")


def configure_cloudinary() -> dict[str, str]:
    """Configure Cloudinary only when credentials are present."""
    try:
        import cloudinary
        from cloudinary.uploader import upload
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("cloudinary is not installed") from exc

    cloud_name = CLOUDINARY_CLOUD_NAME or os.getenv("CLOUDINARY_CLOUD_NAME", "")
    api_key = CLOUDINARY_API_KEY or os.getenv("CLOUDINARY_API_KEY", "")
    api_secret = CLOUDINARY_API_SECRET or os.getenv("CLOUDINARY_API_SECRET", "")
    if not cloud_name or not api_key or not api_secret:
        raise ValueError("Cloudinary credentials are missing: CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, and CLOUDINARY_API_SECRET are required.")

    cloudinary.config(
        cloud_name=cloud_name,
        api_key=api_key,
        api_secret=api_secret,
        secure=True,
    )
    return {"cloud_name": cloud_name, "api_key": api_key, "api_secret": api_secret}


def upload_to_cloudinary(file_path: str | Path, *, folder: str = "nexus-ai", public_id: str | None = None) -> dict[str, Any]:
    """Upload a file to Cloudinary and return the normalized metadata."""
    if not file_path:
        raise ValueError("A file path is required for upload.")
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Media file not found: {path}")

    configure_cloudinary()
    import cloudinary

    options: dict[str, Any] = {"folder": folder}
    if public_id:
        options["public_id"] = public_id
    result = cloudinary.uploader.upload(str(path), **options)
    log_connection_event("cloudinary_upload", endpoint="Cloudinary/upload", payload={"folder": folder, "public_id": public_id or result.get("public_id")})
    return result


def _read_hf_token() -> str:
    credentials = get_provider_credentials("Hugging Face")
    if not credentials:
        raise RuntimeError("No Hugging Face credentials configured. Add HF_TOKEN_1..HF_TOKEN_n or HF_TOKENS.")
    return credentials[0].value


def generate_wan_video(prompt: str, *, space: str | None = None, api_name: str | None = None, timeout: float = 180.0) -> dict[str, Any]:
    """Generate a video using the configured Hugging Face Wan 2.1 space."""
    if not prompt or not prompt.strip():
        raise ValueError("A non-empty prompt is required for text-to-video generation.")

    if LTX_API_KEY:
        try:
            return _ltx_video(prompt, timeout=timeout)
        except Exception as ltx_exc:
            log_connection_failure(ltx_exc, endpoint=f"{LTX_API_BASE_URL}/v2/text-to-video", payload={"operation": "primary"}, function="generate_ltx_video")
    target_space = (space or os.getenv("HF_TEXT_TO_VIDEO_SPACE") or HF_TEXT_TO_VIDEO_SPACE or "Wan-AI/Wan2.1").strip()
    if not target_space:
        raise RuntimeError("HF_TEXT_TO_VIDEO_SPACE is not configured.")

    token = os.getenv("HF_TOKEN", "").strip() or os.getenv("HF_TOKEN_1", "").strip() or _read_hf_token()
    if not token:
        return _ltx_video(prompt, timeout=timeout)

    from gradio_client import Client

    def operation(current_token: str) -> Any:
        client = Client(target_space, token=current_token)
        try:
            if api_name:
                return client.predict(prompt, api_name=api_name)
            return client.predict(prompt)
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()

    try:
        result = call_with_key_rotation(
            "Hugging Face",
            operation,
            endpoint=f"https://huggingface.co/spaces/{target_space}",
            payload={"prompt": prompt, "api_name": api_name},
            credentials=get_provider_credentials("Hugging Face") or (type("_Cred", (), {"value": token, "key_id": "HF_TOKEN"})(),),
        )
        media = result
        if isinstance(media, (str, bytes, bytearray, Path)):
            return {"status": "completed", "source": "huggingface", "output": media, "message": "Video generated successfully."}
        return {"status": "completed", "source": "huggingface", "output": media, "message": "Video generation completed."}
    except ProviderRateLimitError as exc:
        log_connection_failure(
            exc,
            endpoint=f"https://huggingface.co/spaces/{target_space}",
            payload={"prompt": prompt, "api_name": api_name},
            function="generate_wan_video",
        )
        return {"status": "rate_limited", "message": f"Hugging Face is busy. Retry later. {exc}"}
    except Exception as exc:
        message = str(exc).lower()
        if "503" in message or "warmup" in message or "loading" in message:
            time.sleep(5)
            return {"status": "warming_up", "message": "The Wan 2.1 video model is warming up. Please retry in a moment."}
        log_connection_failure(
            exc,
            endpoint=f"https://huggingface.co/spaces/{target_space}",
            payload={"prompt": prompt, "api_name": api_name},
            function="generate_wan_video",
        )
        try:
            return _ltx_video(prompt, timeout=timeout)
        except Exception as ltx_exc:
            log_connection_failure(ltx_exc, endpoint=f"{LTX_API_BASE_URL}/v2/text-to-video", payload={"operation": "fallback"}, function="generate_ltx_video")
            return {"status": "error", "message": f"Video generation failed: {exc}"}


__all__ = [
    "configure_cloudinary",
    "upload_to_cloudinary",
    "generate_wan_video",
]
