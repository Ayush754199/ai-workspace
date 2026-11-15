"""Document text extraction and generation helpers used by chat and office tools.

Only real libraries are used (pypdf, Pillow, python-docx, python-pptx, openpyxl,
reportlab). If an optional library is missing the caller gets an explicit error
instead of a fake success.
"""

from __future__ import annotations

import io
import re
import zipfile

MAX_CHARS = 200_000


class OcrUnavailable(RuntimeError):
    """OCR was needed but no engine/provider is available.

    Re-exported from ``app.services.vision`` so callers can import it from the
    module they already use. A document whose pages carry no text layer is never
    reported as "processed": the upload endpoint answers with a controlled 503 and
    chat attachments set ``ocr_unavailable``.
    """





def extract_pdf_text(data: bytes, *, max_chars: int = MAX_CHARS) -> str:
    # PyMuPDF gives stable page boundaries and is more reliable for mixed PDF
    # layouts. Keep pypdf as a compatibility fallback for minimal installs.
    try:
        import fitz

        document = fitz.open(stream=data, filetype="pdf")
        chunks: list[str] = []
        total = 0
        for index, page in enumerate(document, start=1):
            text = (page.get_text("text") or "").strip()
            if text:
                block = f"## Page {index}\n{text}"
                chunks.append(block)
                total += len(block)
            if total >= max_chars:
                break
        return "\n\n".join(chunks).strip()[:max_chars]
    except ImportError:
        from pypdf import PdfReader

        chunks = []
        total = 0
        for index, page in enumerate(PdfReader(io.BytesIO(data)).pages, start=1):
            text = (page.extract_text() or "").strip()
            if text:
                block = f"## Page {index}\n{text}"
                chunks.append(block)
                total += len(block)
            if total >= max_chars:
                break
        return "\n\n".join(chunks).strip()[:max_chars]


def extract_pdf_ocr(data: bytes, *, max_chars: int = MAX_CHARS, strict: bool = False) -> str:
    """OCR pages that carry no text layer.

    The local engine (``pytesseract``) is tried first because it is free and never
    sends the document anywhere; the configured vision provider is the fallback.

    ``strict=False`` (default, used by ``extract_any`` and chat attachments) keeps the
    historical behaviour: whatever was extracted is returned, and an unreadable page
    simply contributes nothing. ``strict=True`` (used by ``/documents/*``) raises
    :class:`OcrUnavailable` when pages needed OCR and no engine produced text, so a
    scanned PDF is never silently reported as processed.
    """
    try:
        import fitz
    except ImportError:
        # No rasterizer: scanned pages cannot be turned into images here. Digital
        # PDFs are unaffected (extract_pdf_text uses pypdf), and the caller decides
        # whether an unreadable scan is an error (strict) or just "no extra text".
        if strict and _has_image_pages(data):
            raise OcrUnavailable(
                "No OCR engine is available on this server, so scanned pages cannot be read. "
                "Install PyMuPDF plus Tesseract, or configure GEMINI_KEY_1 for vision OCR."
            ) from None
        return ""
    parts: list[str] = []
    ocr_needed = False
    ocr_failed = False
    failure_detail = ""
    for index, page in enumerate(fitz.open(stream=data, filetype="pdf"), start=1):
        if page.get_text("text").strip():
            continue
        ocr_needed = True
        pixmap = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
        image_data = pixmap.tobytes("png")
        text = ""
        try:
            import pytesseract
            from PIL import Image
            text = pytesseract.image_to_string(Image.open(io.BytesIO(image_data))).strip()
        except (ImportError, OSError):
            try:
                text = ocr_image(image_data).strip()
            except OcrUnavailable as exc:
                ocr_failed = True
                failure_detail = str(exc)
                break
        if text:
            parts.append(f"## Page {index} (OCR)\n{text}")
        if sum(len(part) for part in parts) >= max_chars:
            break
    if strict and ocr_needed and not parts and ocr_failed:
        raise OcrUnavailable(failure_detail or "No OCR engine is available on this server")
    return "\n\n".join(parts)[:max_chars]


