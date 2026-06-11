# docdex — Roadmap

> **This is the living plan.** It is meant to outlive any single work session or
> terminal. When something ships, move it to **Shipped** and tick the box; when a
> new idea or constraint appears, add it under the right milestone. The
> per-release design docs (e.g. [`docs/V0.2_PLAN.md`](docs/V0.2_PLAN.md)) are
> frozen historical records; *this* file is the one that keeps moving.
>
> _Last updated: 2026-06-11 (after v0.2.0)._

## North star

**Give an AI agent the majority of the context it needs to finish a task in a
document-heavy project, with the fewest tokens — accurately, with citations, and
honest about what's missing.** docdex is the retrieval layer an LLM calls on your
behalf. It is *not* an OS search replacement, and it never calls an LLM itself —
it stays deterministic and hands the already-running agent a packet to reason
over.

Success is measured as **task-context recall at a token budget**: did the agent
get *enough* of the right context to do the job, while wasting few tokens?

---

## Why v0.2 was "a foundation, not the finished tool"

v0.2 built the **trustworthy plumbing**: it can no longer hurt you (security
fixes), it ranks honestly (real BM25 engine instead of a foolable scorer), it
counts tokens, and it can assemble a cited, budget-sized **evidence packet**
(`docdex context`). That is the skeleton the real product hangs on.

What it deliberately does **not** yet do — the things that make it *smart* rather
than a careful lexical index:

- It matches **words, not meaning.** "Legal name" won't find "the Vendor";
  "governing law" won't find "governed by". (M1)
- It has **no sense of time or truth.** If you index a file saying *30 deals* and
  later one saying *40 deals*, it returns **both**, ranked by keyword relevance,
  and lets the agent notice the conflict. It does not know the newer one
  supersedes the older. (M2)
- It is a **static index.** It grows as you add files and only forgets a file
  when that file is deleted from disk. It has no notion of "this is stale,
  de-prioritise it." (M3)
- Its **budget is a fixed cap**, not an adaptive judgement of how much context a
  task actually needs. (M4)
- It runs on **macOS and Linux**, not Windows. (M5)

Those five gaps *are* the roadmap below. v0.2 is the point at which the
foundation is solid enough to build them safely.

---

## Shipped

- **v0.1.0** — packaged the internal toolchain: `init / sync / search / semantic /
  dumps / prefetch / vision / dedup / doctor / purge`, incremental indexing,
  per-project state, agent scaffolding, 42 tests, CI.
- **v0.1.1** — reproducible single-fact value benchmark (≈36× less context than
  read-everything); README "using docdex with an LLM" guide.
- **v0.2.0 — "Trust & Context Foundations"** (see [`docs/V0.2_PLAN.md`](docs/V0.2_PLAN.md)):
  - [x] Security: index confined to its own project; symlink escape closed.
  - [x] SQLite **FTS5 / BM25** engine; `.txt` caches stay the source of truth.
  - [x] **`docdex context "task" --budget N`** — the cited evidence packet.
  - [x] Per-chunk token accounting (`tiktoken` or chars/4).
  - [x] Honest **form-filling benchmark** (8/11 fields @ 1,464 tok, 1/1 honest miss).
  - [x] Friendly errors on corrupt state; duplicate-vs-rename fix; embedder
    validation; semantic no-match honesty; SPDX license.
  - [x] Corrupt-inventory detection made Python-version-independent (NUL guard).

---

## Forward milestones

Status legend: ⬜ planned · 🟦 in progress · ✅ shipped · ❓ needs a decision (see
**Open questions**).

### M1 — Retrieval quality: match meaning, not just words  *(the v0.3 core)*

The benchmark's 3 misses all live here. Closing them is the highest-value next
step because it directly raises field accuracy at the same token cost.

- ⬜ **Field-alias / synonym registry** — a small, user-extensible map so
  `Legal name → {Vendor, Supplier, Party, legal entity}`. Deterministic.
- ⬜ **Stemming + light lemmatisation** so `governing/governed/governs` collide.
- ⬜ **Optional reranking** of the top-N candidates (pluggable
  `DOCDEX_RERANK_CMD`, off by default → stays deterministic unless you opt in).
- ⬜ **Hybrid lexical + vector fusion** (Reciprocal Rank Fusion) when
  `DOCDEX_EMBED_CMD` is set, so a real embedding model can bridge pure paraphrase
  while BM25 remains the dependency-free default.

### M2 — Corpus intelligence: freshness, conflicts, supersession  *(answers the "30 then 40 deals" question)*

Make docdex aware that documents change and disagree.

- ⬜ **Show recency on every excerpt** — each evidence line already has a source;
  add its `mtime` so "which is newer" is visible at a glance.
