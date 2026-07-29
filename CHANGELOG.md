# Changelog

All notable changes to docdex are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and version numbers follow
[Semantic Versioning](https://semver.org/).

**Readability rule:** entries are written for humans first. Anything that is
unavoidably technical gets a plain-English *"In plain terms"* line so you can
tell what changed and why without reading the code.

## [Unreleased]

Next: **v0.5.4** — reading a value that sits *before* its label ("Helios Components
Pvt Ltd **as the Vendor**"), the last form-filling gap; then optional embeddings /
RRF via `DOCDEX_EMBED_CMD`. See [ROADMAP.md](ROADMAP.md).

## [0.5.3] — 2026-07-30 — "Out of Spotlight"

**docdex's copies of your documents no longer show up in Spotlight or Finder search.**
Reported as a bug, and it was a privacy one, not just clutter.

### Fixed

- **The extracted text of your documents is no longer searchable by the OS.** To
  search a folder cheaply, docdex saves a **plain-text copy of every document it
  reads** — including PDFs and Word files whose contents macOS previously could not
  see inside. Where those copies sat in a place the system indexes, two things went
  wrong: searching a phrase from a contract returned *docdex's copy* instead of the
  real document, and the full text of private documents became searchable and was
  stored in the system's search index. *In plain terms:* docdex was quietly making
  the inside of your confidential files searchable to anything on the Mac. It now
  keeps that text in a directory the OS is told to skip.
  - **Your own documents are unaffected and stay searchable.** The change only ever
    applies to docdex's own storage. Making your real files unfindable would be a
    worse bug than this one.
  - **Nothing is re-read from your documents.** The upgrade *renames* one directory,
    which is instant, rather than re-extracting your corpus.
  - Measured before and after on a folder the system does index: the original
    document is still found, docdex's copy is not.

### Notes

- **What actually works, because the popular answer doesn't.** The widely-cited fix
  is an empty `.metadata_never_index` file inside the directory. Measured on macOS
  26.5, that **does not work** — a file next to it was indexed anyway. What does work
  is the directory's own name: a name starting with `.` or ending in `.noindex` is
  skipped. So the protection is in the name (`_state.noindex`), which holds no matter
  where the directory lives.
- **Why it had not been noticed.** The current layout keeps this text under
  `~/.cache/docdex/`, and `~/.cache` starts with a dot, so it was already being
  skipped — by luck, not design. On this machine 17,317 cached files were absent from
  the search index for that reason alone. The older layout (before v0.4.1) kept the
  same text in a plainly-visible folder *inside* the project, and there it was fully
  exposed: 164 such files were all 164 in the index, with a content search returning
  149 of them. Anyone still on that layout, or pointing `DOCDEX_CACHE_DIR` at a
  normal folder, was exposed. The fix no longer depends on luck.
- **`docdex doctor` now tells you.** A new line reports whether your extracted text
  is actually hidden from desktop search, so you can confirm it rather than trust it.
- Already-indexed copies from before this fix are not retroactively removed from the
  system's index; `docdex purge --state-only` followed by `docdex sync` clears them.
- 270 tests (8 new).

## [0.5.2] — 2026-07-30 — "Form filling that understands the words"

Filling a form now works when your documents **don't use the form's wording**. A
field called `Governing law` reads its value from "governed by the laws of…", and a
field called `Legal name` reads one from a clause that says `Vendor` — provided you
declared that synonym. The form benchmark reaches **10 of 11 fields** (from 9) using
**fewer** tokens than before, 1,433 against 1,595.

### Added

- **A form field finds its value even when the document words it differently.**
  Until now form fields were matched literally: `Governing law` only ever matched
  the exact words "governing law", so a contract saying "governed by the laws of
  Karnataka" gave nothing, and `Legal name` could not use a clause saying `Vendor`
  even with that synonym declared in `.docdex/aliases.json`. Now the label is looked
  for in three passes, in this order: **the exact words, then a different word
  ending, then a synonym you declared.** The exact wording always wins when it is
  present anywhere, so a synonym can never hijack a field that already matched
  properly. *In plain terms:* docdex used to need your documents to use the form's
  exact words; now it can bridge "governed by" and, if you tell it they mean the
  same thing, "Vendor" and "Legal name".
- **Anything matched loosely is flagged.** A value read from a synonym or a
  different word ending is marked **`~approx`** on its own line, so an agent can
  tell "the document literally said this" from "the document said something I was
  told means the same thing." An exactly-matched label is never flagged, which is
  what keeps the flag meaningful.
- **Disagreements are now spotted across synonyms too.** If one document labels a
  value `Vendor` and another labels it `Legal name`, they are compared against each
  other instead of each being treated as its own unchallenged fact. A disagreement
  hidden is a disagreement the agent states confidently.

### Fixed

- **A label repeated without a value no longer buries the one document that answers
  you.** If sixty documents say "Payment terms are described in the annexure" and
  one says "Payment terms are net-45", all sixty-one look equally relevant to a
  keyword index — so docdex read six of them in alphabetical order and the one with
  the answer, sixty-first in line, was never opened. It now records for each passage
  whether it contains an actual number, date, amount or ID, and prefers those
  passages **when and only when relevance is otherwise a tie**. A passage that is
  genuinely more relevant is never displaced. *In plain terms:* when docdex can't
  tell several documents apart, it now reads the ones that actually contain a value
  first. This closed the benchmark's `Renewal term` miss.
  - Worth recording: those sixty-one scores were not actually *equal* — they
    differed in the ninth decimal place, purely from one sentence being slightly
    longer than another. When a word appears in nearly every document its ranking
    weight collapses to almost nothing and that jitter is all that is left, so
    "close enough to be noise" is treated as a tie (`SCORE_GRAIN`). Real matches
    score in the ones and tens, far above that.
- **Locating a label is position-safe.** Label matching previously used a plain
  substring search, which is unsafe once word endings are involved — the stem
  `govern` would be "found" inside `government`. It now works on real token
  positions.
- **A repeated label word no longer skips the value.** If a field's own words
  appeared again later in the same sentence — "Payment terms: Net-45 and general
  terms apply" — docdex started reading after the *second* "terms" and returned
  "apply", losing a value that was right there and correctly labelled. It now reads
  from the first complete appearance of the label. *(Pre-existing: the old substring
  search took the last occurrence too. Found by external review of this release.)*
- **Label precedence now holds across documents, not just within one.** A synonym
  match in a better-ranked document could outrank a word-ending match in a
  worse-ranked one, inverting the exact → word-ending → synonym order this release
  introduced. *(Found by external review.)*
- **Two synonyms in one sentence take the first, as a reader would.** "Vendor: Acme
  Corp, Supplier: Beta Ltd" returned Beta; it now returns Acme. *(Found by external
  review.)*
- **A synonym can no longer put another field's value into a disagreement.** A
  declared synonym can match the start of a different field's label ("Vendor" inside
  "Vendor turnover"), and the conflict check — unlike the answer check — had no guard
  against that, so one field's number could be logged as another field's and reported
  as a disagreement that never existed. Both paths now apply the same guard.
  *(Prompted by external review; the reported case turned out to be already blocked
  by the clause-boundary rule, but the missing guard was real.)*

