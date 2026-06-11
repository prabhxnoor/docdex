"""docdex command-line interface."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from docdex import __version__
from docdex.config import (
    DEFAULT_INDEX_DIR, DEFAULT_WRAPPER, DocdexError, NotAProject, Project,
)


def _project(args: argparse.Namespace) -> Project:
    if args.root:
        return Project.load(Path(args.root).resolve())
    return Project.discover()


# --------------------------------------------------------------------- init
def cmd_init(args: argparse.Namespace) -> int:
    from docdex.scaffold import run_init
    root = Path(args.root).resolve() if args.root else Path.cwd()
    run_init(root, index_dir=args.index, wrapper=None if args.no_wrapper else args.wrapper,
             agent_docs=not args.no_agent_docs)
    return 0


# ------------------------------------------------------------------- status
def cmd_status(args: argparse.Namespace) -> int:
    from docdex import semantic, vision
    from docdex.sync import compute_status
    project = _project(args)
    if not project.inventory_path.exists():
        print(f"docdex status — {project.root}")
        print("  never synced. Run `docdex sync` to build the index.")
        return 1
    s = compute_status(project)
    print(f"docdex status — {project.root}")
    print(f"  inventory rows : {s['inventory_rows']}")
    print(f"  source files   : {s['source_files']}")
    print(f"  state          : {'STALE' if s['stale'] else 'fresh'}")
    print(f"  new {len(s['added'])} / changed {len(s['changed'])} / "
          f"deleted {len(s['deleted'])} / inbox-newer {len(s['update_newer'])}")
    if s["gaps"]:
        print(f"  cache          : GAPS — missing {len(s['missing_cache'])}, "
              f"failed {len(s['failed'])}")
    else:
        print("  cache          : ok")
    if s["no_text"]:
        print(f"  no-text files  : {len(s['no_text'])} (vision/OCR candidates — "
              "not errors)")
    from docdex import index_db
    if index_db.available(project):
        import sqlite3
        conn = index_db.connect(project)
        try:
            n = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        except sqlite3.Error:
            n = "?"
        finally:
            conn.close()
        print(f"  lexical index  : sqlite/fts5, {n} chunks (BM25)")
    else:
        print("  lexical index  : not built")
    meta = semantic.status(project)
    if meta:
        print(f"  semantic index : backend={meta.get('backend')} "
              f"files={meta.get('files')} chunks={meta.get('chunks')}")
    else:
        print("  semantic index : not built")
    vq = vision.queue_status(project, quiet=True)
    if vq["exists"]:
        print(f"  vision queue   : {vq['done']}/{vq['total']} done, {vq['pending']} pending")

    samples = (s["added"][:4] + s["changed"][:4] + s["deleted"][:4]
               + s["missing_cache"][:4] + s["failed"][:4])
    if samples:
        print("\n  samples:")
        for p in samples[:10]:
            print(f"    - {p}")
    if s["stale"]:
        print("\nNext: run `docdex sync`.")
    elif s["gaps"]:
        print("\nNext: run `docdex doctor` to inspect cache gaps.")
    return 1 if s["stale"] or s["gaps"] else 0


# --------------------------------------------------------------------- sync
def cmd_sync(args: argparse.Namespace) -> int:
    from docdex import dumps as dumpsmod
    from docdex import index_db
    from docdex import prefetch as prefetchmod
    from docdex import semantic, vision
    from docdex.sync import SyncLocked, run_sync
    project = _project(args)
    if not args.no_prefetch and not args.dry_run:
        print("[1/6] cloud prefetch")
        prefetchmod.run_prefetch(project, quiet=True)
    print("[2/6] sync inventory + text caches")
    try:
        run_sync(project, dry_run=args.dry_run, no_hash=args.no_hash,
                 no_extract=args.no_extract, backfill=args.backfill)
    except SyncLocked as e:
        raise SystemExit(f"docdex: {e}")
    if args.dry_run:
        return 0
    if not args.no_fts:
        print("[3/6] lexical index (sqlite/fts5)")
        index_db.build(project)
    if not args.no_dumps:
        print("[4/6] context dumps")
        dumpsmod.build_dumps(project, quiet=True)
    if not args.no_embed:
        print("[5/6] semantic index")
        semantic.build(project)
    if not args.no_vision:
        print("[6/6] vision/OCR queue")
        vision.create_queue(project, quiet=True)
    print("\ndone. `docdex status` for a summary.")
    return 0


# ------------------------------------------------------------------- search
def cmd_search(args: argparse.Namespace) -> int:
    from docdex import index_db
    from docdex.search import run_search, snippet, tokenize
    project = _project(args)
    terms = tokenize(args.query)
    if not terms:
        print("query has no searchable terms", file=sys.stderr)
        return 2

    # Prefer the BM25 (FTS5) engine; fall back to the cache scorer when the
    # index hasn't been built or the local SQLite lacks FTS5.
    try:
        rows = index_db.search(project, args.query, folder=args.folder, limit=args.limit)
        if not rows:
            print(f"no indexed text matches: {args.query}", file=sys.stderr)
            return 1
        for rank, r in enumerate(rows, 1):
            snip = snippet(r["text"], args.query, terms)
            print(f"[#{rank}] score={r['score']}  {r['rel']}  chunk={r['chunk_index']}")
            print(f"     {snip}\n")
        return 0
    except FileNotFoundError:
        pass

    hits = run_search(project, args.query, folder=args.folder, limit=args.limit)
    if not hits:
        print(f"no indexed text matches: {args.query}", file=sys.stderr)
        return 1
    for rank, (score, rel, cache_rel, snip) in enumerate(hits, 1):
        print(f"[#{rank}] score={score}  {rel}")
        print(f"     cache: {cache_rel}")
        print(f"     {snip}\n")
    return 0


def cmd_semantic(args: argparse.Namespace) -> int:
    from docdex import semantic
    project = _project(args)
    try:
        hits = semantic.search(project, args.query, folder=args.folder, limit=args.limit)
    except semantic.EmptyQuery as e:
        print(f"docdex: {e}", file=sys.stderr)
        return 2
    except (FileNotFoundError, semantic.EmbeddingError) as e:
        print(f"docdex: {e}", file=sys.stderr)
        return 2
    if not hits:
        print(f"no semantic matches: {args.query}", file=sys.stderr)
        return 1
    for rank, (score, row) in enumerate(hits, 1):
        print(f"[#{rank}] semantic={score:.4f}  {row['path']}  chunk={row['chunk']}")
        print(f"     {row['text']}\n")
    return 0


def cmd_embed(args: argparse.Namespace) -> int:
    from docdex import semantic
    try:
        semantic.build(_project(args), force=args.force)
    except semantic.EmbeddingError as e:
        print(f"docdex: {e}", file=sys.stderr)
        return 2
    return 0


def cmd_dumps(args: argparse.Namespace) -> int:
    from docdex.dumps import build_dumps, parse_size
    max_bytes = parse_size(args.max_bytes) if args.max_bytes else None
    build_dumps(_project(args), folder=args.folder, max_bytes=max_bytes)
    return 0


def cmd_prefetch(args: argparse.Namespace) -> int:
    from docdex.prefetch import run_prefetch
    result = run_prefetch(_project(args), dry_run=args.dry_run, limit=args.limit)
    return 1 if result["failed"] else 0


def cmd_vision(args: argparse.Namespace) -> int:
    from docdex import vision
    project = _project(args)
    if args.action == "create":
        vision.create_queue(project)
        return 0
    result = vision.queue_status(project)
    return 1 if result["pending"] else 0


def cmd_doctor(args: argparse.Namespace) -> int:
    from docdex.doctor import run_doctor
    return run_doctor(_project(args), no_sha=args.no_sha, e2e=args.e2e)


def cmd_dedup(args: argparse.Namespace) -> int:
    from docdex.dedup import run_dedup, run_restore
    project = _project(args)
    if args.restore:
        run_restore(project)
        return 0
    run_dedup(project, apply=args.apply)
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    from docdex import extract as ex
    try:
        sys.stdout.write(ex.extract(args.file))
        return 0
    except Exception as e:  # noqa: BLE001 - report any parser failure
        print(f"docdex: failed to extract {args.file}: {e}", file=sys.stderr)
        return 2


def cmd_info(args: argparse.Namespace) -> int:
    project = _project(args)
    print(f"docdex {__version__}")
    print(f"  root       : {project.root}")
    print(f"  index dir  : {project.index_dir_name}/")
    print(f"  state dir  : {project.rel_to_root(project.state_dir)}/")
    print(f"  wrapper    : {project.wrapper_name or '(none)'}")
    extra = sorted(set(project.config.get('skip_dirs', [])))
    print(f"  skip dirs  : {', '.join(extra) if extra else '(built-ins only)'}")
    return 0


def cmd_purge(args: argparse.Namespace) -> int:
    from docdex.scaffold import run_purge
    return run_purge(_project(args), yes=args.yes, state_only=args.state_only)


# --------------------------------------------------------------------- main
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="docdex",
        description="Token-efficient local document index for LLM workflows.",
    )
    parser.add_argument("--version", action="version", version=f"docdex {__version__}")
    parser.add_argument("--root", help="project root (default: walk up from cwd)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="initialize a docdex project here")
    p.add_argument("--index", default=DEFAULT_INDEX_DIR,
                   help=f"index folder name (default: {DEFAULT_INDEX_DIR})")
    p.add_argument("--wrapper", default=DEFAULT_WRAPPER,
                   help=f"wrapper script name (default: {DEFAULT_WRAPPER})")
    p.add_argument("--no-wrapper", action="store_true", help="skip the ./ctx wrapper")
    p.add_argument("--no-agent-docs", action="store_true",
                   help="skip CLAUDE.md / AGENTS.md templates")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("status", help="freshness and cache coverage")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("sync", help="incrementally (re)index the project")
    p.add_argument("--dry-run", action="store_true", help="diff only; write nothing")
    p.add_argument("--no-hash", action="store_true", help="mtime+size only (no rename detection)")
    p.add_argument("--no-extract", action="store_true", help="inventory only")
    p.add_argument("--backfill", action="store_true", help="re-extract anything lacking a cache")
    p.add_argument("--no-prefetch", action="store_true", help="skip cloud placeholder prefetch")
    p.add_argument("--no-fts", action="store_true", help="skip the SQLite/FTS5 lexical index")
    p.add_argument("--no-dumps", action="store_true", help="skip context dumps")
    p.add_argument("--no-embed", action="store_true", help="skip semantic index")
    p.add_argument("--no-vision", action="store_true", help="skip vision queue refresh")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("search", help="ranked keyword search over extracted text")
    p.add_argument("query")
    p.add_argument("--folder", help="restrict to paths containing this substring")
    p.add_argument("-n", "--limit", type=int, default=8)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("semantic", help="semantic-index search")
    p.add_argument("query")
    p.add_argument("--folder")
    p.add_argument("-n", "--limit", type=int, default=8)
    p.set_defaults(func=cmd_semantic)

    p = sub.add_parser("embed", help="rebuild the semantic index (incremental)")
    p.add_argument("--force", action="store_true", help="re-embed everything")
    p.set_defaults(func=cmd_embed)

    p = sub.add_parser("dumps", help="rebuild per-folder context dumps")
    p.add_argument("--folder", help="only this top-level folder")
    p.add_argument("--max-bytes", help="split dumps larger than this (e.g. 5M)")
    p.set_defaults(func=cmd_dumps)

    p = sub.add_parser("prefetch", help="materialize cloud placeholder files")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--limit", type=int, default=0)
    p.set_defaults(func=cmd_prefetch)

    p = sub.add_parser("vision", help="vision/OCR task queue")
    p.add_argument("action", choices=["create", "status"])
    p.set_defaults(func=cmd_vision)

    p = sub.add_parser("doctor", help="integrity checks")
    p.add_argument("--no-sha", action="store_true", help="skip sha spot-checks")
    p.add_argument("--e2e", action="store_true", help="run the end-to-end sentinel self-test")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("dedup", help="move Update/ duplicates to <index>/bin (dry-run by default)")
    p.add_argument("--apply", action="store_true", help="actually move files")
    p.add_argument("--restore", action="store_true", help="move bin/ contents back to Update/")
    p.set_defaults(func=cmd_dedup)

    p = sub.add_parser("extract", help="extract one file's text to stdout")
    p.add_argument("file")
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("info", help="show project configuration and paths")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("purge", help="remove all docdex files from this project")
    p.add_argument("--yes", action="store_true", help="confirm removal")
    p.add_argument("--state-only", action="store_true",
                   help="only clear _state/ (rebuildable); keep curated files and notes")
    p.set_defaults(func=cmd_purge)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except DocdexError as e:
        print(f"docdex: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
