# docdex

**A token-efficient local document index for LLM workflows.** Point it at a folder of documents — Word, PowerPoint, Excel, PDF, Markdown, anything — and it builds a private, incremental, greppable index that lets Claude (or Codex, Gemini, or you) answer questions from thousands of files without ever loading them all.

- **Local-first.** No daemons, no vector database, no cloud services, no API keys. Plain files on disk that you can inspect, grep, and back up.
- **Incremental.** Re-sync only touches new, changed, renamed, or deleted files. Renames are detected by content hash and never re-extracted.
- **LLM-native.** `docdex init` scaffolds `CLAUDE.md` / `AGENTS.md` instructions so any coding agent dropped into the folder knows exactly how to retrieve context cheaply, in tiers, instead of bulk-loading your corpus.
- **Zero residue.** Everything docdex creates lives in three known places. `docdex purge` removes them completely.

### What docdex is — and isn't

docdex is a **context provider for an AI agent**, not a desktop search engine. Its job is to hand a coding agent (Claude Code, Codex, Gemini) *the specific context it needs to finish a task* — with citations, at the lowest possible token cost — so the model doesn't re-read your whole corpus every session. The headline command is **`docdex context "<task>"`**, which returns a packed, cited evidence brief sized to a token budget; the lower-level `search` and `semantic` commands exist to *support* that, not the other way round.

It is **not** a replacement for Spotlight, `grep`, or your operating system's file search, and it isn't designed for you to sit and type queries into all day. Think of it as the retrieval layer an LLM calls on your behalf. The benchmarks below measure exactly that: context delivered to an agent per token spent, not human search ergonomics.

```
$ cd ~/Work/MyCorpus
$ docdex init
$ docdex sync
$ docdex search "liability cap in the Acme master agreement"
[#1] score=412  Contracts/Acme/MSA final v3 (signed).pdf
     cache: _index/_state/extracted/Contracts/...
     ...limitation of liability shall not exceed the fees paid in the twelve (12) months...
```

## Why

If you work with a large folder of poorly named documents, the information you need is *in* the files but not *findable* — filenames lie, and LLM context windows are too expensive to fill with 5,000 documents per question. docdex fixes the economics: extraction and indexing are paid **once**, at sync time; every question afterwards costs only a ranked handful of snippets.

## Does it actually help? Measured.

The repo ships a reproducible benchmark (`python3 benchmarks/run_benchmark.py`): a deterministic 162-file corpus with 12 facts planted inside `.docx`/`.xlsx`/`.pptx`/`.pdf` files whose **filenames deliberately lie** (`scan_0231 copy.docx`, `Final_v7_USE_THIS_ONE.xlsx`), surrounded by 150 distractors that share the queries' vocabulary. For each question we measure what an agent must ingest **until the answer is actually in its context**:

| method | right file ranked #1 | answer reached | median tokens to answer |
|---|---|---|---|
| browse by filename (no index) | 0/12 | 0/12 | 976 (then fails) |
| raw `grep -ril` (no index) | 0/12 | 0/12 | 1,017 (then fails) |
| read everything (no index) | 12/12 | 12/12 | **28,312** |
| **`docdex search`** | **12/12** | **12/12** | **780** |

**36× less context per question**, after a one-time sub-second indexing cost on this corpus — and the gap widens with corpus size, because "read everything" scales with the corpus while `docdex search` doesn't. Filename browsing and grep aren't just worse, they're structurally blind: Office files are zip containers and PDF streams are compressed, so their content is invisible to both.

