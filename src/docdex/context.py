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
#
# Case sensitivity is per branch, NOT global. A global `re.I` used to be applied to
# the whole pattern, which quietly defeated the ID-ish branch's own `[A-Z0-9]`
# character class: `covid19`, `windows10` and `section2b` were all read as
# identifiers. Units and month names still need to match in any case, so they say so
# themselves with a scoped `(?i:...)`.
# Alternative ORDER decides what gets extracted, because the first alternative that
# matches at a position wins. Longest-and-most-specific first, therefore:
#
#   - Emails before everything: `user123@x.com` used to yield the bare `123`.
#   - Identifiers before bare numbers: `29ABCDE1234F1Z5` — a GSTIN — used to yield
#     `29`, so a conflict entry displayed "29" as the value. Exactly the bug v0.5.0
#     fixed for dates (`31/12/2026` extracting as `31`), left standing one branch
#     lower. Found while making this test assert the whole match rather than
#     truthiness.
#
# The unit is inside its own optional group, `(?:\s*UNIT)?`, not `\s*UNIT?`. Written
# the second way the `\s*` applied whether or not a unit followed, so every value at
# the end of a phrase carried a trailing space into the extracted string.
#
# Units are one source of truth: the pattern below absorbs them, and `_STOP` refuses
# to read any of them as the first word of a FOLLOWING field's label. Without that
# refusal, "Renewal term: 24 months  Vendor: Acme Ltd" cut the value window at "24" —
# the lookahead for the next `Label:` allows spaces, so it matched "months Vendor:" as
# though "months Vendor" were the label — and the field was answered `24`. 24 what?
# Durations were missing from the units too, so even an uncut window yielded `24`.
# Found while writing this release's tests; a unit is not decoration, it is half the
# value.
_UNIT_WORDS = (
    "percent", "crore", "lakh", "cr", "mn", "million", "billion", "k",
    "business", "calendar", "working",
    "days", "day", "weeks", "week", "months", "month", "years", "year",
    "hours", "hour", "minutes", "minute",
)
_DURATION = r"(?:days?|weeks?|months?|years?|hours?|minutes?)\b"
_UNIT = (r"(?i:%|percent|crore|lakh|cr\b|mn\b|million|billion|k\b"
         rf"|(?:business|calendar|working)\s+{_DURATION}|{_DURATION})")
_MONTH = r"(?i:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-zA-Z]*"
VALUE_RE = re.compile(
    r"([\w.+-]+@[\w-]+\.[\w.-]+)"                      # emails
    rf"|(-?\s?[₹$€£]\s?\d[\d,]*\.?\d*(?:\s*{_UNIT})?)"
    r"|(\b\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}\b)"
    r"|(\b\d{4}[/\-]\d{1,2}[/\-]\d{1,2}\b)"
    rf"|(\b\d{{1,2}}\s+{_MONTH}\s+\d{{2,4}}\b)"
    rf"|(\b{_MONTH}\s+\d+)"
    r"|([A-Z0-9]{6,}\d|[0-9]{2}[A-Z]{4,})"            # ID-ish tokens (case-SENSITIVE)
    rf"|(-?\d[\d,]*\.?\d*(?:\s*{_UNIT})?)",
)

# Words that introduce a NUMBER used to locate something in a document rather than
# to state a fact about the world. "Clause 4", "section 12" and "page 3" answer no
# question a form field could ask.
#
# `has_value` exists to break ranking ties toward a chunk that can actually answer.
# Measured on the real 10.5k-file corpus it was true for 96.6% of chunks, and on a
# 4,000-chunk sample it agreed with "does this text contain a digit" 4,000 times out
# of 4,000 — so it barely discriminated at all. Excluding structural numbering is a
# narrow, explainable correction: it removes exactly the matches that point at a
# place in a document, and leaves every match that states an amount, date, rate,
# identifier or address.
# Deliberately narrow: only words that unambiguously introduce a document location.
# "version", "part" and a bare "a"/"q" were considered and left out — "version 2" can
# be the answer to a real question, and "a" precedes far too much to be a safe signal.
#
# `no`, `nos`, `sr` and `serial` were in this list and had to come OUT: in
# "Invoice No. 42", "PO No. 7781" and "Serial No. 90210" the number IS the answer, and
# suppressing it costs the value-bearing tie-break exactly where a form field wants
# it. Found by adversarial review. Treating a stray "No. 5" cross-reference as a value
# is much the cheaper mistake — it spends a tie-break, it does not hide evidence.
_STRUCTURAL_REF = re.compile(
    r"(?i:\b(?:clause|section|sec|article|art|para|paragraph|page|pg|item|figure|fig"
    r"|table|annex|annexure|appendix|exhibit|schedule|chapter|chap|note|step|point"
    r"|rule)\.?\s*)$")

# A match that is nothing but a number: no currency symbol, no unit or scale word, no
# date delimiter, no letters, no `@`. Only these are ambiguous enough to need their
# introducing word checked — "5 crore", "12%", "₹4.2", "31/12/2026" and
# "PO4400182" state a value wherever they appear.
#
# Trailing sentence punctuation is part of the match — `\.?\d*` absorbs the full stop
# in "page 3." — so it is stripped before this test. Without that, every number ending
# a sentence failed the plain-number test, counted as a non-plain value, and skipped
# the structural check altogether: the suppression silently did not apply wherever a
# sentence ended. Found while adjudicating a neighbouring review finding.
_PLAIN_NUMBER = re.compile(r"^-?\s?\d[\d,]*(?:\.\d+)?$")


def _plain_number(token: str) -> bool:
    return bool(_PLAIN_NUMBER.match(token.strip().rstrip(".,;:")))


def first_real_value(text: str):
    """The first `VALUE_RE` match that states a fact rather than pointing at a place.

    ONE rule, used by every caller that asks "is there a value here" — the findability
    signal, the field answer, the conflict list and the evidence lines. It used to be
    the signal's rule alone, and the answer path had none, so "Liability cap: refer to
    section 9 for details" was presented under `## Answers` with `9` for a value, and
    "Legal name is described in annexure 4 at clause 4.2" was answered with `4`. A
    cross-reference tells the reader where to look; it is not what was asked for.

    Found by adversarial review, which arrived at it from the opposite direction (it
    argued the ranking would pick the wrong chunk) — and it is the third time one
    question has turned out to have more than one rule answering it. v0.5.6 found three
    for aliases; this release also found `_pick_field_hit` scanning for numbers while
    the reading beside it looked for names.
    """
    for m in VALUE_RE.finditer(text):
        if not _plain_number(m.group(0)):
            return m             # currency, unit, date, month, identifier or email
        if not _STRUCTURAL_REF.search(text[max(0, m.start() - 24):m.start()]):
            return m
    return None


