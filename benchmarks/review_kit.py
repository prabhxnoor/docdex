"""The standard two-pass external review for a docdex release.

    python3 benchmarks/review_kit.py --version 0.5.3 --base v0.5.2

Builds both review prompts from the diff, runs pass 1, scaffolds the archive folder,
and prints the pass-2 command. See `docs/RELEASING.md` step 3 for why it is shaped
this way; the short version:

  pass 1  agy / gemini-3.6-flash-high  ->  the DIFF   (separate quota, run automatically)
  pass 2  codex / gpt-5.6-sol @ xhigh  ->  the TESTS  (limited quota, run deliberately)

A Claude subagent reviewing Claude-written code shares its blind spots, so the
reviewer is always a different model family. Pass 2 is the higher-yield one: asked
"would these tests catch a variant of this bug?", it finds assertions that prove
nothing far more reliably than a code reviewer finds bugs.

Both prompts carry an explicit "answer from this text only, you have no repo access
and need none" preamble. Without it, headless `agy` tries to run a tool, gets
auto-denied a permission it cannot prompt for, and returns nothing at all.

Findings are then **adjudicated, never accepted** — reproduce or disprove each one
against the code before changing anything. Across v0.5.1 and v0.5.2, five findings
were refuted, one of them a CRITICAL that would have meant "fixing" correct code.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
QA_ARCHIVE = REPO.parent / "docdex-qa"
AGY_MODEL = "gemini-3.6-flash-high"

NO_TOOLS = (
    "IMPORTANT: Answer from the text in this message ONLY. Do NOT run any commands, "
    "do NOT read any files, do NOT use any tools — you have no repo access and none "
    "is needed. The complete diff is inline below. Reason about it as text and reply "
    "with your findings directly.\n\n")

RULES = """## The product and its hard rules

`docdex` is a deterministic retrieval layer that hands an LLM agent a cited "context
packet" assembled from a folder of documents. Its north star is **"never confidently
wrong"**: never present a wrong value as found, never hide a conflict, never report
present evidence as missing. Hard constraints:

- **Deterministic.** Same corpus + same query => byte-identical output. No randomness,
  no wall-clock or hash-order dependence in ranking.
- The core never calls an LLM. Pure Python + SQLite FTS5. macOS/Linux.
- Exact IDs, amounts and dates stay literal — never altered or normalised into a
  different value.
- Rebuildable state stays lean; nothing docdex writes should outlive its usefulness.
"""

OUTPUT_FORMAT = """For each finding output EXACTLY:

SEVERITY: CRITICAL | MAJOR | MINOR
WHERE: file:line or function
FAILURE: the concrete input/state -> the wrong result
WHY IT MATTERS: which hard rule it breaks
FIX: the smallest correct change

Prioritise anything that would let a "never confidently wrong" violation ship. If you
find nothing critical, say so plainly rather than inventing something — but look hard
first. Every finding must name concrete inputs and the wrong output that results; no
style nits, no "consider adding a comment".
"""

CODE_PROMPT = """You are a harsh, skeptical staff engineer doing an adversarial code
review. Your job is to BREAK this change, not to praise it. Assume the author is
overconfident and has missed something.

{rules}
## What this release claims to do

{summary}

## Attack it

Go beyond the obvious. Consider at minimum: whether each new ranking or scoring rule
can promote a worse result than the old one; whether any bound, pool or truncation can
still drop the right answer; whether ordering is a total order and genuinely
deterministic; what breaks for a user on the PREVIOUS on-disk schema who queries
before re-syncing; whether an error handler is broad enough to swallow a real failure
and answer from degraded state while looking healthy; and whether any new score or
flag is consumed downstream as a threshold that this change moves.

{fmt}
Here is the complete diff.

"""

TEST_PROMPT = """You are reviewing the QA APPARATUS for a release, not the product
code. Be a harsh skeptic. The question is not "is this nice" but: **would these tests
actually catch the bug they were written for, catch a VARIANT of it, and catch the next
one?** Attack the tests. Assume the author fooled himself.

{rules}
## What this release claims to do

{summary}

## Attack the tests

For EACH test: name a change to the production code that would break the behaviour the
test claims to protect while leaving the test green. Then consider:

1. **Vacuous passes.** Any assertion that holds when the feature is absent, when the
   thing under test disappears entirely, or that asserts on a whole document/packet
   when it should assert on one specific field or line?
2. **Fixtures masquerading as properties.** Is a test named like a general rule
   actually one hand-built example? What inputs would violate the rule while passing?
3. **Preconditions.** Where a fixture could fail to set up the situation it claims
   (e.g. it means to vary an order that something else then sorts), is there an
   assertion proving the fixture actually did its job?
4. **Determinism and environment.** Same-process comparisons cannot see hash-order
   dependence; a fixed decimal rounding has boundaries; a wall-clock value in a hash
   makes it time-dependent.
5. **Corpus realism.** Fixtures are small and synthetic. Which bug classes only appear
   at 10k+ files, across a chunk boundary, or in real documents (multi-sheet
   workbooks, .docx headers and text boxes, merged cells, multi-column PDFs, scans)?
