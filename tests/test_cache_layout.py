"""The external, per-machine cache location and its confinement guard.

A docdex project keeps small, durable, syncable content in its in-project
home dir, but the big rebuildable state lives in a per-machine cache OUTSIDE
the project (so two machines syncing the same folder never share — and never
corrupt — one database). These tests pin down where that cache lives, how a
project maps to a stable cache id, and the guard that keeps every write/delete
inside the cache base.
"""
from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from docdex import config  # noqa: E402


class TestCacheBase:
    def test_defaults_to_home_dot_cache_docdex(self, monkeypatch, tmp_path):
        monkeypatch.delenv("DOCDEX_CACHE_DIR", raising=False)
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        assert config.cache_base() == tmp_path / ".cache" / "docdex"

    def test_honors_xdg_cache_home(self, monkeypatch, tmp_path):
        monkeypatch.delenv("DOCDEX_CACHE_DIR", raising=False)
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
        assert config.cache_base() == tmp_path / "xdg" / "docdex"

    def test_docdex_cache_dir_overrides_everything(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
        monkeypatch.setenv("DOCDEX_CACHE_DIR", str(tmp_path / "explicit"))
        assert config.cache_base() == tmp_path / "explicit"


class TestProjectCacheId:
    def test_is_deterministic_for_same_root(self, tmp_path):
        assert config.project_cache_id(tmp_path) == config.project_cache_id(tmp_path)

    def test_differs_for_different_roots(self, tmp_path):
        one, two = tmp_path / "one", tmp_path / "two"
        one.mkdir(); two.mkdir()
        assert config.project_cache_id(one) != config.project_cache_id(two)

    def test_includes_readable_slug_and_is_filename_safe(self, tmp_path):
        d = tmp_path / "Q Documents!"
        d.mkdir()
        cid = config.project_cache_id(d)
        assert cid.startswith("Q_Documents")  # spaces/punctuation sanitized
        assert "/" not in cid and " " not in cid
        assert len(cid) <= 64

    def test_distinguishes_paths_that_sanitize_alike(self, tmp_path):
        # "A B" and "A_B" flatten to the same slug; the hash keeps them apart.
        a, b = tmp_path / "A B", tmp_path / "A_B"
        a.mkdir(); b.mkdir()
        assert config.project_cache_id(a) != config.project_cache_id(b)


class TestIsWithin:
    def test_true_for_child(self, tmp_path):
        base = tmp_path / "base"
        assert config.is_within(base / "x" / "y", base) is True

    def test_base_itself_counts_as_within(self, tmp_path):
        base = tmp_path / "base"
        assert config.is_within(base, base) is True

    def test_false_for_sibling_outside(self, tmp_path):
        base = tmp_path / "base"
        assert config.is_within(tmp_path / "outside", base) is False

    def test_false_for_dotdot_escape(self, tmp_path):
        base = tmp_path / "base"
        base.mkdir()
        assert config.is_within(base / ".." / "evil", base) is False

    def test_false_for_symlink_escaping_base(self, tmp_path):
        base = tmp_path / "base"
        base.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (base / "link").symlink_to(outside)
        assert config.is_within(base / "link" / "secret", base) is False