- ⬜ **Conflict flagging** — when ≥2 sources give different values for the same
  question/field, the packet says so explicitly ("⚠ 2 sources disagree: *30*
  in `old.xlsx` (Jan), *40* in `new.xlsx` (Mar)") instead of silently picking one.
- ⬜ **Optional recency-weighting** in ranking (a tunable, not a default — a newer
  draft isn't always the truth).
- ⬜ **Same-family supersession hints** ("this looks like a newer version of X").

### M3 — Index lifecycle & self-maintenance  *(answers "won't the DB balloon?" — **DECIDED: opt-in auto-archival, with rails**)*

Reality check: **SQLite FTS5 is built for scale** — tens of thousands of files is
a DB of tens of MB with sub-second queries, so raw *size* isn't a near-term
problem. The decision (2026-06-11) is to go beyond hygiene: docdex should
**self-prune over time via opt-in auto-archival** — engineered so it can never
lose a document.

**Non-negotiable safety rails** (these define what "archive" means here):

- **Off by default.** Archival runs only when you enable explicit rules in
  `.docdex.json` (e.g. *archive files untouched > 18 months*; *demote superseded
  versions of the same doc*). No rules = today's keep-everything behavior.
- **Archives the *index entry*, never the source file.** docdex's hard rule —
  never move or modify source files — still holds absolutely. Archiving parks a
  file's *index presence* (it stops appearing in `search`/`context`) into an
  `_state/archive/` tier; your document on disk is untouched.
- **Fully reversible + audit-logged.** Every archive/restore is recorded (extends
  the existing history log). `docdex archive list`, `docdex restore <path>`, and
  `--restore-all` bring anything back instantly.
- **Preview before action.** `docdex archive --dry-run` shows exactly what would
  be parked; nothing is archived without that preview / an explicit run.

Build order:

- ⬜ **DB hygiene** — periodic `optimize`/`VACUUM`, prune rows for deleted files,
  rotate `inventory_history`. *(Cheap; ships first.)*
- ⬜ **Usage/recency signals** — record what gets retrieved + last-seen, to drive
  both ranking (M2) and the archival rules.
- ⬜ **Opt-in auto-archival engine** — rule evaluation, the `_state/archive/`
  tier, `archive`/`restore` commands, `--dry-run`, audit log. **Depends on M2**
  (it needs recency + same-family supersession detection to know what is "old" or
  "superseded").

### M4 — Budget intelligence: stop guessing how much context to fetch  *(answers the "does the LLM pick the budget?" concern)*

Today `--budget` is a fixed cap (default 3000) and the agent can override it. The
packet already self-reports a **"Missing"** section, so a too-small budget can't
*silently* mislead — but we can do better than a fixed number.

- ⬜ **Confidence-based stopping** — fill until coverage is high, not just until
  the token cap; a simple task shouldn't be padded, a hard one shouldn't be
  starved.
- ⬜ **Adaptive budget suggestion** from task shape (a 12-field form needs more
  than a one-fact lookup).
- ⬜ **Louder incompleteness signal** so an agent never turns a truncated packet
  into a confident wrong answer.

### M5 — Cross-platform: run on Windows  *(answers the "Windows?" question)*

Today: **macOS + Linux only** (CI proves both). Windows is unverified and will
have at least one hard failure.

- ⬜ Replace the macOS-only `textutil` path for `.doc`/`.rtf` with a
  cross-platform extractor (or degrade gracefully).
- ⬜ Audit path handling, symlink logic, and the `./ctx` wrapper for Windows.
- ⬜ Add `windows-latest` to the CI matrix and make it green.

### M6 — Structured extraction & knowledge layer  *(later)*

- ⬜ `ctx facts` / `ctx fill-context` with a typed field registry.
- ⬜ Contextual chunks (prepend a short doc summary/entities before indexing).
- ⬜ Source-authority configuration (trust signed contracts over drafts).
- ⬜ ANN/vector store for 100k+ files (only when a corpus actually needs it).

---

## Decisions & open questions

**Decided**

1. **How smart should the index get about old / superseded information? (M2+M3)** —
   *Decided 2026-06-11:_ go all the way to **option (c), opt-in auto-archival**,
   built on **(b) flag-&-rank** as its prerequisite. Hard rails (see M3): it
   archives *index entries* not source files, stays **off by default**, and is
   **fully reversible + audit-logged** with a `--dry-run` preview. M2 ships first.

**Open**

- _(none right now — add here as they arise.)_

---

## Known limitations (honest, current)

- Lexical matching only by default (M1) — pure paraphrase needs `DOCDEX_EMBED_CMD`.
- No conflict/recency reasoning yet (M2) — conflicting facts are both returned,
  unranked by time.
- macOS/Linux only (M5).
- `read_extract_status` does not yet have the same corrupt-file guard as
  `read_inventory` — low impact (rebuildable snapshot), tracked for consistency.

---

## How releases are cut

Trust/correctness first, then retrieval quality, then convenience. Every landed
item updates the `[Unreleased]` section of [`CHANGELOG.md`](CHANGELOG.md) with a
plain-English line. A release tags, pushes, and verifies a clean install from the
built wheel before announcing.