def carries_value(text: str) -> bool:
    """Does `text` state a value, as opposed to merely containing a number?

    True when at least one `VALUE_RE` match is not a structural reference — a bare
    number introduced by a word like "clause", "section" or "page". A chunk whose
    only numbers locate parts of a document answers nothing, so flagging it as
    value-bearing spends the ranking tie-break on a chunk that cannot help.
    """
    if first_real_value(text) is not None:
        return True
    # A party defined by apposition — "Helios Components Pvt Ltd as the Vendor" — is
    # an answer too, and this is what makes it FINDABLE. Measured on the benchmark
    # corpus: without this the chunk carrying that line was not in a pool of 60 for
    # the `Legal name` field, because every candidate ties at BM25 0 (the label's
    # words are ubiquitous in contract prose) and the has_value tie-break then sorted
    # every chunk containing any digit above the one chunk that could answer. Exactly
    # the shape of the v0.5.1 bug, one signal lower down. With it, the chunk enters
    # the pool at position 4 of 6.
    # A company presented as a field's value — "Vendor: Acme Industries Ltd" — is the
    # same kind of answer written the other way round, and needs the same help to be
    # reachable.
    return carries_apposition(text) or carries_labelled_name(text)
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
        extra: List[str] = []
        for group in al.triggered_groups(query, alias_groups):
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
        # The SAME rule that widened `search_query` above. When these differed, a
        # document the widening had reached could be dropped here as "missing".
        content_stems |= al.query_stems(query, alias_groups)

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
_STOP = re.compile(
    r"(?P<hard>[;\n])"
    r"|(?P<sent>(?<=[.!?])\s)"
    r"|(?P<label>(?<=\S)\s+[^\W\d_][\w '&/()\-]{0,38}?[:_]\s)",
    re.UNICODE)

# A unit word only belongs to the value when a NUMBER comes before it — see
# `_unit_of_the_value`. The label lookahead above allows spaces, so in
# "Renewal term: 24 months Vendor: Acme" it read "months Vendor" as the next label and
# cut the value down to a bare `24`. Twenty-four what?
#
# Refusing unit-initial labels outright was the first attempt and broke the other
# direction, which adversarial review caught immediately: `Working days:` and
# `Business days:` are real field labels whose first word is a unit, and they stopped
# being boundaries at all, so the previous field swallowed them whole.
_UNIT_HEAD = re.compile(r"(?i:%s)\b" % "|".join(sorted(_UNIT_WORDS, key=len,
                                                       reverse=True)))
_ENDS_IN_A_NUMBER = re.compile(r"\d[\d,.]*\s*$")


def _unit_of_the_value(before: str, after: str) -> bool:
    """Does the word starting `after` continue a number that ends `before`?"""
    return bool(_UNIT_HEAD.match(after) and _ENDS_IN_A_NUMBER.search(before))

_CLAUSE_SPLIT = re.compile(r"(?P<hard>[;\n]+)|(?P<sent>(?<=[.!?])\s+)")

# Abbreviations that live INSIDE a company name, so the full stop after one of them
# does not end a clause: "Helios Components Pvt. Ltd. as the Vendor" is one clause and
# one name. Every earlier release split it into three, which is why v0.5.7 shipped a
# name written this way as a tracked gap — apposition looked backwards from `as` and
# found only "Ltd.", while the forward window stopped at "Pvt.".
#
# Deliberately only NAME abbreviations. A general "a dot before a lowercase word is not
# a boundary" rule would merge most of a document into one clause, and the clause is
# the unit that keeps one field's value away from another's.
_NAME_ABBREV = {
    "pvt", "ltd", "inc", "corp", "co", "llp", "llc", "plc", "gmbh", "pte", "sdn",
    "bhd", "pty", "spa", "aps", "oy", "oyj", "ab", "asa", "bv", "nv", "srl", "sa",
    "ag", "kg", "kk", "mfg", "bros", "intl",
}
_WORD_BEFORE_DOT = re.compile(r"([^\W\d_][\w'’&\-]*)\.$")
_WORD_AT = re.compile(r"[^\W\d_][\w'’&\-]*\.?")


def _abbreviation_join(text: str, at: int, resume: int,
                       inside_a_name_only: bool = False) -> bool:
    """Is the full stop just before `at` part of a name, rather than a clause end?

    True when the word before it is a name abbreviation AND what follows continues the
    phrase: a lowercase word ("Ltd. as the Vendor") or another name abbreviation
    ("Pvt. Ltd."). A capitalised ordinary word means a new sentence really did start,
    so "Zeta Corporation Ltd. Acme Industries Ltd" stays two clauses and two companies.

    In text that is entirely upper-case neither test can fire — "is this word
    capitalised" carries no information there, as v0.5.7's real-corpus pass established
    — so the boundary stays where it is and the name goes unread. A miss, not a wrong
    answer.

    `inside_a_name_only` is the stricter question a value WINDOW has to ask, and the
    real corpus is what forced the distinction. Merging "…Pvt. Ltd." with the lowercase
    word after it is right for a clause — the sentence really does continue — but it
    also lets a value window run further, and over 104,168 real chunks that changed 20
    field answers, every one of them into a wrong answer. From a signed NDA:

        "( Effective Date ) by and between Helios Components Pvt. Ltd. with offices at
         Meridian House, 2nd Floor, …"          ->  Effective date: 2

    So a window may cross a full stop only when what follows continues the NAME
    (`Pvt.` → `Ltd.`), never merely because the sentence does.
    """
    m = _WORD_BEFORE_DOT.search(text[:at])
    if not m or m.group(1).lower() not in _NAME_ABBREV:
        return False
    nxt = _WORD_AT.match(text, resume)
    if not nxt:
        return False
    word = nxt.group(0)
    low = word.lower().rstrip(".")
    if inside_a_name_only:
        return bool(low in _NAME_ABBREV
                    or (word[:1].isupper() and low in _NAME_PART))
    return bool(word[:1].islower() or low in _NAME_ABBREV)


