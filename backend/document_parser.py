"""Text extraction helpers for documents sent to Gemini context."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def extract_document_text(file_path: str | Path, *, max_chars: int | None = None) -> str:
    path = Path(file_path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf(path, max_chars)
    if suffix in {".docx", ".doc"}:
        return _extract_docx(path, max_chars)
    if suffix in {".xlsx", ".xls", ".csv"}:
        return _extract_spreadsheet(path, max_chars)
    if suffix in {".txt", ".md", ".json", ".py", ".js", ".ts"}:
        text = path.read_text(encoding="utf-8", errors="replace")
        return _truncate(text, max_chars)
    raise ValueError(f"Unsupported document type: {suffix}")


def _truncate(text: str, max_chars: int | None) -> str:
    if max_chars is not None and len(text) > max_chars:
        return text[:max_chars] + "\n...[truncated]"
    return text


def _extract_pdf(path: Path, max_chars: int | None) -> str:
    try:
        import pdfplumber

        chunks: list[str] = []
        with pdfplumber.open(str(path)) as pdf:
            for page in pdf.pages:
                chunks.append(page.extract_text() or "")
        return _truncate("\n\n".join(chunks).strip(), max_chars)
    except ImportError:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
        return _truncate(text.strip(), max_chars)


def _extract_docx(path: Path, max_chars: int | None) -> str:
    from docx import Document

    document = Document(str(path))
    paragraphs = [paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()]
    table_text: list[str] = []
    for table in document.tables:
        for row in table.rows:
            table_text.append(" | ".join(cell.text.strip() for cell in row.cells))
    text = "\n".join(paragraphs + table_text)
    return _truncate(text.strip(), max_chars)


def _extract_spreadsheet(path: Path, max_chars: int | None) -> str:
    if path.suffix.lower() == ".csv":
        import csv

        with path.open(newline="", encoding="utf-8", errors="replace") as handle:
            rows = [", ".join(row) for row in csv.reader(handle)]
        return _truncate("\n".join(rows).strip(), max_chars)

    import openpyxl

    workbook = openpyxl.load_workbook(str(path), data_only=True)
    chunks: list[str] = []
    for worksheet in workbook.worksheets:
        chunks.append(f"Sheet: {worksheet.title}")
        for row in worksheet.iter_rows(values_only=True):
            values = ["" if value is None else str(value) for value in row]
            if any(value.strip() for value in values):
                chunks.append(" | ".join(values))
    return _truncate("\n".join(chunks).strip(), max_chars)
PY