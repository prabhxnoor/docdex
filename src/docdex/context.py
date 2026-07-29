"""`docdex context` — a token-budgeted evidence packet for an LLM task.

This is what an agent reads instead of opening files: the smallest useful set
of cited excerpts for a task, an explicit account of what was found / weak /
missing / dropped-by-budget, and any conflicts between sources. docdex stays
deterministic — it surfaces and packs evidence with citations; the agent already
in the loop does the reasoning. No LLM is called here.
"""
from __future__ import annotations

import json
import re
from collections import OrderedDict
from datetime import datetime
from typing import List, Optional

from docdex import index_db
from docdex import tokens as tok
from docdex.config import DocdexError, Project
from docdex.inventory import read_inventory
from docdex.search import run_search, snippet, stemmed, tokenize
from docdex.stemming import stem
from docdex import aliases as al

# Lines that look like they carry a concrete value are the best "likely answer"
# candidates. Conservative on purpose — the agent confirms; we only surface.
VALUE_RE = re.compile(
    r"(-?\s?[₹$€£]\s?\d[\d,]*\.?\d*\s*(?:%|percent|crore|lakh|cr\b|mn\b|million|billion|k\b)?)"
    r"|(\b\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}\b)"
    r"|(\b\d{4}[/\-]\d{1,2}[/\-]\d{1,2}\b)"
    r"|(\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{2,4}\b)"
    r"|(-?\d[\d,]*\.?\d*\s*(?:%|percent|crore|lakh|cr\b|mn\b|million|billion|k\b)?)"
    r"|(\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d+)"
    r"|([A-Z0-9]{6,}\d|[0-9]{2}[A-Z]{4,})"            # ID-ish tokens
    r"|([\w.+-]+@[\w-]+\.[\w.-]+)",                    # emails
    re.I,
)
STOPWORDS = {
    "the", "and", "for", "with", "from", "this", "that", "all", "any", "our",
    "fill", "find", "form", "please", "using", "about", "what", "which", "name",
    "many", "much", "have", "does", "did",
}
EXCERPT_CHARS = 360
MAX_PER_SOURCE = 2
MIN_EVIDENCE_SCORE = 0.01   # drop matches that hit only stopwords (BM25 ≈ 0)
HEADER_RESERVE = 80         # rough token headroom for the fixed header + labels


class EmptyTask(DocdexError):
    """Raised when a context task has no searchable terms."""


def _scaffold_rels(project: Project) -> tuple:
    """docdex's own auto-generated instruction/READMEs — they describe docdex,
    not the corpus."""
    idx = project.index_dir_name
    return (
        "CLAUDE.md", "AGENTS.md",
        f"{idx}/HANDOFF.md", f"{idx}/00_MASTER_INDEX.md",
        f"{idx}/Update/README.md", f"{idx}/vision_notes/README.md",
    )


