"""`docdex migrate` — upgrading a legacy v1 project to the v2 layout.

A v1 project keeps its marker at the root (`.docdex.json`) and all state
in-project (`_index/_state`). Migration consolidates the durable content into
the hidden `.docdex/` home, drops the rebuildable state (rebuilt on the next
sync, now in the external cache), and is safe to re-run.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from docdex import config  # noqa: E402
from docdex.config import Project  # noqa: E402
from docdex.migrate import is_legacy, migrate_project  # noqa: E402


def make_legacy(root: Path) -> None:
    """A v1 project: root marker + secrets, in-project _index/{_state,notes,...}."""
    root.mkdir(parents=True, exist_ok=True)
    (root / ".docdex.json").write_text(json.dumps({
        "docdex_schema": 1, "index_dir": "_index", "wrapper": "",
        "skip_dirs": ["Archive"],
    }), encoding="utf-8")
    (root / ".docdex.secrets.json").write_text(
        json.dumps({"PW_X": "secret"}), encoding="utf-8")
    idx = root / "_index"
    (idx / "_state" / "extracted").mkdir(parents=True)
    (idx / "_state" / "index.db").write_text("OLD DB", encoding="utf-8")
    (idx / "vision_notes").mkdir(parents=True)
    (idx / "vision_notes" / "note.md").write_text(
        "Source: x\nOCR TEXT KEEPME\n", encoding="utf-8")
    (idx / "Update").mkdir(parents=True)
    (idx / "HANDOFF.md").write_text("manual\n", encoding="utf-8")
    (idx / "00_MASTER_INDEX.md").write_text("# overview\n", encoding="utf-8")


class TestDryRun:
    def test_dry_run_changes_nothing(self, tmp_path):
        root = tmp_path / "proj"
        make_legacy(root)
        res = migrate_project(root, dry_run=True, quiet=True)
        assert res["migrated"] is False
        assert (root / ".docdex.json").is_file()              # still legacy
        assert not (root / ".docdex" / "config.json").exists()
        assert Project.load(root).legacy is True


class TestMigrate:
    def test_migrates_and_preserves_durable_content(self, tmp_path):
        root = tmp_path / "proj"
        make_legacy(root)
        res = migrate_project(root, quiet=True)
        assert res["migrated"] is True

        cfg = root / ".docdex" / "config.json"
        assert cfg.is_file()
        data = json.loads(cfg.read_text())
        assert data["index_dir"] == ".docdex"
        assert data["skip_dirs"] == ["Archive"]               # config preserved
        assert not (root / ".docdex.json").exists()           # legacy marker gone
        assert not (root / ".docdex.secrets.json").exists()
        assert (root / ".docdex" / "secrets.json").is_file()  # secrets relocated
        note = root / ".docdex" / "vision_notes" / "note.md"
        assert note.is_file() and "KEEPME" in note.read_text()
        assert (root / ".docdex" / "HANDOFF.md").is_file()
        assert (root / ".docdex" / "00_MASTER_INDEX.md").is_file()
        assert not (root / "_index").exists()                 # old home + state gone

    def test_loads_as_v2_after_migration(self, tmp_path):
        root = tmp_path / "proj"
        make_legacy(root)
        migrate_project(root, quiet=True)
        p = Project.load(root)
        assert p.legacy is False
        assert p.index_dir_name == ".docdex"
        assert not config.is_within(p.state_dir, root)         # state now external

    def test_os_junk_is_not_carried_into_the_new_home(self, tmp_path):
        root = tmp_path / "proj"
        make_legacy(root)
        (root / "_index" / ".DS_Store").write_bytes(b"\x00junk")
        migrate_project(root, quiet=True)
        assert not (root / ".docdex" / ".DS_Store").exists()
        assert not (root / "_index").exists()  # junk-only old home is removed

    def test_idempotent_second_run_is_noop(self, tmp_path):
        root = tmp_path / "proj"
        make_legacy(root)
        migrate_project(root, quiet=True)
        res2 = migrate_project(root, quiet=True)
        assert res2["already"] is True
        assert res2["migrated"] is False

    def test_migrated_notes_are_searchable_after_sync(self, tmp_path):
        from docdex import index_db
        from docdex.config import ensure_state_dirs
        from docdex.sync import run_sync
        root = tmp_path / "proj"
        make_legacy(root)
        migrate_project(root, quiet=True)
        p = Project.load(root)
        ensure_state_dirs(p)
        run_sync(p, quiet=True)
        index_db.build(p)
        rows = index_db.search(p, "KEEPME")
        assert any("vision_notes/note.md" in r["rel"] for r in rows)


class TestIsLegacy:
    def test_true_for_v1(self, tmp_path):
        root = tmp_path / "p"
        make_legacy(root)
        assert is_legacy(root) is True

    def test_false_for_v2(self, tmp_path):
        from docdex.config import ensure_state_dirs
        root = tmp_path / "p"
        root.mkdir()
        p = Project.create(root)
        ensure_state_dirs(p)
        p.save()
        assert is_legacy(root) is False
