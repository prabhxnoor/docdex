"""Ranked keyword search over the extracted text caches."""
from __future__ import annotations

import re
from collections import Counter
from typing import List, Optional, Tuple

from docdex.config import Project
from docdex.inventory import read_inventory
from docdex.stemming import stem


def tokenize(query: str) -> List[str]:
    """Split text into lowercased word tokens, Unicode-aware (DDX-034).

    A token starts with a letter/digit of any script and may contain word
    characters and hyphens, so "Échéance" stays one token instead of breaking
    into the ASCII fragments "ch"/"ance". This is the single tokenizer used for
    search, FTS query construction, form-label matching, value lines, and the
    "tried" display, and it aligns with SQLite FTS5's unicode61 tokenizer (which
    folds both the indexed text and the MATCH terms the same way)."""
    return [t.lower() for t in re.findall(r"[^\W_][\w\-]*", query, re.UNICODE)
            if len(t) >= 2]


def stemmed(text: str) -> set:
    """Porter-stemmed token set of `text` — the stem-aware companion to
    tokenize(), for match-existence and recall. Never use for positions: a stem
    is often a prefix and would not locate the literal word."""
    return {stem(t) for t in tokenize(text)}


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
    """Coverage-weighted keyword score with term-frequency saturation, matching
    on Porter stems so morphological variants (governing/governed) count together.
    The literal-phrase bonus stays on raw text; only term matching is stemmed.
    """
    lower = text.lower()
    text_stems = Counter(stem(t) for t in tokenize(text))
    path_stems = Counter(stem(t) for t in tokenize(path))
    query_stems = {stem(t) for t in terms}
    score = 0
    matched = 0
    if query.lower() in lower:
        score += 20 * min(3, lower.count(query.lower()))
    for qs in query_stems:
        weight = 3 if len(qs) >= 5 else 1
        tf = text_stems.get(qs, 0)
        pf = path_stems.get(qs, 0)
        if tf + pf:
            matched += 1
        score += weight * min(tf, 3)
        score += 5 * min(pf, 2)
    if matched == 0:
        return 0
    coverage = (matched / max(1, len(query_stems))) ** 2
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
