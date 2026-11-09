"""MoviePy utilities for trimming videos and mixing background music."""

from __future__ import annotations

import math
from pathlib import Path

from backend.logger import log_connection_event, log_connection_failure


def trim_video(input_path: str | Path, start: float, end: float) -> Path:
    """Trim a video to [start, end] and write a sibling MP4 file."""
    source = Path(input_path)
    output = source.with_name(f"{source.stem}_trimmed.mp4")
    try:
        from moviepy import VideoFileClip

        if not source.is_file():
            raise FileNotFoundError(f"Video file does not exist: {source}")
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            raise ValueError("Trim times must be finite, start at or after zero, and end after start.")
        with VideoFileClip(str(source)) as clip:
            if end > clip.duration:
                raise ValueError(f"End time ({end:g}s) exceeds video duration ({clip.duration:.2f}s).")
            with clip.subclipped(start, end) as trimmed:
                trimmed.write_videofile(
                    str(output),
                    codec="libx264",
                    audio_codec="aac",
                    logger=None,
                )
        if not output.is_file() or output.stat().st_size == 0:
            raise RuntimeError("MoviePy did not produce the trimmed video.")
        log_connection_event(
            "video_trim_succeeded",
            endpoint="MoviePy/FFmpeg",
            payload={"operation": "trim", "start_seconds": start, "end_seconds": end},
        )
        return output
    except Exception as exc:
        details = log_connection_failure(
            exc,
            endpoint="MoviePy/FFmpeg",
            payload={"operation": "trim", "filename": source.name, "start_seconds": start, "end_seconds": end},
            function="trim_video",
        )
        raise RuntimeError(f"Video trimming failed. Diagnostics: {details}") from exc


def add_background_audio(video_path: str | Path, audio_path: str | Path, volume: float) -> Path:
    """Loop a soundtrack to the video duration and mix it with the original audio."""
    source_video = Path(video_path)
    source_audio = Path(audio_path)
    output = source_video.with_name(f"{source_video.stem}_with_audio.mp4")
    try:
        from moviepy import AudioFileClip, CompositeAudioClip, VideoFileClip
        from moviepy.audio.fx import AudioLoop

        if not source_video.is_file():
            raise FileNotFoundError(f"Video file does not exist: {source_video}")
        if not source_audio.is_file():
            raise FileNotFoundError(f"Audio file does not exist: {source_audio}")
        if not math.isfinite(volume) or not 0 <= volume <= 1:
            raise ValueError("Background audio volume must be between 0 and 1.")

        with VideoFileClip(str(source_video)) as video, AudioFileClip(str(source_audio)) as music:
            if music.duration <= 0:
                raise ValueError("Background audio file contains no playable audio.")
            looped_music = music.with_effects([AudioLoop(duration=video.duration)])
            background = looped_music.with_volume_scaled(volume)
            tracks = [background]
            if video.audio is not None:
                tracks.insert(0, video.audio)
            mixed_audio = CompositeAudioClip(tracks).with_duration(video.duration)
            try:
                with video.with_audio(mixed_audio) as output_video:
                    output_video.write_videofile(
                        str(output),
                        codec="libx264",
                        audio_codec="aac",
                        logger=None,
                    )
            finally:
                mixed_audio.close()
                background.close()
                looped_music.close()

        if not output.is_file() or output.stat().st_size == 0:
            raise RuntimeError("MoviePy did not produce the audio-mixed video.")
        log_connection_event(
            "background_audio_succeeded",
            endpoint="MoviePy/FFmpeg",
            payload={"operation": "mix_background_audio", "volume": volume},
        )
        return output
    except Exception as exc:
        details = log_connection_failure(
            exc,
            endpoint="MoviePy/FFmpeg",
            payload={
                "operation": "mix_background_audio",
                "video_filename": source_video.name,
                "audio_filename": source_audio.name,
                "volume": volume,
            },
            function="add_background_audio",
        )
        raise RuntimeError(f"Background audio mixing failed. Diagnostics: {details}") from exc
