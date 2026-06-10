"""Aggregate context dumps: one CONTEXT_<top-folder>.txt per top-level folder.

Long-context models can load a single dump and grep within it — the bulk
tier that complements curated topical files and ranked search.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from docdex.config import Project
from docdex.inventory import read_inventory

HEADER_LINE = "=" * 60


def parse_size(s: str) -> int:
    s = s.strip().upper()
    for suffix, mult in (("K", 1024), ("M", 1024 ** 2), ("G", 1024 ** 3)):
        if s.endswith(suffix):
            return int(float(s[:-1]) * mult)
    return int(s)


def safe_filename(folder: str) -> str:
    return re.sub(r"[^A-Za-z0-9_\-]", "_", folder)


def _write_dump(project: Project, folder: str, rows: List[dict],
                max_bytes: Optional[int]) -> Tuple[List[Path], int, int]:
    safe = safe_filename(folder)
    project.dumps_dir.mkdir(parents=True, exist_ok=True)
    written: List[Path] = []
    part = 1
    out_path = project.dumps_dir / f"CONTEXT_{safe}.txt"
    fp = open(out_path, "w", encoding="utf-8")
    fp.write(f"# Context dump: {folder}\n")
    fp.write(f"# Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}\n\n")
    cur_bytes = 0
    files_in_part = 0
    dumped = 0
    skipped = 0

    for row in sorted(rows, key=lambda r: r["path"]):
        rel = row["path"]
        cache = project.cache_path_for(rel)
        try:
            if not cache.exists() or cache.stat().st_size == 0:
                skipped += 1
                continue
            text = cache.read_text(encoding="utf-8", errors="replace")
        except OSError:
            skipped += 1
            continue
        chunk = (
            f"\n\n{HEADER_LINE}\nFILE: {rel}\nMTIME: {row.get('mtime_iso', '')}\n"
            f"SHA1: {row.get('sha1', '')[:12]}\n{HEADER_LINE}\n" + text.strip() + "\n"
        )
        encoded = len(chunk.encode("utf-8"))
        if max_bytes and cur_bytes + encoded > max_bytes and files_in_part > 0:
            fp.close()
            written.append(out_path)
            part += 1
            out_path = project.dumps_dir / f"CONTEXT_{safe}_part{part}.txt"
            fp = open(out_path, "w", encoding="utf-8")
            fp.write(f"# Context dump (part {part}): {folder}\n\n")
            cur_bytes = 0
            files_in_part = 0
        fp.write(chunk)
        cur_bytes += encoded
        files_in_part += 1
        dumped += 1

    fp.close()
    written.append(out_path)
    return written, dumped, skipped


def build_dumps(project: Project, folder: Optional[str] = None,
                max_bytes: Optional[int] = None, quiet: bool = False) -> dict:
    by_folder: Dict[str, List[dict]] = {}
    for rel, row in read_inventory(project.inventory_path).items():
        top = project.top_folder_for(rel)
        if folder and top != folder:
            continue
        by_folder.setdefault(top, []).append(row)

    manifest_lines = ["folder\tparts\tfiles_dumped\tskipped_no_cache\tbytes"]
    grand_dumped = grand_skipped = 0
    for top, rows in sorted(by_folder.items()):
        paths, dumped, skipped = _write_dump(project, top, rows, max_bytes)
        total_bytes = sum(p.stat().st_size for p in paths)
        manifest_lines.append(f"{top}\t{len(paths)}\t{dumped}\t{skipped}\t{total_bytes}")
        grand_dumped += dumped
        grand_skipped += skipped
        if not quiet:
            print(f"  {top:36s} parts={len(paths):2d} dumped={dumped:5d} "
                  f"skipped={skipped:4d} bytes={total_bytes}")

    (project.dumps_dir / "_manifest.tsv").write_text(
        "\n".join(manifest_lines) + "\n", encoding="utf-8")
    return {"folders": len(by_folder), "dumped": grand_dumped, "skipped": grand_skipped}
