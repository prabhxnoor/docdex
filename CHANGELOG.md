# Changelog

All notable changes to docdex are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and version numbers follow
[Semantic Versioning](https://semver.org/).

**Readability rule:** entries are written for humans first. Anything that is
unavoidably technical gets a plain-English *"In plain terms"* line so you can
tell what changed and why without reading the code.

## [Unreleased] — v0.2 "Trust & Context Foundations"

Theme: make every result safe and trustworthy, then make docdex hand an agent
*the context it needs for a task* instead of a list of search hits. Full plan
in [docs/V0.2_PLAN.md](docs/V0.2_PLAN.md). This section fills in as the work
lands; nothing here has shipped in a tagged release yet.

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