### Known gaps (tracked)

- **A value written *before* its label is still not read.** Contracts routinely name
  a party as "Helios Components Pvt Ltd **as the Vendor**" or "Acme (the
  **Supplier**)" — the value sits before the label, and docdex only reads what
  follows one. This is the benchmark's last miss (`Legal name`) and the **v0.5.3**
  target. It is deliberately not bolted on here: reading backwards without a
  required connective, a bounded lookback and a clause-boundary stop is exactly the
  cross-field leakage v0.4.0 fixed — "Payment terms are net-45. Vendor: Acme" would
  hand `net-45` to `Legal name`.
- **Values docdex cannot recognise are shown, not asserted.** It types values as
  numbers, dates, amounts, IDs and emails, so a company name is displayed under
  "needs follow-up" with the text following its label, rather than stated as a
  confident answer. Treating any prose after a colon as a value is how a retrieval
  tool starts being confidently wrong.

### Notes

- The index gains one small field per passage, so **the first `sync` after upgrading
  rebuilds the lexical index once**. As in v0.5.1 it re-reads the plain-text copies
  docdex already keeps — nothing is re-extracted from your documents. Until that
  sync runs, search keeps working and simply doesn't have the new tie-break.
- 263 tests (13 new), 2 tracked gaps.
- Reviewed before release by an external cross-family adversarial pass
  (gemini-3.6-flash-high): 6 findings, all adjudicated against the code — one
  CRITICAL and two MAJOR confirmed and fixed above, one MAJOR refuted (it
  assumed single-character label words reach the matcher; they are dropped
  from the label too, so the case works), and one refuted in its specifics
  while exposing a real missing guard. Archived in `docdex-qa/v0.5.2/`.