def _clauses(text: str) -> List[str]:
    """Split text into clauses on ';', newlines, and sentence ends.

    A sentence end that is really an abbreviation inside a name is not a boundary —
    see `_abbreviation_join`.
    """
    out: List[str] = []
    start = 0
    for m in _CLAUSE_SPLIT.finditer(text):
        if m.lastgroup == "sent" and _abbreviation_join(text, m.start(), m.end()):
            continue
        piece = text[start:m.start()].strip()
        if piece:
            out.append(piece)
        start = m.end()
    tail = text[start:].strip()
    if tail:
        out.append(tail)
    return out


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
    """The value region that follows a label, cut before the next label/delimiter.

    Skips a sentence end that is an abbreviation inside a name, for the same reason
    `_clauses` does — otherwise "Legal name: Helios Components Pvt. Ltd." yields a
    window of "Helios Components Pvt", which names no company that exists.
    """
    after = text[end:]
    window = after
    # `search(pos)` rather than `finditer`, because a rejected match must not consume
    # the text inside it: the label alternative allows spaces, so the match rejected in
    # "24 months Vendor:" spans the real boundary before "Vendor" as well, and scanning
    # on from its END lost that boundary and returned the next field's value too.
    pos = 0
    while True:
        m = _STOP.search(after, pos)
        if m is None:
            break
        if (m.lastgroup == "sent"
                and _abbreviation_join(after, m.start(), m.end(),
                                       inside_a_name_only=True)) or (
                m.lastgroup == "label"
                and _unit_of_the_value(after[:m.start()],
                                       after[m.start():].lstrip())):
            pos = m.start() + 1
            continue
        window = after[:m.start()]
        break
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
    span = _terms_span(spans, label_terms, stemwise)
    return None if span is None else span[1]


def _terms_span(spans: List[tuple], label_terms: set, stemwise: bool):
    """(start, end) of the first complete occurrence of a label, or None.

    The start is where the label PHRASE begins — needed by the apposition fallback,
    which has to see what introduces the label rather than what follows it.

    "Begins" means the start of the SMALLEST run holding every label token and ending
    here, not the first token anywhere that happened to match one of them. Locking it
    to the latter pointed the apposition lookback at the start of the sentence
    whenever a label word appeared earlier in the clause — "The legal team reviewed it
    and Acme Corp as the legal name applies" found no connective there and reported a
    present answer as missing. Found by adversarial review.
    """
    want = {stem(t) for t in label_terms} if stemwise else set(label_terms)
    if not want:
        return None
    last_at: dict = {}
    for tok, start, end in spans:
        key = stem(tok) if stemwise else tok
        if key in want:
            last_at[key] = start
            if len(last_at) >= len(want):
                return (min(last_at.values()), end)
    return None


def _alias_end(spans: List[tuple], label: str, alias_groups) -> Optional[int]:
    """End offset after a declared SYNONYM of this field's label.

    Phrase-level by necessity: `legal name` maps to `vendor`, so there is no
    term-by-term correspondence. Only groups that actually contain this field's own
    label are considered, and a synonym must appear as a contiguous stemmed run —
    never scattered words that merely co-occur.
    """
    span = _alias_span(spans, label, alias_groups)
    return None if span is None else span[1]


def _alias_span(spans: List[tuple], label: str, alias_groups):
    """(start, end) of the earliest declared synonym of this field's label."""
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
                    here = (spans[i][1], spans[i + n - 1][2])
                    # FIRST occurrence in reading order. Two synonyms of one field in
                    # one clause is inherently ambiguous ("Vendor: Acme, Supplier:
                    # Beta"); taking the earliest is what a reader would do, and it
                    # matches the exact path's first-complete rule.
                    if best is None or here[1] < best[1]:
                        best = here
    return best


# Label-match precedence, defined once and used both inside _field_answer and for
# the cross-candidate comparison in build_packet.
# `appos` is last: reading a value backwards from sentence structure is the most
# approximate route there is, so it may only win when nothing else produced anything.
_HOW_RANK = {"exact": 0, "stem": 1, "alias": 2, "appos": 3}


def label_window(text: str, label: str, label_terms: set,
                 alias_groups=None) -> tuple:
    """(value region after this field's label, how it was matched).

    Strict precedence — **exact, then word-ending, then declared synonym** — so a
    literal label always decides when one is present and a synonym can never hijack
    a field that already matched properly. `how` is one of "exact" / "stem" /
    "alias"; anything but "exact" is approximate and gets tagged for the agent.

    Returns (None, None) when the label isn't present at all.
    """
    end, how = _label_end(text, label, label_terms, alias_groups)
    if end is None:
        return None, None
    return _cut_after(text, end), how


def _label_end(text: str, label: str, label_terms: set, alias_groups=None) -> tuple:
    """(offset just past this field's label, how it was matched), or (None, None).

    Split out of `label_window` because reading a NAME after a label needs to know
    whether a separator follows it, which the cut window has already thrown away.
    """
    spans = _token_spans(text)
    for stemwise, how in ((False, "exact"), (True, "stem")):
        end = _terms_end(spans, label_terms, stemwise)
        if end is not None:
            return end, how
    end = _alias_end(spans, label, alias_groups)
    if end is not None:
        return end, "alias"
    return None, None


def _label_window(text: str, label_terms: set) -> Optional[str]:
    """Exact-only window — kept for callers that must not widen (see label_window)."""
    return label_window(text, " ".join(sorted(label_terms)), label_terms, None)[0]


# --------------------------------------------------------------- apposition (v0.5.7)
#
# Contracts name a party by stating it and THEN saying what role it plays:
#
#     Master agreement with Helios Components Pvt Ltd **as the Vendor**.
#     Acme Corporation (**the "Supplier"**) shall deliver.
#
# Every release before this read a field's value from the text after its label, so
# the window after the label here is empty and the field reported "matched, no clear
# value" — the form benchmark's last miss.
#
# Reading backwards is the dangerous direction, and it waited for its own release
# because of it. Unbounded lookback is the DDX-029 cross-field leakage class that
# v0.4.0 fixed: from "Payment terms are net-45. Vendor: Acme" a backwards reader
# hands `net-45` to `Legal name`. A confidently wrong answer costs far more than the
# missing one it replaces, because the agent cannot tell.
#
# What makes it safe is not a list of exceptions but the shape of what may be read: a
# run of PROPER NOUNS immediately before a REQUIRED apposition connective. Values are
# not capitalised proper nouns, so `net-45`, `24 months` and `INR 6.5 crore` cannot be
# read this way at all — the whole leakage class is closed by construction.

