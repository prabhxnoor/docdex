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
from docdex.search import run_search, snippet, tokenize

# Lines that look like they carry a concrete value are the best "likely answer"
# candidates. Conservative on purpose — the agent confirms; we only surface.
VALUE_RE = re.compile(
    r"([₹$€£]\s?\d[\d,]*\.?\d*\s*(?:%|percent|crore|lakh|cr\b|mn\b|million|billion|k\b)?)"
    r"|(\d[\d,]*\.?\d*\s*(?:%|percent|crore|lakh|cr\b|mn\b|million|billion|k\b)?)"
    r"|(\b\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}\b)"
    r"|(\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d)"
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
                pool: int, exclude: Optional[set] = None) -> List[dict]:
    """Unified candidate list from the best available engine, with docdex's own
    scaffold files, the form file itself, and near-zero (stopword-only) matches
    filtered out."""
    skip = exclude or set()
    try:
        rows = index_db.search(project, query, folder=folder, limit=pool)
        cands = [{"rel": r["rel"], "chunk": r["chunk_index"], "text": r["text"],
                  "score": r["score"]} for r in rows]
    except FileNotFoundError:
        hits = run_search(project, query, folder=folder, limit=pool)
        cands = [{"rel": rel, "chunk": 0, "text": snip, "score": float(score)}
                 for score, rel, _cache, snip in hits]
    content = set(_content_terms(query))

    def keep(c: dict) -> bool:
        if c["rel"] in skip:
            return False
        # Match *existence* is decided by content-term overlap, never by the BM25
        # display score: a real hit whose score rounds to 0 (a term present in
        # every doc) must not be dropped as "missing" (DDX-030). The score still
        # drives ranking below; it just isn't a truth filter here.
        if not content:
            return c["score"] >= MIN_EVIDENCE_SCORE
        return bool(content & set(tokenize(c["text"])))

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


def _label_window(text: str, label_terms: set) -> Optional[str]:
    """The text right after this field's label — all label tokens must be present
    (by token, so 'term' ≠ 'terms') — cut before the next label or delimiter, so
    a dense fragment yields just this field's value region, not the neighbour's."""
    toks = set(tokenize(text))
    if not label_terms <= toks:
        return None
    low = text.lower()
    start = max(low.rfind(t) + len(t) for t in label_terms)
    after = text[start:]
    stop = _STOP.search(after)
    window = after[:stop.start()] if stop else after
    return re.sub(r"^[\s:_\-]+", "", window).strip()


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
    return n


def _value_key(value: str):
    """Comparison key for conflict grouping: equal amounts collapse to one key
    (no false conflict between '₹4.20 cr' and '4.2 crore'); everything else
    compares by normalized text."""
    n = _amount(value)
    if n is not None:
        return ("num", round(n, 2))
    return ("txt", re.sub(r"\s+", " ", value).strip().lower())


def _field_values(text: str, label_terms: set) -> List[tuple]:
    """Every (clause, value) where this field's label is present and a value
    follows it within the label window — never elsewhere in the clause."""
    out = []
    for clause in _clauses(text):
        w = _label_window(clause, label_terms)
        if not w:
            continue
        m = VALUE_RE.search(w)
        if m:
            out.append((clause, re.sub(r"\s+", " ", m.group(0)).strip()))
    return out


def _field_answer(text: str, label_terms: set, foreign_terms: set):
    """This field's own label-local answer: (value, display, clean) or None.

    The value comes from the window after the label; `clean` is False when that
    window still names another field (a broad/dense line that didn't split), so
    the caller can downgrade it to 'weak' instead of asserting it as found."""
    fallback = None
    for clause in _clauses(text):
        w = _label_window(clause, label_terms)
        if not w:
            continue
        m = VALUE_RE.search(w)
        if not m:
            continue
        value = re.sub(r"\s+", " ", m.group(0)).strip()
        display = _trim(w, 160)
        clean = not (foreign_terms & set(tokenize(w)))
        if clean:
            return (value, display, clean)
        fallback = fallback or (value, display, clean)
    return fallback


def _content_terms(task: str) -> List[str]:
    return [t for t in tokenize(task) if len(t) >= 4 and t not in STOPWORDS]


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


