"""Tests for docdex v0.5.0 Conflict v2 (M1 piece 5).

Two things are under test:
  1. Disagreeing DATES are now detected. Before this piece `VALUE_RE` matched the
     bare-number alternative before the date alternative, so `31/12/2026` was
     extracted as `31`; two disagreeing dates keyed to the same value and no
     conflict was flagged (a silent honesty gap).
  2. Conflicting values are shown with their source + date, ranked newest-first
     with a transparent, deterministic authority-hint tiebreak — while STILL
     surfacing every disagreement (docdex never picks a winner).

Free-text conflicts come from value lines that mention a query term (see
`_value_near`/`conflict_items` in context.py), so the fixtures make the same query
term co-occur with a date/amount in two sources.
"""
from __future__ import annotations

import os
import re
import time

from docdex import context as ctx
from docdex import index_db
from docdex.context import _authority, _conflicts, build_packet
from docdex.scaffold import run_init
from docdex.sync import run_sync

_DATE_IN_PARENS = re.compile(r"\(\d{4}-\d{2}-\d{2}\)")


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


# ---- 1. Date-value fix -----------------------------------------------------
def test_date_conflict_is_detected(tmp_path):
    """Two sources give a different closing date; the conflict must be flagged
    and BOTH dates surfaced. FAILS if the VALUE_RE date-alternative move is
    reverted (the date then extracts as its leading `31` for both docs)."""
    project = _project_with(tmp_path, {
        "old.txt": "Closing date 31/12/2026.\n",
        "new.txt": "Closing date 31/01/2027.\n"})
    packet = build_packet(project, "closing date", budget=3000)
    assert "## Conflicts" in packet, packet
    assert "31/12/2026" in packet, packet
    assert "31/01/2027" in packet, packet


# ---- 2. Amounts: equal don't conflict, different do (DDX-032 preserved) -----
def test_equal_amounts_still_not_conflicting(tmp_path):
    """`4.2 crore` and `₹4.20 cr` are the same amount written differently — they
    must NOT be reported as a conflict (DDX-032 preserved)."""
    project = _project_with(tmp_path, {
        "a.txt": "Liability cap 4.2 crore.\n",
        "b.txt": "Liability cap ₹4.20 cr.\n"})
    packet = build_packet(project, "liability cap", budget=3000)
    assert "## Conflicts" not in packet, packet


def test_amount_conflict_still_detected(tmp_path):
    """Genuinely different amounts still conflict and both are surfaced."""
    project = _project_with(tmp_path, {
        "a.txt": "Contract value 42,000,000.\n",
        "b.txt": "Contract value 40,000,000.\n"})
    packet = build_packet(project, "contract value", budget=3000)
    assert "## Conflicts" in packet, packet
    assert "42,000,000" in packet and "40,000,000" in packet, packet


# ---- 3. Richer render: every value with a date, nothing dropped ------------
def test_conflict_shows_dates_and_all_values(tmp_path):
    """Every conflicting value line carries its source date and NO value is
    dropped (surface-not-resolve)."""
    project = _project_with(tmp_path, {
        "old.txt": "Closing date 31/12/2026.\n",
        "new.txt": "Closing date 31/01/2027.\n"})
    packet = build_packet(project, "closing date", budget=3000)
    lines = packet.splitlines()
    start = lines.index("## Conflicts")
    # the block runs until the next blank line
    block = []
    for l in lines[start + 1:]:
        if l == "":
            break
        block.append(l)
    value_lines = [l for l in block if l.lstrip().startswith("- ")
                   and ("31/12/2026" in l or "31/01/2027" in l)]
    assert len(value_lines) == 2, block          # both values present, none dropped
    for vl in value_lines:                        # each carries a YYYY-MM-DD date
        assert _DATE_IN_PARENS.search(vl), vl
    # the self-describing verify note is present
    assert any("verify" in l for l in block), block


# ---- 4. Authority hint: labels, ordering, still-surfaced -------------------
def test_authority_helper_is_transparent_and_deterministic():
    # keywords must sit on a word boundary (the helper uses \b, so hyphen/slash
    # delimiters count; note underscores are word chars and do NOT delimit).
    assert _authority("Contracts/signed-final-agreement.txt") == 1
    assert _authority("2026/Executed/msa.txt") == 1
    assert _authority("drafts/vendor-draft-wip.txt") == -1
    assert _authority("archive/old-note.txt") == -1
    assert _authority("Finance/vendor-sheet.txt") == 0
    # a path with both signals nets to a neutral hint (never a resolver)
    assert _authority("signed and draft.txt") == 0


def test_authority_orders_within_equal_recency():
    """With equal mtimes, authority breaks the tie: the signed source ranks above
    the draft one, and BOTH values remain listed (docdex never resolves)."""
    mt = "2026-01-01T00:00:00"
    mtimes = {"contracts/signed/msa.txt": mt, "contracts/draft/msa.txt": mt}
    items = [
        ("contract value", "42,000,000", "contracts/signed/msa.txt", "Contract value 42,000,000"),
        ("contract value", "40,000,000", "contracts/draft/msa.txt", "Contract value 40,000,000"),
    ]
    conflicts = _conflicts(items, mtimes)
    assert conflicts, conflicts
    _key, reps = conflicts[0]
    assert len(reps) == 2, reps                       # nothing dropped
    assert "signed" in reps[0][1], reps                # authoritative ranked first
    assert "draft" in reps[1][1], reps


def test_authority_labels_render_and_verify_note(tmp_path):
    """The render labels an authoritative source and a draft source, keeps the
    verify note, and lists every value."""
    project = _project_with(tmp_path, {
        "contracts/signed-agreement.txt": "Contract value 42,000,000.\n",
        "contracts/draft-agreement.txt": "Contract value 40,000,000.\n"})
    now = time.time()
    root = project.root
    os.utime(root / "contracts" / "draft-agreement.txt", (now - 2000, now - 2000))
    os.utime(root / "contracts" / "signed-agreement.txt", (now, now))  # newer
    run_sync(project, quiet=True)
    index_db.build(project, quiet=True)

    packet = build_packet(project, "contract value", budget=3000)
    assert "## Conflicts" in packet, packet
    assert "verify" in packet, packet
    lines = packet.splitlines()
    auth_line = [l for l in lines if "authoritative" in l and "signed-agreement" in l]
    draft_line = [l for l in lines if "· draft" in l and "draft-agreement" in l]
    assert auth_line, packet
    assert draft_line, packet
    # both values surfaced, authoritative/newer first
    assert "42,000,000" in packet and "40,000,000" in packet, packet
    assert lines.index(auth_line[0]) < lines.index(draft_line[0]), packet
