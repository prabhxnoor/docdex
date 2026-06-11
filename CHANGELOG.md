# Changelog

All notable changes to docdex are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and version numbers follow
[Semantic Versioning](https://semver.org/).

**Readability rule:** entries are written for humans first. Anything that is
unavoidably technical gets a plain-English *"In plain terms"* line so you can
tell what changed and why without reading the code.

## [Unreleased]

Next: **v0.4.0 — "A packet you can trust."** The independent round-3 audit
(2026-06-12) confirmed the v0.3 speed fix but refuted the honesty claim, so v0.4
hardens the packet *before* making it cleverer: confine `purge --state-only`
(DDX-028), field-local value extraction so a form answer can't steal a neighbour's
value (DDX-029), stop reporting present search hits as "missing" (DDX-030), fix
conflict newest-source + equivalent-amount handling (DDX-031/032), token-exact
budgets (DDX-033), a Unicode-aware tokenizer (DDX-034), and stop hiding corrupt
state (DDX-035). Meaning-aware search (aliases/stemming) moves to **v0.5.0**, on
the auditor's advice to fix extraction first. See [ROADMAP.md](ROADMAP.md).

## [0.3.0] — 2026-06-11 — "Task-aware context"

Reshapes the `context` packet so an agent can never mistake a partial answer for
a complete one — the heart of what docdex is for. (Phase 2 of the v0.2 audit
plan; 108 tests.)

### Added

- **A coverage line on every packet.** In form mode it reads e.g. *"12 fields ·
  8 found · 2 weak · 1 missing · 1 dropped(budget)"*; in free-text mode it counts
  value answers and unmatched terms. *In plain terms:* the packet now tells the
  agent up front how much of the job it actually covered — so a thin answer looks
  thin instead of looking finished.
- **Honest budgets (audit DDX-018).** The budget line shows *requested · used ·
  free*; a non-positive budget retrieves nothing and says so loudly; and when the
  budget cuts evidence a **"Dropped (budget)"** section appears with a "rerun with
  --budget N" hint. *In plain terms:* a too-small budget can no longer hand back a
  confident-but-incomplete packet without flagging it.
- **A Conflicts section.** When two sources give different values for the same
  thing — one file says 30 deals, a newer one says 40 — the packet flags the
  disagreement and marks the newer source instead of silently picking one. (A
  first, lexical version; richer recency/authority handling is the next milestone.)
- **`--check-freshness`** for an on-demand full staleness re-check.

### Changed

- **`context` is fast again on large folders (audit DDX-019).** It no longer
  walks the whole corpus on every call just to print freshness; by default it
  trusts the last sync (and says so), doing the full walk only with
  `--check-freshness`. *In plain terms:* the packet command keeps pace with search
  even on big corpora.
- **Form parsing handles all fields and Unicode labels (audit DDX-020).** No more
  silent stop at 40 fields, and labels like "Échéance" now parse.
- Packet section "Likely answers" is now **"Answers"**; evidence lines show the
  source's date.

### Notes

- The form-filling benchmark still reproduces at 8/11 fields with the absent field
  flagged honestly, now at ~1,338 tokens (chars/4) — ~7% of a naive search loop's
  cost. Excluding docdex's own scaffold files from evidence (so they're never
  cited as answers) also made packets leaner.

## [0.2.1] — 2026-06-11 — "Trust & robustness"

Closes the trust and robustness findings from the independent v0.2.0 audit
(Phase 1 of the v0.3 plan). One was a real safety hole; the rest stop a
foreseeable corrupt file or interrupted run from crashing or quietly misleading
the tool. 98 tests now (up from 82) — 16 new ones reproduce each finding below
before asserting the fix.

### Security

- **The index can no longer escape the project through a symlink.** If the index
  folder is — or is swapped for — a symlink pointing outside the project, docdex
  refuses to write rather than putting its state (and a later `purge`) somewhere
  outside. *In plain terms:* a leftover or planted shortcut named like the index
  folder used to let docdex write outside your project; that's blocked now. Index
  folder names are also tightened (no `~`, tabs, or newlines — spaces are still
  fine). (Audit findings DDX-015, DDX-025.)

### Fixed

- **A corrupted index database self-heals instead of crashing.** If `index.db`
  gets damaged, `sync` now sets the bad file aside and rebuilds it from the text
  caches instead of stopping with a Python error. (DDX-016.)
