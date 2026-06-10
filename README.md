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
