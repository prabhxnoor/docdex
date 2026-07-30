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
from docdex.config import DocdexError, Project
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


# The tables a schema change may redefine, in an order safe to drop: the FTS mirrors
# name `chunks` as their external content, so they go first. `meta` is deliberately
# absent — it holds the version number that decides whether to do any of this.
DERIVED_TABLES = ("chunks_fts_exact", "chunks_fts", "chunks", "files")

_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)",
    "CREATE TABLE IF NOT EXISTS files("
    "rel TEXT PRIMARY KEY, sha1 TEXT, mtime_iso TEXT, ext TEXT, "
    "top_folder TEXT, tokens INTEGER)",
    "CREATE TABLE IF NOT EXISTS chunks("
    "chunk_id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "rel TEXT, chunk_index INTEGER, start_offset INTEGER, "
    "end_offset INTEGER, tokens INTEGER, text TEXT, "
    "has_value INTEGER DEFAULT 0)",
    "CREATE INDEX IF NOT EXISTS chunks_rel ON chunks(rel)",
)


def _expected_chunk_columns() -> set:
    """The columns `chunks` should have, read from the DDL that creates it.

    Derived rather than declared so this check can never drift from the definition
    it is checking — a hand-written copy of the column list is one more thing to
    forget when a column is added, which is how this release's bug began.
    """
    probe = sqlite3.connect(":memory:")
    try:
        for stmt in _TABLE_SQL:
            probe.execute(stmt)
        return {r[1] for r in probe.execute("PRAGMA table_info(chunks)")}
    finally:
        probe.close()


def _needs_rebuild(conn: sqlite3.Connection, stored_ver: Optional[str]) -> bool:
    """Must the derived tables be recreated before anything is indexed?

    Two independent signals, because the recorded version alone is not enough. A
    database whose `meta` row for 'schema' is missing reports no version at all, so a
    version comparison sees nothing to do while `chunks` still carries its old column
    list — and the crash simply waits for the next changed file to insert a row.
    Found by adversarial review of this very fix. So the real table shape is checked
    too, and either mismatch is enough.
    """
    if stored_ver is not None and stored_ver != SCHEMA_VERSION:
        return True
    cols = {r[1] for r in conn.execute("PRAGMA table_info(chunks)")}
    if not cols:
        return False          # no `chunks` yet: a fresh database, nothing to redo
    return cols != _expected_chunk_columns()


def _init_schema(conn: sqlite3.Connection, has_fts: bool) -> None:
    # One statement at a time, NOT executescript: executescript commits any pending
    # transaction before it runs. That would split the schema upgrade away from the
    # rebuild that completes it, which is the whole reason a half-finished upgrade was
    # able to leave an empty index on disk.
    for stmt in _TABLE_SQL:
        conn.execute(stmt)
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
        try:
            stored = conn.execute(
                "SELECT value FROM meta WHERE key='schema'").fetchone()
            stored_ver = stored[0] if stored else None
        except sqlite3.Error:
            stored_ver = None
        upgrading = _needs_rebuild(conn, stored_ver)

        # Probed before the transaction opens: on a build without FTS5 this raises
        # and is caught, and a failed statement has no business inside the unit of
        # work that protects the index.
        has_fts = fts5_available(conn)

        # ONE transaction for the schema change AND the rebuild that completes it.
        # SQLite makes DDL transactional, but Python's sqlite3 starts an implicit
        # transaction only for DML — measured: `in_transaction` is still False right
        # after a DROP. So the drops below used to commit on the spot and outlive any
        # later failure, leaving mirrors that existed but held nothing, while the
        # `meta` write recording the new version rolled back with the DML and left the
        # same destruction to repeat on the next sync. An explicit BEGIN makes the
        # upgrade all-or-nothing: if anything fails, the index that was working is
        # still the index on disk.
        conn.execute("BEGIN IMMEDIATE")

        if upgrading:
            # A schema change can redefine an EXISTING table, not just add new ones,
            # and `CREATE TABLE IF NOT EXISTS` cannot express that — on a database
            # that already had `chunks`, v0.5.2's new `has_value` column was silently
            # never added and every sync then died inserting into it. Recreating the
            # derived tables handles any change, including a tokenizer change, with no
            # per-version migration ladder to keep correct. It costs nothing: the
            # reindex below already re-reads every chunk from the `.txt` caches, which
            # are the source of truth. `meta` survives, so this stays diagnosable.
            for table in DERIVED_TABLES:
                conn.execute(f"DROP TABLE IF EXISTS {table}")
            force = True
            if not quiet:
                was = stored_ver if stored_ver is not None else "unrecorded"
                print(f"Lexical index: schema {was}->{SCHEMA_VERSION}; "
                      "rebuilding once from caches")

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
    except BaseException:
        # Includes KeyboardInterrupt: a sync interrupted halfway through a rebuild
        # must leave the previous index answering queries, not an empty one.
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        raise
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