def _has_image_pages(data: bytes) -> bool:
    """Best-effort: does this PDF contain pages without a text layer?

    Uses PyMuPDF when present and falls back to pypdf (always installed), so the
    answer does not depend on an optional dependency.
    """
    try:
        import fitz

        return any(not page.get_text("text").strip() for page in fitz.open(stream=data, filetype="pdf"))
    except ImportError:
        pass
    except Exception:  # noqa: BLE001 - an unreadable PDF is handled by the caller
        return False
    try:
        from pypdf import PdfReader

        return any(
            not (page.extract_text() or "").strip()
            for page in PdfReader(io.BytesIO(data)).pages
        )
    except Exception:  # noqa: BLE001 - unreadable/corrupt PDF: let the caller report it
        return False


def pdf_page_count(data: bytes) -> int:
    from pypdf import PdfReader

    return len(PdfReader(io.BytesIO(data)).pages)


def extract_docx_text(data: bytes, *, max_chars: int = MAX_CHARS) -> str:
    import docx

    document = docx.Document(io.BytesIO(data))
    parts = [paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()]
    for table in document.tables:
        for row in table.rows:
            parts.append(" | ".join(cell.text.strip() for cell in row.cells))
    return "\n".join(parts)[:max_chars]


def extract_pptx_text(data: bytes, *, max_chars: int = MAX_CHARS) -> str:
    from pptx import Presentation

    presentation = Presentation(io.BytesIO(data))
    parts: list[str] = []
    for index, slide in enumerate(presentation.slides, start=1):
        parts.append(f"## Slide {index}")
        for shape in slide.shapes:
            text = getattr(shape, "text", "")
            if text and text.strip():
                parts.append(text.strip())
    return "\n".join(parts)[:max_chars]


def extract_xlsx_text(data: bytes, *, max_chars: int = MAX_CHARS) -> str:
    import openpyxl

    workbook = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    parts: list[str] = []
    for sheet in workbook.worksheets:
        parts.append(f"## Sheet: {sheet.title}")
        for row in sheet.iter_rows(values_only=True):
            if row and any(cell is not None for cell in row):
                parts.append(" | ".join("" if cell is None else str(cell) for cell in row))
            if sum(len(p) for p in parts) >= max_chars:
                break
    return "\n".join(parts)[:max_chars]


def ocr_image(data: bytes) -> str:
    """OCR an image through the configured vision provider.

    Raises :class:`OcrUnavailable` when no provider is configured, so the caller can
    surface a controlled message instead of pretending the image was read.
    """
    from app.services.vision import OcrUnavailable as vision_unavailable
    from app.services.vision import ocr as gemini_ocr

    try:
        return gemini_ocr(data)
    except vision_unavailable as exc:
        raise OcrUnavailable(str(exc)) from exc


def _gemini_configured() -> bool:
    try:
        from app.services.vision import is_configured

        return is_configured()
    except Exception:  # noqa: BLE001 - availability probe must never raise
        return False


