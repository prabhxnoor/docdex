"""The v0.4.1 storage layout.

In-project: a single hidden ``.docdex/`` home holding small, durable, syncable
content (config marker, secrets, vision_notes, Update inbox, curated docs).
Out-of-project: the big rebuildable state (extracted caches, SQLite index,
semantic index) in a per-machine cache, so two machines syncing the same
folder never share — and never corrupt — one database.

Back-compat: a legacy v1 project (root ``.docdex.json`` + in-project
``_index/_state``) still loads and keeps using its in-project state until it is
migrated, so upgrading docdex never breaks an existing corpus mid-flight.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from docdex import config  # noqa: E402
from docdex.config import Project, ensure_state_dirs, project_cache_id  # noqa: E402


def _root(tmp_path: Path) -> Path:
    r = tmp_path / "proj"
    r.mkdir()
    return r


def _new_project(root: Path) -> Project:
    p = Project.create(root)
    ensure_state_dirs(p)
    p.save()
    return p


class TestHomeInProject:
    def test_home_is_hidden_dot_docdex(self, tmp_path):
        p = Project.create(_root(tmp_path))
        assert p.index_dir_name == ".docdex"
        assert p.index_dir.name == ".docdex"

    def test_only_the_hidden_home_appears_at_root(self, tmp_path):
        root = _root(tmp_path)
        _new_project(root)
        assert [c.name for c in root.iterdir()] == [".docdex"]

    def test_config_secrets_notes_update_live_in_home(self, tmp_path):
        root = _root(tmp_path)
        p = _new_project(root)
        assert p.config_path == root / ".docdex" / "config.json"
        assert p.secrets_path == root / ".docdex" / "secrets.json"
        assert p.notes_dir == root / ".docdex" / "vision_notes"
        assert p.update_dir == root / ".docdex" / "Update"
        assert p.config_path.is_file()
        assert p.notes_dir.is_dir() and p.update_dir.is_dir()


class TestStateOutsideProject:
    def test_state_is_in_external_cache_not_under_root(self, tmp_path, monkeypatch):
        cache = tmp_path / "cache"
        monkeypatch.setenv("DOCDEX_CACHE_DIR", str(cache))
        root = _root(tmp_path)
        p = Project.create(root)
        assert not config.is_within(p.state_dir, root)
        assert config.is_within(p.state_dir, cache)
        assert p.cache_dir == cache / project_cache_id(root)
        assert p.state_dir == p.cache_dir / "_state"

    def test_all_derived_state_paths_follow(self, tmp_path):
        p = Project.create(_root(tmp_path))
        for path in (p.index_db_path, p.inventory_path, p.history_path,
                     p.semantic_index_path, p.extracted_dir, p.dumps_dir,
                     p.vision_dir, p.lock_path):
            assert config.is_within(path, p.state_dir)

    def test_ensure_state_dirs_creates_external_and_writes_meta(self, tmp_path):
        root = _root(tmp_path)
        p = _new_project(root)
        assert p.state_dir.is_dir()
        meta = p.cache_dir / "meta.json"
        assert meta.is_file()
        assert json.loads(meta.read_text())["root"] == str(root.resolve())

    def test_separate_roots_get_separate_caches(self, tmp_path):
        a, b = tmp_path / "a", tmp_path / "b"
        a.mkdir(); b.mkdir()
        assert Project.create(a).cache_dir != Project.create(b).cache_dir


class TestSaveLoadDiscover:
    def test_roundtrip_new_layout(self, tmp_path):
        root = _root(tmp_path)
        _new_project(root)
        loaded = Project.load(root)
        assert loaded.index_dir_name == ".docdex"
        assert loaded.legacy is False

    def test_discover_from_subdir(self, tmp_path):
        root = _root(tmp_path)
        _new_project(root)
        deep = root / "Reports" / "Deep"
        deep.mkdir(parents=True)
        assert Project.discover(deep).root == root.resolve()


class TestLegacyV1BackCompat:
    def _make_legacy(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        (root / ".docdex.json").write_text(json.dumps({
            "docdex_schema": 1, "index_dir": "_index",
            "wrapper": "", "skip_dirs": [],
        }), encoding="utf-8")
        (root / "_index" / "_state").mkdir(parents=True)
        (root / "_index" / "vision_notes").mkdir(parents=True)

    def test_legacy_marker_loads(self, tmp_path):
        root = tmp_path / "legacyproj"
        self._make_legacy(root)
        p = Project.load(root)
        assert p.legacy is True
        assert p.index_dir_name == "_index"

    def test_legacy_keeps_in_project_state(self, tmp_path):
        root = tmp_path / "legacyproj"
        self._make_legacy(root)
        p = Project.load(root)
        assert p.state_dir == root / "_index" / "_state"
        assert config.is_within(p.state_dir, root)

    def test_discover_finds_legacy_from_subdir(self, tmp_path):
        root = tmp_path / "legacyproj"
        self._make_legacy(root)
        sub = root / "x"
        sub.mkdir(parents=True)
        assert Project.discover(sub).root == root.resolve()


class TestTwoLaptopsShareHomeNotState:
    """The headline guarantee: two machines syncing the same folder share the
    small in-project home, but each keeps its OWN external state — so there is
    never one shared database to corrupt."""

    def test_same_root_two_caches_are_independent(self, tmp_path, monkeypatch):
        from docdex.sync import run_sync
        root = tmp_path / "proj"
        root.mkdir()
        (root / "doc.md").write_text("ALPHA quantum content\n", encoding="utf-8")

        # Laptop A
        monkeypatch.setenv("DOCDEX_CACHE_DIR", str(tmp_path / "cacheA"))
        pa = Project.create(root)
        ensure_state_dirs(pa)
        pa.save()
        run_sync(pa, quiet=True)
        cache_a = pa.cache_dir
        assert pa.inventory_path.exists()

        # Laptop B: same shared .docdex/ home (OneDrive synced it), but a
        # different per-machine cache. It reads the SAME config, builds its OWN
        # state, and never touches A's inventory/database.
        monkeypatch.setenv("DOCDEX_CACHE_DIR", str(tmp_path / "cacheB"))
        pb = Project.load(root)
        assert pb.index_dir == pa.index_dir          # shared home
        assert pb.cache_dir != cache_a               # independent state
        assert not pb.inventory_path.exists()        # B hasn't built yet
        run_sync(pb, quiet=True)
        assert pb.inventory_path.exists()            # B built its own, no clash
        assert config.is_within(cache_a, tmp_path / "cacheA")
        assert config.is_within(pb.cache_dir, tmp_path / "cacheB")


class TestCorruptEdges:
    def test_corrupt_v2_config_raises_clean_error(self, tmp_path):
        from docdex.config import ConfigError
        root = tmp_path / "proj"
        (root / ".docdex").mkdir(parents=True)
        (root / ".docdex" / "config.json").write_text("{ not json", encoding="utf-8")
        with pytest.raises(ConfigError):
            Project.load(root)
