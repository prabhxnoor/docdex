"""Upgrading an existing index must never leave it worse than it was.

Reported as a bug: `docdex sync` stopped working after v0.5.2, and keyword search
returned nothing at all across a 10,498-file corpus. Reproduced with the real v0.5.1
code that had built the index, then the shipped v0.5.3 code run over it:

    [3/6] lexical index (sqlite/fts5)
    Lexical index: schema 3->4; rebuilding once from caches
    sqlite3.OperationalError: table chunks has no column named has_value

Three separate defects, each with its own test below.

**D1 — the upgrade could not add a column.** `_init_schema` creates every table with
`CREATE TABLE IF NOT EXISTS`. On a database that already exists that is a no-op, so
`chunks` kept its old column list and the `has_value` insert introduced by v0.5.2
failed. The upgrade path only ever handled *new tables* (v0.5.1 added a second FTS
mirror, which `IF NOT EXISTS` does create), so nothing had exercised a changed
definition of an existing table.

**D2 — the failed upgrade destroyed a working index.** The version check drops both
FTS mirrors before rebuilding. Python's `sqlite3` opens an implicit transaction for
DML only, never for DDL — measured: `isolation_level=''` and `in_transaction` still
False right after a `DROP`. So the drop was committed immediately and outlived the
crash, while the `INSERT OR REPLACE INTO meta` that records the new version was
rolled back with the rest of the DML. The result on disk: `chunks` intact with all
92,490 rows of text, both mirrors present but empty (2 rows of 7 bytes in the FTS
shadow tables), and `meta.schema` still reading '3' — so every subsequent sync
repeated the same destruction. The text was never at risk; the index was.

**D3 — nothing said so.** An empty FTS index matches nothing, and no match is
reported as `no indexed text matches: <query>` — identical to a genuine miss. For a
corpus that certainly contained the word, docdex answered "not here" about all
10,498 files, confidently and with exit code 1. That is the failure mode this
project exists to refuse, and it is why the broken index went unnoticed.

Every helper below is defined locally on purpose. These tests must fail on the
PREVIOUS release to prove they catch the old behaviour, and a test that imports a
symbol this release introduces fails at collection instead — proving only that the
API changed. The release gate rejects that, correctly.
"""
from __future__ import annotations

import sqlite3

import pytest

from docdex import index_db
from docdex.scaffold import run_init
from docdex.sync import run_sync

# ---------------------------------------------------------------- local helpers


def sync_index(project):
    """What `docdex sync` does, in the order the CLI does it.

    `run_sync` refreshes the inventory and the `.txt` caches; building the lexical
    index is a separate step (`[3/6]`) that the CLI drives. Both are needed here —
    the bug lives in the second one, and it reads what the first one wrote.
    """
    run_sync(project, quiet=True)
    return index_db.build(project, quiet=True)


def open_db(project):
    return sqlite3.connect(str(project.index_db_path))


def rows_indexed(project, table="chunks_fts"):
    """How many rows the FTS mirror has actually indexed.

    `SELECT COUNT(*) FROM chunks_fts` is useless here: for an external-content FTS5
    table that reads the *content* table, so it reports a full row count even when
    the index is empty — which is exactly how this bug stayed invisible.

    Row count, not "does it hold any term". Adversarial review of these tests
    pointed out that a mirror which indexed only its FIRST chunk does hold terms, so
    a term-based check would call a badly incomplete index healthy.
    """
    conn = open_db(project)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}_docsize").fetchone()[0]
    finally:
        conn.close()


def chunks_stored(project):
    conn = open_db(project)
    try:
        return conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    finally:
        conn.close()


def chunk_columns(project):
    conn = open_db(project)
    try:
        return [r[1] for r in conn.execute("PRAGMA table_info(chunks)")]
    finally:
        conn.close()


