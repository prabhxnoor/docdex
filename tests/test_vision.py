from __future__ import annotations

import csv

from docdex import vision
from docdex.inventory import read_inventory
from docdex.search import run_search
from docdex.sync import run_sync


def queue_rows(project):
    with open(vision.manifest_path(project), encoding="utf-8", newline="") as f:
        return {row["path"]: row for row in csv.DictReader(f, delimiter="\t")}


def test_queue_collects_visual_sources(synced):
    vision.create_queue(synced, quiet=True)
    rows = queue_rows(synced)
    assert rows["diagram.png"]["reason"] == "image-file"
    assert rows["Reports/scan.pdf"]["reason"] == "pdf-low-or-no-text"
    assert "Notes/real.pdf" not in rows  # has plenty of text
    # image asset copied for the LLM to open
    assets = rows["diagram.png"]["assets"]
    assert assets and (synced.root / assets.split(";")[0]).exists()


def test_notes_become_searchable_after_sync(synced):
    """Regression guard for the original system's biggest bug: vision notes
    must live inside the indexed tree and become searchable via plain sync."""
    vision.create_queue(synced, quiet=True)
    note = vision.note_path_for(synced, "diagram.png")
    note.write_text(
        "# Vision/OCR note\nSource: diagram.png\nReason: image-file\n\n"
        "## Visual/OCR Summary\nA PURPLEGRAPH architecture diagram.\n",
        encoding="utf-8",
    )
    status = vision.queue_status(synced, quiet=True)
    assert status["done"] == 1

    run_sync(synced, quiet=True)
    rel = synced.rel_to_root(note)
    assert rel in read_inventory(synced.inventory_path)
    hits = run_search(synced, "PURPLEGRAPH", limit=3)
    assert hits and hits[0][1] == rel


def test_done_sources_are_not_offered_again(synced):
    """Finished work must never be handed out a second time.

    This used to be asserted as "the row disappears from the manifest", and dropping
    the row is what made the queue unable to count: `queue_status` looked for rows
    whose note already existed, and by construction there were none, so `done` was
    structurally always 0 while the total shrank as work got finished. On the real
    corpus 1,041 completed notes read as `0/1896 done`. The row now stays and says
    `done` — the same guarantee (never re-offered) with a total that means something.
    """
    vision.create_queue(synced, quiet=True)
    note = vision.note_path_for(synced, "diagram.png")
    note.write_text("# Vision/OCR note\nSource: diagram.png\n", encoding="utf-8")
    vision.create_queue(synced, quiet=True)

    row = queue_rows(synced).get("diagram.png")
    assert row is not None, "the finished task vanished from the manifest"
    assert row["status"] == "done", row
    pending = [p for p, r in queue_rows(synced).items() if r["status"] == "pending"]
    assert "diagram.png" not in pending, pending
