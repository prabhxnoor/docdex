"""Stemming: morphological variants must collide, but identifiers / amounts /
non-English text must stay literal (recall without breaking exact-answer honesty).
"""
from __future__ import annotations

from docdex.stemming import stem


def test_inflections_collide_to_one_stem():
    assert stem("governing") == stem("governed") == stem("governs")
    assert stem("deal") == stem("deals")
    assert stem("close") == stem("closed")


def test_stem_is_idempotent():
    for w in ("governing", "organizations", "closed", "happiness"):
        assert stem(stem(w)) == stem(w)


def test_stem_lowercases_plain_words():
    assert stem("Governing") == stem("governing")


def test_identifiers_and_amounts_are_never_stemmed():
    # Anything with a digit is an identifier/amount/date — returned unchanged.
    for tok in ("gstr3b", "42000000", "27abcde1234f1z5", "31"):
        assert stem(tok) == tok.lower()


def test_non_ascii_is_never_stemmed():
    for tok in ("échéance", "naïve", "शीर्षक", "café"):
        assert stem(tok) == tok.lower()


import pytest

from docdex import index_db


@pytest.fixture
def stem_project(tmp_path):
    """An initialized, synced, FTS-indexed project with inflected content."""
    from docdex.scaffold import run_init
    from docdex.sync import run_sync
    root = tmp_path / "stemproj"
    root.mkdir()
    (root / "contract.txt").write_text(
        "This agreement is governed by the laws of Delaware. "
        "The parties closed forty deals this quarter.\n", encoding="utf-8")
    (root / "ledger.txt").write_text(
        "Invoice GSTR3B total INR 42,000,000 due 31/12/2026.\n", encoding="utf-8")
    project = run_init(root, quiet=True)
    run_sync(project, quiet=True)
    index_db.build(project, quiet=True)
    return project


def test_fts_matches_inflected_variant(stem_project):
    # Query "governing"; the document says "governed" — porter tokenizer collides.
    hits = index_db.search(stem_project, "governing")
    assert any("contract.txt" in h["rel"] for h in hits)


def test_schema_bumped_to_v2(stem_project):
    import sqlite3
    conn = sqlite3.connect(str(stem_project.index_db_path))
    try:
        val = conn.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
    finally:
        conn.close()
    assert val[0] == "2"


def test_stale_schema_forces_rebuild(stem_project):
    # Simulate an old (v1, non-porter) index, then rebuild and confirm the
    # porter recall works and the schema is upgraded — no user action.
    import sqlite3
    conn = sqlite3.connect(str(stem_project.index_db_path))
    conn.execute("UPDATE meta SET value='1' WHERE key='schema'")
    conn.commit()
    conn.close()
    index_db.build(stem_project, quiet=True)
    hits = index_db.search(stem_project, "governing")
    assert any("contract.txt" in h["rel"] for h in hits)
