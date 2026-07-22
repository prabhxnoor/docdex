"""Adversarial-audit hardening for docdex's "never confidently wrong" core.

Each honesty fix has a discriminating test that FAILS on the pre-fix code and
passes after. Grouped by the fix number from the audit:

  1+2. ISO (`2026-12-31`) and day-first (`15 Jan 2026`) dates must be extracted
       as whole dates, not truncated to a leading number — otherwise two distinct
       dates collapse to one key and a real conflict is hidden.
  3.   A weak (no-value) form field dropped by budget must not be reported as
       "answer found".
  5.   A negative amount (`-$500,000`) must stay negative — a loss must not read
       as a gain, and a profit/loss conflict must surface.
  6.   An exact literal match must not be tagged `~approx` merely because a
       declared alias word appears elsewhere in the chunk.
"""
from __future__ import annotations

import json

from docdex import context as ctx
from docdex import index_db
from docdex.context import _amount, build_packet
from docdex.scaffold import run_init
from docdex.sync import run_sync


def _project_with(tmp_path, files: dict, aliases: dict | None = None):
    """Build + index a throwaway corpus from {rel: text}, with optional aliases."""
    root = tmp_path / "corpus"
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    project = run_init(root, quiet=True)
    if aliases is not None:
        project.aliases_path.write_text(json.dumps(aliases), encoding="utf-8")
    run_sync(project, quiet=True)
    index_db.build(project, quiet=True)
    return project


# ---- Fix 1: ISO dates -------------------------------------------------------
def test_iso_date_conflict_detected(tmp_path):
    """Two ISO dates for the same field must conflict. Before the fix both matched
    the bare-number branch as their leading `2026` and the conflict was hidden."""
    project = _project_with(tmp_path, {
        "old.txt": "Effective date 2026-12-31.\n",
        "new.txt": "Effective date 2026-05-15.\n"})
    packet = build_packet(project, "effective date", budget=3000)
    assert "## Conflicts" in packet, packet
    assert "2026-12-31" in packet, packet
    assert "2026-05-15" in packet, packet


# ---- Fix 2: day-first month dates -------------------------------------------
def test_dayfirst_date_conflict_detected(tmp_path):
    """Day-first dates ('15 Jan 2026' vs '15 Jan 2027') must conflict. Before the
    fix both matched the bare-number branch as their leading `15`."""
    project = _project_with(tmp_path, {
        "old.txt": "Review 15 Jan 2026.\n",
        "new.txt": "Review 15 Jan 2027.\n"})
    packet = build_packet(project, "review", budget=3000)
    assert "## Conflicts" in packet, packet
    assert "15 Jan 2026" in packet, packet
    assert "15 Jan 2027" in packet, packet


# ---- Fix 3: weak field dropped by budget is not reported "answer found" ------
def test_weak_field_budget_drop_not_reported_found(tmp_path):
    """A field whose label matched but that carries no extractable value, dropped
    by a tiny budget, must NOT be described as 'answer found'."""
    project = _project_with(tmp_path, {
        "form.txt": "Signature: pending review.\n"})
    packet = build_packet(project, "fill the form", budget=1,
                          form_fields=["Signature"])
    assert "## Dropped (budget)" in packet, packet
    # the honest message, not a fabricated "answer found"
    assert "answer found" not in packet, packet
    assert "Signature" in packet, packet


# ---- Fix 5: negative amounts stay negative ----------------------------------
def test_negative_amount_conflicts_with_positive(tmp_path):
    """A loss (`-$500,000`) and a gain (`$500,000`) must conflict, and the minus
    sign must survive into the packet. Before the fix the '-' was dropped and both
    keyed to +500000, collapsing the profit/loss conflict."""
    project = _project_with(tmp_path, {
        "a.txt": "Net result -$500,000.\n",
        "b.txt": "Net result $500,000.\n"})
    packet = build_packet(project, "net result", budget=3000)
    assert "## Conflicts" in packet, packet
    assert "-$500,000" in packet, packet


def test_amount_sign_helper():
    """Unit-level guard on `_amount`: the sign is read, not discarded."""
    assert _amount("-$500,000") < 0
    assert _amount("$500,000") > 0


# ---- Fix 6: exact literal match is not tagged ~approx despite an alias word ---
def test_literal_match_not_tagged_approx_despite_alias_word(tmp_path):
    """`invoice` and `1001` are literal in the chunk, so the hit is EXACT — the
    mere presence of the alias word 'bill' elsewhere must not tag it ~approx."""
    project = _project_with(
        tmp_path,
        {"doc.txt": "Invoice 1001 details. Send bill to accounts.\n"},
        aliases={"invoice": ["bill"]})
    packet = build_packet(project, "invoice 1001", budget=3000)
    assert "doc.txt" in packet, packet          # the hit was retrieved and packed
    assert "~approx" not in packet, packet       # but not falsely flagged
