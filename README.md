# docdex

**A token-efficient local document index for LLM workflows.** Point it at a folder of documents — Word, PowerPoint, Excel, PDF, Markdown, anything — and it builds a private, incremental, greppable index that lets Claude (or Codex, Gemini, or you) answer questions from thousands of files without ever loading them all.

- **Local-first.** No daemons, no vector database, no cloud services, no API keys. Plain files on disk that you can inspect, grep, and back up.
- **Incremental.** Re-sync only touches new, changed, renamed, or deleted files. Renames are detected by content hash and never re-extracted.
- **LLM-native.** `docdex init` scaffolds `CLAUDE.md` / `AGENTS.md` instructions so any coding agent dropped into the folder knows exactly how to retrieve context cheaply, in tiers, instead of bulk-loading your corpus.
- **Zero residue.** Everything docdex creates lives in three known places. `docdex purge` removes them completely.

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
Budget: 1200 tok  |  Used: ~130  |  Index: fresh

## Likely answers (cited)
- The aggregate liability cap under this agreement is INR 4.2 crore.  [Contracts/scan_0231 copy.md ·0]
- Payment terms are net-45 from invoice date.  [Contracts/scan_0231 copy.md ·0]

## Evidence
[E1] Contracts/scan_0231 copy.md ·0  (score 5.31)
  "...aggregate liability cap under this agreement is INR 4.2 crore. Payment terms are net-45..."
```

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

`init` also installs a `./ctx` wrapper in the project root, so `./ctx sync`, `./ctx search "..."` work for anyone (and any LLM) in that directory without knowing about docdex — from any subdirectory too.

## Commands

| Command | What it does |
|---|---|
| `docdex init` | Initialize a project. `--index NAME` to rename the index folder, `--no-agent-docs` / `--no-wrapper` to skip extras. |
| `docdex sync` | Incremental reindex: cloud prefetch → inventory + text caches → context dumps → semantic index → vision queue. Flags to skip stages: `--no-prefetch`, `--no-dumps`, `--no-embed`, `--no-vision`; plus `--dry-run`, `--backfill`, `--no-hash`. |
| `docdex context "task"` | Build a token-budgeted evidence packet (cited answers, excerpts, gaps). `--budget N`, `--folder X`, `--from-file form.md`, `--explain`. |
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

The default backend (`local-hash-v1`) is a deterministic hashed-feature embedding over words, bigrams, and character n-grams: private, dependency-free, surprisingly effective at narrowing candidates — but it is *not* a neural model. For real embeddings, point `DOCDEX_EMBED_CMD` at any command that reads text on stdin and prints a JSON float array:

```bash
export DOCDEX_EMBED_CMD="my-embed-cli --model text-embedding-3-small"
docdex embed --force   # rebuild under the new backend
```

Rebuilds are incremental either way: only files whose content hash changed get re-embedded.

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

- Designed for corpora up to roughly **10,000 files**. Sync on a ~5,000-file corpus is seconds when warm; the first full extraction is the only slow run.
- Files ≥ 200 MB are inventoried but not hashed (no rename detection for them).
- Semantic search scans the index linearly per query — simple and dependable, a few seconds on large corpora. If you outgrow it, plug in an external embedding backend or a real vector store.
- Keyword `search` reads every cache per query; same trade-off, same scale.
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

Layout: `src/docdex/` (one module per concern: `walk`, `sync`, `search`, `semantic`, `vision`, …), `tests/` (42 tests covering walker rules, sync lifecycle, cache-name collision safety, incremental embedding, vision-note searchability, CLI end-to-end, and purge residue checks).

## License

MIT — see [LICENSE](LICENSE).