- **Every state file fails with a clear message when corrupt.** The NUL-byte and
  format checks now cover the extraction-status file too, and reject ragged or
  garbled rows, so a damaged file says "run sync to rebuild" instead of crashing
  or being silently read as empty. This also makes corrupt-file detection behave
  the same on every Python version (Python 3.11+ had quietly changed the
  behaviour the old check relied on, which turned the test suite red on GitHub).
  (DDX-017, DDX-008.)
- **An interrupted sync recovers immediately.** If a sync is killed, the next run
  notices the previous process is gone and takes over at once, instead of
  refusing for 30 minutes. (DDX-021.)
- **`search` before the first sync now says "run sync first"** (a clear error)
  instead of the misleading "no matches". (DDX-024.)

### Added

- **A size cap so one huge file can't bloat the index.** A supported file larger
  than `max_extract_mb` (default 50 MB) is recorded as `skipped` rather than
  extracted; raise `max_extract_mb` in `.docdex.json` (or set `0` to disable), or
  pass `docdex sync --allow-large-text`, to index it anyway. *In plain terms:* a
  stray multi-hundred-MB log or export used to balloon the index to several times
  its size; now it's skipped with a note. (DDX-022.)

### Docs

- New living **[ROADMAP.md](ROADMAP.md)**; README states plainly what docdex is
  and isn't (a context provider for an agent, not an OS search engine) and adds a
  full **install → index → use → uninstall** guide — including that indexing is
  100% deterministic, so the AI model and effort setting make no difference to
  it. The embedding-model example is now **local-only with a privacy warning**,
  and the scaffolded `AGENTS.md` teaches the `context` workflow. (DDX-010, DDX-013,
  DDX-023, DDX-026.)

## [0.2.0] — 2026-06-11 — "Trust & Context Foundations"

Theme: make every result safe and trustworthy, then make docdex hand an agent
*the context it needs for a task* instead of a list of search hits. Shaped by an
independent third-party audit (every confirmed finding below is closed) and an
architecture review. Full plan in [docs/V0.2_PLAN.md](docs/V0.2_PLAN.md).

### Security

- **The index can no longer reach outside its own project.** The `index_dir`
  setting in `.docdex.json` is now validated to be a plain folder name inside
  the project; absolute paths, `..`, and path separators are rejected. Every
  delete during `purge` is additionally checked to stay inside the project.
  *In plain terms:* a corrupted or hand-edited config file could previously
  make `docdex purge` delete a folder **outside** your project, including
  non-docdex files. That can't happen anymore. (Audit finding DDX-001.)
- **Symlinks no longer leak content from outside the project.** Symlinked
  files are skipped by default; an optional `follow_symlinks` config can
  re-enable them, and even then the target must stay inside the project.
  *In plain terms:* if your folder contained a shortcut pointing somewhere
  private, docdex used to read and cache that private file. Now it doesn't,
  unless you explicitly opt in. (Audit finding DDX-002.)

### Added

- **`docdex context "your task" --budget N` — the headline new command.** Instead
  of a list of search hits, it returns a compact *evidence packet*: the likely
  cited answers, supporting excerpts with sources, an explicit "what's missing"
  list, and a suggested follow-up — all packed to fit a token budget.
  `docdex context --from-file form.md` retrieves evidence field-by-field for a
  form. *In plain terms:* this is the thing the tool was really for — an AI
  assistant asks docdex for the context it needs to do a job, and gets just
  that, with citations, instead of reading hundreds of files. docdex stays
  deterministic and never calls an AI model itself; it hands the packet to the
  assistant already doing the work.
- **A real search engine under the hood (SQLite + FTS5, BM25 ranking).** `sync`
  now builds a `_state/index.db` lexical index; `search` uses it automatically
  when present. *In plain terms:* search is now both faster on big folders and
  much harder to fool — a file that simply repeats a word can no longer beat
  the file that actually answers your question. The plain-text caches are still
  the source of truth; the database is just a rebuildable index, and docdex
  falls back to the old scorer (with the same anti-stuffing fix) if a machine's
  SQLite happens to lack FTS5. (Audit finding DDX-007.)
- Per-chunk **token counting** (uses `tiktoken` when installed, a chars/4
  estimate otherwise) — groundwork for token-budgeted context (see plan).