def extract_any(data: bytes, filename: str, content_type: str | None = None) -> tuple[str, str]:
    """Return ``(kind, text)`` for a supported document."""
    name = (filename or "").lower()
    ctype = (content_type or "").lower()
    if name.endswith(".pdf") or ctype == "application/pdf":
        text = extract_pdf_text(data)
        # A scanned PDF may contain a mixture of text and image-only pages.
        # OCR only the pages without a text layer, and retain the page labels
        # emitted by extract_pdf_ocr so answers can cite their source page.
        # ``strict`` turns "pages needed OCR and none was possible" into a clear
        # OcrUnavailable instead of an empty, apparently-successful extraction.
        ocr_text = extract_pdf_ocr(data, strict=True)
        if ocr_text:
            text = f"{text}\n\n{ocr_text}".strip() if text else ocr_text
        return "pdf", text
    if name.endswith(".docx"):
        return "docx", extract_docx_text(data)
    if name.endswith(".pptx"):
        return "pptx", extract_pptx_text(data)
    if name.endswith(".xlsx") or name.endswith(".xlsm"):
        return "xlsx", extract_xlsx_text(data)
    if name.endswith(".csv") or ctype == "text/csv":
        return "csv", data.decode("utf-8", errors="replace")[:MAX_CHARS]
    if name.endswith((".txt", ".md")) or ctype.startswith("text/"):
        return "text", data.decode("utf-8", errors="replace")[:MAX_CHARS]
    if name.endswith((".png", ".jpg", ".jpeg", ".webp")) or ctype.startswith("image/"):
        # An image has no text layer at all: OCR is the only way to read it, so a
        # missing engine must be reported (strict callers) rather than returning "".
        return "image", ocr_image(data)
    if zipfile.is_zipfile(io.BytesIO(data)):
        return "archive", ""
    return "unknown", ""


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET = re.compile(r"^\s*[-*]\s+(.*)$")
_NUMBERED = re.compile(r"^\s*\d+[.)]\s+(.*)$")


def markdown_blocks(markdown: str) -> list[tuple[str, str, int]]:
    """Split simple Markdown into ``(kind, text, level)`` blocks."""
    blocks: list[tuple[str, str, int]] = []
    for raw in (markdown or "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        heading = _HEADING.match(line)
        if heading:
            blocks.append(("heading", heading.group(2).strip(), len(heading.group(1))))
            continue
        bullet = _BULLET.match(line)
        if bullet:
            blocks.append(("bullet", bullet.group(1).strip(), 0))
            continue
        numbered = _NUMBERED.match(line)
        if numbered:
            blocks.append(("number", numbered.group(1).strip(), 0))
            continue
        blocks.append(("paragraph", line.strip(), 0))
    return blocks


def generate_docx(title: str, markdown: str) -> bytes:
    import docx

    document = docx.Document()
    if title:
        document.add_heading(title, level=0)
    for kind, text, level in markdown_blocks(markdown):
        if kind == "heading":
            document.add_heading(text, level=min(level, 4))
        elif kind in {"bullet", "number"}:
            document.add_paragraph(text, style="List Bullet" if kind == "bullet" else "List Number")
        else:
            document.add_paragraph(text)
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def generate_pptx(title: str, markdown: str) -> bytes:
    from pptx import Presentation
    from pptx.util import Pt

    presentation = Presentation()
    layout_title = presentation.slide_layouts[0]
    layout_bullets = presentation.slide_layouts[1]
    first = presentation.slides.add_slide(layout_title)
    first.shapes.title.text = title or "Presentation"
    if len(first.placeholders) > 1:
        first.placeholders[1].text = "Generated by AI Workspace"

    slide = None
    body = None
    for kind, text, level in markdown_blocks(markdown):
        if kind == "heading" and level <= 2:
            slide = presentation.slides.add_slide(layout_bullets)
            slide.shapes.title.text = text
            body = slide.placeholders[1].text_frame
            body.clear()
            continue
        if slide is None:
            slide = presentation.slides.add_slide(layout_bullets)
            slide.shapes.title.text = title or "Overview"
            body = slide.placeholders[1].text_frame
            body.clear()
        paragraph = body.add_paragraph() if body.paragraphs[0].text else body.paragraphs[0]
        paragraph.text = ("• " if kind in {"bullet", "paragraph"} else "") + text
        paragraph.font.size = Pt(18)
    output = io.BytesIO()
    presentation.save(output)
    return output.getvalue()


def generate_xlsx(rows: list[dict], *, sheet_title: str = "Data", title: str = "") -> bytes:
    import openpyxl
    from openpyxl.styles import Font, PatternFill

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = (sheet_title or "Data")[:31]
    if not rows:
        rows = [{"message": "No rows were provided"}]
    columns: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in columns:
                columns.append(str(key))
    header_fill = PatternFill("solid", fgColor="1F6654")
    for index, column in enumerate(columns, start=1):
        cell = sheet.cell(row=1, column=index, value=column)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
    for row_index, row in enumerate(rows, start=2):
        for column_index, column in enumerate(columns, start=1):
            value = row.get(column, "")
            if isinstance(value, (dict, list)):
                value = str(value)
            sheet.cell(row=row_index, column=column_index, value=value)
    for index, column in enumerate(columns, start=1):
        longest = max([len(str(column))] + [len(str(row.get(column, ""))) for row in rows])
        sheet.column_dimensions[openpyxl.utils.get_column_letter(index)].width = min(60, max(12, longest + 2))
    sheet.freeze_panes = "A2"
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def generate_csv(rows: list[dict], columns: list[str] | None = None) -> bytes:
    import csv

    buffer = io.StringIO()
    if not columns:
        columns = []
        for row in rows:
            for key in row.keys():
                if key not in columns:
                    columns.append(str(key))
    writer = csv.DictWriter(buffer, fieldnames=columns or ["message"], extrasaction="ignore")
    writer.writeheader()
    if rows:
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in writer.fieldnames})
    else:
        writer.writerow({"message": "No rows were provided"})
    return buffer.getvalue().encode("utf-8-sig")


