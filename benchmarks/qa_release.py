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
import re
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
    # Scrub pytest's environment channels. Found by adversarial review: `run()` copies
    # os.environ, so an inherited PYTEST_ADDOPTS="-k one_test" would leave gate 1 with
    # a single passing test — which satisfies "passed > 0" — and gate 3 with the one
    # assertion it needs, while the rest of the suite never ran. The gate must decide
    # what it runs; nothing in the operator's shell gets a vote.
    scrubbed = {"PYTEST_ADDOPTS": "", "PYTEST_PLUGINS": "",
                "PYTEST_DISABLE_PLUGIN_AUTOLOAD": ""}
    scrubbed.update(env or {})
    proc = run(cmd + (targets or []), tree, env=scrubbed)
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
    # Deselection is invisible in the JUnit XML — a deselected test simply is not
    # there, so `total` looks like the whole suite. Any source of it (an inherited
    # flag the scrub missed, a `-k` in a config file, a conftest hook) is reported.
    if " deselected" in proc.stdout:
        line = next((ln for ln in proc.stdout.splitlines() if " deselected" in ln), "")
        out["errors"].append(f"<tests were deselected: {line.strip()}>")
    return out


def tree_hashes(dirty_lines: list) -> dict:
    """{path: hash of its contents} for everything not committed.

    Gate 5 compares this before and after the run. Porcelain status alone cannot see a
    dirty file edited twice — the status line is identical either way, while the suite
    and the benchmarks read different source. Only uncommitted paths need hashing: what
    is committed is pinned by the SHA the verdict already names.

    Per path rather than one digest over the whole set, so a difference can be NAMED.
    A gate that reports "something changed" and cannot say what leaves the reader to
    guess whether it mattered, which is how a real signal gets waved through.
    """
    out = {}
    for line in sorted(dirty_lines):
        path = line[3:].strip().strip('"')
        # A rename reads as `old -> new`; the destination is what exists now.
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        h = hashlib.sha256()
        target = REPO / path
        try:
            if target.is_file():
                h.update(hashlib.sha256(target.read_bytes()).digest())
            elif target.is_dir():
                # An untracked DIRECTORY is reported as one entry; walk it.
                for child in sorted(p for p in target.rglob("*") if p.is_file()):
                    h.update(str(child.relative_to(REPO)).encode("utf-8", "replace"))
                    h.update(hashlib.sha256(child.read_bytes()).digest())
            else:
                h.update(b"<absent>")
        except OSError as exc:
            h.update(f"<unreadable: {exc}>".encode("utf-8", "replace"))
        out[path] = h.hexdigest()
    return out


# Paths the gate itself rewrites while it runs: gate 2 re-runs both benchmark suites,
# and those harnesses write their results. Listing them explicitly keeps the content
# check meaningful for everything else instead of switching it off wholesale.
GATE_WRITES = (
    "benchmarks/results.json", "benchmarks/RESULTS.md",
    "benchmarks/results_task.json", "benchmarks/RESULTS_TASK.md",
)


def highest_release_tag() -> str:
    """The highest `vX.Y.Z` tag by VERSION order, across every tag in the repo."""
    out = run(["git", "tag", "--list", "v*"], REPO).stdout.split()
    best, best_key = "", None
    for tag in out:
        try:
            key = tuple(int(p) for p in tag.lstrip("v").split(".")[:3])
        except ValueError:
            continue
        if best_key is None or key > best_key:
            best, best_key = tag, key
    return best


def _changelog_has_section(version: str) -> bool:
    """A real `## [x.y.z]` heading, not the string appearing anywhere in the file."""
    try:
        text = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    except OSError:
        return False
    fenced = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue                      # a heading quoted in an example is not one
        if re.match(rf"^##\s+\[{re.escape(version)}\]", line.strip()):
            return True
    return False


def _roadmap_names(version: str) -> bool:
    """`vX.Y.Z` on a heading or a list item, not buried in unrelated prose."""
    try:
        text = (REPO / "ROADMAP.md").read_text(encoding="utf-8")
    except OSError:
        return False
    needle = f"v{version}"
    for line in text.splitlines():
        s = line.strip()
        if (s.startswith("#") or s.startswith("-") or s.startswith("*")
                or s.startswith("|")) and needle in s:
            return True
    return False


