# Changelog

## 0.1.1 — 2026-06-11

- **Hybrid semantic ranking.** `docdex semantic` now boosts embedding
  similarity by the fraction of distinct query terms a chunk actually
  contains. Pure cosine over hashed features rewarded vocabulary-soup
  documents; chunks that really mention the query now rank on top.
- **Reproducible value benchmark** (`benchmarks/`): deterministic corpus with
  facts planted in Office/PDF files behind misleading filenames; measures
  hit@1/hit@3, tokens-until-answer, and wall time for docdex vs. filename
  browsing, raw grep, and read-everything baselines. Results in
  `benchmarks/RESULTS.md`; headline 36× context reduction at 12/12 accuracy.
- README: measured-value section and an explicit "Using docdex with an LLM"
  walkthrough (agent session protocol, curation prompt, automation notes).

## 0.1.0 — 2026-06-11

First packaged release. docdex is the productized, generic rewrite of an
internal document-indexing toolchain ("qdoc"/"ctx") that previously lived as
vendored scripts inside one corpus.

### Architecture

- Proper Python package with a `docdex` console command; install once with
  pipx, use in any number of projects. Per-project state lives under
  `<index>/_state/`; projects carry no code, so fixes ship via
  `pipx upgrade` instead of copy-paste (which had already caused two-way
  template drift in the prototype).
- Project discovery via a `.docdex.json` root marker; every command works
  from any subdirectory. The optional `./ctx` wrapper resolves its own
  location, so it also works regardless of cwd.
- One shared filesystem walker for all commands (the prototype had three
  divergent walk implementations).

### Fixes relative to the prototype

- **Vision/OCR notes are searchable.** Notes now live in
  `<index>/vision_notes/`, inside the indexed tree. In the prototype they
  were written under `_tools/` which the walker skipped — notes never became
  searchable despite the documented workflow. Guarded by a regression test
  and a `doctor` check.
- **Collision-proof cache names.** Cache filenames embed a hash of the full
  source path; previously `A B.docx` and `A_B.docx` mapped to the same cache
  file and silently overwrote each other.
- **Scanned/empty documents no longer poison status.** Empty extractions are
  recorded once (`extract_status.tsv`: `empty`) and surfaced as vision/OCR
  candidates instead of being re-extracted every sync and permanently
  reported as cache gaps.
- **Segment-safe path filtering.** Sibling folders sharing the index dir's
  name prefix (e.g. `_indexes/` next to `_index/`) are no longer skipped.
- **Incremental semantic indexing.** Only new/changed files are re-embedded;
  unchanged index lines stream through. The prototype re-embedded the entire
  corpus on every sync.
- **Pure dry-run.** `sync --dry-run` writes nothing (the prototype appended
  to its error log even in dry-run).
- **Prefetch covers the whole corpus.** The prototype's `--quick` mode
  stopped after the first 200 files in walk order.
- Cross-platform behavior for `.doc`/`.rtf`: macOS uses `textutil`; other
  platforms report the files as unsupported instead of erroring.

### Features

- `docdex init` scaffolds the index plus `CLAUDE.md`/`AGENTS.md` agent
  instructions and a `./ctx` wrapper.
- `docdex doctor --e2e` runs a full write→sync→search→delete sentinel
  self-test.
- `docdex purge` guarantees zero residue (`--state-only` keeps curated
  files and vision notes).
- `docdex dedup` is dry-run by default.
- Pluggable embeddings via `DOCDEX_EMBED_CMD`.
