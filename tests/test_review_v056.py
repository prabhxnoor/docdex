"""v0.5.6 — the ten findings from the whole-product external review of v0.5.5.

Every test here states its invariant locally, in terms of behaviour a user can
observe, so it fails on the v0.5.5 tree on an *assertion* rather than erroring on
an import of something this release introduces.

Grouped by what the defect costs:

  Wrong answers   1  `--no-hash` left the lexical index permanently stale: search
                     returned deleted text and missed the current text, while sync
                     reported the file as changed.
                  2  `--folder` went into a SQL LIKE unescaped, so `_` matched any
                     character and one folder's request returned another's files.
                 10  Retrieval widened through a declared synonym while the
                     evidence test and the `~approx` tag did not, so documents
                     found only via a synonym were judged as if it never applied.

  Untrue output   3  The Conflicts section keyed on "which query terms appear in
                     this line", so unrelated facts about one subject were
                     reported as values that disagree.
                  4  The vision queue's `done` count could never be non-zero, and
                     finished work vanished from the total instead of counting.
                  5  `re.I` on an upper-case-only character class made ordinary
                     lowercase words read as identifiers.
                  9  `doctor` hashed about 2% of rows and reported the result as
                     if it had checked every row.

  Robustness      6  Stages 3-6 of sync ran with no lock held.
                  7  One failing stage silently skipped every later stage.
                  8  Semantic reuse keyed on the string "external", so swapping
                     embedding models mixed two models' vectors in one index.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from docdex import cli, dumps, index_db, inventory, semantic, vision
from docdex import aliases as al
from docdex.context import VALUE_RE, build_packet
from docdex.doctor import Doctor
from docdex.scaffold import run_init
from docdex.sync import acquire_lock, release_lock, run_sync

REPO_ROOT = Path(__file__).resolve().parents[1]


# Nothing this release introduces is imported at module scope. A module-level import
# of a new name raises ImportError on the previous release, which makes the whole file
# fail to COLLECT there — so gate 3 sees a setup error instead of a failing assertion
# and every test in the file stops being evidence of anything. State the invariant
# locally; reach for new internals inside the test that needs them.
def triggers_alias(query: str, groups) -> bool:
    """Does this query ask for an alias group? The rule, restated independently.

    A group fires when the stems of one of its phrases are all present in the query.
    Written out here rather than imported so this file still runs against a release
    that has no such helper.
    """
    from docdex.context import stemmed
    return any(stemmed(phrase) and stemmed(phrase) <= stemmed(query)
               for group in groups for phrase in group)


# --------------------------------------------------------------------- helpers
def corpus(tmp_path, files: dict, *, aliases: dict | None = None):
    """An initialized, synced and indexed throwaway corpus from {rel: text}."""
    root = tmp_path / "corpus"
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            p.write_bytes(text)
        else:
            p.write_text(text, encoding="utf-8")
    project = run_init(root, quiet=True)
    if aliases is not None:
        project.aliases_path.parent.mkdir(parents=True, exist_ok=True)
        project.aliases_path.write_text(json.dumps(aliases), encoding="utf-8")
    run_sync(project, quiet=True)
    index_db.build(project, quiet=True)
    return project


def sync_via_cli(project, *extra):
    """Run the real command, so deleting a stage from the CLI cannot leave these
    tests green while the advertised command stops working."""
    return cli.main(["--root", str(project.root), "sync",
                     "--no-prefetch", *extra])


def counters(line: str) -> dict:
    return {k: int(v) for k, v in re.findall(r"(\w+)=(\d+)", line)}


def doctor_line(project, name: str, **kw) -> tuple:
    doc = Doctor(project)
    if name == "inventory matches disk":
        doc.check_rows_on_disk(**kw)
    else:                                     # pragma: no cover - guard
        raise AssertionError(f"no runner wired for {name!r}")
    hits = [r for r in doc.results if r[0] == name]
    assert hits, f"doctor produced no {name!r} row: {doc.results}"
    return hits[0]


# ============================================================== 1. stale index
def test_no_hash_sync_keeps_the_index_current(tmp_path):
    """A document edited between two `--no-hash` syncs must be searchable by its
    NEW text and must not still answer with its old text.

    `--no-hash` records an empty sha1 for every file. The index compared sha1 to
    sha1, so "" == "" read as unchanged and nothing was reindexed — while the text
    cache was correctly rewritten. The two disagreed silently: `search` answered
    from text the document no longer contained and could not find the text it did.
    """
    project = corpus(tmp_path, {"Docs/note.md": "The magic word is alphaunique.\n"})
    sync_via_cli(project, "--no-hash", "--no-dumps", "--no-embed", "--no-vision")

    note = project.root / "Docs" / "note.md"
    note.write_text("The magic word is betaunique now.\n", encoding="utf-8")
    st = note.stat()
    os.utime(note, (st.st_atime + 10, st.st_mtime + 10))

    sync_via_cli(project, "--no-hash", "--no-dumps", "--no-embed", "--no-vision")

    cached = project.cache_path_for("Docs/note.md").read_text(encoding="utf-8")
    assert "betaunique" in cached, "precondition: the text cache must be current"

    assert index_db.search(project, "betaunique"), (
        "the document's current text is not searchable, though its cache holds it")
    assert not index_db.search(project, "alphaunique"), (
        "search still answers with text the document no longer contains")


def test_unhashable_file_still_tracked_by_mtime_and_size(tmp_path):
    """The same trap without any flag: a file too large to hash carries an empty
    sha1 forever, so it must be tracked by mtime+size instead — the rule
    `sync` itself already applies (sync.py: `same_hash if ... else same_meta`)."""
    project = corpus(tmp_path, {"Docs/big.md": "value gammaunique here.\n"})
    conn = sqlite3.connect(project.index_db_path)
    try:
        conn.execute("UPDATE files SET sha1 = '' WHERE rel = ?", ("Docs/big.md",))
        conn.commit()
    finally:
        conn.close()

    big = project.root / "Docs" / "big.md"
    big.write_text("value deltaunique here instead.\n", encoding="utf-8")
    st = big.stat()
    os.utime(big, (st.st_atime + 10, st.st_mtime + 10))

    run_sync(project, no_hash=True, quiet=True)
    index_db.build(project, quiet=True)

    assert index_db.search(project, "deltaunique"), (
        "a file with no recorded hash was never reindexed after being edited")


# ------------------------------------------------------------- 2. folder filter
@pytest.mark.parametrize("requested,neighbour", [
    ("PWC_DD", "PWCxDD"),                 # `_` matched any single character
    ("Audited_Financials", "Audited Financials"),   # the real-corpus collision
    ("Q1%FY26", "Q1-anything-FY26"),      # `%` matched any run of characters
    ("Costs%", "CostsElsewhere"),         # a trailing `%` matched everything after
    ("Backup\\Old", "BackupXOld"),        # the escape character itself
])
def test_folder_filter_returns_only_the_requested_folder(tmp_path, requested, neighbour):
    """`--folder X` must return documents from X and from nowhere else.

    The folder name was interpolated into a SQL LIKE pattern with no ESCAPE, so
    `_` matched any one character and `%` matched anything. On the real corpus
    `--folder "1. Audited_Financials"` also returned a different tree's
    "1. Audited Financials" — one folder's request answered with another's files.
    """
    project = corpus(tmp_path, {
        f"{requested}/a.md": "Diligence finding zebrafish, requested folder.\n",
        f"{neighbour}/b.md": "Diligence finding zebrafish, unrelated folder.\n"})

    hits = index_db.search(project, "zebrafish", folder=requested)
    rels = [h["rel"] for h in hits]
    assert f"{requested}/a.md" in rels, (
        f"the requested folder's own document was lost: {rels}")
    assert f"{neighbour}/b.md" not in rels, (
        f"--folder {requested!r} leaked a document from {neighbour!r}: {rels}")


def test_folder_filter_leak_is_absent_from_the_context_packet(tmp_path):
    """`docdex context --folder` passes the same string through, so the leak must
    be gone there too — a packet is what an LLM is handed as fact."""
    project = corpus(tmp_path, {
        "PWC_DD/a.md": "Diligence finding zebrafish, requested folder.\n",
        "PWCxDD/b.md": "Diligence finding zebrafish, unrelated folder.\n"})
    packet = build_packet(project, "zebrafish", budget=3000, folder="PWC_DD")
    assert "PWC_DD/a.md" in packet, packet
    assert "PWCxDD/b.md" not in packet, packet


# ---------------------------------------------------------------- 3. conflicts
def test_unrelated_facts_are_not_reported_as_a_conflict(tmp_path):
    """A ship date, a headcount and a revenue figure about one subject do not
    disagree with each other, so no conflict may be claimed.

    The grouping key was "which query terms appear in this line", so any two value
    lines mentioning the same term became a conflict. Real disagreements then sat
    inside a list of manufactured ones, which is the opposite of the point.
    """
    project = corpus(tmp_path, {
        "Reports/r1.md": "The widget ships in March 2024.\n",
        "Reports/r2.md": "The widget team has 12 engineers.\n",
        "Reports/r3.md": "The widget line earned 5 crore last year.\n"})
    packet = build_packet(project, "widget", budget=3000)
    assert "## Conflicts" not in packet, (
        "three unrelated facts were reported as values that disagree:\n" + packet)


def conflict_entries(packet: str) -> list:
    """The `- label: N values disagree` entries, with their value lines."""
    out, current = [], None
    for ln in packet.splitlines():
        if ln.startswith("## Conflicts"):
            current = []
            continue
        if current is None:
            continue
        if ln.startswith("## "):
            break
        if ln.startswith("- ") and "disagree" in ln:
            out.append({"label": ln, "values": []})
        elif out and ln.strip().startswith("- "):
            out[-1]["values"].append(ln.strip())
    return out


@pytest.mark.parametrize("a,b,query", [
    # same subject, same verb, DIFFERENT metric — the reviewer's case
    ("Widget has 12 engineers.\n", "Widget has 5 offices.\n", "widget"),
    # same subject and verb, different unit of account
    ("Widget shipped 40 units.\n", "Widget shipped 7 orders.\n", "widget"),
])
def test_same_subject_different_metric_is_not_a_conflict(tmp_path, a, b, query):
    """Two numbers about one subject are not two readings of one fact.

    Found by adversarial review of the v0.5.6 conflict fix: keying on the nearest
    preceding word still collapsed "Widget has 12 engineers" and "Widget has 5
    offices" onto the subject itself, because the verb between them is a function
    word. Both counts are numeric, so they were asserted to disagree. What the
    number *counts* is part of what it means, so the key has to include it.
    """
    project = corpus(tmp_path, {"R/a.md": a, "R/b.md": b})
    packet = build_packet(project, query, budget=3000)
    assert not conflict_entries(packet), (
        "two different metrics were reported as values that disagree:\n" + packet)


def test_a_genuine_disagreement_is_still_reported(tmp_path):
    """The guard on the test above: two sources stating the SAME thing with
    different values must still conflict. A fix that simply stopped reporting
    conflicts would pass the previous test and fail this one."""
    project = corpus(tmp_path, {
        "Reports/a.md": "The widget line earned 5 crore last year.\n",
        "Reports/b.md": "The widget line earned 9 crore last year.\n"})
    packet = build_packet(project, "widget", budget=3000)
    entries = conflict_entries(packet)
    assert entries, "the genuine disagreement was not reported:\n" + packet
    # Asserted on ONE entry's own value list, not on the packet as a whole: an empty
    # or unrelated Conflicts section plus two values sitting in Answers would satisfy
    # a whole-packet substring check while grouping nothing.
    grouped = [e for e in entries
               if any("5 crore" in v for v in e["values"])
               and any("9 crore" in v for v in e["values"])]
    assert grouped, f"the two values were not grouped into one conflict: {entries}"
    sources = " ".join(grouped[0]["values"])
    assert "Reports/a.md" in sources and "Reports/b.md" in sources, grouped


def test_a_conflict_is_reported_when_the_value_starts_the_line(tmp_path):
    """A value with nothing before it must still be grouped and reported.

    Guard against the v0.5.6 conflict fix itself, found by adversarial review of it:
    the predicate was read from the words *before* the value, so "$500,000 is the
    approved budget" had no predicate at all and the item was dropped — trading
    fabricated conflicts for silently hidden ones, which is far worse. This case
    passes on v0.5.5 (the old key grouped it), so it proves nothing about the base
    tree; it exists to stop this release from regressing what the base got right.
    """
    project = corpus(tmp_path, {
        "Budget/a.md": "$500,000 is the approved budget.\n",
        "Budget/b.md": "$900,000 is the approved budget.\n"})
    packet = build_packet(project, "budget", budget=3000)
    assert "500,000" in packet and "900,000" in packet, packet
    assert "## Conflicts" in packet, (
        "two contradictory values were shown as evidence with no conflict "
        "reported:\n" + packet)


def test_a_zero_size_row_is_not_reindexed_forever(tmp_path):
    """Comparing sizes must not turn the integer 0 into an empty string.

    `str(size or "")` made a zero-size row compare unequal to itself, so it would be
    re-extracted and re-indexed on every sync. Not reachable through the CLI today
    (a row reaches this comparison only when its text cache is non-empty, which a
    0-byte file cannot produce), so it is asserted directly on the comparison.
    """
    from docdex.index_db import _index_is_current
    stored = {"sha1": "", "mtime_iso": "2026-01-01T00:00:00", "size": 0}
    row = {"sha1": "", "mtime_iso": "2026-01-01T00:00:00", "size": "0"}
    assert _index_is_current(stored, row) is True, (
        "a zero-size row reads as changed against itself")
    moved = {"sha1": "", "mtime_iso": "2026-01-01T00:00:00", "size": "12"}
    assert _index_is_current(stored, moved) is False, "a size change went unnoticed"


def test_values_of_different_kinds_never_conflict(tmp_path):
    """A date and an amount cannot be two readings of one value."""
    project = corpus(tmp_path, {
        "Reports/a.md": "Renewal fee 5 crore.\n",
        "Reports/b.md": "Renewal fee due 31/12/2026.\n"})
    packet = build_packet(project, "renewal fee", budget=3000)
    assert "## Conflicts" not in packet, packet


# -------------------------------------------------------------- 4. vision queue
def _image_bytes() -> bytes:
    return bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082")


def test_finished_vision_work_counts_as_done(tmp_path):
    """Finishing one of two OCR tasks must read as total=2 done=1 pending=1, and
    must still read that way after the next sync rebuilds the queue.

    `create_queue` omitted every already-done row, so the manifest contained only
    not-done rows by construction; `queue_status` then counted rows whose path was
    already done — necessarily zero. The total shrank as work got done, so 1,041
    finished notes on the real corpus showed as `0/1896 done`.
    """
    root = tmp_path / "corpus"
    (root / "Docs").mkdir(parents=True)
    for name in ("scan1.png", "scan2.png"):
        (root / "Docs" / name).write_bytes(_image_bytes())
    project = run_init(root, quiet=True)
    run_sync(project, quiet=True)
    vision.create_queue(project, quiet=True)

    fresh = vision.queue_status(project, quiet=True)
    assert (fresh["total"], fresh["done"], fresh["pending"]) == (2, 0, 2), fresh

    project.notes_dir.mkdir(parents=True, exist_ok=True)
    (project.notes_dir / "scan1.md").write_text(
        "Source: Docs/scan1.png\n\nA purchase order for 42 units.\n",
        encoding="utf-8")

    vision.create_queue(project, quiet=True)          # the next sync
    after = vision.queue_status(project, quiet=True)
    assert (after["total"], after["done"], after["pending"]) == (2, 1, 1), (
        f"one of two tasks is done, but the queue reports {after}")


def test_a_deleted_note_makes_the_task_pending_again(tmp_path):
    """The note is the deliverable, so its absence means the work is not done.

    Found by adversarial review: `done` was counted from the note on disk OR the
    manifest's own `status` column, so deleting a note left the queue reporting
    finished work that no longer existed — and it would not re-offer the task. The
    manifest is a cache; the note is the fact.
    """
    root = tmp_path / "corpus"
    (root / "Docs").mkdir(parents=True)
    for name in ("scan1.png", "scan2.png"):
        (root / "Docs" / name).write_bytes(_image_bytes())
    project = run_init(root, quiet=True)
    run_sync(project, quiet=True)
    project.notes_dir.mkdir(parents=True, exist_ok=True)
    note = project.notes_dir / "scan1.md"
    note.write_text("Source: Docs/scan1.png\n\nfindings\n", encoding="utf-8")
    vision.create_queue(project, quiet=True)
    assert vision.queue_status(project, quiet=True)["done"] == 1

    note.unlink()
    after = vision.queue_status(project, quiet=True)
    assert (after["total"], after["done"], after["pending"]) == (2, 0, 2), (
        f"the queue still claims finished work whose note is gone: {after}")


def test_finished_notes_outside_the_queue_are_still_accounted_for(tmp_path):
    """Every note on disk must appear in the queue's arithmetic, somewhere.

    Found by running this release against the real corpus: `doctor` reported "1042
    note(s) indexed" while the queue reported "done=833" on the same screen. Both
    numbers were true — 208 notes belong to files that have since been deleted, gone
    over the size cap, or started extracting real text and stopped being OCR
    candidates — but two true numbers that look like a contradiction are the same
    problem as one false one. The queue now says where the difference went.
    """
    root = tmp_path / "corpus"
    (root / "Docs").mkdir(parents=True)
    (root / "Docs" / "scan1.png").write_bytes(_image_bytes())
    project = run_init(root, quiet=True)
    run_sync(project, quiet=True)
    project.notes_dir.mkdir(parents=True, exist_ok=True)
    (project.notes_dir / "scan1.md").write_text(
        "Source: Docs/scan1.png\n\nfindings\n", encoding="utf-8")
    # a note for a document that is no longer in the corpus at all
    (project.notes_dir / "old.md").write_text(
        "Source: Docs/deleted-long-ago.png\n\nfindings\n", encoding="utf-8")
    vision.create_queue(project, quiet=True)

    status = vision.queue_status(project, quiet=True)
    notes_on_disk = len(vision.existing_note_sources(project))
    assert status["done"] + status.get("notes_outside_queue", 0) == notes_on_disk, (
        f"{notes_on_disk} notes exist but the queue accounts for "
        f"done={status['done']} + outside={status.get('notes_outside_queue')}: {status}")
    assert status.get("notes_outside_queue") == 1, status


def test_vision_manifest_marks_which_rows_are_done(tmp_path):
    """The manifest is what a person or agent works from, so a finished row must
    say so there rather than disappear."""
    root = tmp_path / "corpus"
    (root / "Docs").mkdir(parents=True)
    for name in ("scan1.png", "scan2.png"):
        (root / "Docs" / name).write_bytes(_image_bytes())
    project = run_init(root, quiet=True)
    run_sync(project, quiet=True)
    project.notes_dir.mkdir(parents=True, exist_ok=True)
    (project.notes_dir / "scan1.md").write_text(
        "Source: Docs/scan1.png\n\nnotes\n", encoding="utf-8")
    vision.create_queue(project, quiet=True)

    text = vision.manifest_path(project).read_text(encoding="utf-8")
    rows = [ln.split("\t") for ln in text.splitlines()[1:] if ln.strip()]
    by_path = {r[2]: r[0] for r in rows}
    assert by_path.get("Docs/scan1.png") == "done", by_path
    assert by_path.get("Docs/scan2.png") == "pending", by_path


# ------------------------------------------------------------------ 5. has_value
@pytest.mark.parametrize("token,numeric", [
    ("covid19", "19"), ("windows10", "10"), ("section2b", "2")])
def test_a_lowercase_word_is_not_read_as_an_identifier(token, numeric):
    """`[A-Z0-9]{6,}\\d` under `re.I` matches lowercase words, defeating its own
    character class: `covid19` was extracted whole as an ID-ish value token.

    Asserted exactly — the value read out of the word must be only its number, not
    the letters — because "the match changed" would also accept a different wrong
    reading.
    """
    m = VALUE_RE.search(token)
    assert m is not None and m.group(0) == numeric, (
        f"{token!r} read as {None if m is None else m.group(0)!r}, "
        f"expected just {numeric!r}")


@pytest.mark.parametrize("text,identifier", [
    ("reference PO4400182 attached", "PO4400182"),
    ("GSTIN 29ABCDE1234F1Z5 on file", "29ABCDE1234F1Z5"),
])
def test_a_real_identifier_is_matched_whole(text, identifier):
    """The guard, asserted exactly.

    "an identifier still matches" was checked for truthiness only, so a change that
    reduced `PO4400182` to `4400182` would have passed. An identifier that loses its
    prefix is a different identifier, and docdex quotes these into packets.
    """
    m = VALUE_RE.search(text)
    assert m is not None, f"identifier not matched at all in {text!r}"
    assert m.group(0) == identifier, (
        f"matched {m.group(0)!r}, not the whole identifier {identifier!r}")


@pytest.mark.parametrize("text,value", [
    ("Total -₹1,23,456.70 payable", "-₹1,23,456.70"),   # sign, symbol, lakh grouping
    ("Rate 12.5% per annum", "12.5%"),
    ("Cap of 4.2 crore applies", "4.2 crore"),
    ("Due 31/12/2026 sharp", "31/12/2026"),
    ("Filed 2026-07-30 today", "2026-07-30"),
])
def test_a_value_is_matched_byte_for_byte(text, value):
    """Amounts and dates must be extracted exactly as written.

    These paths were only ever asserted as booleans or substrings, so dropping a
    sign, a currency symbol, Indian digit grouping or a decimal would not have been
    caught — and the extracted string is what a conflict entry displays.
    """
    m = VALUE_RE.search(text)
    assert m is not None, f"no value found in {text!r}"
    assert m.group(0) == value, f"extracted {m.group(0)!r}, expected {value!r}"


@pytest.mark.parametrize("text,expected,why", [
    ("Invoice No. 42 is enclosed.", True, "an invoice number IS the value"),
    ("PO No. 7781 refers.", True, "a PO number is a value"),
    ("Serial No. 90210 on the unit.", True, "a serial number is a value"),
    ("Fee 5 crore under Clause 4.", True, "a fee beside a clause reference"),
    ("See Clause 4 and page 3.", False, "nothing but document locations"),
    ("Refer to section 12.", False, "a section reference at end of sentence"),
    ("As set out in paragraph 7, item 3.", False, "two locations, no value"),
])
def test_only_document_locations_are_treated_as_non_values(text, expected, why):
    """Suppressing a number must never suppress a real one.

    Two defects here, both found by adversarial review. `no`/`nos`/`sr`/`serial`
    were in the suppression list, so "Invoice No. 42" — where the number IS the
    answer — stopped counting as value-bearing; under a tight budget that loses the
    tie-break and present evidence gets reported missing. And a number at the end of
    a sentence absorbed the full stop ("page 3."), which no longer looked like a
    plain number, so the structural check was skipped and it counted as a value
    after all — the suppression quietly not applying wherever a sentence ended.
    """
    from docdex.context import carries_value
    assert carries_value(text) is expected, f"{text!r}: {why}"


def test_structural_numbering_is_not_a_value(tmp_path):
    """`has_value` exists to break ranking ties toward a chunk that can answer a
    field. A clause number, a section number and a page number answer nothing, so
    a chunk carrying only those must not be flagged as value-bearing — otherwise
    the signal means no more than "contains a digit" (96.6% of the real corpus)."""
    project = corpus(tmp_path, {
        "Docs/structural.md": "Clause 4 of the agreement is hereby amended. "
                              "See section 12 and page 3 for the carve-out.\n",
        "Docs/valued.md": "Total consideration payable is 5 crore.\n"})
    conn = sqlite3.connect(project.index_db_path)
    conn.row_factory = sqlite3.Row
    try:
        flags = {r["rel"]: r["has_value"] for r in conn.execute(
            "SELECT rel, MAX(has_value) AS has_value FROM chunks GROUP BY rel")}
    finally:
        conn.close()
    assert flags.get("Docs/valued.md") == 1, flags
    assert flags.get("Docs/structural.md") == 0, (
        "clause/section/page numbering was counted as a value: " + repr(flags))


# ----------------------------------------------------------------- 6. lock scope
STAGE_HOOKS = [
    ("3 lexical index", index_db, "build"),
    ("4 context dumps", dumps, "build_dumps"),
    ("5 semantic index", semantic, "build"),
    ("6 vision queue", vision, "create_queue"),
]


@pytest.mark.parametrize("label,module,attr", STAGE_HOOKS,
                         ids=[h[0] for h in STAGE_HOOKS])
def test_the_lock_is_held_during_every_stage(tmp_path, monkeypatch, label,
                                             module, attr):
    """A second sync must be refused for the WHOLE run, not only during stage 2.

    Probed inside each stage in turn, because a single probe in stage 3 would pass
    just as happily if the lock were released immediately afterwards. Two syncs that
    overlap past stage 2 both rebuild the FTS mirrors and both replace the semantic
    index — and because the index and its manifest are two separate replacements, the
    manifest can end up describing lines the index does not contain.
    """
    project = corpus(tmp_path, {"Docs/a.md": "hello world\n"})
    observed = {}
    real = getattr(module, attr)

    def watching(*a, **kw):
        observed["lock_file_present"] = project.lock_path.exists()
        observed["second_sync_allowed"] = acquire_lock(project)
        if observed["second_sync_allowed"]:
            release_lock(project)
        return real(*a, **kw)

    monkeypatch.setattr(module, attr, watching)
    sync_via_cli(project)

    assert observed.get("lock_file_present") is True, (
        f"no lock was held while stage {label} ran: {observed}")
    assert observed.get("second_sync_allowed") is False, (
        f"a second sync could start during stage {label}")


def test_a_real_second_process_is_refused_mid_run(tmp_path, monkeypatch):
    """The claim is about two processes, so the test uses two processes.

    A same-process `acquire_lock` call cannot tell a genuinely exclusive lock from
    one that merely refuses its own PID. This launches the actual CLI while the first
    run is inside stage 3 and requires it to be turned away by name.
    """
    project = corpus(tmp_path, {"Docs/a.md": "hello world\n"})
    seen = {}
    real_build = index_db.build

    def watching_build(proj, **kw):
        # The child needs this repo's src on its path and the SAME throwaway cache
        # dir the fixture set, so it resolves to the same project state.
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO_ROOT / "src")
        proc = subprocess.run(
            [sys.executable, "-m", "docdex", "--root", str(project.root),
             "sync", "--no-prefetch", "--no-dumps", "--no-embed", "--no-vision"],
            capture_output=True, text=True, env=env, cwd=str(REPO_ROOT))
        seen["rc"] = proc.returncode
        seen["out"] = proc.stdout + proc.stderr
        return real_build(proj, **kw)

    monkeypatch.setattr(index_db, "build", watching_build)
    sync_via_cli(project)

    assert seen, "the second process never ran"
    assert seen["rc"] != 0, (
        "a second docdex process synced the same project concurrently:\n"
        + seen["out"])
    assert "another sync" in seen["out"].lower(), (
        "the second process failed, but not because of the lock:\n" + seen["out"])


def test_the_lock_is_released_when_the_run_ends(tmp_path):
    """The guard: holding the lock longer must not leave it behind."""
    project = corpus(tmp_path, {"Docs/a.md": "hello world\n"})
    sync_via_cli(project, "--no-dumps", "--no-embed", "--no-vision")
    assert not project.lock_path.exists(), "the lock outlived the sync"
    assert acquire_lock(project) is True, "the next sync cannot start"
    release_lock(project)


@pytest.mark.parametrize("blow_up", [KeyboardInterrupt, SystemExit, RuntimeError])
def test_an_interrupted_run_does_not_leave_the_lock_behind(tmp_path, blow_up):
    """Ctrl-C must not make the project permanently unsyncable.

    `KeyboardInterrupt` and `SystemExit` derive from BaseException, not Exception, so
    a lock released only on the success path or in an `except Exception` would
    survive them and every later sync would refuse to start for 30 minutes.
    """
    from docdex.sync import sync_lock
    project = corpus(tmp_path, {"Docs/a.md": "hello world\n"})
    with pytest.raises(blow_up):
        with sync_lock(project):
            raise blow_up("interrupted mid-run")
    assert not project.lock_path.exists(), (
        f"{blow_up.__name__} left the lock behind")
    assert acquire_lock(project) is True, "a later sync cannot start"
    release_lock(project)


# ------------------------------------------------------------ 7. stage isolation
def stage_outputs(project) -> dict:
    """What each independent stage is supposed to leave behind, checked for real
    content rather than mere existence — an empty file would satisfy `exists()`."""
    def semantic_records():
        if not project.semantic_index_path.exists():
            return 0
        n = 0
        for ln in project.semantic_index_path.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                json.loads(ln)          # must parse, not just be bytes
                n += 1
        return n

    def dump_bytes():
        if not project.dumps_dir.exists():
            return 0
        return sum(p.stat().st_size for p in project.dumps_dir.glob("CONTEXT_*.txt"))

    def manifest_rows():
        path = vision.manifest_path(project)
        if not path.exists():
            return 0
        return len([ln for ln in path.read_text(encoding="utf-8").splitlines()[1:]
                    if ln.strip()])

    conn = sqlite3.connect(project.index_db_path) if project.index_db_path.exists() \
        else None
    try:
        chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] if conn else 0
    except sqlite3.Error:
        chunks = 0
    finally:
        if conn:
            conn.close()
    return {"3 lexical index": chunks, "4 context dumps": dump_bytes(),
            "5 semantic index": semantic_records(), "6 vision queue": manifest_rows()}


@pytest.mark.parametrize("label,module,attr", STAGE_HOOKS,
                         ids=[h[0] for h in STAGE_HOOKS])
def test_one_failing_stage_does_not_skip_the_others(tmp_path, monkeypatch, capsys,
                                                    label, module, attr):
    """Stages 3-6 do not depend on each other, so ANY one failing must not cancel
    the rest — and the failure must be named, not swallowed.

    A raised exception in the index build used to skip dumps, embeddings and the
    vision queue entirely. That is how one migration bug froze the vision queue and
    the context dumps for a day: the failure was real, its consequences invisible.

    Every stage is failed in turn, because isolating only stage 3 would leave the
    same trap one stage lower. Each surviving stage is checked for real output — an
    implementation that merely touched an empty file would satisfy `exists()`.
    """
    project = corpus(tmp_path, {
        "Docs/a.md": "hello world with several searchable words\n" * 40,
        "Docs/scan.png": _image_bytes()})

    def exploding(*a, **kw):
        raise RuntimeError(f"simulated {label} failure")

    monkeypatch.setattr(module, attr, exploding)
    # The command must SURVIVE a stage failure and report it, so an escaping exception
    # is stated as an assertion rather than left to abort the test — a test that merely
    # errors proves nothing about whether the release changed anything.
    crashed = None
    rc = None
    try:
        rc = sync_via_cli(project)
    except BaseException as e:                # noqa: BLE001 - the behaviour under test
        crashed = e
    captured = capsys.readouterr()
    out = captured.out + captured.err
    assert crashed is None, (
        f"one failing stage ({label}) aborted the whole command with "
        f"{type(crashed).__name__}: {crashed}\n{out}")

    produced = stage_outputs(project)
    for other, count in produced.items():
        if other == label:
            continue
        assert count > 0, (
            f"stage {other!r} produced nothing ({produced}) because {label!r} "
            f"failed — stages must be independent:\n{out}")
    assert f"simulated {label} failure" in out, (
        "the failure was not reported to the user:\n" + out)
    assert rc != 0, "a run with a failed stage reported success"


def test_a_clean_run_actually_runs_every_stage(tmp_path, capsys):
    """The guard, asserted on work done rather than on the absence of a word.

    "no failure was printed" would also be satisfied by a sync that skipped dumps,
    embeddings and the vision queue and exited 0 — a clean-looking run leaving
    advertised state stale. Every stage must be entered AND leave real output.
    """
    project = corpus(tmp_path, {
        "Docs/a.md": "hello world with several searchable words\n" * 40,
        "Docs/scan.png": _image_bytes()})
    entered = []
    import unittest.mock as mock
    patches = []
    for label, module, attr in STAGE_HOOKS:
        real = getattr(module, attr)
        patches.append(mock.patch.object(
            module, attr,
            side_effect=lambda *a, _l=label, _r=real, **kw: (
                entered.append(_l), _r(*a, **kw))[1]))
    for p in patches:
        p.start()
    try:
        rc = sync_via_cli(project)
    finally:
        for p in patches:
            p.stop()
    captured = capsys.readouterr()
    out = (captured.out + captured.err).lower()

    assert rc == 0, out
    assert entered == [h[0] for h in STAGE_HOOKS], (
        f"not every stage was entered: {entered}")
    produced = stage_outputs(project)
    assert all(v > 0 for v in produced.values()), (
        f"a stage ran but left nothing usable: {produced}")
    assert "failed" not in out.replace("failed=0", ""), out


# --------------------------------------------------------- 8. embedding identity
def _fake_model(value: float) -> str:
    import sys
    return (f'{sys.executable} -c "import sys,json;sys.stdin.read();'
            f'print(json.dumps([{value}]*16))"')


def semantic_records(project) -> dict:
    """{(path, chunk): first vector component} for every record in the index."""
    out = {}
    for ln in project.semantic_index_path.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        row = json.loads(ln)
        out[(row["path"], row["chunk"])] = round(row["vector"][0], 3)
    return out


# Long enough to chunk several times over (chunking is ~1800 chars), so a change that
# re-embedded only the first chunk of each file cannot pass.
LONG_A = "Revenue for the quarter was 12 crore and the outlook is steady. " * 90
LONG_B = "Attrition held at 8 percent across every engineering team. " * 90


def test_changing_the_embedding_model_re_embeds_the_corpus(tmp_path, monkeypatch):
    """Two different external models of the same size must not share one index.

    Reuse compared the literal string "external", which every `DOCDEX_EMBED_CMD`
    reports, so swapping models reused every unchanged file's old vectors and
    embedded only new files — one index holding two models' vectors, silently, and
    similarity across two embedding spaces is meaningless.

    Asserted per chunk, not per file: re-embedding one chunk per file and reporting
    the file counters as complete would drop every later chunk out of semantic
    retrieval while the counters looked right.
    """
    project = corpus(tmp_path, {"Docs/a.md": LONG_A, "Docs/b.md": LONG_B})

    monkeypatch.setenv("DOCDEX_EMBED_CMD", _fake_model(0.1))
    first = semantic.build(project, quiet=True)
    before = semantic_records(project)
    assert first["embedded_files"] > 0, first
    multi = [k for k in before if k[1] > 0]
    assert multi, f"fixture produced no multi-chunk file: {sorted(before)}"

    monkeypatch.setenv("DOCDEX_EMBED_CMD", _fake_model(0.9))
    second = semantic.build(project, quiet=True)
    after = semantic_records(project)

    assert second["reused_files"] == 0, (
        f"a different embedding model reused the old model's vectors: {second}")
    assert second["embedded_files"] == first["embedded_files"], (
        f"only part of the corpus was re-embedded for the new model: {second}")
    assert set(after) == set(before), (
        "the chunk set changed across the model swap; missing="
        f"{sorted(set(before) - set(after))} added={sorted(set(after) - set(before))}")
    stale = {k: v for k, v in after.items() if v != 0.9}
    assert not stale, f"these chunks still hold the old model's vectors: {stale}"


def test_the_same_embedding_model_is_still_reused(tmp_path, monkeypatch):
    """The guard: re-embedding 92k chunks on every sync would be its own bug.

    Checked against the index file's own bytes as well as the counters, because
    re-embedding everything while reporting `embedded_files=0` would satisfy the
    counters alone — and the counters are what this guard exists to distrust.
    """
    import hashlib
    project = corpus(tmp_path, {"Docs/a.md": LONG_A})
    monkeypatch.setenv("DOCDEX_EMBED_CMD", _fake_model(0.1))
    first = semantic.build(project, quiet=True)
    digest = hashlib.sha256(project.semantic_index_path.read_bytes()).hexdigest()

    again = semantic.build(project, quiet=True)
    assert again["embedded_files"] == 0, again
    assert again["reused_files"] == first["embedded_files"], (
        f"an unchanged corpus was not fully reused: {again}")
    assert hashlib.sha256(
        project.semantic_index_path.read_bytes()).hexdigest() == digest, (
        "the index was rewritten despite reporting a full reuse")


# ------------------------------------------------------------- 9. doctor honesty
def test_doctor_states_how_many_rows_it_hashed(tmp_path, monkeypatch):
    """`doctor` hashes every 50th row. The line must say how many it hashed, so
    `sha_mismatch=0` cannot be read as "every file verified".

    The count is checked against the number of times the hash function was actually
    called, not taken on trust: printing `sha_checked=1` while hashing nothing would
    otherwise satisfy a check that only looked for a positive number below the total.
    """
    files = {f"Docs/f{i:03d}.md": f"document number {i} about widgets\n"
             for i in range(120)}
    project = corpus(tmp_path, files)

    import docdex.doctor as doctormod
    calls = []
    real_sha = doctormod.sha1_of
    monkeypatch.setattr(doctormod, "sha1_of",
                        lambda p, *a, **kw: (calls.append(str(p)), real_sha(p))[1])

    name, ok, detail = doctor_line(project, "inventory matches disk", no_sha=False)
    nums = counters(detail)
    assert "sha_checked" in nums, (
        f"the check does not say how much it verified: {detail!r}")
    assert nums["sha_checked"] == len(calls), (
        f"reported sha_checked={nums['sha_checked']} but hashed {len(calls)} file(s)")
    assert 0 < nums["sha_checked"] < nums["rows"], (
        f"claims to have hashed {nums['sha_checked']} of {nums['rows']} rows: {detail}")
    assert ok, detail


def test_doctor_fails_when_a_sampled_file_was_edited(tmp_path):
    """The sample must actually be able to catch something.

    A file inside the sample is edited in place with its size and timestamp
    preserved, so only a hash can notice. If `doctor` reports this as healthy, the
    hash check is decoration.
    """
    files = {f"Docs/f{i:03d}.md": f"document number {i} about widgets\n"
             for i in range(120)}
    project = corpus(tmp_path, files)

    # doctor hashes the row at every 50th position of the inventory, in its order.
    rows = list(inventory.read_inventory(project.inventory_path))
    victim_rel = rows[49]
    victim = project.root / victim_rel
    before = victim.stat()
    text = victim.read_text(encoding="utf-8")
    victim.write_text(text[:-1].upper() + text[-1], encoding="utf-8")
    assert victim.stat().st_size == before.st_size, "precondition: same size"
    os.utime(victim, (before.st_atime, before.st_mtime))

    _name, ok, detail = doctor_line(project, "inventory matches disk", no_sha=False)
    assert counters(detail).get("sha_mismatch") == 1, (
        f"an edited file inside the sample went unnoticed: {detail}")
    assert ok is False, f"doctor called an edited corpus healthy: {detail}"


def test_doctor_reports_zero_hashed_when_hashing_is_off(tmp_path, monkeypatch):
    """With `--no-sha` nothing is hashed, and the line must not imply otherwise.

    Enforced by making the hash function fail if it is called at all, so continuing
    to hash while printing `sha_checked=0` cannot pass.
    """
    project = corpus(tmp_path, {"Docs/a.md": "widgets\n"})

    import docdex.doctor as doctormod

    def must_not_run(*a, **kw):
        raise AssertionError("--no-sha still hashed a file")

    monkeypatch.setattr(doctormod, "sha1_of", must_not_run)
    _name, _ok, detail = doctor_line(project, "inventory matches disk", no_sha=True)
    assert counters(detail).get("sha_checked") == 0, detail


def test_the_doctor_command_shows_the_sample_size(tmp_path, capsys):
    """A user reads the command's output, not `Doctor.results`.

    Every other assertion here calls the check directly, so a renderer that dropped
    `sha_checked` would leave them all green while the health command went back to
    implying it had verified everything.
    """
    files = {f"Docs/f{i:03d}.md": f"document number {i} about widgets\n"
             for i in range(120)}
    project = corpus(tmp_path, files)
    cli.main(["--root", str(project.root), "doctor"])
    out = capsys.readouterr().out
    line = [ln for ln in out.splitlines() if "inventory matches disk" in ln]
    assert line, "the command printed no inventory-vs-disk line:\n" + out
    assert "sha_checked=" in line[0], (
        "the command's own output does not say how much it hashed:\n" + line[0])


# ------------------------------------------------------------------ 10. aliases
ALIAS_FILE = {"effective date": ["commencement date", "start date"]}
NON_CONTIGUOUS = "what date does the agreement become effective"


def citation_lines(packet: str, rel: str) -> list:
    """The `[En] <rel> ...` header lines that cite one source.

    The tag has to be asserted on the source's OWN line: a legend sentence
    mentioning `~approx`, or a different source carrying the tag, would satisfy a
    whole-packet substring check while this citation was still presented as exact.
    """
    return [ln for ln in packet.splitlines()
            if ln.startswith("[E") and rel in ln]


def test_a_synonym_that_widens_the_search_also_governs_the_evidence(tmp_path):
    """One query, one alias rule.

    Retrieval widened when a group's phrase-stems were a subset of the query's,
    but the evidence test and the `~approx` tag required the phrase as a
    contiguous run. On a natural question ("what date does the agreement become
    effective") the search reached documents that only say "Commencement Date",
    then judged and labelled them as if the synonym had never applied — so a
    document present *because* of the user's synonym list was shown without the
    tag the packet's own legend promises for exactly that case.
    """
    project = corpus(tmp_path, {
        "Contracts/msa.md": "Commencement Date: 14 April 2026.\n"},
        aliases=ALIAS_FILE)
    packet = build_packet(project, NON_CONTIGUOUS, budget=3000)
    cited = citation_lines(packet, "Contracts/msa.md")
    assert cited, "the synonym no longer reaches the document at all:\n" + packet
    assert all("~approx" in ln for ln in cited), (
        "a document reached only through a declared synonym is presented as an "
        "exact match on its own citation line:\n" + "\n".join(cited))


@pytest.mark.parametrize("query", [
    NON_CONTIGUOUS,
    "effective date",
    "which date is effective for this agreement",
])
def test_a_synonym_reached_document_is_never_shown_as_an_exact_match(tmp_path, query):
    """The property behind finding 10, over phrasings nobody thought to test.

    The document says only "Commencement Date" — never the query's own words — so a
    query that triggers the alias group must both REACH it and mark it `~approx`.
    Both halves are required: an earlier version of this test only checked the tag
    *if* the document appeared, which let a change that stopped retrieving it
    altogether pass vacuously. The trigger is asserted first, so a query that stops
    triggering fails here instead of quietly becoming a no-op.
    """
    project = corpus(tmp_path, {"Contracts/msa.md": "Commencement Date: 2026.\n"},
                     aliases=ALIAS_FILE)
    groups = al.load_aliases(project)
    assert groups, "precondition: the alias file must load"
    assert triggers_alias(query, groups), (
        f"query {query!r} no longer triggers the alias group at all")

    packet = build_packet(project, query, budget=3000)
    cited = citation_lines(packet, "Contracts/msa.md")
    assert cited, (
        f"query {query!r} triggers the synonym but no longer reaches the "
        f"document:\n{packet}")
    assert all("~approx" in ln for ln in cited), (
        f"query {query!r} reached the document only through a declared synonym but "
        f"presented it as an exact match:\n" + "\n".join(cited))


def test_a_phrasing_that_triggers_nothing_does_not_reach_the_document(tmp_path):
    """The other side of the property, kept separate so neither can hide the other.

    "the start of the agreement" shares only one word with any alias phrase, so the
    group must not fire and the document — which says nothing the query says —
    must not be cited as evidence.
    """
    project = corpus(tmp_path, {"Contracts/msa.md": "Commencement Date: 2026.\n"},
                     aliases=ALIAS_FILE)
    groups = al.load_aliases(project)
    query = "the start of the agreement"
    assert not triggers_alias(query, groups), (
        "a lone shared word triggered a multi-word alias phrase")
    packet = build_packet(project, query, budget=3000)
    assert not citation_lines(packet, "Contracts/msa.md"), (
        "a document was cited through an alias that never triggered:\n" + packet)


def test_an_unrelated_shared_word_still_triggers_nothing(tmp_path):
    """The guard: unifying the rules must not make a lone shared token trigger an
    alias. 'service owner email' shares only 'service' with service level↔SLA.

    A second document is reachable ONLY through the alias word, so an erroneous
    widening has somewhere visible to show up. With just the one document — already
    retrieved exactly by "owner" and "email" — a wrong widening left no trace.
    """
    project = corpus(tmp_path, {
        "Docs/sla.md": "SLA owner: alice@x.com\n",
        "Docs/only_sla.md": "The SLA governs zebrafish handling.\n"},
        aliases={"service level": ["sla"]})
    packet = build_packet(project, "service owner email", budget=2000)
    assert "alice@x.com" in packet, packet
    assert not citation_lines(packet, "Docs/only_sla.md"), (
        "a document reachable only through an untriggered alias entered the "
        "packet:\n" + packet)
    assert "~approx" not in packet, packet


def test_explain_names_the_alias_groups_that_actually_fired(tmp_path):
    """`--explain` must describe the widening that really happened.

    It applied the contiguous-run rule while its own comment claimed it matched
    retrieval — a third answer to one question. So an explanation could say "no
    alias matched" for a packet whose evidence was found only because one did.
    """
    project = corpus(tmp_path, {"Contracts/msa.md": "Commencement Date: 2026.\n"},
                     aliases=ALIAS_FILE)
    fired = build_packet(project, NON_CONTIGUOUS, budget=3000, explain=True)
    alias_line = [ln for ln in fired.splitlines() if ln.startswith("- aliases:")]
    assert alias_line, "explain printed no aliases line:\n" + fired
    assert "effective date" in alias_line[0], (
        "explain does not name the group that widened the search: " + alias_line[0])

    quiet = build_packet(project, "who signed the contract", budget=3000, explain=True)
    quiet_line = [ln for ln in quiet.splitlines() if ln.startswith("- aliases:")]
    assert quiet_line and "none matched" in quiet_line[0], (
        "explain claims an alias fired when none did: " + str(quiet_line))
