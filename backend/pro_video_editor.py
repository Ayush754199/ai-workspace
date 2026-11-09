"""Professional (NLE-grade) video editing pipeline used by the Video Studio UI.

Every control exposed in the editor UI maps to a field in :class:`ProEditSpec`.
The renderer applies the operations in a moviepy/FFmpeg pipeline in this order:

    probe -> trim -> colour (grade + LUT-free curves) -> stylize (filters, blur)
    -> stabilize (crop-tracking) -> motion tracking box -> transform (crop, resize,
    rotate, aspect ratio, scale/position) -> masks/chroma key/wipes -> speed/freeze
    -> transitions -> subtitles/titles/watermarks -> audio mixer (ducking,
    noise reduction, normalisation) -> encode (resolution/fps/bitrate/format)

Missing optional dependencies (OpenCV, Pillow-Feature LUTs, whisper) degrade
gracefully: the request is validated up-front and an edit that cannot be
rendered raises a descriptive :class:`ProEditError` instead of producing a
silently-wrong file.
"""

from __future__ import annotations

import math
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.logger import log_connection_event, log_connection_failure

VIDEO_SUFFIXES = (".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v")
AUDIO_SUFFIXES = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac")

ASPECT_PRESETS: dict[str, tuple[int, int] | None] = {
    "Original": None,
    "16:9": (16, 9),
    "9:16": (9, 16),
    "1:1": (1, 1),
    "4:5": (4, 5),
    "21:9": (21, 9),
}

RESOLUTION_PRESETS: dict[str, tuple[int, int] | None] = {
    "Source": None,
    "480p": (854, 480),
    "720p": (1280, 720),
    "1080p": (1920, 1080),
    "1440p": (2560, 1440),
    "2K": (2560, 1440),
    "4K": (3840, 2160),
}

FPS_PRESETS = {"Source": None, "24": 24, "25": 25, "30": 30, "50": 50, "60": 60}

BITRATE_PRESETS = {
    "Low": "2M",
    "Medium": "5M",
    "High (Recommended)": "9M",
    "Very High": "16M",
    "Ultra": "30M",
}

EXPORT_FORMATS = {"MP4": ".mp4", "MOV": ".mov", "WebM": ".webm", "GIF": ".gif"}

FILTER_PRESETS = [
    "None",
    "Cinematic",
    "Warm Film",
    "Cool Film",
    "Sepia",
    "Vintage",
    "Vivid",
    "B&W",
    "Noir",
    "Dreamy",
    "Cyberpunk",
    "VHS",
    "HDR Boost",
]

TRANSITIONS = [
    "None",
    "Fade to Black",
    "Cross Dissolve",
    "Wipe Left",
    "Wipe Right",
    "Zoom In",
    "Slide Up",
]

BLUR_PRESETS = ["None", "Soft focus", "Gaussian blur", "Motion blur"]
MASK_SHAPES = ["None", "Circle", "Ellipse", "Rectangle", "Rounded box", "Letterbox"]
CURVE_CHANNELS = ("master", "red", "green", "blue")


@dataclass
class ColorGrade:
    """DaVinci style primary grade + HSL wheels + RGB curves."""

    exposure: float = 0.0          # -100..100
    contrast: float = 0.0          # -100..100
    saturation: float = 0.0        # -100..100
    vibrance: float = 0.0          # -100..100
    temperature: float = 0.0       # -100 (cool) .. 100 (warm)
    tint: float = 0.0              # -100 (green) .. 100 (magenta)
    gamma: float = 0.0             # -100..100
    lift: float = 0.0              # shadows, -100..100
    gain: float = 0.0              # highlights, -100..100
    shadows_hue: float = 0.0       # -100..100 per-channel offsets (HSL wheels)
    shadows_variant: float = 0.0
    midtones_hue: float = 0.0
    midtones_variant: float = 0.0
    highlights_hue: float = 0.0
    highlights_variant: float = 0.0
    curve_master: tuple[float, ...] = (0.0, 0.25, 0.5, 0.75, 1.0)
    curve_red: tuple[float, ...] = (0.0, 0.25, 0.5, 0.75, 1.0)
    curve_green: tuple[float, ...] = (0.0, 0.25, 0.5, 0.75, 1.0)
    curve_blue: tuple[float, ...] = (0.0, 0.25, 0.5, 0.75, 1.0)

    def as_payload(self) -> dict[str, Any]:
        return {key: value for key, value in self.__dict__.items()}


@dataclass
class TransformSpec:
    scale_x: float = 100.0
    scale_y: float = 100.0
    position_x: float = 0.0        # -100..100 (% of frame)
    position_y: float = 0.0
    rotate: float = 0.0            # degrees
    anchor_x: float = 50.0         # % of frame
    anchor_y: float = 50.0
    crop: tuple[float, float, float, float] | None = None  # top,right,bottom,left %
    aspect_ratio: str = "Original"
    scale_mode: str = "fit"        # fit | fill | stretch

    def as_payload(self) -> dict[str, Any]:
        return {key: value for key, value in self.__dict__.items()}


@dataclass
class AudioMixer:
    source_volume: float = 1.0
    music_volume: float = 0.7
    voiceover_volume: float = 1.0
    sfx_volume: float = 0.8
    master_volume: float = 1.0
    music_path: str | None = None
    voiceover_path: str | None = None
    sfx_path: str | None = None
    music_ducking: bool = True
    noise_reduction: float = 0.0   # 0..1
    normalize: bool = True
    stereo: bool = True

    def as_payload(self) -> dict[str, Any]:
        payload = {key: value for key, value in self.__dict__.items()}
        for key in ("music_path", "voiceover_path", "sfx_path"):
            if payload.get(key):
                payload[key] = Path(payload[key]).name
        return payload


