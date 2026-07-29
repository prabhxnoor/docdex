"""SQLite + FTS5 lexical index over the extracted text caches.

The `.txt` caches remain the source of truth; this database is a rebuildable
query index that gives real BM25 ranking (which saturates term frequency, so
keyword stuffing no longer wins) and keeps per-query cost flat as the corpus
grows. When the local SQLite build lacks FTS5, the engine reports unavailable
and callers fall back to the pure-Python scorer.

Tables:
  files(rel PK, sha1, mtime_iso, ext, top_folder, tokens)
  chunks(chunk_id PK, rel, chunk_index, start_offset, end_offset, tokens, text)
  chunks_fts        -- FTS5 mirror of chunks.text, porter tokenizer (stem class)
  chunks_fts_exact  -- FTS5 mirror of chunks.text, unicode61 (literal surface form)
  meta(key, value)

Two mirrors, one text: a term is discriminating in whichever space it happens to
be rare, so ranking takes the *stronger* of the two BM25 scores rather than
trusting the stemmed space alone. Stemming a term whose literal form is selective
but whose stem class is common ('terms' in 1 chunk → 'term' in 154) otherwise
collapses its IDF and buries the one chunk that carries the answer.
"""
from __future__ import annotations

import sqlite3
import time
from typing import List, Optional

from docdex import tokens as tok
from docdex.config import Project
from docdex.inventory import read_inventory
from docdex.search import tokenize

SCHEMA_VERSION = "4"   # v4: chunks.has_value, a tie-break signal (see _mirror_rows).
                       # v3: dual FTS (porter + unicode61), max-score fusion.
                       # Older versions auto-rebuild on the next sync.

# How many rows to pull from each mirror before fusing. Generous enough that a
# chunk ranked mid-pack in one space still competes, bounded so a corpus-common
# term can't drag the whole table into memory.
FUSE_POOL_FACTOR = 5
FUSE_POOL_MIN = 50

# Relevance differences smaller than this are noise, not signal, and are treated as
# equal so a more useful tie-break can decide. This is what makes `has_value` work:
# when a term appears in nearly every document its IDF collapses and every score
# lands within ~1e-6 of zero, so what remains is length-normalisation jitter — the
# decoy corpus scored -0.000002472 against the answer's -0.000002216, "beating" it on
# nothing at all. Real matches score in the ones and tens, far above this grain.
SCORE_GRAIN = 4


