"""Low-token semantic retrieval index.

Default backend `local-hash-v1`: dependency-free hashed embeddings over word
unigrams, bigrams, and character 5-grams. Deterministic, private, and good
enough to narrow candidates before an LLM reads snippets — not a neural
embedding. Set DOCDEX_EMBED_CMD to a command that reads text on stdin and
prints a JSON float array to plug in a real embedding model.

Rebuilds are incremental: a manifest maps each indexed path to the source
sha1, so only new/changed files are re-embedded and unchanged index lines are
streamed through untouched.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import subprocess
from typing import Dict, Iterator, List, Optional, Tuple

from docdex.config import Project
from docdex.inventory import read_inventory, write_tsv

DIM = 384
CHUNK_CHARS = 1800
OVERLAP = 250
LOCAL_BACKEND = "local-hash-v1"
EMBED_CMD_ENV = "DOCDEX_EMBED_CMD"
MANIFEST_HEADER = ["path", "sha1", "chunks", "backend"]


def norm_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def chunks(text: str, size: int = CHUNK_CHARS, overlap: int = OVERLAP) -> Iterator[Tuple[int, str]]:
    text = norm_text(text)
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        yield start, text[start:end]
        if end == len(text):
            break
        start = max(0, end - overlap)


def _stable_hash(token: str) -> int:
    return int(hashlib.blake2b(token.encode("utf-8", errors="ignore"), digest_size=8).hexdigest(), 16)


def local_hash_embed(text: str) -> List[float]:
    vec = [0.0] * DIM
    lowered = text.lower()
    words = re.findall(r"[a-z0-9][a-z0-9_\-]{1,}", lowered)
    feats: List[str] = list(words)
    feats.extend("_".join(words[i:i + 2]) for i in range(max(0, len(words) - 1)))
    compact = re.sub(r"\s+", " ", lowered)
    feats.extend(compact[i:i + 5] for i in range(0, max(0, len(compact) - 4), 3))
    for feat in feats:
        h = _stable_hash(feat)
        idx = h % DIM
        sign = 1.0 if (h >> 9) & 1 else -1.0
        weight = 1.0 + min(3.0, len(feat) / 12.0)
        vec[idx] += sign * weight
    mag = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [round(v / mag, 6) for v in vec]


def external_embed(text: str, command: str) -> List[float]:
    proc = subprocess.run(command, input=text, text=True, shell=True,
                          capture_output=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"embed command exited {proc.returncode}")
    data = json.loads(proc.stdout)
    if not isinstance(data, list) or not data:
        raise RuntimeError("embed command did not return a JSON float array")
    return [float(x) for x in data]


def current_backend() -> str:
    return "external" if os.environ.get(EMBED_CMD_ENV, "").strip() else LOCAL_BACKEND


def embed(text: str) -> List[float]:
    cmd = os.environ.get(EMBED_CMD_ENV, "").strip()
    if cmd:
        return external_embed(text, cmd)
    return local_hash_embed(text)


def dot(a: List[float], b: List[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _read_manifest(project: Project) -> Dict[str, dict]:
    rows: Dict[str, dict] = {}
    path = project.semantic_manifest_path
    if not path.exists():
        return rows
    with open(path, "r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            if row.get("path"):
                rows[row["path"]] = dict(row)
    return rows


def build(project: Project, force: bool = False, quiet: bool = False) -> dict:
    backend = current_backend()
    inventory = read_inventory(project.inventory_path)

    targets: Dict[str, str] = {}
    for rel, row in inventory.items():
        cache = project.cache_path_for(rel)
        try:
            if cache.exists() and cache.stat().st_size > 0:
                targets[rel] = row.get("sha1", "")
        except OSError:
            continue

    old_manifest = {} if force else _read_manifest(project)
    reuse = {
        rel for rel, sha in targets.items()
        if rel in old_manifest
        and old_manifest[rel].get("sha1") == sha and sha
        and old_manifest[rel].get("backend") == backend
    }
    if not project.semantic_index_path.exists():
        reuse = set()

    to_embed = sorted(set(targets) - reuse)
    tmp = project.semantic_index_path.with_suffix(".jsonl.tmp")
    new_manifest: Dict[str, dict] = {}
    total_chunks = 0

    embedded_files = 0
    with open(tmp, "w", encoding="utf-8") as out:
        if reuse:
            # Zero-chunk files (too short to index) are tracked too, so they
            # are not pointlessly revisited on every rebuild.
            for rel in reuse:
                new_manifest[rel] = {"path": rel, "sha1": targets[rel],
                                     "chunks": "0", "backend": backend}
            with open(project.semantic_index_path, "r", encoding="utf-8") as old:
                for line in old:
                    try:
                        rel = json.loads(line).get("path")
                    except json.JSONDecodeError:
                        continue
                    if rel in reuse:
                        out.write(line if line.endswith("\n") else line + "\n")
                        entry = new_manifest[rel]
                        entry["chunks"] = str(int(entry["chunks"]) + 1)
                        total_chunks += 1
        for rel in to_embed:
            cache = project.cache_path_for(rel)
            try:
                text = cache.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            file_chunks = 0
            for idx, (offset, chunk) in enumerate(chunks(text)):
                if len(chunk) < 40:
                    continue
                out.write(json.dumps({
                    "path": rel, "chunk": idx, "offset": offset,
                    "text": chunk[:500], "vector": embed(chunk),
                }, ensure_ascii=False) + "\n")
                file_chunks += 1
                total_chunks += 1
            new_manifest[rel] = {
                "path": rel, "sha1": targets[rel],
                "chunks": str(file_chunks), "backend": backend,
            }
            if file_chunks:
                embedded_files += 1

    os.replace(tmp, project.semantic_index_path)
    write_tsv(project.semantic_manifest_path,
              [new_manifest[k] for k in sorted(new_manifest)],
              header=MANIFEST_HEADER)
    meta = {
        "backend": backend,
        "files": sum(1 for m in new_manifest.values() if int(m["chunks"]) > 0),
        "tracked_files": len(new_manifest),
        "chunks": total_chunks,
        "chunk_chars": CHUNK_CHARS, "overlap": OVERLAP,
        "reused_files": len(reuse), "embedded_files": embedded_files,
    }
    project.semantic_meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    if not quiet:
        print(f"Semantic index: files={meta['files']} chunks={meta['chunks']} "
              f"backend={backend} (re-embedded {len(to_embed)}, reused {len(reuse)})")
    return meta


def search(project: Project, query: str, folder: Optional[str] = None,
           limit: int = 8) -> List[Tuple[float, dict]]:
    if not project.semantic_index_path.exists():
        raise FileNotFoundError("semantic index missing — run `docdex embed` or `docdex sync`")
    qvec = embed(query)
    hits: List[Tuple[float, dict]] = []
    with open(project.semantic_index_path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if folder and folder.lower() not in row.get("path", "").lower():
                continue
            hits.append((dot(qvec, row["vector"]), row))
    hits.sort(key=lambda x: -x[0])
    return hits[:limit]


def status(project: Project) -> Optional[dict]:
    if not project.semantic_index_path.exists():
        return None
    if project.semantic_meta_path.exists():
        return json.loads(project.semantic_meta_path.read_text(encoding="utf-8"))
    return {"backend": "unknown"}