# The connective must mean "the phrase before me IS the role that follows". A bare
# comma or a bare "the" does not: "Payment terms are net-45, the Vendor is Acme"
# would leak. A bare "(" does not either: "liability cap is INR 6.5 crore (Vendor:
# Acme)" would leak. A parenthesis only counts when it opens a DEFINED TERM.
_APPOS_CORE = (
    r"(?:"
    r"\b(?:acting\s+|solely\s+|collectively\s+)?as\b"
    r"|\breferred\s+to\s+as\b"
    r"|\bhereinafter(?:\s+(?:called|known\s+as|referred\s+to\s+as))?\b"
    r"|\(\s*(?:the\s+)?[\"'“‘]"
    r"|\(\s*the\b"
    r")"
)
_APPOS_TAIL = r"[\s:,]*(?:the|an?|its|our|their)?[\s\"'“”‘’(]*"
# Anchored: what sits immediately before a label the caller has already located.
_APPOS_CONNECTIVE = re.compile(_APPOS_CORE + _APPOS_TAIL + r"$", re.IGNORECASE)
# Unanchored, for the corpus-wide findability signal — the same connective followed by
# some word playing the role.
_APPOS_SCAN = re.compile(_APPOS_CORE + _APPOS_TAIL + r"[^\W\d_]", re.IGNORECASE)

# Lowercase words that are legitimately PART of a company name, so a name written
# "Helios Components pvt ltd" is not cut off at its own suffix. The run must still
# contain a capitalised token that is not one of these, so "Company" alone is not a
# name — which is also why "3M Company" is missed: `3M` is not capitalised by this
# test. A missed name shows the field as unanswered, which is safe; a wrong one is not.
_NAME_PART = {
    "pvt", "private", "ltd", "limited", "llp", "llc", "inc", "incorporated",
    "co", "corp", "corporation", "company", "companies", "plc", "gmbh", "ag",
    "sa", "srl", "bv", "nv", "oy", "ab", "pte", "sdn", "bhd", "holdings",
    "partners", "partnership", "group", "trust", "foundation", "and", "of",
}
APPOS_MAX_TOKENS = 8       # a name, not a paragraph: recitals run long

# What may sit between two words of one name. Abbreviation dots ("Pvt. Ltd."),
# ampersands ("Smith & Sons"), apostrophes and slashes belong to names; a comma,
# colon, semicolon, bracket or quote ends one. Sentence-ending dots never reach here
# because `_clauses` has already split on them.
# A dot joins an abbreviation ("Pvt. Ltd") only when whitespace follows it. Extracted
# PDF text routinely loses the space after a full stop, and without that condition
# "Zeta Corporation.Acme Industries Ltd" came back as one company. Found by
# adversarial review.
_NAME_JOIN = re.compile(r"\s*|\.\s+|\s*[&'’/\-]\s*")

# `&` and `/` belong INSIDE a name ("Smith & Sons Ltd", "B S R & Co. LLP") but they
# join two DIFFERENT companies once a legal form has already been stated:
# "Beta Holdings Ltd / Gamma Systems LLP" is two parties, and both readers returned it
# as one company that does not exist. Found by adversarial review.
#
# Two entities where the form asked for one is a disagreement, not a value, so neither
# reader picks one: the name is refused and the window still shows under
# `## Needs follow-up`. The cost is that "GmbH & Co. KG" is unreadable — a miss, which
# is the direction this whole feature errs in.
_NAME_CONJUNCTION = re.compile(r"\s*[&/]\s*")

# ------------------------------------------ what kind of value does a field want? ---
#
# v0.5.7 decided which fields could be answered with a company by a DENY-list of about
# forty quantity words. Every other label was allowed one, so `Aggregate liability` —
# whose words appear in no list — was answered "Helios Components Pvt Ltd", and so was
# a label docdex had never seen. A deny-list has to enumerate everything that can go
# wrong, and the cost of one missing entry is a confidently wrong answer.
#
# So it is asked as a type question instead, and answered from an ALLOW-list: only a
# field known to want a PARTY may be answered with a company. An unfamiliar label gets
# nothing — a miss an agent can act on — and `aliases.json` is how a user says what
# their own label means.
#
# A deny signal beats an allow signal. `Vendor turnover` names a party and asks for a
# number, and the number is what it wants.
#
# `party` is the only kind acted on today. The others are named rather than collapsed
# into "not a party" because this is the one place that claims to know a field's type,
# and a registry that calls `Effective date` a quantity is wrong exactly where it
# matters. Refusing a value of the WRONG kind ("Effective date: 45") is M6.
_PARTY_WORDS = {
    "party", "parties", "counterparty", "counterparties", "entity", "entities",
    "company", "companies", "firm", "organisation", "organization", "corporation",
    "name", "names", "vendor", "vendors", "supplier", "suppliers", "seller",
    "sellers", "buyer", "buyers", "purchaser", "customer", "client", "clients",
    "contractor", "contractors", "subcontractor", "licensor", "licensee", "lessor",
    "lessee", "landlord", "tenant", "employer", "employee", "borrower", "lender",
    "guarantor", "consultant", "provider", "manufacturer", "distributor", "reseller",
    "partner", "signatory", "applicant", "bidder", "payee", "payer", "principal",
    "agent", "trustee", "beneficiary", "transferor", "transferee", "assignor",
    "assignee", "insurer", "insured", "developer", "owner", "operator", "consignee",
    "consignor", "shipper", "carrier", "importer", "exporter", "promoter", "sponsor",
    "affiliate", "subsidiary", "bank", "banker", "bidders", "awardee",
}
_QUANTITY_WORDS = {
    "cap", "amount", "value", "total", "subtotal", "fee", "fees", "price", "cost",
    "rate", "sum", "budget", "revenue", "turnover", "salary", "wage", "tax", "limit",
    "ceiling", "floor", "balance", "discount", "interest", "consideration", "deposit",
    "royalty", "royalties", "indemnity", "penalty", "liability", "liabilities",
    "advance", "retainer", "premium", "charge", "charges", "payable", "receivable",
    "quantum", "count", "quantity", "volume", "weight", "size", "share", "shares",
    "equity", "stake", "margin", "commission", "percentage", "percent", "instalment",
    "installment", "term", "terms", "duration", "tenure", "period", "days", "months",
    "years", "hours",
}
_DATE_WORDS = {
    "date", "dates", "deadline", "expiry", "expiration", "commencement", "effective",
    "anniversary", "maturity", "due", "renewal", "signed", "executed",
}
_ID_WORDS = {
    "number", "no", "nos", "id", "code", "reference", "ref", "gst", "gstin", "pan",
    "tan", "cin", "tin", "vat", "ifsc", "swift", "iban", "account", "licence",
    "license", "registration", "permit", "invoice", "po", "isbn", "serial", "uid",
    "aadhaar", "passport", "phone", "mobile", "telephone", "fax", "email", "url",
    "website", "pincode", "zip",
}
# Ordered: the kinds that REFUSE a party are asked first, so a label naming both
# ("Vendor turnover") resolves to the one that costs less to get wrong.
_FIELD_WORDS = (
    ("quantity", _QUANTITY_WORDS),
    ("date", _DATE_WORDS),
    ("identifier", _ID_WORDS),
    ("party", _PARTY_WORDS),
)


