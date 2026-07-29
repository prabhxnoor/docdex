"""Release QA gate — proves a release fixes what it claims and breaks nothing.

Run before tagging:  python3 benchmarks/qa_release.py --base v0.5.0

Five gates, all of which must pass. Gate 3 is the unusual one and the reason this
script exists.

  1. GREEN ON HEAD      full suite passes: zero failures, zero collection errors,
                        pytest exit 0, and a positive test count.
  2. NO REGRESSION      the form benchmark is compared to the base release PER
                        FIELD — value, section and cited source — plus honest
                        absent-field handling and a token ceiling. v0.5.0 held
                        8/11 while silently trading one field for another, so a
                        headline count is not a regression detector.
  3. TESTS DISCRIMINATE the release's new tests run against the BASE tree and at
                        least one must fail there *on an assertion*. A regression
                        test that passes on the code it was written to catch
                        proves nothing — and a test that merely errors because it
                        references new internals proves nothing either, so setup
                        errors do not satisfy this gate.
  4. DETERMINISTIC      the packet's sha256 is identical across runs AND across
                        different PYTHONHASHSEED values, so ranking cannot depend
                        on dict/set iteration order.
  5. HONEST VERDICT     reports exactly what was verified (commit + whether the
                        working tree was dirty), because "safe to tag" is a claim
                        about a commit, not about an uncommitted tree.

Gate 3 measures the tests; the others measure the product.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BENCH_JSON = Path("benchmarks") / "results_task.json"
TOKEN_CEILING_RATIO = 1.25      # packet may not balloon vs the base release


def run(cmd: list, cwd: Path, env: dict = None) -> subprocess.CompletedProcess:
    e = dict(os.environ)
    if env:
        e.update(env)
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, env=e)


def pytest_run(tree: Path, targets: list = None, env: dict = None) -> dict:
    """Run pytest and classify the outcome from JUnit XML rather than stdout.

    The distinction matters for gate 3: pytest emits `<failure>` for a test whose
    body failed and `<error>` for one that never ran (collection/fixture error). A
    test that merely errors on base because it references internals this release
    added is NOT evidence that the suite would catch the bug — only an assertion
    failure is. Scraping the terminal summary cannot tell these apart, because
    `--tb=line` omits the exception type from the FAILED lines.
    """
    xml = tree / f"_qa_junit_{'all' if not targets else 'subset'}.xml"
    if xml.exists():
        xml.unlink()
    cmd = [sys.executable, "-m", "pytest", "--tb=line", f"--junit-xml={xml}"]
    proc = run(cmd + (targets or []), tree, env=env)
    out = {"returncode": proc.returncode, "assertion": [], "other": [],
           "errors": [], "total": 0, "stdout": proc.stdout}
    if not xml.exists():
        out["errors"].append("<no junit xml produced>")
        return out
    import xml.etree.ElementTree as ET
    try:
        root = ET.parse(xml).getroot()
    except ET.ParseError as exc:
        out["errors"].append(f"<unparsable junit xml: {exc}>")
        return out
    for case in root.iter("testcase"):
        out["total"] += 1
        node = f"{case.get('classname', '')}::{case.get('name', '')}"
        for failure in case.findall("failure"):
            msg = failure.get("message") or ""
            (out["assertion"] if "AssertionError" in msg or "Failed:" in msg
             else out["other"]).append(node)
        for _ in case.findall("error"):
            out["errors"].append(node)
    xml.unlink()
    for k in ("assertion", "other", "errors"):
        out[k] = sorted(set(out[k]))
    return out


def bench(tree: Path, seed: str = "0", cache: Path = None) -> dict:
    """Run the form benchmark inside `tree`, refusing to read a stale result.

    `cache` must live OUTSIDE the tree: pointing DOCDEX_CACHE_DIR inside it would
    write megabytes of throwaway extraction cache into the repo being measured.
    """
    out = tree / BENCH_JSON
    if out.exists():
        out.unlink()                      # never silently re-read a previous run
    proc = run([sys.executable, str(Path("benchmarks") / "task_benchmark.py")], tree,
               env={"PYTHONHASHSEED": seed,
                    "DOCDEX_CACHE_DIR": str(cache)})
    if proc.returncode != 0:
        raise SystemExit(f"benchmark failed in {tree} (exit {proc.returncode})\n"
                         f"{proc.stdout[-1500:]}\n{proc.stderr[-1500:]}")
    if not out.exists():
        raise SystemExit(f"benchmark wrote no results in {tree}")
    return json.loads(out.read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="v0.5.0", help="git ref of the released base")
    ap.add_argument("--keep", action="store_true", help="keep the base worktree")
    args = ap.parse_args()

    work = Path(tempfile.mkdtemp(prefix="docdex-qa-"))
    base_tree = work / "base"
    failures: list = []
    notes: list = []

    head_sha = run(["git", "rev-parse", "--short", "HEAD"], REPO).stdout.strip()
    dirty = [ln for ln in run(["git", "status", "--porcelain"], REPO).stdout.splitlines()
             if ln.strip()]
    print(f"QA gate: working tree at {head_sha} vs {args.base}")
    print("=" * 68)

    # ---------------------------------------------------- gate 1: green on HEAD
    print("\n[1/5] full suite on HEAD")
    rep = pytest_run(REPO)
    summary = next((ln.strip() for ln in reversed(rep["stdout"].splitlines())
                    if " passed" in ln or " failed" in ln or " error" in ln), "")
    print(f"      {summary or '(no summary line)'}  [{rep['total']} cases collected]")
    if rep["returncode"] != 0:
        failures.append(f"pytest exited {rep['returncode']} on HEAD "
                        f"(failures={rep['assertion'] + rep['other']}, "
                        f"errors={rep['errors']})")
    elif rep["total"] == 0:
        failures.append("pytest collected no tests on HEAD — a green run over an "
                        "empty suite is not evidence of anything")
    else:
        print("      OK")

    # ------------------------------------------------ prepare the base worktree
    run(["git", "worktree", "add", "-q", "--detach", str(base_tree), args.base], REPO)
    if not (base_tree / "src").exists():
        raise SystemExit(f"could not check out {args.base} into {base_tree}")
    # The BENCHMARK HARNESS must be identical on both sides, or the oracle itself
    # differs and the comparison is meaningless (a HEAD that loosened `covered()`
    # would look like an improvement). Only src/ may differ.
    shutil.rmtree(base_tree / "benchmarks", ignore_errors=True)
    shutil.copytree(REPO / "benchmarks", base_tree / "benchmarks")

    # ------------------------------------------ gate 2: per-field, not headline
    print(f"\n[2/5] form benchmark vs {args.base} — per field")
    head_b = bench(REPO, cache=work / "cache_head")
    base_b = bench(base_tree, cache=work / "cache_base")
    key = "docdex context"
    hr, br = head_b["results"][key], base_b["results"][key]
    head_cov, base_cov = set(hr["covered"]), set(br["covered"])
    lost, gained = sorted(base_cov - head_cov), sorted(head_cov - base_cov)
    print(f"      values: base {len(base_cov)} -> HEAD {len(head_cov)} fields")
    for f in gained:
        print(f"      IMPROVED  value now found: {f}")
    for f in lost:
        print(f"      REGRESSED value lost:      {f}")
    if lost:
        failures.append(f"field values lost vs {args.base}: {lost}")

    # Attribution: a value can survive while its provenance silently degrades.
    hf, bf = hr.get("fields", {}), br.get("fields", {})
    if hf and bf:
        for f in sorted(set(bf) & set(hf) & base_cov & head_cov):
            b, h = bf[f], hf[f]
            if b["section"] == "answers" and h["section"] != "answers":
                failures.append(
                    f"{f}: demoted from 'answers' to '{h['section']}'")
                print(f"      REGRESSED {f}: answers -> {h['section']}")
            elif b["source"] and h["source"] and b["source"] != h["source"]:
                notes.append(f"{f}: cited source changed "
                             f"{b['source']!r} -> {h['source']!r} (verify it's right)")
    else:
        notes.append("per-field attribution unavailable on one side "
                     "(base predates it) — value-level comparison only")

    # Honesty: an absent field must never become an answer.
    lost_honesty = sorted(set(br.get("honest_absent", [])) - set(hr.get("honest_absent", [])))
    if lost_honesty:
        failures.append(f"no longer reported honestly absent: {lost_honesty}")
        print(f"      REGRESSED honesty lost for: {lost_honesty}")

    # Tokens: coverage must not be bought with unbounded context.
    if br["tokens"] and hr["tokens"] > br["tokens"] * TOKEN_CEILING_RATIO:
        failures.append(f"packet grew {hr['tokens'] / br['tokens']:.2f}x vs base "
                        f"(ceiling {TOKEN_CEILING_RATIO}x)")
    print(f"      tokens: {br['tokens']} -> {hr['tokens']} "
          f"({hr['tokens'] / br['tokens']:.2f}x)" if br["tokens"] else "")
    if not lost and not lost_honesty:
        print("      OK — nothing regressed")

    # ---------------------------------- gate 3: do the release's tests discriminate?
    print(f"\n[3/5] release's new tests must FAIL on {args.base}")
    diff = run(["git", "diff", "--name-only", args.base, "--", "tests/"], REPO)
    changed = [p for p in diff.stdout.split() if p.endswith(".py")]
    untracked = run(["git", "ls-files", "-o", "--exclude-standard", "tests/"], REPO)
    changed += [p for p in untracked.stdout.split() if p.endswith(".py")]
    # Only files that still exist on HEAD can be run; a test deleted by this
    # release is not evidence of anything.
    changed = sorted({p for p in changed if (REPO / p).exists()})
    if not changed:
        notes.append("no test files added or changed vs base — gate 3 skipped")
        print("      skipped (no test changes)")
    else:
        print(f"      candidates: {', '.join(changed)}")
        # Overlay the WHOLE tests/ tree so fixtures, conftest and data come too.
        shutil.rmtree(base_tree / "tests", ignore_errors=True)
        shutil.copytree(REPO / "tests", base_tree / "tests")
        rep = pytest_run(base_tree, changed,
                         env={"DOCDEX_CACHE_DIR": str(work / "cache_gate3")})
        for f in rep["assertion"]:
            print(f"      ASSERTION FAILS ON BASE  {f}")
        for f in rep["other"] + rep["errors"]:
            print(f"      (setup error on base, not counted)  {f}")
        if not rep["assertion"]:
            failures.append(
                "no new test fails on an ASSERTION against the base tree. Either "
                "the tests don't exercise the fixed behaviour, or they only error "
                "on missing internals — neither proves they would catch the bug.")
        else:
            print(f"      OK — {len(rep['assertion'])} assertion(s) catch base behaviour")

    # -------------------------------------------------- gate 4: determinism
    print("\n[4/5] determinism on HEAD")
    h1 = bench(REPO, seed="0", cache=work / "d1")["results"][key]["packet_sha256"]
    h2 = bench(REPO, seed="0", cache=work / "d2")["results"][key]["packet_sha256"]
    h3 = bench(REPO, seed="524287", cache=work / "d3")["results"][key]["packet_sha256"]
    if h1 != h2:
        failures.append("packet differs between identical runs")
    elif h1 != h3:
        failures.append("packet depends on PYTHONHASHSEED — ranking is using "
                        "dict/set iteration order somewhere")
    else:
        print(f"      OK — packet sha256 stable across runs and hash seeds "
              f"({h1[:12]}…)")

    if not args.keep:
        run(["git", "worktree", "remove", "--force", str(base_tree)], REPO)

    # ------------------------------------------------- gate 5: honest verdict
    print("\n[5/5] what was verified")
    print(f"      commit:      {head_sha}")
    print(f"      working tree: {'DIRTY — ' + str(len(dirty)) + ' uncommitted path(s)' if dirty else 'clean'}")
    if dirty:
        notes.append("the gate verified the WORKING TREE, not a commit. Commit "
                     "first and re-run before tagging, or the tag ships unverified code.")

    print("\n" + "=" * 68)
    for n in notes:
        print(f"note: {n}")
    if failures:
        print("QA GATE FAILED")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"QA GATE PASSED for {head_sha}"
          + (" (working tree, uncommitted — see note)" if dirty else " (clean tree)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