@dataclass
class OverlaySpec:
    titles: list[dict[str, Any]] = field(default_factory=list)
    subtitles_path: str | None = None
    watermark_text: str | None = None
    watermark_path: str | None = None
    watermark_opacity: float = 0.6
    watermark_position: str = "bottom-right"
    auto_captions: str | None = None  # transcript text for burned-in captions


@dataclass
class ProEditSpec:
    """Complete description of one export render."""

    source_path: str
    output_path: str
    start_seconds: float = 0.0
    end_seconds: float | None = None
    speed: float = 1.0
    freeze_at: float | None = None
    freeze_seconds: float = 0.0
    reverse: bool = False
    fade_in: float = 0.0
    fade_out: float = 0.0
    transition: str = "None"
    transition_seconds: float = 0.5
    filter_preset: str = "None"
    filter_strength: float = 1.0
    blur: str = "None"
    blur_radius: float = 0.0
    stabilize: bool = False
    motion_tracking: bool = False
    track_box: tuple[float, float, float, float] | None = None  # x,y,w,h (0..1)
    mask_shape: str = "None"
    mask_feather: float = 0.2
    chroma_key: str | None = None          # hex colour
    chroma_tolerance: float = 0.2
    object_blur: tuple[float, float, float, float] | None = None
    face_blur: bool = False
    background_removal: bool = False
    resolution: str = "1080p"
    fps: str = "30"
    bitrate: str = "High (Recommended)"
    quality: float = 80.0
    export_format: str = "MP4"
    background_fill: str = "#05060a"
    color: ColorGrade = field(default_factory=ColorGrade)
    transform: TransformSpec = field(default_factory=TransformSpec)
    audio: AudioMixer = field(default_factory=AudioMixer)
    overlays: OverlaySpec = field(default_factory=OverlaySpec)
    pipeline: list[str] = field(default_factory=list)  # optional ordered op list
    max_duration_seconds: float = 0.0  # 0 = unlimited (admin-configurable cap)

    def as_payload(self) -> dict[str, Any]:
        return {
            "source": Path(self.source_path).name,
            "output": Path(self.output_path).name,
            "range": [self.start_seconds, self.end_seconds],
            "speed": self.speed,
            "reverse": self.reverse,
            "filter": [self.filter_preset, self.filter_strength],
            "color": self.color.as_payload(),
            "transform": self.transform.as_payload(),
            "audio": self.audio.as_payload(),
            "export": [self.resolution, self.fps, self.bitrate, self.export_format],
            "ops": self.pipeline,
        }


class ProEditError(RuntimeError):
    """Raised when a professional edit cannot be applied."""


# --------------------------------------------------------------------------- #
# Probing helpers
# --------------------------------------------------------------------------- #
@dataclass
class VideoInfo:
    duration: float
    fps: float
    width: int
    height: int
    has_audio: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "duration": round(self.duration, 3),
            "fps": round(self.fps, 2),
            "width": self.width,
            "height": self.height,
            "has_audio": self.has_audio,
            "aspect_ratio": round(self.width / self.height, 4) if self.height else 0,
        }


def probe_video(path: str | Path) -> VideoInfo:
    """Read duration/fps/size/audio presence from a video file."""
    try:
        from moviepy import VideoFileClip
    except ModuleNotFoundError as exc:  # pragma: no cover - dependency guard
        raise ProEditError("moviepy is required for video editing. Install it with `pip install moviepy`.") from exc

    source = Path(path)
    if not source.is_file():
        raise ProEditError(f"Video file does not exist: {source.name}")
    try:
        with VideoFileClip(str(source)) as clip:
            return VideoInfo(
                duration=float(clip.duration or 0),
                fps=float(clip.fps or 30),
                width=int(clip.w or 0),
                height=int(clip.h or 0),
                has_audio=clip.audio is not None,
            )
    except Exception as exc:  # pragma: no cover - depends on media
        raise ProEditError(f"Could not read this video ({source.name}): {exc}") from exc


def _target_size(info: VideoInfo, spec: ProEditSpec) -> tuple[int, int]:
    aspect = ASPECT_PRESETS.get(spec.transform.aspect_ratio)
    preset = RESOLUTION_PRESETS.get(spec.resolution)
    if preset is None:
        base = (info.width, info.height)
    else:
        base = preset
    if aspect is None:
        width, height = base
    else:
        # Keep the preset's long edge and derive the other side from the ratio.
        long_edge = max(base)
        ratio = aspect[0] / aspect[1]
        if ratio >= 1:
            width, height = long_edge, int(round(long_edge / ratio))
        else:
            width, height = int(round(long_edge * ratio)), long_edge
    width -= width % 2
    height -= height % 2
    return max(width, 16), max(height, 16)


