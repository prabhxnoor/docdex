"""Format-aware text extraction.

Each extractor imports its dependency lazily so that a missing library only
affects that format. `.doc`/`.rtf` rely on macOS `textutil`; on other
platforms they are reported as unsupported rather than failing.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import warnings
from pathlib import Path

# Quiet the noisy third-party extractor chatter: pdfminer emits hundreds of
# "Cannot set gray color … invalid float value" / "FontBBox" lines on real-world
# PDFs, and openpyxl warns about unsupported spreadsheet extensions. Neither is a
# failure (extraction proceeds), but the flood buries real errors. Set DOCDEX_DEBUG
# in the environment to restore the original verbosity.
if not os.environ.get("DOCDEX_DEBUG"):
    logging.getLogger("pdfminer").setLevel(logging.ERROR)

SECRETS_FILENAME = ".docdex.secrets.json"

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
    parts = []
    with warnings.catch_warnings():
        if not os.environ.get("DOCDEX_DEBUG"):
            warnings.simplefilter("ignore")  # openpyxl "extension not supported" noise
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
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


def read_secrets(root) -> dict:
    """Load the optional, user-controlled PDF-password map. Checks the v2 home
    location `<root>/.docdex/secrets.json` first, then the legacy
    `<root>/.docdex.secrets.json`. Missing or corrupt → empty dict (never
    raises). It lives inside the hidden home (or is a root dotfile), so the
    walker never indexes it; it is never committed to the docdex repo and its
    values are never logged."""
    root = Path(root)
    for candidate in (root / ".docdex" / "secrets.json", root / SECRETS_FILENAME):
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        return data if isinstance(data, dict) else {}
    return {}


def candidate_passwords(rel_path: str, secrets: dict) -> list:
    """Passwords whose key is a substring of the file's path. An empty-string key
    matches every path (a deliberate corpus-wide fallback)."""
    return [pw for key, pw in secrets.items() if key in rel_path]


def extract_pdf(path: str, passwords=()) -> str:
    from pdfminer.high_level import extract_text
    from pdfminer.pdfdocument import PDFPasswordIncorrect
    last_err = None
    for pw in ("", *passwords):  # try unencrypted / owner-readable first, then candidates
        try:
            return extract_text(path, password=pw) or ""
        except PDFPasswordIncorrect as e:
            last_err = e
    raise last_err  # encrypted and no candidate password worked


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


def extract(path, passwords=()) -> str:
    """Return extracted text, or an `[unsupported ...]` marker string.

    `passwords` are candidate passwords tried (in order) for an encrypted PDF;
    callers build them with `read_secrets` + `candidate_passwords`."""
    p = Path(path)
    ext = p.suffix.lower()
    if ext == ".docx":
        return extract_docx(str(p))
    if ext == ".pptx":
        return extract_pptx(str(p))
    if ext in (".xlsx", ".xlsm"):
        return extract_xlsx(str(p))
    if ext == ".pdf":
        return extract_pdf(str(p), passwords=passwords)
    if ext in TEXTUTIL_EXTENSIONS:
        if textutil_available():
            return extract_with_textutil(str(p))
        return f"{UNSUPPORTED_PREFIX} {ext} requires macOS textutil; convert to .docx]"
    if ext in TEXT_EXTENSIONS:
        return extract_plain(str(p))
    return f"{UNSUPPORTED_PREFIX} extension: {ext}]"
