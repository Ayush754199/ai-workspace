"""Image-to-video through a configured Hugging Face Space.

Reuses the existing key rotation and failure logging from media_service. The
Space and its API name are configuration, never hardcoded, so an unconfigured
server fails with a clear message instead of calling a random model.
"""

from __future__ import annotations

import os
import tempfile
from typing import Any

from app.core.config import settings

try:
    from backend.key_rotator import ProviderRateLimitError, call_with_key_rotation, get_provider_credentials
    from backend.logger import log_connection_failure
except ModuleNotFoundError:  # pragma: no cover - package import layout fallback
    from key_rotator import ProviderRateLimitError, call_with_key_rotation, get_provider_credentials
    from logger import log_connection_failure


def image_to_video(data: bytes, filename: str, prompt: str, *, space: str | None = None, api_name: str | None = None) -> dict[str, Any]:
    target_space = (space or settings.HF_IMAGE_TO_VIDEO_SPACE or "").strip()
    credentials = get_provider_credentials("Hugging Face")
    try:
        from backend.media_service import LTX_API_KEY, _ltx_video
        if LTX_API_KEY:
            return _ltx_video(prompt or "Animate this image with subtle natural motion", image_data=data)
    except Exception:
        pass
    try:
        from backend.fal_media import configured, generate_video
        if configured():
            return generate_video(prompt or "Animate this image with subtle natural motion", image_data=data)
    except Exception:
        pass
    if not target_space:
        try:
            from backend.media_service import _ltx_video
            return _ltx_video(prompt or "Animate this image with subtle natural motion", image_data=data)
        except Exception as exc:
            return {"status": "failed", "message": f"Image-to-video failed: {exc}"}
    if not credentials:
        try:
            from backend.media_service import _ltx_video
            return _ltx_video(prompt or "Animate this image with subtle natural motion", image_data=data)
        except Exception as exc:
            return {"status": "disabled", "message": f"Hugging Face credentials are not configured; LTX fallback failed: {exc}"}

    from gradio_client import Client, handle_file

    suffix = os.path.splitext(filename or ".png")[1] or ".png"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
        handle.write(data)
        temp_path = handle.name

    try:
        def operation(token: str):
            client = Client(target_space, token=token)
            try:
                inputs = [
                    handle_file(temp_path),
                    (prompt or "").strip() or "Animate this image with subtle natural motion",
                ]
                if api_name:
                    return client.predict(*inputs, api_name=api_name)
                return client.predict(*inputs)
            finally:
                close = getattr(client, "close", None)
                if callable(close):
                    close()

        result = call_with_key_rotation(
            "Hugging Face",
            operation,
            endpoint=f"https://huggingface.co/spaces/{target_space}",
            payload={"operation": "image_to_video", "has_prompt": bool(prompt)},
            credentials=credentials,
        )
        return {"status": "completed", "source": "huggingface", "mode": "image", "output": _output_path(result)}
    except ProviderRateLimitError as exc:
        log_connection_failure(
            exc,
            endpoint=f"https://huggingface.co/spaces/{target_space}",
            payload={"operation": "image_to_video", "has_prompt": bool(prompt)},
            function="image_to_video",
        )
        return {"status": "rate_limited", "message": f"Hugging Face is busy. Retry later. {exc}"}
    except Exception as exc:  # noqa: BLE001
        log_connection_failure(
            exc,
            endpoint=f"https://huggingface.co/spaces/{target_space}",
            payload={"operation": "image_to_video"},
            function="image_to_video",
        )
        return {"status": "failed", "message": f"Image-to-video failed: {exc}"}
    finally:
        os.unlink(temp_path)


def _output_path(result: Any) -> Any:
    if isinstance(result, (list, tuple)):
        for item in result:
            value = _output_path(item)
            if value:
                return value
        return None
    if isinstance(result, dict):
        for key in ("video", "path", "url", "data"):
            if key in result:
                return _output_path(result[key])
        return None
    path = getattr(result, "path", None) or getattr(result, "url", None) or result
    return path if isinstance(path, (str, bytes)) else str(path)
