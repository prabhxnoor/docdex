"""docdex value benchmark — docdex retrieval vs. what an agent does without it.

For every planted fact (see corpus_gen.py) we ask: how much text must an LLM
agent ingest before the answer string is in its context, and does the method
even surface the right file?

Methods compared:
  filename   list all files, open the 3 whose names best match the query
             (the "browse by filename" move — filenames in this corpus lie)
  rawgrep    grep -ril query terms over the raw corpus, open top 3 matches
             (Office files are zip containers, PDF streams are compressed —
             grep is structurally blind to them)
  readall    read every file's text in path order until the answer appears
             (the guaranteed-success fallback: "load everything")
  docdex     `docdex search "<exact-ish query>"`, read the returned snippets,
             then the top-1 file only if the snippets didn't contain the answer
  docdex-sem `docdex semantic "<fuzzy query>"` — same accounting, using the
             paraphrased query a user types when they forgot the wording

Token counts are chars/4 (approximation, stated in the report). Run:

    python3 benchmarks/run_benchmark.py [--keep] [--workdir DIR]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import platform
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
SRC = REPO / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE))

import corpus_gen  # noqa: E402
from docdex.config import Project  # noqa: E402

TOKEN_DIVISOR = 4


def tokens(text: str) -> int:
    return math.ceil(len(text) / TOKEN_DIVISOR)


def contains_answer(haystack: str, answer: str) -> bool:
    """Whitespace-insensitive: extractors may wrap text mid-phrase; a reader
    (human or LLM) still sees the answer."""
    norm = re.sub(r"\s+", " ", haystack)
    return answer in norm


def query_terms(query: str):
    return [t for t in re.findall(r"[A-Za-z0-9][A-Za-z0-9\-]+", query.lower())
            if len(t) > 2]


def run_docdex(root: Path, *argv, timeout=600):
    env = dict(os.environ, PYTHONPATH=str(SRC))
    t0 = time.perf_counter()
    proc = subprocess.run([sys.executable, "-m", "docdex", *argv],
                          cwd=str(root), env=env, capture_output=True,
                          text=True, timeout=timeout)
    return proc, (time.perf_counter() - t0) * 1000


def parse_ranked_paths(stdout: str):
    return re.findall(r"^\[#\d+\] \S+=\S+\s+(.+?)(?:\s+chunk=\d+)?$",
                      stdout, flags=re.M)


def extracted_text(project: Project, rel: str) -> str:
    cache = project.cache_path_for(rel)
    try:
        return cache.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def all_rel_files(root: Path):
    rels = []
    for p in sorted(root.rglob("*")):
        if p.is_file() and not p.name.startswith(".") and "_index" not in p.parts:
            rels.append(p.relative_to(root).as_posix())
    return rels


# ------------------------------------------------------------------ methods
def method_filename(root, project, case, rels):
    """Rank files by query-term overlap with their path; read top 3."""
    terms = query_terms(case["query_exact"])
    t0 = time.perf_counter()
    scored = []
    for rel in rels:
        path_tokens = re.findall(r"[a-z0-9]+", rel.lower())
        score = sum(1 for t in terms if t in path_tokens)
        scored.append((score, rel))
    scored.sort(key=lambda x: (-x[0], x[1]))
    top = [rel for score, rel in scored[:3] if score > 0]
    ms = (time.perf_counter() - t0) * 1000

    listing = "\n".join(rels)
    consumed = tokens(listing)
    found = False
    for rel in top:
        text = extracted_text(project, rel)
        consumed += tokens(text)
        if contains_answer(text, case["answer"]):
            found = True
            break
    return dict(hit1=bool(top) and top[0] == case["rel"],
                hit3=case["rel"] in top, answer_found=found,
                tokens=consumed, ms=ms)


def method_rawgrep(root, project, case, rels):
    """grep -ril each query term over the raw files; read top 3 matches."""
    terms = query_terms(case["query_exact"])
    t0 = time.perf_counter()
    counts = {}
    for term in terms:
        proc = subprocess.run(
            ["grep", "-ril", "--exclude-dir=_index", term, "."],
            cwd=str(root), capture_output=True, text=True)
        for line in proc.stdout.splitlines():
            rel = line.lstrip("./")
            counts[rel] = counts.get(rel, 0) + 1
    ranked = sorted(counts, key=lambda r: (-counts[r], r))[:3]
    ms = (time.perf_counter() - t0) * 1000

    consumed = tokens("\n".join(ranked))
    found = False
    for rel in ranked:
        text = extracted_text(project, rel)
        consumed += tokens(text)
        if contains_answer(text, case["answer"]):
            found = True
            break
    return dict(hit1=bool(ranked) and ranked[0] == case["rel"],
                hit3=case["rel"] in ranked, answer_found=found,
                tokens=consumed, ms=ms)


def method_readall(root, project, case, rels):
    """Read every file's text in path order until the answer appears."""
    t0 = time.perf_counter()
    consumed = 0
    found = False
    for rel in rels:
        text = extracted_text(project, rel)
        consumed += tokens(text)
        if contains_answer(text, case["answer"]):
            found = True
            break
    ms = (time.perf_counter() - t0) * 1000
    return dict(hit1=found, hit3=found, answer_found=found,
                tokens=consumed, ms=ms)


