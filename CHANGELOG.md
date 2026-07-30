# Changelog

All notable changes to docdex are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and version numbers follow
[Semantic Versioning](https://semver.org/).

**Readability rule:** entries are written for humans first. Anything that is
unavoidably technical gets a plain-English *"In plain terms"* line so you can
tell what changed and why without reading the code.

## [Unreleased]

Next: **v0.5.7** — reading a value that sits *before* its label ("Helios Components
Pvt Ltd **as the Vendor**"), the last form-filling gap; then optional embeddings /
RRF via `DOCDEX_EMBED_CMD`. See [ROADMAP.md](ROADMAP.md).

## [0.5.6] — 2026-07-30 — "Ten things nobody had looked at"

**The first release driven by reviewing the whole product rather than the last
change.** Every previous release reviewed its own diff, which is why nine of these
ten defects had been sitting in place for several releases without anything in the
process being able to notice them.

**In plain terms.** Two of these gave wrong answers on the real corpus. Asking for
one folder could return a different folder's documents, because the folder name went
into a database query where `_` means "any character" — on the real corpus,
`--folder "1. Audited_Financials"` also returned files from an unrelated diligence
tree called `1. Audited Financials`. And documents found *because of* your synonym
list were then judged and labelled as if the synonym had never applied, so a
paraphrase like "what date does the agreement become effective" pulled in eight
contracts that only say "Commencement Date" and presented them as exact matches.

Four more were docdex saying untrue things about itself: it manufactured
disagreements between facts that had nothing to do with each other, it could never
report OCR work as done no matter how much of it you finished, it read ordinary words
like `covid19` as identifiers, and its health check hashed about one file in fifty
while printing a line any reader would take as "all of them verified".

### Fixed

- **`--folder` returned other folders' documents.** The name was interpolated into a
  SQL `LIKE` pattern with no escaping, so `_` matched any single character and `%`
  matched any run. Now escaped with an explicit `ESCAPE` clause. The semantic path
  did this filter with a plain substring test and never had the bug.
- **A synonym that widened the search did not govern the evidence.** There were three
  different rules for "did this query ask for an alias group": retrieval used
  subset-of-stems, the evidence test and the `~approx` tag required the phrase as a
  contiguous run, and `--explain` used the contiguous rule while its own comment
  claimed it matched retrieval. One rule now answers the question everywhere.
- **`sync --no-hash` left the search index permanently stale.** `--no-hash` records an
  empty checksum for every file, and the index compared checksum to checksum — so
  `"" == ""` read as "unchanged" and nothing was re-indexed, while the text cache was
  correctly rewritten. Search then answered from text the document no longer contained
  and could not find the text it did, while sync reported the file as changed. Now
  falls back to modification time and size when either checksum is missing, which is
  the rule `sync` itself already applied. Same trap for any file over the 200 MB
  hashing limit.
- **The Conflicts section manufactured disagreements.** It grouped by "which query
  terms appear in this line", which says nothing about what a number means, so a ship
  date, a headcount and a revenue figure about one subject were reported as three
  values that disagree. On a real GST query that produced eight "disagreeing" values
  that were simply eight different facts — and a genuine disagreement would have been
  buried among them. Values are now grouped by what labels them.
- **Finished OCR work could never count as done.** The queue omitted every completed
  row when it was rebuilt, so the count of completed rows was necessarily zero *and*
  the total shrank as work got finished: 1,041 notes already written on the real
  corpus read as `0/1896 done`. Completed rows now stay, marked `done`.
- **One failing sync stage silently skipped every later one.** An exception in the
  index build cancelled the context dumps, the embeddings and the OCR queue with no
  report. That is the mechanism that left the OCR queue and the dumps frozen for a day
  after v0.5.4's bug: the failure was real, its consequences invisible. Each stage is
  now isolated, named when it fails, and the command exits non-zero.
- **Four of six sync stages ran with no lock held.** `run_sync` released the lock in
  its own `finally`, after which the CLI ran four more stages unprotected. Two syncs
  overlapping past that point both rebuild the search index and both replace the
  semantic index. The lock is now held for the whole run.
- **Swapping embedding models mixed two models' vectors in one index.** Reuse compared
  the literal string `"external"`, which every `DOCDEX_EMBED_CMD` reports, so a
  different model of the same size re-embedded nothing. The command is now
  fingerprinted. Dormant until v0.5.7 uses it — fixed before it could bite.
- **`doctor` reported a 2% sample as if it were the whole corpus.** It hashes every
  50th row; the line read `rows=11880 missing=0 sha_mismatch=0`. It now says
  `sha_checked=`, and says in words that the sample is a sample.
- **Ordinary words read as identifiers.** A global `re.I` was applied to a pattern
  whose identifier branch is written `[A-Z0-9]`, defeating its own character class:
  `covid19`, `windows10` and `section2b` all matched as identifiers. Case sensitivity
  is now per branch.

### From adversarial review of this release

- **The conflict fix hid a real conflict.** Reading the label from the words *before*
  a value meant `$500,000 is the approved budget` had no label at all, so its conflict
  was dropped entirely — two contradictory budgets shown as plain evidence. Trading a
  fabricated conflict for a hidden one is a straight loss. Both sides are now read.
- **And it still fabricated one.** `Widget has 12 engineers` and `Widget has 5
  offices` both reduced to `widget`, because the verb between them is a function word.
  What a number counts is part of what it means, so the word after it counts too.
- **A real invoice number stopped being a value.** The new "ignore document
  numbering" rule listed `no`/`serial`, so `Invoice No. 42` — where the number *is*
  the answer — was suppressed. Suppressing a real value hides evidence; treating a
  stray cross-reference as a value only spends a ranking tie-break.
- **The same rule silently did not apply at the end of a sentence.** `page 3.`
  absorbed the full stop, stopped looking like a plain number, and skipped the check.
  Found while fixing the item above; in no review.
- **`GSTIN 29ABCDE1234F1Z5` extracted as `29`.** The bare-number branch was tried
  before the identifier branch — the same ordering bug v0.5.0 fixed for dates, left
  standing one branch lower. Emails were worse off still: `user123@x.com` yielded
  `123`.
- **A deleted OCR note still counted as finished work.** The new `done` column was
  trusted alongside the note itself. The note is the deliverable.

### Deliberately not fixed, and why

- **A genuine conflict phrased two ways is now missed** — `revenue was 5 crore`
  against `revenue totaled 9 crore`. Both values are still shown as evidence, so
  nothing is hidden from the reader; what docdex will not do is assert a disagreement
  it cannot stand behind. Doing better needs a real field label rather than
  neighbouring words. Tracked for v0.5.7.
- **`has_value` is still close to "contains a digit".** Excluding document numbering
  moved 519 of 92,526 chunks — 96.6% to 96.0%. The cause is granularity, not the
  pattern: the flag is computed per 1,800-character chunk, and almost any chunk that
  size contains some real number. The `re.I` defect above is genuinely fixed; this
  half of the finding is not, and is tracked rather than claimed.

### The release gate, hardened again

The QA gate itself was reviewed and six ways it could certify a bad release were
closed. Two caught real mistakes in this very release within minutes: a
module-level import of a new helper made the whole new test file fail to *collect*
against the previous release, cutting the comparison suite to one case; and two of
these tests only *raised* on the base tree instead of failing an assertion, so they
were not evidence of anything. Notably, gate 3 used to accept **one** failing
assertion as proof of any number of fixes — a release now declares which test proves
each fix, and every one of them must fail on the previous release.

### Real corpus

Verified end to end on the real corpus (10,636 supported files, 92,526 chunks). 387 tests,
up from 324. Both benchmark suites unchanged: single-fact retrieval 12/12, form
filling 10/11 at 1,433 tokens.

## [0.5.5] — 2026-07-30 — "Advice that works"

**Three things docdex said about itself were untrue or impossible to act on.** All
three were found while checking the previous release end to end on the real
10.5k-file corpus, and the first one made v0.5.4's headline fix unreachable in the
one situation it was written for.

**In plain terms.** v0.5.4 taught docdex to stop answering "no matches" when its
search index was actually empty — it now refuses, and prints *"run `docdex sync` to
rebuild the index"*. That instruction did not work. The rebuild only ran when some
document had changed, so a sync over an unchanged folder looked at an empty index,
decided there was no work, and left it empty. Search printed the same instruction
again, and no flag forced a rebuild — a loop with no way out, in exactly the state
the message was written for.

### Fixed

- **A sync with nothing to do now repairs an index that cannot answer.** What
  decides a rebuild is whether the index covers the stored text, not only whether
  the corpus changed. Covers three broken states: nothing indexed, partly indexed
  (a rebuild that stopped early — the dangerous one, because it still answers), and
  one of the two term spaces empty (which silently degrades ranking instead of
  failing). Deliberately *not* triggered when the index cannot be checked at all:
  that would rebuild the whole corpus on every sync forever, and search already
  refuses loudly in that case. A repair is reported, never silent — `sync` prints it
  and `build()` returns `repaired`, because "reindexed 0" while both term spaces
  were rebuilt from scratch is a false statement about work that happened.
- **`doctor` no longer reports its own policy as a defect.** Files over the
  `max_extract_mb` cap are skipped on purpose — `sync` says so in as many words —
  but the coverage check had no branch for them, so all 19 such files on the real
  corpus were counted as *missing caches* and turned a healthy corpus red. They are
  now counted and shown as `skipped`, which is reported but not a failure. The
  second cost was worse than the false alarm: while those 19 sat in `missing`, that
  number could not tell anyone whether a genuine gap had appeared beside them.
- **Extraction failures now say what is actually wrong.** A file that is present but
  truncated reported `PackageNotFoundError: Package not found at '<path>'`, which
  reads as "that file is missing"; an encrypted PDF with no password configured
  reported `PDFPasswordIncorrect:` with an empty message, which reads as "docdex
  tried a password and got it wrong". On the real corpus this hid the actual split —
  six documents damaged on disk, four password-protected — behind wording that sent
  the reader looking for a path problem. Damaged, encrypted-PDF and
  encrypted-Office files are now each named, with the remedy that applies to each.
  Note that a `secrets.json` password is only ever offered for a PDF, because that
  is the only place docdex can use one.
- **A red check now says what to do next.** `cache coverage` names
  `sync --backfill` for missing caches and points at `extract_status.tsv` for
  per-file reasons. A red check with no next step gets ignored, which is how this
  one hid behind its own false alarm.

### From adversarial review of this fix

Two external passes, 41 findings, 33 fixed. Three changed the product beyond the three
faults above:

- **An encrypted `.xlsx` got no plain-English diagnosis at all.** openpyxl raises
  `BadZipFile` where python-docx and python-pptx raise `PackageNotFoundError`, so a
  protected workbook still reached the user as `BadZipFile: File is not a zip file`.
  Every test written for this release used `.docx`.
- **The damaged-file message asserted the file was present without checking.** A file
  deleted between the inventory scan and extraction raises the same error, and the
  message then stated the exact opposite of the filesystem.
- **The Office message named `secrets.json`** — as a disclaimer that passwords there
  work only for PDFs. Naming the file at all invites trying it, so it is gone.

And two in the release gate that could have certified work it had not checked:

- **An inherited `PYTEST_ADDOPTS="-k one_test"` would have reduced the suite to a single
  test** while the gate reported a pass — it copies the environment, and the suite gate
  only required "more than zero tests passed". Now scrubbed, with any deselection
  reported. Verified: with the variable set, the gate still collects all 324.
- **A file edited twice while the gate ran was invisible**, because its git status is
  identical before and after — so the tests and the benchmarks could measure different
  source. Contents are now hashed, not just statuses.

The most useful finding was about the tests, not the code: **they never ran the command
the advice names.** The helper composed the two sync steps by hand, so deleting the index
build from the CLI would have left every test green while `docdex sync` stopped repairing
anything. For a release whose entire subject is advice that works, that was the wrong
shortcut. Two findings were refuted by measurement, and five real gaps are recorded in
ROADMAP as debt rather than quietly dropped.

### Why this was missed a day earlier

v0.5.4 ran the full release process — two external adversarial reviews, 35 findings,
a six-gate release gate. Pass 1 reviewed the diff, pass 2 reviewed the test suite,
and the gate ran the suite. **Nobody asked whether the instruction the product prints
resolves the state it is printed about.** The tests proved the refusal was correct;
none of them followed the advice. `docs/RELEASING.md` now requires that check, and
the regression tests are written that way — they follow the printed instruction and
assert the user is no longer stuck.

### Not a bug, for the record

The semantic index showing fewer chunks than the keyword index was *not* drift. Both
cut text identically; they discard scraps differently (the keyword index keeps
anything over 3 characters, the semantic one drops trailing fragments under 40), so a
one-chunk difference is by design. The larger gap seen on the real corpus was simply
un-run work — the v0.5.4 repair was deliberately run with `--no-embed` to isolate the
migration. A full `docdex sync` reconciled it.

### Real corpus

10.5k files, 92,507 chunks. Full six-step sync: **70s**, `reindexed 0` — the repair
check costs three counting queries and correctly finds nothing to do on a healthy
index. `doctor`: 8 passed, 1 failed, and that one failure is now honest — ten files
docdex genuinely cannot read (six truncated on disk, four password-protected), with
`missing=0` where it used to claim 19.

## [0.5.4] — 2026-07-30 — "The upgrade that broke the index"

**A regression this project shipped, found by the user: after v0.5.2, `docdex sync`
crashed and keyword search returned nothing at all — across an entire 10,498-file
corpus, without saying so.** No document text was ever lost; the search index was.

**In plain terms.** v0.5.2 added a new column to the table that stores your text.
Creating a table only does something when the table doesn't exist yet, so on any
index that already existed the column was never added — and `sync` then died trying
to write to it. Worse, the step just before that deletes the two keyword indexes so
they can be rebuilt, and *that* deletion was already saved to disk by the time the
crash happened. So every sync deleted the working index, failed, and left an empty
one behind. An empty index matches nothing, and "nothing matched" looked exactly
like "your documents don't mention that". docdex answered "not here" about 10,498
files, confidently, ten thousand times over.

### Fixed

- **`sync` upgrades an existing index instead of crashing on it.** A version change
  now recreates the derived tables at their current definition rather than trying to
  patch them, so a new column needs no bespoke migration step. This costs nothing:
  the upgrade already re-reads every chunk from the `.txt` caches, which are the
  source of truth. Verified on the real 10,501-file corpus: schema 3 → 4 in 46
  seconds, 92,507 chunks searchable again.
- **A failed upgrade can no longer destroy a working index.** The schema change and
  the rebuild that completes it are now one transaction, so if anything fails — an
  error, a full disk, Ctrl-C — the index that was working is still the index on disk.
  SQLite has always made this available; docdex simply wasn't using it. Python's
  `sqlite3` starts a transaction for data changes but *not* for schema changes, which
  is why the deletion survived the crash that followed it.
- **An empty index is now reported, never answered.** `search` refuses with a named
  error telling you to run `docdex sync`, instead of returning "no matches" for a
  corpus it cannot search. `docdex doctor` gained a `lexical index` check that
  reports how many chunks are searchable, and fails when text is stored but the index
  holds no terms — the exact state that went unnoticed. It also fails when only one
  of the two term spaces is empty, which degrades ranking silently rather than
  visibly.

### From adversarial review of this fix

Two external review passes ran on the change itself — one on the code, one on the
tests — and found real problems in both. Full record in `docdex-qa/v0.5.4/`.

- **A missing version record no longer hides a stale table.** If the database could
  not say which schema it was, nothing was compared, so the upgrade was skipped and
  the crash simply waited for the next edited document. Whether a rebuild is needed is
  now decided from the actual columns, checked against the definition that creates
  them so the check cannot drift.
- **"Could not check" is no longer reported as "checked, fine".** When the health of
  the index could not be established, it was being treated as healthy — the release's
  own bug one level up. `search` and `doctor` now say they could not verify it.
- **A partly-built index is caught, not just an empty one.** The health probe counts
  how many chunks are actually indexed and compares that to how many are stored, so an
  index covering a fraction of your documents fails instead of quietly answering for
  the part it has.
- **And a false alarm was caught before it shipped.** An earlier version of that probe
  asked "does the index contain any words at all", which would have declared a
  perfectly healthy index broken for a document containing only punctuation. Counting
  indexed chunks instead is right in both directions.

The release gate that checks all of this was audited too, and eleven ways it could
have certified a release it had not really verified were closed — including a default
comparison against a release two versions old, a pass on a dirty working tree that
named a commit it had not tested, and trusting the benchmark's own claim that its
output was byte-identical instead of checking.

### Why it went unnoticed for a day

`SELECT COUNT(*) FROM chunks_fts` returns a full row count even when the index is
completely empty — for an external-content FTS5 table that count is read from the
*content* table, not the index. Every obvious health check therefore looked perfect.
The new check uses `fts5vocab`, which is FTS5's own view of the inverted index, so it
reports what was really indexed rather than what should have been.

### Note for existing installs

Nothing to do beyond one `docdex sync`, which now completes. If you ran a sync
between v0.5.2 and this release, your keyword index was emptied and that sync will
rebuild it; your documents and their extracted text were never touched.

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