Honest caveats, in the report itself ([benchmarks/RESULTS.md](benchmarks/RESULTS.md)): token counts are a chars/4 approximation; the corpus is synthetic (by design — it's checked in and re-runnable by anyone); and the bundled semantic backend is lexical, so it cannot bridge a *pure* paraphrase — keyword `search` is the workhorse, and true paraphrase retrieval needs an external embedding via `DOCDEX_EMBED_CMD`.

**The bigger test — a real multi-field job.** Finding one fact is the easy case; the real job is "fill this whole form from a messy folder." A second benchmark ([benchmarks/RESULTS_TASK.md](benchmarks/RESULTS_TASK.md)) plants a 12-field vendor onboarding form's answers across realistically *large* contracts/sheets/PDFs (one field deliberately absent) and measures context delivered per token:

| method | form fields covered | absent field flagged honestly | tokens |
|---|---|---|---|
| read whole files until budget | 0/11 | n/a | 203 (then stops) |
| search + read each top file | 11/11 | n/a (would guess) | 20,228 |
| **`docdex context --from-file`** | **8/11** | **1/1** | **1,571** |

The naive search loop *can* cover everything — by reading 20k tokens of full documents, and with no way to say "this field isn't in the corpus." `docdex context` delivers ~73% of the findable fields at ~7% of that token cost **and** correctly reports the absent field as not found instead of forcing a guess. The packet carries a coverage header (*found / weak / missing / dropped-by-budget*), a token-exact budget line, and a conflicts section, so an agent can never mistake a partial packet for a complete one. As of **v0.4.0** the answers are also *faithful*: each field's value is extracted from the window right after that field's own label (never a neighbour's value on a dense line), equivalent amounts no longer false-conflict, and a present fact is never reported "missing" just because its relevance score rounds to zero — the trust failures an independent audit surfaced in v0.3. The 3 fields it misses are honest lexical-retrieval limits (a synonym the corpus never spells out; the real document losing to a short distractor that shares the field's word) — the exact gaps the next milestone targets with a field-alias registry and reranking. It does not fabricate them; it lists them under "Missing."

## Using docdex with an LLM (the intended way)

docdex works standalone, but it was designed to be **driven by an agent** — Claude Code, Codex, Gemini CLI, or anything that can run shell commands. The point of the benchmark table above is precisely that an agent *without* an index has only bad moves available (guess filenames, grep blindly, or read everything).

**Setup is two commands, then the agent configures itself:**

```bash
cd ~/Work/BigCorpus
docdex init && docdex sync
claude        # or your agent of choice
```

`init` scaffolds `CLAUDE.md` (auto-loaded by Claude Code) and `AGENTS.md` (for other agents). These teach the agent the session protocol: check `./ctx status` first, offer to sync if stale, then **gather context with `./ctx context "the task" --budget N`** and fill only the gaps it reports — instead of bulk-loading the corpus.

**The `context` command is the heart of it.** Rather than a list of hits, it returns a token-budgeted *evidence packet* — cited answers, supporting excerpts, an explicit "what's missing", and a suggested next call:

```text
$ ./ctx context "what is the liability cap and payment terms with Meridian" --budget 1200
# context packet
Task: what is the liability cap and payment terms with Meridian
Coverage: 2 value answer(s)
Budget: 1200 requested · ~130 used · 1070 free
Index: indexed 2026-06-11 14:02 — not re-checked (run `docdex status` to find new files)

## Answers
- The aggregate liability cap under this agreement is INR 4.2 crore.  [Contracts/scan_0231 copy.md ·0]
- Payment terms are net-45 from invoice date.  [Contracts/scan_0231 copy.md ·0]

## Evidence
[E1] Contracts/scan_0231 copy.md ·0  (2026-06-11)  (score 5.31)
  "...aggregate liability cap under this agreement is INR 4.2 crore. Payment terms are net-45..."
```

The header lines are the honesty contract: **Coverage** (found / weak / missing / dropped-by-budget) and **Budget** (requested / used / free) mean an agent can always tell a *partial* packet from a *complete* one. A `## Conflicts` section appears when two sources disagree (newer flagged); `## Dropped (budget)` appears with a "rerun with a bigger budget" hint when the budget cut something. Pass `--check-freshness` to fully re-verify staleness (it walks the corpus); by default `context` stays cheap and trusts the last sync.

For a form, `./ctx context --from-file vendor_form.md --budget 3000` retrieves evidence field-by-field and tells the agent which fields it couldn't find. docdex stays deterministic — it packs and cites evidence; the agent already in the loop does the reasoning. No API keys, no model calls from docdex.

**What a session looks like afterwards:**

> **You:** What's our liability cap with Meridian?
> **Agent:** *(runs `./ctx context "liability cap Meridian" --budget 1000`)* Per `Contracts/scan_0231 copy.docx`, the aggregate liability cap under the Meridian MSA is INR 4.2 crore. — *~130 tokens of context, not 28,000.*

**One-time curation worth doing.** After the first sync, hand your agent this prompt — it builds the cheapest retrieval tier (and is the step that turns a *search tool* into a *knowledge base*):

```
Read _index/HANDOFF.md. Using ./ctx search and the extracted caches under
_index/_state/extracted/, write _index/00_MASTER_INDEX.md: a 5-8K-token
overview of this corpus — key facts, per-domain snapshot tables, and a file
map. Cite source paths. Then write topical NN_*.md deep-dives for the 3-5
largest domains. Never load all caches at once; work folder by folder.
```

**Vision/OCR with a multimodal agent.** `docdex vision create` queues scanned PDFs, images, and chart-only slides into a manifest; your agent processes them in batches, writes notes to `_index/vision_notes/`, and `docdex sync` makes them searchable. Image content becomes retrievable text exactly once.

**Automation-safe.** All output is plain text and exit codes are stable (`status` exits 1 when stale), so docdex drops into hooks, cron, and CI — e.g. a session-start hook that warns when the index is out of date.

## Install

Requires Python ≥ 3.9 on macOS or Linux.

```bash
# Recommended: pipx (isolated, on PATH everywhere)
pipx install git+ssh://git@github.com/prabhxnoor/docdex.git

# Or plain pip
pip install git+ssh://git@github.com/prabhxnoor/docdex.git

# HTTPS form (needs a GitHub token while the repo is private)
pipx install git+https://github.com/prabhxnoor/docdex.git
```

Upgrade everywhere with `pipx upgrade docdex`. Uninstall with `pipx uninstall docdex`.

Format support out of the box: `.docx`, `.pptx`, `.xlsx`/`.xlsm`, `.pdf`, plus plain-text formats (`.md`, `.txt`, `.csv`, `.json`, `.html`, source code, …). Legacy `.doc`/`.rtf` are converted via the built-in `textutil` on macOS; on Linux they're reported as unsupported rather than failing.

## What to keep in mind (install → index → use → uninstall)

A practical orientation — including the question everyone asks: *does the AI model I use matter for indexing?* (Short answer: **no, not at all.**)

### Installing it

- docdex is an ordinary command-line tool. **You run the one-line install above** — there's no GUI installer and nothing to configure. Run it yourself in a terminal, **or** ask your coding agent (Claude Code, Codex, Gemini CLI) to run the exact same command; it's just a shell command, the agent does nothing special.
- Prerequisites: **Python ≥ 3.9** and **git** (for the git-URL install). `pipx` is recommended so docdex is isolated and available on your PATH in every folder.
- Nothing about your documents leaves your machine. docdex is local-first — no account, no cloud, no API key. Installing only downloads the *code*; your files are never uploaded.

### The first index — and the model question

- **Indexing never uses an AI model.** `docdex sync` extracts text and builds its database with plain, deterministic Python (python-docx, pdfminer, SQLite). There is **no Claude, no GPT, and no embeddings involved by default.**
- So the **model and effort setting make zero difference to indexing.** Opus vs Sonnet vs GPT-5.5; low vs x-high; max vs medium effort — **identical index, identical speed.** Whoever (or whatever) runs `docdex sync` produces the **same database, byte for byte.** The model only matters *later*, when it reasons over the cited packet docdex hands it — and even then docdex's retrieval is deterministic; the model just decides how to use what it's given.
  - *Only exception:* the optional `DOCDEX_EMBED_CMD`, where **you** explicitly wire in an embedding model for fuzzy search. It's **off by default** — the built-in fuzzy backend is a local hash, no model, no network.
- **It's fast precisely because it's deterministic, not model-driven.** Measured first-time (cold) sync — macOS arm64, mixed text corpus, from the independent v0.2 audit:

  | Files | First index | Re-sync (warm) | Index on disk |
  |---:|---:|---:|---:|
  | 1,000 | ~0.4 s | ~0.2 s | 0.5 MB |
  | 10,000 | ~3.5 s | ~1.4 s | 4 MB |
  | 50,000 | ~22 s | ~10 s | 22 MB |

  After the first run, sync is **incremental** — only new/changed files do work, so everyday re-syncs are far cheaper than the "first index" column.
- What *does* affect indexing time (none of it the model): **PDFs** — especially scanned ones — extract slower than plain text; **very large single files** cost more (one giant multi-hundred-MB text file can bloat the index disproportionately, so keep huge logs/exports out of the indexed folder); **cloud placeholder files** (OneDrive/iCloud) must download first (sync prefetches them).

### Using it day to day

- The intended loop — your agent runs this for you, guided by the `CLAUDE.md`/`AGENTS.md` that `init` scaffolds: **`docdex status`** once per session → **`docdex context "your task" --budget N`** for a cited evidence packet → fill only the gaps it reports.
- **Re-sync after you add or change files** (`docdex sync`); `docdex status` tells you when the index is stale.
- Honest current limits worth knowing (v0.2): it matches **words, not synonyms** yet; it does **not flag conflicting or out-of-date facts** yet (if two files disagree you may get both — check the cited sources); and a **too-small `--budget` returns partial context**, so give multi-field jobs a generous budget (≈6000–8000). All three are on the [roadmap](ROADMAP.md).

### Uninstalling it

- **Per folder, first:** run `docdex purge --yes` *inside that folder* — it removes everything docdex created (the `_index/` state) and prints exactly what it will delete. **Your source documents are never touched.** It deliberately leaves the scaffolded `CLAUDE.md`/`AGENTS.md`; delete those by hand if you want them gone.
- **The tool itself:** `pipx uninstall docdex` (or `pip uninstall docdex`).
- **Order matters:** purge each project *before* uninstalling the tool, since `purge` is itself a docdex command.

## Quickstart

```bash
cd ~/path/to/your/documents
docdex init          # scaffolds .docdex.json, _index/, ./ctx, CLAUDE.md, AGENTS.md
docdex sync          # walks the tree, extracts text, builds all indexes
docdex status        # freshness + cache coverage at a glance

docdex search "exact words or topic"        # ranked keyword search
docdex semantic "rough description of it"   # fuzzy retrieval
docdex doctor --e2e                          # full integrity self-test
```

`init` also installs a `./ctx` wrapper in the project root, so `./ctx sync`, `./ctx search "..."` work for anyone (and any LLM) without knowing about docdex. The wrapper lives at the project root: call it as `./ctx` from there, or just use `docdex` from any subdirectory (it walks up to find the project). The wrapper resolves its own location, so a path like `../ctx` works from a subfolder too.

## Commands

| Command | What it does |
|---|---|
| `docdex init` | Initialize a project. `--index NAME` to rename the index folder, `--no-agent-docs` / `--no-wrapper` to skip extras. |
| `docdex sync` | Incremental reindex: cloud prefetch → inventory + text caches → context dumps → semantic index → vision queue. Flags to skip stages: `--no-prefetch`, `--no-dumps`, `--no-embed`, `--no-vision`; plus `--dry-run`, `--backfill`, `--no-hash`. |
| `docdex context "task"` | Build a token-budgeted evidence packet (coverage header, cited answers, conflicts, gaps). `--budget N`, `--folder X`, `--from-file form.md`, `--explain`, `--check-freshness`. |
| `docdex status` | Freshness check (exit 0 fresh, 1 stale/gaps). Distinguishes real cache gaps from scanned files with no text. |
| `docdex search "q"` | Ranked keyword search over extracted text. `--folder X`, `-n N`. |
| `docdex semantic "q"` | Semantic-index search. Same flags. |
| `docdex embed` | Rebuild the semantic index incrementally (`--force` for full rebuild). |
| `docdex dumps` | Rebuild per-folder `CONTEXT_<folder>.txt` aggregates (`--max-bytes 5M` to split). |
| `docdex prefetch` | Materialize OneDrive/iCloud placeholder files before indexing. |
| `docdex vision create` / `status` | Queue images, image-only PDFs, and low-text files for LLM OCR/captioning. |
| `docdex doctor` | Integrity checks; `--e2e` runs a write→sync→search→delete sentinel test. |
| `docdex dedup` | Report `Update/` inbox files that duplicate corpus files (`--apply` moves them to `<index>/bin/`, `--restore` undoes). |
| `docdex extract FILE` | One file's extracted text to stdout. |
| `docdex info` | Project paths and configuration. |
| `docdex purge` | Remove every docdex artifact from the project (`--state-only` keeps curated files and notes). |

Every command accepts `--root PATH`; without it, docdex walks up from the current directory to find the project marker.

## What it creates (and nothing else)

```
your-project/
├── .docdex.json            ← project marker + config
├── ctx                     ← optional wrapper script
├── CLAUDE.md, AGENTS.md    ← optional LLM operating instructions
└── _index/
    ├── HANDOFF.md           ← operating manual for humans/LLMs
    ├── 00_MASTER_INDEX.md   ← stub for your curated overview
    ├── Update/              ← inbox: drop new files here (indexed)
    ├── vision_notes/        ← OCR/caption notes (indexed)
    └── _state/              ← all derived data (safe to delete & rebuild)
        ├── inventory.tsv             path / size / mtime / sha1 / ext / folder
        ├── inventory_history.tsv     soft-delete log
        ├── extract_status.tsv        per-file extraction outcome
        ├── extracted/                per-file text caches
        ├── context_dumps/            per-folder aggregates
        ├── semantic_index.jsonl      embedding index
        └── vision_tasks/             OCR queue manifest + image assets
```

Source documents are **never moved, renamed, or modified** — important when the folder lives in OneDrive/iCloud/Dropbox, where moves break shared links. Deletions in your corpus are soft-deleted from the inventory with full history.

## How retrieval stays cheap: the load tiers

The scaffolded `CLAUDE.md` teaches agents to escalate through tiers and stop as early as possible:

1. **`00_MASTER_INDEX.md`** — a curated ~5-8K-token overview you (or your LLM) write once after the first sync.
2. **One topical `NN_*.md` file** — optional deeper per-domain summaries. `sync` flags which ones might be stale when their source folders change.
3. **`docdex search` / `docdex semantic`** — ranked snippets only, never whole files.
4. **One specific cache or source file** — only after retrieval has narrowed the candidates.

## Semantic search, honestly

The default backend (`local-hash-v1`) is a deterministic hashed-feature embedding over words, bigrams, and character n-grams: private, dependency-free, and useful for narrowing candidates — but it is *not* a neural model, so it can't match by meaning across different words ("legal name" won't find "Vendor"). True meaning-based retrieval needs a real embedding model, which you opt into via `DOCDEX_EMBED_CMD` — any command that reads text on stdin and prints a JSON float array.