def downgrade_to(project, version):
    """Rewrite the index so it looks exactly like one built by an older docdex.

    Faithfulness matters more than brevity here, so this reproduces the real shape
    of each old schema rather than only flipping `meta.schema`:

      * schema 3 (v0.5.1): two FTS mirrors, `chunks` WITHOUT `has_value`.
      * schema 2 (v0.5.0): one porter mirror, no `chunks_fts_exact`, no `has_value`.

    The mirrors are rebuilt afterwards so the old index is genuinely populated —
    otherwise a test claiming the previous index survived a failed upgrade would
    pass over an index that was already empty.
    """
    conn = open_db(project)
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(chunks)")]
        if "has_value" in cols:
            keep = [c for c in cols if c != "has_value"]
            cols_sql = ", ".join(keep)
            conn.executescript(
                f"""
                CREATE TABLE chunks_old(
                    chunk_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rel TEXT, chunk_index INTEGER, start_offset INTEGER,
                    end_offset INTEGER, tokens INTEGER, text TEXT);
                INSERT INTO chunks_old({cols_sql}) SELECT {cols_sql} FROM chunks;
                DROP TABLE chunks;
                ALTER TABLE chunks_old RENAME TO chunks;
                CREATE INDEX IF NOT EXISTS chunks_rel ON chunks(rel);
                """
            )
        if version == 2:
            conn.execute("DROP TABLE IF EXISTS chunks_fts_exact")
        conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('schema',?)",
                     (str(version),))
        conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
        if version >= 3:
            conn.execute(
                "INSERT INTO chunks_fts_exact(chunks_fts_exact) VALUES('rebuild')")
        conn.commit()
    finally:
        conn.close()


# One unique marker per document, and — in the long contract — one near the start,
# one in the middle and one at the end. Adversarial review of these tests noted that
# single-chunk fixtures cannot tell a full reindex from one that stopped after the
# first chunk, and that asserting one term from one file proves nothing about the
# other files a migration touched.
MARKERS = {
    "contract.txt": ["Zopfli-Alpha-4471", "Zopfli-Middle-8823", "Zopfli-Omega-9915"],
    "policy.md": ["Quernstone-Policy-3308"],
    "annexure.txt": ["Vantablack-Annex-7742"],
}


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    filler = ("This clause restates the obligations of both parties at length so "
              "that the document spans several chunks rather than one. ")
    (root / "contract.txt").write_text(
        f"Payment terms are Net-45. {MARKERS['contract.txt'][0]}\n"
        + filler * 60
        + f"\nLiability cap is INR 4.2 crore. {MARKERS['contract.txt'][1]}\n"
        + filler * 60
        + f"\nSignatures follow. {MARKERS['contract.txt'][2]}\n",
        encoding="utf-8")
    (root / "policy.md").write_text(
        f"# Policy\n\nGoverning law is the laws of Karnataka, India. "
        f"{MARKERS['policy.md'][0]}\n", encoding="utf-8")
    (root / "annexure.txt").write_text(
        f"Annexure A lists the deliverables. {MARKERS['annexure.txt'][0]}\n",
        encoding="utf-8")
    p = run_init(root, quiet=True)
    sync_index(p)
    return p


def assert_whole_corpus_searchable(project, why):
    """Every marker in every document must be findable, and every chunk indexed.

    The migration reindexes the entire corpus from the `.txt` caches, so "one term
    from one file still works" is far too weak a claim — it would hold for a rebuild
    that dropped most of the corpus.
    """
    for name, markers in MARKERS.items():
        for marker in markers:
            rows = index_db.search(project, marker, limit=5)
            assert rows, f"{why}: {marker!r} (in {name}) is no longer findable"
            assert any(marker in r["text"] for r in rows)
    total = chunks_stored(project)
    for table in ("chunks_fts", "chunks_fts_exact"):
        assert rows_indexed(project, table) == total, (
            f"{why}: {table} indexed {rows_indexed(project, table)} of {total} "
            f"chunks, so part of the corpus is unsearchable")


# ------------------------------------------------------- D1: the upgrade works