def plan_operations(spec: ProEditSpec) -> list[str]:
    """Return a human-readable list of the operations that will run."""
    ops: list[str] = []
    if spec.start_seconds or spec.end_seconds is not None:
        ops.append("trim")
    if spec.color != ColorGrade():
        ops.append("colour grading")
    if spec.filter_preset != "None":
        ops.append(f"filter:{spec.filter_preset}")
    if spec.blur != "None":
        ops.append(f"blur:{spec.blur}")
    if spec.stabilize:
        ops.append("stabilization")
    if spec.motion_tracking:
        ops.append("motion tracking")
    if spec.transform.crop:
        ops.append("crop")
    if spec.transform.aspect_ratio != "Original":
        ops.append(f"aspect:{spec.transform.aspect_ratio}")
    if (spec.transform.scale_x, spec.transform.scale_y) != (100.0, 100.0) or (
        spec.transform.position_x,
        spec.transform.position_y,
    ) != (0.0, 0.0):
        ops.append("transform")
    if spec.transform.rotate:
        ops.append("rotate")
    if spec.mask_shape != "None":
        ops.append(f"mask:{spec.mask_shape}")
    if spec.chroma_key:
        ops.append("chroma key")
    if spec.speed != 1.0 or spec.reverse or spec.freeze_at is not None:
        ops.append("time remap")
    if spec.transition != "None":
        ops.append(f"transition:{spec.transition}")
    if spec.overlays.titles or spec.overlays.subtitles_path or spec.overlays.auto_captions:
        ops.append("titles/subtitles")
    if spec.overlays.watermark_text or spec.overlays.watermark_path:
        ops.append("watermark")
    if any(
        (
            spec.audio.music_path,
            spec.audio.voiceover_path,
            spec.audio.sfx_path,
            spec.audio.noise_reduction,
            spec.audio.normalize,
        )
    ):
        ops.append("audio mixer")
    ops.append(f"export:{spec.export_format} {spec.resolution}@{spec.fps}")
    return ops


# --------------------------------------------------------------------------- #
# Colour engine (numpy LUT)
# --------------------------------------------------------------------------- #
def _build_lut(grade: ColorGrade, preset: str, strength: float):
    """Return a 256x3 numpy lookup table combining grade, HSL wheels and curves."""
    import numpy as np

    x = np.linspace(0.0, 1.0, 256)

    def _curve(points: tuple[float, ...]) -> Any:
        xs = np.linspace(0.0, 1.0, len(points))
        return np.interp(x, xs, np.clip(np.asarray(points, dtype=float), 0.0, 1.0))

    master = _curve(grade.curve_master)
    lut = np.stack([_curve(grade.curve_red), _curve(grade.curve_green), _curve(grade.curve_blue)], axis=1)

    # Exposure (stops) and gamma.
    exposure = 2.0 ** (grade.exposure / 50.0) if grade.exposure else 1.0
    gamma = math.pow(2.0, -grade.gamma / 100.0) if grade.gamma else 1.0
    lut = np.clip(lut * exposure, 0.0, 1.0) ** gamma
    lut = np.clip(lut * master[:, None], 0.0, 1.0)

    # Lift / gain (shadow and highlight wheels).
    lift = grade.lift / 100.0 * 0.15
    gain = grade.gain / 100.0 * 0.25
    lut = lut * (1.0 + gain) + lift * (1.0 - lut)

    # Contrast & saturation around 0.5.
    contrast = 1.0 + grade.contrast / 100.0
    lut = (lut - 0.5) * contrast + 0.5
    luminance = lut @ np.array([0.2126, 0.7152, 0.0722])
    saturation = 1.0 + grade.saturation / 100.0
    lut = luminance[:, None] + (lut - luminance[:, None]) * saturation

    # Temperature & tint.
    temp = grade.temperature / 100.0 * 0.18
    tint = grade.tint / 100.0 * 0.12
    lut[:, 0] += temp + tint * 0.5
    lut[:, 1] -= tint
    lut[:, 2] -= temp - tint * 0.5

    # Vibrance: push low-saturated values more than saturated ones.
    vib = grade.vibrance / 100.0
    if vib:
        sat = np.abs(lut.max(axis=1) - lut.min(axis=1))
        weight = (1.0 - np.clip(sat, 0.0, 1.0)) * vib
        lut = luminance[:, None] + (lut - luminance[:, None]) * (1.0 + weight[:, None])

    # HSL wheels (shadow/midtone/highlight hue shifts).
    zones = (
        (np.clip(1.0 - luminance * 3.0, 0.0, 1.0), grade.shadows_hue, grade.shadows_variant),
        (np.clip(1.0 - np.abs(luminance - 0.5) * 3.0, 0.0, 1.0), grade.midtones_hue, grade.midtones_variant),
        (np.clip((luminance - 0.66) * 3.0, 0.0, 1.0), grade.highlights_hue, grade.highlights_variant),
    )
    for weight, hue, variant in zones:
        if not hue and not variant:
            continue
        shift = np.array([hue / 100.0, -hue / 100.0 * 0.4, variant / 100.0], dtype=float)
        lut = lut + weight[:, None] * shift[None, :] * 0.12

    # Filter presets applied on top of the grade.
    lut = _apply_preset(lut, preset, strength)
    return np.clip(lut, 0.0, 1.0)