def _scaffold_excludes(project: Project, inv_sha: dict) -> set:
    """Scaffold files to hide from evidence — but only those still *unchanged*
    from what `init` wrote. A user-edited CLAUDE.md is real content and must
    surface like any other file (DDX-036). With no fingerprints (older projects),
    fall back to excluding by name so scaffolds are never cited by accident."""
    names = set(_scaffold_rels(project))
    try:
        fp = json.loads(project.scaffold_fingerprint_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        fp = {}
    if not isinstance(fp, dict) or not fp:
        return names
    keep_hidden = set()
    for rel in names:
        stored, cur = fp.get(rel), inv_sha.get(rel)
        if stored and cur and cur != stored:
            continue                      # edited → treat as user content
        keep_hidden.add(rel)
    return keep_hidden


def _candidates(project: Project, query: str, folder: Optional[str],
                pool: int, exclude: Optional[set] = None,
                alias_groups: Optional[list] = None) -> List[dict]:
    """Unified candidate list from the best available engine, with docdex's own
    scaffold files, the form file itself, and near-zero (stopword-only) matches
    filtered out."""
    skip = exclude or set()
    # Widen the retrieval query itself through aliases: for any group a phrase of
    # which is present (by stem) in the query, add that group's phrases so the
    # engine surfaces docs that name the thing only by a synonym (legal name →
    # vendor). Gated on alias_groups, so with no alias file the query — and thus
    # the whole candidate set and its scores — is byte-identical to before.
    search_query = query
    if alias_groups:
        qstems = stemmed(query)
        extra: List[str] = []
        for group in alias_groups:
            if any(stemmed(p) and stemmed(p) <= qstems for p in group):
                extra += group
        if extra:
            search_query = query + " " + " ".join(extra)
    try:
        rows = index_db.search(project, search_query, folder=folder, limit=pool)
        cands = [{"rel": r["rel"], "chunk": r["chunk_index"], "text": r["text"],
                  "score": r["score"]} for r in rows]
    except FileNotFoundError:
        hits = run_search(project, search_query, folder=folder, limit=pool)
        cands = [{"rel": rel, "chunk": 0, "text": snip, "score": float(score)}
                 for score, rel, _cache, snip in hits]
    content = set(_content_terms(query))
    content_stems = {stem(t) for t in content}
    if alias_groups:
        content_stems |= al.expand_stems(query, alias_groups)

    def keep(c: dict) -> bool:
        if c["rel"] in skip:
            return False
        # Match *existence* is decided by content-term overlap on Porter stems,
        # never by the BM25 display score: a real hit whose score rounds to 0 (a
        # term present in every doc) must not be dropped as "missing" (DDX-030),
        # and a stem-only hit (governing↔governed) must survive too. The score
        # still drives ranking below; it just isn't a truth filter here.
        if not content:
            return c["score"] >= MIN_EVIDENCE_SCORE
        return bool(content_stems & stemmed(c["text"]))

    return [c for c in cands if keep(c)]


def _trim(text: str, limit: int = EXCERPT_CHARS) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + " …"


# A field's value region ends at ';', a newline, a sentence end, or just before
# the *next* 'Label:' that follows a value — so a dense "A: x B: y" line yields
# each field's own value without the catastrophic per-character splitting a bare
# label-lookahead caused.
_STOP = re.compile(r"[;\n]|(?<=[.!?])\s|(?<=\S)\s+[^\W\d_][\w '&/()\-]{0,38}?[:_]\s",
                   re.UNICODE)


def _clauses(text: str) -> List[str]:
    """Split text into clauses on ';', newlines, and sentence ends."""
    parts = re.split(r"[;\n]+|(?<=[.!?])\s+", text)
    return [p.strip() for p in parts if p and p.strip()]


# Same tokenizer as search.tokenize, but keeping offsets. Positions matter here:
# `stemmed()` warns not to use stems to locate words because a stem is usually a
# prefix, so the old `low.rfind(term)` substring search would land mid-word (the
# stem "govern" found inside "government"). Matching token-by-token with real spans
# is what makes stem- and synonym-aware label matching position-safe.
_TOKEN_SPAN_RE = re.compile(r"[^\W_][\w\-]*", re.UNICODE)


def _token_spans(text: str) -> List[tuple]:
    return [(m.group(0).lower(), m.start(), m.end())
            for m in _TOKEN_SPAN_RE.finditer(text) if len(m.group(0)) >= 2]


def _cut_after(text: str, end: int) -> str:
    """The value region that follows a label, cut before the next label/delimiter."""
    after = text[end:]
    stop = _STOP.search(after)
    window = after[:stop.start()] if stop else after
    return re.sub(r"^[\s:_\-]+", "", window).strip()


def _terms_end(spans: List[tuple], label_terms: set, stemwise: bool) -> Optional[int]:
    """End offset just past the FIRST complete occurrence of a label, or None.

    `stemwise=False` requires the literal token ('term' != 'terms'); True collapses
    word endings so a `Governing law` field can match "governed by the laws".

    "First complete" matters: taking the last position of each label token
    independently (as `max(rfind(...))` did) walks past the value whenever a label
    word recurs later — "Payment terms: Net-45 and general terms apply" started the
    window after the SECOND "terms" and returned "apply", dropping a value that was
    present and correctly labelled.
    """
    want = {stem(t) for t in label_terms} if stemwise else set(label_terms)
    if not want:
        return None
    seen: set = set()
    for tok, _start, end in spans:
        key = stem(tok) if stemwise else tok
        if key in want:
            seen.add(key)
            if seen >= want:
                return end          # every label token seen; value follows here
    return None


def _alias_end(spans: List[tuple], label: str, alias_groups) -> Optional[int]:
    """End offset after a declared SYNONYM of this field's label.

    Phrase-level by necessity: `legal name` maps to `vendor`, so there is no
    term-by-term correspondence. Only groups that actually contain this field's own
    label are considered, and a synonym must appear as a contiguous stemmed run —
    never scattered words that merely co-occur.
    """
    label_stems = [stem(t) for t in tokenize(label)]
    if not label_stems or not alias_groups:
        return None
    text_stems = [stem(tok) for tok, _s, _e in spans]
    best = None
    for group in alias_groups:
        phrase_stems = [[stem(t) for t in tokenize(p)] for p in group]
        if label_stems not in phrase_stems:
            continue                       # this group is not about this field
        for ps in phrase_stems:
            if not ps or ps == label_stems:
                continue                   # the label itself is the exact path
            n = len(ps)
            for i in range(len(text_stems) - n + 1):
                if text_stems[i:i + n] == ps:
                    end = spans[i + n - 1][2]
                    # FIRST occurrence in reading order. Two synonyms of one field in
                    # one clause is inherently ambiguous ("Vendor: Acme, Supplier:
                    # Beta"); taking the earliest is what a reader would do, and it
                    # matches the exact path's first-complete rule.
                    best = end if best is None else min(best, end)
    return best


# Label-match precedence, defined once and used both inside _field_answer and for
# the cross-candidate comparison in build_packet.
_HOW_RANK = {"exact": 0, "stem": 1, "alias": 2}


def label_window(text: str, label: str, label_terms: set,
                 alias_groups=None) -> tuple:
    """(value region after this field's label, how it was matched).

    Strict precedence — **exact, then word-ending, then declared synonym** — so a
    literal label always decides when one is present and a synonym can never hijack
    a field that already matched properly. `how` is one of "exact" / "stem" /
    "alias"; anything but "exact" is approximate and gets tagged for the agent.

    Returns (None, None) when the label isn't present at all.
    """
    spans = _token_spans(text)
    for stemwise, how in ((False, "exact"), (True, "stem")):
        end = _terms_end(spans, label_terms, stemwise)
        if end is not None:
            return _cut_after(text, end), how
    end = _alias_end(spans, label, alias_groups)
    if end is not None:
        return _cut_after(text, end), "alias"
    return None, None


def _label_window(text: str, label_terms: set) -> Optional[str]:
    """Exact-only window — kept for callers that must not widen (see label_window)."""
    return label_window(text, " ".join(sorted(label_terms)), label_terms, None)[0]


def _value_lines(text: str, terms: set) -> List[str]:
    """Clauses that mention a query term *by token* (so 'term' no longer matches
    'terms') and carry a concrete value."""
    out = []
    for clause in _clauses(text):
        if (terms & set(tokenize(clause))) and VALUE_RE.search(clause):
            out.append(_trim(clause, 160))
    return out


def _amount(value: str) -> Optional[float]:
    """Normalize a money/number string to a float, applying Indian and metric
    scale words, so 'INR 4.2 crore', '₹4.20 cr' and '42,000,000' compare equal."""
    s = value.lower().replace(",", "")
    m = re.search(r"\d+\.?\d*", s)
    if not m:
        return None
    n = float(m.group(0))
    if "crore" in s or re.search(r"\bcr\b", s):
        n *= 1e7
    elif "lakh" in s or re.search(r"\blac?\b", s):
        n *= 1e5
    elif "billion" in s or re.search(r"\bbn\b", s):
        n *= 1e9
    elif "million" in s or re.search(r"\bmn\b", s):
        n *= 1e6
    elif re.search(r"\bk\b", s):
        n *= 1e3
    if re.match(r"\s*-", value):
        n = -n
    return n


# A date is not an amount. `_amount` reads a date's leading run of digits as a
# number ('31/12/2026' → 31.0), so two dates that share a day (31/12 vs 31/01)
# would collapse to one key and a real date conflict would be silently hidden —
# the very gap the VALUE_RE date fix set out to close. Key dates by their own
# normalized text so distinct dates stay distinct.
_DATE_VALUE_RE = re.compile(
    r"(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}\b)"
    r"|(\d{4}[/\-]\d{1,2}[/\-]\d{1,2}\b)"
    r"|(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{2,4}\b)"
    r"|(\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d+)",
    re.IGNORECASE)


def _value_key(value: str):
    """Comparison key for conflict grouping: equal amounts collapse to one key
    (no false conflict between '₹4.20 cr' and '4.2 crore'); a date keeps its own
    text key (so '31/12/2026' vs '31/01/2027' don't both reduce to 31 and hide a
    conflict); everything else compares by normalized text."""
    if _DATE_VALUE_RE.match(value.strip()):
        d = re.sub(r"\s+", " ", value.strip().lower())
        # A numeric date is normalized (delimiter + zero-padding) so the SAME date
        # written differently ('31-12-2026' / '01/02/2026') is one key, not a false
        # conflict. Month-name dates keep their whitespace/case-normalized text.
        if re.fullmatch(r"\d{1,4}[/\-]\d{1,2}[/\-]\d{1,4}", d):
            d = "/".join(str(int(p)) for p in re.split(r"[/\-]", d))
        return ("date", d)
    n = _amount(value)
    if n is not None:
        return ("num", round(n, 2))
    return ("txt", re.sub(r"\s+", " ", value).strip().lower())


def _field_values(text: str, label: str, label_terms: set,
                  alias_groups=None, foreign_terms: set = None) -> List[tuple]:
    """Every (clause, value) where this field's label is present and a value
    follows it within the label window — never elsewhere in the clause.

    Synonym-aware, so two documents that label the same fact differently ("Vendor"
    vs "Legal name") are compared against each other instead of each being treated
    as a separate, unchallenged fact — a disagreement hidden is a disagreement the
    agent asserts confidently.
    """
    out = []
    for clause in _clauses(text):
        w, _how = label_window(clause, label, label_terms, alias_groups)
        if not w:
            continue
        # Same cross-field guard the answer path applies: a synonym can match the
        # start of a DIFFERENT field's label ("Vendor" inside "Vendor turnover"), and
        # logging that neighbour's value here would invent a disagreement this field
        # never had.
        if foreign_terms and (foreign_terms & set(tokenize(w))):
            continue
        m = VALUE_RE.search(w)
        if m:
            out.append((clause, re.sub(r"\s+", " ", m.group(0)).strip()))
    return out


def _field_answer(text: str, label: str, label_terms: set, foreign_terms: set,
                  alias_groups=None):
    """This field's own label-local answer: (value, display, clean, how) or None.

    The value comes from the window after the label; `clean` is False when that
    window still names another field (a broad/dense line that didn't split), so
    the caller can downgrade it to 'weak' instead of asserting it as found. `how`
    records whether the label was matched literally or only through a word ending /
    declared synonym, so an approximate reading is never presented as certain.

    Exact matches are preferred over approximate ones across the whole chunk, not
    just within a clause: a literal label anywhere must beat a synonym elsewhere.
    """
    best = None                     # (rank, value, display, clean, how)
    rank = _HOW_RANK
    for clause in _clauses(text):
        w, how = label_window(clause, label, label_terms, alias_groups)
        if not w:
            continue
        m = VALUE_RE.search(w)
        if not m:
            continue
        value = re.sub(r"\s+", " ", m.group(0)).strip()
        display = _trim(w, 160)
        clean = not (foreign_terms & set(tokenize(w)))
        # Prefer a literal label, then a clean window, then first seen.
        key = (rank[how], 0 if clean else 1)
        if best is None or key < best[0]:
            best = (key, value, display, clean, how)
        if key == (0, 0):
            break                   # exact label, clean window — nothing can beat it
    return None if best is None else (best[1], best[2], best[3], best[4])


def _content_terms(task: str) -> List[str]:
    return [t for t in tokenize(task) if len(t) >= 4 and t not in STOPWORDS]


def _approx_match(text: str, content: set, alias_extra: Optional[set] = None) -> bool:
    """True when a query content-term matched `text` only approximately — through
    its stem (governing↔governed) or a query-triggered declared synonym
    (legal name↔vendor), not as a literal token."""
    toks = set(tokenize(text))
    tok_stems = {stem(t) for t in toks}
    if content and all(t in toks for t in content):
        return False                      # every query content-term is literal → exact
    for t in content:
        if t not in toks and stem(t) in tok_stems:
            return True
    if alias_extra and (alias_extra & tok_stems):
        return True
    return False


def _pick_field_hit(hits: List[dict], label: str, extra_terms: set) -> Optional[dict]:
    """Rerank a field's candidates by task utility, not raw relevance: prefer a
    chunk that actually carries a value and covers the field's words, over a
    higher-BM25 chunk that merely shares vocabulary with the field label."""
    if not hits:
        return None
    label_terms = set(tokenize(label))

    def utility(h: dict):
        low = h["text"].lower()
        has_value = 1 if _value_lines(h["text"], label_terms | extra_terms) else 0
        coverage = sum(1 for t in label_terms if t in low)
        return (has_value, coverage, h["score"])

    return max(hits, key=utility)


def _utility(cand: dict, terms: set) -> tuple:
    """Task-utility sort key (ascending → best first): prefer chunks that carry a
    value for a query term, then broader term coverage, then BM25, then a stable
    path tiebreak. Reranks the recall from stemming/aliases toward precision."""
    text = cand["text"]
    value_bearing = 1 if _value_lines(text, terms) else 0
    coverage = len({stem(t) for t in terms} & stemmed(text))
    return (-value_bearing, -coverage, -cand["score"], cand["rel"])


def _read_inv(project: Project):
    """(rows, error): rel → inventory row, or ({}, message) on corrupt inventory.

    One read serves both the mtimes (evidence dates / conflict recency) and the
    sha1s (the scaffold-fingerprint check). A corrupt inventory returns the error
    so the packet warns loudly instead of looking healthy over broken state, and
    never swallows the failure into an empty map (DDX-035)."""
    try:
        return (read_inventory(project.inventory_path), None)
    except (DocdexError, OSError, ValueError, UnicodeDecodeError) as e:
        return ({}, str(e))


def _value_near(line: str, terms: set) -> str:
    """The concrete value closest to a query term in the line, normalized.

    Proximity matters: in "Q2 update: the team closed 40 deals", the value about
    "deals" is 40, not the incidental "2" in "Q2". Picking the nearest value to a
    matched term avoids that whole class of false conflicts."""
    low = line.lower()
    positions = [low.find(t) for t in terms if t and t in low]
    best, best_d = "", 10 ** 9
    for m in VALUE_RE.finditer(line):
        if not positions:
            best = m.group(0)
            break
        d = min(abs(m.start() - p) for p in positions)
        if d < best_d:
            best_d, best = d, m.group(0)
    return re.sub(r"\s+", " ", best).strip().lower()


def _freshness(project: Project, check: bool) -> str:
    """Index freshness. Cheap by default (a timestamp, no corpus walk); only the
    full stat-walk staleness check when explicitly requested (DDX-019)."""
    if not project.inventory_path.exists():
        return "not built — run `docdex sync`"
    if check:
        from docdex.sync import compute_status
        try:
            return "STALE — run `docdex sync`" if compute_status(project)["stale"] else "fresh"
        except DocdexError:
            return "unknown"
    try:
        ts = datetime.fromtimestamp(project.inventory_path.stat().st_mtime)
        return (f"indexed {ts.strftime('%Y-%m-%d %H:%M')} — not re-checked "
                "(run `docdex status` to find new files)")
    except OSError:
        return "unknown"


_AUTHORITY_POS = re.compile(r"(?i)\b(executed|signed|final|amended|effective)\b")
_AUTHORITY_NEG = re.compile(r"(?i)\b(draft|wip|working|superseded|archive|old)\b")


def _authority(source: str) -> int:
    """Transparent, deterministic authority HINT from the source path — never a
    resolver. +1 for an executed/signed/final-type name, -1 for a draft-type one,
    else 0. Used only as a same-recency tiebreak and shown as a label."""
    s = source.replace("_", " ")   # underscores are word chars, so \b would miss snake_case
    return (1 if _AUTHORITY_POS.search(s) else 0) - (1 if _AUTHORITY_NEG.search(s) else 0)


def _conflicts(items, mtimes: dict):
    """Group (key, value, source, line) by key. Within a key, group values by a
    *normalized* key so equivalent amounts don't false-conflict (DDX-032); a key
    with two or more distinct values is a conflict. Each distinct value is shown
    via its genuinely newest source — not the first one seen (DDX-031) — newest
    value first."""
    groups: "OrderedDict[object, list]" = OrderedDict()
    for key, value, source, line in items:
        if not key or not value:
            continue
        groups.setdefault(key, []).append((value, source, line))
    out = []
    for key, members in groups.items():
        by_norm: "OrderedDict[object, list]" = OrderedDict()
        for value, source, line in members:
            by_norm.setdefault(_value_key(value), []).append((value, source, line))
        if len(by_norm) >= 2:
            reps = [max(group, key=lambda vsl: (mtimes.get(vsl[1], ""), _authority(vsl[1]), vsl[1]))
                    for group in by_norm.values()]
            reps.sort(key=lambda vsl: (mtimes.get(vsl[1], ""), _authority(vsl[1]), vsl[1]),
                      reverse=True)
            out.append((key, reps))
    return out


def build_packet(project: Project, task: str, budget: int = 3000,
                 folder: Optional[str] = None, form_fields: Optional[List[str]] = None,
                 explain: bool = False, check_freshness: bool = False,
                 exclude: Optional[set] = None) -> str:
    if not tokenize(task):
        raise EmptyTask(f"task has no searchable terms: {task!r}")

    requested = budget
    budget_eff = max(0, budget)            # a non-positive budget retrieves nothing
    # In form mode the field labels define what we're looking for; otherwise the
    # task text does. (Avoids the synthesized "fill the form: x.md" leaking in.)
    if form_fields:
        terms = {t for label in form_fields for t in _content_terms(label)}
    else:
        terms = set(_content_terms(task))
    alias_groups = al.load_aliases(project)
    # Provenance (the ~approx synonym tag) is scoped to FREE-TEXT search only. In
    # form mode the field-label synonym path is deferred, so alias_extra stays
    # empty and the two _approx_match call sites below become no-ops (intended).
    alias_extra = ((al.expand_stems(task, alias_groups) - stemmed(task))
                   if (alias_groups and not form_fields) else set())
    inv_rows, state_err = _read_inv(project)
    mtimes = {rel: row.get("mtime_iso", "") for rel, row in inv_rows.items()}
    # Hide the form file and (only) *unchanged* scaffolds; an edited CLAUDE.md is
    # real evidence and must surface (DDX-036).
    skip = (exclude or set()) | _scaffold_excludes(
        project, {rel: row.get("sha1", "") for rel, row in inv_rows.items()})
    pool = _candidates(project, task, folder, pool=40, exclude=skip,
                       alias_groups=alias_groups)

    # ---- Resolve each form field (retrieval only; budget applied when packing) ----
    resolved: List[dict] = []          # {label, has_value, line, hit|None}
    pinned = set()
    conflict_items: list = []          # (key, value, source, line)
    if form_fields:
        label_tokens = {lbl: set(tokenize(lbl)) for lbl in form_fields}
        all_label_tokens = set().union(*label_tokens.values()) if label_tokens else set()
        for label in form_fields:
            label_terms = label_tokens[label]
            foreign = all_label_tokens - label_terms   # other fields' label tokens
            fhits = _candidates(project, label, folder, pool=6, exclude=skip,
                                alias_groups=alias_groups)
            best = _pick_field_hit(fhits, label, label_terms)
            if not best:
                resolved.append({"label": label, "has_value": False,
                                 "line": None, "hit": None})
                continue
            # Extract this field's value label-locally, preferring the candidate
            # that yields a *clean* (single-field) value over a broad/dense line.
            ans, ans_hit = None, best
            for h in [best] + [x for x in fhits if x is not best]:
                cand = _field_answer(h["text"], label, label_terms, foreign,
                                     alias_groups)
                if cand:
                    # Precedence must hold ACROSS candidates, not just within one:
                    # comparing only against "exact" let an alias match in a better
                    # chunk outrank a stem match in a worse one, inverting the
                    # documented exact → stem → alias order.
                    if ans is None or _HOW_RANK[cand[3]] < _HOW_RANK[ans[3]]:
                        ans, ans_hit = cand, h
                    if cand[2] and cand[3] == "exact":
                        break
            if ans is None:
                # matched the label but no recognisable value — show the label-local
                # text, not a broad snippet that could include a neighbour's value.
                # The window is tiered too, so a field labelled by a synonym still
                # shows ITS OWN value region rather than the whole sentence. Values
                # docdex cannot type-recognise (a company name has no digits) land
                # here by design: shown for the agent to read, never asserted.
                w, how = None, None
                for h in [best] + [x for x in fhits if x is not best]:
                    w, how = label_window(h["text"], label, label_terms, alias_groups)
                    if w:
                        best = h
                        break
                line = _trim(w, 160) if w else snippet(
                    best["text"], label, sorted(label_terms), width=160)
                resolved.append({"label": label, "has_value": False, "line": line,
                                 "hit": best, "approx": bool(how) and how != "exact"})
            else:
                _value, display, clean, how = ans
                resolved.append({"label": label, "has_value": clean, "line": display,
                                 "hit": ans_hit, "approx": how != "exact"})
            pool.append(ans_hit)
            pinned.add((ans_hit["rel"], ans_hit["chunk"]))
            for h in fhits:                # conflicting values for THIS field only
                for clause, val in _field_values(h["text"], label, label_terms,
                                                 alias_groups, foreign):
                    conflict_items.append((label, val, h["rel"], clause))

    missing_fields = [r["label"] for r in resolved if r["hit"] is None]

    # ---- Pack under budget: field answers first (the deliverable), then evidence ----
    used = HEADER_RESERVE
    packed_found: List[dict] = []
    packed_weak: List[dict] = []
    dropped_fields: List[dict] = []
    if budget_eff:
        for r in resolved:
            if r["hit"] is None:
                continue
            cost = tok.count_tokens(f"{r['label']}: {r['line']}") + 6
            first_real = not packed_found and not packed_weak and r["has_value"]
            if used + cost <= budget_eff or first_real:
                (packed_found if r["has_value"] else packed_weak).append(r)
                used += cost
            else:
                dropped_fields.append(r)

    top_score = max((c["score"] for c in pool), default=0.0)
    # A relative floor only when scores are meaningful; when every hit scores ~0
    # (a term in every doc), don't let the floor suppress real evidence (DDX-030).
    rel_floor = 0.15 * top_score if top_score > MIN_EVIDENCE_SCORE else 0.0
    seen = set()
    per_source: dict = {}
    evidence = []
    evidence_truncated = False
    if budget_eff:
        for cand in sorted(pool, key=lambda c: _utility(c, terms)):
            key = (cand["rel"], cand["chunk"])
            if key in seen:
                continue
            if cand["score"] < rel_floor and key not in pinned:
                continue
            if per_source.get(cand["rel"], 0) >= MAX_PER_SOURCE:
                continue
            excerpt = snippet(cand["text"], task, sorted(terms), width=EXCERPT_CHARS)
            cost = tok.count_tokens(excerpt) + 12
            if used + cost > budget_eff and evidence:
                evidence_truncated = True
                break
            seen.add(key)
            per_source[cand["rel"]] = per_source.get(cand["rel"], 0) + 1
            approx = _approx_match(cand["text"], terms, alias_extra)
            evidence.append((cand["rel"], cand["chunk"], excerpt,
                             cand["score"], approx))
            used += cost

    # ---- Free-text answers (value lines) + their conflict candidates ----
    answers = []
    if not form_fields:
        for rel, chunk, excerpt, _score, _approx in evidence:
            for line in _value_lines(excerpt, terms):
                answers.append((line, f"{rel} ·{chunk}", _approx_match(line, terms, alias_extra)))
                key = tuple(sorted(t for t in terms if t in line.lower()))
                conflict_items.append((key, _value_near(line, terms), rel, line))
        answers = answers[:8]
    conflicts = _conflicts(conflict_items, mtimes)

    # Stem-aware, matching keep()'s notion of a hit: a term that only matched via
    # its stem (governing↔governed) surfaced as ~approx evidence, so it must not
    # also be reported as missing — that would contradict the evidence just shown.
    pool_stems = set().union(*(stemmed(c["text"]) for c in pool)) if pool else set()
    if alias_groups:
        pool_stems |= al.expand_stems(" ".join(c["text"] for c in pool), alias_groups)
    missing_terms = [t for t in terms if stem(t) not in pool_stems]

    # A positive budget that the packed content already blew past must be flagged,
    # in free-text mode too — not just silently over (DDX-033).
    over_budget = budget_eff > 0 and used > budget_eff

    # ---- Coverage line ----
    if form_fields:
        cov = [f"{len(form_fields)} fields", f"{len(packed_found)} found",
               f"{len(packed_weak)} weak", f"{len(missing_fields)} missing"]
        if dropped_fields:
            cov.append(f"{len(dropped_fields)} dropped(budget)")
        coverage = " · ".join(cov)
    else:
        cov = [f"{len(answers)} value answer(s)"]
        if missing_terms:
            cov.append(f"{len(missing_terms)} term(s) unmatched")
        if evidence_truncated:
            cov.append("evidence truncated by budget")
        coverage = " · ".join(cov)

    note = "" if tok.using_real_tokenizer() else " (≈ chars/4)"
    budget_warn = "  ⚠ over budget" if over_budget else ""
    out = [
        "# context packet",
        f"Task: {task.strip()}",
        f"Coverage: {coverage}",
        # provisional — rewritten below against the real rendered token count.
        f"Budget: {requested} requested · ~{used} used{note} · "
        f"{max(0, requested - used)} free{budget_warn}",
        (f"Index: unreadable — {state_err}; run `docdex sync` to rebuild"
         if state_err else f"Index: {_freshness(project, check_freshness)}"),
        "",
    ]
    budget_line_idx = 3
    if state_err:
        out += ["⚠ index state is unreadable, so dates and freshness are "
                "unavailable and evidence may be incomplete — run `docdex sync` "
                "to rebuild.", ""]
    if budget_eff <= 0:
        out += ["⚠ Budget is not positive — nothing was retrieved. "
                "Rerun with e.g. --budget 2000.", ""]

    answer_block = []
    if form_fields:
        for r in packed_found:
            atag = "  ~approx" if r.get("approx") else ""
            answer_block.append(
                f"- {r['label']}: {r['line']}  "
                f"[{r['hit']['rel']} ·{r['hit']['chunk']}]{atag}")
    else:
        for line, source, approx in answers:
            atag = "  ~approx" if approx else ""
            answer_block.append(f"- {line}  [{source}]{atag}")
    if answer_block:
        out += ["## Answers", *answer_block, ""]

    if packed_weak:
        out.append("## Needs follow-up (weak)")
        for r in packed_weak:
            atag = "  ~approx" if r.get("approx") else ""
            out.append(f"- {r['label']}: matched, no clear value — {r['line']}  "
                       f"[{r['hit']['rel']} ·{r['hit']['chunk']}]{atag}")
        out.append("")

    if conflicts:
        out.append("## Conflicts")
        out.append("> newer / more-authoritative is not necessarily correct — verify.")
        for key, reps in conflicts:
            label = key if isinstance(key, str) else (", ".join(key) or "value")
            out.append(f"- {label}: {len(reps)} values disagree")
            for i, (val, src, _line) in enumerate(reps):
                mt = mtimes.get(src, "")
                date = f" ({mt[:10]})" if mt else ""
                auth = "  · authoritative" if _authority(src) > 0 else (
                    "  · draft" if _authority(src) < 0 else "")
                is_newest = (i == 0 and mt and
                             (len(reps) < 2 or mt > mtimes.get(reps[1][1], "")))
                tag = "  ← newest" if is_newest else ""
                out.append(f"    - {val} — {src}{date}{auth}{tag}")
        out.append("")

    if missing_fields or missing_terms:
        out.append("## Missing")
        for fld in missing_fields:
            tried = ", ".join(sorted(set(tokenize(fld))))
            out.append(f"- {fld}: not found" + (f" (tried: {tried})" if tried else ""))
        if missing_terms:
            out.append(f"- no index hits for: {', '.join(sorted(missing_terms))}")
        out.append("")

    if dropped_fields or evidence_truncated or budget_eff <= 0 or over_budget:
        bigger = max(2000, requested * 2) if requested > 0 else 2000
        out.append("## Dropped (budget)")
        if budget_eff <= 0:
            out.append(f"- everything (budget was {requested}) — rerun with --budget {bigger}")
        else:
            for r in dropped_fields:
                if r.get("has_value"):
                    out.append(f"- {r['label']}: answer found but cut to fit the budget")
                else:
                    out.append(f"- {r['label']}: label matched but dropped before a value was confirmed")
            if evidence_truncated:
                out.append("- some supporting evidence was not packed")
            if over_budget:
                out.append(f"- the packet is larger than the {requested}-token "
                           "budget (kept the minimum to stay useful)")
            out.append(f"- rerun with --budget {bigger} to fit it")
        out.append("")

    out.append("## Evidence")
    if any(e[4] for e in evidence):
        if alias_groups:
            out.append("> `~approx` = matched by word stem or a declared synonym, "
                       "not the literal term — confirm before relying on it.")
        else:
            out.append("> `~approx` = matched by word stem, not the literal term — "
                       "confirm the literal word before relying on it.")
    if evidence:
        for i, (rel, chunk, excerpt, score, approx) in enumerate(evidence, 1):
            mt = mtimes.get(rel, "")
            mtag = f"  ({mt[:10]})" if mt else ""
            atag = "  ~approx" if approx else ""
            out.append(f"[E{i}] {rel} ·{chunk}{mtag}  (score {score}){atag}")
            out.append(f'  "{excerpt}"')
    else:
        reason = " (budget too small)" if budget_eff <= 0 or evidence_truncated else ""
        out.append(f"- no evidence packed{reason}")
    out.append("")

    gap = (missing_fields[0] if missing_fields else
           dropped_fields[0]["label"] if dropped_fields else
           " ".join(sorted(missing_terms)) if missing_terms else "")
    if gap:
        out += ["## Suggested next call",
                f'- docdex context "{gap}" --budget 1500'
                + (f" --folder {folder}" if folder else ""), ""]

    if explain:
        out.append("## Explain")
        out.append(f"- query terms: {', '.join(sorted(terms)) or '(none)'}")
        stem_groups: "OrderedDict[str, list]" = OrderedDict()
        for t in sorted(terms):
            stem_groups.setdefault(stem(t), []).append(t)
        out.append("- stems: " + ("; ".join(
            f"{s} ← {', '.join(ts)}" for s, ts in stem_groups.items()) or "(none)"))
        if alias_groups:
            # Reflect the groups the query actually TRIGGERED (full phrase present
            # by stem), consistent with retrieval — not any-token membership.
            shown = []
            for g in alias_groups:
                present = [p for p in g if al._phrase_present(p, [stem(t) for t in tokenize(task)])]
                if present:
                    shown.append(" / ".join(g))
            out.append("- aliases: " + ("; ".join(shown) if shown else "(none matched)"))
        out.append(f"- candidate chunks retrieved: {len(pool)}")
        out.append(f"- evidence packed: {len(evidence)} (≤{MAX_PER_SOURCE}/source); "
                   f"fields {len(packed_found)} found / {len(packed_weak)} weak / "
                   f"{len(missing_fields)} missing / {len(dropped_fields)} dropped")
        engine = "FTS5/BM25" if index_db.available(project) else "cache scorer (no FTS5)"
        out.append(f"- engine: {engine}")
        out.append(f"- tokenizer: {'tiktoken' if tok.using_real_tokenizer() else 'chars/4 estimate'}")
        out.append("- ranking: utility (value-bearing · coverage · bm25)")

    # Token-exact accounting: report the budget against the *rendered* packet,
    # not a component-sum estimate that undercounts what the agent receives.
    rendered_used = tok.count_tokens("\n".join(out))
    final_warn = "  ⚠ over budget" if requested > 0 and rendered_used > requested else ""
    out[budget_line_idx] = (
        f"Budget: {requested} requested · ~{rendered_used} used{note} · "
        f"{max(0, requested - rendered_used)} free{final_warn}")
    return "\n".join(out).rstrip() + "\n"


def parse_form_fields(text: str, limit: int = 200) -> List[str]:
    """Pull likely field labels from a form's text: 'Label:' or 'Label ____'.

    Unicode-aware (so 'Échéance' / 'Numéro fiscal' parse), and the cap is high
    enough that real forms are not silently truncated (DDX-020)."""
    fields: List[str] = []
    counts: dict = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"^[-*\d.)\s]*([^\W\d_][\w /&'()\-]{1,60}?)\s*[:_]", line, re.UNICODE)
        if m:
            label = m.group(1).strip()
            if label:
                key = label.lower()
                counts[key] = counts.get(key, 0) + 1
                # Keep repeats so coverage matches the visible form, but
                # disambiguate them into distinct answer lines (DDX-038).
                fields.append(label if counts[key] == 1 else f"{label} #{counts[key]}")
        if len(fields) >= limit:
            break
    return fields
