"""The single source-file walker shared by every command.

Rules:
  * Skip directories named in BUILTIN_SKIP_DIRS or the project's skip_dirs,
    at any depth.
  * Inside the index dir, only `Update/` and `vision_notes/` are indexable.
  * Skip dotfiles, Office lock files (`~$...`), OS junk, the project marker,
    and the project's own `./ctx` wrapper.
  * Path-prefix tests are segment-safe: a sibling dir like `_indexes/` is
    not confused with an index dir named `_index`.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator, Tuple

from docdex.config import SKIP_FILE_NAMES, Project


def _is_under(rel: str, prefix: str) -> bool:
    return rel == prefix or rel.startswith(prefix + "/")


def iter_source_files(project: Project) -> Iterator[Tuple[str, Path]]:
    root = project.root
    index_name = project.index_dir_name
    allowed = project.indexable_index_subdirs

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        rel_dir = os.path.relpath(dirpath, root)
        rel = "" if rel_dir == "." else Path(rel_dir).as_posix()

        kept = []
        for d in sorted(dirnames):
            if d in project.skip_dirs or d.startswith("."):
                continue
            # A symlinked directory is not descended into (followlinks=False),
            # but skip it explicitly too unless the project opted in and the
            # target stays inside the root.
            dpath = Path(dirpath) / d
            if dpath.is_symlink() and not (
                    project.follow_symlinks and project.is_within_root(dpath)):
                continue
            sub = f"{rel}/{d}" if rel else d
            if sub == index_name:
                kept.append(d)  # descend so we can reach Update/ and notes
                continue
            if _is_under(sub, index_name) and not any(_is_under(sub, a) for a in allowed):
                continue
            kept.append(d)
        dirnames[:] = kept

        if rel == index_name:
            continue
        if rel and _is_under(rel, index_name) and not any(_is_under(rel, a) for a in allowed):
            continue

        for fn in sorted(filenames):
            if fn in SKIP_FILE_NAMES or fn.startswith("~$") or fn.startswith("."):
                continue
            if not rel and fn in (project.wrapper_name,):
                continue
            abs_path = Path(dirpath) / fn
            # Skip symlinked files by default — following one would index (and
            # cache) content from outside the project, a privacy leak. Opt in
            # via follow_symlinks, and only when the target stays in-project.
            if abs_path.is_symlink() and not (
                    project.follow_symlinks and project.is_within_root(abs_path)):
                continue
            rel_path = f"{rel}/{fn}" if rel else fn
            yield rel_path, abs_path