# Words that invert a compound's head. "Fees payable **to** the vendor" is about fees,
# not about the vendor, so the last recognised word stops being the one that decides.
_LABEL_INVERTS = {"of", "to", "for", "per", "from", "on", "by", "with", "under"}


def _direct_kind(label: str) -> Optional[str]:
    """This label's kind from its own words, or None if none of them is recognised.

    The LAST recognised word decides, because an English compound puts its head noun
    last: `Vendor turnover` is a quantity and `Tax Entity` is a party, and reading the
    label as an unordered bag of words gets the second one wrong — which it did, until
    adversarial review pointed at `Tax Entity` reporting "matched, no clear value" with
    the company sitting right there.

    A preposition inverts that order, and rather than parse the phrase, the label falls
    back to "any kind that refuses a party wins". That is the safe direction: the cost is
    a company not read, and the cost of the other direction is a company presented as an
    amount.
    """
    toks = [t.lower() for t in tokenize(label)]
    if _LABEL_INVERTS & set(toks):
        for kind, words in _FIELD_WORDS:
            if set(toks) & words:
                return kind
        return None
    for tok_text in reversed(toks):
        for kind, words in _FIELD_WORDS:
            if tok_text in words:
                return kind
    return None


def field_kind(label: str, alias_groups=None) -> str:
    """What kind of value this field wants: party / quantity / date / identifier /
    unknown.

    Decided from the label's own words first, then from a synonym the user declared in
    `aliases.json` — a form that says `Manufacturer` is a party field because its owner
    said so. The declared path returns the group's kind, not simply "party", so
    declaring `exposure ceiling` a synonym of `Liability cap` cannot smuggle a company
    into a field that wants an amount.
    """
    direct = _direct_kind(label)
    if direct:
        return direct
    label_stems = [stem(t) for t in tokenize(label)]
    if not label_stems:
        return "unknown"
    for group in alias_groups or []:
        phrase_stems = [[stem(t) for t in tokenize(p)] for p in group]
        if label_stems not in phrase_stems:
            continue                   # this group is not about this field
        kinds = {_direct_kind(p) for p in group} - {None}
        for kind, _words in _FIELD_WORDS:
            if kind in kinds:
                return kind
    return "unknown"

# A run of capitalised words is NOT enough to be a legal name, and the real corpus is
# what proved it. Running this feature over 92,709 real chunks read four names, of
# which three were nonsense and one was right:
#
#   "TCL Confirmed Northwind Systems as the vendor"          <- an investor slide bullet
#   "AB XYZQ Grant PQR Confirmed Northwind Systems"          <- the same deck, no bullet chars
#   "LINES 1 AND 22 MAY DELAY THE ORDER AS THE ..." <- an ALL-CAPS invoice note
#   "Helios Components Private Limited (\"Supplier\")"        <- correct
#
# Slide decks are title-cased and invoices are upper-cased, so "is this word
# capitalised" carries no information in them at all, and extracted deck text has no
# sentence punctuation to stop the scan either. What separates the one right answer is
# that a company states its legal form. So the run must END in a corporate form —
# unless the label was introduced as a quoted or parenthesised DEFINED TERM, which is
# itself a deliberate act of naming.
#
# This makes the feature narrower than "apposition" in general: it reads a corporate
# ENTITY defined by apposition. "IBM as the Vendor" and "Group 4 Sentinel as the
# Vendor" are missed. That is the intended direction — a missed name shows the field
# as unanswered, which an agent can act on; a wrong one it cannot.
_CORPORATE_FORM = {
    "ltd", "limited", "pvt", "private", "llp", "llc", "inc", "incorporated",
    "corp", "corporation", "co", "company", "companies", "plc", "gmbh", "ag",
    "sa", "srl", "bv", "nv", "oy", "ab", "pte", "sdn", "bhd", "kg", "kk",
    "spa", "aps", "oyj", "asa", "pty", "trust", "foundation", "partnership",
}


def _proper_name_before(text: str, at: int, defined_term: bool = False) -> str:
    """The run of proper-noun tokens immediately before offset `at`, or "".

    Scans right to left from `at` and stops at the first token that is neither
    capitalised nor a recognised name part. The result must contain at least one
    capitalised token that is not merely a corporate suffix, is capped at
    `APPOS_MAX_TOKENS` so a long capitalised recital cannot become "the name", and —
    unless `defined_term` — must END in a corporate form (see `_CORPORATE_FORM` for
    what the real corpus proved about that).
    """
    spans = [(m.group(0), m.start(), m.end())
             for m in _TOKEN_SPAN_RE.finditer(text[:at])]
    kept: List[tuple] = []
    right_edge = at
    for idx in range(len(spans) - 1, -1, -1):
        tok, start, end = spans[idx]
        # What sits BETWEEN this token and what has already been accepted to its
        # right. Checking only whether each token was capitalised read "In January,
        # Helios Components Pvt Ltd" as one company name — punctuation, unrelated
        # words and all — and asserted it as the entity's legal name. A corrupted name
        # is worse than no name. Found by adversarial review.
        if not _NAME_JOIN.fullmatch(text[end:right_edge]):
            break
        if (tok.lower().rstrip(".") in _CORPORATE_FORM
                and _NAME_CONJUNCTION.fullmatch(text[end:right_edge])):
            return ""                  # "… Ltd / Gamma Systems LLP" — two companies
        if tok[:1].isupper() or tok.lower() in _NAME_PART:
            kept.append((tok, start, end))
        elif kept and tok.isdigit() and idx and spans[idx - 1][0][:1].isupper():
            # A digit may sit INSIDE a name — "Group 4 Sentinel" is one company, and
            # returning "Sentinel" names a different one. Never at the right edge
            # though (`kept` must already hold something): that edge is exactly where
            # another field's value sits, and refusing it is what keeps `net-45` and
            # `24 months` unreadable as names.
            kept.append((tok, start, end))
        else:
            break
        right_edge = start
        if len(kept) >= APPOS_MAX_TOKENS:
            # More name-like tokens still to the left means the name is longer than
            # can be read safely, and cutting it to fit would silently return a
            # DIFFERENT entity. Refuse instead. Found by adversarial review.
            if idx and _NAME_JOIN.fullmatch(text[spans[idx - 1][2]:start]) and (
                    spans[idx - 1][0][:1].isupper()
                    or spans[idx - 1][0].lower() in _NAME_PART):
                return ""
            break
    # Drop leading name-parts and digits: "of Baroda" is not a name, "Bank of
    # Baroda" is; "4 Sentinel" is not, "Group 4 Sentinel" is.
    while kept and not kept[-1][0][:1].isupper():
        kept.pop()
    if not kept:
        return ""
    if not any(t[:1].isupper() and t.lower() not in _NAME_PART for t, _s, _e in kept):
        return ""                      # only suffixes: "Company", "Ltd" — not a name
    if not defined_term and kept[0][0].lower().rstrip(".") not in _CORPORATE_FORM:
        return ""                      # a capitalised run is not a legal name
    return text[kept[-1][1]:kept[0][2]].strip()