6. **Coverage gaps.** Which failure modes of this system have NO test at all?
7. **Self-deception in the release gate itself** — anything that lets a broken release
   pass or a healthy one fail.

{fmt}
Files under review follow, then the production diff they are testing.

"""


def run(cmd: list, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True, **kw)


def diff_since(base: str, *paths: str) -> str:
    # Base vs WORKING TREE: the review happens before the release is committed.
    return run(["git", "diff", base, "--", *paths]).stdout


def changed_test_files(base: str) -> list:
    out = run(["git", "diff", "--name-only", base, "--", "tests/"]).stdout.split()
    untracked = run(["git", "ls-files", "-o", "--exclude-standard", "tests/"]).stdout.split()
    return sorted({p for p in out + untracked
                   if p.endswith(".py") and (REPO / p).exists()})


def summary_from_changelog(version: str) -> str:
    """The release's own CHANGELOG section — the claim the reviewer must attack."""
    text = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    marker = f"## [{version}]"
    if marker not in text:
        return (f"(No CHANGELOG section for {version} yet — describe the change in "
                f"the prompt before sending it.)")
    body = text.split(marker, 1)[1]
    return marker + body.split("\n## ", 1)[0]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--version", required=True, help="e.g. 0.5.3")
    ap.add_argument("--base", required=True, help="previous tag, e.g. v0.5.2")
    ap.add_argument("--skip-run", action="store_true",
                    help="build the prompts and archive folder, run nothing")
    args = ap.parse_args()

    out_dir = QA_ARCHIVE / f"v{args.version}"
    (out_dir / "evidence").mkdir(parents=True, exist_ok=True)

    summary = summary_from_changelog(args.version)
    src_diff = diff_since(args.base, "src/")
    if not src_diff.strip():
        print(f"nothing changed under src/ since {args.base} — is --base right?")
        return 1
    tests = changed_test_files(args.base)
    if not tests:
        print("WARNING: no test file added or changed. Every release must add a "
              "failing test first — see docs/RELEASING.md step 1.")

    # ---- pass 1: the diff
    code_prompt = NO_TOOLS + CODE_PROMPT.format(
        rules=RULES, summary=summary, fmt=OUTPUT_FORMAT) + src_diff
    p1 = out_dir / "AUDIT_BRIEF.md"
    p1.write_text(code_prompt, encoding="utf-8")

    # ---- pass 2: the tests
    parts = [NO_TOOLS + TEST_PROMPT.format(
        rules=RULES, summary=summary, fmt=OUTPUT_FORMAT)]
    for rel in tests + ["benchmarks/qa_release.py"]:
        parts += [f"\n===== FILE: {rel} =====\n```python\n",
                  (REPO / rel).read_text(encoding="utf-8"), "\n```\n"]
    parts += ["\n===== the production diff under test =====\n```diff\n", src_diff,
              "\n```\n"]
    p2 = out_dir / "AUDIT_BRIEF_TESTS.md"
    p2.write_text("".join(parts), encoding="utf-8")

    print(f"archive:  {out_dir}")
    print(f"pass 1 prompt: {p1.name}  ({len(code_prompt.splitlines())} lines)")
    print(f"pass 2 prompt: {p2.name}  ({sum(1 for _ in open(p2))} lines)")
    print(f"tests under review: {', '.join(tests) or 'NONE'}")

    review1 = out_dir / f"REVIEW_CODE_{AGY_MODEL}.md"
    if args.skip_run:
        print(f"\n--skip-run: nothing executed. Pass 1 would be:\n"
              f"  agy -p \"$(cat {p1})\" --model {AGY_MODEL} --effort high "
              f"> {review1}")
    else:
        print(f"\nrunning pass 1 ({AGY_MODEL}) — this takes a few minutes...")
        proc = run(["agy", "-p", code_prompt, "--model", AGY_MODEL,
                    "--effort", "high"])
        review1.write_text(proc.stdout or proc.stderr, encoding="utf-8")
        n = (proc.stdout or "").count("SEVERITY:")
        print(f"  wrote {review1.name} — {n} finding(s)")
        if "no output produced" in (proc.stdout or ""):
            print("  agy was auto-denied a tool permission; the NO_TOOLS preamble "
                  "should prevent this — check the prompt reached it intact.")

    print("\nnext:")
    print(f"  1. pass 2 (the higher-yield one; spend codex's limited quota here):")
    print(f"       codex exec -s read-only -o {out_dir / 'REVIEW_TESTS_codex.md'} "
          f"- < {p2}")
    print(f"  2. ADJUDICATE every finding against the code — reproduce or disprove "
          f"it. Never accept on authority.")
    print(f"  3. Write {out_dir / 'ADJUDICATION.md'}: one row per finding with the "
          f"verdict, and keep the scripts that settled it in evidence/.")
    print(f"  4. Fix the confirmed ones with tests that fail first, then:")
    print(f"       python3 benchmarks/bench_all.py record")
    print(f"       python3 benchmarks/qa_release.py --base {args.base}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