> ⚠️ **Privacy — keep it local.** That command receives your document text. A **local** model keeps everything on your machine; a **cloud** embedding API (OpenAI, etc.) sends your corpus text to that provider. Point `DOCDEX_EMBED_CMD` only at something local unless you've explicitly accepted that trade-off. It runs via the shell, so only use a command you trust.

```bash
# A LOCAL embedder — text in on stdin, a JSON float array out, no network.
#   embed.py:
#     import sys, json
#     from sentence_transformers import SentenceTransformer
#     m = SentenceTransformer("all-MiniLM-L6-v2")   # downloaded once, runs locally
#     print(json.dumps(m.encode(sys.stdin.read()).tolist()))
export DOCDEX_EMBED_CMD="python3 /path/to/embed.py"
docdex embed --force   # rebuild under the new backend
```

Use the **same** model for indexing and querying (mixing two gives nonsense), and re-run `docdex embed --force` when you switch. Rebuilds are otherwise incremental: only files whose content hash changed get re-embedded. Today this powers `semantic`; fusing it into the `context` packet is on the [roadmap](ROADMAP.md).

## Vision / OCR workflow

Text extraction can't see scanned PDFs, images, or chart-only slides. `docdex vision create` builds a queue (`_state/vision_tasks/manifest.tsv`) of these sources, with embedded PPTX images exported as files an LLM can open. Process the queue with any multimodal model, write notes to `_index/vision_notes/` in the documented format, run `docdex sync` — the notes live inside the indexed tree, so they become searchable immediately. `docdex vision status` tracks progress; completed sources drop off the next queue.

