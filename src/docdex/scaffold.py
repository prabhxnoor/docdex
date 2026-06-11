"""`docdex init` and `docdex purge` — project scaffolding with zero residue.

init creates exactly three things in the project root:
  1. `.docdex.json`      — the project marker/config
  2. `<index_dir>/`      — index folder (Update/, vision_notes/, _state/, docs)
  3. `./ctx`             — optional wrapper script (plus CLAUDE.md/AGENTS.md
                            unless --no-agent-docs)

purge removes them and nothing else.
"""
from __future__ import annotations

import shutil
import stat
from pathlib import Path
from typing import Optional

from docdex.config import (
    DEFAULT_INDEX_DIR, DEFAULT_WRAPPER, MARKER_NAME, NotAProject, Project,
    ensure_state_dirs,
)

WRAPPER_TEMPLATE = """#!/bin/sh
# docdex wrapper — resolves this project's root regardless of cwd.
DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
exec docdex --root "$DIR" "$@"
"""

HANDOFF_TEMPLATE = """# {index_dir} — Operating manual

Token-efficient local index over the documents in this project. Front door:

```bash
./{wrapper} status     # freshness + cache coverage
./{wrapper} sync       # (re)index: inventory, text caches, dumps, semantic index, vision queue
./{wrapper} search "exact words or topic"
./{wrapper} semantic "rough description"
./{wrapper} doctor     # integrity checks (--e2e for a full self-test)
```

## Load tiers (cheapest first)

1. `00_MASTER_INDEX.md` — curated overview (write it once the corpus is indexed).
2. One topical `NN_*.md` file, if curated.
3. `search` / `semantic` — ranked snippets only.
4. A specific extracted cache under `_state/extracted/`, or the source file.

Never load all topical files, whole context dumps, or the semantic index
into an LLM context.

## Updating

Drop new files anywhere (preferably `{index_dir}/Update/`), edit or delete
files in place, then run `./{wrapper} sync`. Sync never moves or modifies
source files; deletions are soft-deleted to `_state/inventory_history.tsv`.

## Vision / OCR

`./{wrapper} vision create` queues images, image-only PDFs, and low-text files in
`_state/vision_tasks/manifest.tsv`. Write notes to `{index_dir}/vision_notes/`
(format in `_state/vision_tasks/VISION_TASKS.md`), then run `./{wrapper} sync` —
notes are part of the indexed tree and become searchable immediately.
"""

MASTER_INDEX_STUB = """# Master index

Placeholder created by `docdex init`. After the first `sync`, have your LLM
replace this with a ~5-8K-token curated overview: key facts, per-domain
snapshot tables, and a file map pointing at topical `NN_*.md` files.
"""

UPDATE_README = """# Update inbox

Drop new or changed files here (or anywhere in the project), then run
`sync` from the project root. This folder is indexed like any other.
"""

NOTES_README = """# vision_notes

Vision/OCR notes written by an LLM or human for sources listed in
`_state/vision_tasks/manifest.tsv`. This folder is part of the indexed tree:
after `sync`, notes are searchable like any other document.
"""

CLAUDE_MD_TEMPLATE = """# {project_name} — LLM operating context

This project is indexed by [docdex](https://github.com/prabhxnoor/docdex).

## Start every session

Run `./{wrapper} status`. If it reports STALE or cache gaps, tell the user and
ask whether to run `./{wrapper} sync` before doing context-dependent work.
Then read `{index_dir}/HANDOFF.md`.

## Hard rules

1. Token-economy load tiers: `00_MASTER_INDEX.md` -> one topical file ->
   `./{wrapper} search` / `./{wrapper} semantic` snippets -> a specific source file.
   Never load all topical files or the semantic index at once.
2. Never move source files programmatically (cloud-sync links break on moves).
3. Use the `./{wrapper}` front door rather than reimplementing indexing ad hoc.
4. Don't refresh curated `NN_*.md` files automatically — confirm with the user.
5. Freshness lives in `{index_dir}/_state/inventory.tsv` (`mtime_iso` + `sha1`);
   don't guess from filenames.
"""

AGENTS_MD_TEMPLATE = """# {project_name} — agent instructions

This directory is a docdex-indexed document corpus. Non-Claude agents
(Codex, Gemini, others) should treat this file as the entry point, then read
`{index_dir}/HANDOFF.md`.

Quick reference:

- `./{wrapper} status` — freshness; sync first if stale.
- `./{wrapper} sync` — reindex incrementally.
- `./{wrapper} search "words"` / `./{wrapper} semantic "description"` — retrieval.
- `./{wrapper} doctor` — integrity checks.

Load curated summaries before raw files; ask targeted questions instead of
guessing; never move the user's source files.
"""