def _history_has_record(version: str) -> bool:
    """A parseable HISTORY record for this version carrying real measurements.

    A substring check passed on `"v0.5.6"` sitting in malformed or unrelated text, so
    the gate could certify a release whose benchmark row did not exist.
    """
    try:
        data = json.loads((REPO / "benchmarks" / "HISTORY.json").read_text(
            encoding="utf-8"))
    except (OSError, ValueError):
        return False
    for rec in data.get("records", []):
        if rec.get("version") != version:
            continue
        suites = rec.get("suites") or rec
        return bool(suites) and len(json.dumps(suites)) > 80
    return False


# Files that grade the release. Gate 2 imports these from HEAD and overlays them onto
# the base tree, so they must not change in the same release they are grading.
HARNESS_FILES = ("benchmarks/task_benchmark.py", "benchmarks/run_benchmark.py",
                 "benchmarks/corpus_gen.py")


def _claimed_fixes(version: str):
    """[(finding, test node)] from the release's FIXED_BY.tsv, or None if absent.

    Two tab-separated columns; `#` comments and blank lines ignored. A row whose test
    column is `-` declares a fix that deliberately has no discriminating test, and
    must carry a reason in a third column — which the gate prints but cannot judge.
    """
    path = QA_ARCHIVE / f"v{version}" / "FIXED_BY.tsv"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    rows = []
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = [c.strip() for c in line.split("\t") if c.strip()]
        if len(parts) >= 2 and parts[1] != "-":
            rows.append((parts[0], parts[1]))
    return rows