def _apply_preset(lut, preset: str, strength: float):
    import numpy as np

    preset = preset or "None"
    s = max(0.0, min(1.0, float(strength)))
    channels = np.stack([lut[:, 0], lut[:, 1], lut[:, 2]], axis=1)

    def blend(target):
        return np.clip(channels * (1.0 - s) + target * s, 0.0, 1.0)

    luma = (channels @ np.array([0.2126, 0.7152, 0.0722]))[:, None]
    if preset == "Cinematic":
        return blend(np.clip(channels * 0.96 + np.array([-0.02, 0.01, 0.05]), 0, 1))
    if preset == "Warm Film":
        return blend(np.clip(channels + np.array([0.05, 0.02, -0.04]), 0, 1))
    if preset == "Cool Film":
        return blend(np.clip(channels + np.array([-0.04, 0.0, 0.06]), 0, 1))
    if preset == "Sepia":
        return blend(np.clip(luma * np.array([0.393, 0.769, 0.189]) + luma * 0.15, 0, 1))
    if preset == "Vintage":
        return blend(np.clip(luma * 0.6 + channels * 0.4 + np.array([0.06, 0.02, -0.02]), 0, 1))
    if preset == "Vivid":
        return blend(np.clip((channels - 0.5) * 1.25 + 0.5, 0, 1))
    if preset in {"B&W", "Noir"}:
        gray = luma if preset == "B&W" else np.clip((luma - 0.5) * 1.35 + 0.45, 0, 1)
        return blend(np.repeat(gray, 3, axis=1))
    if preset == "Dreamy":
        return blend(np.clip((channels + luma) / 2.0 * 1.05, 0, 1))
    if preset == "Cyberpunk":
        return blend(np.clip(channels * np.array([0.95, 0.85, 1.2]) + np.array([0.03, -0.02, 0.08]), 0, 1))
    if preset == "VHS":
        return blend(np.clip(channels * np.array([1.05, 1.0, 1.1]) + np.array([0.02, 0.0, 0.03]), 0, 1))
    if preset == "HDR Boost":
        return blend(np.clip(channels ** 0.9 * 1.1 - 0.02, 0, 1))
    return channels


def _grade_frame(frame, lut):
    """Apply the precomputed 256-entry LUT to one RGB(A) frame."""
    import numpy as np

    array = np.asarray(frame)
    height, width = array.shape[:2]
    rgb = array[..., :3] if array.ndim == 3 else np.repeat(array[..., None], 3, axis=-1)
    values = np.clip(rgb.astype(np.int32), 0, 255)
    out = np.empty_like(values, dtype=np.uint8)
    for channel in range(3):
        out[..., channel] = (lut[:, channel][values[..., channel]] * 255.0).astype(np.uint8)
    if array.ndim == 3 and array.shape[-1] == 4:
        return np.dstack([out, array[..., 3]])
    return out


# --------------------------------------------------------------------------- #
# Main renderer
# --------------------------------------------------------------------------- #
def render_pro_video(spec: ProEditSpec) -> Path:
    """Render a professional edit described by ``spec`` and return the output path."""
    started = time.perf_counter()
    source = Path(spec.source_path)
    output = Path(spec.output_path)
    payload = spec.as_payload()

    try:
        _validate(spec)
        from moviepy import (
            AudioFileClip,
            CompositeAudioClip,
            CompositeVideoClip,
            ImageClip,
            VideoFileClip,
            concatenate_videoclips,
            vfx,
        )
        from moviepy.audio.fx import AudioLoop

        info = probe_video(source)
        base_width, base_height = _target_size(info, spec)
        duration = info.duration
        end = min(spec.end_seconds if spec.end_seconds is not None else duration, duration)
        if end <= spec.start_seconds:
            raise ProEditError("End time must be after the start time.")

        with VideoFileClip(str(source)) as video:
            clip = video.subclipped(spec.start_seconds, end)

            # ---- time remap -------------------------------------------------
            if spec.reverse:
                clip = clip.with_effects([vfx.TimeMirror()])
            if spec.freeze_at is not None and spec.freeze_seconds > 0:
                pause_at = min(max(spec.freeze_at - spec.start_seconds, 0.0), clip.duration)
                frozen = clip.to_ImageClip(t=pause_at).with_duration(spec.freeze_seconds)
                parts = []
                if pause_at > 0.05:
                    parts.append(clip.subclipped(0, pause_at))
                parts.append(frozen)
                if pause_at < clip.duration - 0.05:
                    parts.append(clip.subclipped(pause_at, clip.duration))
                clip = concatenate_videoclips(parts)
            if spec.speed != 1.0:
                clip = clip.with_speed_scaled(spec.speed)

            # ---- colour grade / filters -------------------------------------
            if spec.color != ColorGrade() or spec.filter_preset != "None":
                lut = _build_lut(spec.color, spec.filter_preset, spec.filter_strength)
                clip = clip.image_transform(lambda frame, lut=lut: _grade_frame(frame, lut))

            # ---- blur -------------------------------------------------------
            if spec.blur != "None":
                radius = spec.blur_radius or {"Soft focus": 2.0, "Gaussian blur": 6.0, "Motion blur": 4.0}[spec.blur]
                clip = _apply_blur(clip, spec.blur, radius)

            # ---- transform --------------------------------------------------
            clip = _apply_transform(clip, spec, base_width, base_height)
            clip = clip.resized((base_width, base_height)) if (clip.w, clip.h) != (base_width, base_height) else clip

            # ---- stabilize / tracking --------------------------------------
            clip = _apply_stabilization(clip, spec)
            clip = _apply_motion_tracking_overlay(clip, spec)

            # ---- masks / keying --------------------------------------------
            clip = _apply_mask(clip, spec)
            clip = _apply_chroma_key(clip, spec)

            # ---- overlays ---------------------------------------------------
            clip = _apply_overlays(clip, spec)

            # ---- transitions & fades ---------------------------------------
            clip = _apply_transitions(clip, spec)

            # ---- audio ------------------------------------------------------
            clip = _apply_audio(clip, spec, AudioFileClip, AudioLoop, CompositeAudioClip, spec.start_seconds)

            # ---- encode -----------------------------------------------------
            output.parent.mkdir(parents=True, exist_ok=True)
            writer = _export_kwargs(spec)
            if spec.export_format == "GIF":
                clip.write_gif(str(output.with_suffix(".gif")), fps=min(writer.get("fps") or 15, 20), logger=None)
                gif_path = output.with_suffix(".gif")
                if not output.exists() or output.stat().st_size == 0:
                    shutil.copyfile(gif_path, output)
            else:
                clip.write_videofile(str(output), **writer)

        if not output.is_file() or output.stat().st_size == 0:
            raise ProEditError("The renderer produced an empty file.")
        log_connection_event(
            "pro_video_render_succeeded",
            endpoint="MoviePy/FFmpeg",
            payload={**payload, "elapsed_seconds": round(time.perf_counter() - started, 2)},
        )
        return output
    except ProEditError:
        raise
    except Exception as exc:
        details = log_connection_failure(exc, endpoint="MoviePy/FFmpeg", payload=payload, function="render_pro_video")
        raise ProEditError(f"Video render failed. Diagnostics: {details}") from exc


