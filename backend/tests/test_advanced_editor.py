import sys
from types import ModuleType

import pytest

from backend import advanced_editor, key_rotator


def test_video_to_video_uses_configured_inputs_and_returns_video_bytes(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    output = tmp_path / "transformed.mp4"
    source.write_bytes(b"source")
    output.write_bytes(b"transformed video")
    calls = {}

    class FakeClient:
        # Signature mirrors the installed gradio_client (2.x): token is positional-or-keyword.
        def __init__(self, space, token=None):
            calls["space"] = space
            calls["token"] = token

        def predict(self, *inputs, api_name=None):
            calls["inputs"] = inputs
            calls["api_name"] = api_name
            return str(output)

        def close(self):
            calls["closed"] = True

    gradio_client = ModuleType("gradio_client")
    gradio_client.Client = FakeClient
    gradio_client.handle_file = lambda path: f"uploaded:{path}"
    monkeypatch.setitem(sys.modules, "gradio_client", gradio_client)
    monkeypatch.setenv("HF_TOKENS", '["test-token"]')
    monkeypatch.delenv("HF_TOKEN_1", raising=False)
    monkeypatch.delenv("HF_API_TOKEN", raising=False)
    monkeypatch.setattr(key_rotator, "log_connection_event", lambda *args, **kwargs: "{}")
    monkeypatch.setattr(key_rotator, "log_connection_failure", lambda *args, **kwargs: "{}")
    monkeypatch.setattr(advanced_editor, "log_connection_event", lambda *args, **kwargs: "{}")
    monkeypatch.setattr(advanced_editor, "log_connection_failure", lambda *args, **kwargs: "{}")

    result = advanced_editor.transform_video_with_hugging_face(
        source,
        "Make it cinematic",
        space="owner/video-style",
        api_name="/transform",
        inputs_json='["{video}", "{prompt}"]',
    )

    assert result == b"transformed video"
    assert calls == {
        "space": "owner/video-style",
        "token": "test-token",
        "inputs": (f"uploaded:{source}", "Make it cinematic"),
        "api_name": "/transform",
        "closed": True,
    }


def test_video_to_video_rejects_missing_input_placeholders(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    monkeypatch.setattr(
        advanced_editor,
        "log_connection_failure",
        lambda error, **kwargs: str(error),
    )

    with pytest.raises(RuntimeError) as raised:
        advanced_editor.transform_video_with_hugging_face(
            source,
            "Make it cinematic",
            space="owner/video-style",
            api_name=None,
            inputs_json='["{prompt}"]',
        )
    assert 'must include both "{video}" and "{prompt}"' in str(raised.value.__cause__)
