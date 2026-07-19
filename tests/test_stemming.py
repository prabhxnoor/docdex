"""Stemming: morphological variants must collide, but identifiers / amounts /
non-English text must stay literal (recall without breaking exact-answer honesty).
"""
from __future__ import annotations

from docdex.stemming import stem


def test_inflections_collide_to_one_stem():
    assert stem("governing") == stem("governed") == stem("governs")
    assert stem("deal") == stem("deals")
    assert stem("close") == stem("closed")


def test_stem_is_deterministic():
    # Single-pass canonical Porter: deterministic (same input -> same stem), but
    # NOT idempotent, so we assert determinism, not stem(stem(x)) == stem(x).
    for w in ("governing", "organizations", "closed", "happiness",
              "organisation", "provisional"):
        assert stem(w) == stem(w)
    # Document the non-idempotency explicitly so no future author assumes it:
    assert stem("organisation") != stem(stem("organisation"))


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


from docdex.search import run_search, score_text, stemmed, tokenize


def test_stemmed_collides_variants():
    assert stemmed("the deal was governed") >= {stem("governing"), stem("deals")}


def test_score_text_matches_stem_variant():
    q = "governing"
    text = "the agreement is governed by law"
    assert score_text("contract.txt", text, q, tokenize(q)) > 0


def test_fallback_search_finds_inflected_variant(tmp_path):
    # run_search is the no-FTS5 path: it reads caches directly, no index_db build.
    from docdex.scaffold import run_init
    from docdex.sync import run_sync
    root = tmp_path / "fb"
    root.mkdir()
    (root / "contract.txt").write_text(
        "This agreement is governed by the laws of Delaware.\n", encoding="utf-8")
    project = run_init(root, quiet=True)
    run_sync(project, quiet=True)
    hits = run_search(project, "governing")
    assert any("contract.txt" in rel for _score, rel, _cache, _snip in hits)


from docdex import context as ctxmod


def test_context_surfaces_inflected_evidence(stem_project):
    packet = ctxmod.build_packet(stem_project, "governing agreement", budget=1500)
    assert "contract.txt" in packet


def test_approx_evidence_is_tagged(stem_project):
    # "governing" only matches "governed" via stem -> approximate, tagged + legend.
    packet = ctxmod.build_packet(stem_project, "governing agreement", budget=1500)
    assert "~approx" in packet
    assert "matched by word stem" in packet


def test_exact_evidence_is_not_tagged_approx(stem_project):
    # The document literally contains "closed" -> exact, no ~approx legend.
    packet = ctxmod.build_packet(stem_project, "closed deals", budget=1500)
    assert "matched by word stem" not in packet


def test_explain_lists_query_stems(stem_project):
    packet = ctxmod.build_packet(stem_project, "governing agreement",
                                 budget=1500, explain=True)
    assert "stems:" in packet
    assert "govern" in packet


def test_literal_amount_survives_stemming(stem_project):
    # An exact amount must appear byte-identical in the packet (never merged/altered).
    packet = ctxmod.build_packet(stem_project, "invoice total amount due",
                                 budget=1500)
    assert "42,000,000" in packet


def test_stem_only_hit_not_listed_missing(stem_project):
    # "governing" matches only via the stem of "governed"; it is surfaced as
    # ~approx evidence, so it must NOT also be reported as an unmatched term.
    packet = ctxmod.build_packet(stem_project, "governing agreement", budget=1500)
    assert "~approx" in packet                 # it did surface via stem
    assert "no index hits for" not in packet   # ...so it is not "missing"


def test_scaffold_explains_approx_tag(tmp_path):
    from docdex.scaffold import run_init
    root = tmp_path / "scaf"
    root.mkdir()
    run_init(root, quiet=True)
    claude = (root / "CLAUDE.md").read_text(encoding="utf-8")
    agents = (root / "AGENTS.md").read_text(encoding="utf-8")
    assert "~approx" in claude
    assert "~approx" in agents
