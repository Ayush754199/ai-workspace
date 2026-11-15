"""Headless render test: exercises render_pro_video_studio with Streamlit's
AppTest so widget wiring / indentation / indentation errors surface before the
user ever opens the app."""
from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "streamlit_app.py")


def main() -> None:
    at = AppTest.from_file(APP, default_timeout=90)
    at.run()
    print("initial exception groups:", len(at.exception))
    for exc in at.exception:
        print("EXC:", exc.value)

    # Simulate the editor being open on a fresh (empty) project.
    at.session_state["pro_media_nonce"] = 0
    def _page() -> None:
        from streamlit_app import init_state, render_pro_video_studio

        init_state()
        render_pro_video_studio()

    harness = AppTest.from_function(_page, default_timeout=90)
    harness.run()
    print("editor exceptions:", len(harness.exception))
    for exc in harness.exception:
        print("EXC:", exc.value)
    print("widgets:", len(harness.slider), "sliders,", len(harness.selectbox), "selectboxes", len(harness.get("file_uploader")), "uploaders")
    assert not at.exception and not harness.exception
    print("UI SMOKE PASSED")


if __name__ == "__main__":
    main()