## [0.5.1] — 2026-07-29 — "Stemming that doesn't lose the answer"

v0.5.0's stemming could **bury the very chunk that held your answer**. This fixes
it, and the form-filling benchmark improves for the first time since it was
written: **9 of 11 fields** recovered, up from 8, at slightly *fewer* tokens.

### Fixed

- **A rare word is no longer flattened into a common one.** v0.5.0 stemmed both
  the index and your query, so a word's literal form was discarded in favour of
  its stem. When the literal form was the *rare, discriminating* one, that
  destroyed the ranking signal: on the benchmark corpus the word `terms` appeared
  in exactly **one** chunk — the one saying `Payment terms are net-45` — while its
  stem `term` appeared in **154 of 167**. So the one chunk that answered the
  question scored ~0 alongside everything else, ranked ~96th, never entered the
  candidate window, and the field came back with no value. Meanwhile the *reverse*
  case (`governing law` finding `governed by the laws of…`) started working — one
  cause, opposite outcomes, decided purely by whether a stem happened to be rare
  or common. *In plain terms:* asking for a distinctive word could return
  everything **except** the document that actually answered you, and which fields
  worked was close to luck. The total looked unchanged (8/11 before and after)
  because one field was silently traded for another — the headline number hid it.
  **How it's fixed:** docdex now indexes your text **twice** — once as written and
  once stemmed — and scores each passage on whichever version gives the stronger
  match. Stemming can now only ever *add* findable evidence; it can no longer take
  a precise word away. Both cases now work together: 8/11 → **9/11 fields**, 1,708
  → 1,595 tokens. The lexical index rebuilds itself once on the next `sync` (no
  action needed).
- **A broken index can no longer masquerade as a healthy one.** While adding the
  second index, a too-broad error handler would have treated *any* SQLite failure —
  corruption, a lock timeout, an I/O error — as "this database is just an older
  version" and quietly answered from the surviving half. Now only a genuinely
  absent second index takes that path; real failures surface. *In plain terms:*
  docdex would rather tell you it's broken than hand you a confident-looking answer
  built on a damaged index. (Caught by external adversarial review before release.)
- **Ranking no longer invents ties.** Relevance scores were rounded to four
  decimals *before* sorting, so two genuinely different scores could tie and be
  reordered alphabetically — putting the less relevant passage first. Sorting now
  uses full precision and rounds only for display.

### Added

- **Retrieval property tests** (`tests/test_retrieval_properties.py`) — rules that
  must hold for *any* corpus, not just a hand-built one: a word occurring in a
  single passage must always be findable; adding files that don't contain your
  answer must never take the answer away; ranking must not depend on the order
  files were indexed. The two that describe this release's bug **fail on v0.5.0**
  and pass here, which is how we know they're real tests and not decoration.
