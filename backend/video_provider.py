"""Hugging Face and Gemini-backed 30-second video generation pipeline."""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import requests

from backend.key_rotator import call_with_key_rotation
from backend.logger import log_connection_event, log_connection_failure
from config import (
    GEMINI_MODEL,
    HF_VIDEO_API_NAME,
    HF_VIDEO_INPUTS_JSON,
    MODEL_SPACE,
    VIDEO_SCENE_COUNT,
    VIDEO_SCENE_DURATION_SECONDS,
    VIDEO_TOTAL_DURATION_SECONDS,
)

if TYPE_CHECKING:
    from moviepy import VideoFileClip


def _parse_scenes(text: str) -> list[str]:
    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("```")
    ]
    numbered_scenes = [
        match
        for line in lines
        if (match := re.match(r"^\s*(?:[-*]\s*)?(?:scene\s*)?(\d+)\s*[:.)-]\s*(.+)$", line, re.IGNORECASE))
    ]
    scenes = [match.group(2).strip(" -*") for match in numbered_scenes]
    if not scenes:
        scenes = [line.strip(" -*") for line in lines]
    if len(scenes) != VIDEO_SCENE_COUNT:
        raise ValueError(f"Gemini returned {len(scenes)} scenes; exactly {VIDEO_SCENE_COUNT} are required.")
    return scenes


def split_prompt_into_scenes(main_prompt: str) -> list[str]:
    """Use Gemini to create six connected, five-second scene prompts."""
    if not main_prompt.strip():
        raise ValueError("Enter a video prompt before generating scenes.")

    try:
        from google import genai

        contents = (
            f"Break this video idea into exactly {VIDEO_SCENE_COUNT} chronological, visually specific "
            f"scene prompts. Each scene will become one {VIDEO_SCENE_DURATION_SECONDS}-second clip. "
            "Keep characters, setting, visual style, and continuity consistent. Return only six lines, "
            "numbered 1 through 6, with one self-contained generation prompt per line.\n\n"
            f"Video idea: {main_prompt.strip()}"
        )

        def generate(key: str) -> Any:
            client = genai.Client(api_key=key)
            try:
                return client.models.generate_content(model=GEMINI_MODEL, contents=contents)
            finally:
                close = getattr(client, "close", None)
                if callable(close):
                    close()

        response = call_with_key_rotation(
            "Gemini",
            generate,
            endpoint=f"Gemini/{GEMINI_MODEL}",
            payload={"scene_count": VIDEO_SCENE_COUNT, "prompt": main_prompt},
        )
        response_text = getattr(response, "text", None)
        if not isinstance(response_text, str) or not response_text.strip():
            raise RuntimeError("Gemini returned an empty scene breakdown.")
        scenes = _parse_scenes(response_text)
        log_connection_event(
            "video_scenes_created",
            endpoint=f"Gemini/{GEMINI_MODEL}",
            payload={"scene_count": len(scenes), "scene_duration_seconds": VIDEO_SCENE_DURATION_SECONDS},
        )
        return scenes
    except Exception as exc:
        log_connection_failure(
            exc,
            endpoint=f"Gemini/{GEMINI_MODEL}",
            payload={"scene_count": VIDEO_SCENE_COUNT, "prompt": main_prompt},
            function="split_prompt_into_scenes",
        )
        return [f"{main_prompt.strip()} (scene {index + 1} of {VIDEO_SCENE_COUNT})" for index in range(VIDEO_SCENE_COUNT)]


def _media_bytes(result: Any) -> bytes:
    if isinstance(result, (list, tuple)):
        for item in result:
            try:
                return _media_bytes(item)
            except (TypeError, ValueError):
                continue
        raise ValueError("The Hugging Face Space did not return a video file.")
    if isinstance(result, dict):
        for key in ("video", "path", "url", "data"):
            if key in result:
                return _media_bytes(result[key])
        raise ValueError("The Hugging Face Space returned an unrecognized result.")
    if isinstance(result, (bytes, bytearray)):
        return bytes(result)
    if isinstance(result, Path):
        if result.is_file():
            return result.read_bytes()
        raise ValueError("The Hugging Face Space returned a missing output file.")
    if not isinstance(result, str):
        result = getattr(result, "path", None) or getattr(result, "url", None)
    if isinstance(result, str):
        path = Path(result)
        if path.is_file():
            return path.read_bytes()
        if result.startswith(("https://", "http://")):
            response = requests.get(result, timeout=(15, 240))
            response.raise_for_status()
            if response.content:
                return response.content
            raise ValueError("The Hugging Face Space returned an empty video download.")
    raise ValueError("The Hugging Face Space returned an unsupported video result.")


