"""`docdex context` — a token-budgeted evidence packet for an LLM task.

This is what an agent reads instead of opening files: the smallest useful set
of cited excerpts for a task, an explicit account of what was found / weak /
missing / dropped-by-budget, and any conflicts between sources. docdex stays
deterministic — it surfaces and packs evidence with citations; the agent already
in the loop does the reasoning. No LLM is called here.
"""
from __future__ import annotations

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
    r"(\d[\d,]*\.?\d*\s*(?:%|percent|crore|lakh|cr\b|mn\b|million|billion)?)"
    r"|([₹$€£]\s?\d)"
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


def _is_scaffold(project: Project, rel: str) -> bool:
    """docdex's own auto-generated instruction/READMEs are not user evidence —
    they describe docdex, not the corpus, so they must never be cited as answers."""
    idx = project.index_dir_name
    return rel in (
        "CLAUDE.md", "AGENTS.md",
        f"{idx}/HANDOFF.md", f"{idx}/00_MASTER_INDEX.md",
        f"{idx}/Update/README.md", f"{idx}/vision_notes/README.md",
    )


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
    return [c for c in cands
            if c["score"] >= MIN_EVIDENCE_SCORE
            and c["rel"] not in skip
            and not _is_scaffold(project, c["rel"])]


def _trim(text: str, limit: int = EXCERPT_CHARS) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + " …"


def _value_lines(text: str, terms: set) -> List[str]:
    out = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n", text):
        low = sentence.lower()
        if any(t in low for t in terms) and VALUE_RE.search(sentence):
            out.append(_trim(sentence, 160))
    return out


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


def _mtime_map(project: Project) -> dict:
    """rel → mtime_iso, read once from the inventory (cheap; no corpus walk)."""
    try:
        return {rel: row.get("mtime_iso", "")
                for rel, row in read_inventory(project.inventory_path).items()}
    except DocdexError:
        return {}


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
    """Group (key, value, source, line) tuples by key; a group whose sources give
    two or more *different* values is a conflict. Newer source (by mtime) first."""
    groups: "OrderedDict[object, list]" = OrderedDict()
    for key, value, source, line in items:
        if not key or not value:
            continue
        groups.setdefault(key, []).append((value, source, line))
    out = []
    for key, members in groups.items():
        by_value: "OrderedDict[str, tuple]" = OrderedDict()
        for value, source, line in members:
            by_value.setdefault(value, (source, line))
        if len(by_value) >= 2:
            ranked = sorted(by_value.items(),
                            key=lambda kv: mtimes.get(kv[1][0], ""), reverse=True)
            out.append((key, ranked))
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
    mtimes = _mtime_map(project)
    pool = _candidates(project, task, folder, pool=40, exclude=exclude)

    # ---- Resolve each form field (retrieval only; budget applied when packing) ----
    resolved: List[dict] = []          # {label, has_value, line, hit|None}
    pinned = set()
    conflict_items: list = []          # (key, value, source, line)
    if form_fields:
        for label in form_fields:
            label_terms = set(tokenize(label))
            fhits = _candidates(project, label, folder, pool=6, exclude=exclude)
            best = _pick_field_hit(fhits, label, label_terms)
            if not best:
                resolved.append({"label": label, "has_value": False,
                                 "line": None, "hit": None})
                continue
            # Match the field's OWN label terms only — never the union of all
            # fields, or one field's value line leaks into another's answer.
            vlines = _value_lines(best["text"], label_terms)
            if vlines:
                line = re.sub(rf"^\s*{re.escape(label)}\s*[:_]\s*", "",
                              vlines[0], flags=re.I).strip() or vlines[0]
            else:
                line = snippet(best["text"], label, tokenize(label), width=160)
            resolved.append({"label": label, "has_value": bool(vlines),
                             "line": line, "hit": best})
            pool.append(best)
            pinned.add((best["rel"], best["chunk"]))
            for h in fhits:                # conflicting values for THIS field only
                for vl in _value_lines(h["text"], label_terms):
                    conflict_items.append(
                        (label, _value_near(vl, label_terms), h["rel"], vl))

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
    rel_floor = max(MIN_EVIDENCE_SCORE, 0.15 * top_score)
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

    free = max(0, budget_eff - used)
    note = "" if tok.using_real_tokenizer() else " (≈ chars/4)"
    out = [
        "# context packet",
        f"Task: {task.strip()}",
        f"Coverage: {coverage}",
        f"Budget: {requested} requested · ~{used} used{note} · {free} free",
        f"Index: {_freshness(project, check_freshness)}",
        "",
    ]
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
        for key, ranked in conflicts:
            label = key if isinstance(key, str) else (", ".join(key) or "value")
            newest_val, (newest_src, _) = ranked[0]
            others = "; ".join(f"{v} in {src}" for v, (src, _) in ranked[1:])
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

    if dropped_fields or evidence_truncated or budget_eff <= 0:
        bigger = max(2000, requested * 2) if requested > 0 else 2000
        out.append("## Dropped (budget)")
        if budget_eff <= 0:
            out.append(f"- everything (budget was {requested}) — rerun with --budget {bigger}")
        else:
            for fld in dropped_fields:
                out.append(f"- {fld}: answer found but cut to fit the budget")
            if evidence_truncated:
                out.append("- some supporting evidence was not packed")
            out.append(f"- rerun with --budget {bigger} to include the above")
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

    return "\n".join(out).rstrip() + "\n"


def parse_form_fields(text: str, limit: int = 200) -> List[str]:
    """Pull likely field labels from a form's text: 'Label:' or 'Label ____'.

    Unicode-aware (so 'Échéance' / 'Numéro fiscal' parse), and the cap is high
    enough that real forms are not silently truncated (DDX-020)."""
    fields: List[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"^[-*\d.)\s]*([^\W\d_][\w /&'()\-]{1,60}?)\s*[:_]", line, re.UNICODE)
        if m:
            label = m.group(1).strip()
            if label and label.lower() not in (f.lower() for f in fields):
                fields.append(label)
        if len(fields) >= limit:
            break
    return fields
