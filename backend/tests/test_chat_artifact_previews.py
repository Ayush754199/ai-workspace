from __future__ import annotations

import io
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

from docx import Document
from openpyxl import Workbook
from pptx import Presentation
from pptx.util import Inches
from streamlit.testing.v1 import AppTest

import streamlit_app


class UploadedFile(io.BytesIO):
    def __init__(self, name: str, data: bytes) -> None:
        super().__init__(data)
        self.name = name

    def getvalue(self) -> bytes:
        return super().getvalue()


def test_parse_csv_for_table_preview_and_ai_context():
    kind, content = streamlit_app.parse_file(
        UploadedFile("metrics.csv", b"metric,value\nrevenue,42\n")
    )

    assert kind == "csv"
    assert content == [["metric", "value"], ["revenue", "42"]]
    assert "revenue, 42" in streamlit_app._document_context(kind, content)


def test_parse_word_document_paragraphs_and_tables():
    document = Document()
    document.add_paragraph("Quarterly report")
    table = document.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "Revenue"
    table.rows[0].cells[1].text = "$42M"
    data = io.BytesIO()
    document.save(data)

    kind, content = streamlit_app.parse_file(UploadedFile("report.docx", data.getvalue()))

    assert kind == "docx"
    assert content["paragraphs"] == ["Quarterly report"]
    assert content["tables"] == [[["Revenue", "$42M"]]]
    assert "Revenue | $42M" in streamlit_app._document_context(kind, content)


def test_parse_excel_workbook_sheets():
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Summary"
    sheet.append(["Metric", "Value"])
    sheet.append(["Revenue", 42])
    data = io.BytesIO()
    workbook.save(data)

    kind, content = streamlit_app.parse_file(UploadedFile("report.xlsx", data.getvalue()))

    assert kind == "xlsx"
    assert content["Summary"] == [["Metric", "Value"], ["Revenue", 42]]
    assert "Revenue | 42" in streamlit_app._document_context(kind, content)


def test_parse_presentation_slides_and_tables():
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[5])
    textbox = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1))
    textbox.text_frame.text = "Quarterly results"
    data = io.BytesIO()
    presentation.save(data)

    kind, content = streamlit_app.parse_file(UploadedFile("results.pptx", data.getvalue()))

    assert kind == "pptx"
    assert content[0]["paragraphs"] == ["Quarterly results"]
    assert "Quarterly results" in streamlit_app._document_context(kind, content)


def test_chat_workspace_renders_canvas_and_chat_input():
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
    app.session_state["nav"] = "New chat"
    app.session_state["messages"] = [
        {"role": "user", "content": "Summarize this report", "files": ["report.docx"]},
        {"role": "assistant", "content": "## Quarterly report\n\nRevenue grew.", "voice_response": False},
    ]
    app.session_state["artifact_files"] = [
        {
            "name": "report.docx",
            "extension": "docx",
            "kind": "docx",
            "content": {"paragraphs": ["Quarterly report"], "tables": []},
            "data": b"test-docx",
            "sha256": "test-digest",
        }
    ]
    app.session_state["artifact_source"] = "report.docx"
    app.run(timeout=90)

    assert not app.exception
    assert any(button.label == "Canvas" for button in app.button)
    assert any(button.label == "Retry" for button in app.button)
    assert app.download_button
    assert any(item.key == "main_chat_input" for item in app.chat_input)


def test_gemini_chat_streams_chunks_to_the_live_output(monkeypatch):
    class FakeClient:
        def __init__(self, *, api_key: str) -> None:
            assert api_key == "test-key"
            self.models = SimpleNamespace(
                generate_content_stream=lambda **_kwargs: iter(
                    [SimpleNamespace(text="Hello "), SimpleNamespace(text="stream")]
                )
            )

        def close(self) -> None:
            pass

    fake_genai = ModuleType("google.genai")
    fake_genai.Client = FakeClient
    fake_genai.types = SimpleNamespace(Part=SimpleNamespace(from_bytes=lambda **_kwargs: None))
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai)
    monkeypatch.setattr(streamlit_app, "_effective_gemini_credentials", lambda: ("configured",))
    monkeypatch.setattr(
        streamlit_app,
        "call_with_key_rotation",
        lambda _provider, operation, **_kwargs: operation("test-key"),
    )
    monkeypatch.setattr(streamlit_app, "log_connection_event", lambda *args, **kwargs: "{}")
    chunks = []

    response = streamlit_app.gemini_generate("prompt", [], on_chunk=chunks.append)

    assert response == "Hello stream"
    assert chunks == ["Hello ", "stream"]