@pytest.mark.parametrize("old_version", [3, 2])
def test_sync_upgrades_an_existing_index_and_search_still_works(project, old_version):
    """The reported bug, at its own level: sync an old index, then search it.

    Asserts on the outcome the user cares about rather than on the exception, so it
    keeps holding if the internals change again.
    """
    downgrade_to(project, old_version)
    assert rows_indexed(project) > 0, "fixture is not a populated old index"
    before = chunks_stored(project)

    sync_index(project)

    rows = index_db.search(project, "net-45", limit=5)
    assert rows, (
        f"searching an index upgraded from schema {old_version} returned nothing; "
        f"the corpus contains 'Net-45'")
    assert any("Net-45" in r["text"] for r in rows)
    assert chunks_stored(project) == before, "the upgrade changed the chunk count"
    assert_whole_corpus_searchable(
        project, f"after upgrading from schema {old_version}")


@pytest.mark.parametrize("old_version", [3, 2])
def test_the_upgrade_leaves_a_complete_index_not_an_empty_one(project, old_version):
    """The specific damage seen on the real corpus: mirrors present but empty.

    Both mirrors must carry terms afterwards, since retrieval scores every chunk in
    both spaces and keeps the stronger — a silently empty mirror would degrade
    ranking rather than fail, which is harder to notice than a crash.
    """
    downgrade_to(project, old_version)
    total = chunks_stored(project)
    assert total > 3, "fixture must span several chunks for this to mean anything"
    sync_index(project)

    for table in ("chunks_fts", "chunks_fts_exact"):
        got = rows_indexed(project, table)
        assert got == total, (
            f"{table} indexed {got} of {total} chunks after the upgrade. Zero means "
            f"every query reports 'no matches' for the whole corpus; anything "
            f"between means it silently answers for only part of it")


def test_the_upgrade_records_the_new_version_so_it_runs_once(project):
    """`meta.schema` must advance.

    It did not, because the write was DML and was rolled back by the crash while the
    destructive DDL was not. So the drop-and-fail repeated on every single sync.
    """
    downgrade_to(project, 3)
    sync_index(project)

    conn = open_db(project)
    try:
        stored = conn.execute(
            "SELECT value FROM meta WHERE key='schema'").fetchone()[0]
    finally:
        conn.close()
    assert stored == index_db.SCHEMA_VERSION, (
        f"schema still reads {stored!r}; the upgrade will run again next sync")

    # "Runs once" is the actual claim, so run it again and require it to be a no-op.
    # Adversarial review of these tests: checking only the recorded version would
    # stay green even if every later sync dropped and rebuilt the whole index.
    again = index_db.build(project, quiet=True)
    assert again["reindexed"] == 0, (
        f"the sync after the upgrade reindexed {again['reindexed']} file(s); the "
        f"rebuild is supposed to happen once, not on every sync")


@pytest.mark.parametrize("old_version", [3, 2])
def test_the_new_column_is_populated_by_the_upgrade_not_just_added(project,
                                                                  old_version):
    """Adding the column is not enough — its values decide ranking ties.

    v0.5.2 breaks near-equal BM25 ties toward chunks that contain a value. An
    upgraded index whose `has_value` was all-zero would lose that behaviour quietly
    and only show up as slightly worse answers.

    Two points from adversarial review of this test: `flagged > 0` would also pass if
    EVERY chunk were flagged, which would hand label-only decoys the same ranking
    preference as real answers — so the flag is now checked to discriminate. And the
    original only covered schema 3, so a v0.5.0 user could have lost the behaviour
    silently; it is parameterized over both old schemas.
    """
    downgrade_to(project, old_version)
    assert "has_value" not in chunk_columns(project)

    sync_index(project)

    assert "has_value" in chunk_columns(project)
    conn = open_db(project)
    try:
        flagged = conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE has_value = 1").fetchone()[0]
        total = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        # The chunk carrying "Net-45" holds a value; the filler prose holds none.
        with_value = conn.execute(
            "SELECT has_value FROM chunks WHERE text LIKE '%Net-45%'").fetchone()[0]
        no_value = conn.execute(
            "SELECT has_value FROM chunks WHERE text LIKE '%obligations of both%' "
            "AND text NOT LIKE '%Net-45%' AND text NOT LIKE '%crore%' "
            "AND text NOT LIKE '%Annexure%'").fetchone()
    finally:
        conn.close()
    assert total > 0
    assert with_value == 1, "the chunk containing 'Net-45' was not flagged"
    assert flagged < total, (
        "every chunk was flagged value-bearing, so the flag cannot break a tie "
        "toward the chunk that actually answers")
    if no_value is not None:
        assert no_value[0] == 0, "a chunk with no value in it was flagged"


