"""Inventory TSV I/O, file hashing, and extraction-status snapshots."""
from __future__ import annotations

import csv
import hashlib
import io
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, Optional

from docdex.config import Project, StateError, utc_now_iso

HEADER = ["path", "size", "mtime_iso", "sha1", "ext", "folder"]
STATUS_HEADER = ["path", "status", "chars", "detail", "ts"]
HASH_SIZE_LIMIT = 200 * 1024 * 1024  # skip hashing files >= 200 MB


def sha1_of(path, chunk: int = 65536) -> str:
    h = hashlib.sha1()
    try:
        with open(path, "rb") as f:
            while True:
                b = f.read(chunk)
                if not b:
                    break
                h.update(b)
        return h.hexdigest()
    except OSError:
        return ""


def stat_row(rel: str, abs_path: Path, do_hash: bool) -> Optional[dict]:
    try:
        st = abs_path.stat()
    except OSError:
        return None
    mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sha = ""
    if do_hash and 0 <= st.st_size < HASH_SIZE_LIMIT:
        sha = sha1_of(abs_path)
    folder = str(Path(rel).parent)
    return {
        "path": rel,
        "size": str(st.st_size),
        "mtime_iso": mtime,
        "sha1": sha,
        "ext": abs_path.suffix.lower(),
        "folder": "." if folder == "." else folder,
    }


def _read_validated_tsv(path: Path, expected_header, label: str) -> list:
    """Read a docdex TSV snapshot, failing friendly on corruption.

    Raises StateError on NUL bytes, a header that doesn't match the expected
    columns, or a ragged row — so a damaged state file is reported clearly
    instead of being parsed into silently-wrong rows (or crashing the csv
    module on an embedded NUL, which it does on Python ≤ 3.10 but not 3.11+).
    Blank lines are tolerated. Returns a list of column→value dicts.
    """
    try:
        with open(path, "r", encoding="utf-8", newline="") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError) as e:
        raise StateError(
            f"{label} is corrupt and could not be read ({e}). "
            "Run `docdex sync` to rebuild it.")
    if "\x00" in text:
        raise StateError(
            f"{label} is corrupt (contains NUL bytes). "
            "Run `docdex sync` to rebuild it.")
    try:
        reader = csv.reader(io.StringIO(text), delimiter="\t")
        try:
            header = next(reader)
        except StopIteration:
            return []
        if header != list(expected_header):
            raise StateError(
                f"{label} header does not match the expected format. "
                "Run `docdex sync` to rebuild it.")
        out = []
        for parts in reader:
            if not any(field.strip() for field in parts):
                continue  # tolerate blank lines
            if len(parts) != len(header):
                raise StateError(
                    f"{label} has a malformed row ({len(parts)} of "
                    f"{len(header)} columns). Run `docdex sync` to rebuild it.")
            out.append(dict(zip(header, parts)))
        return out
    except csv.Error as e:
        raise StateError(
            f"{label} is corrupt and could not be read ({e}). "
            "Run `docdex sync` to rebuild it.")


def read_inventory(path: Path) -> Dict[str, dict]:
    rows: Dict[str, dict] = {}
    if not path.exists():
        return rows
    for row in _read_validated_tsv(path, HEADER, "inventory"):
        row.setdefault("mtime_iso", "")
        row.setdefault("sha1", "")
        rows[row["path"]] = row
    return rows


def write_tsv(path: Path, rows: Iterable[dict], header=HEADER) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter="\t", lineterminator="\n")
        writer.writerow(header)
        for r in rows:
            writer.writerow([r.get(h, "") for h in header])
    os.replace(tmp, path)


def append_history(project: Project, rows: Iterable[dict], action: str) -> None:
    path = project.history_path
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists()
    with open(path, "a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter="\t", lineterminator="\n")
        if new_file:
            writer.writerow(["action", "ts", *HEADER])
        ts = utc_now_iso()
        for r in rows:
            writer.writerow([action, ts, *(r.get(h, "") for h in HEADER)])


def read_extract_status(project: Project) -> Dict[str, dict]:
    """Latest extraction status per path. Snapshot file, rewritten each sync."""
    rows: Dict[str, dict] = {}
    path = project.extract_status_path
    if not path.exists():
        return rows
    for row in _read_validated_tsv(path, STATUS_HEADER, "extract_status"):
        if row.get("path"):
            rows[row["path"]] = row
    return rows


def write_extract_status(project: Project, statuses: Dict[str, dict]) -> None:
    ordered = [statuses[k] for k in sorted(statuses)]
    write_tsv(project.extract_status_path, ordered, header=STATUS_HEADER)
