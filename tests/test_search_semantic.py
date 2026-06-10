from __future__ import annotations

import time

from docdex import semantic
from docdex.search import run_search
from docdex.sync import run_sync


def test_search_ranks_sentinel_first(synced):
    hits = run_search(synced, "ZEPHYRTOKEN", limit=5)
    assert hits, "expected at least one hit"
    assert hits[0][1] == "Reports/Q1 report.md"


def test_search_folder_filter(synced):
    assert run_search(synced, "ZEPHYRTOKEN", folder="Notes") == []
    assert run_search(synced, "ZEPHYRTOKEN", folder="Reports")


def test_search_no_match(synced):
    assert run_search(synced, "qqqqzzzz-not-present") == []


def test_search_reads_office_formats(synced):
    assert run_search(synced, "XANTHICWORD")[0][1] == "Reports/board deck.docx"
    assert run_search(synced, "QUARKPDF")[0][1] == "Notes/real.pdf"


def test_semantic_build_and_search(synced):
    meta = semantic.build(synced, quiet=True)
    assert meta["files"] > 0 and meta["chunks"] > 0
    hits = semantic.search(synced, "quantum key distribution", limit=5)
    assert hits
    assert any(row["path"] == "Notes/real.pdf" for _, row in hits)


def test_semantic_incremental_rebuild(synced, monkeypatch):
    calls = []
    real_embed = semantic.embed

    def counting_embed(text):
        calls.append(1)
        return real_embed(text)

    monkeypatch.setattr(semantic, "embed", counting_embed)

    semantic.build(synced, quiet=True)
    first_build = len(calls)
    assert first_build > 0

    calls.clear()
    meta = semantic.build(synced, quiet=True)
    assert len(calls) == 0, "unchanged corpus must not re-embed anything"
    assert meta["embedded_files"] == 0

    target = synced.root / "Reports" / "Q1 report.md"
    time.sleep(0.02)
    target.write_text("# Q1 report\nrevised content ZEPHYRTOKEN again\n" + "filler " * 30,
                      encoding="utf-8")
    run_sync(synced, quiet=True)
    calls.clear()
    meta = semantic.build(synced, quiet=True)
    assert meta["embedded_files"] == 1
    assert 0 < len(calls) < first_build


def test_semantic_force_rebuilds_all(synced, monkeypatch):
    semantic.build(synced, quiet=True)
    calls = []
    real_embed = semantic.embed
    monkeypatch.setattr(semantic, "embed",
                        lambda t: (calls.append(1), real_embed(t))[1])
    meta = semantic.build(synced, force=True, quiet=True)
    assert len(calls) == meta["chunks"]
