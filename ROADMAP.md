# docdex — Roadmap

> **This is the living plan.** It is meant to outlive any single work session or
> terminal. When something ships, move it to **Shipped** and tick the box; when a
> new idea or constraint appears, add it under the right milestone. The
> per-release design docs (e.g. [`docs/V0.2_PLAN.md`](docs/V0.2_PLAN.md)) are
> frozen historical records; *this* file is the one that keeps moving.
>
> _Last updated: 2026-06-18 (shipped v0.4.1 — hidden `.docdex/` home + external per-machine state cache + `docdex migrate`; added the "lean by default / leave-no-bloat" hygiene discipline to the North star + M3, and self-cleaning OCR scratch to M7, after an external OCR run left ~11 GB of scratch behind)._

## North star

**Give an AI agent the majority of the context it needs to finish a task in a
document-heavy project, with the fewest tokens — accurately, with citations, and
honest about what's missing.** docdex is the retrieval layer an LLM calls on your
behalf. It is *not* an OS search replacement, and it never calls an LLM itself —
it stays deterministic and hands the already-running agent a packet to reason
over.

Success is measured as **task-context recall at a token budget**: did the agent
get *enough* of the right context to do the job, while wasting few tokens?

**Lean by default.** docdex must stay the kind of tool a top-tier engineer would
ship: fast, token-efficient, accurate — and with **zero bloat left behind**. Every
feature cleans up after itself; rebuildable state is bounded and pruned; ephemeral
scratch is always removed; nothing multi-GB ever lingers waiting for a human to
delete it. Storage hygiene is a *feature* (tracked in M3) and a release-checklist
item — never an afterthought.

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
- **Independently audited 2026-06-11 (round 2)** — reports kept locally in
  `~/Projects/docdex-qa/v0.2.0/`. The FTS5 engine was validated (flat ~36 ms
  search even at 50k files); the auditor found **1 critical + 7 major** issues,
  all feeding the v0.3 plan below. Headline verdict: *"the engine is good; the gap
  is task awareness — coverage, budgets, conflicts, follow-up signalling."*
- **v0.2.1 — "Trust & robustness"** (2026-06-11): closed **Phase 1** of the audit
  — the symlink index-escape (DDX-015), corrupt-DB self-heal (DDX-016), state-
  reader hardening (DDX-017), dead-PID lock recovery (DDX-021), large-file cap
  (DDX-022), and the minors. 98 tests (16 new regressions mirroring the repros).
- **v0.3.0 — "Task-aware context"** (2026-06-11): **Phase 2** — the `context`
  packet now carries a coverage header + honest budget accounting (DDX-018),
  flags conflicting sources (newer first), is fast at scale again (DDX-019: no
  per-call corpus walk), and parses all/Unicode form fields (DDX-020). 108 tests.
- **Independently audited 2026-06-12 (round 3)** — reports kept locally in
  `~/Projects/docdex-qa/v0.3.0/`. The **speed fix is confirmed** (50k-file packet
  ~253 ms median vs 43 ms search; the old ~4.4 s walk is now behind
  `--check-freshness`) and the prior trust fixes hold. But the **central v0.3
  thesis was refuted**: an agent still cannot reliably tell a complete packet from
  a wrong or partial one. **4 critical + 5 major + 2 minor** (DDX-028–DDX-038): a
  reopened destructive boundary escape in `purge --state-only`, wrong cross-field
  form answers marked "found", real search hits reported "missing" because the
  BM25 *display* score was used as a truth filter, conflicts marking the wrong
  (older) source and false-conflicting equivalent amounts, tiny budgets returning
  over-budget packets with no drop signal, and corrupt inventory hidden behind a
  healthy-looking packet. Verdict: *"the packet architecture is fast and compact;
  it is not yet trustworthy — optimise for 'never confidently wrong' before 'more
  semantically broad.'"* This reshaped the plan below: **v0.4.0 is now packet-trust
  hardening, not meaning-aware search.**
- **v0.4.0 — "A packet you can trust"** (2026-06-12): **Phase 3** — closed all 11
  round-3 findings (DDX-028–038). `purge --state-only` confined via a shared guard;
  field-local value extraction (no cross-field leakage); match-existence split from
  the BM25 display score; conflict v2 (newest-per-value + amount normalization);
  token-exact budgets; one Unicode-aware tokenizer; corrupt state surfaced not
  hidden; scaffold fingerprinting (an edited `CLAUDE.md` surfaces); zero-field and
  duplicate form-label fixes. 124 tests; the form benchmark holds at 8/11 with now-
  *correct* values, and a real-CLI "make the packet lie" smoke passed.