- **A release QA gate** (`benchmarks/qa_release.py`) — run before tagging. Five
  gates: the suite is green (verified from JUnit XML, so a collection error or an
  empty run can't pass as "green"); the benchmark is compared to the previous
  release **field by field** — value, section *and* cited source, plus honest
  absent-field handling and a token ceiling — rather than by the headline count
  that hid this bug; the packet's hash is stable across runs *and across
  `PYTHONHASHSEED` values*, so ranking can't depend on dict iteration order; and
  the verdict states whether it verified a commit or a dirty working tree.
  Unusually, it also runs the release's *new* tests against the *old* code and
  **fails the release unless at least one of them fails there on an assertion**. A
  regression test that passes on the code it was written to catch is worthless —
  and one that merely errors because it references new internals proves nothing
  either, so setup errors don't satisfy the gate.

### Known gaps (tracked, not fixed here)

Two real gaps were found by adversarial review of this release's *test suite*.
Both reproduce identically on v0.5.0 and v0.5.1 — they are pre-existing, not
caused by this fix — and both are now `xfail(strict=True)` tests, so they stay
visible and fail loudly the moment they start passing:

- **A label without a value can still crowd out the answer.** With 60 documents
  containing the exact phrase "Payment terms" but no value, the one document
  saying `net-45` is buried: all 61 match the label equally well, and ranking
  cannot see which one carries an actual value. This is the label-vs-value gap
  behind the benchmark's 2 remaining misses, and it is what **v0.5.2** targets.
- **A query typed with decomposed accents doesn't match composed text.** `Échéance`
  written as `e` + a combining accent (NFD) finds nothing in a document storing it
  as a single character (NFC). Fixing it means normalising both the cached text and
  the query, which changes indexed content — so it is deferred to a release that
  already carries an index rebuild.

Also known and not covered by any test: the benchmark credits a field when its
value appears *anywhere* in the packet, so a value that is right by accident counts;
and no automated tier exercises real document structure (multi-sheet workbooks,
`.docx` headers and text boxes, merged cells) or 10k-file scale — the scale numbers
above were measured by hand.

### Notes

- **What the second index costs**, measured on a real 10,486-file / 92,433-chunk
  corpus (not a synthetic one — synthetic text has a much smaller vocabulary and
  flatters these numbers):
  - **Disk: +26%** for the lexical index — 277 MB → 349 MB. Both mirrors share the
    stored text, so this is the inverted index only. (A 4,000-file synthetic corpus
    showed just +10%; real documents have far richer vocabulary.)
  - **Query: 1.3×–1.9× slower**, worst observed 80 ms vs 41 ms, typical 25–46 ms.
    Still well inside the "flat, sub-100 ms at scale" property the FTS5 engine was
    adopted for.
  - **One-time migration:** the first `sync` after upgrading rebuilds the lexical
    index once from the `.txt` caches (they remain the source of truth, so nothing
    is re-extracted from your documents). Building the second mirror itself takes
    ~3 s at this scale; the surrounding reindex is the bulk of the time. Until that
    sync runs, `search` keeps working against the old single mirror.
- 240 tests (14 new).

## [0.5.0] — 2026-07-22 — "Meaning-aware search"

docdex now matches *meaning*, not just exact words: Porter **stemming**
(`governing`↔`governed`), user-defined **synonyms** (`legal name`→`vendor`), a
**utility reranker** that floats the answer-bearing excerpt to the top, and richer
**conflict detection** (dates/amounts, recency + a transparent authority hint).
Every piece keeps the "never confidently wrong" contract — approximate matches are
tagged `~approx`, values stay literal, conflicts are surfaced not resolved — and
an adversarial external audit hardened the value/date handling. The fifth M1
piece, **optional embeddings/RRF, is deferred to v0.5.1** (it's off by default
anyway — it only activates with a local `DOCDEX_EMBED_CMD`). 226 tests. Also folds
in the binary-file extraction fix. Per SemVer, this release is versioned for what
it ships; the deferred piece lands in a follow-on minor.

### Added

- **Search now matches word variants, not just exact words (stemming).** A task
  or search for `governing` also finds `governed` and `governs`; `deal` finds
  `deals`; `close` finds `closed`. This raises how much relevant evidence a
  packet recovers at the same token budget. To keep recall high without becoming
  confidently wrong, evidence that matched only through a word stem (not the
  literal term) is tagged **`~approx`**, `context --explain` shows the stems
  used, and the agent scaffolding tells the agent to confirm the literal word
  before asserting a fact. *In plain terms:* docdex used to miss a fact written
  with a different word ending; now it finds it, and flags the match as
  approximate so the agent can double-check. Exact IDs, amounts, and dates are
  always matched literally and are never altered. The lexical index rebuilds
  itself once on the next `sync` (no action needed). First of five v0.5.0
  "meaning-aware search" pieces (see [ROADMAP.md](ROADMAP.md)). 194 tests (18 new).
- **Synonyms: find documents that use a different word for the same thing.** A
  `search` or `context` task for `legal name` now also matches documents that say
  `Vendor` or `Supplier`, via a user-owned `.docdex/aliases.json` that `init`
  seeds with a small, editable starter of contract / due-diligence terms (trim or
  extend it; delete it to turn synonyms off). Synonym matches are tagged
  `~approx`, shown in `context --explain`, and **never invent a value** — they
  only widen which documents are found. *In plain terms:* if one file calls it
  "Legal name" and another calls it "Vendor", docdex can now connect them — but
  only for the synonyms you approve, and it always flags a synonym-based match so
  you can double-check. Auto-reading a form field's value from a synonym label,
  and synonym-aware conflict detection, are a deliberately deferred follow-up (see
  [ROADMAP.md](ROADMAP.md)) — the free-text half ships now. Second of five v0.5.0
  "meaning-aware search" pieces.
- **Better ranking: the useful excerpt comes first (utility reranker).** Evidence
  is now ordered by task usefulness — a chunk that carries a labelled value and
  covers more of your query's words ranks above one that merely repeats a word —
  instead of by raw keyword frequency. This is the precision counterweight to the
  wider recall from stemming and synonyms: docdex casts a broader net, then puts
  the genuinely answer-bearing text at the top so it survives a tight budget. *In
  plain terms:* you're more likely to get the excerpt that actually answers the
  question in the first slot. Deterministic and always on. Third of five v0.5.0
  "meaning-aware search" pieces.
- **Conflicts are caught across more value formats and weighted by recency +
  authority.** When two sources give different values for the same thing, docdex
  flags it — and now it reliably catches disagreeing **dates** (`31/12/2026` vs
  `31/01/2027`, ISO `2026-12-31`, `15 Jan 2026`) and **amounts** (including
  negatives, so a loss isn't read as a gain), which used to collapse to a bare
  number and hide the disagreement. Each conflicting value is shown with its
  source and date, newest first, plus a transparent filename "authority" hint (a
  path containing `signed`/`executed`/`final` ranks above `draft`) — but every
  value is still listed and docdex never picks a winner. *In plain terms:* if two
  documents disagree on a date or amount you now see all of them, dated, with a
  nudge on which looks most authoritative — never a silently-chosen answer.
  Fourth of five v0.5.0 pieces (the recency/authority weighting the M2 seed
  promised).

### Fixed

- **Binary files are no longer indexed as garbage (and no longer hang a sync).**
  docdex decided what to extract purely by file extension, so a binary blob that
  happened to carry a text extension — a rotated log, a renamed image, a database
  dump named `.log`/`.csv`/`.json`/`.xml` — was read as text and turned into
  megabytes of replacement-character noise that got chunked and embedded into both
  indexes. Now the extractor sniffs the file's first bytes and reports binary
  content as `unsupported` (detail: `binary content (.ext)`) instead of extracting
  it. *In plain terms:* one 40 MB binary file used to produce ~24,000 junk chunks
  and take ~20 s on its own; several such files stacked up and made `sync` look
  hung. It now skips them in milliseconds and keeps the index clean. Unknown
  extensions (`.bin`, images, archives) were already skipped; this closes the
  text-extension-but-binary-content hole.
- **Extracted text is sanitized before it is cached.** NUL and stray control
  characters that some PDF/office extractions leak into otherwise-real text are
  now stripped (tabs/newlines and all Unicode are preserved), so a genuine
  document with a few leaked control bytes stays clean in the index rather than
  contaminating it. Heavily non-ASCII text (Devanagari, accented Latin, currency
  symbols) is explicitly *not* mistaken for binary. 9 new tests.
- **Value/date handling hardened against "confidently wrong" edge cases (external
  audit).** An adversarial audit by an outside model tried to break the honesty
  guarantees; this closes what it found: ISO and day-first dates were truncated to
  a number and could hide a real date conflict; a negative amount (`-$500k`) read
  as positive (a loss shown as a gain); a form field that matched a label but had
  no clear value was dropped by the budget with the misleading note "answer found
  but cut" (now "label matched but no value confirmed"); an exact hit was sometimes
  tagged `~approx` just because a synonym word appeared elsewhere in the chunk; and
  a corrupt/non-UTF-8 index file could crash `context` instead of degrading with a
  clear message. *In plain terms:* the "never confidently wrong" promises were
  stress-tested by an outside reviewer trying to break them, and the holes are
  closed.

## [0.4.1] — 2026-06-18 — "One tidy home, state out of the cloud"

A storage-layout overhaul so a synced project folder stays clean and two
machines sharing one cloud-synced folder never corrupt each other's index —
plus the password-PDF and quiet-extractor fixes from the first real-corpus
build. Existing projects upgrade automatically with `docdex migrate`. 167 tests.

### Changed

- **One hidden home in the project; the heavy state moved out of it.** docdex now
  keeps a single hidden `.docdex/` folder in your project — the `config.json`
  marker, the optional `secrets.json`, your `vision_notes/`, the `Update/` inbox,
  and curated docs — and puts all the big, rebuildable state (extracted caches,
  the SQLite index, the semantic index) in a per-machine cache OUTSIDE the
  project: `~/.cache/docdex/<project>-<id>/` by default, overridable with
  `DOCDEX_CACHE_DIR` or `XDG_CACHE_HOME`. *In plain terms:* your documents folder
  is no longer cluttered, and if you sync it across two computers each keeps its
  own search index instead of both writing one shared database through the cloud
  — which file-sync tools (OneDrive/Dropbox/iCloud) are known to corrupt.
- **`init` no longer drops a `./ctx` wrapper or a root marker by default.** The
  project root gets just the hidden `.docdex/` plus `CLAUDE.md`/`AGENTS.md`; opt
  into a wrapper script with `--wrapper NAME`.

### Added

- **`docdex migrate`** upgrades an existing v1 project (root `.docdex.json` +
  in-project `_index/_state`) to the new layout: it consolidates the durable
  content into `.docdex/`, drops the rebuildable state (rebuilt on the next sync),
  and rewrites the marker. Idempotent and safe to run on each machine; `--dry-run`
  shows the plan and changes nothing. A v1 project keeps working unchanged until
  you migrate it.
- **Password-protected PDFs are now extracted.** When a PDF is encrypted, docdex
  tries passwords from an optional, user-controlled secrets file (`.docdex/secrets.json`,
  or the legacy `.docdex.secrets.json`) — never indexed, never in the repo —
  keyed by a substring of the file's path (an empty-string key applies
  corpus-wide). *In plain terms:* locked statements / registration PDFs that used
  to fail now index, and the password is never hard-coded into the tool. Surfaced
  by the first real-corpus build, where 70 of 71 extraction failures were locked PDFs.

### Fixed

- **Quieted the extraction-time warning flood.** pdfminer's "Cannot set gray
  color … invalid float value" / "FontBBox" chatter (hundreds of lines per
  malformed PDF) and openpyxl's "extension not supported" warnings are now
  silenced during extraction; set `DOCDEX_DEBUG=1` to restore them. *In plain
  terms:* a real error in the sync log is no longer buried under thousands of
  harmless warnings.

