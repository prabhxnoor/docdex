"""Release QA gate — proves a release fixes what it claims and breaks nothing.

Run before tagging:  python3 benchmarks/qa_release.py            (base = most recent tag)

Six gates, all of which must pass. Gate 3 is the unusual one and the reason this
script exists.

  0. RELEASE STANDARDS  the parts of docs/RELEASING.md a machine can check: version
                        bumped, a CHANGELOG section and ROADMAP mention for this
                        version, a QA archive folder with an adjudication, and this
                        release recorded in the benchmark history. A stale document
                        is a defect, so the gate refuses to let one through.
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
import atexit
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
QA_ARCHIVE = REPO.parent / "docdex-qa"
BENCH_JSON = Path("benchmarks") / "results_task.json"
# Checked-in benchmark output the gate necessarily rewrites by running the benchmarks
# in this tree. Restored before the gate reports, so it leaves the tree as it found it.
BENCH_OUTPUTS = ["benchmarks/RESULTS.md", "benchmarks/results.json",
                 "benchmarks/RESULTS_TASK.md", "benchmarks/results_task.json"]
TOKEN_CEILING_RATIO = 1.25      # packet may not balloon vs the base release


def run(cmd: list, cwd: Path, env: dict = None) -> subprocess.CompletedProcess:
    e = dict(os.environ)
    if env:
        e.update(env)
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, env=e)


def version_of(tree: Path) -> str:
    for ln in (tree / "src" / "docdex" / "__init__.py").read_text(
            encoding="utf-8").splitlines():
        if ln.startswith("__version__"):
            return ln.split("=", 1)[1].strip().strip('"\'')
    return "?"


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
           "errors": [], "total": 0, "passed": 0, "skipped": 0,
           "stdout": proc.stdout}
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
        fails = case.findall("failure")
        errs = case.findall("error")
        skips = case.findall("skipped")
        for failure in fails:
            # Classify on the exception type at the START of the message, not on a
            # substring found anywhere in it. Found by adversarial review: a base test
            # raising RuntimeError("AssertionError while loading cache") contains
            # "AssertionError" in its message, so a substring test would count an
            # unrelated runtime incompatibility as proof that a regression test
            # catches old behaviour. Review proposed reading the XML `type`
            # attribute instead — measured, and pytest never populates it (always
            # None for failure elements), so the message prefix is the only signal
            # this format actually carries.
            msg = (failure.get("message") or "").lstrip()
            kind = (failure.get("type")
                    or msg.split(":", 1)[0]).rsplit(".", 1)[-1].strip()
            (out["assertion"] if kind in ("AssertionError", "Failed")
             else out["other"]).append(node)
        for _ in errs:
            out["errors"].append(node)
        if skips:
            out["skipped"] += 1
        elif not fails and not errs:
            out["passed"] += 1
    xml.unlink()
    for k in ("assertion", "other", "errors"):
        out[k] = sorted(set(out[k]))
    return out


def _adjudication_ok(path: Path, version: str) -> bool:
    """Is there a real adjudication here, or just a file with the right name?

    Deliberately shallow — it cannot judge whether the reasoning is any good. It only
    refuses the cases that are obviously not an adjudication: missing, near-empty, or
    not even naming the release and a verdict for each finding.
    """
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    if len(text.strip()) < 400:
        return False
    lowered = text.lower()
    verdicts = sum(lowered.count(w) for w in
                   ("confirmed", "refuted", "not reproduced", "answered", "deferred"))
    return version in text and verdicts >= 1


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
    # No default base. Found by adversarial review: with a fixed old default, a
    # release that regressed a field introduced two releases ago compared cleanly
    # against a base that never had it, and the gate called the regression safe.
    ap.add_argument("--base", help="git ref of the released base; defaults to the "
                                   "most recent tag, which is what must be compared")
    ap.add_argument("--keep", action="store_true", help="keep the base worktree")
    ap.add_argument("--allow-dirty", action="store_true",
                    help="permit a pass on an uncommitted tree (pre-commit runs)")
    ap.add_argument("--no-new-tests", metavar="REASON",
                    help="waiver: this release adds no tests, for this stated reason")
    args = ap.parse_args()
    if not args.base:
        args.base = run(["git", "describe", "--tags", "--abbrev=0"],
                        REPO).stdout.strip()
        if not args.base:
            print("qa_release: no tags found; pass --base explicitly", file=sys.stderr)
            return 2
        print(f"(base not given; using the most recent tag: {args.base})")

    work = Path(tempfile.mkdtemp(prefix="docdex-qa-"))
    base_tree = work / "base"
    failures: list = []
    notes: list = []

    head_sha = run(["git", "rev-parse", "--short", "HEAD"], REPO).stdout.strip()
    dirty = [ln for ln in run(["git", "status", "--porcelain"], REPO).stdout.splitlines()
             if ln.strip()]
    print(f"QA gate: working tree at {head_sha} vs {args.base}")
    print("=" * 68)

    # ------------------------------------------------------- gate 0: preflight
    # The release standards a machine can check. See docs/RELEASING.md.
    print("\n[0/6] release standards")
    version = version_of(REPO)
    base_version = run(["git", "show", f"{args.base}:src/docdex/__init__.py"],
                       REPO).stdout
    base_v = next((ln.split("=", 1)[1].strip().strip('"\'')
                   for ln in base_version.splitlines()
                   if ln.startswith("__version__")), "?")
    def semver(v):
        try:
            return tuple(int(p) for p in v.split(".")[:3])
        except ValueError:
            return None

    hv, bv = semver(version), semver(base_v)
    # Found by adversarial review: `version != base_v` also accepts a DOWNGRADE, so a
    # tree carrying older behaviour and known bugs could be released as long as the
    # documents agreed with it.
    newest_tag = run(["git", "describe", "--tags", "--abbrev=0"], REPO).stdout.strip()
    checks = [
        (f"version goes up ({base_v} -> {version})",
         bool(hv) and bool(bv) and hv > bv),
        (f"base {args.base} is the most recent tag ({newest_tag or 'none'})",
         (not newest_tag) or args.base == newest_tag),
        (f"CHANGELOG has a [{version}] section",
         f"## [{version}]" in (REPO / "CHANGELOG.md").read_text(encoding="utf-8")),
        (f"ROADMAP mentions v{version}",
         f"v{version}" in (REPO / "ROADMAP.md").read_text(encoding="utf-8")),
        # Existence alone was satisfied by an empty file, so the gate could claim
        # evidence that did not exist. Found by adversarial review.
        ("QA archive holds a real adjudication for this version",
         _adjudication_ok(QA_ARCHIVE / f"v{version}" / "ADJUDICATION.md", version)),
        (f"this release is recorded in benchmarks/HISTORY.json",
         f'"v{version}"' in (REPO / "benchmarks" / "HISTORY.json").read_text(
             encoding="utf-8")),
    ]
    for label, ok in checks:
        print(f"      {'OK  ' if ok else 'MISS'} {label}")
        if not ok:
            failures.append(f"release standard not met: {label}")

    # ---------------------------------------------------- gate 1: green on HEAD
    print("\n[1/6] full suite on HEAD")
    rep = pytest_run(REPO)
    summary = next((ln.strip() for ln in reversed(rep["stdout"].splitlines())
                    if " passed" in ln or " failed" in ln or " error" in ln), "")
    print(f"      {summary or '(no summary line)'}  [{rep['total']} cases collected, "
          f"{rep['passed']} passed, {rep['skipped']} skipped]")
    if rep["returncode"] != 0:
        failures.append(f"pytest exited {rep['returncode']} on HEAD "
                        f"(failures={rep['assertion'] + rep['other']}, "
                        f"errors={rep['errors']})")
    elif rep["passed"] == 0:
        # Found by adversarial review: if an environment condition skipped every
        # test, pytest still exits 0 with a full testcase count, and this gate
        # printed OK over a run that executed no assertion at all.
        failures.append(
            f"no test actually ran on HEAD — {rep['skipped']} skipped, 0 passed. "
            f"A green run that asserted nothing is not evidence of anything.")
    elif rep["total"] == 0:
        failures.append("pytest collected no tests on HEAD — a green run over an "
                        "empty suite is not evidence of anything")
    else:
        print("      OK")

    # ------------------------------------------------ prepare the base worktree
    run(["git", "worktree", "add", "-q", "--detach", str(base_tree), args.base], REPO)

    # Registered rather than called at the end: any benchmark or gate that raised
    # used to leave a registered detached worktree and a temp cache behind, which
    # then confused the NEXT run. Found by adversarial review.
    def cleanup():
        if args.keep:
            print(f"(kept base worktree at {base_tree})")
            return
        run(["git", "worktree", "remove", "--force", str(base_tree)], REPO)
        shutil.rmtree(work, ignore_errors=True)

    atexit.register(cleanup)
    if not (base_tree / "src").exists():
        raise SystemExit(f"could not check out {args.base} into {base_tree}")
    # The BENCHMARK HARNESS must be identical on both sides, or the oracle itself
    # differs and the comparison is meaningless (a HEAD that loosened `covered()`
    # would look like an improvement). Only src/ may differ.
    shutil.rmtree(base_tree / "benchmarks", ignore_errors=True)
    shutil.copytree(REPO / "benchmarks", base_tree / "benchmarks")

    # ------------------------------------------ gate 2: per-field, not headline
    print(f"\n[2/6] benchmarks vs {args.base} — every suite, per case")
    # Suite A (single-fact retrieval) is compared per method here too. It was
    # historically only recorded at v0.1.1, which is how a five-release blind spot
    # opened up; `bench_all.py` owns the measurement so both tools agree.
    sys.path.insert(0, str(REPO / "benchmarks"))
    import bench_all
    head_rec = bench_all.measure(REPO, "HEAD", head_sha, "head")
    base_rec = bench_all.measure(base_tree, args.base, "", "base")
    ha, ba = head_rec["suite_a"], base_rec["suite_a"]
    if "summary" in ha and "summary" in ba:
        a_lost = []
        for m in bench_all.A_METHODS:
            # A method present on base but gone from HEAD means a whole retrieval
            # path disappeared. Skipping it silently — which is what comparing only
            # the intersection did — is how that ships unnoticed. Found by review.
            if m in ba["summary"] and m not in ha["summary"]:
                a_lost.append(f"{bench_all.A_LABEL.get(m, m)} MISSING on HEAD")
                continue
            if m in ha["summary"] and m in ba["summary"]:
                for k in bench_all.A_HIGHER_BETTER:
                    p, c = ba["summary"][m].get(k), ha["summary"][m].get(k)
                    if p is not None and c is None:
                        a_lost.append(f"{bench_all.A_LABEL.get(m, m)} {k} no longer "
                                      f"measured (was {p})")
                    elif p is not None and c is not None and c < p:
                        a_lost.append(f"{bench_all.A_LABEL.get(m, m)} {k} {p}->{c}")
        if a_lost:
            failures.append(f"suite A regressed vs {args.base}: {a_lost}")
            for x in a_lost:
                print(f"      REGRESSED suite A {x}")
        else:
            print("      suite A: no method regressed")
    else:
        # Was a note, so a HEAD that crashed suite A entirely still passed the gate.
        failures.append(
            f"suite A did not produce a comparable summary on both sides — "
            f"head={ha.get('error', 'ok')} base={ba.get('error', 'ok')}. A retrieval "
            f"path that cannot be measured cannot be certified.")

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
    print(f"\n[3/6] release's new tests must FAIL on {args.base}")
    diff = run(["git", "diff", "--name-only", args.base, "--", "tests/"], REPO)
    changed = [p for p in diff.stdout.split() if p.endswith(".py")]
    untracked = run(["git", "ls-files", "-o", "--exclude-standard", "tests/"], REPO)
    changed += [p for p in untracked.stdout.split() if p.endswith(".py")]
    # Only files that still exist on HEAD can be run; a test deleted by this
    # release is not evidence of anything.
    changed = sorted({p for p in changed if (REPO / p).exists()})
    if not changed:
        # Found by adversarial review: this used to be a note, so a release that
        # changed behaviour and added no test at all sailed through the gate whose
        # entire purpose is to require one. A waiver must be stated out loud.
        if args.no_new_tests:
            notes.append(f"gate 3 WAIVED by --no-new-tests: {args.no_new_tests}")
            print(f"      WAIVED — {args.no_new_tests}")
        else:
            failures.append(
                "no test files added or changed vs base. Every release adds or "
                "changes at least one test (see docs/RELEASING.md); if this one "
                "genuinely should not, pass --no-new-tests \"<reason>\".")
            print("      MISS — no test changes, and no waiver given")
    else:
        print(f"      candidates: {', '.join(changed)}")
        # Overlay the WHOLE tests/ tree so fixtures, conftest and data come too.
        shutil.rmtree(base_tree / "tests", ignore_errors=True)
        shutil.copytree(REPO / "tests", base_tree / "tests")
        rep = pytest_run(base_tree, changed,
                         env={"DOCDEX_CACHE_DIR": str(work / "cache_gate3")})
        for f in rep["assertion"]:
            print(f"      ASSERTION FAILS ON BASE  {f}")
        # Two different things, and calling both "setup error" was misleading in a
        # gate whose entire job is to classify evidence honestly. A body exception
        # (the crash the release fixes) is real evidence of old behaviour — it is just
        # not an *assertion*, so it cannot satisfy this gate on its own.
        for f in rep["other"]:
            print(f"      raised on base (real, but not an assertion)  {f}")
        for f in rep["errors"]:
            print(f"      never ran on base — setup/collection error  {f}")
        if not rep["assertion"]:
            failures.append(
                "no new test fails on an ASSERTION against the base tree. Either "
                "the tests don't exercise the fixed behaviour, or they only error "
                "on missing internals — neither proves they would catch the bug.")
        else:
            print(f"      OK — {len(rep['assertion'])} assertion(s) catch base behaviour")

    # -------------------------------------------------- gate 4: determinism
    print("\n[4/6] determinism on HEAD")
    def packet_hash(seed, cache):
        """Hash the packet HERE rather than trusting the digest the harness reports.

        Found by adversarial review: a harness returning a constant `packet_sha256`
        while the bytes varied would have made this gate certify non-determinism, and
        the gate would never have looked at the output it was certifying.
        """
        rec = bench(REPO, seed=seed, cache=cache)["results"][key]
        packet = rec.get("packet_canonical")
        if packet is None:
            failures.append("the benchmark reported no packet to hash, so "
                            "determinism could only be taken on trust")
            return rec["packet_sha256"]
        mine = hashlib.sha256(packet.encode("utf-8")).hexdigest()
        if mine != rec["packet_sha256"]:
            failures.append(
                f"the benchmark's reported packet hash {rec['packet_sha256'][:12]}… "
                f"does not match the packet it returned ({mine[:12]}…) — the "
                f"determinism oracle cannot be trusted")
        return mine

    h1 = packet_hash("0", work / "d1")
    h2 = packet_hash("0", work / "d2")
    h3 = packet_hash("524287", work / "d3")
    if h1 != h2:
        failures.append("packet differs between identical runs")
    elif h1 != h3:
        failures.append("packet depends on PYTHONHASHSEED — ranking is using "
                        "dict/set iteration order somewhere")
    else:
        print(f"      OK — packet sha256 stable across runs and hash seeds "
              f"({h1[:12]}…)")

    # ------------------------------------------------- gate 5: honest verdict
    print("\n[5/6] what was verified")
    print(f"      commit:      {head_sha}")
    print(f"      working tree: {'DIRTY — ' + str(len(dirty)) + ' uncommitted path(s)' if dirty else 'clean'}")

    # The gate runs the benchmarks inside this tree, and they write their results
    # here — measurement noise that differs every run. Restore exactly the files the
    # gate itself dirtied, and only those that were clean when it started, so a
    # pre-commit run never discards the operator's own edits. Without this the check
    # below fires on the gate's own output; it caught precisely that on first run.
    was_dirty = {ln[3:].strip() for ln in dirty}
    restorable = [p for p in BENCH_OUTPUTS
                  if p not in was_dirty and (REPO / p).exists()]
    if restorable:
        run(["git", "checkout", "--"] + restorable, REPO)

    # Re-read the status AFTER every gate: a gate that wrote into the repo, or an edit
    # made while it ran, would otherwise go unnoticed.
    dirty_now = [ln for ln in run(["git", "status", "--porcelain"], REPO).stdout.splitlines()
                 if ln.strip()]
    if dirty_now != dirty:
        failures.append(f"the working tree changed while the gate ran "
                        f"({len(dirty)} -> {len(dirty_now)} path(s)); nothing it "
                        f"reported describes a single fixed tree")
    if dirty_now:
        # Found by adversarial review: a pass on a dirty tree named HEAD's SHA, so
        # tagging that SHA could ship the committed bug while the fix sat uncommitted.
        # Pre-commit runs are still possible — they just have to say so.
        if args.allow_dirty:
            notes.append(f"--allow-dirty: the gate verified the WORKING TREE, not "
                         f"{head_sha}. Commit and re-run before tagging.")
        else:
            failures.append(
                f"working tree has {len(dirty_now)} uncommitted path(s), so this "
                f"gate did NOT verify {head_sha} — tagging it would ship code "
                f"nothing checked. Commit and re-run, or pass --allow-dirty for a "
                f"pre-commit run.")

    print("\n" + "=" * 68)
    for n in notes:
        print(f"note: {n}")
    if failures:
        print("QA GATE FAILED")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"QA GATE PASSED for {head_sha}"
          + (" (WORKING TREE, uncommitted — not this commit)" if dirty_now
             else " (clean tree)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
