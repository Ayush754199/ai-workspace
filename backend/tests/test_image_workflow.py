from pathlib import Path
from types import ModuleType

import streamlit_app
from streamlit.testing.v1 import AppTest


def test_image_generation_error_explains_missing_hugging_face_token():
    title, message = streamlit_app.describe_hugging_face_error(
        RuntimeError(
            "No Hugging Face credentials configured. Set the provider key array or numbered keys."
        )
    )

    assert title == "Hugging Face token not configured"
    assert "HF_TOKEN_1" in message
    assert "HF_API_TOKEN" in message


def test_image_edit_error_explains_missing_space_configuration():
    title, message = streamlit_app.describe_hugging_face_error(
        RuntimeError("Configure the Hugging Face Space for style transfer in the environment.")
    )

    assert title == "Image editing Space not configured"
    assert "HF_STYLE_TRANSFER_SPACE" in message


def test_image_provider_rate_limit_has_actionable_feedback():
    title, message = streamlit_app.describe_hugging_face_error(
        RuntimeError("429 Client Error: Too Many Requests")
    )

    assert title == "Hugging Face is busy"
    assert "try again" in message


def test_image_edit_space_input_template_includes_uploaded_image_and_prompt(monkeypatch):
    monkeypatch.setattr(streamlit_app, "HF_STYLE_TRANSFER_INPUTS", '["{image}", "{prompt}"]')
    gradio_client = ModuleType("gradio_client")
    gradio_client.handle_file = lambda path: f"file:{path}"
    monkeypatch.setitem(__import__("sys").modules, "gradio_client", gradio_client)

    inputs = streamlit_app._configured_gradio_inputs(
        streamlit_app.HF_STYLE_TRANSFER_INPUTS,
        prompt="Make it watercolor",
        image_path="upload.png",
    )

    assert inputs == ["file:upload.png", "Make it watercolor"]


def test_image_workspace_has_generation_and_edit_controls_in_one_section():
    app = AppTest.from_file(Path(__file__).resolve().parents[2] / "streamlit_app.py")
    app.session_state["user_id"] = "507f1f77bcf86cd799439011"
    app.session_state["username"] = "test-user"
    app.session_state["user"] = {
        "username": "test-user",
        "name": "Test User",
        "role": "user",
        "is_admin": False,
    }
    app.session_state["is_admin"] = False
    app.session_state["auth_session_token"] = "test-token"
    app.session_state["nav"] = "Images"
    app.run(timeout=90)

    assert not app.exception
    assert not app.get("tab")
    assert [form.key for form in app.get("form")] == ["image_generation_form"]
    assert any(item.label == "Upload an image to edit (optional)" for item in app.file_uploader)
    assert any(item.label == "Describe the image or the changes you want" for item in app.text_area)
    assert any(item.label == "Generate image" for item in app.form_submit_button)


def test_hugging_face_backup_text_request_uses_rotated_token(monkeypatch):
    class FakeResponse:
        headers = {"content-type": "application/json"}
        content = b""

        def raise_for_status(self):
            pass

        def json(self):
            return [{"generated_text": "Backup response"}]

    requests = []
    monkeypatch.setattr(
        streamlit_app.requests,
        "post",
        lambda *args, **kwargs: requests.append((args, kwargs)) or FakeResponse(),
    )
    monkeypatch.setattr(
        streamlit_app,
        "call_with_key_rotation",
        lambda _provider, operation, **_kwargs: operation("test-hf-token"),
    )
    monkeypatch.setattr(streamlit_app, "log_connection_event", lambda *args, **kwargs: "{}")

    assert streamlit_app.generate_hf_text("test prompt") == "Backup response"
    assert requests[0][1]["headers"]["Authorization"] == "Bearer test-hf-token"
