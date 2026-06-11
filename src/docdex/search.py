"""Ranked keyword search over the extracted text caches."""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

from docdex.config import Project
from docdex.inventory import read_inventory


def tokenize(query: str) -> List[str]:
    return [t.lower() for t in re.findall(r"[A-Za-z0-9][A-Za-z0-9_\-]{1,}", query)]


def snippet(text: str, query: str, terms: List[str], width: int = 260) -> str:
    lower = text.lower()
    q = query.lower().strip()
    idx = lower.find(q) if q else -1
    if idx < 0:
        hits = [(lower.find(t), t) for t in terms if lower.find(t) >= 0]
        idx = min((i for i, _ in hits), default=0)
    start = max(0, idx - width // 3)
    end = min(len(text), start + width)
    return re.sub(r"\s+", " ", text[start:end]).strip()


def score_text(path: str, text: str, query: str, terms: List[str]) -> int:
    """Coverage-weighted keyword score with term-frequency saturation.

    Each term's contribution is capped (BM25-style) so a document that just
    repeats a common word can't out-rank one that genuinely contains all the
    query terms including the rare/answer-bearing one.
    """
    lower = text.lower()
    path_lower = path.lower()
    score = 0
    matched = 0
    if query.lower() in lower:
        score += 20 * min(3, lower.count(query.lower()))
    for t in terms:
        weight = 3 if len(t) >= 5 else 1
        tf = lower.count(t)
        if tf + path_lower.count(t):
            matched += 1
        score += weight * min(tf, 3)          # saturate term frequency
        score += 5 * min(path_lower.count(t), 2)
    if matched == 0:
        return 0
    coverage = (matched / max(1, len(set(terms)))) ** 2
    return int(score * coverage + 20 * matched)


def run_search(project: Project, query: str, folder: Optional[str] = None,
               limit: int = 8) -> List[Tuple[int, str, str, str]]:
    """Return [(score, source_rel_path, cache_rel_path, snippet)] best-first."""
    terms = tokenize(query)
    if not terms:
        return []
    hits = []
    for rel in read_inventory(project.inventory_path):
        if folder and folder.lower() not in rel.lower():
            continue
        cache = project.cache_path_for(rel)
        try:
            if not cache.exists() or cache.stat().st_size == 0:
                continue
            text = cache.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        score = score_text(rel, text, query, terms)
        if score > 0:
            hits.append((score, rel, project.rel_to_root(cache),
                         snippet(text, query, terms)))
    hits.sort(key=lambda x: (-x[0], x[1]))
    return hits[:limit]
