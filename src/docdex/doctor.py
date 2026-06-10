"""Integrity checks for a docdex project, plus an end-to-end self-test."""
from __future__ import annotations

import time
from typing import List, Tuple

from docdex import extract as ex
from docdex import search as searchmod
from docdex import semantic, vision
from docdex.config import Project
from docdex.inventory import HEADER, read_extract_status, read_inventory, sha1_of
from docdex.sync import run_sync


class Doctor:
    def __init__(self, project: Project):
        self.project = project
        self.results: List[Tuple[str, bool, str]] = []

    def record(self, name: str, ok: bool, detail: str = "") -> None:
        self.results.append((name, ok, detail))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")

    # ------------------------------------------------------------------ checks
    def check_layout(self) -> None:
        p = self.project
        missing = [d.name for d in (p.index_dir, p.update_dir, p.notes_dir, p.state_dir)
                   if not d.is_dir()]
        self.record("layout", not missing,
                    "all dirs present" if not missing else f"missing: {missing}")

    def check_inventory_schema(self) -> bool:
        path = self.project.inventory_path
        if not path.exists():
            self.record("inventory schema", False, "inventory.tsv missing — run `docdex sync`")
            return False
        with open(path, "r", encoding="utf-8") as f:
            header = f.readline().rstrip("\r\n").split("\t")
        ok = header == HEADER
        self.record("inventory schema", ok, f"{len(header)} columns" if ok else f"header={header}")
        return ok

    def check_rows_on_disk(self, no_sha: bool) -> None:
        missing = sha_mismatch = total = 0
        for rel, row in read_inventory(self.project.inventory_path).items():
            total += 1
            abs_path = self.project.root / rel
            if not abs_path.is_file():
                missing += 1
                continue
            if no_sha or not row.get("sha1") or total % 50 != 0:
                continue
            if sha1_of(abs_path) != row["sha1"]:
                sha_mismatch += 1
        self.record("inventory matches disk", missing == 0 and sha_mismatch == 0,
                    f"rows={total} missing={missing} sha_mismatch={sha_mismatch}")

    def check_cache_coverage(self) -> None:
        statuses = read_extract_status(self.project)
        supported = ok = empty = failed = missing = 0
        for rel in read_inventory(self.project.inventory_path):
            if not ex.is_supported(rel):
                continue
            supported += 1
            st = statuses.get(rel, {}).get("status", "")
            cache = self.project.cache_path_for(rel)
            if st == "empty":
                empty += 1
            elif st == "failed":
                failed += 1
            elif cache.exists() and cache.stat().st_size > 0:
                ok += 1
            else:
                missing += 1
        self.record("cache coverage", failed == 0 and missing == 0,
                    f"supported={supported} ok={ok} no-text={empty} "
                    f"failed={failed} missing={missing}")

    def check_orphan_caches(self) -> None:
        if not self.project.extracted_dir.exists():
            self.record("orphan caches", True, "no extracted/ yet")
            return
        expected = {self.project.cache_path_for(rel)
                    for rel in read_inventory(self.project.inventory_path)}
        orphans = [c for c in self.project.extracted_dir.rglob("*.txt")
                   if c not in expected]
        self.record("orphan caches", True,
                    f"{len(orphans)} stale cache file(s) — harmless; informational")

    def check_semantic(self) -> None:
        meta = semantic.status(self.project)
        if meta is None:
            self.record("semantic index", True, "not built (run `docdex embed`)")
            return
        self.record("semantic index", True,
                    f"backend={meta.get('backend')} files={meta.get('files')} "
                    f"chunks={meta.get('chunks')}")

    def check_vision_notes_indexed(self) -> None:
        """Regression guard: notes that predate the last sync must be indexed."""
        notes = list(self.project.notes_dir.glob("*.md")) if self.project.notes_dir.exists() else []
        if not notes:
            self.record("vision notes indexed", True, "no notes yet")
            return
        inv = read_inventory(self.project.inventory_path)
        inv_mtime = (self.project.inventory_path.stat().st_mtime
                     if self.project.inventory_path.exists() else 0)
        unindexed = [
            n.name for n in notes
            if self.project.rel_to_root(n) not in inv and n.stat().st_mtime < inv_mtime
        ]
        pending = sum(1 for n in notes if self.project.rel_to_root(n) not in inv)
        if unindexed:
            self.record("vision notes indexed", False,
                        f"{len(unindexed)} note(s) missed by sync: {unindexed[:3]}")
        else:
            detail = f"{len(notes)} note(s)"
            if pending:
                detail += f", {pending} awaiting next sync"
            self.record("vision notes indexed", True, detail)

    def e2e_sentinel(self) -> None:
        """Write a sentinel into Update/, sync, search it, delete it, sync."""
        token = f"DOCDEX-E2E-{int(time.time())}"
        sentinel = self.project.update_dir / "_docdex_e2e_sentinel.md"
        try:
            sentinel.write_text(f"# docdex self-test\n\n{token} {token}\n", encoding="utf-8")
            run_sync(self.project, quiet=True)
            rel = self.project.rel_to_root(sentinel)
            in_inventory = rel in read_inventory(self.project.inventory_path)
            hits = searchmod.run_search(self.project, token, limit=3)
            found = any(h[1] == rel for h in hits)
            self.record("e2e sentinel", in_inventory and found,
                        f"inventory={in_inventory} searchable={found}")
        finally:
            sentinel.unlink(missing_ok=True)
            run_sync(self.project, quiet=True)
            still = self.project.rel_to_root(sentinel) in read_inventory(self.project.inventory_path)
            self.record("e2e cleanup", not still, "sentinel soft-deleted")


def run_doctor(project: Project, no_sha: bool = False, e2e: bool = False) -> int:
    d = Doctor(project)
    print(f"docdex doctor — {project.root}")
    d.check_layout()
    if d.check_inventory_schema():
        d.check_rows_on_disk(no_sha)
        d.check_cache_coverage()
        d.check_orphan_caches()
        d.check_semantic()
        d.check_vision_notes_indexed()
        vision.queue_status(project)
        if e2e:
            d.e2e_sentinel()
    fails = sum(1 for _, ok, _ in d.results if not ok)
    print(f"\n{len(d.results) - fails} passed, {fails} failed")
    return 1 if fails else 0
