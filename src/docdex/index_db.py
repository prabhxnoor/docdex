"""SQLite + FTS5 lexical index over the extracted text caches.

The `.txt` caches remain the source of truth; this database is a rebuildable
query index that gives real BM25 ranking (which saturates term frequency, so
keyword stuffing no longer wins) and keeps per-query cost flat as the corpus
grows. When the local SQLite build lacks FTS5, the engine reports unavailable
and callers fall back to the pure-Python scorer.

Tables:
  files(rel PK, sha1, mtime_iso, ext, top_folder, tokens)
  chunks(chunk_id PK, rel, chunk_index, start_offset, end_offset, tokens, text)
  chunks_fts  -- FTS5 external-content mirror of chunks.text
  meta(key, value)
"""
from __future__ import annotations

import sqlite3
from typing import List, Optional

from docdex import tokens as tok
from docdex.config import Project
from docdex.inventory import read_inventory
from docdex.search import tokenize

SCHEMA_VERSION = "1"


def connect(project: Project) -> sqlite3.Connection:
    project.state_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(project.index_db_path))
    conn.row_factory = sqlite3.Row
    return conn


def fts5_available(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("CREATE VIRTUAL TABLE temp._fts5_probe USING fts5(x)")
        conn.execute("DROP TABLE temp._fts5_probe")
        return True
    except sqlite3.OperationalError:
        return False


def _init_schema(conn: sqlite3.Connection, has_fts: bool) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS files(
            rel TEXT PRIMARY KEY, sha1 TEXT, mtime_iso TEXT, ext TEXT,
            top_folder TEXT, tokens INTEGER);
        CREATE TABLE IF NOT EXISTS chunks(
            chunk_id INTEGER PRIMARY KEY AUTOINCREMENT,
            rel TEXT, chunk_index INTEGER, start_offset INTEGER,
            end_offset INTEGER, tokens INTEGER, text TEXT);
        CREATE INDEX IF NOT EXISTS chunks_rel ON chunks(rel);
        """
    )
    if has_fts:
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5("
            "text, content='chunks', content_rowid='chunk_id')")
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('schema', ?)",
                 (SCHEMA_VERSION,))
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('fts', ?)",
                 ("1" if has_fts else "0",))


def build(project: Project, force: bool = False, quiet: bool = False) -> dict:
    inventory = read_inventory(project.inventory_path)
    conn = connect(project)
    try:
        has_fts = fts5_available(conn)
        _init_schema(conn, has_fts)

        prior = {r["rel"]: r["sha1"] for r in conn.execute("SELECT rel, sha1 FROM files")}
        current = {}
        for rel, row in inventory.items():
            cache = project.cache_path_for(rel)
            try:
                if cache.exists() and cache.stat().st_size > 0:
                    current[rel] = row
            except OSError:
                continue

        changed = [rel for rel, row in current.items()
                   if force or prior.get(rel) != row.get("sha1")]
        removed = [rel for rel in prior if rel not in current]

        for rel in removed + changed:
            conn.execute("DELETE FROM chunks WHERE rel = ?", (rel,))
            conn.execute("DELETE FROM files WHERE rel = ?", (rel,))

        for rel in changed:
            row = current[rel]
            cache = project.cache_path_for(rel)
            try:
                text = cache.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            conn.execute(
                "INSERT INTO files(rel, sha1, mtime_iso, ext, top_folder, tokens) "
                "VALUES(?,?,?,?,?,?)",
                (rel, row.get("sha1", ""), row.get("mtime_iso", ""),
                 row.get("ext", ""), project.top_folder_for(rel),
                 tok.count_tokens(text)))
            for idx, (start, end, chunk) in enumerate(tok.iter_chunks(text)):
                if len(chunk.strip()) < 3:
                    continue
                conn.execute(
                    "INSERT INTO chunks(rel, chunk_index, start_offset, "
                    "end_offset, tokens, text) VALUES(?,?,?,?,?,?)",
                    (rel, idx, start, end, tok.count_tokens(chunk), chunk))

        if has_fts and (changed or removed):
            # External-content FTS5: rebuild keeps the mirror exactly in sync
            # without trigger bookkeeping. Fast at this scale and never drifts.
            conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
        conn.commit()

        total_files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        total_chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        result = {"fts": has_fts, "files": total_files, "chunks": total_chunks,
                  "reindexed": len(changed), "removed": len(removed)}
        if not quiet:
            engine = "FTS5/BM25" if has_fts else "no-FTS5 (fallback ranking)"
            print(f"Lexical index: files={total_files} chunks={total_chunks} "
                  f"engine={engine} (reindexed {len(changed)})")
        return result
    finally:
        conn.close()


def _match_query(query: str) -> Optional[str]:
    """Turn free text into a safe FTS5 MATCH expression (OR of quoted terms)."""
    terms = tokenize(query)
    if not terms:
        return None
    return " OR ".join(f'"{t}"' for t in terms)


def available(project: Project) -> bool:
    if not project.index_db_path.exists():
        return False
    conn = connect(project)
    try:
        fts = conn.execute("SELECT value FROM meta WHERE key='fts'").fetchone()
        return bool(fts) and fts[0] == "1"
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def search(project: Project, query: str, folder: Optional[str] = None,
           limit: int = 8) -> List[dict]:
    """BM25-ranked chunk hits, best first. Empty list when nothing matches;
    raises FileNotFoundError when the FTS index is unavailable so the caller
    can fall back."""
    if not available(project):
        raise FileNotFoundError("FTS index unavailable")
    match = _match_query(query)
    if match is None:
        return []
    conn = connect(project)
    try:
        sql = (
            "SELECT c.rel AS rel, c.chunk_index AS chunk_index, "
            "c.text AS text, c.tokens AS tokens, c.start_offset AS start_offset, "
            "bm25(chunks_fts) AS bm25 "
            "FROM chunks_fts JOIN chunks c ON c.chunk_id = chunks_fts.rowid "
            "WHERE chunks_fts MATCH ?")
        params: list = [match]
        if folder:
            sql += " AND c.rel LIKE ?"
            params.append(f"%{folder}%")
        sql += " ORDER BY bm25 LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    # bm25() returns lower=better; present a positive relevance for humans.
    return [{
        "rel": r["rel"], "chunk_index": r["chunk_index"], "text": r["text"],
        "tokens": r["tokens"], "start_offset": r["start_offset"],
        "score": round(-r["bm25"], 4),
    } for r in rows]