- **v0.4.1 — "One tidy home, state out of the cloud"** (2026-06-18): storage-layout
  overhaul — one hidden `.docdex/` home in the project; all rebuildable state moved
  to a per-machine external cache (`~/.cache/docdex/`), so a cloud-synced folder
  stays clean and two machines syncing it never corrupt one shared index. New
  `docdex migrate` (idempotent, `--dry-run`) upgrades v1 projects, which keep
  working until migrated. Folds in the real-corpus fixes (password-protected PDFs;
  quieted extractor warnings). 167 tests. Reasoned from the two-laptop / OneDrive
  sync question.

---

## The sequenced plan  *(updated after the v0.3.0 round-3 audit)*

Theme: **make the packet trustworthy, then task-aware, then smart.** The engine
scales and is fast; the open problem is *honesty* — an agent must never mistake a
wrong or partial packet for a complete one. v0.2.1 closed the trust blockers and
v0.3.0 shipped the packet shape + speed, but the round-3 audit showed the honesty
guarantees don't hold yet. So the next release hardens the packet *before* we make
it cleverer. Build in this order:

**Phase 1 — Trust blockers — ✅ shipped in v0.2.1.**
- ✅ **DDX-015 [CRITICAL]** — a symlinked `index_dir` can no longer steer writes
  (or a later `purge`) outside the project; refused at every init/sync write.
- ✅ **DDX-016 [MAJOR]** — a corrupt `index.db` is quarantined and rebuilt from the
  caches instead of crashing `sync`.
- ✅ **DDX-017 [MAJOR]** — NUL/header/row validation now covers `extract_status.tsv`
  and the semantic manifest/meta; a ragged inventory errors instead of being read
  as zero rows. *(The `semantic_index.jsonl` read path already skips bad lines;
  full per-line hardening tracked for v0.3.)*
- ✅ **DDX-021 [MAJOR]** — a killed sync is recovered immediately via a dead-PID
  check instead of blocking for 30 minutes.
- ✅ **DDX-022 [MAJOR]** — `max_extract_mb` (default 50) records oversize files as
  `skipped`; `--allow-large-text` overrides.
- ✅ **DDX-023/024/025/026 [MINOR]** — `search` before first sync says "run sync";
  `index_dir` rejects `~`/control chars (spaces still allowed); hand-edited caches
  documented; `AGENTS.md` teaches the `context` workflow.

**Phase 2 — Make the product honest and fast — ✅ shipped in v0.3.0.**
- ✅ **DDX-019 [MAJOR]** — `context` no longer walks the corpus for freshness on
  every call; it trusts the last sync by default, `--check-freshness` does the walk.
- ✅ **Budget honesty + coverage accounting (DDX-018)** — coverage header (found/
  weak/missing/dropped), a `requested · used · free` budget line, a non-positive
  budget retrieves nothing loudly, and a `Dropped (budget)` section with a rerun
  hint. Done in the tool, not just the scaffold.
- ✅ **DDX-020 [MAJOR]** — form mode parses all fields (no 40-cap) and Unicode
  labels; the coverage line discloses the field count.
- ✅ **Conflicts (M2 seed)** — differing values across sources are flagged with the
  newer marked; lexical for now, deepened in v0.4.

> ⚠️ **Round-3 audit verdict (2026-06-12): the shape shipped, the honesty did
> not.** The coverage/budget/conflict sections are the right contract, but the
> audit produced confident packets that were *wrong*: cross-field answers stolen
> from a neighbour and marked *found*, present search hits reported *missing*, the
> *older* file marked "newest" in a conflict, equivalent amounts false-conflicted,
> and corrupt state hidden. Phase 3 is now about making each of those guarantees
> literally true.

**Phase 3 — A packet you can trust — ✅ shipped in v0.4.0.**  *This replaced
meaning-aware search at the front of the queue, on the auditor's explicit advice:
"aliases will not fix v0.3's worst bugs — layered on today's value heuristics they
will only increase the candidate pool and the false-found/false-conflict rate. Fix
field-local extraction first." (`CONTEXT_EFFICIENCY_REVIEW.md` §5.)*

- ✅ **DDX-028 [CRITICAL · security]** — `purge --state-only` still deletes through
  a symlinked index dir (the DDX-015 fix missed this branch). Apply the same
  `is_within_root`/`is_symlink` confinement as full purge, via a *shared* path
  helper, + a regression test.
