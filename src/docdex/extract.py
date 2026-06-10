"""Format-aware text extraction.

Each extractor imports its dependency lazily so that a missing library only
affects that format. `.doc`/`.rtf` rely on macOS `textutil`; on other
platforms they are reported as unsupported rather than failing.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

TEXT_EXTENSIONS = {
    ".txt", ".md", ".csv", ".json", ".html", ".htm", ".py", ".js", ".ts",
    ".tsx", ".css", ".yaml", ".yml", ".xml", ".log", ".bat", ".tex", ".rst",
}
OFFICE_EXTENSIONS = {".docx", ".pptx", ".xlsx", ".xlsm", ".pdf"}
TEXTUTIL_EXTENSIONS = {".doc", ".rtf"}  # macOS only

UNSUPPORTED_PREFIX = "[unsupported"


def textutil_available() -> bool:
    return sys.platform == "darwin"


def supported_extensions() -> set:
    exts = TEXT_EXTENSIONS | OFFICE_EXTENSIONS
    if textutil_available():
        exts |= TEXTUTIL_EXTENSIONS
    return exts


def is_supported(path) -> bool:
    return Path(path).suffix.lower() in supported_extensions()


def extract_docx(path: str) -> str:
    from docx import Document
    doc = Document(path)
    parts = []
    for p in doc.paragraphs:
        if p.text.strip():
            parts.append(p.text)
    for tbl in doc.tables:
        for row in tbl.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                parts.append(" | ".join(cells))
    return "\n".join(parts)


def extract_pptx(path: str) -> str:
    from pptx import Presentation
    prs = Presentation(path)
    parts = []
    for i, slide in enumerate(prs.slides, 1):
        parts.append(f"\n--- SLIDE {i} ---")
        for shape in slide.shapes:
            if shape.has_text_frame:
                for p in shape.text_frame.paragraphs:
                    t = "".join(r.text for r in p.runs).strip()
                    if t:
                        parts.append(t)
            if shape.has_table:
                for row in shape.table.rows:
                    cells = [c.text.strip() for c in row.cells]
                    if any(cells):
                        parts.append(" | ".join(cells))
        if slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame.text.strip()
            if notes:
                parts.append(f"[Notes] {notes}")
    return "\n".join(parts)


def extract_xlsx(path: str) -> str:
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    parts = []
    try:
        for sheet in wb.sheetnames:
            ws = wb[sheet]
            parts.append(f"\n=== SHEET: {sheet} ===")
            for row in ws.iter_rows(values_only=True):
                cells = ["" if v is None else str(v) for v in row]
                if any(c.strip() for c in cells):
                    parts.append("\t".join(cells))
    finally:
        wb.close()
    return "\n".join(parts)


def extract_pdf(path: str) -> str:
    from pdfminer.high_level import extract_text
    return extract_text(path) or ""


def extract_plain(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def extract_with_textutil(path: str) -> str:
    r = subprocess.run(
        ["textutil", "-convert", "txt", "-stdout", path],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or f"textutil exited {r.returncode}")
    return r.stdout


def extract(path) -> str:
    """Return extracted text, or an `[unsupported ...]` marker string."""
    p = Path(path)
    ext = p.suffix.lower()
    if ext == ".docx":
        return extract_docx(str(p))
    if ext == ".pptx":
        return extract_pptx(str(p))
    if ext in (".xlsx", ".xlsm"):
        return extract_xlsx(str(p))
    if ext == ".pdf":
        return extract_pdf(str(p))
    if ext in TEXTUTIL_EXTENSIONS:
        if textutil_available():
            return extract_with_textutil(str(p))
        return f"{UNSUPPORTED_PREFIX} {ext} requires macOS textutil; convert to .docx]"
    if ext in TEXT_EXTENSIONS:
        return extract_plain(str(p))
    return f"{UNSUPPORTED_PREFIX} extension: {ext}]"
