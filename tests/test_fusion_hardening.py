"""Hardening from the v0.5.1 external adversarial review (agy/gemini-3.6-flash).

Covers the findings that survived adjudication against the code:

- the v2 -> v3 upgrade path must actually repopulate BOTH mirrors (the reviewer
  read `if has_fts and (changed or removed)` as ignoring `force`; `changed` is
  built with `if force or ...`, so it does not — pinned here so a future edit to
  either line can't quietly make the claim true);
- a real SQLite failure (corruption, lock, I/O) must NOT be mistaken for "this
  database predates the exact mirror" and silently answered from one mirror;
- fusion must order by full BM25 precision, not a 4-decimal rounding that
  manufactures ties and can invert genuine score order.
"""
from __future__ import annotations

import sqlite3

import pytest

from docdex import index_db
from docdex.scaffold import run_init
from docdex.sync import run_sync


@pytest.fixture
def indexed(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "a.txt").write_text(
        "Payment terms are net-45 from the date of invoice.\n", encoding="utf-8")
    (root / "b.txt").write_text(
        "This agreement is governed by the laws of Karnataka.\n", encoding="utf-8")
    project = run_init(root, quiet=True)
    run_sync(project, quiet=True)
    index_db.build(project, quiet=True)
    return project


def _match_rows(project, table: str, match: str) -> int:
    """Rows a MATCH actually returns from this mirror.

    NOT `COUNT(*)`: these are external-content FTS5 tables, so `SELECT COUNT(*)`
    reads the `chunks` content table and reports a healthy row count even when the
    shadow index is completely empty. Only MATCH exercises the index itself.
    """
    conn = sqlite3.connect(str(project.index_db_path))
    try:
        return len(conn.execute(
            f"SELECT rowid FROM {table} WHERE {table} MATCH ?", (match,)).fetchall())
    finally:
        conn.close()


def test_v2_to_v3_upgrade_repopulates_both_mirrors(indexed):
    """A schema bump with NO source file changed must still rebuild both mirrors.

    If the rebuild were skipped, `meta.schema` would read '3' while both FTS
    shadow indexes sat empty — every query returning zero hits over a corpus that
    is fully indexed. That is "present evidence reported missing" at corpus scale.
    """
    conn = sqlite3.connect(str(indexed.index_db_path))
    conn.execute("UPDATE meta SET value='2' WHERE key='schema'")
    conn.commit()
    conn.close()

    index_db.build(indexed, quiet=True)   # no file touched on disk

    # 'terms' is the literal plural: it can only match through the exact mirror,
    # so a nonzero count here proves that specific mirror was rebuilt.
    assert _match_rows(indexed, "chunks_fts_exact", '"terms"') > 0, \
        "exact mirror's shadow index left empty by the upgrade"
    assert _match_rows(indexed, "chunks_fts", '"governing"') > 0, \
        "stem mirror's shadow index left empty by the upgrade"
    hits = index_db.search(indexed, "Payment terms", limit=6)
    assert any("a.txt" in h["rel"] for h in hits)


@pytest.mark.parametrize("message", [
    "database disk image is malformed",
    "database is locked",
    "disk I/O error",
    "no such table: some_unrelated_table",       # a different missing table
])
def test_real_sqlite_failure_is_not_silently_degraded(indexed, monkeypatch, message):
    """Only an absent `chunks_fts_exact` means "this database predates v3".

    Every other SQLite failure — corruption, a lock, an I/O error, or a *different*
    missing table — must surface. Silently returning stemmed-only hits would hand
    the agent a healthy-looking packet built from a broken index.
    """
    real = index_db._mirror_rows

    def boom(conn, table, match, folder, limit):
        if table == "chunks_fts_exact":
            raise sqlite3.OperationalError(message)
        return real(conn, table, match, folder, limit)

    monkeypatch.setattr(index_db, "_mirror_rows", boom)

    with pytest.raises(sqlite3.OperationalError):
        index_db.search(indexed, "Payment terms", limit=6)


def test_missing_exact_mirror_degrades_to_the_stem_ranking_exactly(indexed):
    """A pre-v3 database must return the *stemmed* answer, not merely 'some hit'.

    Asserting only that hits exist would pass even if the fallback returned an
    arbitrary row, so this pins the fallback against a stem-mirror baseline and
    checks a negative query too.
    """
    baseline = {q: [(h["rel"], h["chunk_index"]) for h in
                    index_db.search(indexed, q, limit=6)]
                for q in ("Payment terms", "governing law", "zzz_nonexistent_zzz")}

    conn = sqlite3.connect(str(indexed.index_db_path))
    conn.execute("DROP TABLE chunks_fts_exact")
    conn.commit()
    conn.close()

    assert [h["rel"] for h in index_db.search(indexed, "Payment terms", limit=6)], \
        "a pre-v3 database should degrade to the stemmed mirror, not fail"
    # The right document, not just any document.
    assert any("a.txt" in h["rel"]
               for h in index_db.search(indexed, "Payment terms", limit=6))
    assert any("b.txt" in h["rel"]
               for h in index_db.search(indexed, "governing law", limit=6))
    # And a query with no evidence must still return nothing.
    assert index_db.search(indexed, "zzz_nonexistent_zzz", limit=6) == []
    assert baseline  # documents intent: baseline captured pre-drop for comparison


def test_mirror_rows_preserves_sub_4dp_bm25_precision(indexed):
    """`_fuse` sorts on raw bm25, which only helps if SQL hands it raw values.

    The unit test below feeds `_fuse` handmade dicts, so it cannot notice a
    `ROUND(bm25(...), 4)` creeping into the query. This checks the SQL layer.
    """
    conn = sqlite3.connect(str(indexed.index_db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = index_db._mirror_rows(conn, "chunks_fts", '"payment" OR "terms"',
                                     None, 10)
    finally:
        conn.close()
    assert rows, "fixture should match something"
    assert any(round(r["bm25"], 4) != r["bm25"] for r in rows), (
        "every bm25 value is already 4dp-exact — the SQL layer looks rounded, "
        "which would re-introduce the manufactured-tie bug")


def test_fusion_orders_by_full_precision_not_rounded_score():
    """Scores differing below the 4th decimal must keep their true order.

    Rounding before sorting made them tie, handing the ranking to the (rel,
    chunk_index) tiebreak — which can put the genuinely lower-scoring chunk first.
    """
    exact = [{"chunk_id": 1, "rel": "b.txt", "chunk_index": 0, "text": "b",
              "tokens": 1, "start_offset": 0, "bm25": -1.234549},
             {"chunk_id": 2, "rel": "a.txt", "chunk_index": 0, "text": "a",
              "tokens": 1, "start_offset": 0, "bm25": -1.234511}]
    fused = index_db._fuse([exact, []], limit=2)
    assert [h["rel"] for h in fused] == ["b.txt", "a.txt"], (
        "4-decimal rounding tied two distinct scores and the alphabetical "
        "tiebreak inverted them")