- ✅ **DDX-029 [CRITICAL]** — **field-local value extraction.** Stop substring label
  matching (`term` ∈ `terms`), split semicolon/table-dense lines, take the value in
  a bounded window *after* the matched label, and downgrade broad multi-label lines
  to *weak*. This is the worst class — wrong-as-right in form answers, which is the
  user's core due-diligence use case.
- ✅ **DDX-030 [CRITICAL]** — separate "a match exists" from the BM25 *display*
  score; never report present, searchable evidence as *missing* just because the
  rounded score is ~0 (common in small / all-matching corpora).
- ✅ **DDX-031 [CRITICAL]** — conflict grouping by value → *all* its sources; mark
  the genuinely newest source (not the first one seen); list agreeing sources.
- ✅ **DDX-032 [MAJOR]** — normalise equivalent amounts (₹4.20 cr = 4.2 crore =
  42,000,000) so they don't false-conflict; capture the full currency phrase (no
  `₹4` truncation); show raw + normalised value.
- ✅ **DDX-033 [MAJOR]** — **token-exact budget.** Count the *rendered* packet with
  the same tokenizer the packet reports, warn whenever `used > requested`, enforce a
  minimum viable budget, and emit the drop signal in free-text mode too (not only
  form mode).
- ✅ **DDX-034 [MAJOR]** — one Unicode-aware tokenizer across parse / FTS query /
  value-match / "tried" display, so `Échéance` evidence is actually retrieved.
- ✅ **DDX-035 [MAJOR]** — stop `_mtime_map` swallowing a corrupt-inventory error;
  `context` must fail friendly or warn loudly, never emit a confident packet from
  known-corrupt state.
- ✅ **DDX-036 [MAJOR]** — fingerprint scaffold files at init and exclude only the
  *unchanged* ones, so a user-edited root `CLAUDE.md`/`AGENTS.md` is treated as real
  evidence instead of silently hidden.
- ✅ **DDX-037 / DDX-038 [MINOR]** — a zero-field `--from-file` says "0 fields"
  instead of running the filename as a free-text query; duplicate form labels are
  preserved/flagged, not silently deduped.
- ✅ **Tests for every repro (DDX-028–038)** + a scale guard (default `context`
  stays near `search`; only `--check-freshness` may grow). The audit's §10 lists
  exactly why the 108-test suite missed these — clean one-value-per-sentence
  corpora — so the new tests use dense / shared-label / Unicode / score-0 fixtures.

**Phase 4 — Meaning-aware search + deeper conflict (→ v0.5.0). ← next.**  *(Was
Phase 3; moved one release back, deliberately gated behind Phase 3.)*
- ⬜ **Stemming / lemmatisation** first (`close`/`closed`, `governing`/`governed`).
- ⬜ **Field-alias registry** ("legal name" → "Vendor"), deterministic, visible in
  `--explain`, never used to fabricate a value.
- ⬜ **Utility reranker** — prefer label-local values, explicit label-value rows,
  and source diversity over raw term frequency.
- ⬜ **Optional embeddings / RRF** via `DOCDEX_EMBED_CMD` (local-only) for pure
  paraphrase and folder discovery — exact IDs, amounts, dates, and missing-evidence
  honesty stay lexical/structured.
- ⬜ **Conflict v2** — recency/authority weighting on top of Phase 3's grouping,
  still surfacing disagreement rather than auto-resolving.

**Phase 5 — Lifecycle & self-maintenance (M3 → v0.6.0).**  *(Was Phase 4.)* DB
hygiene (`optimize`/`VACUUM`, prune deleted-file rows, rotate `inventory_history`)
first; then the **opt-in auto-archival** tier with the non-negotiable M3 rails —
but only after Phases 3–4, because the auditor's premortem
(`CONTEXT_EFFICIENCY_REVIEW.md` §10) confirms archival needs reliable conflict
grouping, a live/archived index flag separate from source deletion, a last-used
signal, and the shared path-confinement helper that Phase 3's DDX-028 fix
introduces. *"Lifecycle features are where boundary assumptions regress."*

The thematic detail for each milestone (M1–M7) follows.

---

## Forward milestones

Status legend: ⬜ planned · 🟦 in progress · ✅ shipped · ❓ needs a decision (see
**Open questions**).

### M1 — Retrieval quality: match meaning, not just words  *(now v0.5.0 — gated behind the Phase 3 extraction fix)*