def _harness_unchanged(base: str) -> bool:
    changed = run(["git", "diff", "--name-only", base, "--"] + list(HARNESS_FILES),
                  REPO).stdout.split()
    return not changed


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
    # "unconfirmed" and "not confirmed" both contain "confirmed", so counting the
    # substring accepted a file saying every finding remained unadjudicated as though it
    # carried a verdict for each. Found by adversarial review. Word-boundary matched,
    # and the negations are removed first so they cannot masquerade as a verdict.
    for negation in ("unconfirmed", "not confirmed", "unadjudicated"):
        lowered = lowered.replace(negation, "")
    verdicts = sum(len(re.findall(rf"\b{re.escape(w)}\b", lowered)) for w in
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
        # Highest release tag by version order, NOT `git describe`'s nearest reachable
        # tag: on a branch cut before the latest release those differ, and comparing
        # against the older one lets a fixed wrong answer come back unnoticed.
        args.base = highest_release_tag()
        if not args.base:
            print("qa_release: no tags found; pass --base explicitly", file=sys.stderr)
            return 2
        print(f"(base not given; using the highest release tag: {args.base})")

    work = Path(tempfile.mkdtemp(prefix="docdex-qa-"))
    base_tree = work / "base"
    failures: list = []
    notes: list = []

    head_sha = run(["git", "rev-parse", "--short", "HEAD"], REPO).stdout.strip()
    dirty = [ln for ln in run(["git", "status", "--porcelain"], REPO).stdout.splitlines()
             if ln.strip()]
    started_hashes = tree_hashes(dirty)
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
    newest_tag = highest_release_tag()
    checks = [
        (f"version goes up ({base_v} -> {version})",
         bool(hv) and bool(bv) and hv > bv),
        # `git describe` finds the nearest tag REACHABLE from HEAD, so on a branch cut
        # before the latest release it happily nominates an older one and then agrees
        # with itself that it is the newest. Compared by version order across all
        # tags instead. Found by adversarial review.
        (f"base {args.base} is the highest release tag ({newest_tag or 'none'})",
         (not newest_tag) or args.base == newest_tag),
        # Parsed as a HEADING, not as a substring: `## [0.5.6]` quoted inside an older
        # entry's code block would satisfy `in`. Same for the others below.
        (f"CHANGELOG has a [{version}] section", _changelog_has_section(version)),
        (f"ROADMAP names v{version} in a heading or bullet",
         _roadmap_names(version)),
        # Existence alone was satisfied by an empty file, so the gate could claim
        # evidence that did not exist. Found by adversarial review.
        ("QA archive holds a real adjudication for this version",
         _adjudication_ok(QA_ARCHIVE / f"v{version}" / "ADJUDICATION.md", version)),
        (f"benchmarks/HISTORY.json holds a valid record for v{version}",
         _history_has_record(version)),
        # The benchmark harness is the oracle for BOTH sides of gate 2: `sweep`
        # overlays today's harness onto the base tree, so a release that edits the
        # harness moves the exam and the answer together and both sides look perfect.
        # Changing it is sometimes right — it just cannot pass unnoticed.
        ("benchmark harness unchanged since the base (it grades both sides)",
         _harness_unchanged(args.base)),
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

    # How many cases HEAD and the BASE release collect. A conftest hook can drop almost
    # the whole suite without printing "deselected" — pytest then exits 0 over one
    # passing smoke test and gate 1 says OK. A release may legitimately remove tests,
    # but it cannot quietly lose most of them. Compared after gate 3 builds the base
    # tree. Found by adversarial review.
    rep_head_total = rep["total"]
    base_total = None

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
    # "per case" was only ever true of suite B, which is compared field by field.
    # Suite A is compared per method summary, so a lost fact offset by a newly found
    # one leaves recall unchanged and passes. Naming it accurately here because the
    # label was doing the arguing; the per-query fix is tracked in ROADMAP QA debt.
    print(f"\n[2/6] benchmarks vs {args.base} — suite A per method, suite B per field")
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
    # Absolute, not only relative. Found by adversarial review: comparing HEAD against
    # base means a field that BOTH sides answer wrongly produces no regression, so a
    # standing "confidently wrong" answer passes the gate indefinitely for the sole
    # reason that it is not new. Every field the benchmark plants as absent must be
    # reported absent on HEAD, whatever base did.
    import task_benchmark          # same sys.path insert as bench_all above
    unhonest = sorted(set(task_benchmark.ABSENT) - set(hr.get("honest_absent", [])))
    if unhonest:
        failures.append(f"fields absent from the corpus are not reported absent on "
                        f"HEAD: {unhonest} (a standing wrong answer is still wrong)")
        print(f"      NOT HONESTLY ABSENT on HEAD: {unhonest}")

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
        src_changed = sorted(
            p for p in run(["git", "diff", "--name-only", f"{args.base}..HEAD"],
                           REPO).stdout.split()
            if p.startswith("src/") and p.endswith(".py"))
        if args.no_new_tests and src_changed:
            # Any non-empty string used to waive the only machine check that requires
            # release-specific tests — including for a release that rewrote retrieval.
            # Found by adversarial review. The waiver survives for docs/tooling-only
            # releases, which is what it was actually for.
            failures.append(
                f"--no-new-tests cannot waive gate 3 when product code changed: "
                f"{src_changed}. The waiver is for releases that touch no `src/` "
                f"file; this one does, so it needs a test that discriminates.")
            print(f"      WAIVER REFUSED — product code changed: {src_changed}")
        elif args.no_new_tests:
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
        # The base suite's own size, measured with this release's tests in place, so
        # gate 1 can tell "we removed a test" from "a hook swallowed the suite".
        base_all = pytest_run(base_tree,
                              env={"DOCDEX_CACHE_DIR": str(work / "cache_basefull")})
        base_total = base_all["total"]
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

        # ONE assertion failure is not evidence for TEN claimed fixes. A release that
        # fixes many things declares which test proves each, and every one of those
        # tests must fail on the base tree. Without this, nine untested fixes ride
        # along behind a single valid test. Found by adversarial review.
        mapping = _claimed_fixes(version)
        if mapping is None:
            failures.append(
                f"{QA_ARCHIVE.name}/v{version}/FIXED_BY.tsv is missing. List each "
                f"finding this release fixes and the test that proves it, so the gate "
                f"can check EVERY claimed fix is covered — not just that one is.")
            print("      MISS — no finding-to-test map declared")
        else:
            caught = set(rep["assertion"])
            unproven = [(f, t) for f, t in mapping
                        if not any(a.endswith(t) or t in a for a in caught)]
            for finding, test in mapping:
                mark = "OK  " if (finding, test) not in unproven else "MISS"
                print(f"      {mark} {finding} <- {test}")
            if unproven:
                failures.append(
                    "these claimed fixes have no test that fails on the base tree, so "
                    "nothing shows they were ever broken: "
                    + "; ".join(f"{f} ({t})" for f, t in unproven))

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

    # Deferred from gate 1: the base tree only exists once gate 3 has built it, and
    # this comparison needs both sizes. A conftest hook that swallows the suite exits
    # 0 with no "deselected" line, so the only reliable signal is the case COUNT
    # against the previous release's. Found by adversarial review.
    if base_total:
        if rep_head_total < base_total * 0.9:
            failures.append(
                f"HEAD collected {rep_head_total} test cases against the base's "
                f"{base_total} — over a tenth of the suite disappeared without "
                f"failing anything. A hook or a collection error can do this while "
                f"pytest still exits 0.")
        else:
            print(f"\n      suite size: {rep_head_total} cases on HEAD vs "
                  f"{base_total} on {args.base}")

    # ------------------------------------------------- gate 5: honest verdict
    print("\n[5/6] what was verified")
    print(f"      commit:      {head_sha}")
    print(f"      working tree: {'DIRTY — ' + str(len(dirty)) + ' uncommitted path(s)' if dirty else 'clean'}")

    # The gate runs the benchmarks inside this tree, and they write their results
    # here — measurement noise that differs every run. Restore exactly the files the
    # gate itself dirtied, and only those that were clean when it started, so a
    # pre-commit run never discards the operator's own edits. Without this the check
    # below fires on the gate's own output; it caught precisely that on first run.
    # What must never be clobbered is an edit the operator had NOT staged, because
    # `git checkout --` would destroy it. A merely STAGED bench output is safe to
    # restore: checkout reads from the index, so the staged content is exactly what
    # comes back. Keying on "dirty at all" instead refused to restore anything staged
    # and then failed the run below on the gate's own benchmark output — `M ` became
    # `MM` on the same path, so it reported "14 -> 14 path(s) changed", which reads as
    # a broken gate. Porcelain column 2 is the worktree half of the status.
    unstaged_at_start = {ln[3:].strip() for ln in dirty if len(ln) > 1 and ln[1] != " "}
    restorable = [p for p in BENCH_OUTPUTS
                  if p not in unstaged_at_start and (REPO / p).exists()]
    if restorable:
        run(["git", "checkout", "--"] + restorable, REPO)

    # Re-read the status AFTER every gate: a gate that wrote into the repo, or an edit
    # made while it ran, would otherwise go unnoticed.
    dirty_now = [ln for ln in run(["git", "status", "--porcelain"], REPO).stdout.splitlines()
                 if ln.strip()]
    if dirty_now != dirty:
        # Name the paths. A count-only message ("14 -> 14") cannot describe a status
        # change on an unchanged set of paths, which is the common case.
        moved = sorted(set(dirty_now).symmetric_difference(dirty))
        failures.append(f"the working tree changed while the gate ran "
                        f"({len(dirty)} -> {len(dirty_now)} path(s)); nothing it "
                        f"reported describes a single fixed tree. Differing status "
                        f"line(s): {'; '.join(m.strip() for m in moved[:6])}")
    else:
        # Status lines alone cannot see a dirty file edited AGAIN while the gate ran:
        # `M  src/docdex/index_db.py` before and after, different contents in between,
        # so the tests and the benchmarks measured different source. Found by
        # adversarial review. Content is hashed, not just the status.
        ended_hashes = tree_hashes(dirty)
        churned = sorted(p for p in set(started_hashes) | set(ended_hashes)
                         if started_hashes.get(p) != ended_hashes.get(p))
        mine = [p for p in churned if p in GATE_WRITES]
        theirs = [p for p in churned if p not in GATE_WRITES]
        if mine:
            notes.append(f"gate 2 rewrote its own benchmark output while running: "
                         f"{', '.join(mine)} (expected)")
        if theirs:
            failures.append(
                f"these files' CONTENTS changed while the gate ran, without changing "
                f"their git status, so the suite and the benchmarks did not "
                f"necessarily measure the same source: {', '.join(theirs)}. Re-run on "
                f"a settled tree.")
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