def test_a_widened_value_signal_is_recomputed_on_an_existing_index(tmp_path):
    """The half-working release this project has now nearly shipped twice.

    `has_value` is derived data, computed once when a chunk is indexed. When a release
    WIDENS what counts as a value — v0.5.7 added a party defined by apposition, v0.5.8
    added a company presented as a field's value — the reading half starts working
    immediately and the retrieval half stays frozen at whatever the old code stored,
    on every index already on disk. Nothing but a schema-version change recomputes it,
    because the column is already there and `CREATE TABLE IF NOT EXISTS` has nothing to
    do.

    v0.5.2 shipped the crashing version of this mistake. v0.5.7 caught the silent
    version of it only by noticing that a real sync reindexed 22 files instead of
    10,521. This test is that observation, made mechanical: an index built by the
    previous release, holding the previous release's answer for a chunk, must come out
    of `sync` holding this release's answer.
    """
    root = tmp_path / "widened"
    root.mkdir()
    (root / "party.txt").write_text("Vendor: Acme Industries Pvt Ltd\n",
                                    encoding="utf-8")
    project = run_init(root, quiet=True)
    sync_index(project)

    # Make it look exactly like an index the PREVIOUS release left behind: the column
    # is present and its stored answer is the old one.
    conn = open_db(project)
    try:
        current = conn.execute(
            "SELECT value FROM meta WHERE key = 'schema'").fetchone()[0]
        previous = str(int(current) - 1)
        conn.execute("UPDATE chunks SET has_value = 0 WHERE rel = 'party.txt'")
        conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('schema',?)",
                     (previous,))
        conn.commit()
        stale = conn.execute(
            "SELECT has_value FROM chunks WHERE rel = 'party.txt'").fetchone()[0]
    finally:
        conn.close()
    assert stale == 0, "the fixture failed to plant the previous release's answer"

    sync_index(project)

    conn = open_db(project)
    try:
        got = conn.execute(
            "SELECT has_value FROM chunks WHERE rel = 'party.txt'").fetchone()[0]
        version = conn.execute(
            "SELECT value FROM meta WHERE key = 'schema'").fetchone()[0]
    finally:
        conn.close()
    assert version == current, (
        f"sync left the index at schema {version}, so it will re-upgrade every time")
    assert got == 1, (
        "a chunk this release counts as value-bearing kept the previous release's "
        "answer, so the retrieval half of the feature is inert on every existing index")
    # And the rebuild that recomputed it left the text searchable, not an empty index.
    hits = index_db.search(project, "Acme Industries", limit=5)
    assert hits and any("Acme Industries" in h["text"] for h in hits), (
        "the recomputation rebuilt an index that can no longer find the document")


# ------------------------------- D2: a failed upgrade must not destroy the index