The benchmark's 3 misses all live here, and closing them raises field accuracy at
the same token cost — but the round-3 audit was explicit that this must come
**after** field-local extraction (Phase 3 / DDX-029). Aliases layered on today's
value heuristics would widen the candidate pool and multiply the false-found and
false-conflict cases, not reduce them.

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

> *Update (round-3 audit):* the lexical seed shipped in v0.3.0 but had real bugs —
> the wrong source marked "newest" and equivalent-amount false-conflicts. Phase 3
> (v0.4.0) fixes the grouping/normalisation (DDX-031/032); the recency/authority
> weighting below stays v0.5.0.

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

- ⬜ **Storage hygiene — leave no bloat (cross-cutting; ships first).** A
  first-class discipline every feature obeys: SQLite `optimize`/`VACUUM` and pruned
  rows for deleted files; rotate `inventory_history`; **bounded caches** (size cap +
  LRU/age prune, never unbounded growth); **orphan-cache pruning** — drop
  `~/.cache/docdex/<id>` dirs whose project root no longer exists (the cache's
  `meta.json` records that root, so this is safe and automatic); **guaranteed
  ephemeral-temp cleanup** (all scratch under one temp root, removed on success and
  swept on the next run after a crash); a `docdex gc` command and a `doctor`/`status`
  line that reports cache + scratch size so bloat is visible. Modeled on the
  best-in-class — `git gc`, `npm cache verify`/`clean`, `cargo`'s cache GC, XDG cache
  conventions — adapted to docdex's "rebuildable state is disposable" design.
  *Principle: nothing docdex writes should outlive its usefulness or sit multi-GB
  waiting for manual removal.*
- ⬜ **Usage/recency signals** — record what gets retrieved + last-seen, to drive
  both ranking (M2) and the archival rules.
- ⬜ **Opt-in auto-archival engine** — rule evaluation, the `_state/archive/`
  tier, `archive`/`restore` commands, `--dry-run`, audit log. **Depends on M2**
  (it needs recency + same-family supersession detection to know what is "old" or
  "superseded").

### M4 — Budget intelligence: stop guessing how much context to fetch  *(answers the "does the LLM pick the budget?" concern)*

Today `--budget` is a fixed cap (default 3000) and the agent can override it.

> *Update (round-3 audit):* the budget line is **not yet honest** — a tiny
> free-text budget still returns evidence with no drop signal, and reported `used`
> undercounts the real packet. Phase 3 (v0.4.0) makes accounting token-exact
> (DDX-033) before the adaptive work below.

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

### M7 — Generative helpers: auto-curated master index + the OCR runner  *(opt-in engine layer; preserves "docdex never calls an LLM itself")*

Two artifacts users want populated *for* them — the `00_MASTER_INDEX.md` overview
and vision/OCR notes — both need an LLM to **write**, which the North Star says the
core must not do itself. The resolution is **not** to bake an LLM into the core, but
to keep the deterministic core LLM-free and add **one opt-in, pluggable engine hook**
that the *already-running agent* (or a configured CLI/API) drives. A single layer
powers both jobs — and later M6 extraction.