### Safety

- The external cache carries the same confinement guard as the in-project home:
  docdex refuses to write — or, in `purge`, delete — through a symlinked or
  out-of-bounds cache dir, so a tampered cache can never steer an operation
  elsewhere. `purge` removes the home **and** that project's external cache (each
  under its own guard) and nothing else; source documents are never touched.

## [0.4.0] — 2026-06-12 — "A packet you can trust"

Closes every finding from the independent round-3 audit (4 critical + 5 major +
2 minor). The v0.3 packet had the right *shape*, but the audit produced confident
packets that were wrong; v0.4 makes the honesty guarantees literally true. The
engine and its speed are unchanged. 124 tests (16 new regressions, one per
finding, on deliberately adversarial fixtures).

### Security

- **`purge --state-only` can no longer delete outside the project.** Both purge
  modes and the state-*write* path now share one confinement check, so a symlink
  named like the index dir can't steer a delete — or a write — outside the
  project. *In plain terms:* the v0.2.1 symlink fix had missed the `--state-only`
  path; that hole is closed, and the write and delete paths can't drift apart
  again. (DDX-028.)

### Fixed

- **A form answer can't borrow a neighbouring field's value.** Field values are
  matched by whole word (so "term" no longer matches "terms") and read from the
  window right after that field's *own* label, with dense multi-field lines split
  first and broad lines marked "weak" instead of "found." *In plain terms:* on a
  packed line like `GST: …; PAN: …; Liability cap: …`, each field now gets its own
  value instead of the whole line — the worst kind of wrong when filling a form.
  (DDX-029.)
