"""`docdex context` — a token-budgeted evidence packet for an LLM task.

This is what an agent reads instead of opening files: the smallest useful set
of cited excerpts for a task, plus what's missing and what to retrieve next.
docdex stays deterministic — it surfaces and packs evidence with citations; the
agent already in the loop does the reasoning. No LLM is called here.
"""
from __future__ import annotations

import re
from typing import List, Optional

from docdex import index_db
from docdex import tokens as tok
from docdex.config import DocdexError, Project
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
}
EXCERPT_CHARS = 360
MAX_PER_SOURCE = 2
MIN_EVIDENCE_SCORE = 0.01   # drop matches that hit only stopwords (BM25 ≈ 0)


class EmptyTask(DocdexError):
    """Raised when a context task has no searchable terms."""


def _is_scaffold(project: Project, rel: str) -> bool:
    """docdex's own auto-generated READMEs are not user evidence."""
    idx = project.index_dir_name
    return rel in (f"{idx}/Update/README.md", f"{idx}/vision_notes/README.md")


def _candidates(project: Project, query: str, folder: Optional[str],
                pool: int) -> List[dict]:
    """Unified candidate list from the best available engine, with docdex's own
    scaffold files and near-zero (stopword-only) matches filtered out."""
    try:
        rows = index_db.search(project, query, folder=folder, limit=pool)
        cands = [{"rel": r["rel"], "chunk": r["chunk_index"], "text": r["text"],
                  "score": r["score"]} for r in rows]
    except FileNotFoundError:
        hits = run_search(project, query, folder=folder, limit=pool)
        cands = [{"rel": rel, "chunk": 0, "text": snip, "score": float(score)}
                 for score, rel, _cache, snip in hits]
    return [c for c in cands
            if c["score"] >= MIN_EVIDENCE_SCORE and not _is_scaffold(project, c["rel"])]


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


def _freshness(project: Project) -> str:
    from docdex.sync import compute_status
    if not project.inventory_path.exists():
        return "NOT BUILT — run sync"
    try:
        return "STALE — run sync" if compute_status(project)["stale"] else "fresh"
    except DocdexError:
        return "unknown"