def _proper_name_at(text: str) -> str:
    """The company named at the very START of `text`, or "".

    The forward twin of `_proper_name_before`, for the plainest form there is:
    `Legal name: Beta Holdings Ltd`. v0.5.7 could read a name written *before* its
    label and not one written after it, because a value had to look like a number, an
    amount, a date or an email — and a company name is none of those, so the field
    reported "matched, no clear value".

    Same rules as the backward reader, and for the same reasons: the run must contain a
    capitalised word that is not merely a suffix, it may not be longer than
    `APPOS_MAX_TOKENS` (refused rather than truncated — a cut name is a different
    company), and it must END in a corporate form, which is what the real corpus proved
    separates a company from a capitalised phrase. Here the run is TRIMMED back to its
    last corporate form rather than rejected outright, because a forward run starts
    where the value starts and the words after the legal form are the sentence
    continuing: "Beta Holdings Ltd shall deliver the services".

    Once a legal form has been read, a lowercase joining word ends the name — otherwise
    "Beta Holdings Ltd and Gamma Systems LLP" comes back as one company that does not
    exist.
    """
    spans = [(m.group(0), m.start(), m.end())
             for m in _TOKEN_SPAN_RE.finditer(text)]
    if not spans or not spans[0][0][:1].isupper():
        return ""
    if text[:spans[0][1]].strip(" \t\"'“‘("):
        return ""                      # something other than the name starts here
    kept: List[tuple] = []
    seen_form = False
    for idx, (tok_text, start, end) in enumerate(spans):
        if kept and not _NAME_JOIN.fullmatch(text[kept[-1][2]:start]):
            break
        low = tok_text.lower().rstrip(".")
        if seen_form and kept and _NAME_CONJUNCTION.fullmatch(
                text[kept[-1][2]:start]):
            return ""                  # "Ltd / Gamma …" — two companies, not one
        if seen_form and not tok_text[:1].isupper() and low in _NAME_PART:
            break
        if tok_text[:1].isupper() or low in _NAME_PART:
            kept.append((tok_text, start, end))
        elif (kept and tok_text.isdigit() and idx + 1 < len(spans)
              and spans[idx + 1][0][:1].isupper()):
            kept.append((tok_text, start, end))   # "Group 4 Sentinel" is one company
        else:
            break
        if low in _CORPORATE_FORM:
            seen_form = True
        if len(kept) >= APPOS_MAX_TOKENS:
            nxt = spans[idx + 1] if idx + 1 < len(spans) else None
            if nxt and _NAME_JOIN.fullmatch(text[end:nxt[1]]) and (
                    nxt[0][:1].isupper() or nxt[0].lower() in _NAME_PART):
                return ""              # longer than can be read safely — refuse
            break
    while kept and kept[-1][0].lower().rstrip(".") not in _CORPORATE_FORM:
        kept.pop()
    if not kept:
        return ""                      # a capitalised run is not a legal name
    if not any(t[:1].isupper() and t.lower().rstrip(".") not in _NAME_PART
               for t, _s, _e in kept):
        return ""                      # only suffixes: "Company", "Ltd" — not a name
    return text[kept[0][1]:kept[-1][2]].strip()


# A label, then a separator, then a word. The shape of a document presenting a value
# as a field's own, which is what a name reading requires — see `_presented_as_a_field`.
_FIELD_SEPARATOR = re.compile(r"[ \t]*[:_=–—-]")
_LABELLED_NAME = re.compile(
    r"(?<=[^\W\d_])[ \t]*[:_=–—-][ \t]*(?=[^\W\d_])")


def _presented_as_a_field(text: str, end: int) -> bool:
    """Does a separator sit immediately after the label that ends at `end`?

    A name is read forward only when the document PRESENTS it as this field's value —
    `Legal name: Beta Holdings Ltd` — and not merely because a company follows a label
    word somewhere in prose. Requiring the separator does two things: it keeps
    "Supplier reference: Acme Ltd" from answering `Legal name` (the separator does not
    follow *supplier*), and it keeps this reading identical in shape to
    `carries_labelled_name`, which decides FINDABILITY with no label to work from and
    can only look for the same thing. A signal narrower than the reading it serves is
    the defect v0.5.6 spent a release closing for aliases.
    """
    return bool(_FIELD_SEPARATOR.match(text, end))


def carries_labelled_name(text: str) -> bool:
    """Does `text` present a company as some field's value — "Vendor: Acme Ltd"?

    The findability half of reading a name written after its label. Deliberately WITHOUT
    the field-type test: this signal decides which chunks are reachable and cannot know
    which field will ask. Broader than the reading only spends a ranking tie-break;
    narrower than the reading hides the one chunk that could answer, which is what kept
    v0.5.7's apposition line out of a candidate pool of sixty.
    """
    for m in _LABELLED_NAME.finditer(text):
        if _proper_name_at(text[m.end():]):
            return True
    return False


def _name_follows_a_label(text: str, name: str) -> bool:
    """Does a `Label:` sit immediately before this name?

    Then the run may have absorbed that label's value — "Governing law: Karnataka
    Helios Pvt Ltd" reads back as one name. Structural, so it does not depend on which
    other fields the caller happened to ask for: deciding this from the form's OTHER
    labels meant a single-field request had nothing foreign to compare against and the
    merged name was asserted outright. Found by adversarial review.
    """
    at = text.find(name)
    return at > 0 and text[:at].rstrip().endswith((":", "_"))


def carries_apposition(text: str) -> bool:
    """Does this text define a party by apposition anywhere in it?

    The findability half of the feature (see `carries_value`). Deliberately built from
    the SAME connective and proper-noun rules the extractor uses, rather than a
    separate pattern that approximates them: a signal that decides which chunks are
    reachable, drifting from the reading that answers the field, is precisely the
    defect v0.5.6 spent a release closing for aliases.
    """
    for m in _APPOS_SCAN.finditer(text):
        if _proper_name_before(text, m.start(), "(" in m.group(0)):
            return True
    return False


