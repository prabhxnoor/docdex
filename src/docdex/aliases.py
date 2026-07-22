"""User-defined synonym registry (aliases).

Lets a task or form field match documents that name the same thing with a
different word: `legal name` -> `vendor`/`supplier`. Deterministic and
user-owned — read from `<project>/.docdex/aliases.json`; an absent or malformed
file means no aliases (pure stemming). Aliases only WIDEN what is found and which
field label is recognised; they never synthesise or alter a value.
"""
from __future__ import annotations

import json
import logging
from typing import List

from docdex.config import Project
from docdex.search import stemmed, tokenize
from docdex.stemming import stem

log = logging.getLogger("docdex")


def load_aliases(project: Project) -> List[List[str]]:
    """Read the alias file into a list of groups; each group is a list of phrase
    strings that are mutually synonymous (the key plus its synonyms). Absent file
    -> []. Malformed -> [] with a one-line warning (never raises)."""
    path = project.aliases_path
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        log.warning("aliases: %s is unreadable or not valid JSON — ignoring", path.name)
        return []
    if not isinstance(raw, dict):
        log.warning("aliases: %s is not a JSON object — ignoring", path.name)
        return []
    groups: List[List[str]] = []
    for key, syns in raw.items():
        if not isinstance(key, str) or not isinstance(syns, list):
            continue
        phrases = [key.strip().lower()]
        phrases += [s.strip().lower() for s in syns if isinstance(s, str) and s.strip()]
        phrases = [p for p in phrases if tokenize(p)]        # drop empties
        # de-dupe while keeping order
        seen, uniq = set(), []
        for p in phrases:
            if p not in seen:
                seen.add(p)
                uniq.append(p)
        if len(uniq) >= 2:
            groups.append(uniq)
    return groups


def _phrase_stems(phrase: str) -> set:
    return {stem(t) for t in tokenize(phrase)}


def _phrase_present(phrase: str, text_stems: set) -> bool:
    ps = _phrase_stems(phrase)
    return bool(ps) and ps <= text_stems


def expand_stems(text: str, groups: List[List[str]]) -> set:
    """Extra stems contributed by aliases: for any group with a phrase present
    (by stem) in `text`, add the stems of ALL the group's phrases. Used only to
    widen retrieval/existence — never to read a value."""
    tstems = stemmed(text)
    extra: set = set()
    for group in groups:
        if any(_phrase_present(p, tstems) for p in group):
            for p in group:
                extra |= _phrase_stems(p)
    return extra


def label_variants(label: str, groups: List[List[str]]) -> List[set]:
    """Alternative *literal* label-token sets for a form field: for any group a
    phrase of which matches the field label, the group's OTHER phrases become
    alternative labels (their literal token sets), used to read a value after a
    synonym label. Empty when no group matches."""
    lbl_stems = {stem(t) for t in tokenize(label)}
    variants: List[set] = []
    for group in groups:
        if any(_phrase_stems(p) == lbl_stems for p in group):
            for p in group:
                if _phrase_stems(p) != lbl_stems:
                    variants.append(set(tokenize(p)))
    return variants