- **A form-filling benchmark** (`benchmarks/task_benchmark.py`) that measures the
  thing the tool is really for: how much of a multi-field job's context each
  approach delivers per token. `docdex context` got ~73% of the answerable
  fields at ~7% of a naive search loop's token cost, and — unlike the
  alternatives — correctly reported the one absent field as "not found" instead
  of forcing a guess. *In plain terms:* a measured, honest demonstration that
  asking docdex for task context beats reading files, with the misses explained
  rather than hidden.

### Fixed

- **Fuzzy search no longer reports junk as a real result.** `docdex semantic`
  now exits with a "no matches" status for empty, punctuation-only, or
  genuinely-unmatched queries instead of returning scaffold README files with
  a score of zero. Very short documents are now indexed too, so a one-line
  file is findable. *In plain terms:* the fuzzy search used to confidently
  hand back unrelated files as if they were the answer — the worst kind of
  wrong for an AI assistant. It now says "nothing matched" when nothing
  matched. (Audit findings DDX-003, DDX-009.)
- **Adding a duplicate file is counted as a new file, not a rename.** Sync
  only treats a file as renamed when its twin has actually disappeared.
  (Audit finding DDX-006.)
- **A plug-in embedding model that misbehaves no longer crashes docdex.**
  Embedding output is checked for valid numbers and a consistent size, and
  errors are reported cleanly instead of as a Python stack trace. (Audit
  finding DDX-004.)
- **Corrupt index files give a clear message, not a stack trace.** A damaged
  `.docdex.json` or inventory file now reports "this file looks corrupt; run
  doctor or re-sync" with a clean exit code. (Audit finding DDX-008.)
- Smaller polish from the audit: `dumps` before a sync now says "run sync
  first" instead of writing an empty file (DDX-012); docdex's own scaffold
  READMEs are no longer queued as OCR work or shown as evidence (DDX-011);
  `status` notes that it is a fast check and `sync` is authoritative (DDX-005);
  docs clarify the `./ctx` wrapper from subdirectories (DDX-010) and that large
  text files are cached in full (DDX-013).

### Packaging

- Switched to an SPDX `license = "MIT"` declaration, clearing the setuptools
  deprecation warning that was set to break builds in 2027. (Audit DDX-014.)

## [0.1.1] — 2026-06-11

### Added

- **Reproducible value benchmark** (`benchmarks/`). A deterministic test corpus
  with facts hidden inside Office/PDF files behind misleading filenames, used to
  measure how much context an agent must read to reach an answer. Headline:
  `docdex search` reached every answer at roughly **36× less context** than
  reading everything; filename browsing and raw grep found nothing.
- README sections on the measured value and on **using docdex with an LLM**
  (agent session protocol, a curation prompt, automation notes).

### Changed

- **Smarter fuzzy ranking.** `docdex semantic` now boosts a result by how many
  of your search words actually appear in it, so a file that genuinely mentions
  the query beats a file that just happens to share vocabulary.
  *In plain terms:* fuzzy search got noticeably less easy to fool.

## [0.1.0] — 2026-06-11

First packaged release. docdex is the productized, generic rewrite of an
internal document-indexing toolchain, turned into an installable package.

### Added

- `docdex` command installable once via pipx and usable in any number of
  projects; per-project state under `<index>/_state/`. `init` scaffolds the
  index plus `CLAUDE.md`/`AGENTS.md` agent instructions and a `./ctx` wrapper.
- Incremental `sync` (new/changed/renamed/deleted, content-hash rename
  detection), ranked `search`, fuzzy `semantic`, per-folder context `dumps`,
  cloud `prefetch`, a `vision` OCR queue, `dedup`, `doctor` (with `--e2e`),
  and a zero-residue `purge`.
- 42 automated tests; CI on Ubuntu and macOS across Python 3.9 and 3.12.

### Fixed (versus the internal prototype)

- Vision/OCR notes are now searchable (they live inside the indexed tree).
- Cache filenames are collision-proof (content-hash suffix).
- Scanned/empty documents are recorded once, not re-processed every sync.
- Sibling folders sharing the index name are no longer skipped by mistake.
- Semantic indexing is incremental; `--dry-run` writes nothing; cloud prefetch
  covers the whole corpus.
