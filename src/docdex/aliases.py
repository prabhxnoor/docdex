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
from docdex.search import tokenize
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


def _phrase_present(phrase: str, text_stem_list: list) -> bool:
    ps = [stem(t) for t in tokenize(phrase)]
    if not ps:
        return False
    n = len(ps)
    return any(text_stem_list[i:i + n] == ps for i in range(len(text_stem_list) - n + 1))


def triggered_groups(query: str, groups):
    """The alias groups a QUERY asks for: a group fires when the stems of one of
    its phrases are all present in the query.

    ONE rule for the query, used at every point a query's aliases matter — the
    retrieval widening, `keep()`'s notion of a hit, the `~approx` tag, and what
    `--explain` reports. There used to be three. Retrieval widened on this subset
    rule; the evidence test and the tag required the phrase as a contiguous run;
    `--explain` used the contiguous rule too while its comment claimed it matched
    retrieval. So a natural question — "what date does the agreement become
    effective" — reached documents that only say "Commencement Date" and then
    judged and labelled them as though the synonym had never applied: present in
    the packet *because* of the user's alias file, yet shown as an exact match.

    `expand_stems` keeps the stricter contiguous rule because it answers a
    different question: whether a piece of TEXT actually says the phrase.
    """
    stems = [stem(t) for t in tokenize(query)]
    qs = set(stems)
    out = []
    for group in groups:
        for phrase in group:
            ps = {stem(t) for t in tokenize(phrase)}
            if ps and ps <= qs:
                out.append(group)
                break
    return out


def query_stems(query: str, groups):
    """Stems contributed by every group the query triggers — the widening that
    `triggered_groups` justifies, as a stem set."""
    extra = set()
    for group in triggered_groups(query, groups):
        for phrase in group:
            extra |= {stem(t) for t in tokenize(phrase)}
    return extra


def expand_stems(text: str, groups):
    """Extra stems contributed by aliases: for any group a phrase of which is
    present as a CONTIGUOUS stemmed run in `text`, add the stems of ALL the
    group's phrases. Used only to widen retrieval/existence — never to read a
    value.

    For TEXT, not for a query: see `triggered_groups` for the query-side rule.
    """
    stem_list = [stem(t) for t in tokenize(text)]
    extra = set()
    for group in groups:
        if any(_phrase_present(p, stem_list) for p in group):
            for p in group:
                extra |= {stem(t) for t in tokenize(p)}
    return extra
