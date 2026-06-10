"""Move Update/ inbox files that duplicate corpus files into <index>/bin/.

Dry-run by default; nothing moves without --apply. Only files inside the
Update/ inbox are ever moved — corpus files stay where they are.
"""
from __future__ import annotations

import shutil
from typing import List, Tuple

from docdex.config import Project
from docdex.inventory import read_inventory


def bin_dir(project: Project):
    return project.index_dir / "bin"


def find_duplicates(project: Project) -> List[Tuple[str, str]]:
    """[(update_rel, original_rel)] — Update/ files whose sha1 exists elsewhere."""
    rows = read_inventory(project.inventory_path)
    update_prefix = project.indexable_index_subdirs[0] + "/"
    by_sha = {}
    for rel, r in rows.items():
        if r.get("sha1") and not rel.startswith(update_prefix):
            by_sha.setdefault(r["sha1"], []).append(rel)
    pairs = []
    for rel, r in rows.items():
        if rel.startswith(update_prefix) and r.get("sha1") in by_sha:
            pairs.append((rel, by_sha[r["sha1"]][0]))
    return pairs


def run_dedup(project: Project, apply: bool = False, quiet: bool = False) -> dict:
    update_prefix = project.indexable_index_subdirs[0] + "/"
    pairs = find_duplicates(project)
    moved = skipped = 0
    for upd_rel, orig_rel in pairs:
        sub = upd_rel[len(update_prefix):]
        src = project.root / upd_rel
        dst = bin_dir(project) / sub
        if not src.exists() or dst.exists():
            skipped += 1
            continue
        if not quiet:
            print(f"  [{'move' if apply else 'would move'}] {sub}")
            print(f"      duplicate of: {orig_rel}")
        if apply:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            moved += 1
    if not quiet:
        verb = "moved" if apply else "would move"
        print(f"\n{len(pairs)} duplicate(s) found; {verb} {moved if apply else len(pairs) - skipped}, "
              f"skipped {skipped}")
        if apply and moved:
            print("Run `docdex sync` to drop the moved rows from the inventory.")
        elif pairs and not apply:
            print("Re-run with --apply to move them into "
                  f"{project.rel_to_root(bin_dir(project))}/")
    return {"found": len(pairs), "moved": moved, "skipped": skipped}


def run_restore(project: Project, quiet: bool = False) -> dict:
    restored = skipped = 0
    b = bin_dir(project)
    if b.exists():
        for src in sorted(p for p in b.rglob("*") if p.is_file()):
            dst = project.update_dir / src.relative_to(b)
            if dst.exists():
                skipped += 1
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            restored += 1
    if not quiet:
        print(f"restored {restored}, skipped {skipped} (already present in Update/)")
    return {"restored": restored, "skipped": skipped}
