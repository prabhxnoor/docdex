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

### M3 — Index lifecycle & scale: stay lean and smart over time  *(answers the "won't the DB balloon?" question)* ❓

Reality check first: **SQLite FTS5 is built for this.** A personal corpus of tens
of thousands of files is a DB of tens of MB and sub-second queries; size is not a
practical problem for years. So the work here is *hygiene + optional
intelligence*, not rescue.

- ⬜ **DB hygiene** — periodic `optimize`/`VACUUM`, prune index rows for
  soft-deleted files so the DB tracks the live corpus, not its whole history.
- ⬜ **Usage/recency signals** — record what actually gets retrieved, to inform
  ranking and any aging policy.
- ❓ **Aging / archival policy** — *should* docdex ever de-prioritise or drop old
  content on its own, and if so by what rule? This is a real design fork with
  data-loss stakes — **see Open question #1.** Default stance until decided:
  docdex never deletes indexed content you haven't deleted yourself.

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

## Open questions (need a user decision before building)

1. **How "smart" should the index get about old / superseded information? (M3)**
   Three distinct directions, very different risk:
   - **(a) Hygiene only** — keep the DB small and fast; never drop content you
     didn't delete. *Safe, low effort.*
   - **(b) Recency & conflict awareness** — never delete, but *flag* stale or
     conflicting facts and optionally weight newer ones higher (this is also M2).
     *The high-value, low-risk option.*
   - **(c) Active forgetting / archival** — actually drop or archive old content
     by age/access so the corpus self-prunes. *Powerful but data-loss-prone;
     needs strict rules.*
   _Recommendation: (a)+(b). Treat (c) as opt-in only, if at all._

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