def test_a_failed_upgrade_leaves_the_previous_index_intact(project, monkeypatch):
    """The defect that actually cost the user his index.

    An upgrade that cannot finish must roll back to the working index, not leave an
    empty one behind. SQLite makes DDL transactional, so this is available — it just
    was not being used.
    """
    downgrade_to(project, 3)
    before = {t: rows_indexed(project, t)
              for t in ("chunks_fts", "chunks_fts_exact")}
    before_chunks = chunks_stored(project)
    assert all(n > 0 for n in before.values()) and before_chunks > 3

    real = index_db.tok.count_tokens
    state = {"calls": 0, "armed": True, "ddl_had_run": None}

    def fail_partway(text):
        state["calls"] += 1
        if state["armed"] and state["calls"] > 2:  # DDL done, some rows already in
            # Adversarial review of this test: injecting a failure that happens
            # BEFORE the destructive step proves nothing about atomicity, and the
            # test would stay green even if a later failure still committed the
            # damage. So record what the transaction had already done. Read on a
            # separate connection, which cannot see uncommitted work — if the DDL
            # were committed (the old bug), the mirrors would look wiped from here.
            probe = sqlite3.connect(str(project.index_db_path))
            try:
                state["ddl_had_run"] = probe.execute(
                    "SELECT COUNT(*) FROM chunks_fts_docsize").fetchone()[0]
            except sqlite3.Error as exc:
                state["ddl_had_run"] = f"unreadable: {exc}"
            finally:
                probe.close()
            raise RuntimeError("simulated failure during rebuild")
        return real(text)

    monkeypatch.setattr(index_db.tok, "count_tokens", fail_partway)
    with pytest.raises(RuntimeError):
        index_db.build(project, quiet=True)
    # Disarmed rather than undone: `monkeypatch` is one function-scoped instance
    # shared with the autouse fixture that redirects the cache directory, so undo()
    # here would also drop DOCDEX_CACHE_DIR and point the project at real state.
    state["armed"] = False

    assert state["ddl_had_run"] is not None, (
        "the injected failure never fired, so nothing about atomicity was tested")
    assert state["ddl_had_run"] == before["chunks_fts"], (
        f"another connection saw {state['ddl_had_run']} indexed rows mid-upgrade, "
        f"not the original {before['chunks_fts']} — the destructive step was "
        f"committed before the rebuild finished, which is the bug itself")

    # BOTH mirrors, not just the default one: a rollback that restored the porter
    # mirror and left the exact mirror empty would still answer "net-45" and hide a
    # ranking change behind a passing test.
    for table, was in before.items():
        assert rows_indexed(project, table) == was, (
            f"{table} lost rows to a failed upgrade: {was} -> "
            f"{rows_indexed(project, table)}")
    assert chunks_stored(project) == before_chunks
    assert_whole_corpus_searchable(project, "after a failed upgrade rolled back")


# ----------------------------------- D3: an empty index must never read as a miss


def empty_the_mirrors(project, tables=("chunks_fts", "chunks_fts_exact")):
    """Reproduce the on-disk state the crash left: mirrors present but holding nothing."""
    conn = open_db(project)
    try:
        for table in tables:
            conn.execute(f"INSERT INTO {table}({table}) VALUES('delete-all')")
        conn.commit()
    finally:
        conn.close()


def test_search_does_not_report_an_empty_index_as_no_matches(project):
    """"No matches" and "no index" must not look the same to the caller.

    This is the difference between docdex being wrong and docdex being unhelpful.
    `chunks` still holds the text, so the claim "nothing here" is provably false.
    """
    empty_the_mirrors(project)
    assert rows_indexed(project) == 0

    with pytest.raises(Exception) as exc:
        index_db.search(project, "net-45", limit=5)
    message = str(exc.value).lower()
    # The type by NAME, not by import: naming it pins the specific error instead of
    # accepting any exception, without importing a symbol this release introduces
    # (which would make the file error rather than fail against the base tree).
    assert type(exc.value).__name__ == "IndexEmptyError", (
        f"expected a specific index error, got {type(exc.value).__name__}")
    assert "index" in message, (
        f"search failed to distinguish an empty index from a genuine miss; "
        f"got {exc.value!r}")
    assert "sync" in message, "the error should name the command that repairs it"


