# docdex — Roadmap

> **This is the living plan.** It is meant to outlive any single work session or
> terminal. When something ships, move it to **Shipped** and tick the box; when a
> new idea or constraint appears, add it under the right milestone. The
> per-release design docs (e.g. [`docs/V0.2_PLAN.md`](docs/V0.2_PLAN.md)) are
> frozen historical records; *this* file is the one that keeps moving.
>
> _Last updated: 2026-08-01 (SHIPPED **v0.5.8 "The three gaps we wrote down"** —
> v0.5.7 stated three gaps rather than closing them, and closing all three turned out
> to be one question asked properly: **what is a field's value, and which field may
> have it?** (1) `Legal name: Beta Holdings Ltd` — the plainest labelled value there
> is — reported "matched, no clear value", because a value had to look like a number, an
> amount, a date or an email. It is now read, but only when a separator presents it as
> the field's value and only for a field known to want a party, and for such a field the
> name beats a number in the same window. (2) A full stop inside a company name no longer
> ends a clause, so `Helios Components Pvt. Ltd.` is finally seen whole — the largest
> blast radius in the release, moving a boundary in 3.1% of real chunks, which is why it
> was a strict xfail rather than a rider on v0.5.7. (3) Which fields may be answered with
> a company is now a **type** question answered from an allow-list: `field_kind()` says
> party / quantity / date / identifier / unknown, and the forty-word deny-list it
> replaces had been letting a company into `Aggregate liability`, `Consideration
> payable`, `Security deposit`, `Royalty` and `Indemnity`. An unfamiliar label now gets
> nothing, and `aliases.json` is how a user says what their own label means. Two defects
> the work itself surfaced: a value lost its unit (`Renewal term: 24 months  Vendor:
> Acme` was answered `24`, because durations were not units and the search for the next
> field's label read "months Vendor" as that label), and a cross-reference could be
> presented as a legal name (the step that picks which chunk to read scanned for numbers,
> and a company name has none — the third place answering "does this text carry a
> value"). Also fixed the benchmark's own blindness — no source was recorded for any
> `~approx` answer, so gate 2's attribution check saw 9 fields of 11 — and the gate rule
> that had made that unfixable: a flat ban on touching the harness is replaced by
> measuring the property it stood in for, that a harness change must not report the
> PREVIOUS release as better. On the real 104,168-chunk corpus all 4 forward name
> readings are correct, in contrast to v0.5.7's 3-of-4 wrong before its corporate-form
> rule. Schema 6 → 7. Both benchmarks unchanged (11/11 at 1,424 tokens); 466 tests, up
> from 427.)_
>
> _Previously: 2026-07-30 (SHIPPED **v0.5.7 "The name before the label"** — the form
> benchmark reads **11/11 for the first time**, at fewer tokens than 10/11 cost.
> Contracts name a company and then say what role it plays ("Helios Components Pvt Ltd
> **as the Vendor**"), and every release until now read a field's value from the text
> AFTER its label, so it found the label, looked forward, saw nothing and reported
> "matched, no clear value". Deferred four times because reading backwards is the
> direction that leaks: from "Payment terms are net-45. Vendor: Acme" a careless
> backward reader hands `net-45` to `Legal name`. Made safe by the shape of what may be
> read — a proper-noun run ending in a legal form, immediately before a required
> connective — so amounts and dates cannot be read this way at all. It needed TWO
> changes, and only measurement showed why: the chunk carrying the benchmark's own
> apposition line was not in a candidate pool of 60, because every candidate ties at
> BM25 0 and the v0.5.2 `has_value` tie-break sorted every chunk containing a digit
> above the one chunk that could answer. Then the real corpus taught it what neither
> review did: of four names it read across 92,709 chunks, three were nonsense from a
> title-cased investor deck and an ALL-CAPS invoice note, where "is this word
> capitalised" carries no information — hence the corporate-form requirement, and hence
> a feature narrower than its name (it reads a corporate ENTITY defined by apposition;
> `IBM as the Vendor` is deliberately missed). 8 defects in the fix itself came out of
> the two review passes. 54 findings: 12 fixed, 5 refuted by measurement. 426 tests, up
> from 388. Three gaps are stated rather than closed, including that a forward-written
> name is still not a value.)_
>
> _Previously: 2026-07-30 (SHIPPED **v0.5.6 "Ten things nobody had looked at"** —
> the first release driven by reviewing the whole product instead of the last change,
> which is why nine of its ten defects had sat in place for several releases. Two gave
> wrong answers on the real corpus: `--folder` went into a SQL `LIKE` unescaped, so
> `--folder "1. Audited_Financials"` also returned an unrelated tree's
> `1. Audited Financials`; and documents reached *through* a declared synonym were then
> judged and labelled as if the synonym had never applied, because three different
> rules answered "did this query ask for an alias". Four more were docdex saying untrue
> things about itself — manufactured conflicts between unrelated facts, an OCR queue
> whose `done` count could never be non-zero while its total shrank as work finished,
> `covid19` read as an identifier, and a health check that hashed one file in fifty
> while printing a line that read as "all verified". Plus `sync --no-hash` leaving the
> index permanently stale (search answering with deleted text), four of six sync stages
> running unlocked, one failing stage cancelling the rest, and an embedding-model swap
> silently mixing two vector spaces. 65 review findings across three passes: 26 fixed,
> 4 refuted by measurement, 35 tracked below. 387 tests, up from 324. Both benchmarks
> unchanged. Two findings are deliberately NOT closed and say so: a genuine conflict
> phrased two ways is now missed, and `has_value` is still close to "contains a digit"
> because it is computed per 1,800-character chunk.)_
>
> _Previously: 2026-07-30 (SHIPPED **v0.5.5 "Advice that works"** — three things
> docdex said about itself were untrue or impossible to act on, all found while
> checking v0.5.4 end to end on the real corpus. The worst made v0.5.4's own fix
> unreachable: `search` refused an empty index and told the user to run `docdex sync`,
> but the rebuild only ran when a document had changed, so a sync over an unchanged
> folder left the index empty and printed the same instruction again — a loop with no
> way out. A rebuild is now decided by whether the index covers the stored text.
> Also: `doctor` counted 19 files it had deliberately skipped for size as *missing
> caches* and turned a healthy corpus red, and extraction errors described a present
> but truncated file as "not found" and an unconfigured password as "incorrect". 311
> tests. The process gap that let this ship — nobody checked that the instruction the
> product prints actually resolves the state it names — is now a required step in
> `docs/RELEASING.md`.)_
>
> _Previously: 2026-07-30 (SHIPPED **v0.5.4 "The upgrade that broke the index"** —
> a regression this project shipped, reported by the user: after v0.5.2 `docdex sync`
> crashed on any index that already existed, and keyword search then returned nothing
> across a 10,498-file corpus without saying so. The new column was never added to an
> existing table, and the drop of the two FTS mirrors was committed before the crash
> that followed it, so each sync destroyed a working index and left an empty one. Now:
> a version change recreates the derived tables, the whole upgrade is one transaction,
> and an empty index is reported by `search` and by a new `doctor` check instead of
> being answered as "no matches". 284 tests. No document text was ever at risk.)_
>
> _Previously: 2026-07-30 (SHIPPED **v0.5.3 "Out of Spotlight"** — docdex's
> plain-text copies of your documents no longer appear in Spotlight/Finder search.
> User-reported; a privacy bug, not clutter. The state dir is now named
> `_state.noindex`, the only mechanism measured to work — the widely-cited
> `.metadata_never_index` marker does NOT. 270 tests. Also added the standard release
> process: `docs/RELEASING.md`, `benchmarks/review_kit.py`, and a preflight gate.)_
>
> _Previously: 2026-07-30 (SHIPPED **v0.5.2 "Form filling that understands the
> words"** — form fields now read a value from a label written as a different
> inflection or a declared synonym, in strict exact→stem→synonym precedence, with
> anything non-literal tagged `~approx`; and a value-bearing chunk can no longer be
> buried by chunks that repeat the label without answering. Benchmark **10/11** at
> fewer tokens. 259 tests. Remaining form gap: a value written BEFORE its label
> ("… as the Vendor") → v0.5.3.)_
>
> _Previously: 2026-07-29 (SHIPPED **v0.5.1 "Stemming that doesn't lose the
> answer"** — fixes a precision collapse in v0.5.0 where stemming a selective
> literal term flattened its IDF and buried the value-bearing chunk; dual FTS
> mirrors with max-score fusion. Benchmark 8/11 → **9/11** fields, the first
> coverage gain since the benchmark was written. 240 tests, new retrieval property
> tests + a release QA gate. Next: **v0.5.2** meaning-aware form filling (the two
> deferred form-field pieces), then **v0.5.3** optional embeddings/RRF.)_

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
  `../docdex-qa/v0.2.0/`. The FTS5 engine was validated (flat ~36 ms
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
  `../docdex-qa/v0.3.0/`. The **speed fix is confirmed** (50k-file packet
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
- **v0.5.0 — "Meaning-aware search"** (2026-07-22): **Phase 4 / M1** — docdex now
  matches meaning, not just exact words. Porter **stemming** (`governing`↔
  `governed`), user-defined **synonyms** (`.docdex/aliases.json`, free-text), a
  **utility reranker** (floats the answer-bearing excerpt first), and **conflict
  v2** (date/amount conflicts incl. ISO / day-first / negative amounts; recency +
  a transparent filename authority hint; still surfaced, never auto-resolved).
  `~approx` provenance throughout; values stay literal. Hardened by an adversarial
  external audit (codex / Antigravity-Gemini) that stress-tested the honesty
  guarantees. 226 tests. **The 5th M1 piece — optional embeddings/RRF — is deferred
  to v0.5.1** (off by default anyway); synonym-aware *form-field* value-extraction
  + conflict is deferred to a later pass.
- **v0.5.3 — "Out of Spotlight"** (2026-07-30): user-reported bug — docdex's
  extracted `.txt` copies appeared in Spotlight and Finder search. A privacy defect,
  not clutter: docdex writes a plain-text copy of every document it reads, so in an
  indexed location the full text of confidential files became searchable and entered
  the Spotlight store, and a search returned docdex's copy instead of the real file.
  Fixed by naming the state directory `_state.noindex`. **The obvious fix does not
  work:** measured on macOS 26.5, an empty `.metadata_never_index` marker inside a
  directory leaves a neighbouring file indexed; only a dot-prefixed or `.noindex`
  directory NAME is skipped. Naming it removes the dependency on where state lives —
  the v2 cache under hidden `~/.cache` was already safe by luck (17,317 files absent
  from the index for that reason alone), while the pre-v0.4.1 in-project `_index/`
  layout was fully exposed (164 files, all 164 indexed, 149 returned by a content
  search). Migration is a lossless rename, not a re-extraction. `docdex doctor` now
  reports the guarantee. Also standardised the release process itself
  ([`docs/RELEASING.md`](docs/RELEASING.md), `benchmarks/review_kit.py`, gate 0).
- **v0.5.2 — "Form filling that understands the words"** (2026-07-30): closed the
  two form-field pieces deferred since v0.5.0. A field's label is now located in
  strict precedence — **exact words → different word ending → declared synonym** — so
  a literal label present anywhere always decides and a synonym can never hijack a
  field that already matched. Position-safe via real token offsets (the old
  `rfind()` would have found the stem `govern` inside `government`). Non-literal
  matches are tagged `~approx`, and `_field_values` is synonym-aware so two documents
  labelling the same fact differently are compared rather than each standing
  unchallenged. Also fixed the label-vs-value starvation tracked as an xfail in
  v0.5.1: `chunks.has_value` (schema 4) breaks **ties only** toward a chunk carrying
  a number/date/amount/ID, which is what let 60 label-repeating decoys bury the one
  answering chunk. Those 60 scores were not even equal — they differed in the 9th
  decimal on length-normalisation jitter once IDF collapsed, so "close enough to be
  noise" is now treated as a tie (`SCORE_GRAIN`). Benchmark **9/11 → 10/11 at 1,595 →
  1,428 tokens** (`Renewal term` closed). 259 tests. Remaining: a value written
  *before* its label → v0.5.3.
- **v0.5.1 — "Stemming that doesn't lose the answer"** (2026-07-29): the v0.5.0
  stemming win came with a **precision collapse** nobody caught, because the
  benchmark's headline held at 8/11 while one field was silently traded for
  another. Root cause: porter stemmed index *and* query, so a term's literal form
  was discarded for its stem class — and when the literal form was the selective
  one (`terms` in 1 chunk of 167; stem `term` in 154) its IDF collapsed, the
  value-bearing chunk fell from rank 0 to ~96, and the per-field candidate window
  never saw it. The utility reranker couldn't help: it was starved, not wrong.
  Fixed by indexing the text in **two FTS mirrors** (porter + unicode61) and
  scoring each chunk as **max(exact, stem)** — "strongest evidence in either term
  space" — so stemming can only add reachable evidence, never remove a precise
  word. A naive "literal always first" rule was measured and rejected: it fixes
  one direction and pushes the other direction's answer to rank 102. Benchmark
  8/11 → **9/11** at 1,708 → 1,595 tokens, nothing lost vs *either* prior release.
  External adversarial review (agy/gemini-3.6-flash-high) additionally caught a
  bare `except OperationalError` that would have masked DB corruption as "old
  schema", and score rounding that manufactured ranking ties. 240 tests, incl.
  corpus-independent **property tests** (two of which fail on v0.5.0) and a
  **release QA gate** that fails a release whose new tests don't fail on the base.

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

**Phase 4 — Meaning-aware search + deeper conflict (→ v0.5.0, precision repaired
in v0.5.1).**  *(Was Phase 3; moved one release back, deliberately gated behind
Phase 3.)* Free-text meaning-awareness is done; the two **form-field** pieces below
are what remain, and they are **v0.5.2 ← next**.
- ✅ **Stemming / lemmatisation** (`close`/`closed`, `governing`/`governed`) —
  FTS5 porter tokenizer + a vendored Python Porter as the authoritative match
  check; recall-favoring, with `~approx` provenance tags so the agent can verify.
  **v0.5.1 repaired its precision cost**: stemming index *and* query discarded a
  term's literal form for its stem class, so a selective literal (`terms`, 1 chunk
  of 167) inherited the IDF of a corpus-common stem (`term`, 154) and the
  value-bearing chunk fell out of the candidate window. Now indexed in two mirrors
  (porter + unicode61) and scored `max(exact, stem)` — recall-only, never
  precision-destroying.
- ✅ **Field-alias registry** ("legal name" → "Vendor") — user-owned
  `.docdex/aliases.json` (curated editable starter, off when deleted),
  deterministic, contiguous-phrase, `~approx`-tagged, in `--explain`, never
  fabricates. **Free-text search/context** (piece 2).
- ✅ **Synonym-aware form field-value extraction + conflict detection** — shipped
  v0.5.2. A field reads its value after a declared-synonym label (phrase-level, as a
  contiguous stemmed run — `legal name` has no term-by-term correspondence with
  `vendor`), and `_field_values` is synonym-aware so two documents labelling the same
  fact differently are compared instead of each standing unchallenged. Non-literal
  matches are tagged `~approx`.
- ✅ **Utility reranker** — evidence ordered by task utility (value-bearing +
  query-term coverage, source diversity via MAX_PER_SOURCE) over raw BM25 term
  frequency; deterministic, always on. The precision counterweight to
  stemming/alias recall. (piece 3)
- ✅ **Stem-aware form field-value extraction** — shipped v0.5.2 alongside the
  synonym piece, as predicted (both rework the same code). Position-safe via real
  token offsets: the old `rfind()` substring search would have located the stem
  `govern` inside `government`. Strict exact→stem→synonym precedence, so a literal
  label present anywhere always decides.
- ✅ **Apposition: a value written BEFORE its label** — shipped v0.5.7; took the
  form benchmark to 11/11. Reads a corporate ENTITY defined by apposition, behind a
  required connective; see `docdex-qa/v0.5.7/ADJUDICATION.md` for why it is narrower
  than the name suggests. Superseded text below kept for the record:
- ✅ (was) — "Helios Components Pvt Ltd
  **as the Vendor**", "Acme (the **Supplier**)". The standard way contracts name a
  party, and the benchmark's last miss (`Legal name`). **→ v0.5.7** (v0.5.4 went to
  repairing the schema-upgrade regression, v0.5.5 to the three false or unactionable
  things that repair turned out to be saying, and v0.5.6 to the ten defects a
  whole-product review found once someone finally looked past the last diff). Needs a required
  connective, a bounded lookback and a clause-boundary stop: unbounded backwards
  reading is the DDX-029 cross-field leakage class ("Payment terms are net-45.
  Vendor: Acme" would hand `net-45` to `Legal name`), so it gets its own change and
  its own review rather than riding along with something else.
- ⬜ **Optional embeddings / RRF** via `DOCDEX_EMBED_CMD` (local-only) for pure
  paraphrase and folder discovery — exact IDs, amounts, dates, and missing-evidence
  honesty stay lexical/structured. **→ v0.5.9 (NEXT)** (was v0.5.1, which the precision fix took,
  then pushed by the Spotlight fix, the schema-upgrade repair, its follow-up, the
  whole-product review, apposition, and closing apposition's three stated gaps); off by
  default, needs a local embedder. Note v0.5.1 already built the
  two-ranking fusion plumbing this will extend — a vector ranking becomes a third
  input to the same merge.
- ✅ **Conflict v2** — recency/authority weighting on top of Phase 3's grouping,
  still surfacing disagreement rather than auto-resolving. Shipped in v0.5.0
  (dates incl. ISO/day-first, negative amounts, deterministic tiebreak).

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

- ✅ **Field-alias / synonym registry** — a small, user-extensible map so
  `Legal name → {Vendor, Supplier, Party, legal entity}`. Deterministic.
  Free-text search/context shipped; synonym-aware form-field extraction deferred to conflict v2.
- ✅ **Stemming + light lemmatisation** so `governing/governed/governs` collide.
- ✅ **Reranking of the top-N candidates** — built-in deterministic utility rerank
  (value-bearing + coverage) shipped (piece 3). A pluggable `DOCDEX_RERANK_CMD`
  (off by default) stays an optional later add-on.
- ⬜ **Hybrid lexical + vector fusion** (Reciprocal Rank Fusion) when
  `DOCDEX_EMBED_CMD` is set, so a real embedding model can bridge pure paraphrase
  while BM25 remains the dependency-free default.

### M2 — Corpus intelligence: freshness, conflicts, supersession  *(answers the "30 then 40 deals" question)*

Make docdex aware that documents change and disagree.

> *Update (round-3 audit):* the lexical seed shipped in v0.3.0 but had real bugs —
> the wrong source marked "newest" and equivalent-amount false-conflicts. Phase 3
> (v0.4.0) fixes the grouping/normalisation (DDX-031/032); the recency/authority
> weighting below stays v0.5.0.

- ✅ **Show recency on every excerpt** — each evidence line carries its source and
  `mtime`, so "which is newer" is visible at a glance (v0.5.0).
- ✅ **Conflict flagging** — when ≥2 sources give different values for the same
  question/field, the packet says so explicitly ("⚠ 2 sources disagree: *30*
  in `old.xlsx` (Jan), *40* in `new.xlsx` (Mar)") instead of silently picking one.
  Seeded v0.3.0, grouping/normalisation fixed v0.4.0 (DDX-031/032), dates +
  recency/authority weighting v0.5.0.
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

- Lexical matching by default (M1) — stemming, declared synonyms and utility
  reranking shipped in v0.5.0 (precision repaired in v0.5.1), so `governing` finds
  `governed` and a declared `legal name → Vendor` widens *free-text* search. Pure
  paraphrase with no declared synonym still needs a local `DOCDEX_EMBED_CMD`
  (v0.5.3).
- **Form-field** matching is meaning-aware since v0.5.2 — a field reads its value
  from a label written as a different inflection or a declared synonym, tagged
  `~approx`. The remaining gap is a value written **before** its label ("… as the
  Vendor"), which is the benchmark's last miss and **v0.5.4**.
- **Values are typed** — numbers, dates, amounts, IDs, emails. A value docdex cannot
  type (a company name) is shown under "needs follow-up" with the text following its
  label, never asserted as a confident answer.
- Conflict handling is lexical with amount/date normalization and recency+authority
  weighting (M2) — disagreements are surfaced, never auto-resolved.
- macOS/Linux only (M5); Windows unverified.

---

## How releases are cut

Trust/correctness first, then retrieval quality, then convenience. Every landed
item updates the `[Unreleased]` section of [`CHANGELOG.md`](CHANGELOG.md) with a
plain-English line. A release tags, pushes, and verifies a clean install from the
built wheel before announcing.

**Every release records every benchmark suite** (added v0.5.1):

```
python3 benchmarks/bench_all.py record          # append this release to the history
python3 benchmarks/bench_all.py sweep <ref>...  # backfill / re-measure old releases
```

Results land in [`benchmarks/HISTORY.json`](benchmarks/HISTORY.json) and a generated
[`benchmarks/HISTORY.md`](benchmarks/HISTORY.md) table, one row per release, with a
regression list computed between consecutive releases. `sweep` overlays **today's**
`benchmarks/` onto each checked-out release so the harness, corpus and scoring are
identical everywhere and only `src/` varies — otherwise the comparison measures the
oracle, not the product. *This was added because Suite A had not been re-run since
v0.1.1: five releases with an unverified headline in the README, and no trend that
could show when anything moved.*

**Every release runs the QA gate before tagging** (added v0.5.1):

```
python3 benchmarks/qa_release.py --base <previous tag>
```

Four gates, all of which must pass: the suite is green on HEAD; the form benchmark
is compared to the previous release **field by field** (a per-field diff, because
v0.5.0's headline held at 8/11 while silently trading one field for another — a
total-only check is blind to that); the benchmark is byte-identical across two
runs; and — the unusual one — **the release's new tests are run against the base
tree and at least one must fail there.** A regression test that passes on the code
it was written to catch proves nothing, so the gate refuses to accept one. Gate 3
measures the tests; the others measure the product.

Substantive changes also get an **external cross-family adversarial review** before
release (see the v0.5.1 entry): a Claude subagent reviewing Claude-written code
shares its blind spots, so the reviewer is a different model family, framed as a
devil's advocate. Its findings are **adjudicated against the code**, not accepted —
of 6 findings on the v0.5.1 *diff*, one CRITICAL and two MAJOR were refuted by
reading the code and constructing counter-corpora, and two real bugs were fixed
with discriminating tests.

**Review the tests, not just the code.** v0.5.1 added a second review pass aimed at
the QA suite itself ("would these tests catch a variant of this bug?"). It was the
higher-yield of the two: it found that a `COUNT(*)` assertion on an external-content
FTS table proves nothing (it reads the content table, so an empty shadow index
still reports healthy rows), that a determinism test comparing two calls in one
process cannot see hash-order dependence, that an indexing-order test was vacuous
because the sync sorts paths, and that an honesty test passed when the field
vanished entirely. It also produced two corpora that exposed **real pre-existing
bugs** (now tracked as `xfail(strict=True)`). Adjudicate these the same way — of 28
findings, several were hypotheticals about arbitrary future edits rather than
present defects.

### QA debt (known blind spots)

Honest list of what the suite still cannot see, so it is not mistaken for coverage:

- ⬜ **Value-attribution oracle** — the benchmark credits a field when its value
  appears *anywhere* in the packet, so a value that is right by accident counts and
  an equivalently-rendered value (`₹6.5 cr` vs `INR 6.5 crore`) counts as lost. The
  per-field section/source diff in the gate mitigates but does not replace typed
  value comparison.
- ⬜ **Real document structure** — fixtures are simple paragraphs, one-sheet
  workbooks and linear PDFs. Nothing covers `.docx` headers/text boxes/tracked
  changes, multi-sheet or merged-cell workbooks, multi-column PDFs, or scans. Needs
  checked-in golden files asserting both extracted text and packet provenance.
- ⬜ **Scale tier** — no automated 10k-file check for index size, latency, lock
  duration, or rebuild cost after a one-file edit; v0.5.1's scale numbers were
  measured by hand on the real corpus.
- ⬜ **Unicode breadth** — NFD/NFC (tracked as a failing `xfail`), plus CJK
  segmentation, RTL and combining marks are untested.

**Raised by the v0.5.4 review and deliberately deferred** (recorded so they are debt,
not oversights — each was reproduced or reasoned about, none is fixed):

- ⬜ **FTS index corruption, as distinct from an empty one.** v0.5.4 detects an index
  that is empty or incompletely built. It cannot detect a mirror whose postings point
  at the *wrong* content rows, which would return a confident hit citing the wrong
  document — worse than any failure v0.5.4 fixes. FTS5 offers
  `INSERT INTO chunks_fts(chunks_fts) VALUES('integrity-check')`; the open question is
  cost on a 92k-chunk index, so it likely belongs behind a `docdex doctor --deep` flag
  rather than in the default run.
- ⬜ **Gate 3 can be satisfied by a test that discriminates nothing.** A single
  `assert __version__ == "0.5.4"` fails on the base tree and would satisfy the gate on
  its own. The real fix is a declared manifest of the node IDs that must fail on base
  for each claimed behaviour, checked against the archive's adjudication. Until then
  gate 3 is a floor, not a proof, and the adjudication carries the argument.
- ⬜ **A changed citation is a note, not a failure.** Gate 2 fails on a lost field but
  only warns when a field's *cited source* changes, so a correct value attributed to
  the wrong document can pass. Making it a failure needs an adjudicated allowlist for
  intentional improvements, since a better source legitimately changes the citation.
- ⬜ **Determinism is tested on a corpus too small to tie.** Gate 4 compares packet
  bytes across hash seeds, but the fixture never produces the large blocks of
  equal-scoring chunks where set-iteration order would actually show. Needs a stress
  corpus of hundreds of tied chunks across several queries.

**Raised by the v0.5.7 round and deliberately deferred:**

- ⬜ **A forward reading of a SYNONYM window can return a clause, not a value.**
  Pre-existing, and v0.5.7 made it visible: on the real partner agreement `Legal name`
  matches the alias "Supplier" and the window after it happens to contain "sixty (60)
  days", so the field answers with a sentence about invoicing — and because a forward
  reading always outranks apposition, the correct party name never gets a chance in
  that chunk. Unchanged from v0.5.6 (the forward path was not touched), but it caps
  what apposition can achieve in practice: it answered `Legal name` in 4 real documents,
  all correctly ("Helios Components Private Limited"), and could have in more. Wants a
  plausibility check on a forward window — a value region that reads as a full clause
  is probably not a field value.

- ✅ **A forward-written name is now a value** — closed in v0.5.8. `Legal name: Beta
  Holdings Ltd` is read, but only when a separator presents it as the field's value and
  only for a field known to want a party, which is also what keeps "Payment terms: See
  Schedule B" from answering "Schedule B" (a schedule reference is not a company). All 4
  readings on the real corpus are correct.
- ✅ **Clause splitting no longer cuts abbreviations** — closed in v0.5.8. `Pvt.`,
  `Ltd.`, `Co.`, `Inc.` keep a clause going when a lowercase word or another such
  abbreviation follows. Was a strict xfail; the change moved a boundary in 3.1% of real
  chunks, which is why it needed its own release.
- ✅ **A field's expected TYPE is a registry, not a word list** — closed in v0.5.8.
  `field_kind()` answers party / quantity / date / identifier / unknown, from the
  label's words or from a declared synonym, and only a `party` field may be answered
  with a company. The deny-list it replaces allowed a company into `Aggregate
  liability`, `Consideration payable`, `Security deposit`, `Royalty` and `Indemnity`.
  Refusing a value of the WRONG kind (`Effective date: 45`) is still M6.
- ✅ **The benchmark harness now records a source for approximate answers** — closed in
  v0.5.8, together with the gate rule that had frozen it. Gate 0's flat ban on touching
  the harness is replaced by the property it stood in for: gate 2 benchmarks the base
  tree under both harnesses and fails a change that reports the previous release as
  *better*. The harness also records the `~approx` tag now, so an answer that was read
  from a literal label and is now read through a synonym fails gate 2.
- ⬜ **`has_value` moved the wrong way.** Recognising apposition-defined parties added
  879 of 92,709 chunks (95.9% → 96.9%), against the v0.5.6 debt item saying this signal
  already barely discriminates. v0.5.8 added labelled names on top: of 104,168 real
  chunks, 112 are value-bearing only because of apposition and 73 only because of a
  labelled name — 0.18% between them, so the aggregate story is unchanged and both were
  necessary for findability. It makes the sharpening work more urgent, not less.

**Raised by the v0.5.8 round and deliberately deferred:**

- ⬜ **The CONFLICT path still accepts a cross-reference as a value.** v0.5.8 made
  `first_real_value` the single answer to "is there a value here" for the index signal,
  the field answer and the per-field conflict list — but not for `_value_and_position`,
  which finds the value nearest a query term in free-text conflict detection. Seen live
  on the real corpus immediately after the release: filling `Governing law` reported
  *"2 values disagree — `1996` vs `12.1`"*, which is a statute year against a clause
  number. A fabricated conflict is on the "never confidently wrong" list. Left out of
  v0.5.8 because it changes free-text conflict grouping, which wants its own measurement;
  the field-level path is fixed and this one is not, and the changelog says so.
- ⬜ **A forward reading of a synonym window still returns a clause, not a value** — the
  v0.5.7 item below, with a fresh real example: `Governing law` on the real corpus answers
  *"AND DISPUTE RESOLUTION 12.1 Governing law and jurisdiction The provisions of this
  Agreement shall, in all respects, be governed by…"*. The window is the field's own, so
  this is not leakage; it is that no value was recognised inside it and the whole window
  was shown as though one had been. A plausibility check — a value region that reads as a
  full clause is probably not a field value — is the shape of the fix.

- ⬜ **The free-text sort key still recognises only numbers.** `_pick_field_hit` now
  asks the answer path whether a chunk carries a value for a *field*, which is what
  stopped a cross-reference being presented as a legal name. `_utility` asks the same
  question for *query terms* in free-text search and still scans for `VALUE_RE` only, so
  a chunk whose only answer is a company name ranks as though it had none. Not changed
  in v0.5.8 because it moves evidence ranking for every search and needs its own
  measurement against suite A.
- ⬜ **A reference abbreviation still splits a clause from its number.** The abbreviation
  rule covers company forms only, so "Invoice No. 42" is still cut into "Invoice No."
  and "42" — the label loses its value in exactly the way `Pvt. Ltd.` lost its name.
  Continuing on a *digit* after `No.`, `Sr.`, `Cl.`, `Art.` is a second rule with its own
  blast radius. Landed as a strict xfail.
- ⬜ **The gate does not check its own rules for having got weaker.** v0.5.8 replaced a
  gate-0 prohibition with a gate-2 measurement, which is a stronger check — but nothing
  in the gate would have objected if it were a weaker one. The oracle is now verified
  against the base; `qa_release.py` itself is not.
- ⬜ **An ALL-CAPS or title-cased abbreviated name is still unread.** The boundary rule
  decides "did a new sentence start" by whether the next word is lowercase, and in text
  that is entirely upper-case that carries no information, so `PVT. LTD. AS THE VENDOR`
  stays split. Deliberate: splitting is the safe direction, and it produces no reading
  rather than a wrong one.

**Raised by the v0.5.6 whole-product review and deliberately deferred** (65 findings
across three passes — see `docdex-qa/v0.5.6/ADJUDICATION.md`; 26 fixed, 4 refuted by
measurement, these tracked). None is a known wrong answer on the current corpus; each
is a way a future one could go unnoticed:

- ⬜ **Conflict identity needs a field label, not neighbouring words.** v0.5.6 groups a
  value by the words on either side of it, which fixes fabricated conflicts but now
  MISSES a genuine one phrased two ways: `revenue was 5 crore` against `revenue totaled
  9 crore` — `was` is a function word and `totaled` is not, so the keys differ. Both
  values are still shown as evidence; what is lost is the explicit disagreement. Wants
  a real field-label extraction, the same machinery apposition (v0.5.7) needs.
- ⬜ **`has_value` is still ≈ "contains a digit".** Excluding document numbering moved
  519 of 92,526 real chunks (96.6% → 96.0%; agreement with "contains a digit" 99.96% →
  99.40%). The pattern is not the problem — the flag is computed per 1,800-character
  chunk, and almost any chunk that size contains some genuine number. A sharper signal
  needs a window around the candidate label, not the whole chunk.
- ⬜ **OCR notes carry no identity for their source.** Editing a scanned file leaves its
  old note — and its old OCR text — counted as current and searchable, because
  completion is keyed on the note's existence alone. Pre-existing. Needs the source's
  hash written into the note and compared when the queue is rebuilt.
- ⬜ **The semantic index and its manifest are two separate replacements.** A crash
  between them leaves a manifest describing records the index does not contain, and the
  incremental reuse path then trusts it. Raised independently by both review passes.
  Wants one atomic swap (a versioned directory plus a pointer), not two `os.replace`
  calls.
- ⬜ **An embedding model can change behind an unchanged command.** v0.5.6 fingerprints
  `DOCDEX_EMBED_CMD`, which catches a changed command but not a changed model *behind*
  the same command. Hashing the command is what can be known cheaply and portably; the
  limit is documented rather than implied away. A real fix needs the embedder to report
  its own model identity.
- ⬜ **Gate 2 still compares suite A per method, not per query**, and treats a changed
  source attribution as a note rather than a failure — so a lost fact offset by a newly
  found one passes, and a correct value can arrive with the wrong provenance. Raised for
  the second release running; needs per-query expectations plumbed through the harness.
- ⬜ **The benchmark oracle is not independently pinned.** v0.5.6 fails the release if
  the harness files changed since the base, which is a guard, not a solution: the
  harness is still the same code on both sides of the comparison. Wants oracle
  self-tests over near-miss IDs, amounts, dates and sources.
- ⬜ **No golden corpus of real document structures.** Release tests use small Markdown,
  PNG and synthetic PDF fixtures, so a regression that ignored an `.xlsx` second sheet,
  `.docx` text boxes, merged cells, a right-hand PDF column, or OCR text past a chunk
  boundary would pass everything. Wants a compact corpus with exact expected literals.
- ⬜ **No scale or platform coverage.** Order-dependent ranking above ~10,000 tied
  files, SQLite parameter limits at large candidate counts, and macOS-versus-Linux FTS
  differences are all invisible to a small single-environment suite.
- ⬜ **Determinism is certified from one fixture and one clock.** Gate 4 hashes one
  corpus and query, so nondeterminism confined to alias expansion, tied scores,
  conflicts, folder filtering or truncation boundaries would not show; and all runs
  share a wall clock, so a new date dependence passes today and breaks tomorrow.

**Raised by the v0.5.5 review and deliberately deferred** (each reproduced by
measurement — see `docdex-qa/v0.5.5/ADJUDICATION.md` — and each pre-existing, not
introduced by that release):

- ⬜ **Nothing verifies the index against the caches.** Every health check compares the
  FTS mirrors to the `chunks` table, so a `chunks` that has itself lost rows reads as
  perfectly healthy: the mirrors match it, and the missing documents are silently
  unfindable. Reproduced with a direct `DELETE FROM chunks`, so it needs the database
  edited behind docdex's back — but that is also what a partial disk failure looks like.
  Belongs with the `doctor --deep` item above: compare `files`/`chunks` against the
  `.txt` caches that are the actual source of truth. The obvious cheap check
  (`COUNT(DISTINCT rel)` in `chunks` versus `files`) is a trap — it fires forever on any
  file whose chunks all fall under the 3-character floor.
- ⬜ **A document over the size cap keeps answering with its old text.** If a file was
  cached while under `max_extract_mb` and is then edited past it, `sync` records
  `skipped` and returns before touching the cache it wrote earlier, while the lexical
  index keeps serving the previous contents — a stale answer with no marker saying so.
  Measured (`evidence/adjudicate.py`, case B). Options: drop the stale cache when a file
  becomes skipped (loses searchability, but honestly), or keep it and mark the age.
- ⬜ **Gate 2 hides a conflict promoted to an answer.** A field that moves from the
  `conflicts` section on base to `answers` on HEAD passes, because only demotions out of
  `answers` are checked — so hiding a disagreement reads as an improvement. Needs a
  conflict-resolution oracle to distinguish a legitimate resolution from a suppressed
  one; until then the section transition is invisible to the gate.
- ⬜ **Gate 2 compares suite A per method, not per query.** A lost fact offset by a newly
  found one leaves recall unchanged and passes. The label now says so, but the fix is a
  per-query comparison of expected value, source and absence state.
- ⬜ **The fixed oracle is not immutable.** `sweep`/`bench_all` overlay today's
  `benchmarks/` onto every release so only `src/` differs — which also means a loosened
  `covered()` is applied to base and HEAD alike, and a real regression disappears.
  Wants oracle self-tests over near-miss IDs, amounts, dates and sources, so the
  measuring stick is itself measured.
- ⬜ **Determinism is only checked within one day and one clock.** Gate 4 compares packet
  bytes across runs and hash seeds, but all runs share a wall clock, so a new
  `date.today()` dependence in the packet would pass today and break tomorrow. Needs
  injected clock values and time zones, or no clock access on the packet path at all.
- ⬜ **Nothing runs on Linux.** The gate is a local macOS script, and the product claims
  macOS and Linux with their native SQLite/FTS5 builds. A platform-specific retrieval
  difference would ship unseen. Needs CI, not a bigger script.
