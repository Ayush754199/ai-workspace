"""Phase 1E: OCR fails in a controlled way and never fakes an extraction.

``documents.ocr_image`` imported ``app.services.vision``, which did not exist, so a
scanned PDF / image silently produced no text. The contract now is:

* digital PDF text extraction is untouched;
* an image that needs OCR with no configured provider raises ``OcrUnavailable``;
* a configured provider is used through the existing Gemini client;
* no text is ever invented.
"""

from __future__ import annotations

import io
import tempfile
from pathlib import Path

import pytest

from app.services import documents as docs
from app.services import vision


# PyMuPDF is optional; the PDF-raster fixture uses it. The vision adapter,
# image path and digital-extraction contract are tested without it, so a missing
# rasterizer must not hide them.
try:
    import fitz  # type: ignore

except ImportError:  # pragma: no cover - optional dependency
    fitz = None

requires_fitz = pytest.mark.skipif(
    fitz is None, reason="PyMuPDF is optional; PDF raster tests need it"
)


def _image_pdf() -> bytes:
    """A one-page PDF whose only content is a raster image (no text layer)."""
    from PIL import Image

    buffer = io.BytesIO()
    image = Image.new("RGB", (40, 40), "white")
    image.save(buffer, format="PNG")
    png = buffer.getvalue()

    document = fitz.open()
    page = document.new_page()
    page.insert_image(fitz.Rect(0, 0, 200, 200), stream=png)
    return document.tobytes()


def _text_pdf() -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Digital text layer")
    return document.tobytes()


@pytest.fixture()
def no_vision(monkeypatch):
    monkeypatch.setattr(vision, "is_configured", lambda: False)
    monkeypatch.setattr(vision, "_credentials", lambda: ())
    monkeypatch.setattr(docs, "_gemini_configured", lambda: False)
    # No local tesseract: force the vision fallback path.
    import sys

    monkeypatch.setitem(sys.modules, "pytesseract", None)
    return monkeypatch


def test_vision_module_exists_and_reports_unavailable(no_vision):
    assert vision.is_configured() is False
    with pytest.raises(vision.OcrUnavailable):
        vision.ocr(b"\x89PNG\r\n\x1a\nfake")
    assert issubclass(docs.OcrUnavailable, RuntimeError)


@requires_fitz
def test_digital_pdf_extraction_still_works(no_vision):
    # Written per test so nothing is persisted in the repository.
    path = Path(tempfile.mkstemp(suffix=".pdf")[1])
    try:
        path.write_bytes(_text_pdf())
        kind, text = docs.extract_any(path.read_bytes(), "digital.pdf", "application/pdf")
    finally:
        path.unlink(missing_ok=True)
    assert kind == "pdf"
    assert "Digital text layer" in text


@requires_fitz
def test_scanned_pdf_without_ocr_engine_raises_instead_of_returning_empty(no_vision):
    with pytest.raises(docs.OcrUnavailable):
        docs.extract_any(_image_pdf(), "scanned.pdf", "application/pdf")


def test_image_without_ocr_engine_raises(no_vision):
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (10, 10), (10, 10, 10)).save(buffer, format="PNG")
    with pytest.raises(docs.OcrUnavailable):
        docs.extract_any(buffer.getvalue(), "picture.png", "image/png")


@requires_fitz
def test_blank_image_is_not_sent_to_the_provider(monkeypatch):
    """A pure-white scan is answered locally instead of spending provider quota."""
    from PIL import Image

    calls: list[int] = []
    monkeypatch.setattr(vision, "ocr", lambda data: calls.append(len(data)) or "")
    buffer = io.BytesIO()
    Image.new("RGB", (20, 20), "white").save(buffer, format="PNG")
    assert docs.ocr_image(buffer.getvalue()) == ""
    assert calls == []


def test_configured_vision_provider_is_used(monkeypatch):
    captured: dict = {}

    def fake_ocr(data: bytes) -> str:
        captured["bytes"] = len(data)
        return "Transcribed line"

    monkeypatch.setattr(vision, "ocr", fake_ocr)
    monkeypatch.setattr(docs, "_gemini_configured", lambda: True)
    monkeypatch.setattr(
        "app.services.documents.ocr_image",
        lambda data: fake_ocr(data),
    )
    text = docs.ocr_image(b"image-bytes")
    assert text == "Transcribed line"
    assert captured["bytes"] == len(b"image-bytes")


def test_vision_returns_no_text_marker_as_empty(monkeypatch):
    """The model saying "NO_TEXT" must not become a literal transcript."""
    import sys

    class Response:
        text = "NO_TEXT"

    class _Models:
        @staticmethod
        def generate_content(**_kwargs):
            return Response()

    class _Client:
        def __init__(self, **_kwargs) -> None:
            self.models = _Models()

        def close(self) -> None:
            return None

    class _Types:
        @staticmethod
        def Part(**kwargs):
            return kwargs

    # "from google.genai import types" requires both packages to behave like
    # packages, so the fakes are modules rather than plain type() objects.
    import types as _module_types

    fake_genai = _module_types.ModuleType("google.genai")
    fake_genai.Client = _Client
    fake_types = _module_types.ModuleType("google.genai.types")
    fake_types.Part = _Types.Part
    fake_genai.types = fake_types
    fake_google = _module_types.ModuleType("google")
    fake_google.genai = fake_genai
    monkeypatch.setitem(sys.modules, "google", fake_google)
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai)
    monkeypatch.setitem(sys.modules, "google.genai.types", fake_types)
    monkeypatch.setattr(vision, "_credentials", lambda: ("cred",))
    # call_gemini_model returns (result, model_that_answered).
    monkeypatch.setattr("backend.key_rotator.call_gemini_model", lambda *a, **k: (Response(), "fake-model"))
    assert vision.ocr(b"image-bytes") == ""


def test_vision_returns_transcript_from_model_chain(monkeypatch):
    """A real transcript must survive the (result, model) unpacking of the chain."""
    import sys
    import types as _module_types

    class Response:
        text = "Invoice total: 1200"

    class _Client:
        def __init__(self, **_kwargs) -> None:
            return None

        def close(self) -> None:
            return None

    class _Types:
        @staticmethod
        def Part(**kwargs):
            return kwargs

    fake_genai = _module_types.ModuleType("google.genai")
    fake_genai.Client = _Client
    fake_types = _module_types.ModuleType("google.genai.types")
    fake_types.Part = _Types.Part
    fake_genai.types = fake_types
    fake_google = _module_types.ModuleType("google")
    fake_google.genai = fake_genai
    monkeypatch.setitem(sys.modules, "google", fake_google)
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai)
    monkeypatch.setitem(sys.modules, "google.genai.types", fake_types)
    monkeypatch.setattr(vision, "_credentials", lambda: ("cred",))
    monkeypatch.setattr("backend.key_rotator.call_gemini_model", lambda *a, **k: (Response(), "fake-model"))
    assert vision.ocr(b"image-bytes") == "Invoice total: 1200"