def _read_inv(project: Project):
    """(rows, error): rel → inventory row, or ({}, message) on corrupt inventory.

    One read serves both the mtimes (evidence dates / conflict recency) and the
    sha1s (the scaffold-fingerprint check). A corrupt inventory returns the error
    so the packet warns loudly instead of looking healthy over broken state, and
    never swallows the failure into an empty map (DDX-035)."""
    try:
        return (read_inventory(project.inventory_path), None)
    except DocdexError as e:
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
            reps = [max(group, key=lambda vsl: mtimes.get(vsl[1], ""))
                    for group in by_norm.values()]
            reps.sort(key=lambda vsl: mtimes.get(vsl[1], ""), reverse=True)
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
    inv_rows, state_err = _read_inv(project)
    mtimes = {rel: row.get("mtime_iso", "") for rel, row in inv_rows.items()}
    # Hide the form file and (only) *unchanged* scaffolds; an edited CLAUDE.md is
    # real evidence and must surface (DDX-036).
    skip = (exclude or set()) | _scaffold_excludes(
        project, {rel: row.get("sha1", "") for rel, row in inv_rows.items()})
    pool = _candidates(project, task, folder, pool=40, exclude=skip)

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
            fhits = _candidates(project, label, folder, pool=6, exclude=skip)
            best = _pick_field_hit(fhits, label, label_terms)
            if not best:
                resolved.append({"label": label, "has_value": False,
                                 "line": None, "hit": None})
                continue
            # Extract this field's value label-locally, preferring the candidate
            # that yields a *clean* (single-field) value over a broad/dense line.
            ans, ans_hit = None, best
            for h in [best] + [x for x in fhits if x is not best]:
                cand = _field_answer(h["text"], label_terms, foreign)
                if cand:
                    ans, ans_hit = cand, h
                    if cand[2]:                  # clean → take it
                        break
            if ans is None:
                # matched the label but no clean value — show the label-local
                # text, not a broad snippet that could include a neighbour's value.
                w = _label_window(best["text"], label_terms)
                line = _trim(w, 160) if w else snippet(
                    best["text"], label, sorted(label_terms), width=160)
                resolved.append({"label": label, "has_value": False,
                                 "line": line, "hit": best})
            else:
                _value, display, clean = ans
                resolved.append({"label": label, "has_value": clean,
                                 "line": display, "hit": ans_hit})
            pool.append(ans_hit)
            pinned.add((ans_hit["rel"], ans_hit["chunk"]))
            for h in fhits:                # conflicting values for THIS field only
                for clause, val in _field_values(h["text"], label_terms):
                    conflict_items.append((label, val, h["rel"], clause))

    missing_fields = [r["label"] for r in resolved if r["hit"] is None]

    # ---- Pack under budget: field answers first (the deliverable), then evidence ----
    used = HEADER_RESERVE
    packed_found: List[dict] = []
    packed_weak: List[dict] = []
    dropped_fields: List[str] = []
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
                dropped_fields.append(r["label"])

    top_score = max((c["score"] for c in pool), default=0.0)
    # A relative floor only when scores are meaningful; when every hit scores ~0
    # (a term in every doc), don't let the floor suppress real evidence (DDX-030).
    rel_floor = 0.15 * top_score if top_score > MIN_EVIDENCE_SCORE else 0.0
    seen = set()
    per_source: dict = {}
    evidence = []
    evidence_truncated = False
    if budget_eff:
        for cand in sorted(pool, key=lambda c: -c["score"]):
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
            evidence.append((cand["rel"], cand["chunk"], excerpt, cand["score"]))
            used += cost

    # ---- Free-text answers (value lines) + their conflict candidates ----
    answers = []
    if not form_fields:
        for rel, chunk, excerpt, _score in evidence:
            for line in _value_lines(excerpt, terms):
                answers.append((line, f"{rel} ·{chunk}"))
                key = tuple(sorted(t for t in terms if t in line.lower()))
                conflict_items.append((key, _value_near(line, terms), rel, line))
        answers = answers[:8]
    conflicts = _conflicts(conflict_items, mtimes)

    pool_text = " ".join(c["text"] for c in pool).lower()
    missing_terms = [t for t in terms if t not in pool_text]

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
            answer_block.append(
                f"- {r['label']}: {r['line']}  [{r['hit']['rel']} ·{r['hit']['chunk']}]")
    else:
        for line, source in answers:
            answer_block.append(f"- {line}  [{source}]")
    if answer_block:
        out += ["## Answers", *answer_block, ""]

    if packed_weak:
        out.append("## Needs follow-up (weak)")
        for r in packed_weak:
            out.append(f"- {r['label']}: matched, no clear value — {r['line']}  "
                       f"[{r['hit']['rel']} ·{r['hit']['chunk']}]")
        out.append("")

    if conflicts:
        out.append("## Conflicts")
        for key, reps in conflicts:
            label = key if isinstance(key, str) else (", ".join(key) or "value")
            newest_val, newest_src, _ = reps[0]
            others = "; ".join(f"{v} in {src}" for v, src, _ in reps[1:])
            out.append(f"- {label}: {newest_val} in {newest_src} (newest) vs {others}")
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
            for fld in dropped_fields:
                out.append(f"- {fld}: answer found but cut to fit the budget")
            if evidence_truncated:
                out.append("- some supporting evidence was not packed")
            if over_budget:
                out.append(f"- the packet is larger than the {requested}-token "
                           "budget (kept the minimum to stay useful)")
            out.append(f"- rerun with --budget {bigger} to fit it")
        out.append("")

    out.append("## Evidence")
    if evidence:
        for i, (rel, chunk, excerpt, score) in enumerate(evidence, 1):
            mt = mtimes.get(rel, "")
            mtag = f"  ({mt[:10]})" if mt else ""
            out.append(f"[E{i}] {rel} ·{chunk}{mtag}  (score {score})")
            out.append(f'  "{excerpt}"')
    else:
        reason = " (budget too small)" if budget_eff <= 0 or evidence_truncated else ""
        out.append(f"- no evidence packed{reason}")
    out.append("")

    gap = (missing_fields[0] if missing_fields else
           dropped_fields[0] if dropped_fields else
           " ".join(sorted(missing_terms)) if missing_terms else "")
    if gap:
        out += ["## Suggested next call",
                f'- docdex context "{gap}" --budget 1500'
                + (f" --folder {folder}" if folder else ""), ""]

    if explain:
        out.append("## Explain")
        out.append(f"- query terms: {', '.join(sorted(terms)) or '(none)'}")
        out.append(f"- candidate chunks retrieved: {len(pool)}")
        out.append(f"- evidence packed: {len(evidence)} (≤{MAX_PER_SOURCE}/source); "
                   f"fields {len(packed_found)} found / {len(packed_weak)} weak / "
                   f"{len(missing_fields)} missing / {len(dropped_fields)} dropped")
        engine = "FTS5/BM25" if index_db.available(project) else "cache scorer (no FTS5)"
        out.append(f"- engine: {engine}")
        out.append(f"- tokenizer: {'tiktoken' if tok.using_real_tokenizer() else 'chars/4 estimate'}")

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
