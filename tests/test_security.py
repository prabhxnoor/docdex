"""Regression tests for the v0.2 trust & safety fixes (audit findings DDX-*)."""
from __future__ import annotations

import json

import pytest

from docdex.config import ConfigError, Project, StateError
from docdex.scaffold import run_init, run_purge
from docdex.sync import run_sync


# ---- DDX-001: index_dir confinement -------------------------------------
@pytest.mark.parametrize("bad", ["../sibling", "/abs/path", "a/b", "..", ".",
                                 "", "x\\y", "with/slash"])
def test_hostile_index_dir_is_rejected(tmp_path, bad):
    marker = tmp_path / ".docdex.json"
    marker.write_text(json.dumps({"index_dir": bad}), encoding="utf-8")
    with pytest.raises(ConfigError):
        Project.load(tmp_path)


def test_purge_never_deletes_outside_project(tmp_path):
    proj = tmp_path / "proj"
    sibling = tmp_path / "sibling"
    sibling.mkdir()
    precious = sibling / "PRECIOUS.txt"
    precious.write_text("do not delete", encoding="utf-8")

    project = run_init(proj, quiet=True)
    # Forge a hostile marker post-init and confirm load refuses it outright.
    project.config_path.write_text(
        json.dumps({"index_dir": "../sibling"}), encoding="utf-8")
    with pytest.raises(ConfigError):
        Project.load(proj)
    # Even if a Project object were constructed with a hostile target, the
    # purge guard must refuse to delete outside the root.
    project.index_dir_name = "../sibling"  # simulate a bypass
    run_purge(project, yes=True, quiet=True)
    assert sibling.exists() and precious.exists()


# ---- DDX-002: symlink escape --------------------------------------------
def test_symlinked_file_outside_root_is_not_indexed(tmp_path):
    secret = tmp_path / "secret.md"
    secret.write_text("OUTSIDE_SECRET_do_not_index\n", encoding="utf-8")
    proj = tmp_path / "proj"
    project = run_init(proj, quiet=True)
    (proj / "innocent.md").symlink_to(secret)
    run_sync(project, quiet=True)
    from docdex.inventory import read_inventory
    assert "innocent.md" not in read_inventory(project.inventory_path)
    from docdex.search import run_search
    assert run_search(project, "OUTSIDE_SECRET") == []


def test_symlink_within_root_followed_when_opted_in(tmp_path):
    proj = tmp_path / "proj"
    project = run_init(proj, quiet=True)
    (proj / "real.md").write_text("INNER_TARGET_content\n", encoding="utf-8")
    (proj / "alias.md").symlink_to(proj / "real.md")

    run_sync(project, quiet=True)
    from docdex.inventory import read_inventory
    assert "alias.md" not in read_inventory(project.inventory_path)  # default: skip

    project.follow_symlinks = True
    run_sync(project, quiet=True)
    assert "alias.md" in read_inventory(project.inventory_path)  # opt-in: indexed


# ---- DDX-006: duplicate add is new, not rename --------------------------
def test_duplicate_add_counts_as_new(tmp_path):
    proj = tmp_path / "proj"
    project = run_init(proj, quiet=True)
    (proj / "original.md").write_text("unique content ABC\n", encoding="utf-8")
    run_sync(project, quiet=True)

    import shutil
    shutil.copy2(proj / "original.md", proj / "copy.md")
    totals = run_sync(project, quiet=True)
    assert totals["new"] == 1
    assert totals["renamed"] == 0


def test_true_rename_still_detected(tmp_path):
    proj = tmp_path / "proj"
    project = run_init(proj, quiet=True)
    (proj / "before.md").write_text("movable content QQQ\n", encoding="utf-8")
    run_sync(project, quiet=True)
    (proj / "before.md").rename(proj / "after.md")
    totals = run_sync(project, quiet=True)
    assert totals["renamed"] == 1
    assert totals["new"] == 0 and totals["deleted"] == 0


# ---- DDX-008: corrupt state fails friendly ------------------------------
def test_corrupt_marker_raises_config_error(tmp_path):
    (tmp_path / ".docdex.json").write_text("{not valid json", encoding="utf-8")
    with pytest.raises(ConfigError):
        Project.load(tmp_path)


def test_corrupt_inventory_raises_state_error(synced):
    synced.inventory_path.write_bytes(b"path\tsize\n\x00\x00bad\trow\n")
    from docdex.inventory import read_inventory
    with pytest.raises(StateError):
        read_inventory(synced.inventory_path)
