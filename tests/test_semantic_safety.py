"""Regression tests for semantic-search trust fixes (DDX-003, DDX-004, DDX-009)."""
from __future__ import annotations

import json

import pytest

from docdex import semantic
from docdex.sync import run_sync


# ---- DDX-003: junk/no-match queries must not return false hits ----------
def test_empty_query_raises(synced):
    semantic.build(synced, quiet=True)
    with pytest.raises(semantic.EmptyQuery):
        semantic.search(synced, "")
    with pytest.raises(semantic.EmptyQuery):
        semantic.search(synced, "!!! ???")


def test_no_match_returns_no_hits(synced):
    semantic.build(synced, quiet=True)
    assert semantic.search(synced, "ZZZQQQNOTPRESENTTOKEN") == []


def test_real_query_still_matches(synced):
    semantic.build(synced, quiet=True)
    hits = semantic.search(synced, "quantum key distribution")
    assert hits and hits[0][1]["path"] == "Notes/real.pdf"


def test_cli_semantic_exit_codes(synced):
    import os
    import subprocess
    import sys
    from pathlib import Path

    semantic.build(synced, quiet=True)
    src = str(Path(__file__).resolve().parents[1] / "src")
    env = dict(os.environ, PYTHONPATH=src)

    def run(*argv):
        return subprocess.run([sys.executable, "-m", "docdex", *argv],
                              cwd=str(synced.root), env=env,
                              capture_output=True, text=True)

    assert run("semantic", "!!!").returncode == 2          # bad query
    assert run("semantic", "").returncode == 2             # empty query
    assert run("semantic", "ZZZNOPE").returncode == 1      # no match


# ---- DDX-009: short non-empty files are indexed -------------------------
def test_short_file_is_indexed(tmp_path):
    from docdex.scaffold import run_init
    proj = tmp_path / "p"
    project = run_init(proj, quiet=True)
    (proj / "tiny.md").write_text("SHORTTOKEN\n", encoding="utf-8")
    run_sync(project, quiet=True)
    semantic.build(project, quiet=True)
    hits = semantic.search(project, "SHORTTOKEN")
    assert any(row["path"] == "tiny.md" for _, row in hits)


# ---- DDX-004: external embedder output is validated ---------------------
def _embedder(tmp_path, body: str):
    script = tmp_path / "emb.py"
    script.write_text(body, encoding="utf-8")
    return f"{__import__('sys').executable} {script}"


def test_external_embedder_nan_rejected(synced, tmp_path, monkeypatch):
    cmd = _embedder(tmp_path,
                    "import sys,json; sys.stdin.read(); print(json.dumps([float('nan')]*8))")
    monkeypatch.setenv("DOCDEX_EMBED_CMD", cmd)
    with pytest.raises(semantic.EmbeddingError):
        semantic.build(synced, force=True, quiet=True)


def test_external_embedder_garbage_rejected(synced, tmp_path, monkeypatch):
    cmd = _embedder(tmp_path, "import sys; sys.stdin.read(); print('not json at all')")
    monkeypatch.setenv("DOCDEX_EMBED_CMD", cmd)
    with pytest.raises(semantic.EmbeddingError):
        semantic.build(synced, force=True, quiet=True)


def test_external_embedder_nonzero_exit_rejected(synced, tmp_path, monkeypatch):
    cmd = _embedder(tmp_path, "import sys; sys.exit(3)")
    monkeypatch.setenv("DOCDEX_EMBED_CMD", cmd)
    with pytest.raises(semantic.EmbeddingError):
        semantic.build(synced, force=True, quiet=True)


def test_failed_embed_leaves_previous_index_intact(synced, tmp_path, monkeypatch):
    semantic.build(synced, quiet=True)
    before = synced.semantic_index_path.read_bytes()
    cmd = _embedder(tmp_path, "import sys; sys.exit(1)")
    monkeypatch.setenv("DOCDEX_EMBED_CMD", cmd)
    with pytest.raises(semantic.EmbeddingError):
        semantic.build(synced, force=True, quiet=True)
    assert synced.semantic_index_path.read_bytes() == before  # not corrupted
