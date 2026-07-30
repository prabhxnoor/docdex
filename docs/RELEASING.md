# How a docdex release is cut

Every release follows this, in this order. Nothing here is optional and nothing here
depends on remembering it: `benchmarks/qa_release.py` enforces the parts a machine
can check and fails the release when one is missing.

```
python3 benchmarks/review_kit.py  --version 0.5.3 --base v0.5.2   # step 3
python3 benchmarks/bench_all.py   record                          # step 4
python3 benchmarks/qa_release.py  --base v0.5.2                   # step 5 (the gate)
```

Why it is this strict: v0.5.0 shipped a regression that a 226-test suite and the
benchmark both missed, because the benchmark reported one number and that number
didn't move — one field had been silently traded for another. Every rule below exists
because something got through.

---

## 1. Write the failing test first

A release starts with a test that fails. Not after the fix — before it. A test written
afterwards passes immediately, which proves nothing about whether it can catch the bug.

**Standard: every release adds or changes at least one file under `tests/`, and at
least one of those tests must fail *on an assertion* when run against the previous
release.** The gate checks this (gate 3) and rejects the release otherwise. A test
that merely *errors* on the base tree — because it calls a function this release
introduced — does not count: that is evidence the API changed, not that the behaviour
did.

Name the file for what it protects, not the version: `test_stem_precision.py`,
`test_form_meaning.py`, `test_never_index.py`. A future reader wants to know what the
test defends, not which release added it.

Prefer **properties over fixtures** where the bug is about ranking or retrieval —
`tests/test_retrieval_properties.py` holds statements that must be true for *any*
corpus ("a term occurring in exactly one chunk is always retrievable"), which is where
flooding and ranking bugs actually live. A hand-built corpus where the answer is easy
to find will keep passing while the product breaks.

If a real bug is found but deliberately not fixed in this release, land it as
`pytest.mark.xfail(strict=True)` with a reason naming the target release. Strict means
it fails loudly the moment it starts passing, which is the signal to delete the
marker — that is how v0.5.1's tracked gap became v0.5.2's fix.

### If the release touches stored state, test the *upgrade*, not just the result

**Standard: any change to something docdex has already written to disk — a database
column, a schema version, a file name, a cache layout — needs a test that starts from
the previous release's on-disk format and upgrades it.** A fresh build is not that
test, and it is what every existing test does by default, because every test starts
from an empty directory.

This rule exists because v0.5.2 shipped with a full green suite and broke every index
already on disk. It added a column to `chunks`, and `CREATE TABLE IF NOT EXISTS` does
nothing to a table that exists — so on a real index the column was never added and
`sync` died inserting into it. Nothing caught it: 263 tests, all starting from nothing.

Two properties are worth stating separately, because the second is what turned a crash
into data loss:

- **The upgrade succeeds** — old format in, working index out, and the data is still
  there afterwards.
- **A failed upgrade changes nothing.** Force the upgrade to fail partway and assert
  the previous state still works. v0.5.2's upgrade deleted the search index outside a
  transaction, so the deletion was committed and the crash that followed left an empty
  index — every query then returned "no matches" for the entire corpus.

And whatever the release makes newly detectable, add the check to `docdex doctor` in
the same commit. A guarantee nobody can inspect is a guarantee nobody can trust —
v0.5.4's index was empty for a day while every check reported PASS.

## 2. Fix it, and check what the fix cost

Measure the cost of the fix on something real, not only the synthetic corpus.
v0.5.1's index-size figure was +10% on a 4,000-file synthetic corpus and **+26%** on
the real 10.5k-file one, because real documents have far richer vocabulary. Publish
the real number.

## 3. External adversarial review — two passes