def generate_single_clip(scene_prompt: str) -> bytes:
    """Generate one scene remotely and download its output into Render's temporary storage."""
    try:
        from gradio_client import Client

        input_template = json.loads(HF_VIDEO_INPUTS_JSON)
        if not isinstance(input_template, list):
            raise ValueError("HF_VIDEO_INPUTS_JSON must be a JSON array.")
        inputs = [
            VIDEO_SCENE_DURATION_SECONDS if value == "{duration_seconds}" else (
                scene_prompt if value == "{prompt}" else value
            )
            for value in input_template
        ]
        def generate(token: str) -> Any:
            client = Client(MODEL_SPACE, token=token)
            try:
                if HF_VIDEO_API_NAME:
                    return client.predict(*inputs, api_name=HF_VIDEO_API_NAME)
                return client.predict(*inputs)
            finally:
                close = getattr(client, "close", None)
                if callable(close):
                    close()

        result = call_with_key_rotation(
            "Hugging Face",
            generate,
            endpoint=f"https://huggingface.co/spaces/{MODEL_SPACE}",
            payload={"api_name": HF_VIDEO_API_NAME, "prompt": scene_prompt},
        )
        clip = _media_bytes(result)
        if not clip:
            raise RuntimeError("The Hugging Face Space returned an empty video clip.")
        log_connection_event(
            "video_scene_generated",
            endpoint=f"https://huggingface.co/spaces/{MODEL_SPACE}",
            payload={"api_name": HF_VIDEO_API_NAME, "prompt": scene_prompt, "bytes": len(clip)},
        )
        return clip
    except Exception as exc:
        details = log_connection_failure(
            exc,
            endpoint=f"https://huggingface.co/spaces/{MODEL_SPACE}",
            payload={"api_name": HF_VIDEO_API_NAME, "prompt": scene_prompt},
            function="generate_single_clip",
        )
        raise RuntimeError(f"Hugging Face video generation failed. Diagnostics: {details}") from exc


def generate_30s_video(main_prompt: str) -> bytes:
    """Generate six cloud clips, trim each to five seconds, and return a stitched MP4."""
    clips: list[VideoFileClip] = []
    source_clips: list[VideoFileClip] = []
    try:
        from moviepy import VideoFileClip, concatenate_videoclips

        scenes = split_prompt_into_scenes(main_prompt)
        with tempfile.TemporaryDirectory(prefix="nexus-video-") as directory:
            workdir = Path(directory)
            for index, scene_prompt in enumerate(scenes, start=1):
                clip_bytes = generate_single_clip(scene_prompt)
                clip_path = workdir / f"scene-{index:02}.mp4"
                clip_path.write_bytes(clip_bytes)
                clip = VideoFileClip(str(clip_path))
                source_clips.append(clip)
                if clip.duration < VIDEO_SCENE_DURATION_SECONDS:
                    actual_duration = clip.duration
                    clip.close()
                    raise ValueError(
                        f"Generated scene {index} is {actual_duration:.2f} seconds; "
                        f"at least {VIDEO_SCENE_DURATION_SECONDS} seconds are required."
                    )
                clips.append(clip.subclipped(0, VIDEO_SCENE_DURATION_SECONDS))

            final_path = workdir / "nexus-video-30s.mp4"
            final_clip = concatenate_videoclips(clips, method="compose")
            try:
                if abs(final_clip.duration - VIDEO_TOTAL_DURATION_SECONDS) > 0.1:
                    raise RuntimeError(
                        f"Stitched video duration is {final_clip.duration:.2f}s, "
                        f"expected {VIDEO_TOTAL_DURATION_SECONDS}s."
                    )
                final_clip.write_videofile(
                    str(final_path),
                    codec="libx264",
                    audio_codec="aac",
                    fps=clips[0].fps or 24,
                    logger=None,
                )
            finally:
                final_clip.close()

            output = final_path.read_bytes()
            if not output:
                raise RuntimeError("MoviePy produced an empty stitched video.")
            log_connection_event(
                "video_pipeline_succeeded",
                endpoint=f"https://huggingface.co/spaces/{MODEL_SPACE}",
                payload={
                    "scene_count": len(scenes),
                    "scene_duration_seconds": VIDEO_SCENE_DURATION_SECONDS,
                    "total_duration_seconds": VIDEO_TOTAL_DURATION_SECONDS,
                    "output_bytes": len(output),
                },
            )
            return output
    except Exception as exc:
        details = log_connection_failure(
            exc,
            endpoint=f"https://huggingface.co/spaces/{MODEL_SPACE}",
            payload={
                "scene_count": VIDEO_SCENE_COUNT,
                "scene_duration_seconds": VIDEO_SCENE_DURATION_SECONDS,
                "total_duration_seconds": VIDEO_TOTAL_DURATION_SECONDS,
                "prompt": main_prompt,
            },
            function="generate_30s_video",
        )
        raise RuntimeError(f"The 30-second video pipeline failed. Diagnostics: {details}") from exc
    finally:
        for clip in clips:
            clip.close()
        for clip in source_clips:
            clip.close()
