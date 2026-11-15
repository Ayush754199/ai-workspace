"""Smoke test for backend.pro_video_editor: builds clips with moviepy, then renders
every feature group and asserts a non-empty output file."""
from __future__ import annotations

import math
import tempfile
from pathlib import Path

import numpy as np
from moviepy import AudioArrayClip, VideoClip

from backend import pro_video_editor as pve


def make_clip(path: Path, seconds: float = 2.0, size=(320, 180), fps=12) -> None:
    def frame(t):
        arr = np.zeros((size[1], size[0], 3), dtype=np.uint8)
        arr[...] = int((t * 60) % 255)
        arr[:, : size[0] // 2, 1] = 200
        return arr

    clip = VideoClip(frame, duration=seconds).with_fps(fps)
    audio = AudioArrayClip(np.zeros((int(22050 * seconds), 2), dtype=np.float32), fps=22050)
    clip = clip.with_audio(audio)
    clip.write_videofile(str(path), codec="libx264", audio_codec="aac", logger=None, preset="ultrafast")


def run(name: str, spec: pve.ProEditSpec, work: Path) -> None:
    out = work / f"{name}.mp4"
    spec.output_path = str(out)
    spec.export_format = "MP4" if name != "gif" else "GIF"
    result = pve.render_pro_video(spec)
    size = result.stat().st_size
    print(f"{name:22s} ops={len(pve.plan_operations(spec)):2d} bytes={size}")
    assert size > 0


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="pve-test-") as tmp:
        work = Path(tmp)
        src = work / "source.mp4"
        make_clip(src)
        info = pve.probe_video(src)
        print("probe:", info.as_dict())

        base = dict(source_path=str(src), output_path="", resolution="480p", fps="24")
        run("trim_speed", pve.ProEditSpec(**base, start_seconds=0.2, end_seconds=1.5, speed=1.5), work)

        grade = pve.ColorGrade(
            exposure=10, contrast=15, saturation=20, vibrance=30, temperature=25, tint=-10,
            gamma=5, lift=5, gain=10, shadows_hue=40, midtones_variant=-20, highlights_hue=-30,
            curve_master=(0.0, 0.3, 0.55, 0.8, 1.0),
        )
        run("color_grade", pve.ProEditSpec(**base, color=grade, filter_preset="Cinematic"), work)

        run("transform_crop", pve.ProEditSpec(**base, transform=pve.TransformSpec(
            crop=(0, 10, 0, 10), scale_x=90, scale_y=90, position_x=5, position_y=-5,
            rotate=5, aspect_ratio="9:16",
        )), work)

        run("filters_blur", pve.ProEditSpec(**base, filter_preset="Cyberpunk", blur="Gaussian blur", blur_radius=3), work)

        run("mask_chroma", pve.ProEditSpec(**base, mask_shape="Circle", chroma_key="#00ff00", chroma_tolerance=0.3), work)

        run("titles_watermark", pve.ProEditSpec(**base, overlays=pve.OverlaySpec(
            titles=[{"text": "WANDER", "start": 0.0, "duration": 1.0, "position": ("center", "center")}],
            auto_captions="Your journey starts here.\nKeep walking.",
            watermark_text="Auralis",
        )), work)

        run("audio_mixer", pve.ProEditSpec(**base, audio=pve.AudioMixer(
            source_volume=0.8, music_path=str(src), music_volume=0.2, normalize=True,
            noise_reduction=0.1, master_volume=0.9,
        )), work)

        run("speed_fx", pve.ProEditSpec(**base, speed=2.0, fade_in=0.3, fade_out=0.3,
                                         transition="Slide Up", freeze_at=0.5, freeze_seconds=0.4), work)

        run("gif", pve.ProEditSpec(source_path=str(src), output_path="", resolution="480p"), work)

        big_grade = pve.ColorGrade(contrast=10)
        lut = pve._build_lut(big_grade, "Vivid", 1.0)
        assert lut.shape == (256, 3) and float(lut.min()) >= 0 and float(lut.max()) <= 1
        print("lut ok")

        try:
            pve.ProEditSpec(source_path=str(src), output_path="", speed=-1)
            pve.render_pro_video(pve.ProEditSpec(source_path=str(src), output_path=str(work / "x.mp4"), speed=-1))
            raise AssertionError("validation should have failed")
        except pve.ProEditError as exc:
            print("validation ok:", exc)

    print("ALL PRO EDITOR TESTS PASSED")


if __name__ == "__main__":
    main()
