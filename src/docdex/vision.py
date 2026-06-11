"""Vision/OCR task queue.

Indexing should not hallucinate image content. This module queues visual
sources (images, image-heavy PPTX, scanned PDFs, no-text files) for one-time
captioning/OCR by a multimodal LLM or a human. Notes are written to
`<index>/vision_notes/`, which IS part of the indexable tree — the next
`docdex sync` makes them searchable like any other document.
"""
from __future__ import annotations

import csv
import hashlib
import shutil
import zipfile
from pathlib import Path
from typing import Set

from docdex import extract as ex
from docdex.config import NOTES_DIR, Project, _truncate_utf8
from docdex.inventory import read_extract_status, read_inventory

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".gif", ".bmp"}
LOW_TEXT_THRESHOLD = 300

PROMPT_TEMPLATE = """# Vision/OCR indexing queue

Process `manifest.tsv` in small batches. For each pending row:

1. Open the source file and any extracted assets listed.
2. Write a markdown note at the `note` path shown in the manifest.
3. Use this exact header so docdex can track completion:

```
# Vision/OCR note
Source: <source path from manifest>
Reason: <reason from manifest>

## Visual/OCR Summary
...

## Search Keywords
...

## Extracted Text / Captions
...
```

Capture visible text, tables, slide titles, chart labels, logos, and any
distinctive diagram/image description. Do not infer facts not visible in the
file unless clearly marked as inference.

When notes are written, run `docdex sync` — notes live inside the indexable
tree and become searchable immediately.
"""


def _short_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()[:12]


def manifest_path(project: Project) -> Path:
    return project.vision_dir / "manifest.tsv"


def note_path_for(project: Project, rel: str) -> Path:
    flat = rel.replace("/", "__").replace(" ", "_")
    stem = _truncate_utf8(Path(flat).stem, 120)
    return project.notes_dir / f"{stem}.{_short_hash(rel)}.md"


def _asset_dir_for(project: Project, rel: str) -> Path:
    return project.vision_dir / "assets" / _short_hash(rel)


def _copy_image(project: Project, rel: str) -> list:
    src = project.root / rel
    out_dir = _asset_dir_for(project, rel)
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / src.name
    if not dest.exists():
        try:
            shutil.copy2(src, dest)
        except OSError:
            return []
    return [dest]


def _extract_pptx_images(project: Project, rel: str, cap: int = 60) -> list:
    src = project.root / rel
    out_dir = _asset_dir_for(project, rel)
    assets = []
    try:
        with zipfile.ZipFile(src) as zf:
            for name in zf.namelist():
                if not name.startswith("ppt/media/"):
                    continue
                if Path(name).suffix.lower() not in IMAGE_EXTS:
                    continue
                out_dir.mkdir(parents=True, exist_ok=True)
                dest = out_dir / Path(name).name
                if not dest.exists():
                    dest.write_bytes(zf.read(name))
                assets.append(dest)
                if len(assets) >= cap:
                    break
    except Exception:  # noqa: BLE001 - corrupt pptx is not queue-fatal
        return []
    return assets


def _is_low_text(project: Project, rel: str) -> bool:
    cache = project.cache_path_for(rel)
    try:
        if not cache.exists():
            return True
        return len(cache.read_text(encoding="utf-8", errors="replace").strip()) < LOW_TEXT_THRESHOLD
    except OSError:
        return True


def existing_note_sources(project: Project) -> Set[str]:
    done: Set[str] = set()
    if not project.notes_dir.exists():
        return done
    for p in project.notes_dir.glob("*.md"):
        try:
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("Source: "):
                    done.add(line[len("Source: "):].strip())
                    break
        except OSError:
            continue
    return done


def create_queue(project: Project, quiet: bool = False) -> dict:
    project.vision_dir.mkdir(parents=True, exist_ok=True)
    project.notes_dir.mkdir(parents=True, exist_ok=True)
    done = existing_note_sources(project)
    notes_prefix = f"{project.index_dir_name}/{NOTES_DIR}/"

    scaffold = {f"{project.index_dir_name}/{NOTES_DIR}/README.md",
                f"{project.index_dir_name}/Update/README.md"}
    statuses = read_extract_status(project)
    rows = []
    for rel in read_inventory(project.inventory_path):
        if rel in done or rel.startswith(notes_prefix) or rel in scaffold:
            continue
        if statuses.get(rel, {}).get("status") == "skipped":
            continue  # too large to extract — not a vision/OCR candidate
        ext = Path(rel).suffix.lower()

        reason = ""
        assets = []
        if ext in IMAGE_EXTS:
            reason = "image-file"
            assets = _copy_image(project, rel)
        elif ext == ".pptx":
            assets = _extract_pptx_images(project, rel)
            if assets:
                reason = "ppt-embedded-images"
        elif ext == ".pdf" and _is_low_text(project, rel):
            reason = "pdf-low-or-no-text"
        elif ex.is_supported(rel) and _is_low_text(project, rel):
            reason = "file-low-or-no-text"
        if not reason:
            continue
        rows.append({
            "status": "pending",
            "reason": reason,
            "path": rel,
            "assets": ";".join(project.rel_to_root(p) for p in assets),
            "note": project.rel_to_root(note_path_for(project, rel)),
        })

    with open(manifest_path(project), "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["status", "reason", "path", "assets", "note"],
                                delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    (project.vision_dir / "VISION_TASKS.md").write_text(PROMPT_TEMPLATE, encoding="utf-8")

    if not quiet:
        print(f"Vision queue: {len(rows)} pending tasks")
        print(f"  manifest: {project.rel_to_root(manifest_path(project))}")
        print(f"  notes go to: {project.rel_to_root(project.notes_dir)}/")
    return {"pending": len(rows)}


def queue_status(project: Project, quiet: bool = False) -> dict:
    mpath = manifest_path(project)
    if not mpath.exists():
        if not quiet:
            print("Vision queue: not created yet (run `docdex vision create`)")
        return {"total": 0, "done": 0, "pending": 0, "exists": False}
    done_sources = existing_note_sources(project)
    total = done = 0
    with open(mpath, "r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            total += 1
            if row.get("path") in done_sources:
                done += 1
    result = {"total": total, "done": done, "pending": total - done, "exists": True}
    if not quiet:
        print(f"Vision queue: total={total} done={done} pending={total - done}")
    return result
