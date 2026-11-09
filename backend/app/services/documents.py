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


def extract_pdf_text(data: bytes, *, max_chars: int = MAX_CHARS) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    chunks: list[str] = []
    total = 0
    for page in reader.pages:
        text = page.extract_text() or ""
        if text:
            chunks.append(text)
            total += len(text)
            if total >= max_chars:
                break
    return "\n\n".join(chunks).strip()[:max_chars]


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
    """OCR via the configured Gemini vision model.

    There is no local OCR engine in this deployment, so an unconfigured server
    returns an empty string and the caller reports that OCR was unavailable
    rather than returning invented text.
    """
    if not _gemini_configured():
        return ""
    from app.services.vision import ocr as gemini_ocr

    return gemini_ocr(data)


def _gemini_configured() -> bool:
    try:
        from backend.key_rotator import get_provider_credentials

        return bool(get_provider_credentials("Gemini"))
    except Exception:
        return False


def extract_any(data: bytes, filename: str, content_type: str | None = None) -> tuple[str, str]:
    """Return ``(kind, text)`` for a supported document."""
    name = (filename or "").lower()
    ctype = (content_type or "").lower()
    if name.endswith(".pdf") or ctype == "application/pdf":
        return "pdf", extract_pdf_text(data)
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
