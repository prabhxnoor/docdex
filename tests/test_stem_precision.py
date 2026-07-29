"""Stemming must add recall WITHOUT destroying the ranking signal.

Regression (found 2026-07-29 by re-running the form benchmark against v0.5.0):
the `porter unicode61` tokenizer stems the index *and* the query, so a query
term's literal form is discarded in favour of its stem class. When the literal
form is highly selective but the stem class is common, the discriminating signal
is annihilated — on the benchmark corpus `"terms"` matched exactly 1 chunk (the
one carrying the answer) while its stem `"term"` matched 154 of 167, so BM25 IDF
collapsed to ~0, keyword-dense filler won on term frequency, and the
value-bearing chunk fell from rank 0 (score 3.19) to rank 96 — outside the
6-candidate field window, where the utility reranker could never see it.

The fix must keep the literal signal alongside the stemmed one. These tests pin
both halves of that contract: precision (a literal-selective term stays findable)
and the recall win stemming was added for (inflections still collide), so the fix
cannot be a quiet revert of stemming.
"""
from __future__ import annotations

import pytest

from docdex import index_db
from docdex.scaffold import run_init
from docdex.sync import run_sync

# Repeats the SINGULAR 'term' many times and never the plural 'terms', so the
# stem class is corpus-common while the literal plural stays unique to the answer.
FILLER = ("payment term renewal term contract term payment term budget term "
          "vendor term invoice term compliance term onboarding term ") * 3


@pytest.fixture
def stem_flood(tmp_path):
    """One chunk holds the answer under the literal plural label; 60 keyword-dense
    distractors flood the singular stem."""
    root = tmp_path / "flood"
    root.mkdir()
    (root / "answer.txt").write_text(
        "Payment terms are net-45 from the date of invoice.\n", encoding="utf-8")
    for i in range(60):
        (root / f"filler_{i:02d}.txt").write_text(FILLER + "\n", encoding="utf-8")
    project = run_init(root, quiet=True)
    run_sync(project, quiet=True)
    index_db.build(project, quiet=True)
    return project


def test_literal_selective_term_is_not_buried_by_its_stem_class(stem_flood):
    """'terms' occurs in exactly one chunk; its stem 'term' occurs in all 61.

    The chunk matching the literal term must rank inside the per-field candidate
    window (context.py uses pool=6), not be flooded out by the stem class.
    """
    hits = index_db.search(stem_flood, "Payment terms", limit=6)
    rels = [h["rel"] for h in hits]
    assert "answer.txt" in rels, (
        f"literal 'terms' match buried by the 'term' stem flood; top-6 was {rels}")


def test_form_field_value_survives_stem_flood(stem_flood):
    """End-to-end: the packet must still carry the field's actual value."""
    from docdex.context import build_packet
    packet = build_packet(stem_flood, "fill the vendor form", budget=3000,
                          form_fields=["Payment terms"])
    assert "net-45" in packet, (
        f"field value lost from the packet entirely; packet was:\n{packet}")


def test_search_survives_a_v2_database_without_the_exact_mirror(stem_flood):
    """A database written by v0.5.0 has only the stemmed mirror.

    `sync` rebuilds it at the new schema version, but a `search` issued *before*
    that must degrade to the stemmed mirror, not crash with 'no such table'.
    """
    import sqlite3
    conn = sqlite3.connect(str(stem_flood.index_db_path))
    conn.execute("DROP TABLE chunks_fts_exact")
    conn.execute("UPDATE meta SET value='2' WHERE key='schema'")
    conn.commit()
    conn.close()

    hits = index_db.search(stem_flood, "Payment terms", limit=6)
    assert hits, "search crashed or returned nothing on a pre-v3 database"


def test_stemmed_recall_still_bridges_inflection(tmp_path):
    """Guard against fixing precision by reverting stemming.

    'governing' must still find a document that only says 'governed' — the recall
    win v0.5.0 shipped for, and the reason Governing law started working.
    """
    root = tmp_path / "recall"
    root.mkdir()
    (root / "contract.txt").write_text(
        "This agreement is governed by the laws of Karnataka.\n", encoding="utf-8")
    (root / "other.txt").write_text("unrelated filler content\n", encoding="utf-8")
    project = run_init(root, quiet=True)
    run_sync(project, quiet=True)
    index_db.build(project, quiet=True)

    hits = index_db.search(project, "governing law", limit=6)
    assert any("contract.txt" in h["rel"] for h in hits), (
        "stemming no longer bridges governing->governed; the recall win regressed")