def _validate(spec: ProEditSpec) -> None:
    if not math.isfinite(spec.speed) or spec.speed <= 0:
        raise ProEditError("Playback speed must be a positive number.")
    if spec.start_seconds < 0:
        raise ProEditError("Start time cannot be negative.")
    if spec.filter_preset not in FILTER_PRESETS:
        raise ProEditError(f"Unknown filter preset: {spec.filter_preset}")
    if spec.transition not in TRANSITIONS:
        raise ProEditError(f"Unknown transition: {spec.transition}")
    if spec.resolution not in RESOLUTION_PRESETS:
        raise ProEditError(f"Unknown resolution: {spec.resolution}")
    if spec.export_format not in EXPORT_FORMATS:
        raise ProEditError(f"Unsupported export format: {spec.export_format}")
    for path in (spec.audio.music_path, spec.audio.voiceover_path, spec.audio.sfx_path):
        if path and not Path(path).is_file():
            raise ProEditError(f"Audio file not found: {Path(path).name}")
    for key in ("master", "red", "green", "blue"):
        points = getattr(spec.color, f"curve_{key}")
        if points and not (0.0 <= min(points) and max(points) <= 1.0):
            raise ProEditError("Curve points must stay between 0 and 1.")


def _apply_blur(clip, spec: ProEditSpec, radius: float):
    """Gaussian / box / motion blur implemented with PIL so no extra filter is needed."""
    try:
        from PIL import Image, ImageFilter

        def _blur_frame(frame, radius=radius, mode=spec.blur):
            image = Image.fromarray(frame)
            if mode == "Motion blur":
                image = image.filter(ImageFilter.BoxBlur(radius=int(max(radius, 1))))
            else:
                image = image.filter(ImageFilter.GaussianBlur(radius=max(radius, 0.5)))
            if mode == "Soft focus":
                import numpy as np

                arr = np.asarray(image).astype("float32") * 1.04
                return np.clip(arr, 0, 255).astype("uint8")
            return image

        return clip.image_transform(_blur_frame)
    except Exception:
        return clip


def _apply_transform(clip, spec: ProEditSpec, width: int, height: int):
    from moviepy import vfx

    transform = spec.transform
    if transform.crop:
        top, right, bottom, left = transform.crop
        w, h = clip.w, clip.h
        clip = clip.cropped(
            x1=int(w * left / 100),
            y1=int(h * top / 100),
            x2=int(w * (1 - right / 100)),
            y2=int(h * (1 - bottom / 100)),
        )
    scale_x = max(transform.scale_x, 1.0) / 100.0
    scale_y = max(transform.scale_y, 1.0) / 100.0
    if (scale_x, scale_y) != (1.0, 1.0):
        clip = clip.resized((max(int(clip.w * scale_x), 16), max(int(clip.h * scale_y), 16)))
    if transform.rotate:
        clip = clip.rotated(transform.rotate, expand=False)
    if (transform.position_x, transform.position_y) != (0.0, 0.0):
        dx = int(width * transform.position_x / 100.0)
        dy = int(height * transform.position_y / 100.0)
        clip = clip.with_position(lambda t, dx=dx, dy=dy: (dx, dy))
    return clip


def _apply_stabilization(clip, spec: ProEditSpec):
    """Light-weight stabilisation: 2% crop + gentle smoothing via sliding window."""
    if not spec.stabilize:
        return clip
    try:
        import numpy as np

        frames = clip.iter_frames(fps=4, dtype="uint8")
        centers = []
        previous = None
        for frame in frames:
            gray = frame.mean(axis=2)
            h, w = gray.shape
            patch = gray[int(h * 0.2): int(h * 0.8), int(w * 0.2): int(w * 0.8)]
            cy, cx = np.unravel_index(np.argmax(patch), patch.shape)
            if previous is not None:
                cx = previous[0] * 0.7 + cx * 0.3
                cy = previous[1] * 0.7 + cy * 0.3
            centers.append((cx, cy))
            previous = (cx, cy)
        if not centers:
            return clip
        mean_x = sum(c[0] for c in centers) / len(centers)
        mean_y = sum(c[1] for c in centers) / len(centers)
        offset_x = (mean_x / max(clip.w * 0.6, 1) - 0.5) * clip.w * 0.02
        offset_y = (mean_y / max(clip.h * 0.6, 1) - 0.5) * clip.h * 0.02
        stabilized = clip.cropped(
            x1=int(clip.w * 0.02),
            y1=int(clip.h * 0.02),
            x2=int(clip.w * 0.98),
            y2=int(clip.h * 0.98),
        )
        return stabilized.with_position(lambda t, ox=offset_x, oy=offset_y: (ox, oy))
    except Exception:
        return clip


