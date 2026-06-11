"""Incremental sync: reconcile the inventory with the filesystem.

  NEW / CHANGED   -> re-extract into _state/extracted/
  RENAMED / MOVED -> sha1 match at a new path; cache is copied, not re-extracted
  DELETED         -> soft-deleted to inventory_history.tsv
  UNCHANGED       -> skipped (unless --backfill and the cache is missing)

Empty extractions (e.g. scanned PDFs) are recorded with status `empty` and a
zero-byte cache file. They are *not* retried on every run and are reported
separately from real cache gaps — they are vision/OCR candidates, not errors.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import time
from pathlib import Path
from typing import Dict, Optional

from docdex import extract as ex
from docdex.config import Project, ensure_state_dirs, utc_now_iso
from docdex.inventory import (
    append_history, read_extract_status, read_inventory, stat_row, write_extract_status,
    write_tsv,
)
from docdex.walk import iter_source_files

LOCK_STALE_SECONDS = 1800


class SyncLocked(Exception):
    pass


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # process exists but is owned by another user
    except OSError:
        return False
    return True


def _lock_payload() -> str:
    return json.dumps({"pid": os.getpid(), "host": socket.gethostname(),
                       "ts": time.time()})


def _lock_is_reclaimable(lock: Path) -> bool:
    """True if an existing lock can be safely taken over: its owning process is
    gone on this host, or (fallback) the lock has aged past the stale timeout.

    The PID check makes an interrupted sync (SIGKILL/crash) recover on the very
    next run instead of blocking for 30 minutes; a lock from another host or an
    older docdex (which wrote just a PID number) falls back to the age check.
    """
    try:
        info = json.loads(lock.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        info = None
    if isinstance(info, dict) and info.get("host") == socket.gethostname():
        pid = info.get("pid")
        if isinstance(pid, int) and not _pid_alive(pid):
            return True
    try:
        return time.time() - lock.stat().st_mtime > LOCK_STALE_SECONDS
    except OSError:
        return False


def acquire_lock(project: Project) -> bool:
    lock = project.lock_path
    if lock.exists():
        if _lock_is_reclaimable(lock):
            try:
                lock.unlink()
            except OSError:
                return False
        else:
            return False
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(_lock_payload(), encoding="utf-8")
        return True
    except OSError:
        return False


def release_lock(project: Project) -> None:
    try:
        project.lock_path.unlink()
    except OSError:
        pass


def cache_has_text(dest: Path) -> bool:
    try:
        return dest.exists() and dest.stat().st_size > 0
    except OSError:
        return False


class _Extractor:
    """Extraction with status bookkeeping for one sync run."""

    def __init__(self, project: Project, statuses: Dict[str, dict],
                 allow_large: bool = False):
        self.project = project
        self.statuses = statuses
        self.allow_large = allow_large
        self.error_lines: list = []
        self.counts = {"ok": 0, "empty": 0, "failed": 0, "unsupported": 0,
                       "skipped": 0}

    def _record(self, rel: str, status: str, chars: int = 0, detail: str = "") -> None:
        self.statuses[rel] = {
            "path": rel, "status": status, "chars": str(chars),
            "detail": detail, "ts": utc_now_iso(),
        }
        self.counts[status] = self.counts.get(status, 0) + 1

    def refresh(self, rel: str, abs_path: Path, force: bool = False) -> None:
        if not ex.is_supported(abs_path):
            self._record(rel, "unsupported", detail=abs_path.suffix.lower())
            return
        cap = self.project.max_extract_bytes
        if cap and not self.allow_large:
            try:
                size = abs_path.stat().st_size
            except OSError:
                size = 0
            if size > cap:
                self._record(
                    rel, "skipped",
                    detail=f"{size // (1024 * 1024)} MB > {cap // (1024 * 1024)} MB "
                           "cap (raise max_extract_mb or use --allow-large-text)")
                return
        dest = self.project.cache_path_for(rel)
        if not force:
            try:
                if dest.exists() and dest.stat().st_mtime >= abs_path.stat().st_mtime:
                    return  # cache (possibly empty) is current
            except OSError:
                pass
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            text = ex.extract(str(abs_path))
        except Exception as e:  # noqa: BLE001 - any parser error is a data point
            self.error_lines.append(f"FAIL\t{rel}\t{type(e).__name__}: {e}")
            self._record(rel, "failed", detail=f"{type(e).__name__}: {e}"[:300])
            return
        if isinstance(text, str) and text.startswith(ex.UNSUPPORTED_PREFIX):
            self._record(rel, "unsupported", detail=text[:120])
            return
        text = text or ""
        dest.write_text(text, encoding="utf-8")
        if text.strip():
            self._record(rel, "ok", chars=len(text))
        else:
            self._record(rel, "empty", detail="no extractable text (scan/image?)")

    def copy_renamed(self, old_rels: list, new_rel: str) -> bool:
        new_dest = self.project.cache_path_for(new_rel)
        if new_dest.exists():
            return True
        for old_rel in old_rels:
            old_dest = self.project.cache_path_for(old_rel)
            if old_dest.exists():
                new_dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(old_dest, new_dest)
                old_status = self.statuses.get(old_rel)
                if old_status:
                    self.statuses[new_rel] = dict(old_status, path=new_rel)
                return True
        return False


def stale_topical_report(changed_paths, project: Project) -> dict:
    """Naive heuristic: flag NN_*.md files that mention a changed top folder."""
    flagged: dict = {}
    topicals = sorted(project.index_dir.glob("[0-9][0-9]_*.md"))
    if not topicals:
        return flagged
    folders = sorted({p.split("/", 1)[0] for p in changed_paths if "/" in p})
    for t in topicals:
        try:
            content = t.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for folder in folders:
            if folder and folder in content:
                flagged.setdefault(t.name, set()).add(folder)
    return flagged


def run_sync(project: Project, dry_run: bool = False, no_hash: bool = False,
             no_extract: bool = False, backfill: bool = False,
             allow_large: bool = False, quiet: bool = False) -> dict:
    if not dry_run:
        ensure_state_dirs(project)
        if not acquire_lock(project):
            raise SyncLocked(
                "another sync appears to be running. If you're sure none is, "
                f"delete the lock file: {project.lock_path}")

    statuses = read_extract_status(project)
    extractor = _Extractor(project, statuses, allow_large=allow_large)
    try:
        old = read_inventory(project.inventory_path)
        old_by_sha: Dict[str, list] = {}
        for r in old.values():
            if r.get("sha1"):
                old_by_sha.setdefault(r["sha1"], []).append(r["path"])

        new_rows: Dict[str, dict] = {}
        counts = {"new": 0, "changed": 0, "renamed": 0, "unchanged": 0}
        may_write = not dry_run and not no_extract

        for rel, abs_path in iter_source_files(project):
            row = stat_row(rel, abs_path, do_hash=not no_hash)
            if row is None:
                continue
            new_rows[rel] = row
            prev = old.get(rel)
            if prev is None:
                # A rename means an old path with this content has *gone*. If a
                # twin still exists on disk, this is a new (duplicate) file, not
                # a rename — otherwise copying one file is miscounted as moving.
                rename_sources = [
                    old_rel for old_rel in old_by_sha.get(row["sha1"], [])
                    if not (project.root / old_rel).exists()
                ] if row["sha1"] else []
                if rename_sources:
                    counts["renamed"] += 1
                    if may_write and not extractor.copy_renamed(rename_sources, rel):
                        extractor.refresh(rel, abs_path, force=True)
                else:
                    counts["new"] += 1
                    if may_write:
                        extractor.refresh(rel, abs_path)
            else:
                same_hash = bool(row["sha1"]) and row["sha1"] == prev.get("sha1")
                same_meta = (row["mtime_iso"] == prev.get("mtime_iso")
                             and row["size"] == prev.get("size"))
                identical = same_hash if row["sha1"] and prev.get("sha1") else same_meta
                if identical:
                    counts["unchanged"] += 1
                    if may_write and ex.is_supported(abs_path):
                        dest = project.cache_path_for(rel)
                        if backfill and not dest.exists():
                            extractor.refresh(rel, abs_path, force=True)
                        elif not dest.exists() and statuses.get(rel, {}).get("status") not in ("unsupported",):
                            extractor.refresh(rel, abs_path)
                else:
                    counts["changed"] += 1
                    if may_write:
                        extractor.refresh(rel, abs_path, force=True)

        sha_now = {r["sha1"] for r in new_rows.values() if r.get("sha1")}
        deleted_rows = [
            prev for path, prev in old.items()
            if path not in new_rows
            and not (prev.get("sha1") and prev["sha1"] in sha_now)
        ]

        if not dry_run:
            if deleted_rows:
                append_history(project, deleted_rows, action="deleted")
            write_tsv(project.inventory_path,
                      sorted(new_rows.values(), key=lambda r: r["path"]))
            for stale_path in set(statuses) - set(new_rows):
                statuses.pop(stale_path, None)
            write_extract_status(project, statuses)
            if extractor.error_lines:
                with open(project.errors_log, "a", encoding="utf-8") as f:
                    f.write(f"=== sync {utc_now_iso()} ===\n")
                    f.write("\n".join(extractor.error_lines) + "\n")

        changed_paths = [
            p for p, r in new_rows.items()
            if p not in old or (r.get("sha1") and r["sha1"] != old[p].get("sha1"))
        ]
        stale = stale_topical_report(changed_paths, project)

        totals = {
            "total": len(new_rows), "deleted": len(deleted_rows), **counts,
            "extracted": dict(extractor.counts), "stale_topicals": sorted(stale),
            "dry_run": dry_run,
        }

        if not quiet:
            mode = "DRY-RUN" if dry_run else "updated"
            print(f"  root        : {project.root}")
            print(f"  index dir   : {project.index_dir_name}")
            print(f"  inventory   : {project.rel_to_root(project.inventory_path)}  ({mode})")
            print(f"  total files : {totals['total']}")
            for key in ("new", "changed", "renamed", "unchanged", "deleted"):
                print(f"  {key:<11} : {totals[key]}")
            exc = extractor.counts
            print(f"  extracted   : ok={exc['ok']} empty={exc['empty']} "
                  f"failed={exc['failed']} unsupported={exc['unsupported']} "
                  f"skipped={exc['skipped']}")
            if stale:
                print("\nTopical files to review (mention a changed folder):")
                for fname, folders in sorted(stale.items()):
                    print(f"  - {fname} :: {', '.join(sorted(folders))}")

        if not dry_run:
            _write_last_run(project, totals)
        return totals
    finally:
        if not dry_run:
            release_lock(project)


def _write_last_run(project: Project, totals: dict) -> None:
    exc = totals["extracted"]
    lines = [
        "docdex sync summary",
        f"Time: {utc_now_iso()}",
        f"Root: {project.root}",
        "",
        f"Total files: {totals['total']}",
        f"New: {totals['new']}, Changed: {totals['changed']}, "
        f"Renamed: {totals['renamed']}, Unchanged: {totals['unchanged']}, "
        f"Deleted: {totals['deleted']}",
        f"Extraction: ok={exc['ok']}, empty={exc['empty']}, "
        f"failed={exc['failed']}, unsupported={exc['unsupported']}, "
        f"skipped={exc.get('skipped', 0)}",
        "",
    ]
    if totals["stale_topicals"]:
        lines.append("Topical files to review: " + ", ".join(totals["stale_topicals"]))
    else:
        lines.append("Topical files to review: none flagged.")
    project.last_run_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def compute_status(project: Project) -> dict:
    """Cheap staleness check: stat-only walk, no hashing, no writes."""
    old = read_inventory(project.inventory_path)
    statuses = read_extract_status(project)
    current: Dict[str, dict] = {}
    for rel, abs_path in iter_source_files(project):
        row = stat_row(rel, abs_path, do_hash=False)
        if row:
            current[rel] = row

    added = sorted(set(current) - set(old))
    deleted = sorted(set(old) - set(current))
    changed = sorted(
        p for p in set(current) & set(old)
        if current[p]["size"] != old[p].get("size")
        or current[p]["mtime_iso"] != old[p].get("mtime_iso")
    )

    missing_cache, no_text, failed = [], [], []
    for rel in old:
        if not ex.is_supported(rel):
            continue
        status = statuses.get(rel, {}).get("status", "")
        cache = project.cache_path_for(rel)
        if status == "empty":
            no_text.append(rel)
        elif status == "failed":
            failed.append(rel)
        elif status == "skipped":
            continue  # intentionally not extracted (too large) — not a gap
        elif not cache.exists():
            missing_cache.append(rel)

    update_newer = []
    if project.inventory_path.exists():
        inv_mtime = project.inventory_path.stat().st_mtime
        if project.update_dir.exists():
            for p in project.update_dir.rglob("*"):
                try:
                    if p.is_file() and not p.name.startswith("~$") and p.stat().st_mtime > inv_mtime:
                        update_newer.append(project.rel_to_root(p))
                except OSError:
                    continue

    return {
        "inventory_rows": len(old),
        "source_files": len(current),
        "added": added, "deleted": deleted, "changed": changed,
        "update_newer": update_newer,
        "missing_cache": missing_cache, "no_text": no_text, "failed": failed,
        "stale": bool(added or deleted or changed or update_newer),
        "gaps": bool(missing_cache or failed),
    }
