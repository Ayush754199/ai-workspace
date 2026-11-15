"""Full end-to-end check of the new NLE controls: builds a real clip, drives the
build_spec helper through st.session_state and renders an export that uses
colour, transform, mask, overlay, audio and export settings together."""
from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
from moviepy import AudioArrayClip, VideoClip

from backend import pro_video_editor as pve
from streamlit.testing.v1 import AppTest
import streamlit_app


def make_source(path: Path, seconds: float = 2.0, size=(320, 180), fps=12) -> None:
    def frame(t):
        arr = np.zeros((size[1], size[0], 3), dtype=np.uint8)
        arr[..., 0] = int((t * 50) % 255)
        arr[:, : size[0] // 2, 1] = 180
        return arr

    clip = VideoClip(frame, duration=seconds).with_fps(fps)
    clip = clip.with_audio(AudioArrayClip(np.zeros((22050 * int(seconds), 2), np.float32), fps=22050))
    clip.write_videofile(str(path), codec="libx264", audio_codec="aac", logger=None, preset="ultrafast")


def main() -> None:
    # Trigger st.session_state creation outside a script run.
    at = AppTest.from_file(str(Path(__file__).with_name("test_pro_video_editor.py").parents[1] / "streamlit_app.py"), default_timeout=120)
    at.run()
    assert not at.exception, [e.value for e in at.exception]

    app = streamlit_app
    # `init_state` uses st.session_state.setdefault, which only works inside a
    # script run, so the defaults are supplied directly here instead.
    app = streamlit_app
    app.st.session_state.update(
        {
            "pro_source_name": "source.mp4",
            "pro_exposure": 12.0,
            "pro_contrast": 20.0,
            "pro_saturation": 10.0,
            "pro_vibrance": 15.0,
            "pro_temperature": 20.0,
            "pro_filter_preset": "Warm Film",
            "pro_blur": "Soft focus",
            "pro_mask_shape": "Rounded box",
            "pro_scale_x": 90.0,
            "pro_rotate": 4.0,
            "pro_export_aspect": "9:16",
            "pro_speed": 1.25,
            "pro_fade_in": 0.2,
            "pro_watermark_text": "Auralis",
            "pro_captions_text": "Wander far.",
            "pro_auto_captions_on": True,
            "pro_resolution": "480p",
            "pro_fps": "24",
            "pro_quality": 70.0,
            "pro_music_volume": 0.3,
        }
    )

    with tempfile.TemporaryDirectory(prefix="e2e-pro-") as tmp:
        work = Path(tmp)
        src = work / "source.mp4"
        make_source(src)
        out = work / "out.mp4"

        app.st.session_state["pro_source_bytes"] = src.read_bytes()
        app.st.session_state["pro_source_name"] = "source.mp4"
        changes = {
            "exposure": 12.0, "contrast": 20.0, "saturation": 10.0, "vibrance": 15.0,
            "temperature": 20.0, "filter_preset": "Warm Film", "blur": "Soft focus",
            "mask_shape": "Rounded box", "scale_x": 90.0, "rotate": 4.0,
            "export_aspect": "9:16", "speed": 1.25, "fade_in": 0.2,
            "watermark_text": "Auralis", "captions_text": "Wander far.", "auto_captions_on": True,
            "resolution": "480p", "fps": "24", "quality": 70.0,
        }
        for key, value in changes.items():
            app.st.session_state[f"pro_{key}"] = value

        music = work / "music.wav"
        music.write_bytes((work / "source.mp4").read_bytes())

        spec = app._pro_build_spec(src, out, dict(app.st.session_state), {"music": str(music)})
        ops = pve.plan_operations(spec)
        print("planned ops:", ops)
        result = pve.render_pro_video(spec)
        print("rendered bytes:", result.stat().st_size)
        assert result.stat().st_size > 0

        info = pve.probe_video(result)
        print("output:", info.as_dict())
        assert info.has_audio

    print("E2E PRO EDIT PASSED")


if __name__ == "__main__":
    main()