def apposition_before(text: str, end: int) -> str:
    """The name defined as the role that starts at `end`, or "".

    `end` is the offset just past the label. Requires an apposition connective
    immediately before the label and a proper-noun run immediately before that.
    """
    before = text[:end]
    # The label itself has already been consumed by the caller, so strip it back off
    # to see what introduces it.
    m = _APPOS_CONNECTIVE.search(before)
    if not m:
        return ""
    # A quoted or parenthesised defined term — `Acme (the "Supplier")` — is a
    # deliberate act of naming, so it stands in for the corporate-form requirement.
    return _proper_name_before(before, m.start(), "(" in m.group(0))


def _role_follows(text: str, label: str, label_terms: set, alias_groups) -> bool:
    """Does `text` BEGIN with this field's label, or a declared synonym of it?

    Used to confirm that what an apposition connective introduces really is this
    field's role, and not some other word.
    """
    spans = _token_spans(text)
    if not spans:
        return False
    head = [tok for tok, _s, _e in spans[:max(len(label_terms), 1) + 2]]
    head_stems = [stem(t) for t in head]
    if label_terms <= set(head) or {stem(t) for t in label_terms} <= set(head_stems):
        return True
    label_stems = [stem(t) for t in tokenize(label)]
    for group in alias_groups or []:
        phrase_stems = [[stem(t) for t in tokenize(p)] for p in group]
        if label_stems not in phrase_stems:
            continue                   # this group is not about this field
        for ps in phrase_stems:
            if ps and head_stems[:len(ps)] == ps:
                return True
    return False


def apposition_window(text: str, label: str, label_terms: set,
                      alias_groups=None) -> tuple:
    """(name defined as this field's role, "appos") or (None, None).

    A strict FALLBACK: `_field_answer` only reaches here when no clause yields a value
    in the ordinary forward direction, so this can never displace a real
    label-then-value reading.

    Driven from the CONNECTIVES rather than from the label's position, because a
    clause can name the field's role more than once and only one of them is the
    definition. A real partner agreement reads "1.1 Parties The parties to the present
    agreement are: Helios Components Private Limited ("Supplier")" — `parties` and `Supplier`
    are both synonyms of `Legal name`, and looking only at the first put the lookback
    at the start of a heading. Enumerating connectives finds each candidate naming
    site, and every one still has to pass the same guards.
    """
    for m in _APPOS_SCAN.finditer(text):
        # `_APPOS_SCAN` consumes the first letter of the role, so back up one.
        if not _role_follows(text[m.end() - 1:], label, label_terms, alias_groups):
            continue
        name = _proper_name_before(text, m.start(), "(" in m.group(0))
        if name:
            return name, "appos"
    return None, None


def _value_lines(text: str, terms: set) -> List[str]:
    """Clauses that mention a query term *by token* (so 'term' no longer matches
    'terms') and carry a concrete value.

    "A concrete value" is `first_real_value`, the same rule the index-level signal and
    the field answer use. It used to be any `VALUE_RE` match, so a clause whose only
    number was "clause 4.2" counted as carrying a value and won the ranking tie-break
    from a chunk that could actually answer.
    """
    out = []
    for clause in _clauses(text):
        if (terms & set(tokenize(clause))) and first_real_value(clause) is not None:
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
        m = first_real_value(w)
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
    wants_party = field_kind(label, alias_groups) == "party"
    for clause in _clauses(text):
        end, how = _label_end(clause, label, label_terms, alias_groups)
        if end is None:
            continue
        w = _cut_after(clause, end)
        if not w:
            continue
        value = display = None
        if wants_party and _presented_as_a_field(clause, end):
            # A party field prefers the name over any number in the same window:
            # "Legal name: Beta Holdings Ltd, GST 29ABCDE1234F1Z5" is answered with the
            # company, not the tax number that follows it. The name is exactly and only
            # what was read, so it is also what is shown, and it is what the
            # cross-field check looks at — the window may run on past the name, and
            # judging the name by words it does not contain would downgrade a reading
            # that is not in doubt.
            name = _proper_name_at(w)
            if name:
                value = display = name
                clean = not (foreign_terms & set(tokenize(name)))
        if value is None:
            if wants_party:
                # A company is not a number, in any document. Without this, any digits
                # inside a party field's window could become its value — and on the real
                # corpus they did: an exported ledger reading
                # "…,Vendor Advances,Kestrel India Pvt. Ltd.,8461920000075310642,…"
                # answered `Legal name` with the transaction ID. The registry is what
                # makes this one line instead of a heuristic: a field that wants a party
                # is answered by a name or not at all, and "not at all" still shows the
                # window under `## Needs follow-up` for the agent to read.
                continue
            m = first_real_value(w)
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
    if best is not None:
        return (best[1], best[2], best[3], best[4])

    # Only now: the value may PRECEDE the label ("… as the Vendor"). Reached only when
    # no clause produced a value in the ordinary direction, so a real label-then-value
    # reading always wins and this can never reorder an existing answer.
    # Ranked the same way the forward path ranks: a clean reading first, then the
    # first seen. Returning the first clause that produced anything let an ambiguous
    # reading suppress an unambiguous one right after it, reporting a weak answer
    # where a found one was available. Found by adversarial review.
    # Apposition supplies a corporate entity, so only a field known to want a party is
    # a candidate for it — see `field_kind`.
    if not wants_party:
        return None
    fallback = None
    for clause in _clauses(text):
        name, how = apposition_window(clause, label, label_terms, alias_groups)
        if not name:
            continue
        # A dense clause carrying two fields cannot be read backwards safely: there is
        # no delimiter saying where the previous field's value ended, so the name may
        # have been merged with it. `clean=False` sends it to the weak tier instead of
        # being asserted (the same treatment the forward path gives a dense window).
        clean = (not (foreign_terms & set(tokenize(clause)))
                 and not _name_follows_a_label(clause, name))
        # The displayed region IS the name. Forward readings show the window after the
        # label because the value sits at its head with useful context behind it; here
        # the name is exactly and only what was read, and showing the whole clause
        # would present text the reading did not rely on as though it had.
        if clean:
            return (name, name, True, how)
        if fallback is None:
            fallback = (name, name, False, how)
    return fallback


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