## Day-to-day updating

Drop new files anywhere (or into `_index/Update/` if you haven't decided where they belong), edit or delete files in place, then `docdex sync`. That's the whole workflow. `docdex status` tells you (and warns your LLM) when the index is stale.

## Configuration

`.docdex.json` in the project root:

```json
{
  "docdex_schema": 1,
  "index_dir": "_index",
  "wrapper": "ctx",
  "skip_dirs": ["Archive", "Raw Exports"]
}
```

`skip_dirs` are skipped at any depth, in addition to built-ins (`.git`, `node_modules`, `.venv`, `__pycache__`, …). Dotfiles, Office lock files (`~$…`), and OS junk are always ignored.

## Performance notes & honest limits

- Comfortable for corpora up to roughly **10,000 files** end-to-end (the FTS5 `search` engine itself stays fast well past that — see the indexing-time table under [What to keep in mind](#what-to-keep-in-mind-install--index--use--uninstall)). The first full extraction is the only slow run; warm re-syncs are incremental.
- Files ≥ 200 MB are inventoried but not hashed (no rename detection for them). A supported file larger than **`max_extract_mb` (default 50 MB)** is recorded as `skipped` rather than extracted, so one giant log/export can't balloon the index. Raise `max_extract_mb` in `.docdex.json` (or `0` to disable the cap), or pass `docdex sync --allow-large-text`, to index it anyway.
- Keyword `search` runs on the SQLite **FTS5/BM25** index, so its latency is effectively **flat as the corpus grows** (~36 ms median even at 50k files in the v0.2 audit). Only the no-FTS5 fallback scans caches linearly.
- The `index.db` is rebuilt from your **source** files (via their `.txt` caches). If you ever hand-edit a cache directly, delete `index.db` and re-sync so the edit is picked up.
- `docdex context` is the exception today: it does a full freshness walk per call, so on very large corpora it's slower than raw `search` (a known v0.3 fix — see the [roadmap](ROADMAP.md)). Run `docdex status` once per session and keep budgets sensible.
- Semantic search scans the index linearly per query — simple and dependable, a few seconds on large corpora. If you outgrow it, plug in an external embedding backend or a real vector store.
- Encrypted, corrupted, or image-only files are recorded in `extract_status.tsv` (`failed` / `empty`) instead of being silently retried forever — `status` reports them separately from real gaps.

## Exit codes

`status`: 0 fresh, 1 stale or cache gaps. `search`/`semantic`: 0 hits, 1 no hits, 2 bad query/missing index. `doctor`: 0 all checks pass. Everything else: 0 success.

## Uninstall

```bash
docdex purge --yes      # per project: removes marker, index dir, wrapper
pipx uninstall docdex   # the tool itself
```

`purge` prints exactly what it will delete and refuses to run without `--yes`. Your documents are untouched either way.

## Development

```bash
git clone git@github.com:prabhxnoor/docdex.git
cd docdex
pip install -e ".[dev]"
pytest
```

Layout: `src/docdex/` (one module per concern: `walk`, `sync`, `search`, `semantic`, `index_db`, `context`, `vision`, …), `tests/` (124 tests covering walker rules, sync lifecycle, cache-name collision safety, the FTS5 engine, coverage-aware context packets, field-local value extraction, conflict/amount normalization, security/boundary confinement, incremental embedding, vision-note searchability, CLI end-to-end, and purge residue checks).

## License

MIT — see [LICENSE](LICENSE).