def method_docdex(root, project, case, query, command):
    proc, ms = run_docdex(root, command, query, "-n", "3")
    out = proc.stdout
    ranked = parse_ranked_paths(out)
    consumed = tokens(out)
    found = contains_answer(out, case["answer"])
    if not found and ranked:
        text = extracted_text(project, ranked[0])
        consumed += tokens(text)
        found = contains_answer(text, case["answer"])
    return dict(hit1=bool(ranked) and ranked[0] == case["rel"],
                hit3=case["rel"] in ranked, answer_found=found,
                tokens=consumed, ms=ms)


# -------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default=None)
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    work = Path(args.workdir) if args.workdir else Path(tempfile.mkdtemp(prefix="docdex-bench-"))
    root = work / "corpus"
    print(f"corpus: {root}")
    cases = corpus_gen.generate(root)
    rels = all_rel_files(root)
    raw_bytes = sum((root / r).stat().st_size for r in rels)

    print("indexing (one-time cost)...")
    run_docdex(root, "init", "--no-agent-docs", "--no-wrapper")
    _, sync_ms = run_docdex(root, "sync", "--no-prefetch", "--no-embed",
                            "--no-vision", "--no-dumps")
    _, embed_ms = run_docdex(root, "embed")
    project = Project.load(root)
    index_bytes = sum(p.stat().st_size for p in project.state_dir.rglob("*") if p.is_file())
    corpus_tokens = sum(tokens(extracted_text(project, r)) for r in rels)

    methods = {
        "filename": lambda c: method_filename(root, project, c, rels),
        "rawgrep": lambda c: method_rawgrep(root, project, c, rels),
        "readall": lambda c: method_readall(root, project, c, rels),
        "docdex": lambda c: method_docdex(root, project, c, c["query_exact"], "search"),
        "docdex-sem-x": lambda c: method_docdex(root, project, c, c["query_exact"], "semantic"),
        "docdex-fuz": lambda c: method_docdex(root, project, c, c["query_fuzzy"], "search"),
        "docdex-sem": lambda c: method_docdex(root, project, c, c["query_fuzzy"], "semantic"),
    }

    results = {name: {} for name in methods}
    for qid, case in sorted(cases.items()):
        print(f"  {qid}: {case['query_exact'][:60]}")
        for name, fn in methods.items():
            results[name][qid] = fn(case)

    # ------------------------------------------------------------- report
    def agg(name):
        rows = results[name].values()
        return dict(
            hit1=sum(r["hit1"] for r in rows),
            hit3=sum(r["hit3"] for r in rows),
            answered=sum(r["answer_found"] for r in rows),
            med_tokens=int(statistics.median(r["tokens"] for r in rows)),
            med_ms=int(statistics.median(r["ms"] for r in rows)),
        )

    n = len(cases)
    summary = {name: agg(name) for name in methods}
    meta = dict(
        files=len(rels), raw_mb=round(raw_bytes / 1e6, 2),
        corpus_tokens=corpus_tokens, cases=n,
        sync_s=round(sync_ms / 1000, 1), embed_s=round(embed_ms / 1000, 1),
        index_mb=round(index_bytes / 1e6, 2),
        python=platform.python_version(), platform=platform.platform(),
        token_model=f"chars/{TOKEN_DIVISOR} approximation",
    )

    lines = [
        "# docdex benchmark results", "",
        f"Corpus: **{meta['files']} files** ({meta['raw_mb']} MB raw, "
        f"~{meta['corpus_tokens']:,} tokens of text), {n} planted facts behind "
        "misleading filenames. Deterministic (seed 42) — regenerate and rerun with "
        "`python3 benchmarks/run_benchmark.py`.", "",
        f"One-time indexing: sync {meta['sync_s']}s + semantic build {meta['embed_s']}s; "
        f"index on disk {meta['index_mb']} MB. "
        f"Environment: Python {meta['python']}, {meta['platform']}. "
        f"Token counts are a {meta['token_model']}.", "",
        "| method | right file ranked #1 | in top 3 | answer reached | median tokens to answer | median ms |",
        "|---|---|---|---|---|---|",
    ]
    label = {
        "filename": "browse by filename (no docdex)",
        "rawgrep": "raw `grep -ril` (no docdex)",
        "readall": "read everything (no docdex)",
        "docdex": "**`docdex search`** (exact-ish query)",
        "docdex-sem-x": "`docdex semantic` (exact-ish query)",
        "docdex-fuz": "`docdex search` (fuzzy/paraphrased query)",
        "docdex-sem": "**`docdex semantic`** (fuzzy query)",
    }
    for name in methods:
        s = summary[name]
        lines.append(f"| {label[name]} | {s['hit1']}/{n} | {s['hit3']}/{n} | "
                     f"{s['answered']}/{n} | {s['med_tokens']:,} | {s['med_ms']:,} |")

    ratio = summary["readall"]["med_tokens"] / max(1, summary["docdex"]["med_tokens"])
    lines += [
        "",
        f"Headline: to reach an answer, `docdex search` needs a median of "
        f"**{summary['docdex']['med_tokens']:,} tokens** vs "
        f"**{summary['readall']['med_tokens']:,}** for the read-everything fallback — "
        f"**{ratio:,.0f}× less context** per question, after a one-time "
        f"{meta['sync_s']}s indexing cost. Filename browsing and raw grep are "
        "structurally blind to Office/PDF content and fail on most questions.",
        "",
        "## Per-question detail", "",
        "| case | method | hit@1 | hit@3 | answered | tokens | ms |", "|---|---|---|---|---|---|---|",
    ]
    for qid in sorted(cases):
        for name in methods:
            r = results[name][qid]
            lines.append(f"| {qid} | {name} | {'Y' if r['hit1'] else '-'} | "
                         f"{'Y' if r['hit3'] else '-'} | {'Y' if r['answer_found'] else '-'} | "
                         f"{r['tokens']:,} | {int(r['ms'])} |")

    report = "\n".join(lines) + "\n"
    (HERE / "RESULTS.md").write_text(report, encoding="utf-8")
    (HERE / "results.json").write_text(
        json.dumps({"meta": meta, "summary": summary, "cases": results}, indent=2),
        encoding="utf-8")
    print("\n" + "\n".join(lines[:20]))
    print(f"\nfull report: {HERE / 'RESULTS.md'}")

    if not args.keep:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
