"""Tests for v0.3.0 Phase-2: the coverage-aware, budget-honest context packet
(audit findings DDX-018/019/020) plus basic conflict awareness."""
from __future__ import annotations

import os
import time

from docdex import context as ctxmod
from docdex import index_db
from docdex.context import build_packet, parse_form_fields
from docdex.scaffold import run_init
from docdex.sync import run_sync


def _project_with(tmp_path, files: dict):
    root = tmp_path / "corpus"
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    project = run_init(root, quiet=True)
    run_sync(project, quiet=True)
    index_db.build(project, quiet=True)
    return project


# ---- DDX-018: budget honesty -------------------------------------------
def test_nonpositive_budget_retrieves_nothing_and_says_so(tmp_path):
    project = _project_with(tmp_path, {
        "Contracts/acme.md": "The liability cap is INR 4.2 crore under this MSA.\n"})
    packet = build_packet(project, "liability cap", budget=0)
    assert "not positive" in packet
    assert "## Dropped (budget)" in packet
    assert "no evidence packed" in packet
    # honest accounting: requested shown verbatim
    assert "0 requested" in packet


def test_tiny_budget_flags_truncation(tmp_path):
    project = _project_with(tmp_path, {
        "a.md": "alpha liability cap is 5 crore in the agreement here today.\n",
        "b.md": "beta payment terms are net 45 days from the invoice date here.\n",
        "c.md": "gamma governing law clause names the courts of Bengaluru here.\n"})
    packet = build_packet(project, "liability payment governing terms", budget=60)
    # something was dropped, and the packet must say so (not look complete)
    assert "## Dropped (budget)" in packet or "truncated by budget" in packet


# ---- coverage header ----------------------------------------------------
def test_coverage_header_is_present(tmp_path):
    project = _project_with(tmp_path, {
        "x.md": "annual revenue for the year was 12 crore total.\n"})
    packet = build_packet(project, "annual revenue", budget=1500)
    cov = [l for l in packet.splitlines() if l.startswith("Coverage:")]
    assert cov and "value answer" in cov[0]


def test_form_coverage_counts_found_and_missing(tmp_path):
    project = _project_with(tmp_path, {
        "f.md": "GST number: 29ABCDE1234F1Z5. Liability cap: 4.2 crore.\n"})
    packet = build_packet(project, "fill form", budget=2000,
                          form_fields=["GST number", "Liability cap", "Bank IFSC"])
    cov = [l for l in packet.splitlines() if l.startswith("Coverage:")][0]
    assert "3 fields" in cov and "missing" in cov
    assert "Bank IFSC: not found" in packet


# ---- conflict awareness (M2 seed) --------------------------------------
def test_conflicting_sources_are_flagged_newer_first(tmp_path):
    root = tmp_path / "corpus"
    (root / "Sales").mkdir(parents=True)
    (root / "Sales" / "q1.md").write_text(
        "Q1 pipeline review: we closed 30 deals across regions.\n", encoding="utf-8")
    (root / "Sales" / "q2.md").write_text(
        "Q2 update: the team closed 40 deals in total.\n", encoding="utf-8")
    now = time.time()
    os.utime(root / "Sales" / "q1.md", (now - 1000, now - 1000))  # q1 older
    os.utime(root / "Sales" / "q2.md", (now, now))                # q2 newer
    project = run_init(root, quiet=True)
    run_sync(project, quiet=True)
    index_db.build(project, quiet=True)

    packet = build_packet(project, "how many deals did we close", budget=2000)
    assert "## Conflicts" in packet
    conf = [l for l in packet.splitlines() if "(newest)" in l][0]
    assert "40" in conf.split("(newest)")[0]   # the newer value is marked newest
    assert "30" in conf                         # the older value is still shown


# ---- DDX-019: freshness must be cheap by default -----------------------
def test_freshness_does_not_walk_corpus_by_default(tmp_path, monkeypatch):
    project = _project_with(tmp_path, {"x.md": "some indexed content here today.\n"})
    import docdex.sync as syncmod
    calls = []
    orig = syncmod.compute_status
    monkeypatch.setattr(syncmod, "compute_status",
                        lambda p: (calls.append(1), orig(p))[1])
    p1 = build_packet(project, "indexed content", budget=1000)
    assert not calls and "not re-checked" in p1        # no walk by default
    build_packet(project, "indexed content", budget=1000, check_freshness=True)
    assert len(calls) == 1                              # walk only when asked


# ---- DDX-020: form parsing — Unicode + no silent 40-cap ----------------
def test_form_parsing_handles_unicode_and_many_fields():
    text = ("Échéance: ____\nNuméro fiscal: ____\n"
            + "".join(f"Field {i}: __\n" for i in range(50)))
    fields = parse_form_fields(text)
    assert "Échéance" in fields
    assert "Numéro fiscal" in fields
    assert len(fields) > 40           # the old hard 40-cap is gone


def test_scaffold_agent_docs_never_cited_as_evidence(tmp_path):
    # init writes CLAUDE.md / AGENTS.md at the root; they describe docdex, not the
    # corpus, so a query that matches their text must not surface them as evidence.
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "data.md").write_text("The annual revenue figure was 12 crore.\n",
                                  encoding="utf-8")
    project = run_init(root, quiet=True)        # creates CLAUDE.md + AGENTS.md
    run_sync(project, quiet=True)
    index_db.build(project, quiet=True)
    packet = build_packet(project, "context budget agent sync", budget=2000)
    assert "CLAUDE.md" not in packet
    assert "AGENTS.md" not in packet


def test_form_field_answers_do_not_cross_contaminate(tmp_path):
    # two fields' values live in one file/line; each answer must be its own value
    project = _project_with(tmp_path, {
        "reg.md": "GST number: 29ABCDE1234F1Z5. Liability cap: 4.2 crore.\n"})
    packet = build_packet(project, "fill form", budget=2000,
                          form_fields=["GST number", "Liability cap"])
    ans = [l for l in packet.splitlines() if l.startswith("- ")]
    gst = [l for l in ans if l.startswith("- GST number:")][0]
    cap = [l for l in ans if l.startswith("- Liability cap:")][0]
    assert "29ABCDE1234F1Z5" in gst and "crore" not in gst   # GST line is the GST value
    assert "4.2 crore" in cap and "29ABCDE" not in cap        # cap line is the cap value


def test_empty_task_still_raises(tmp_path):
    project = _project_with(tmp_path, {"x.md": "content\n"})
    import pytest
    with pytest.raises(ctxmod.EmptyTask):
        build_packet(project, "!!! ???")