def connect(project: Project) -> sqlite3.Connection:
    project.state_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(project.index_db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _quarantine_corrupt_db(project: Project) -> Optional[str]:
    """Move a corrupt index.db aside so a fresh one can be rebuilt from caches."""
    db = project.index_db_path
    if not db.exists():
        return None
    dest = db.parent / f"{db.name}.corrupt.{time.strftime('%Y%m%d-%H%M%S')}"
    try:
        db.rename(dest)
        return dest.name
    except OSError:
        try:
            db.unlink()
        except OSError:
            pass
        return None


def _open_for_build(project: Project, quiet: bool = False) -> sqlite3.Connection:
    """Connect, verifying the file is a real SQLite database.

    A corrupt or non-SQLite `index.db` makes the first statement raise
    `sqlite3.DatabaseError`. Rather than crash `sync` with a raw traceback, we
    move the bad file aside and return a fresh connection — the index then
    rebuilds from the `.txt` caches (the source of truth) on this same run.
    """
    conn = connect(project)
    try:
        conn.execute("PRAGMA schema_version")  # reads the DB header
        return conn
    except sqlite3.DatabaseError:
        conn.close()
        moved = _quarantine_corrupt_db(project)
        if not quiet:
            where = f" (saved as {moved})" if moved else ""
            print(f"Lexical index: index.db was corrupt; rebuilding from caches{where}")
        return connect(project)


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
            end_offset INTEGER, tokens INTEGER, text TEXT,
            has_value INTEGER DEFAULT 0);
        CREATE INDEX IF NOT EXISTS chunks_rel ON chunks(rel);
        """
    )
    if has_fts:
        # `porter` stems both the indexed text and MATCH terms, so "governing"
        # finds "governed" (recall). unicode61 keeps the existing folding.
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5("
            "text, content='chunks', content_rowid='chunk_id', "
            "tokenize='porter unicode61')")
        # The same text unstemmed: a query term's literal surface form keeps its
        # own IDF here, so a rare plural is not flattened into a common singular.
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts_exact USING fts5("
            "text, content='chunks', content_rowid='chunk_id', "
            "tokenize='unicode61')")
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('schema', ?)",
                 (SCHEMA_VERSION,))
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('fts', ?)",
                 ("1" if has_fts else "0",))


def build(project: Project, force: bool = False, quiet: bool = False) -> dict:
    # Imported here, not at module scope: `context` imports this module, so a
    # top-level import would be circular. Sharing the ONE pattern matters more than
    # import tidiness — a second copy of it here would drift from the extractor's,
    # and then a chunk could be ranked as value-bearing but yield no value.
    from docdex.context import VALUE_RE as value_re

    inventory = read_inventory(project.inventory_path)
    conn = _open_for_build(project, quiet=quiet)
    try:
        # A schema/tokenizer upgrade (e.g. v1 unicode61 -> v2 porter) can't take
        # effect via CREATE ... IF NOT EXISTS on an existing DB. Detect a version
        # change, drop the FTS mirror, and force a full reindex so the new
        # tokenizer applies. Rebuilt from the .txt caches (the source of truth).
        try:
            stored = conn.execute(
                "SELECT value FROM meta WHERE key='schema'").fetchone()
            stored_ver = stored[0] if stored else None
        except sqlite3.Error:
            stored_ver = None
        if stored_ver is not None and stored_ver != SCHEMA_VERSION:
            conn.execute("DROP TABLE IF EXISTS chunks_fts")
            conn.execute("DROP TABLE IF EXISTS chunks_fts_exact")
            force = True
            if not quiet:
                print(f"Lexical index: schema {stored_ver}->{SCHEMA_VERSION}; "
                      "rebuilding once from caches")

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
                    "end_offset, tokens, text, has_value) VALUES(?,?,?,?,?,?,?)",
                    (rel, idx, start, end, tok.count_tokens(chunk), chunk,
                     1 if value_re.search(chunk) else 0))

        if has_fts and (changed or removed):
            # External-content FTS5: rebuild keeps the mirrors exactly in sync
            # without trigger bookkeeping. Fast at this scale and never drifts.
            conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
            conn.execute(
                "INSERT INTO chunks_fts_exact(chunks_fts_exact) VALUES('rebuild')")
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


def _has_value_column(conn) -> bool:
    """Whether this database carries `chunks.has_value` (schema 4+).

    A schema-3 database still answers queries until its next `sync` rebuilds it, so
    the ORDER BY has to adapt rather than fail with "no such column".
    """
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(chunks)")}
    except sqlite3.Error:
        return False
    return "has_value" in cols


def _mirror_rows(conn, table: str, match: str, folder: Optional[str],
                 limit: int) -> List[sqlite3.Row]:
    """Top `limit` rows from one FTS mirror.

    Ordering is BM25 first, so genuine relevance always wins. `has_value` and then
    (rel, chunk_index) only settle **ties** — and BM25 ties happen en masse: when a
    field's label appears in 61 chunks and only one of them states a value, all 61
    score identically and the alphabetical tiebreak alone would decide, which is why
    the one useful chunk could end up ranked 60th and never be read. Preferring a
    chunk that contains a number, date, amount or ID breaks that tie toward the one
    that can actually answer, without ever reordering chunks whose relevance differs.
    """
    has_col = _has_value_column(conn)
    value_col = "c.has_value AS has_value" if has_col else "0 AS has_value"
    sql = (
        f"SELECT c.chunk_id AS chunk_id, c.rel AS rel, "
        f"c.chunk_index AS chunk_index, c.text AS text, c.tokens AS tokens, "
        f"c.start_offset AS start_offset, bm25({table}) AS bm25, {value_col} "
        f"FROM {table} JOIN chunks c ON c.chunk_id = {table}.rowid "
        f"WHERE {table} MATCH ?")
    params: list = [match]
    if folder:
        sql += " AND c.rel LIKE ?"
        params.append(f"%{folder}%")
    # Bucket by SCORE_GRAIN first so noise-level differences don't decide; within a
    # bucket prefer a value-bearing chunk, then fall back to the exact score, then to
    # a stable path order. Chunks whose relevance genuinely differs never reorder.
    order = (f"ROUND(bm25, {SCORE_GRAIN}), "
             + ("has_value DESC, " if has_col else "")
             + "bm25, c.rel, c.chunk_index")
    sql += f" ORDER BY {order} LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def search(project: Project, query: str, folder: Optional[str] = None,
           limit: int = 8) -> List[dict]:
    """BM25-ranked chunk hits, best first. Empty list when nothing matches;
    raises FileNotFoundError when the FTS index is unavailable so the caller
    can fall back.

    Each chunk is scored in both term spaces — literal (unicode61) and stem class
    (porter) — and keeps the *stronger* score. Stemming then only ever adds
    reachable evidence; it can no longer flatten a selective literal term into a
    corpus-common stem and bury the chunk that carries the answer.
    """
    if not available(project):
        raise FileNotFoundError("FTS index unavailable")
    match = _match_query(query)
    if match is None:
        return []
    pool = max(limit * FUSE_POOL_FACTOR, FUSE_POOL_MIN)
    conn = connect(project)
    try:
        try:
            exact_rows = _mirror_rows(conn, "chunks_fts_exact", match, folder, pool)
        except sqlite3.OperationalError as exc:
            # ONLY this exact mirror being absent means "the database predates v3".
            # Corruption, a lock timeout, an I/O error — or some *other* missing
            # table — must surface: answering from the surviving mirror would hand
            # the agent a healthy-looking packet built from a broken index.
            msg = str(exc)
            if "no such table" not in msg or "chunks_fts_exact" not in msg:
                raise
            exact_rows = []
        stem_rows = _mirror_rows(conn, "chunks_fts", match, folder, pool)
    finally:
        conn.close()
    return _fuse([exact_rows, stem_rows], limit)


def _fuse(mirrors: List[List], limit: int) -> List[dict]:
    """Keep each chunk's strongest score across the mirrors, best first.

    Ordering uses full BM25 precision; the score is rounded only for output.
    Rounding first would manufacture ties between genuinely distinct scores and
    hand the ranking to the (rel, chunk_index) tiebreak, which can invert them.
    """
    best: dict = {}
    for rows in mirrors:
        for r in rows:
            score = -r["bm25"]          # bm25() is lower=better; flip it
            prev = best.get(r["chunk_id"])
            if prev is None or score > prev[0]:
                best[r["chunk_id"]] = (score, r)

    def key(pair):
        score, r = pair
        try:
            has_value = r["has_value"]
        except (IndexError, KeyError):
            has_value = 0               # handmade rows in unit tests
        # Same ordering as _mirror_rows, or truncation and final rank would
        # disagree: bucket, then value-bearing, then the exact score at full
        # precision, then a stable path order.
        return (-round(score, SCORE_GRAIN), -has_value, -score,
                r["rel"], r["chunk_index"])

    ordered = sorted(best.values(), key=key)
    return [{
        "rel": r["rel"], "chunk_index": r["chunk_index"], "text": r["text"],
        "tokens": r["tokens"], "start_offset": r["start_offset"],
        "score": round(score, 4),
    } for score, r in ordered[:limit]]
