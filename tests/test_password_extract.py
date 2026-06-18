"""Password-protected PDF extraction, the secrets map, and quiet extractor logging.

Real-corpus shakedown (docdex-qa v0.4.0): the first sync over a real corpus failed to
read ~70 password-protected PDFs and flooded stderr with pdfminer/openpyxl warnings.
These tests pin down the fix in `docdex.extract`.
"""
from __future__ import annotations

import json
import logging

import pytest

from conftest import make_encrypted_pdf

from docdex import extract as ex


def test_candidate_passwords_matches_only_paths_containing_the_key():
    secrets = {"PW_SUNI0306": "SUNI0306", "Direct Tax": "taxpw"}
    assert ex.candidate_passwords("Misc/CC PW_SUNI0306/2023/stmt.pdf", secrets) == ["SUNI0306"]
    assert ex.candidate_passwords("Other/unrelated.pdf", secrets) == []


def test_candidate_passwords_empty_key_matches_everything():
    assert ex.candidate_passwords("any/where/x.pdf", {"": "global"}) == ["global"]


def test_read_secrets_reads_the_map(tmp_path):
    (tmp_path / ".docdex.secrets.json").write_text(json.dumps({"PW_X": "x"}), encoding="utf-8")
    assert ex.read_secrets(tmp_path) == {"PW_X": "x"}


def test_read_secrets_missing_or_corrupt_is_empty_not_a_crash(tmp_path):
    assert ex.read_secrets(tmp_path) == {}                       # no file
    (tmp_path / ".docdex.secrets.json").write_text("{ not json", encoding="utf-8")
    assert ex.read_secrets(tmp_path) == {}                       # corrupt -> empty, no raise


def test_extract_reads_a_password_protected_pdf(tmp_path):
    p = tmp_path / "locked.pdf"
    p.write_bytes(make_encrypted_pdf("SECRET42 quantum key distribution", "hunter2"))
    assert "SECRET42" in ex.extract_pdf(str(p), passwords=["hunter2"])
    assert "SECRET42" in ex.extract(p, passwords=["hunter2"])


def test_extract_pdf_raises_on_wrong_or_missing_password(tmp_path):
    p = tmp_path / "locked.pdf"
    p.write_bytes(make_encrypted_pdf("SECRET42", "hunter2"))
    with pytest.raises(Exception):
        ex.extract_pdf(str(p), passwords=["nope"])
    with pytest.raises(Exception):
        ex.extract_pdf(str(p))


def test_plain_pdf_still_extracts_without_passwords(tmp_path):
    from conftest import make_pdf
    p = tmp_path / "open.pdf"
    p.write_bytes(make_pdf("OPENPDF content here"))
    assert "OPENPDF" in ex.extract_pdf(str(p))


def test_pdfminer_warning_logger_is_quieted_on_import():
    assert logging.getLogger("pdfminer").getEffectiveLevel() >= logging.ERROR


def test_sync_uses_secrets_to_extract_a_locked_pdf(tmp_path):
    """End-to-end: a real sync reads a password-protected PDF via the secrets file."""
    from docdex.config import Project, ensure_state_dirs
    from docdex.sync import run_sync

    root = tmp_path / "proj"
    (root / "Statements").mkdir(parents=True)
    (root / "Statements" / "card PW_ACME99.pdf").write_bytes(
        make_encrypted_pdf("LOCKEDSTMT balance due 1234", "ACME99"))
    (root / ".docdex.secrets.json").write_text(
        json.dumps({"PW_ACME99": "ACME99"}), encoding="utf-8")

    project = Project.create(root, index_dir="_index")
    ensure_state_dirs(project)
    project.save()
    run_sync(project, quiet=True)

    cache = project.cache_path_for("Statements/card PW_ACME99.pdf")
    assert cache.exists(), "locked PDF was not extracted at all"
    assert "LOCKEDSTMT" in cache.read_text(encoding="utf-8")
