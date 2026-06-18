"""`docdex migrate` — upgrade a legacy v1 project to the v2 layout.

v1: marker at the project root (`.docdex.json`), state in-project under the
index dir (`_index/_state`). v2: one hidden `.docdex/` home (config.json,
secrets.json, vision_notes/, Update/, curated docs) and the big rebuildable
state in the per-machine external cache.

Migration consolidates the durable in-project content into `.docdex/`, drops
the rebuildable `_state` (rebuilt on the next sync, now external), and rewrites
the marker. It is idempotent (a no-op once on v2) and safe to run on each
machine independently.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from docdex.config import (
    CONFIG_NAME, DEFAULT_INDEX_DIR, LEGACY_MARKER_NAME, LEGACY_SECRETS_NAME,
    NotAProject, SECRETS_NAME, SKIP_FILE_NAMES, STATE_DIR, validate_index_dir,
)


def is_legacy(root: Path) -> bool:
    """True iff `root` is a v1 project not yet migrated to v2."""
    root = Path(root)
    return (root / LEGACY_MARKER_NAME).is_file() and \
        not (root / DEFAULT_INDEX_DIR / CONFIG_NAME).is_file()


def migrate_project(root: Path, dry_run: bool = False, quiet: bool = False) -> dict:
    """Upgrade the v1 project at `root` to v2. Returns a result dict with
    `migrated`, `already`, and the list of `actions`."""
    root = Path(root).resolve()
    new_home = root / DEFAULT_INDEX_DIR
    result = {"root": str(root), "migrated": False, "already": False, "actions": []}
    actions = result["actions"]

    if (new_home / CONFIG_NAME).is_file():
        result["already"] = True
        if not quiet:
            print(f"already on the v2 layout: {root}")
        return result
    if not (root / LEGACY_MARKER_NAME).is_file():
        raise NotAProject(
            f"no legacy docdex project at {root} (no {LEGACY_MARKER_NAME} to migrate)")

    legacy_cfg = json.loads((root / LEGACY_MARKER_NAME).read_text(encoding="utf-8"))
    if not isinstance(legacy_cfg, dict):
        legacy_cfg = {}
    old_index = validate_index_dir(legacy_cfg.get("index_dir", "_index"))
    old_home = root / old_index
    same_home = old_home.resolve() == new_home.resolve()

    # Plan: move every durable item out of the old home (everything but _state).
    moves = []
    if old_home.is_dir() and not same_home:
        for item in sorted(old_home.iterdir()):
            if item.name == STATE_DIR or item.name in SKIP_FILE_NAMES:
                continue  # never carry rebuildable state or OS junk across
            moves.append((item, new_home / item.name))
    secrets_src = root / LEGACY_SECRETS_NAME
    secrets_move = (secrets_src, new_home / SECRETS_NAME) if secrets_src.is_file() else None

    for src, dst in moves:
        actions.append(f"move {old_index}/{src.name} -> {DEFAULT_INDEX_DIR}/{dst.name}")
    if secrets_move:
        actions.append(f"move {LEGACY_SECRETS_NAME} -> {DEFAULT_INDEX_DIR}/{SECRETS_NAME}")
    if (old_home / STATE_DIR).exists():
        actions.append(
            f"delete rebuildable state {old_index}/{STATE_DIR}/ (rebuilt on next sync)")
    actions.append(f"write {DEFAULT_INDEX_DIR}/{CONFIG_NAME}; remove {LEGACY_MARKER_NAME}")

    if dry_run:
        if not quiet:
            print(f"migrate (dry run) — {root}")
            for a in actions:
                print(f"  - {a}")
            print("\nNothing changed. Re-run without --dry-run to apply.")
        return result

    new_home.mkdir(parents=True, exist_ok=True)
    for src, dst in moves:
        if dst.exists():
            continue  # never clobber something already in the new home
        shutil.move(str(src), str(dst))
    if secrets_move and not secrets_move[1].exists():
        shutil.move(str(secrets_move[0]), str(secrets_move[1]))

    # Drop the rebuildable state; remove the emptied old home.
    state = (new_home if same_home else old_home) / STATE_DIR
    if state.is_dir():
        shutil.rmtree(state)
    if not same_home and old_home.is_dir():
        # Remove the emptied old home, including any OS-junk left behind — but
        # keep it if a real file survived (e.g. a skipped name collision).
        real_leftover = [p for p in old_home.iterdir() if p.name not in SKIP_FILE_NAMES]
        if not real_leftover:
            shutil.rmtree(old_home)

    # Rewrite the marker inside the new home, then remove the legacy root marker.
    legacy_cfg["index_dir"] = DEFAULT_INDEX_DIR
    (new_home / CONFIG_NAME).write_text(
        json.dumps(legacy_cfg, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (root / LEGACY_MARKER_NAME).unlink(missing_ok=True)

    result["migrated"] = True
    if not quiet:
        print(f"migrated to v2 layout: {root}")
        print(f"  home : {DEFAULT_INDEX_DIR}/  (in project)")
        print("  next : run `docdex sync` to build the external state cache.")
    return result