def _pick_field_hit(hits: List[dict], label: str, extra_terms: set,
                    alias_groups=None) -> Optional[dict]:
    """Rerank a field's candidates by task utility, not raw relevance: prefer a
    chunk that actually carries a value and covers the field's words, over a
    higher-BM25 chunk that merely shares vocabulary with the field label.

    "Carries a value" here has to mean the same thing it means to the reading that
    follows, or this step throws away the only chunk that could answer. It did exactly
    that: given sixty chunks reading "Legal name is described in annexure 4 at clause
    4.2" and one reading "Legal name: Beta Holdings Ltd", a chunk-wide `VALUE_RE` scan
    scored every decoy 1 and the answer 0, so a cross-reference was presented as the
    company's legal name. A company name contains no digits — which is the whole reason
    reading one needed its own rules — so this asks the answer path itself as well.

    Widening only: a chunk that already counted still counts, so no field's existing
    pick can move. The free-text sort key `_utility` asks the same question for query
    terms rather than for a field and still recognises numbers only; changing that
    moves evidence ranking for every search, so it needs its own measurement against
    suite A and is tracked, not smuggled in here.
    """
    if not hits:
        return None
    label_terms = set(tokenize(label))

    def utility(h: dict):
        low = h["text"].lower()
        has_value = 1 if (
            _value_lines(h["text"], label_terms | extra_terms)
            or _field_answer(h["text"], label, label_terms, set(), alias_groups)
        ) else 0
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


def _value_and_position(line: str, terms: set):
    """(normalized value, start, end) for the value closest to a query term.

    Proximity matters: in "Q2 update: the team closed 40 deals", the value about
    "deals" is 40, not the incidental "2" in "Q2". Picking the nearest value to a
    matched term avoids that whole class of false conflicts."""
    low = line.lower()
    positions = [low.find(t) for t in terms if t and t in low]
    best, best_at, best_end, best_d = "", -1, -1, 10 ** 9
    for m in VALUE_RE.finditer(line):
        if not positions:
            best, best_at, best_end = m.group(0), m.start(), m.end()
            break
        d = min(abs(m.start() - p) for p in positions)
        if d < best_d:
            best_d, best, best_at, best_end = d, m.group(0), m.start(), m.end()
    return re.sub(r"\s+", " ", best).strip().lower(), best_at, best_end


def _value_near(line: str, terms: set) -> str:
    """The concrete value closest to a query term in the line, normalized."""
    return _value_and_position(line, terms)[0]


# Function words are never the thing a value is about, so they are stepped over when
# looking for the word that labels it.
_PREDICATE_SKIP = {
    "a", "an", "the", "of", "in", "on", "at", "to", "for", "by", "with", "from",
    "is", "was", "were", "are", "be", "been", "being", "has", "have", "had",
    "and", "or", "but", "as", "that", "this", "these", "those", "it", "its",
    "will", "shall", "may", "must", "about", "per", "up", "than", "then", "there",
    "not", "no", "any", "all", "each", "into", "over", "under", "within", "we",
}


def _predicate_of(line: str, at: int, end: int) -> tuple:
    """(word before the value, word after it) — the two ways prose labels a number.

    A conflict is a claim that two values are two readings of ONE fact, so the
    grouping key has to say what each value *means*. It used to be "which query
    terms appear in this line", which says nothing of the kind: a ship date, a
    headcount and a revenue figure about the same subject all shared one key and
    were reported as three values that disagree. On a real GST query that produced
    eight "disagreeing" values that were simply eight different facts — and a
    genuine disagreement would have been buried in the middle of them.

    The word immediately before a value is how both prose and forms label it:
    "Closing date 31/12/2026", "Contract value 42,000,000", "Payment terms:
    net-45". Taking one word keeps the key robust to rephrasing ("revenue was 5
    crore" and "revenue: 5.5 crore" still meet) while separating different facts.
    BOTH sides are needed, and two rounds of adversarial review are why.

    Reading only backwards was too weak: the word before a number is often a function
    word, so "Widget has 12 engineers" and "Widget has 5 offices" both reduced to
    "widget" and were asserted to disagree — two different metrics, one fabricated
    conflict. What a number *counts* is part of what it means, and that word comes
    after it.

    Reading only backwards was also too narrow: "$500,000 is the approved budget" has
    nothing before the value at all, so it got no key and its conflict item was
    dropped — two contradictory budgets shown as plain evidence with no conflict
    reported. Trading a fabricated conflict for a hidden one is a straight loss.

    Deliberately a conservative trade in the other direction: a genuine conflict
    phrased two ways ("revenue was 5 crore" / "revenue totaled 9 crore") is now
    missed, because "was" is a function word and "totaled" is not. Both values are
    still shown as evidence when that happens, so the reader can see them; what
    docdex will not do is assert a disagreement it cannot stand behind. A real fix
    needs a field label rather than neighbouring words — tracked for v0.5.7.
    """
    if at < 0:
        return ("", "")
    before = [t for t in tokenize(line[:at]) if t not in _PREDICATE_SKIP]
    after = [t for t in tokenize(line[end:]) if t not in _PREDICATE_SKIP]
    return (stem(before[-1]) if before else "",
            stem(after[0]) if after else "")


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


def _conflict_label(key) -> str:
    """What to call a conflict in the packet.

    A form field's key is its own label and is shown verbatim. A free-text key is
    (query terms, value kind, word before, word after), so the terms name the subject
    and the labelling words name the fact — shown only where they add something the
    terms don't already say, and in stemmed form because that is what the grouping
    used.
    """
    if isinstance(key, str):
        return key
    if not isinstance(key, tuple) or len(key) != 4:      # pragma: no cover - guard
        return "value"
    terms, _kind, before, after = key
    subject = ", ".join(terms)
    seen = {stem(t) for t in terms}
    extra = [w for w in (before, after) if w and w not in seen]
    if extra:
        return f"{subject} · {' '.join(extra)}" if subject else " ".join(extra)
    return subject or "value"


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
    alias_extra = ((al.query_stems(task, alias_groups) - stemmed(task))
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
            best = _pick_field_hit(fhits, label, label_terms, alias_groups)
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
                value, at, end = _value_and_position(line, terms)
                labels = _predicate_of(line, at, end)
                if not any(labels):
                    continue      # nothing labels this value → no conflict to claim
                # The key names the FACT, not merely the words the line shares with
                # the query: the query terms it mentions, what kind of value it is,
                # and the words that label the value on either side. Two values may
                # only be called disagreeing when all of that matches.
                key = (tuple(sorted(t for t in terms if t in line.lower())),
                       _value_key(value)[0]) + labels
                conflict_items.append((key, value, rel, line))
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
            label = _conflict_label(key)
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
            # The groups the query actually triggered, through the one shared rule —
            # so `--explain` reports the widening that really happened. This used to
            # apply the contiguous-run rule while claiming to match retrieval, which
            # made it a third answer to the same question.
            shown = [" / ".join(g) for g in al.triggered_groups(task, alias_groups)]
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
