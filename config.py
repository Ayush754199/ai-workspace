"""Cloud runtime configuration for NEXUS AI media services."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).with_name(".env"), override=False)
load_dotenv(dotenv_path=Path(__file__).parent / "backend" / ".env", override=False)

# Keep the legacy 30-second pipeline compatible with the configured, single-shot
# text-to-video Space. Older deployments used HF_VIDEO_* while newer .env files
# use the more explicit HF_TEXT_TO_VIDEO_* names.
MODEL_SPACE = (
    os.getenv("HF_VIDEO_SPACE", "").strip()
    or os.getenv("HF_TEXT_TO_VIDEO_SPACE", "").strip()
    or "Wan-AI/Wan2.1"
)
VIDEO_SCENE_COUNT = 6
VIDEO_SCENE_DURATION_SECONDS = 5
VIDEO_TOTAL_DURATION_SECONDS = VIDEO_SCENE_COUNT * VIDEO_SCENE_DURATION_SECONDS
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip() or "gemini-3.8-flash"
# Tried in order when a model is retired (404) or its quota is exhausted (429).
# "gemini-flash-latest" is Google's rolling alias, which absorbs future retirements.
GEMINI_MODEL_FALLBACKS = tuple(
    model.strip()
    for model in os.getenv(
        "GEMINI_MODEL_FALLBACKS",
        "gemini-flash-latest,gemini-3.5-flash,gemini-3.5-flash-lite,gemini-3.1-flash-lite",
    ).split(",")
    if model.strip()
)

HF_VIDEO_API_NAME = (
    os.getenv("HF_VIDEO_API_NAME", "").strip()
    or os.getenv("HF_TEXT_TO_VIDEO_API_NAME", "").strip()
    or None
)
HF_VIDEO_INPUTS_JSON = (
    os.getenv("HF_VIDEO_INPUTS_JSON", "").strip()
    or os.getenv("HF_TEXT_TO_VIDEO_INPUTS", "").strip()
    or '["{prompt}"]'
)
HF_VIDEO_TO_VIDEO_SPACE = os.getenv("HF_VIDEO_TO_VIDEO_SPACE", "").strip()
HF_VIDEO_TO_VIDEO_API_NAME = os.getenv("HF_VIDEO_TO_VIDEO_API_NAME", "").strip() or None
HF_VIDEO_TO_VIDEO_INPUTS_JSON = (
    os.getenv("HF_VIDEO_TO_VIDEO_INPUTS_JSON", "").strip()
    or os.getenv("HF_VIDEO_TO_VIDEO_INPUTS", "").strip()
    or '["{video}", "{prompt}"]'
)
PROVIDER_RETRY_BACKOFF_SECONDS = (2, 4, 8)
PROVIDER_RETRY_COOLDOWN_SECONDS = 30
