"""Local MoviePy editing and Hugging Face video-to-video transformation."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import requests

from backend.key_rotator import call_with_key_rotation
from backend.logger import log_connection_event, log_connection_failure


def edit_video(
    input_path: str | Path,
    output_path: str | Path,
    *,
    start_seconds: float,
    end_seconds: float,
    speed: float,
    audio_path: str | Path | None = None,
    audio_volume: float = 0.25,
) -> Path:
    """Trim, adjust playback speed, and optionally mix a looping background track."""
    source = Path(input_path)
    output = Path(output_path)
    allowed_speeds = {0.5, 1.0, 1.5, 2.0}
    payload = {
        "operation": "trim_speed_and_audio_mix",
        "filename": source.name,
        "start_seconds": start_seconds,
        "end_seconds": end_seconds,
        "speed": speed,
        "audio_filename": Path(audio_path).name if audio_path else None,
        "audio_volume": audio_volume if audio_path else None,
    }

    try:
        from moviepy import AudioFileClip, CompositeAudioClip, VideoFileClip
        from moviepy.audio.fx import AudioLoop

        if not source.is_file():
            raise FileNotFoundError(f"Video file does not exist: {source}")
        if not all(math.isfinite(value) for value in (start_seconds, end_seconds, speed)):
            raise ValueError("Start time, end time, and speed must be finite values.")
        if start_seconds < 0 or end_seconds <= start_seconds:
            raise ValueError("End time must be greater than the non-negative start time.")
        if speed not in allowed_speeds:
            raise ValueError(f"Speed must be one of {sorted(allowed_speeds)}.")
        if audio_path and (not math.isfinite(audio_volume) or not 0 <= audio_volume <= 1):
            raise ValueError("Background audio volume must be between 0 and 1.")
        if audio_path and not Path(audio_path).is_file():
            raise FileNotFoundError(f"Audio file does not exist: {audio_path}")

        output.parent.mkdir(parents=True, exist_ok=True)
        with VideoFileClip(str(source)) as video:
            if end_seconds > video.duration:
                end_seconds = video.duration
            if end_seconds <= start_seconds:
                raise ValueError("Start time must be before the video duration.")
            edited = video.subclipped(start_seconds, end_seconds).with_speed_scaled(speed)
            music = None
            looped_music = None
            background = None
            mixed_audio = None
            output_video = None
            try:
                if audio_path:
                    music = AudioFileClip(str(audio_path))
                    if music.duration <= 0:
                        raise ValueError("Background audio file contains no playable audio.")
                    looped_music = music.with_effects([AudioLoop(duration=edited.duration)])
                    background = looped_music.with_volume_scaled(audio_volume)
                    audio_tracks = [background]
                    if edited.audio is not None:
                        audio_tracks.insert(0, edited.audio)
                    mixed_audio = CompositeAudioClip(audio_tracks).with_duration(edited.duration)
                    output_video = edited.with_audio(mixed_audio)
                else:
                    output_video = edited

                output_video.write_videofile(
                    str(output),
                    codec="libx264",
                    audio_codec="aac",
                    logger=None,
                )
            finally:
                if output_video is not None and output_video is not edited:
                    output_video.close()
                if mixed_audio is not None:
                    mixed_audio.close()
                if background is not None:
                    background.close()
                if looped_music is not None:
                    looped_music.close()
                if music is not None:
                    music.close()
                if edited is not video:
                    edited.close()

        if not output.is_file() or output.stat().st_size == 0:
            raise RuntimeError("MoviePy did not produce the edited video.")
        log_connection_event("advanced_video_edit_succeeded", endpoint="MoviePy/FFmpeg", payload=payload)
        return output
    except Exception as exc:
        details = log_connection_failure(
            exc,
            endpoint="MoviePy/FFmpeg",
            payload=payload,
            function="edit_video",
        )
        raise RuntimeError(f"Advanced video editing failed. Diagnostics: {details}") from exc


def _resolve_gradio_input(value: Any, *, video_path: str, prompt: str) -> Any:
    if value == "{video}":
        from gradio_client import handle_file

        return handle_file(video_path)
    if value == "{prompt}":
        return prompt
    if isinstance(value, list):
        return [_resolve_gradio_input(item, video_path=video_path, prompt=prompt) for item in value]
    if isinstance(value, dict):
        return {
            key: _resolve_gradio_input(item, video_path=video_path, prompt=prompt)
            for key, item in value.items()
        }
    if isinstance(value, str):
        return value.replace("{prompt}", prompt)
    return value


def _video_result_bytes(result: Any) -> bytes:
    if isinstance(result, (list, tuple)):
        for item in result:
            try:
                return _video_result_bytes(item)
            except (TypeError, ValueError):
                continue
        raise ValueError("The Hugging Face Space did not return a video file.")
    if isinstance(result, dict):
        for key in ("video", "path", "url", "data"):
            if key in result:
                return _video_result_bytes(result[key])
        raise ValueError("The Hugging Face Space returned an unrecognized result.")
    if isinstance(result, (bytes, bytearray)):
        return bytes(result)
    if not isinstance(result, (str, Path)):
        result = getattr(result, "path", None) or getattr(result, "url", None)
    if isinstance(result, Path):
        if result.is_file():
            return result.read_bytes()
        raise ValueError("The Hugging Face Space returned a missing output file.")
    if isinstance(result, str):
        path = Path(result)
        if path.is_file():
            return path.read_bytes()
        if result.startswith(("https://", "http://")):
            response = requests.get(result, timeout=(15, 300))
            response.raise_for_status()
            if response.content:
                return response.content
            raise ValueError("The Hugging Face Space returned an empty video download.")
    raise ValueError("The Hugging Face Space returned an unsupported video result.")


def transform_video_with_hugging_face(
    video_path: str | Path,
    prompt: str,
    *,
    space: str,
    api_name: str | None,
    inputs_json: str,
) -> bytes:
    """Submit a video and transformation prompt to a configured Hugging Face Space."""
    source = Path(video_path)
    endpoint = f"https://huggingface.co/spaces/{space or 'unconfigured'}"
    payload = {
        "filename": source.name,
        "prompt": prompt,
        "api_name": api_name,
    }
    try:
        if not source.is_file():
            raise FileNotFoundError(f"Video file does not exist: {source}")
        if not prompt.strip():
            raise ValueError("Enter a transformation prompt.")
        if not space.strip():
            raise RuntimeError("Set HF_VIDEO_TO_VIDEO_SPACE to the video-to-video Hugging Face Space ID.")
        input_template = json.loads(inputs_json)
        if not isinstance(input_template, list):
            raise ValueError("HF_VIDEO_TO_VIDEO_INPUTS_JSON must be a JSON array.")
        serialized = json.dumps(input_template)
        if "{video}" not in serialized or "{prompt}" not in serialized:
            raise ValueError('HF_VIDEO_TO_VIDEO_INPUTS_JSON must include both "{video}" and "{prompt}".')

        from gradio_client import Client

        inputs = [
            _resolve_gradio_input(value, video_path=str(source), prompt=prompt.strip())
            for value in input_template
        ]

        def transform(token: str) -> Any:
            client = Client(space, token=token)
            try:
                return (
                    client.predict(*inputs, api_name=api_name)
                    if api_name
                    else client.predict(*inputs)
                )
            finally:
                close = getattr(client, "close", None)
                if callable(close):
                    close()

        result = call_with_key_rotation(
            "Hugging Face",
            transform,
            endpoint=endpoint,
            payload=payload,
        )
        video_bytes = _video_result_bytes(result)
        if not video_bytes:
            raise RuntimeError("The Hugging Face Space returned an empty transformed video.")
        log_connection_event(
            "video_to_video_succeeded",
            endpoint=endpoint,
            payload={"api_name": api_name, "output_bytes": len(video_bytes), **payload},
        )
        return video_bytes
    except Exception as exc:
        details = log_connection_failure(
            exc,
            endpoint=endpoint,
            payload=payload,
            function="transform_video_with_hugging_face",
        )
        raise RuntimeError(f"AI video transformation failed. Diagnostics: {details}") from exc