def _make_executable(path: Path) -> None:
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _write_if_missing(path: Path, text: str) -> bool:
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return True


def run_init(root: Path, index_dir: str = DEFAULT_INDEX_DIR,
             wrapper: Optional[str] = DEFAULT_WRAPPER,
             agent_docs: bool = True, quiet: bool = False) -> Project:
    root = root.resolve()
    if (root / MARKER_NAME).exists():
        project = Project.load(root)
        if not quiet:
            print(f"already initialized (index dir: {project.index_dir_name}); nothing changed")
        return project
    try:
        outer = Project.discover(root)
        raise SystemExit(
            f"refusing to nest: {outer.root} is already a docdex project. "
            "Run docdex from there, or init a directory outside it."
        )
    except NotAProject:
        pass

    project = Project.create(root, index_dir=index_dir, wrapper=wrapper or "")
    ensure_state_dirs(project)
    project.save()

    fmt = {
        "project_name": root.name, "index_dir": index_dir,
        "wrapper": wrapper or "ctx",
    }
    _write_if_missing(project.index_dir / "HANDOFF.md", HANDOFF_TEMPLATE.format(**fmt))
    _write_if_missing(project.index_dir / "00_MASTER_INDEX.md", MASTER_INDEX_STUB)
    _write_if_missing(project.update_dir / "README.md", UPDATE_README)
    _write_if_missing(project.notes_dir / "README.md", NOTES_README)

    if wrapper:
        wrapper_path = root / wrapper
        if not wrapper_path.exists():
            wrapper_path.write_text(WRAPPER_TEMPLATE, encoding="utf-8")
            _make_executable(wrapper_path)
    if agent_docs:
        _write_if_missing(root / "CLAUDE.md", CLAUDE_MD_TEMPLATE.format(**fmt))
        _write_if_missing(root / "AGENTS.md", AGENTS_MD_TEMPLATE.format(**fmt))

    if not quiet:
        print(f"docdex project initialized at {root}")
        print(f"  index dir : {index_dir}/")
        if wrapper:
            print(f"  wrapper   : ./{wrapper}")
        print("\nNext: run "
              f"{'./' + wrapper + ' sync' if wrapper else 'docdex sync'} to build the index.")
    return project


def purge_targets(project: Project) -> list:
    targets = [project.index_dir, project.marker_path]
    if project.wrapper_name:
        wrapper = project.root / project.wrapper_name
        if wrapper.exists():
            targets.append(wrapper)
    return targets


def run_purge(project: Project, yes: bool = False, state_only: bool = False,
              quiet: bool = False) -> int:
    if state_only:
        if not yes:
            print(f"would remove: {project.rel_to_root(project.state_dir)}/ "
                  "(re-run with --yes to confirm)")
            return 1
        if project.state_dir.exists():
            shutil.rmtree(project.state_dir)
        if not quiet:
            print("state cleared; sources, curated files, and notes untouched. "
                  "Run sync to rebuild.")
        return 0

    curated = [p.name for p in project.index_dir.glob("[0-9][0-9]_*.md")]
    notes = list(project.notes_dir.glob("*.md")) if project.notes_dir.exists() else []
    if not yes:
        print("purge would remove:")
        for t in purge_targets(project):
            print(f"  - {project.rel_to_root(t)}")
        if curated:
            print(f"  ! includes {len(curated)} curated topical file(s): {curated[:5]}")
        if notes:
            print(f"  ! includes {len(notes)} vision note(s)")
        print("\nSource documents are never touched. Re-run with --yes to confirm.")
        return 1
    for t in purge_targets(project):
        # Never delete anything that resolves outside the project root, even if
        # a config value somehow steered a target there.
        if not project.is_within_root(t):
            if not quiet:
                print(f"refusing to delete outside the project: {t}")
            continue
        if t.is_dir():
            shutil.rmtree(t)
        else:
            t.unlink(missing_ok=True)
    if not quiet:
        print(f"purged. {project.root} no longer contains any docdex files.")
        leftover = [n for n in ("CLAUDE.md", "AGENTS.md") if (project.root / n).exists()]
        if leftover:
            print(f"left in place (may contain your edits): {', '.join(leftover)} — "
                  "delete manually if unwanted.")
    return 0