def build_packet(project: Project, task: str, budget: int = 3000,
                 folder: Optional[str] = None, form_fields: Optional[List[str]] = None,
                 explain: bool = False) -> str:
    if not tokenize(task):
        raise EmptyTask(f"task has no searchable terms: {task!r}")

    # In form mode the field labels define what we're looking for; otherwise the
    # task text does. (Avoids the synthesized "fill the form: x.md" leaking in.)
    if form_fields:
        terms = {t for label in form_fields for t in _content_terms(label)}
    else:
        terms = set(_content_terms(task))
    # Retrieve a candidate pool, then pack under budget with source diversity.
    pool = _candidates(project, task, folder, pool=40)

    fields_report = []          # (label, best_line_or_None, source)
    pinned = set()              # (rel, chunk) that must survive the score floor
    if form_fields:
        for label in form_fields:
            fhits = _candidates(project, label, folder, pool=6)
            best = _pick_field_hit(fhits, label, terms)
            if best:
                vlines = _value_lines(best["text"], set(tokenize(label)) | terms)
                line = vlines[0] if vlines else snippet(
                    best["text"], label, tokenize(label), width=160)
                fields_report.append((label, line, f"{best['rel']} ·{best['chunk']}"))
                pool.append(best)
                pinned.add((best["rel"], best["chunk"]))
            else:
                fields_report.append((label, None, None))

    # Pack evidence: highest score first, at most MAX_PER_SOURCE chunks/source,
    # de-duplicated, dropping weak matches far below the top, until the budget
    # is reached. Pinned form-field hits always survive the relative floor.
    top_score = max((c["score"] for c in pool), default=0.0)
    rel_floor = max(MIN_EVIDENCE_SCORE, 0.15 * top_score)
    seen = set()
    per_source: dict = {}
    evidence = []
    used = 0
    reserve = 200  # headroom for headers/answers/missing sections
    for cand in sorted(pool, key=lambda c: -c["score"]):
        key = (cand["rel"], cand["chunk"])
        if key in seen:
            continue
        if cand["score"] < rel_floor and key not in pinned:
            continue
        if per_source.get(cand["rel"], 0) >= MAX_PER_SOURCE:
            continue
        # Center the excerpt on where the query terms actually appear, so a
        # value buried deep in a long chunk isn't trimmed away.
        excerpt = snippet(cand["text"], task, sorted(terms), width=EXCERPT_CHARS)
        cost = tok.count_tokens(excerpt) + 12
        if used + cost > max(0, budget - reserve) and evidence:
            break
        seen.add(key)
        per_source[cand["rel"]] = per_source.get(cand["rel"], 0) + 1
        evidence.append((cand["rel"], cand["chunk"], excerpt, cand["score"]))
        used += cost

    # Likely answers: value-bearing lines from the chosen evidence.
    answers = []
    for rel, chunk, excerpt, _score in evidence:
        for line in _value_lines(excerpt, terms):
            answers.append((line, f"{rel} ·{chunk}"))
    answers = answers[:8]

    # Missing: content terms with no presence anywhere in the pool; plus any
    # form field that found no evidence.
    pool_text = " ".join(c["text"] for c in pool).lower()
    missing_terms = [t for t in terms if t not in pool_text]
    missing_fields = [lbl for lbl, line, _ in fields_report if line is None]

    fresh = _freshness(project)
    token_note = "" if tok.using_real_tokenizer() else " (≈ chars/4)"
    out = [
        "# context packet",
        f"Task: {task.strip()}",
        f"Budget: {budget} tok  |  Used: ~{used}{token_note}  |  Index: {fresh}",
        "",
    ]

    out.append("## Likely answers (cited)")
    if form_fields:
        for label, line, source in fields_report:
            if line:
                out.append(f"- {label}: {line}  [{source}]")
            else:
                out.append(f"- {label}: — not found")
    elif answers:
        for line, source in answers:
            out.append(f"- {line}  [{source}]")
    else:
        out.append("- (no value lines matched directly — see Evidence)")
    out.append("")

    out.append("## Evidence")
    if evidence:
        for i, (rel, chunk, excerpt, score) in enumerate(evidence, 1):
            out.append(f"[E{i}] {rel} ·{chunk}  (score {score})")
            out.append(f'  "{excerpt}"')
    else:
        out.append("- no matching evidence in the index")
    out.append("")

    if missing_terms or missing_fields:
        out.append("## Missing")
        for f in missing_fields:
            out.append(f"- {f} — no evidence found")
        if missing_terms:
            out.append(f"- no index hits for: {', '.join(sorted(missing_terms))}")
        out.append("")
        gap = missing_fields[0] if missing_fields else " ".join(sorted(missing_terms))
        out.append("## Next")
        out.append(f'- docdex context "{gap}" --budget 1000'
                   + (f" --folder {folder}" if folder else ""))
        out.append("")

    if explain:
        out.append("## Explain")
        out.append(f"- query terms: {', '.join(sorted(terms)) or '(none)'}")
        out.append(f"- candidate chunks retrieved: {len(pool)}")
        out.append(f"- evidence packed: {len(evidence)} (≤{MAX_PER_SOURCE}/source)")
        engine = "FTS5/BM25" if index_db.available(project) else "cache scorer (no FTS5)"
        out.append(f"- engine: {engine}")
        out.append(f"- tokenizer: {'tiktoken' if tok.using_real_tokenizer() else 'chars/4 estimate'}")

    return "\n".join(out).rstrip() + "\n"


def parse_form_fields(text: str, limit: int = 40) -> List[str]:
    """Pull likely field labels from a form's text: 'Label:' or 'Label ____'."""
    fields = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"^[-*\d.)\s]*([A-Za-z][A-Za-z0-9 /&'()\-]{2,60}?)\s*[:_]", line)
        if m:
            label = m.group(1).strip()
            if label and label.lower() not in (f.lower() for f in fields):
                fields.append(label)
        if len(fields) >= limit:
            break
    return fields
