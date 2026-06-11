"""Regression tests for v0.4.0 Phase-3 packet-trust fixes (audit DDX-028..038).

Each test reproduces a failure the independent v0.3.0 audit reported, then
asserts the fixed behavior. The v0.3 lesson was that clean, one-value-per-
sentence corpora hid these bugs, so these fixtures are deliberately adversarial:
symlinked index dirs, semicolon-dense forms, shared labels, Unicode evidence,
score-0 FTS hits, and corrupt inventory alongside a healthy index.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from docdex import index_db
from docdex.context import build_packet, parse_form_fields
from docdex.scaffold import run_init, run_purge
from docdex.search import tokenize
from docdex.sync import run_sync

_SRC = str(Path(__file__).resolve().parents[1] / "src")


def _cli(root, *argv):
    return subprocess.run(
        [sys.executable, "-m", "docdex", *argv], cwd=str(root),
        env=dict(os.environ, PYTHONPATH=_SRC),
        capture_output=True, text=True, timeout=300)


def _project_with(tmp_path, files: dict):
    """Build + index a throwaway corpus from {rel: text}."""
    root = tmp_path / "corpus"
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    project = run_init(root, quiet=True)
    run_sync(project, quiet=True)
    index_db.build(project, quiet=True)
    return project


# ---- DDX-028: `purge --state-only` must stay confined to the project --------
def _relink_index_outside(project, tmp_path, name="outside_target"):
    """Move the real index dir outside the project and leave a symlink behind,
    mirroring the audit's state-only purge repro."""
    outside = tmp_path / name
    shutil.move(str(project.index_dir), str(outside))
    project.index_dir.symlink_to(outside, target_is_directory=True)
    return outside


def test_purge_state_only_refuses_symlinked_index_dir(tmp_path):
    proj = tmp_path / "proj"
    project = run_init(proj, quiet=True)
    outside = _relink_index_outside(project, tmp_path)
    sentinel = outside / "_state" / "DO_NOT_DELETE.txt"
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text("keep me", encoding="utf-8")

    rc = run_purge(project, yes=True, state_only=True, quiet=True)

    assert rc != 0, "state-only purge should refuse a symlinked index dir"
    assert sentinel.exists(), "state-only purge deleted state through the symlink"
    assert (outside / "_state").exists()


def test_purge_full_refuses_symlinked_index_dir(tmp_path):
    # the same shared confinement guard governs a full purge.
    proj = tmp_path / "proj"
    project = run_init(proj, quiet=True)
    outside = _relink_index_outside(project, tmp_path)
    sentinel = outside / "_state" / "DO_NOT_DELETE.txt"
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text("keep me", encoding="utf-8")

    rc = run_purge(project, yes=True, quiet=True)

    assert rc != 0
    assert sentinel.exists()
    assert (outside / "_state").exists()


# ---- DDX-034: one Unicode-aware tokenizer everywhere -----------------------
def test_tokenize_keeps_unicode_words_whole():
    # the old ASCII tokenizer split "Échéance" into ["ch", "ance"].
    assert tokenize("Échéance") == ["échéance"]
    assert "numéro" in tokenize("Numéro fiscal")


def test_context_finds_unicode_evidence(tmp_path):
    project = _project_with(tmp_path, {
        "f.md": "Échéance: 31/12/2026 is the contract due date.\n"})
    packet = build_packet(project, "Échéance", budget=2000)
    assert "31/12/2026" in packet
    assert "no index hits" not in packet


# ---- DDX-029: field-local value extraction (no cross-field leakage) ---------
def test_field_answer_does_not_steal_shared_word_neighbour(tmp_path):
    # "Renewal term" must not be answered with "Payment terms"' value just
    # because "term" is a substring of "terms".
    project = _project_with(tmp_path, {
        "v.md": ("Payment terms: net 45 days from invoice date. "
                 "Renewal term is mentioned in the summary but no value is given.\n")})
    packet = build_packet(project, "fill form", budget=3000,
                          form_fields=["Renewal term", "Payment terms"])
    lines = packet.splitlines()
    # only the Renewal *answer/weak* line, not the raw evidence excerpt
    renewal = [l for l in lines if l.startswith("- Renewal term")]
    assert renewal and not any("net 45" in l for l in renewal), renewal
    assert any(l.startswith("- Payment terms:") and "net 45" in l for l in lines)