A Claude subagent reviewing Claude-written code shares its blind spots ("it wrote it
anyway"). The reviewer is a **different model family**, framed as a devil's advocate
told to break the change.

`review_kit.py` builds both prompts, runs the first, and archives everything.

| pass | reviewer | target | why |
|---|---|---|---|
| 1 | `agy` — `gemini-3.6-flash-high` | the **diff** | separate quota, fast, and it beats 3.1-pro on every coding/agentic benchmark |
| 2 | `codex` — `gpt-5.6-sol` @ xhigh | the **test suite** | the higher-yield pass; spend the limited quota here |

**Pass 2 is the one that pays.** Asked *"would these tests catch a variant of this
bug?"*, codex found on v0.5.1: a `COUNT(*)` assertion that proves nothing on an
external-content FTS table (it reads the content table, so an empty index still
reports healthy rows), a determinism test blind to hash-seed order, an indexing-order
test that was vacuous because the sync sorts paths, an honesty test that passed when
the field vanished entirely, and five ways the gate itself could pass a broken
release. Reviewing the code found less.

**Adjudicate; never accept.** Every finding is reproduced or disproved against the
code before anything changes. Across v0.5.1 and v0.5.2, **5 findings were refuted** —
including a CRITICAL that would have meant "fixing" correct code. Build a
counter-corpus when a claim is falsifiable; two of v0.5.2's findings died that way and
two of codex's suggested corpora exposed *real* bugs instead.

`codex` runs on a limited plan. One combined pass at `xhigh` per release, on the
highest-risk surface. Never per-commit.

## 4. Benchmark every suite, every release

**Standard: `bench_all.py record` before every release.** It runs *both* suites and
appends to [`benchmarks/HISTORY.json`](../benchmarks/HISTORY.json), with a generated
[`HISTORY.md`](../benchmarks/HISTORY.md) table and a regression list computed between
consecutive releases.

This exists because Suite A had not been run since **v0.1.1** — five releases with an
unverified headline in the README — and Suite B overwrote its own results each run, so
there was no trend and the v0.5.0 trade was invisible.

Comparisons use a **fixed oracle**: `bench_all.py sweep` overlays *today's*
`benchmarks/` onto each checked-out release, so only `src/` differs between rows.
Comparing releases whose harness also differs measures the measuring stick. A release
whose numbers cannot be produced is recorded as an **error**, never skipped — silence
would read as "no change".

## 5. The gate — run it, then run it again on the commit

```
python3 benchmarks/qa_release.py --base <previous tag>
```

| gate | what it refuses to let through |
|---|---|
| 0 preflight | version not bumped; no CHANGELOG section; no ROADMAP mention; no test file added; no QA archive folder |
| 1 suite | any failure, any collection error, a non-zero pytest exit, or an empty run — read from JUnit XML, not scraped from stdout |
| 2 benchmarks | **both** suites compared; suite B per field — value, section *and* cited source — plus honest absent-field handling and a token ceiling |
| 3 discrimination | no new test fails **on an assertion** against the base tree |
| 4 determinism | packet hash differs between runs, or across `PYTHONHASHSEED` values |
| 5 honest verdict | states whether it verified a commit or a dirty tree |

Gate 3 measures the tests. The others measure the product.

The gate reports a dirty working tree as a note, not a pass: **commit, then run it
again.** "Safe to tag" is a claim about a commit, and a tag that ships unverified code
is exactly the failure this whole document is about.

## 6. Verify the built wheel, then tag and push

```
python3 -m build --wheel
python3 -m venv /tmp/wheeltest && /tmp/wheeltest/bin/pip install -q dist/docdex-<v>-py3-none-any.whl
/tmp/wheeltest/bin/docdex --version         # then init/sync/context on a scratch corpus
git tag -a v<version> -m "..." && git push origin main --follow-tags
```

## 7. Archive the round

`docdex-qa/v<version>/` gets `AUDIT_BRIEF.md`, each `REVIEW_*.md`, `ADJUDICATION.md`,
and `evidence/` — the scripts that settled the arguments, including the ones that
**refuted** findings. Never venvs, corpora, pip installs or `__pycache__`: the v0.3.0
round accumulated 2 GB of regenerable scratch that had to be deleted later.

v0.4.0 and v0.5.0 skipped this, and their review findings now survive only in commit
messages. That is the gap the archive exists to close.

## 8. Keep the documents true

Updated **in the same commit as the code**, never after: `CHANGELOG.md` (plain
English, with an *"In plain terms"* line for anything unavoidably technical),
`ROADMAP.md` (move the item to Shipped, tick the box, renumber what shifted, and
record any newly-discovered gap), `README.md` (any figure it quotes), and this file
when the process itself changes.

A stale document is a defect. The gate checks that CHANGELOG and ROADMAP mention the
version; it cannot check that a README figure is current, so re-read the ones you
touched. v0.5.1 found the README quoting a benchmark number that had gone unverified
for five releases.