- **A present fact is never reported "missing."** `context` decides whether a hit
  is real from its content, not from a relevance score that can round to zero when
  a word appears in every file — so facts `search` finds are no longer dropped as
  absent. (DDX-030.)
- **Conflicts name the right newest source and stop crying wolf.** When sources
  disagree, the newest is chosen per value (not the first one seen), and amounts
  that are the same written differently — `INR 4.2 crore`, `₹4.20 cr`,
  `42,000,000` — are recognised as equal instead of flagged as a conflict (and the
  shown value is no longer truncated to `₹4`). (DDX-031, DDX-032.)
- **The budget line tells the truth.** It reports the *rendered* packet's real
  token count, and a packet that overflows a tiny budget says so loudly in
  free-text mode too — no more confident-looking over-budget packet. (DDX-033.)
- **Non-English evidence is found.** One Unicode-aware tokenizer is used
  everywhere, so a label and value like `Échéance: 31/12/2026` is retrieved
  instead of split into ASCII fragments and missed. (DDX-034.)
- **Corrupt state can't hide behind a healthy packet.** If the inventory is
  unreadable, `context` says so loudly (dates/freshness unavailable, run `sync`)
  instead of emitting a confident packet over broken state. (DDX-035.)
- **A note you add to `CLAUDE.md` is treated as real evidence.** Scaffold files
  are fingerprinted at `init`; `context` hides only the *unchanged* ones, so an
  edited `CLAUDE.md`/`AGENTS.md` surfaces like any other file while a pristine one
  stays out of the way. (DDX-036.)
- **A form file with no recognisable fields says so** (instead of searching for
  the filename), and **duplicate form labels are kept** (disambiguated `#2`) so
  the coverage count matches the form you can see. (DDX-037, DDX-038.)

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
