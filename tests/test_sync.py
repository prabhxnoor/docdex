from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from docdex.inventory import read_extract_status, read_inventory
from docdex.sync import SyncLocked, compute_status, run_sync


def snapshot(root: Path):
    out = {}
    for p in root.rglob("*"):
        if p.is_file():
            st = p.stat()
            out[str(p)] = (st.st_size, st.st_mtime_ns)
    return out


def test_first_sync_builds_everything(project):
    totals = run_sync(project, quiet=True)
    assert totals["new"] > 0
    assert totals["changed"] == totals["deleted"] == totals["renamed"] == 0

    inv = read_inventory(project.inventory_path)
    assert "Reports/Q1 report.md" in inv
    assert inv["Reports/Q1 report.md"]["sha1"]

    cache = project.cache_path_for("Reports/board deck.docx")
    assert "XANTHICWORD" in cache.read_text(encoding="utf-8")

    statuses = read_extract_status(project)
    assert statuses["Reports/scan.pdf"]["status"] == "empty"
    assert statuses["unsupported.bin"]["status"] == "unsupported"
    assert statuses["Notes/real.pdf"]["status"] == "ok"


def test_second_sync_is_idempotent_and_skips_empty(synced):
    project = synced
    empty_cache = project.cache_path_for("Reports/scan.pdf")
    before = empty_cache.stat().st_mtime_ns

    totals = run_sync(project, quiet=True)
    assert totals["new"] == totals["changed"] == totals["deleted"] == 0
    assert totals["unchanged"] == totals["total"]
    # the empty (scanned) pdf must NOT have been re-extracted
    assert empty_cache.stat().st_mtime_ns == before
    assert totals["extracted"]["empty"] == 0


def test_change_detection(synced):
    project = synced
    target = project.root / "Notes" / "hello.txt"
    time.sleep(0.02)
    target.write_text("plain hello world note\nplus NEWCHANGEMARK\n", encoding="utf-8")
    totals = run_sync(project, quiet=True)
    assert totals["changed"] == 1
    cache = project.cache_path_for("Notes/hello.txt")
    assert "NEWCHANGEMARK" in cache.read_text(encoding="utf-8")


def test_rename_detection_copies_cache(synced):
    project = synced
    src = project.root / "Notes" / "hello.txt"
    dst = project.root / "Reports" / "renamed hello.txt"
    src.rename(dst)
    totals = run_sync(project, quiet=True)
    assert totals["renamed"] == 1
    assert totals["deleted"] == 0
    assert totals["new"] == 0
    new_cache = project.cache_path_for("Reports/renamed hello.txt")
    assert "hello world" in new_cache.read_text(encoding="utf-8")
    inv = read_inventory(project.inventory_path)
    assert "Notes/hello.txt" not in inv
    assert "Reports/renamed hello.txt" in inv


def test_soft_delete(synced):
    project = synced
    (project.root / "Reports" / "data.csv").unlink()
    totals = run_sync(project, quiet=True)
    assert totals["deleted"] == 1
    assert "Reports/data.csv" not in read_inventory(project.inventory_path)
    history = project.history_path.read_text(encoding="utf-8")
    assert "Reports/data.csv" in history and "deleted" in history


def test_dry_run_writes_nothing(project):
    run_sync(project, quiet=True)
    before = snapshot(project.state_dir)
    (project.root / "Reports" / "brand new.md").write_text("new!", encoding="utf-8")
    totals = run_sync(project, dry_run=True, quiet=True)
    assert totals["new"] == 1
    assert snapshot(project.state_dir) == before


def test_lock_blocks_and_stale_lock_clears(project):
    project.lock_path.parent.mkdir(parents=True, exist_ok=True)
    project.lock_path.write_text("12345", encoding="utf-8")
    with pytest.raises(SyncLocked):
        run_sync(project, quiet=True)
    old = time.time() - 3600
    os.utime(project.lock_path, (old, old))
    totals = run_sync(project, quiet=True)  # stale lock is cleared
    assert totals["total"] > 0
    assert not project.lock_path.exists()


def test_compute_status_buckets(synced):
    project = synced
    s = compute_status(project)
    assert not s["stale"]
    assert not s["gaps"]
    assert "Reports/scan.pdf" in s["no_text"]

    (project.root / "Notes" / "later.md").write_text("later", encoding="utf-8")
    s2 = compute_status(project)
    assert s2["stale"]
    assert "Notes/later.md" in s2["added"]


def test_backfill_restores_missing_cache(synced):
    project = synced
    cache = project.cache_path_for("Reports/Q1 report.md")
    cache.unlink()
    run_sync(project, quiet=True)  # auto-repairs missing cache even unchanged
    assert cache.exists()
    assert "ZEPHYRTOKEN" in cache.read_text(encoding="utf-8")