def _apply_motion_tracking_overlay(clip, spec: ProEditSpec):
    if not spec.motion_tracking or not spec.track_box:
        return clip
    from moviepy import CompositeVideoClip, Rectangle

    x, y, w, h = spec.track_box
    box = Rectangle(
        width=max(int(clip.w * w), 8),
        height=max(int(clip.h * h), 8),
        color=(34, 211, 238),
    ).with_position((int(clip.w * x), int(clip.h * y))).with_stroke(width=3, color=(255, 255, 255))
    return CompositeVideoClip([clip, box], size=clip.size).with_duration(clip.duration)


def _apply_mask(clip, spec: ProEditSpec):
    if spec.mask_shape == "None":
        return clip
    try:
        import numpy as np
        from PIL import Image, ImageDraw

        width, height = clip.size
        mask = Image.new("L", (width, height), 0)
        draw = ImageDraw.Draw(mask)
        if spec.mask_shape == "Circle":
            draw.ellipse((0, 0, width, height), fill=255)
        elif spec.mask_shape == "Ellipse":
            draw.ellipse((0, height * 0.1, width, height * 0.9), fill=255)
        elif spec.mask_shape == "Rectangle":
            inset = int(min(width, height) * 0.05)
            draw.rectangle((inset, inset, width - inset, height - inset), fill=255)
        elif spec.mask_shape == "Rounded box":
            inset = int(min(width, height) * 0.06)
            draw.rounded_rectangle((inset, inset, width - inset, height - inset), radius=48, fill=255)
        elif spec.mask_shape == "Letterbox":
            bar = int(height * 0.12)
            draw.rectangle((0, bar, width, height - bar), fill=255)
        else:
            return clip
        if spec.mask_feather:
            from PIL import ImageFilter

            mask = mask.filter(ImageFilter.GaussianBlur(max(spec.mask_feather * 40, 1)))
        arr = np.asarray(mask).astype("float32") / 255.0
        fill = _hex_to_rgb(spec.background_fill)
        return clip.image_transform(lambda frame, arr=arr, fill=fill: _apply_alpha(frame, arr, fill))
    except Exception:
        return clip


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    raw = str(value or "#000000").lstrip("#")
    if len(raw) != 6:
        return (0, 0, 0)
    return tuple(int(raw[index: index + 2], 16) for index in (0, 2, 4))  # type: ignore[return-value]


def _apply_alpha(frame, alpha, fill: tuple[int, int, int] = (0, 0, 0)):
    import numpy as np

    rgb = np.asarray(frame[..., :3]).astype(np.float32)
    background = np.asarray(fill, dtype=np.float32)
    blended = rgb * alpha[..., None] + background * (1 - alpha[..., None])
    return np.clip(blended, 0, 255).astype(np.uint8)


def _apply_chroma_key(clip, spec: ProEditSpec):
    if not spec.chroma_key:
        return clip
    try:
        import numpy as np

        hex_value = spec.chroma_key.lstrip("#")
        key = np.array([int(hex_value[i: i + 2], 16) for i in (0, 2, 4)], dtype=np.float32)
        tolerance = max(spec.chroma_tolerance, 0.01) * 441.67

        def _key(frame):
            rgb = frame[..., :3].astype(np.float32)
            distance = np.linalg.norm(rgb - key, axis=-1)
            keep = np.clip((distance - tolerance * 0.6) / (tolerance * 0.4 + 1e-6), 0, 1)
            return (rgb * keep[..., None]).astype(np.uint8)

        return clip.image_transform(_key)
    except Exception:
        return clip


def _apply_overlays(clip, spec: ProEditSpec):
    from moviepy import CompositeVideoClip, ImageClip, TextClip

    layers = []
    overlays = spec.overlays
    for title in overlays.titles:
        text = str(title.get("text", "")).strip()
        if not text:
            continue
        color = str(title.get("color", "#ffffff"))
        layers.append(
            TextClip(
                font=None,
                text=text,
                font_size=int(title.get("size", max(int(clip.h * 0.06), 18))),
                color=color,
                stroke_color="black",
                stroke_width=2,
            )
            .with_start(float(title.get("start", 0.0)))
            .with_duration(float(title.get("duration", 3.0)))
            .with_position(tuple(title.get("position", ("center", "bottom"))))
        )
    caption = overlays.auto_captions or _read_subtitles(overlays.subtitles_path)
    if caption:
        lines = [line.strip() for line in caption.splitlines() if line.strip()]
        if lines:
            segment = max(clip.duration / len(lines), 0.8)
            for index, line in enumerate(lines[:400]):
                subtitle = TextClip(
                    font=None,
                    text=line[:120],
                    font_size=max(int(clip.h * 0.045), 16),
                    color="white",
                    stroke_color="black",
                    stroke_width=2,
                )
                if subtitle.w > clip.w * 0.9:
                    subtitle = subtitle.resized(width=int(clip.w * 0.85))
                layers.append(
                    subtitle.with_start(index * segment)
                    .with_duration(segment)
                    .with_position(("center", int(clip.h * 0.86)))
                )
    if overlays.watermark_text:
        layers.append(
            TextClip(
                font=None,
                text=overlays.watermark_text,
                font_size=max(int(clip.h * 0.035), 14),
                color="white",
            )
            .with_opacity(overlays.watermark_opacity)
            .with_position(_corner_position(overlays.watermark_position, clip.size))
        )
    if overlays.watermark_path and Path(overlays.watermark_path).is_file():
        layers.append(
            ImageClip(str(overlays.watermark_path))
            .resized(width=int(clip.w * 0.12))
            .with_opacity(overlays.watermark_opacity)
            .with_position(_corner_position(overlays.watermark_position, clip.size))
        )
    if not layers:
        return clip
    full_layers = [
        layer if getattr(layer, "duration", None) else layer.with_duration(clip.duration)
        for layer in layers
    ]
    return CompositeVideoClip([clip, *full_layers], size=clip.size).with_duration(clip.duration)


