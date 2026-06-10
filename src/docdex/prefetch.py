"""Best-effort materialization of cloud placeholder files (OneDrive, iCloud).

Reads one byte of every supported file so the file provider downloads it
before extraction tries to parse it. Never moves or modifies sources. On
macOS, a failed read triggers `brctl download` (iCloud) and one retry.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from docdex import extract as ex
from docdex.config import Project
from docdex.walk import iter_source_files

CLOUD_SHORTCUTS = {".gdoc", ".gsheet", ".gslides"}


def _brctl_download(path: Path) -> None:
    if sys.platform != "darwin":
        return
    try:
        subprocess.run(["brctl", "download", str(path)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=30)
    except Exception:  # noqa: BLE001 - brctl is best-effort
        pass


def _touch_read(path: Path) -> tuple:
    try:
        with open(path, "rb") as f:
            f.read(1)
        return True, ""
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def run_prefetch(project: Project, dry_run: bool = False, limit: int = 0,
                 quiet: bool = False) -> dict:
    attempted = ok = failed = shortcuts = 0
    failures = []
    shortcut_paths = []

    for rel, abs_path in iter_source_files(project):
        ext = abs_path.suffix.lower()
        if ext in CLOUD_SHORTCUTS:
            shortcuts += 1
            if len(shortcut_paths) < 10:
                shortcut_paths.append(rel)
            continue
        if not ex.is_supported(abs_path):
            continue
        if limit and attempted >= limit:
            break
        attempted += 1
        if dry_run:
            continue
        success, detail = _touch_read(abs_path)
        if not success:
            _brctl_download(abs_path)
            success, detail = _touch_read(abs_path)
        if success:
            ok += 1
        else:
            failed += 1
            if len(failures) < 10:
                failures.append(f"{rel} :: {detail}")

    if not quiet:
        print("Cloud prefetch summary")
        print(f"  attempted: {attempted}  readable: {ok}  failed: {failed}")
        if shortcuts:
            print(f"  cloud-native shortcuts (.gdoc/.gsheet): {shortcuts} — "
                  "export from browser to index them")
            for p in shortcut_paths:
                print(f"    - {p}")
        if failures:
            print("  read failures:")
            for f in failures:
                print(f"    - {f}")
    return {"attempted": attempted, "ok": ok, "failed": failed,
            "shortcuts": shortcuts, "failures": failures}