*Why not have docdex build the master index itself (e.g. on install):* it breaks the
North Star — the core stays deterministic, offline, and private, and **never sends
your documents to an LLM on its own**; there is no index to summarise until the first
`sync` (so "on install" is too early); and an auto-written overview that is
confidently wrong is the exact failure v0.4 fought ("never confidently wrong before
more semantically broad"). *Why do it at all:* an empty stub is poor first-run UX, the
curated overview is "the step that turns a search tool into a knowledge base," and a
master index goes stale — an on-demand rebuild fixes that.

- ⬜ **`docdex curate`** — turnkey master-index build. Assembles the file map +
  per-folder snippets + a token budget, then either (a) prints the exact instruction
  for the already-running agent to write `00_MASTER_INDEX.md` (default — no LLM inside
  docdex), or (b) if an engine hook is configured, runs it and writes the file
  directly. Before generating, it confirms the operator is running a top-tier reasoning
  model and warns otherwise — it never pins a model ID (see decision #3). Never required;
  everything else works with no master index.
- ⬜ **Staleness nudge** — `status`/`sync` flag an empty or far-behind master index
  and suggest `docdex curate`, so the overview can't silently rot.
- ⬜ **Pluggable engine layer (opt-in, local-first; `DOCDEX_*_CMD` family)** — one
  adapter interface (built-in/offline OCR · Gemini · Claude · OpenAI) shared by the
  **vision/OCR runner** and `curate`. Productizes the external `run_pro.py` — model
  fallback, circuit-breaker, page-render, authoritative no-text verdict — with PDF
  passwords moved out of code into a user secret store. Off by default → the core
  stays deterministic and private.
- ⬜ **Self-cleaning by design — no GB left behind.** The OCR/engine runner treats
  its render cache, exported page images, and per-call session logs as *ephemeral*:
  all scratch lives under one known temp root, is removed automatically once a note
  is written (and swept on the next run if a crash interrupted it), and never
  accumulates. `docdex vision clean` reclaims anything stranded; `doctor` reports
  scratch size. *(Concrete lesson from the v0.4.1 cleanup: an external Gemini OCR
  run left ~11 GB — a render cache plus ~16k session files — sitting in
  `~/.gemini/tmp` long after the notes were finished. The productized runner must
  never strand scratch like that.)*
- ⬜ **Trust rails** — a generated master index is marked machine-written + dated +
  regenerable; `curate` never asserts beyond what the index supports; engine secrets
  never live in the repo.

> Relationship to M6: M6's "contextual chunks" summarise *individual* files for
> indexing; M7 summarises the *whole corpus* (master index) and captions *visual*
> sources (OCR). Same engine layer, different scope.

---

## Decisions & open questions

**Decided**

1. **How smart should the index get about old / superseded information? (M2+M3)** —
   *Decided 2026-06-11:* go all the way to **option (c), opt-in auto-archival**,
   built on **(b) flag-&-rank** as its prerequisite. Hard rails (see M3): it
   archives *index entries* not source files, stays **off by default**, and is
   **fully reversible + audit-logged** with a `--dry-run` preview. M2 ships first.

2. **Should docdex build the master index itself (e.g. on install)? (M7)** —
   *Reasoned 2026-06-17:* **No** to docdex calling an LLM on its own — it breaks the
   North Star (deterministic, offline, private, "never confidently wrong"), and
   nothing is indexed until the first `sync`. **Yes** to making it effortless: a
   one-command, opt-in, agent-driven `docdex curate` + a staleness nudge, sharing the
   same opt-in pluggable engine layer as the OCR runner. "Populated out of the box"
   happens when an engine is configured or the running agent runs `curate`; with
   nothing configured you still get a fresh index and a one-step prompt.

3. **Which engines power the M7 generative layer? (M7)** — *Reasoned 2026-06-17,
   data-backed:* split by task. **Master-index curation → the strongest reasoning model
   available, confirmed at runtime — never a pinned model ID.** Curation is performed by
   whatever agent is driving docdex (could be Codex, Gemini, a small or older model), so
   `curate` does a **pre-flight check that the operator is on a top-tier reasoning model
   and warns if it isn't** ("this looks like <engine> — the master index is high-leverage;
   switch to your strongest reasoning model first?"). The *target class* is the strongest
   reasoning model of the day (Opus 4.8 / Fable 5 at time of writing); writing a model
   string into the tool is forbidden precisely so the guidance survives Opus 5, Fable 6,
   etc. **OCR / vision → Gemini** (`gemini-3.1-pro-preview`, `gemini-3-flash-preview` as
   quota fallback) — top general-purpose frontier model for document parsing (OmniDocBench
   ~90.3, lowest edit distance ~0.115, 1M context, lowest frontier cost), already proven in
   `run_pro.py`. "Antigravity" is Google's agentic IDE, not an OCR engine — out of scope.
   Optional later: a local/offline OCR engine (GLM-OCR / PaddleOCR-VL beat frontier LLMs on
   raw OCR) for fully-private runs.

**Open**

- _(none right now — add here as they arise.)_

---

## Known limitations (honest, current)

- Lexical matching only by default (M1) — "legal name" won't find "Vendor" unless
  the corpus spells it out; pure paraphrase needs a local `DOCDEX_EMBED_CMD`.
  Meaning-aware aliases / stemming / reranking land in v0.5.0.
- Conflict handling is lexical with amount-normalization (M2) — disagreements are
  surfaced with the newest source marked and equivalent amounts merged, but not
  yet authority/recency-*ranked*.
- macOS/Linux only (M5); Windows unverified.

---

## How releases are cut

Trust/correctness first, then retrieval quality, then convenience. Every landed
item updates the `[Unreleased]` section of [`CHANGELOG.md`](CHANGELOG.md) with a
plain-English line. A release tags, pushes, and verifies a clean install from the
built wheel before announcing.