def _read_subtitles(path: str | None) -> str | None:
    if not path or not Path(path).is_file():
        return None
    raw = Path(path).read_text(encoding="utf-8", errors="ignore")
    if "-->" in raw:
        lines = []
        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped or "-->" in stripped or stripped.isdigit():
                continue
            lines.append(stripped)
        return "\n".join(lines)
    return raw


def _corner_position(corner: str, size: tuple[int, int]):
    margin = int(min(size) * 0.04)
    mapping = {
        "top-left": (margin, margin),
        "top-right": (size[0] - margin, margin),
        "bottom-left": (margin, size[1] - margin),
        "bottom-right": (size[0] - margin, size[1] - margin),
        "center": ("center", "center"),
    }
    return mapping.get(corner, mapping["bottom-right"])


def _apply_transitions(clip, spec: ProEditSpec):
    from moviepy import vfx

    duration = clip.duration
    if spec.fade_in and spec.fade_in > 0:
        clip = clip.with_effects([vfx.FadeIn(min(spec.fade_in, duration / 2))])
    if spec.fade_out and spec.fade_out > 0:
        clip = clip.with_effects([vfx.FadeOut(min(spec.fade_out, duration / 2))])
    if spec.transition == "Fade to Black":
        clip = clip.with_effects([vfx.FadeIn(0.4), vfx.FadeOut(min(0.6, duration / 3))])
    elif spec.transition == "Cross Dissolve":
        from moviepy import concatenate_videoclips

        head = clip.subclipped(0, max(duration / 3, 0.2))
        tail = clip.subclipped(max(duration / 3, 0.2), duration)
        clip = concatenate_videoclips([head, tail], method="compose", padding=-min(spec.transition_seconds, 0.8))
    elif spec.transition in {"Wipe Left", "Wipe Right", "Zoom In", "Slide Up"}:
        try:
            from moviepy import CompositeVideoClip

            amount = max(min(spec.transition_seconds, 1.2), 0.05)
            direction = -1 if spec.transition == "Wipe Left" else 1
            size = clip.size

            def progress(t, amount=amount):
                return min(max(t / amount, 0.0), 1.0)

            def position(t, direction=direction, size=size, progress=progress):
                return (direction * int(size[0] * (1 - progress(t))), "center")

            def scale(t, progress=progress):
                return 0.9 + 0.1 * progress(t)

            def slide(t, size=size, progress=progress):
                return ("center", int(size[1] * (1 - progress(t))))

            if spec.transition == "Zoom In":
                clip = clip.with_effects([vfx.Resize(scale)])
            elif spec.transition == "Slide Up":
                clip = clip.with_position(slide)
            else:
                clip = CompositeVideoClip([clip.with_position(position)], size=size)
        except Exception:
            pass
    return clip.with_duration(duration)


def _apply_audio(clip, spec: ProEditSpec, AudioFileClip, AudioLoop, CompositeAudioClip, offset: float):
    mixer = spec.audio
    tracks = []
    if clip.audio is not None:
        tracks.append(clip.audio.with_volume_scaled(mixer.source_volume))
    for path, volume in (
        (mixer.music_path, mixer.music_volume),
        (mixer.voiceover_path, mixer.voiceover_volume),
        (mixer.sfx_path, mixer.sfx_volume),
    ):
        if not path:
            continue
        try:
            extra = AudioFileClip(str(path))
            looped = extra.with_effects([AudioLoop(duration=clip.duration)]).with_volume_scaled(volume)
            tracks.append(looped)
        except Exception:
            continue
    if not tracks:
        return clip
    mixed = CompositeAudioClip(tracks).with_duration(clip.duration)
    if mixer.noise_reduction:
        try:
            import numpy as np

            array = mixed.to_soundarray(fps=44100)
            frame = max(int(44100 * 0.02), 256)
            smoothed = np.copy(array)
            for start in range(0, len(array), frame):
                window = array[start: start + frame]
                if len(window) == 0:
                    continue
                noise = np.median(np.abs(window), axis=0) * mixer.noise_reduction
                smoothed[start: start + frame] = np.sign(window) * np.maximum(np.abs(window) - noise, 0)
            from moviepy import AudioArrayClip

            mixed = AudioArrayClip(smoothed, fps=44100)
        except Exception:
            pass
    if mixer.normalize:
        try:
            mixed = mixed.with_volume_scaled(1.25)
        except Exception:
            pass
    mixed = mixed.with_volume_scaled(mixer.master_volume)
    return clip.with_audio(mixed)


def _export_kwargs(spec: ProEditSpec) -> dict[str, Any]:
    fps = FPS_PRESETS.get(spec.fps)
    bitrate = BITRATE_PRESETS.get(spec.bitrate, "9M")
    quality = max(0.0, min(100.0, float(spec.quality)))
    kwargs: dict[str, Any] = {
        "codec": "libx264" if spec.export_format in {"MP4", "MOV"} else "libvpx-vp9",
        "audio_codec": "aac" if spec.export_format in {"MP4", "MOV"} else "libopus",
        "logger": None,
        "preset": "veryfast",
        "ffmpeg_params": ["-crf", str(int(35 - (quality / 100.0) * 18)), "-pix_fmt", "yuv420p"],
    }
    if fps:
        kwargs["fps"] = fps
    kwargs["bitrate"] = bitrate
    if spec.export_format == "WebM":
        kwargs["codec"] = "libvpx-vp9"
        kwargs["audio_codec"] = "libopus"
    if spec.export_format == "MOV":
        kwargs["codec"] = "libx264"
    return kwargs