def test_the_cli_tells_the_user_the_index_is_empty(project, capsys):
    """What the user actually sees. The old output was `no indexed text matches`,
    with exit code 1 — indistinguishable from a real miss."""
    from docdex.cli import main

    empty_the_mirrors(project)
    code = main(["--root", str(project.root), "search", "net-45"])
    out = capsys.readouterr()
    combined = (out.out + out.err).lower()

    assert code != 0
    assert "no indexed text matches" not in combined, (
        "the CLI still reports a broken index as a plain miss")
    assert "sync" in combined, (
        f"the CLI did not tell the user how to repair it; said: {combined!r}")


def test_the_cli_still_answers_a_healthy_search(project, capsys):
    """The counterweight to the test above, from adversarial review: a CLI that
    rejected *every* search would satisfy it. This fails if it does."""
    from docdex.cli import main

    code = main(["--root", str(project.root), "search", "net-45"])
    out = capsys.readouterr()
    assert code == 0, f"a healthy search failed: {out.out + out.err}"
    assert "Net-45" in out.out


def test_the_cli_reports_a_genuine_miss_as_a_miss(project, capsys):
    """And a real miss on a healthy index stays a plain miss, not a diagnosis."""
    from docdex.cli import main

    code = main(["--root", str(project.root), "search", "zzzznotpresentzzzz"])
    out = capsys.readouterr()
    combined = out.out + out.err
    assert code != 0
    assert "no indexed text matches" in combined.lower(), (
        f"a genuine miss was reported as something else: {combined!r}")


def test_doctor_reports_an_empty_index(project, capsys):
    """`docdex doctor` exists to answer "is this thing healthy". An index that
    matches nothing at all is the clearest possible unhealthy state, and it was
    passing every check."""
    from docdex.doctor import run_doctor

    empty_the_mirrors(project)
    code = run_doctor(project, no_sha=True)
    out = capsys.readouterr().out.lower()

    assert code != 0, f"doctor passed on an index holding no terms:\n{out}"
    assert "index" in out


# ------------------ hardening from external review of this release --------------


def forget_the_recorded_version(project):
    """Drop the `meta` row that records the schema version, leaving the tables as
    they are — a database that cannot say how old it is."""
    conn = open_db(project)
    try:
        conn.execute("DELETE FROM meta WHERE key='schema'")
        conn.commit()
    finally:
        conn.close()


def test_an_index_with_no_recorded_version_is_still_upgraded(project):
    """Found by adversarial review: the version string alone was not enough.

    With no recorded version there is nothing to compare, so the upgrade was skipped
    while `chunks` still carried its old column list. Nothing failed immediately
    either — with no file changed there is no row to insert — so the crash waited for
    the next edited document. Reproduced exactly that way before the fix.
    """
    downgrade_to(project, 3)
    forget_the_recorded_version(project)

    # An ordinary edit: this is what makes sync insert rows, and what used to crash.
    (project.root / "contract.txt").write_text(
        "Payment terms are Net-60 now.\nLiability cap is INR 5 crore.\n",
        encoding="utf-8")
    sync_index(project)

    rows = index_db.search(project, "net-60", limit=5)
    assert rows, "an index with no recorded version was left unusable after an edit"
    assert "has_value" in chunk_columns(project)


def test_a_mirror_that_cannot_be_checked_is_not_reported_as_healthy(project,
                                                                   monkeypatch):
    """Found by adversarial review, and the sharpest finding of the round.

    An unanswerable probe was filtered out of the verdict, so an index whose health
    could not be established was indistinguishable from one established as healthy.
    "I could not check" must never render as "checked, fine" — that is the whole
    failure this release is about, one level up.
    """
    monkeypatch.setattr(index_db, "indexed_rows", lambda conn, table: None)

    conn = open_db(project)
    try:
        state = index_db.index_state(conn)
    finally:
        conn.close()
    assert state["unverified"], f"unverifiable mirrors read as fine: {state}"

    from docdex.doctor import Doctor
    d = Doctor(project)
    d.check_lexical_index()
    _name, ok, detail = d.results[-1]
    assert not ok, f"doctor passed an index it could not verify: {detail}"


