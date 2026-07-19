"""Binary-content guard: docdex must not index binary garbage.

Repro for the "it started indexing bin files and got stuck" bug: a binary file
carrying a whitelisted text extension (.log/.csv/.json/.xml/.txt/.html) used to
be read via errors="replace" and indexed as megabytes of replacement-character
noise — bloating the index and, at size/scale, hanging the sync. The fix:

  A. content-sniff before the plain-text read; binary content is reported as
     `unsupported` (never extracted), and
  B. all extracted text is sanitized (NUL / stray C0 control chars stripped)
     before it is cached, so real PDFs with leaked control chars stay clean.
"""
from __future__ import annotations

import os

from docdex import extract as ex
from docdex.inventory import read_extract_status
from docdex.sync import run_sync


# --- Part A: binary detection -------------------------------------------------

def test_looks_binary_on_nul_bytes():
    assert ex.looks_binary(b"hello\x00world")
    assert ex.looks_binary(os.urandom(4096))


def test_looks_binary_on_high_control_ratio_without_nul():
    # No NUL, but mostly C0 control bytes (not tab/newline/CR/FF) -> binary.
    assert ex.looks_binary(bytes([1, 2, 3, 4, 5, 6, 7] * 100))


def test_plain_text_is_not_binary():
    assert not ex.looks_binary(b"a normal ascii log line\n1,2,3\n")
    assert not ex.looks_binary(b"")


def test_unicode_utf8_is_not_binary():
    # Heavy non-ASCII UTF-8 (Devanagari + accented Latin + currency) must NOT be
    # mistaken for binary just because its bytes are >= 0x80.
    sample = "करार की तारीख़ ₹4.20 crore — Échéance à-vis GSTR3B\n".encode("utf-8") * 20
    assert not ex.looks_binary(sample)


def test_extract_binary_log_reports_unsupported(tmp_path):
    p = tmp_path / "server.log"
    p.write_bytes(os.urandom(200_000))  # binary blob with a whitelisted extension
    out = ex.extract(p)
    assert out.startswith(ex.UNSUPPORTED_PREFIX)
    assert "binary" in out.lower()


def test_extract_real_unicode_text_still_works(tmp_path):
    p = tmp_path / "note.md"
    body = "# शीर्षक\nGoverning law is Delaware. Échéance 2026. ₹4.20 crore.\n"
    p.write_text(body, encoding="utf-8")
    assert "Delaware" in ex.extract(p)
    assert "शीर्षक" in ex.extract(p)


# --- Part B: sanitization -----------------------------------------------------

def test_sanitize_strips_control_chars_keeps_text_and_whitespace():
    dirty = "clean\x00text\x07here\twith\nnewlines\rand\x0cff and unicode café ₹"
    out = ex.sanitize_text(dirty)
    assert "\x00" not in out and "\x07" not in out
    assert "cleantexthere" in out          # control chars removed, letters kept
    assert "\t" in out and "\n" in out      # legitimate whitespace preserved
    assert "café" in out and "₹" in out     # unicode untouched


# --- Integration: a binary file in a real project ----------------------------

def test_binary_file_with_text_extension_is_not_indexed(project):
    # A binary blob named like a log — the exact shape that got sync stuck.
    (project.root / "dump.log").write_bytes(os.urandom(300_000))
    run_sync(project, quiet=True)

    statuses = read_extract_status(project)
    assert statuses["dump.log"]["status"] == "unsupported"

    # No garbage cache should have been written for it.
    cache = project.cache_path_for("dump.log")
    assert not cache.exists() or cache.stat().st_size == 0


def test_cached_text_has_no_control_characters(synced):
    # Every extracted cache must be clean text — no NULs / stray control chars,
    # regardless of which extractor produced it.
    ext_root = synced.extracted_dir
    checked = 0
    for dirpath, _, filenames in os.walk(ext_root):
        for fn in filenames:
            if not fn.endswith(".txt"):
                continue
            raw = (os.path.join(dirpath, fn))
            data = open(raw, "rb").read()
            assert b"\x00" not in data, f"NUL byte in cache {fn}"
            checked += 1
    assert checked > 0
