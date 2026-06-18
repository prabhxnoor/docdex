from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from docdex.config import MARKER_NAME, Project
from docdex.scaffold import run_init, run_purge

SRC = str(Path(__file__).resolve().parents[1] / "src")


def test_init_creates_exactly_the_documented_files(corpus):
    run_init(corpus, quiet=True)
    assert (corpus / ".docdex" / "config.json").is_file()
    assert (corpus / ".docdex" / "HANDOFF.md").is_file()
    assert (corpus / ".docdex" / "Update" / "README.md").is_file()
    assert (corpus / ".docdex" / "vision_notes" / "README.md").is_file()
    # no wrapper and no legacy root marker by default — the root stays clean
    assert not (corpus / "ctx").exists()
    assert not (corpus / MARKER_NAME).exists()
    assert (corpus / "CLAUDE.md").is_file()
    assert (corpus / "AGENTS.md").is_file()


def test_init_is_idempotent_and_refuses_nesting(corpus):
    run_init(corpus, quiet=True)
    run_init(corpus, quiet=True)  # second call: no-op, no error
    with pytest.raises(SystemExit, match="refusing to nest"):
        run_init(corpus / "Notes", quiet=True)


def test_purge_leaves_zero_residue(corpus):
    before = {p.relative_to(corpus) for p in corpus.rglob("*")}
    project = run_init(corpus, quiet=True)

    assert run_purge(project, yes=False) == 1  # dry refusal
    assert project.config_path.exists()
    assert project.cache_dir.exists()

    assert run_purge(project, yes=True, quiet=True) == 0
    after = {p.relative_to(corpus) for p in corpus.rglob("*")}
    leftover = after - before
    # CLAUDE.md/AGENTS.md are deliberately kept (may carry user edits)
    assert leftover == {Path("CLAUDE.md"), Path("AGENTS.md")}
    assert not (corpus / ".docdex").exists()
    assert not project.cache_dir.exists()  # external cache removed too


def run_cli(corpus, *argv):
    env = dict(os.environ, PYTHONPATH=SRC)
    return subprocess.run(
        [sys.executable, "-m", "docdex", *argv],
        cwd=str(corpus), env=env, capture_output=True, text=True, timeout=300,
    )


def test_cli_end_to_end(corpus):
    r = run_cli(corpus, "init")
    assert r.returncode == 0, r.stderr
    assert "initialized" in r.stdout

    r = run_cli(corpus, "sync")
    assert r.returncode == 0, r.stderr

    r = run_cli(corpus, "status")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "fresh" in r.stdout

    r = run_cli(corpus, "search", "ZEPHYRTOKEN")
    assert r.returncode == 0
    assert "Reports/Q1 report.md" in r.stdout

    r = run_cli(corpus, "semantic", "quantum key distribution")
    assert r.returncode == 0
    assert "real.pdf" in r.stdout

    r = run_cli(corpus, "doctor", "--no-sha")
    assert r.returncode == 0, r.stdout

    # status from a nested cwd must discover the project root
    nested = corpus / "Notes" / "Deep"
    env = dict(os.environ, PYTHONPATH=SRC)
    r2 = subprocess.run([sys.executable, "-m", "docdex", "status"],
                        cwd=str(nested), env=env, capture_output=True, text=True)
    assert r2.returncode == 0

    r = run_cli(corpus, "purge", "--yes")
    assert r.returncode == 0
    assert not (corpus / MARKER_NAME).exists()


def test_cli_outside_project_fails_helpfully(tmp_path):
    r = run_cli(tmp_path, "status")
    assert r.returncode != 0
    assert "docdex init" in (r.stdout + r.stderr)


def test_cli_version(tmp_path):
    r = run_cli(tmp_path, "--version")
    assert r.returncode == 0
    assert "docdex" in r.stdout


def test_project_load_with_explicit_root(corpus):
    run_init(corpus, quiet=True)
    p = Project.load(corpus)
    assert p.index_dir_name == ".docdex"