class IndexEmptyError(DocdexError):
    """Every FTS mirror is empty, so no query can match anything.

    Deliberately not an empty result list: the caller cannot tell those apart, and
    answering "not found" about an entire corpus — confidently, with no way to
    notice — is the one failure this project exists to refuse.
    """


def indexed_rows(conn: sqlite3.Connection, table: str) -> Optional[int]:
    """How many rows this FTS mirror has actually indexed.

    `SELECT COUNT(*) FROM chunks_fts` cannot answer this. For an external-content
    FTS5 table that count is proxied to the *content* table, so a wiped index still
    reports a full row count — exactly how a corpus-wide index wipe went unnoticed.

    Counting *rows* rather than asking "any terms at all?" was the answer to two
    review findings at once. A corpus of nothing but punctuation legitimately indexes
    zero terms, so a term-based probe would have called a healthy index broken; and a
    mirror that indexed only its first row holds terms, so a term-based probe would
    have called a badly incomplete index healthy. Row count separates all three:
    equal to the chunk count is healthy, zero is wiped, in between is incomplete.

    Returns None when the question can't be answered, which callers report as
    unverified rather than assuming either answer.
    """
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}_docsize").fetchone()[0]
    except sqlite3.Error:
        return None


def _existing_mirrors(conn: sqlite3.Connection) -> List[str]:
    """Which FTS mirrors this database actually has.

    Checked separately from probing them so "this schema never had that mirror" is
    never confused with "the probe failed" — an older single-mirror database is
    normal, an unanswerable probe is not.
    """
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
            "('chunks_fts', 'chunks_fts_exact')").fetchall()
    except sqlite3.Error:
        return []
    return [r[0] for r in rows]


def index_state(conn: sqlite3.Connection) -> dict:
    """What the lexical index actually contains, for reporting and for refusing.

    `chunks` is the stored text; the mirrors are what makes it findable. The state
    that matters is "text present, index empty" — what a failed upgrade left behind,
    in which every query returns nothing and looks like a clean miss.

    `unverified` exists because of adversarial review of this fix: an unanswerable
    probe used to be filtered out, which made an index that could not be checked
    indistinguishable from one checked and found healthy. Not knowing is reported as
    not knowing.
    """
    try:
        chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    except sqlite3.Error:
        chunks = 0
    present = _existing_mirrors(conn)
    mirrors = {t: indexed_rows(conn, t) for t in present}
    counted = [n for n in mirrors.values() if n is not None]
    return {
        "chunks": chunks,
        "mirrors": mirrors,
        "missing": [t for t in ("chunks_fts", "chunks_fts_exact")
                    if t not in present],
        # Nothing indexed at all, though text is stored: the wipe.
        "empty": bool(chunks) and bool(counted) and all(n == 0 for n in counted),
        # Some rows indexed but not all: a rebuild that stopped early would answer
        # for part of the corpus and silently miss the rest.
        "incomplete": bool(chunks) and any(0 < n < chunks for n in counted),
        "unverified": bool(chunks) and any(n is None for n in mirrors.values()),
        # One mirror indexed, the other empty: ranking degrades without failing.
        "partial": bool(chunks) and any(n > 0 for n in counted)
                   and any(n == 0 for n in counted),
    }


def _refuse_if_index_is_empty(project: Project) -> None:
    conn = connect(project)
    try:
        state = index_state(conn)
    finally:
        conn.close()
    if state["empty"]:
        raise IndexEmptyError(
            f"the lexical index holds no searchable terms, though "
            f"{state['chunks']:,} chunks of text are stored — every query would "
            f"report no matches. Run `docdex sync` to rebuild the index.")
    if state["incomplete"]:
        indexed = min(n for n in state["mirrors"].values() if n is not None)
        raise IndexEmptyError(
            f"the lexical index covers only {indexed:,} of {state['chunks']:,} "
            f"stored chunks, so this query searched part of your documents — a "
            f"result of 'no matches' cannot be trusted. Run `docdex sync` to "
            f"rebuild the index.")
    if state["unverified"]:
        # A miss we cannot vouch for is not reported as a miss. Returning [] here
        # would be indistinguishable from "your documents don't say that".
        raise IndexEmptyError(
            f"this query matched nothing, and whether the lexical index is intact "
            f"could not be verified ({state['chunks']:,} chunks of text are "
            f"stored) — so 'no matches' cannot be trusted. Run `docdex sync` to "
            f"rebuild the index.")


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
    rows = _fuse([exact_rows, stem_rows], limit)
    if not rows:
        # Only on a miss, so the healthy path pays nothing: distinguish "this corpus
        # doesn't say that" from "this index can't answer anything".
        _refuse_if_index_is_empty(project)
    return rows


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
