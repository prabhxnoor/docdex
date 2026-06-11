"""Regression tests for v0.2.1 Phase-1 trust fixes (audit findings DDX-015..026).

Each test reproduces the failure the independent v0.2 audit reported, then
asserts the fixed behavior.
"""
from __future__ import annotations

import json
import shutil
import socket

import pytest

from docdex.config import ConfigError, Project, StateError, ensure_state_dirs
from docdex.inventory import read_extract_status, read_inventory
from docdex.scaffold import run_init
from docdex.sync import acquire_lock, compute_status, release_lock, run_sync


# ---- DDX-015: symlinked index_dir must not escape the project ------------
def test_init_refuses_symlinked_index_dir(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    target = tmp_path / "outside_target"
    target.mkdir()
    (proj / "idxlink").symlink_to(target, target_is_directory=True)
    with pytest.raises(ConfigError):
        run_init(proj, index_dir="idxlink", quiet=True)
    assert not (target / "_state").exists(), "state was written through the symlink"


def test_sync_refuses_symlinked_index_dir(tmp_path):
    proj = tmp_path / "proj"
    project = run_init(proj, quiet=True)
    target = tmp_path / "evil"
    target.mkdir()
    shutil.rmtree(project.index_dir)          # attacker swaps the index dir...
    project.index_dir.symlink_to(target)      # ...for a symlink pointing outside
    with pytest.raises(ConfigError):
        run_sync(project, quiet=True)
    assert not (target / "_state").exists()


# ---- DDX-025: index_dir rejects ~ and control chars, but allows spaces ----
@pytest.mark.parametrize("bad", ["~", "ev~il", "line\nbreak", "tab\there"])
def test_index_dir_rejects_tilde_and_control_chars(tmp_path, bad):
    (tmp_path / ".docdex.json").write_text(
        json.dumps({"index_dir": bad}), encoding="utf-8")
    with pytest.raises(ConfigError):
        Project.load(tmp_path)


def test_index_dir_still_allows_spaces(tmp_path):
    p = Project.create(tmp_path, index_dir="About My Index")
    assert p.index_dir_name == "About My Index"


# ---- DDX-016: a corrupt index.db is quarantined and rebuilt ---------------
def test_corrupt_index_db_is_quarantined_and_rebuilt(synced):
    from docdex import index_db
    db = synced.index_db_path
    db.write_bytes(b"not a sqlite database at all")
    result = index_db.build(synced, quiet=True)      # must NOT raise
    assert result["files"] > 0
    assert index_db.available(synced)
    assert list(db.parent.glob("index.db.corrupt.*")), "no quarantine copy left"


# ---- DDX-017: all state readers fail friendly on corruption --------------
def test_corrupt_extract_status_raises_state_error(synced):
    synced.extract_status_path.write_bytes(
        b"path\tstatus\tchars\tdetail\tts\n\x00bad\tok\t1\t\tsoon\n")
    with pytest.raises(StateError):
        read_extract_status(synced)


def test_ragged_inventory_raises_state_error(synced):
    synced.inventory_path.write_text(
        "path\tsize\tmtime_iso\tsha1\text\tfolder\nonlypath\n", encoding="utf-8")
    with pytest.raises(StateError):
        read_inventory(synced.inventory_path)


# ---- DDX-021: a lock from a dead PID is reclaimed immediately -------------
def test_stale_lock_with_dead_pid_is_reclaimed(project):
    dead_pid = 2147483647  # max int32 — practically never a live process
    project.lock_path.write_text(
        json.dumps({"pid": dead_pid, "host": socket.gethostname(), "ts": 0}),
        encoding="utf-8")
    assert acquire_lock(project) is True
    release_lock(project)


def test_live_lock_still_blocks(project):
    assert acquire_lock(project) is True       # this process holds it (alive)
    assert acquire_lock(project) is False      # a second sync must refuse
    release_lock(project)


# ---- DDX-022: an oversize text file is skipped, not ballooned ------------
def test_oversize_text_file_is_skipped_not_a_gap(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "huge.md").write_text("x" * (1024 * 1024 + 4096), encoding="utf-8")
    (proj / "small.md").write_text("findable SMALLTOKEN\n", encoding="utf-8")
    project = Project.create(proj, index_dir="_index")
    project.config["max_extract_mb"] = 1
    ensure_state_dirs(project)
    project.save()
    run_sync(project, quiet=True)

    st = read_extract_status(project)
    assert st["huge.md"]["status"] == "skipped"
    assert st["small.md"]["status"] == "ok"
    assert not project.cache_path_for("huge.md").exists()
    assert "huge.md" not in compute_status(project)["missing_cache"]


def test_allow_large_text_overrides_cap(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "huge.md").write_text("BIGTOKEN " * (200 * 1024), encoding="utf-8")
    project = Project.create(proj, index_dir="_index")
    project.config["max_extract_mb"] = 1
    ensure_state_dirs(project)
    project.save()
    run_sync(project, allow_large=True, quiet=True)
    assert read_extract_status(project)["huge.md"]["status"] == "ok"


# ---- DDX-024: search before first sync says "run sync", exit 2 -----------
def test_search_before_sync_reports_missing_index(tmp_path, capsys):
    from docdex.cli import main
    proj = tmp_path / "proj"
    run_init(proj, quiet=True)
    rc = main(["--root", str(proj), "search", "anything"])
    assert rc == 2
    assert "sync" in capsys.readouterr().err.lower()


# ---- DDX-023: the AGENTS.md scaffold teaches the context workflow --------
def test_agents_md_mentions_context_workflow(tmp_path):
    proj = tmp_path / "proj"
    run_init(proj, quiet=True)
    agents = (proj / "AGENTS.md").read_text(encoding="utf-8")
    assert "context" in agents and "--budget" in agents