def test_dense_multilabel_line_does_not_contaminate(tmp_path):
    project = _project_with(tmp_path, {
        "reg.md": ("Vendor name: Acme Corp; GST number: 29ABCDE1234F1Z5; "
                   "PAN: ZXCVB9876K; CIN: U72900KA2016PTC096629; "
                   "Liability cap: 4.2 crore.\n")})
    fields = ["Vendor name", "GST number", "PAN", "CIN", "Liability cap"]
    packet = build_packet(project, "fill form", budget=5000, form_fields=fields)
    ans = [l for l in packet.splitlines() if l.startswith("- ")]
    gst = [l for l in ans if l.startswith("- GST number:")]
    assert gst and "29ABCDE1234F1Z5" in gst[0]
    assert "PAN" not in gst[0] and "Liability" not in gst[0] and "Vendor" not in gst[0]
    pan = [l for l in ans if l.startswith("- PAN:")]
    assert pan and "ZXCVB9876K" in pan[0] and "GST" not in pan[0]


# ---- DDX-030: present facts must not be reported missing at display score 0 -
def test_present_facts_not_reported_missing_at_score_zero(tmp_path):
    # the query term is in every file, so BM25 IDF -> 0 and the display score
    # rounds to 0.0; `search` finds them but `context` used to filter them out.
    project = _project_with(tmp_path, {
        "q1.md": "Deals: 30.\n", "q2.md": "Deals: 40.\n",
        "q3.md": "Deals: 35.\n", "q4.md": "Deals: 50.\n"})
    rows = index_db.search(project, "deals", limit=5)
    assert rows and all(r["score"] == 0.0 for r in rows), rows
    packet = build_packet(project, "deals", budget=2000)
    assert "no index hits" not in packet
    assert "no evidence packed" not in packet
    assert "deals" in packet.lower()


# ---- DDX-031/032: conflict newest-per-value + equivalent-amount handling ----
def test_conflict_marks_genuinely_newest_source(tmp_path):
    root = tmp_path / "corpus"
    (root / "sales").mkdir(parents=True)
    (root / "sales" / "samefile.md").write_text(
        "Old note: we closed 30 deals. Newer note: we closed 40 deals.\n",
        encoding="utf-8")
    (root / "sales" / "q3.md").write_text("We closed 30 deals.\n", encoding="utf-8")
    (root / "sales" / "q2.md").write_text("We closed 40 deals.\n", encoding="utf-8")
    now = time.time()
    os.utime(root / "sales" / "samefile.md", (now - 3000, now - 3000))  # oldest
    os.utime(root / "sales" / "q3.md", (now - 2000, now - 2000))
    os.utime(root / "sales" / "q2.md", (now, now))                      # newest 40
    project = run_init(root, quiet=True)
    run_sync(project, quiet=True)
    index_db.build(project, quiet=True)

    packet = build_packet(project, "how many deals did we close", budget=3000)
    conf = [l for l in packet.splitlines() if "(newest)" in l]
    assert conf, packet
    # value 40 must be attributed to its genuinely newest source q2.md.
    assert "q2.md (newest)" in conf[0], conf
    assert "40" in conf[0].split("(newest)")[0]


def test_equivalent_amounts_do_not_false_conflict(tmp_path):
    project = _project_with(tmp_path, {
        "a.md": "Liability cap: INR 4.2 crore.\n",
        "b.md": "Liability cap: 4.2 crore.\n",
        "c.md": "Liability cap: ₹4.20 cr.\n",
        "d.md": "Liability cap: 42,000,000.\n"})
    packet = build_packet(project, "fill form", budget=3000,
                          form_fields=["Liability cap"])
    assert "Liability cap: not found" not in packet, packet  # DDX-030: score 0
    assert "## Conflicts" not in packet, packet               # DDX-032: same amount
    # and the currency value is not truncated to a bare "₹4".
    assert "₹4 " not in packet and "₹4." not in packet.replace("₹4.20", "")


