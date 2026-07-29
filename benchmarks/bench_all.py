"""Versioned benchmarking — run every suite, keep the history, see the trend.

Why this exists: Suite A (`run_benchmark.py`, single-fact retrieval) was last
recorded at **v0.1.1** and then not re-run for five releases, while its headline
stayed quoted in the README. Suite B *was* re-run, but each run overwrote the last,
so there was no trend — and the v0.5.0 regression it later exposed (a field silently
traded for another at an unchanged 8/11) was invisible for exactly that reason.

The first backfill sweep settled a question that could not be answered before it
existed: paraphrased-query retrieval reads 4/12 right-file-first today versus 7/12 in
the v0.1.1 record, and the sweep shows it is **bit-identical across every tagged
release from v0.2.0 on**. So that change belongs to the untagged v0.1.1 -> v0.2.0
window — where BM25/FTS5 replaced the original scorer — and is not drift since. Being
able to say that, instead of guessing, is the point of keeping history.

  record                      run every suite on the current tree, append to history
  sweep <ref> [<ref> ...]     run every suite against each git ref, fixed oracle
  show                        rewrite HISTORY.md from HISTORY.json

The sweep is the point: it checks out each release into a worktree and overlays
**today's** `benchmarks/` directory onto it, so the harness, corpus and scoring are
identical everywhere and only `src/` varies. Comparing releases whose benchmark
harness also differs measures the oracle, not the product.

A release whose numbers can't be produced is recorded as an error rather than
skipped — "this harness can't run against that release" is a real result, and
silence would read as "no change".
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HISTORY_JSON = REPO / "benchmarks" / "HISTORY.json"
HISTORY_MD = REPO / "benchmarks" / "HISTORY.md"

# Suite A methods worth trending. `docdex` = exact-ish query, `docdex-fuz` = the
# paraphrased query a user actually types — the weakest path, and the one worth
# watching most closely.
A_METHODS = ["docdex", "docdex-fuz", "docdex-sem-x", "docdex-sem", "readall"]
A_LABEL = {"docdex": "search (exact)", "docdex-fuz": "search (fuzzy)",
           "docdex-sem-x": "semantic (exact)", "docdex-sem": "semantic (fuzzy)",
           "readall": "read-everything"}

# Metrics where a DROP is a regression (higher is better).
A_HIGHER_BETTER = ["hit1", "hit3", "answered"]


def run(cmd: list, cwd: Path, env: dict = None) -> subprocess.CompletedProcess:
    import os
    e = dict(os.environ)
    if env:
        e.update(env)
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, env=e)


def _suite(tree: Path, script: str, out_name: str, cache: Path,
           seed: str = "0") -> dict:
    """Run one benchmark script in `tree` and return its result JSON."""
    out = tree / "benchmarks" / out_name
    if out.exists():
        out.unlink()               # never read a previous run's numbers
    proc = run([sys.executable, f"benchmarks/{script}"], tree,
               env={"PYTHONHASHSEED": seed, "DOCDEX_CACHE_DIR": str(cache)})
    if proc.returncode != 0 or not out.exists():
        return {"error": f"exit {proc.returncode}",
                "stderr": (proc.stderr or proc.stdout)[-800:]}
    return json.loads(out.read_text(encoding="utf-8"))


def measure(tree: Path, ref: str, sha: str, version: str,
            seed: str = "0") -> dict:
    work = Path(tempfile.mkdtemp(prefix="docdex-bench-"))
    a = _suite(tree, "run_benchmark.py", "results.json", work / "a", seed)
    b = _suite(tree, "task_benchmark.py", "results_task.json",
               work / "b", seed)
    rec = {
        "ref": ref, "sha": sha, "version": version,
        "recorded_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "suite_a": ({"error": a["error"], "stderr": a.get("stderr", "")}
                    if "error" in a else
                    {"meta": a["meta"],
                     "summary": {m: a["summary"][m] for m in A_METHODS
                                 if m in a["summary"]}}),
    }
    if "error" in b:
        rec["suite_b"] = {"error": b["error"], "stderr": b.get("stderr", "")}
    else:
        ctx = b["results"]["docdex context"]
        rec["suite_b"] = {
            "findable": b["findable"],
            "covered": sorted(ctx["covered"]),
            "covered_n": len(ctx["covered"]),
            "tokens": ctx["tokens"],
            "honest_absent": sorted(ctx.get("honest_absent", [])),
            "fields": ctx.get("fields", {}),
            "packet_sha256": ctx.get("packet_sha256", ""),
            "search_loop_tokens": b["results"]["search-loop"]["tokens"],
        }
    return rec


def load_history() -> dict:
    if HISTORY_JSON.exists():
        return json.loads(HISTORY_JSON.read_text(encoding="utf-8"))
    return {"records": []}


def save_history(hist: dict) -> None:
    HISTORY_JSON.write_text(json.dumps(hist, indent=2, sort_keys=True) + "\n",
                            encoding="utf-8")


def upsert(hist: dict, rec: dict) -> None:
    """One record per (ref, sha): re-recording a ref replaces it in place."""
    hist["records"] = [r for r in hist["records"]
                       if not (r["ref"] == rec["ref"] and r["sha"] == rec["sha"])]
    hist["records"].append(rec)


def _order(records: list) -> list:
    """Chronological by record time — the order releases actually happened."""
    return sorted(records, key=lambda r: (r["recorded_utc"], r["ref"]))


def regressions(records: list) -> list:
    """Metrics that got worse from one recorded version to the next."""
    out = []
    ordered = _order(records)
    for prev, cur in zip(ordered, ordered[1:]):
        pa, ca = prev.get("suite_a", {}), cur.get("suite_a", {})
        if "summary" in pa and "summary" in ca:
            for m in A_METHODS:
                if m in pa["summary"] and m in ca["summary"]:
                    for k in A_HIGHER_BETTER:
                        p, c = pa["summary"][m].get(k), ca["summary"][m].get(k)
                        if p is not None and c is not None and c < p:
                            out.append(f"{prev['ref']} -> {cur['ref']}: suite A "
                                       f"{A_LABEL.get(m, m)} {k} {p} -> {c}")
        pb, cb = prev.get("suite_b", {}), cur.get("suite_b", {})
        if "covered" in pb and "covered" in cb:
            lost = sorted(set(pb["covered"]) - set(cb["covered"]))
            if lost:
                out.append(f"{prev['ref']} -> {cur['ref']}: suite B lost {lost}")
    return out


def render(hist: dict) -> str:
    ordered = _order(hist["records"])
    L = ["# docdex benchmark history", "",
         "Every release, every suite, one table. Regenerate with "
         "`python3 benchmarks/bench_all.py show`; add a release with `record`, or "
         "backfill old ones with `sweep`.", "",
         "All rows are produced by **today's** harness — `sweep` overlays the "
         "current `benchmarks/` onto each checked-out release, so only `src/` "
         "differs between rows. Rows recorded by an older harness would compare "
         "the oracle instead of the product.", ""]

    L += ["## Suite A — single-fact retrieval (12 planted facts, 162 files)", "",
          "`hit1` = right file ranked first. `answered` = the answer string was "
          "reached. `tok` = median tokens to the answer.", "",
          "| release | search exact hit1 · answered · tok | search **fuzzy** hit1 · "
          "answered · tok | semantic exact hit1 | read-all tok |",
          "|---|---|---|---|---|"]
    for r in ordered:
        a = r.get("suite_a", {})
        if "summary" not in a:
            L.append(f"| `{r['ref']}` | _not recorded: {a.get('error', 'n/a')}_ "
                     f"| | | |")
            continue
        s = a["summary"]

        def cell(m):
            if m not in s:
                return "—"
            d = s[m]
            return f"{d['hit1']}/12 · {d['answered']}/12 · {d['med_tokens']:,}"
        L.append(f"| `{r['ref']}` | {cell('docdex')} | {cell('docdex-fuz')} "
                 f"| {s.get('docdex-sem-x', {}).get('hit1', '—')}/12 "
                 f"| {s.get('readall', {}).get('med_tokens', 0):,} |")

    L += ["", "## Suite B — multi-field form filling (12 fields, 1 absent)", "",
          "| release | fields covered | packet tokens | absent flagged honestly |",
          "|---|---|---|---|"]
    for r in ordered:
        b = r.get("suite_b", {})
        if "covered" not in b:
            L.append(f"| `{r['ref']}` | _not recorded: {b.get('error', 'n/a')}_ | | |")
            continue
        L.append(f"| `{r['ref']}` | {b['covered_n']}/{b['findable']} "
                 f"| {b['tokens']:,} | {len(b['honest_absent'])} |")

    regs = regressions(hist["records"])
    L += ["", "## Regressions between recorded releases", ""]
    L += ([f"- {r}" for r in regs] if regs else
          ["_None across the recorded releases._"])
    L += ["", "Each line is a metric that went **down** from one release to the "
          "next. A line here is not automatically a bug — a deliberate trade "
          "belongs in the changelog — but it must never be a surprise.", ""]
    return "\n".join(L)


def git(*args: str) -> str:
    """`git rev-parse <args>` — each arg separate, or git parses "--short HEAD" as a
    single (invalid) revision and the recorded sha is garbage."""
    return run(["git", "rev-parse", *args], REPO).stdout.strip()


def version_of(tree: Path) -> str:
    init = tree / "src" / "docdex" / "__init__.py"
    for ln in init.read_text(encoding="utf-8").splitlines():
        if ln.startswith("__version__"):
            return ln.split("=", 1)[1].strip().strip('"\'')
    return "?"


def cmd_record() -> int:
    sha = git("--short", "HEAD")
    rec = measure(REPO, git("--abbrev-ref", "HEAD") or "HEAD", sha,
                  version_of(REPO))
    rec["ref"] = f"v{rec['version']}"
    hist = load_history()
    upsert(hist, rec)
    save_history(hist)
    HISTORY_MD.write_text(render(hist), encoding="utf-8")
    print(f"recorded {rec['ref']} ({sha})")
    for line in regressions(hist["records"]):
        print(f"  REGRESSION  {line}")
    return 0


def cmd_sweep(refs: list) -> int:
    hist = load_history()
    for ref in refs:
        work = Path(tempfile.mkdtemp(prefix=f"docdex-sweep-"))
        tree = work / "t"
        res = run(["git", "worktree", "add", "-q", "--detach", str(tree), ref], REPO)
        if not (tree / "src").exists():
            print(f"  {ref}: cannot check out ({res.stderr.strip()[:120]})")
            continue
        # Fixed oracle: today's harness, that release's src.
        shutil.rmtree(tree / "benchmarks", ignore_errors=True)
        shutil.copytree(REPO / "benchmarks", tree / "benchmarks")
        sha = run(["git", "rev-parse", "--short", "HEAD"], tree).stdout.strip()
        rec = measure(tree, ref, sha, version_of(tree))
        upsert(hist, rec)
        a_ok = "summary" in rec["suite_a"]
        b_ok = "covered" in rec["suite_b"]
        print(f"  {ref} ({sha}): suite A {'ok' if a_ok else 'ERROR'}, "
              f"suite B {'ok' if b_ok else 'ERROR'}")
        run(["git", "worktree", "remove", "--force", str(tree)], REPO)
    save_history(hist)
    HISTORY_MD.write_text(render(hist), encoding="utf-8")
    print(f"\nhistory: {HISTORY_MD}")
    for line in regressions(hist["records"]):
        print(f"  REGRESSION  {line}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("record", help="run every suite on the current tree")
    sw = sub.add_parser("sweep", help="run every suite against each git ref")
    sw.add_argument("refs", nargs="+")
    sub.add_parser("show", help="rewrite HISTORY.md from HISTORY.json")
    args = ap.parse_args()

    if args.cmd == "record":
        return cmd_record()
    if args.cmd == "sweep":
        return cmd_sweep(args.refs)
    hist = load_history()
    HISTORY_MD.write_text(render(hist), encoding="utf-8")
    print(f"wrote {HISTORY_MD} from {len(hist['records'])} record(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
