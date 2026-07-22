"""Tests for the v0.5.0 utility reranker (M1 piece 3).

The evidence candidate pool is ordered by task UTILITY (value-bearing, then
term coverage, then BM25, then a stable path tiebreak) instead of raw BM25, so
a chunk that carries a labelled value and covers more query terms ranks above
one that merely repeats a query word. Reranking changes ORDER only.
"""
from __future__ import annotations

import pytest

from docdex import context as ctxmod
from docdex import index_db
from docdex.scaffold import run_init
from docdex.sync import run_sync


@pytest.fixture
def payment_corpus(tmp_path):
    """Two docs for a "payment" query: one value-bearing, one keyword-stuffed
    (higher raw term frequency, no value)."""
    root = tmp_path / "corp"
    root.mkdir(parents=True)
    (root / "a.txt").write_text("Payment: 4.2 crore\n", encoding="utf-8")
    (root / "b.txt").write_text(
        "payment payment payment payment payment\n", encoding="utf-8")
    project = run_init(root, quiet=True)
    run_sync(project, quiet=True)
    index_db.build(project, quiet=True)
    return project


def _evidence_lines(packet: str):
    return [l for l in packet.splitlines() if l.startswith("[E")]


def test_value_bearing_chunk_outranks_keyword_stuffed(payment_corpus):
    packet = ctxmod.build_packet(payment_corpus, "payment", budget=2000)
    ev = _evidence_lines(packet)
    assert ev, "expected at least one Evidence line"
    # a.txt carries a value for the query term; b.txt only repeats the word with
    # a higher raw BM25. The value-bearing chunk must pack first.
    assert "a.txt" in ev[0], f"first evidence line was: {ev[0]!r}"
    # And its concrete value surfaces as an answer.
    assert "4.2 crore" in packet


def test_utility_prefers_coverage():
    """At equal value-bearing and score, broader term coverage sorts first."""
    terms = {"payment", "vendor"}
    high = {"text": "vendor payment details", "score": 1.0, "rel": "z.txt", "chunk": 0}
    low = {"text": "payment details only", "score": 1.0, "rel": "a.txt", "chunk": 0}
    # Neither carries a value → equal value-bearing; equal score. Coverage decides.
    assert ctxmod._utility(high, terms) < ctxmod._utility(low, terms)


def test_value_bearing_beats_coverage_and_score():
    """value-bearing is the FIRST key: a chunk with a value for a term outranks a
    higher-coverage, higher-score chunk that carries no value."""
    terms = {"payment", "vendor"}
    valued = {"text": "Payment: 4.2 crore", "score": 0.1, "rel": "a.txt", "chunk": 0}
    prose = {"text": "vendor payment discussion notes", "score": 9.9, "rel": "b.txt", "chunk": 0}
    assert ctxmod._utility(valued, terms) < ctxmod._utility(prose, terms)


def test_rerank_is_deterministic(payment_corpus):
    p1 = ctxmod.build_packet(payment_corpus, "payment", budget=2000)
    p2 = ctxmod.build_packet(payment_corpus, "payment", budget=2000)
    assert _evidence_lines(p1) == _evidence_lines(p2)
    assert p1 == p2


def test_explain_reports_utility_ranking(payment_corpus):
    packet = ctxmod.build_packet(payment_corpus, "payment", budget=2000, explain=True)
    assert "- ranking: utility (value-bearing · coverage · bm25)" in packet
