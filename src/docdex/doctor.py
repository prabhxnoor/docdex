"""Integrity checks for a docdex project, plus an end-to-end self-test."""
from __future__ import annotations

import time
from typing import List, Tuple

from docdex import extract as ex
from docdex import index_db
from docdex import search as searchmod
from docdex import semantic, vision
from docdex.config import (LEGACY_STATE_DIRS, Project,
                           is_hidden_from_desktop_search)
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

    def check_hidden_from_desktop_search(self) -> None:
        """Is docdex's extracted text out of reach of Spotlight / Finder search?

        docdex writes a plain-text copy of every document it extracts. In a location
        the OS indexes, that makes the full text of private documents searchable and
        puts it in the Spotlight store — and returns docdex's copy instead of the
        real file. The state directory's `.noindex` suffix prevents it; this reports
        whether that actually holds for your layout.
        """
        p = self.project
        exposed = [str(d) for d in (p.state_dir, p.extracted_dir, p.dumps_dir,
                                    p.vision_dir)
                   if d.is_dir() and not is_hidden_from_desktop_search(d)]
        # A state directory from before the `.noindex` rename, still on disk because
        # sync has not run or the rename could not complete. Checking only the NEW
        # paths reported "not indexed" while the old one sat beside them holding the
        # same document text, fully indexed — reassurance that was simply false.
        exposed += [str(p.state_dir.parent / old) for old in LEGACY_STATE_DIRS
                    if (p.state_dir.parent / old).is_dir()]
        self.record("hidden from desktop search", not exposed,
                    "extracted text is not indexed by Spotlight" if not exposed
                    else f"EXPOSED to Spotlight: {exposed} — run `docdex sync` to "
                         f"move state into a .noindex directory")

    def check_lexical_index(self) -> None:
        """Can the keyword index actually match anything?

        A failed schema upgrade left both FTS mirrors present but empty while all
        92,490 chunks of text sat in `chunks`. Every query then returned "no matches"
        — for the whole corpus, with no error anywhere — and every check here passed.
        `COUNT(*)` on an external-content FTS5 table reads the content table, so it
        reported a full index; only FTS5's own view of the inverted index reveals it.
        """
        if not index_db.available(self.project):
            self.record("lexical index", True, "not built (run `docdex sync`)")
            return
        conn = index_db.connect(self.project)
        try:
            state = index_db.index_state(conn)
        finally:
            conn.close()
        empty = [t for t, n in state["mirrors"].items() if n == 0]
        unknown = [t for t, n in state["mirrors"].items() if n is None]
        if not state["chunks"]:
            self.record("lexical index", True, "no chunks indexed yet")
        elif state["empty"]:
            self.record("lexical index", False,
                        f"{state['chunks']:,} chunks of text are stored but the "
                        f"index has NOTHING indexed — every search reports no "
                        f"matches; run `docdex sync` to rebuild")
        elif state["incomplete"]:
            worst = min(n for n in state["mirrors"].values() if n is not None)
            self.record("lexical index", False,
                        f"only {worst:,} of {state['chunks']:,} chunks are indexed "
                        f"— searches cover part of your documents; run `docdex "
                        f"sync` to rebuild")
        elif state["unverified"]:
            # Found by adversarial review: this used to fall through to PASS, so an
            # index that could not be checked was reported as checked and healthy.
            self.record("lexical index", False,
                        f"could not verify mirror(s) {unknown} — health unknown, so "
                        f"a search reporting no matches cannot be trusted; run "
                        f"`docdex sync` to rebuild")
        elif state["partial"]:
            # Not fatal, but it degrades ranking silently rather than failing: both
            # mirrors exist so a term selective in either space can win.
            self.record("lexical index", False,
                        f"mirror(s) {empty} have nothing indexed — ranking is "
                        f"degraded; run `docdex sync` to rebuild")
        elif state["missing"]:
            self.record("lexical index", False,
                        f"mirror(s) {state['missing']} absent — this index predates "
                        f"the current schema; run `docdex sync` to rebuild")
        else:
            self.record("lexical index", True,
                        f"{state['chunks']:,} chunks searchable in "
                        f"{len(state['mirrors'])} term spaces")

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
        """Is every supported document's text actually extracted?

        `skipped` is counted separately because it is docdex's own decision, not a
        gap: `sync` declines files over `max_extract_mb` and says so in as many words
        ("intentionally not extracted (too large) — not a gap"). This check had no
        branch for it, so all 19 such files on the real 10.5k-file corpus fell through
        to `missing` and turned a healthy corpus red. The cost was not just the false
        alarm — while they sat in `missing`, that number could not tell anyone whether
        a genuine gap had appeared beside them. Still reported, never hidden: those
        documents really are unsearchable, which is the user's setting to change.
        """
        statuses = read_extract_status(self.project)
        supported = ok = empty = failed = missing = skipped = 0
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
                # Checked before `skipped`: a file the cap now excludes may still have
                # a cache from before the cap changed, and that text IS searchable.
                ok += 1
            elif st == "skipped":
                skipped += 1
            else:
                missing += 1
        healthy = failed == 0 and missing == 0
        detail = (f"supported={supported} ok={ok} no-text={empty} skipped={skipped} "
                  f"failed={failed} missing={missing}")
        if not healthy:
            # A red check that does not say what to do next gets ignored, which is how
            # this one went unexamined long enough to hide behind its own false alarm.
            fixes = []
            if missing:
                fixes.append("`docdex sync --backfill` re-extracts anything with no cache")
            if failed:
                fixes.append(f"see {self.project.extract_status_path.name} for the "
                             f"reason on each failed file")
            detail += " — " + "; ".join(fixes)
        self.record("cache coverage", healthy, detail)

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
    d.check_hidden_from_desktop_search()
    d.check_lexical_index()
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