def test_search_will_not_call_an_unverifiable_index_a_miss(project, monkeypatch):
    """The same finding where it reaches the user: a miss docdex cannot vouch for is
    not reported as a miss."""
    monkeypatch.setattr(index_db, "indexed_rows", lambda conn, table: None)

    with pytest.raises(Exception) as exc:
        index_db.search(project, "a-term-that-is-genuinely-absent-zzz", limit=5)
    assert "sync" in str(exc.value).lower()


def test_a_query_that_genuinely_misses_still_returns_empty(project):
    """The counterweight: on a verified-healthy index, a real miss is an empty list,
    not an error. Without this the checks above could be satisfied by refusing
    everything."""
    rows = index_db.search(project, "zzzznotinthecorpuszzzz", limit=5)
    assert rows == []


def test_doctor_passes_on_a_healthy_index(project, capsys):
    """Guards the check above from being a permanent failure that everyone learns to
    ignore — and asserts the lexical line was actually printed.

    Adversarial review: without the second assertion the whole check could be deleted
    and this test would still pass, since `run_doctor` would simply return 0.
    """
    from docdex.doctor import run_doctor

    code = run_doctor(project, no_sha=True)
    out = capsys.readouterr().out
    assert code == 0, f"doctor failed on a freshly synced index:\n{out}"
    assert "lexical index" in out.lower(), "doctor never inspected the lexical index"
    assert f"{chunks_stored(project):,} chunks searchable" in out, (
        f"doctor did not report what it actually verified:\n{out}")


def test_doctor_reports_one_empty_mirror(project, capsys):
    """Only ONE mirror wiped: retrieval still answers, so nothing fails — it just
    ranks worse, because a term selective in the wiped space no longer competes.

    Found by adversarial review: the empty-index test wiped both mirrors, so this
    branch of the check had no test at all.
    """
    from docdex.doctor import run_doctor

    empty_the_mirrors(project, tables=("chunks_fts_exact",))
    assert rows_indexed(project, "chunks_fts") > 0
    assert rows_indexed(project, "chunks_fts_exact") == 0

    code = run_doctor(project, no_sha=True)
    out = capsys.readouterr().out
    assert code != 0, f"doctor passed with one mirror wiped:\n{out}"
    assert "chunks_fts_exact" in out


def test_doctor_reports_a_mirror_that_is_absent_entirely(project, capsys):
    """An index predating the second mirror is reported rather than described as two
    healthy term spaces. Found by adversarial review of `index_state`."""
    from docdex.doctor import run_doctor

    conn = open_db(project)
    try:
        conn.execute("DROP TABLE chunks_fts_exact")
        conn.commit()
    finally:
        conn.close()

    code = run_doctor(project, no_sha=True)
    out = capsys.readouterr().out
    assert code != 0, f"doctor passed with a mirror missing entirely:\n{out}"
    assert "chunks_fts_exact" in out
    assert "2 term spaces" not in out, "doctor claimed a term space that is not there"


def test_a_corpus_with_no_indexable_words_is_not_called_broken(tmp_path):
    """The false alarm this check must not raise, found by adversarial review.

    A document of nothing but punctuation stores chunks and legitimately indexes zero
    *terms* — so a term-based emptiness probe would call a perfectly healthy index
    corrupt and demand a sync that cannot change anything. Counting indexed *rows*
    distinguishes the two, which is why the probe counts rows.
    """
    root = tmp_path / "punct"
    root.mkdir()
    (root / "marks.txt").write_text("— !!! ??? ;;; *** ###\n", encoding="utf-8")
    p = run_init(root, quiet=True)
    sync_index(p)

    conn = open_db(p)
    try:
        state = index_db.index_state(conn)
    finally:
        conn.close()
    if not state["chunks"]:
        pytest.skip("this corpus stored no chunks, so there is nothing to misjudge")
    assert not state["empty"], (
        f"a punctuation-only corpus was reported as a wiped index: {state}")
    assert index_db.search(p, "anything", limit=3) == [], (
        "a genuine miss on this corpus should be an empty list, not an error")