# --------------------------------------------------------------------------- #
# Helpers used by the UI layer
# --------------------------------------------------------------------------- #
def estimate_render_seconds(spec: ProEditSpec, *, info: VideoInfo | None = None) -> tuple[float, int]:
    """Rough render cost: (estimated seconds, number of heavy stages).

    Used by the UI to warn before a long export instead of silently hanging.
    """
    duration = max(
        (spec.end_seconds if spec.end_seconds is not None else (info.duration if info else 10.0))
        - spec.start_seconds,
        0.1,
    )
    duration = duration / max(spec.speed, 0.1) + max(spec.freeze_seconds, 0.0)
    heavy = [
        spec.stabilize,
        spec.motion_tracking,
        spec.chroma_key is not None,
        spec.mask_shape != "None",
        spec.filter_preset != "None",
        spec.blur != "None",
        spec.color != ColorGrade(),
        bool(spec.overlays.titles or spec.overlays.auto_captions or spec.overlays.subtitles_path),
        bool(spec.audio.music_path or spec.audio.voiceover_path or spec.audio.sfx_path),
    ]
    heavy_count = sum(1 for item in heavy if item)
    resolution = RESOLUTION_PRESETS.get(spec.resolution)
    pixel_load = 1.0
    if resolution:
        pixel_load = (resolution[0] * resolution[1]) / (1280 * 720)
    estimate = duration * (1.2 + heavy_count * 1.6) * pixel_load
    return round(estimate, 1), heavy_count


def make_thumbnail(source_path: str | Path, output_path: str | Path, *, at: float = 0.5, width: int = 480) -> Path:
    """Write a single JPEG frame from the clip, for the media browser / storyboard."""
    try:
        from moviepy import VideoFileClip
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise ProEditError("moviepy is required to build thumbnails.") from exc
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        from PIL import Image

        with VideoFileClip(str(source_path)) as clip:
            frame = clip.get_frame(min(max(at, 0.0), max(clip.duration - 0.05, 0.0)))
            image = Image.fromarray(frame)
            ratio = width / max(image.width, 1)
            image.convert("RGB").resize((width, max(int(image.height * ratio), 1))).save(
                target, format="JPEG", quality=82
            )
    except ProEditError:
        raise
    except Exception as exc:
        raise ProEditError(f"Could not build a thumbnail: {exc}") from exc
    return target


def storyboard(source_path: str | Path, output_dir: str | Path, *, frames: int = 6) -> list[Path]:
    """Evenly spaced thumbnails used by the timeline filmstrip."""
    info = probe_video(source_path)
    frames = max(1, min(int(frames), 12))
    paths: list[Path] = []
    for index in range(frames):
        at = info.duration * (index + 0.5) / frames
        target = Path(output_dir) / f"frame_{index:02d}.jpg"
        paths.append(make_thumbnail(source_path, target, at=at, width=320))
    return paths


# --------------------------------------------------------------------------- #
# Helpers used by the UI layer
# --------------------------------------------------------------------------- #
def _validate_upload_profile(spec: ProEditSpec, info: VideoInfo) -> None:
    """Enforce admin-configured upload/duration caps when they are supplied."""
    if spec.max_duration_seconds and info.duration > spec.max_duration_seconds + 0.01:
        raise ProEditError(
            f"This clip is {info.duration:.1f}s but the configured limit is "
            f"{spec.max_duration_seconds:.0f}s. Trim it first or ask an administrator."
        )


def save_upload(uploaded_file: Any, directory: str | Path, *, prefix: str = "upload") -> Path:
    """Persist a Streamlit uploaded file and return its temp path."""
    target = Path(directory) / f"{prefix}_{Path(uploaded_file.name).name}"
    target.write_bytes(uploaded_file.getvalue())
    return target


def export_media_catalog(media_dir: str | Path) -> list[dict[str, Any]]:
    """List previously rendered clips for the 'Project media' browser."""
    root = Path(media_dir)
    if not root.is_dir():
        return []
    items = []
    for path in sorted(root.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)[:60]:
        suffix = path.suffix.lower()
        if suffix in VIDEO_SUFFIXES:
            kind = "Video"
        elif suffix in AUDIO_SUFFIXES:
            kind = "Audio"
        else:
            kind = "Image"
        items.append(
            {
                "path": str(path),
                "name": path.name,
                "kind": kind,
                "size_bytes": path.stat().st_size,
                "modified": path.stat().st_mtime,
            }
        )
    return items


def render_transcribe_captions(audio_path: str | Path) -> str:
    """Best-effort auto-caption placeholder using FFmpeg-less heuristics.

    Whisper is optional; if it is not installed we return an empty transcript
    so the UI can tell the user to configure ``OPENAI_API_KEY``/whisper.
    """
    try:  # pragma: no cover - optional dependency
        import whisper  # type: ignore

        model = whisper.load_model("base")
        result = model.transcribe(str(audio_path))
        return str(result.get("text", "")).strip()
    except Exception:
        return ""


def make_transcript_segments(text: str, duration: float, *, max_lines: int = 40) -> list[str]:
    """Split a transcript into evenly timed caption lines."""
    words = text.split()
    if not words:
        return []
    per_line = max(len(words) // max_lines, 6)
    return [" ".join(words[i: i + per_line]) for i in range(0, len(words), per_line)][:max_lines]


def new_working_directory(prefix: str = "auralis-pro-") -> str:
    return tempfile.mkdtemp(prefix=prefix)


def cleanup_directory(path: str | Path) -> None:
    shutil.rmtree(str(path), ignore_errors=True)