def generate_pdf(title: str, markdown: str) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer

    styles = getSampleStyleSheet()
    output = io.BytesIO()
    document = SimpleDocTemplate(output, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=18 * mm, bottomMargin=18 * mm)
    story = []
    if title:
        story.append(Paragraph(_escape(title), styles["Title"]))
        story.append(Spacer(1, 8))
    bullets: list[str] = []
    for kind, text, level in markdown_blocks(markdown) + [("flush", "", 0)]:
        if kind in {"bullet", "number"}:
            bullets.append(text)
            continue
        if bullets:
            story.append(ListFlowable([ListItem(Paragraph(_escape(item), styles["BodyText"])) for item in bullets], bulletType="bullet"))
            story.append(Spacer(1, 6))
            bullets = []
        if kind == "heading":
            story.append(Paragraph(_escape(text), styles[f"Heading{min(level, 4)}"]))
        elif kind == "paragraph":
            story.append(Paragraph(_escape(text), styles["BodyText"]))
            story.append(Spacer(1, 5))
    document.build(story or [Paragraph("Generated by AI Workspace", styles["BodyText"])])
    return output.getvalue()


def _escape(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("**", "")
    )


def table_from_text(text: str) -> list[dict]:
    """Best-effort parse of a pipe table or CSV block returned by a model."""
    import csv
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    pipe_lines = [line for line in lines if "|" in line]
    if not pipe_lines:
        rows = [[cell.strip() for cell in row] for row in csv.reader(lines, skipinitialspace=True)]
        rows = [row for row in rows if len(row) > 1]
        if len(rows) < 2:
            return []
        width = max(len(row) for row in rows)
        header = [cell or f"column_{index + 1}" for index, cell in enumerate(rows[0] + [""] * (width - len(rows[0])))]
        return [dict(zip(header, row + [""] * (width - len(row)))) for row in rows[1:]]
    rows: list[list[str]] = []
    for line in pipe_lines:
        stripped = line.strip()
        if not stripped:
            continue
        if "|" in stripped:
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if all(set(cell) <= {"-", ":", " "} for cell in cells):
                continue
            rows.append(cells)
    if len(rows) < 2:
        return []
    header = [cell or f"column_{index + 1}" for index, cell in enumerate(rows[0])]
    return [dict(zip(header, row + [""] * (len(header) - len(row)))) for row in rows[1:]]
