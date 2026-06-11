"""Tests for the SQLite/FTS5 lexical engine (Workstream B)."""
from __future__ import annotations

import sqlite3

import pytest

from docdex import index_db
from docdex import tokens as tok
from docdex.sync import run_sync


def _has_fts5() -> bool:
    c = sqlite3.connect(":memory:")
    try:
        c.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        c.close()


requires_fts5 = pytest.mark.skipif(not _has_fts5(), reason="SQLite lacks FTS5")


@requires_fts5
def test_build_and_bm25_search(synced):
    result = index_db.build(synced, quiet=True)
    assert result["fts"] is True
    assert result["files"] > 0 and result["chunks"] > 0
    hits = index_db.search(synced, "ZEPHYRTOKEN")
    assert hits and hits[0]["rel"] == "Reports/Q1 report.md"
    assert hits[0]["tokens"] > 0


@requires_fts5
def test_bm25_beats_term_stuffing(tmp_path):
    """DDX-007 regression: a document repeating common words must not out-rank
    the one that actually contains all query terms including the rare one."""
    from docdex.scaffold import run_init
    proj = tmp_path / "p"
    (proj / "Docs").mkdir(parents=True)
    (proj / "Docs" / "stuffed.md").write_text(
        "alpha beta gamma " * 30, encoding="utf-8")
    (proj / "Docs" / "answer.md").write_text(
        "The answer is RAREWINNERTOKEN. alpha beta gamma once.", encoding="utf-8")
    project = run_init(proj, quiet=True)
    run_sync(project, quiet=True)
    index_db.build(project, quiet=True)

    hits = index_db.search(project, "alpha beta gamma RAREWINNERTOKEN", limit=3)
    assert hits[0]["rel"] == "Docs/answer.md"


@requires_fts5
def test_incremental_reindex(synced):
    index_db.build(synced, quiet=True)
    second = index_db.build(synced, quiet=True)
    assert second["reindexed"] == 0  # unchanged corpus does no work

    (synced.root / "Notes" / "hello.txt").write_text(
        "hello world plus FRESHMARK token", encoding="utf-8")
    run_sync(synced, quiet=True)
    third = index_db.build(synced, quiet=True)
    assert third["reindexed"] == 1
    assert index_db.search(synced, "FRESHMARK")[0]["rel"] == "Notes/hello.txt"


@requires_fts5
def test_deleted_file_drops_from_index(synced):
    index_db.build(synced, quiet=True)
    assert index_db.search(synced, "XANTHICWORD")  # docx present
    (synced.root / "Reports" / "board deck.docx").unlink()
    run_sync(synced, quiet=True)
    index_db.build(synced, quiet=True)
    assert index_db.search(synced, "XANTHICWORD") == []


@requires_fts5
def test_folder_filter_and_punctuation_query(synced):
    index_db.build(synced, quiet=True)
    assert index_db.search(synced, "ZEPHYRTOKEN", folder="Notes") == []
    assert index_db.search(synced, "ZEPHYRTOKEN", folder="Reports")
    # punctuation-only query has no terms -> empty, never a SQL error
    assert index_db.search(synced, "!!! ???") == []


def test_search_unavailable_before_build(synced):
    # No index.db yet -> callers get a clean signal to fall back.
    with pytest.raises(FileNotFoundError):
        index_db.search(synced, "anything")


def test_token_counter_fallback():
    assert tok.count_tokens("") == 0
    assert tok.count_tokens("a" * 40) >= 1
    chunks = list(tok.iter_chunks("word " * 1000))
    assert len(chunks) > 1
    assert all(c[2] for c in chunks)