# ---- DDX-033: token-exact budget accounting + honest over-budget signal ----
def test_tiny_freetext_budget_signals_over_budget(tmp_path):
    import re as _re
    from docdex import tokens as _tok
    project = _project_with(tmp_path, {
        "v.md": "Liability cap: INR 4.2 crore. Payment terms: net 45 days.\n"})
    packet = build_packet(project, "liability cap payment terms", budget=1)
    assert "## Dropped (budget)" in packet, packet
    assert "over budget" in packet or "larger than" in packet, packet
    m = _re.search(r"~(\d+) used", packet)
    assert m and int(m.group(1)) > 1
    # reported `used` matches the real rendered token count (no big undercount).
    assert abs(_tok.count_tokens(packet) - int(m.group(1))) <= 3, (
        _tok.count_tokens(packet), m.group(1))


def test_ample_budget_has_no_over_budget_noise(tmp_path):
    project = _project_with(tmp_path, {"v.md": "Annual revenue was 12 crore.\n"})
    packet = build_packet(project, "annual revenue", budget=3000)
    assert "## Dropped (budget)" not in packet
    assert "over budget" not in packet


# ---- DDX-035: corrupt inventory must be surfaced, not hidden ----------------
def test_corrupt_inventory_is_surfaced_not_hidden(tmp_path):
    project = _project_with(tmp_path, {"f.md": "Inventory corruption token here.\n"})
    # corrupt the inventory header; the FTS index.db is left intact.
    project.inventory_path.write_text("path\tsize\nragged\n", encoding="utf-8")
    packet = build_packet(project, "Inventory corruption token", budget=1000)
    assert "⚠" in packet, packet
    assert "sync" in packet.lower()
    # must NOT present a confident, healthy "indexed ... not re-checked" line.
    assert "not re-checked" not in packet


# ---- DDX-036: a user-edited scaffold file is real content, not hidden -------
def test_user_edited_scaffold_is_surfaced(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "data.md").write_text("Ordinary corpus content.\n", encoding="utf-8")
    project = run_init(root, quiet=True)       # writes scaffold + fingerprints
    (root / "CLAUDE.md").write_text(
        "Real user note: CLAUDEONLY88 value is 88 crore.\n", encoding="utf-8")
    run_sync(project, quiet=True)
    index_db.build(project, quiet=True)
    packet = build_packet(project, "CLAUDEONLY88", budget=1000)
    assert "CLAUDEONLY88" in packet, packet
    assert "no index hits" not in packet


def test_unedited_scaffold_still_not_cited(tmp_path):
    project = _project_with(tmp_path, {"data.md": "Revenue was 12 crore.\n"})
    packet = build_packet(project, "context budget sync agent docdex", budget=2000)
    assert "AGENTS.md" not in packet
    assert "CLAUDE.md" not in packet


# ---- DDX-037: a zero-field form file must not run as a filename query -------
def test_zero_field_form_file_errors(tmp_path):
    project = _project_with(tmp_path, {"data.md": "Some ordinary content here.\n"})
    form = project.root / "empty_form.md"
    form.write_text("just prose with no labels in this paragraph at all.\n",
                    encoding="utf-8")
    r = _cli(project.root, "context", "--from-file", str(form), "--budget", "1000")
    assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
    assert "field" in (r.stdout + r.stderr).lower()


# ---- DDX-038: duplicate form labels are preserved, not silently deduped -----
def test_duplicate_form_labels_are_preserved():
    fields = parse_form_fields("GST number:\nGST number:\nLiability cap:\n")
    assert len(fields) == 3, fields
    assert fields[0] == "GST number" and fields[2] == "Liability cap"
    assert "#2" in fields[1]
