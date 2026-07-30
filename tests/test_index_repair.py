"""v0.5.5 — a sync with nothing to do must still repair an index that cannot answer.

v0.5.4 taught `search` and `doctor` to refuse an index that holds text but has nothing
indexed, instead of reporting "no matches" about a whole corpus. Both of them print the
same instruction: *run `docdex sync` to rebuild the index*.

That instruction did not work. The rebuild was gated on `changed or removed` — a
document having been edited or deleted — so a sync over an unchanged corpus looked at a
wiped index, found no work, and left it exactly as it was. `search` then printed the
same instruction again. No flag forced a lexical rebuild, so there was no way out of the
loop, and the state the advice named is precisely the one it could not fix.

The tests here are shaped around that lesson rather than around the code: each one
follows the printed advice and asserts the user is no longer stuck. Two of them exist to
stop the fix overcorrecting — a repair that fires on a healthy index would reindex the
whole corpus on every sync, and one that mistakes a corpus of punctuation for a broken
index would reindex forever.

Helpers are local on purpose, and nothing this release introduces is imported by name:
a new test file must FAIL against the base tree, not error on an import, or gate 3
cannot tell a real regression test from a missing symbol.
"""
from __future__ import annotations

import sqlite3

import pytest

from docdex import index_db
from docdex.scaffold import run_init
from docdex.sync import run_sync


def sync_index(project, quiet=True):
    """What `docdex sync` does, in the order the CLI does it.

    `run_sync` refreshes the inventory and the `.txt` caches; building the lexical
    index is the separate `[3/6]` step the CLI drives. Both matter here — the bug is in
    the second, and "the advice works" is only true if the whole command repairs it.

    Used where a test needs `build()`'s return value. Where a test's claim is about the
    *advice* — "run `docdex sync`" — it calls `run_the_advised_command` instead, which
    goes through the real entry point. Adversarial review of this file made that split
    necessary: composing the two steps by hand here means deleting `index_db.build`
    from the CLI would leave every one of these tests green while the printed
    instruction stopped repairing anything.
    """
    run_sync(project, quiet=quiet)
    return index_db.build(project, quiet=quiet)


def run_the_advised_command(project):
    """Literally `docdex --root <root> sync`, through the CLI the message names."""
    from docdex.cli import main
    code = main(["--root", str(project.root), "sync",
                 "--no-dumps", "--no-embed", "--no-vision"])
    assert code == 0, f"the advised command exited {code}"


def open_db(project):
    return sqlite3.connect(str(project.index_db_path))


def rows_indexed(project, table="chunks_fts"):
    """How many rows this mirror has actually indexed.

    `SELECT COUNT(*) FROM chunks_fts` is worthless for this: on an external-content
    FTS5 table it is proxied to the *content* table, so a wiped mirror still reports a
    full row count. That is how a corpus-wide wipe went unnoticed for a day.
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


class RecordedRebuilds:
    """Every FTS `'rebuild'` statement `build()` actually executed.

    The cost guards used to assert on `build()`'s returned `repaired` flag, which
    review correctly called measuring the self-report rather than the behaviour: a
    version that rebuilt both mirrors on every healthy sync while returning
    `repaired=False` passed them. SQLite's own trace callback sees the statements, so
    this observes the work instead of believing the summary.
    """

    def __init__(self, monkeypatch):
        self.statements = []
        real_open = index_db._open_for_build

        def traced(project, quiet=False):
            conn = real_open(project, quiet=quiet)
            conn.set_trace_callback(self.statements.append)
            return conn

        monkeypatch.setattr(index_db, "_open_for_build", traced)

    @property
    def count(self):
        return len([s for s in self.statements if "'rebuild'" in s])


def wipe_the_mirrors(project, tables=("chunks_fts", "chunks_fts_exact")):
    """The on-disk state the v0.5.2 crash left behind: mirrors present, holding nothing."""
    conn = open_db(project)
    try:
        for table in tables:
            conn.execute(f"INSERT INTO {table}({table}) VALUES('delete-all')")
        conn.commit()
    finally:
        conn.close()


def leave_only_the_first_chunk_indexed(project):
    """A rebuild that stopped early: some rows indexed, most not.

    Worth separating from a full wipe because it is the state that still *answers* —
    it holds real terms, so it looks healthy to anything that asks "any terms at all?"
    and silently misses the rest of the corpus.
    """
    conn = open_db(project)
    try:
        for table in ("chunks_fts", "chunks_fts_exact"):
            conn.execute(f"INSERT INTO {table}({table}) VALUES('delete-all')")
        first = conn.execute("SELECT MIN(chunk_id) FROM chunks").fetchone()[0]
        # The mirrors carry one column (`text`) over `content='chunks'`, so a row is
        # re-indexed by inserting its rowid and text back — FTS5's documented way to
        # index a single row of an external-content table.
        for table in ("chunks_fts", "chunks_fts_exact"):
            conn.execute(
                f"INSERT INTO {table}(rowid, text) "
                f"SELECT chunk_id, text FROM chunks WHERE chunk_id = ?", (first,))
        conn.commit()
    finally:
        conn.close()


# One unique marker per document, and three across the long contract, so "the corpus is
# searchable again" cannot be satisfied by a repair that only reindexed the first file
# or the first chunk.
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


def terms_matchable_in(project, table, term):
    """Does this specific mirror match this term? A direct MATCH against one table.

    Row counts alone cannot answer it. Adversarial review pointed out that a mirror
    rebuilt with every correct rowid but empty text reaches the full `_docsize` count
    while holding no terms at all — and ordinary searches would still pass through the
    other mirror, so nothing in the suite would notice the exact-term space was dead.
    """
    conn = open_db(project)
    try:
        return conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {table} MATCH ?", (f'"{term}"',)
        ).fetchone()[0]
    finally:
        conn.close()


def assert_whole_corpus_searchable(project, why):
    for name, markers in MARKERS.items():
        for marker in markers:
            rows = index_db.search(project, marker, limit=5)
            assert rows, f"{why}: {marker!r} (in {name}) is not findable"
            assert any(marker in r["text"] for r in rows)
    total = chunks_stored(project)
    for table in ("chunks_fts", "chunks_fts_exact"):
        assert rows_indexed(project, table) == total, (
            f"{why}: {table} indexed {rows_indexed(project, table)} of {total} chunks, "
            f"so part of the corpus is still unsearchable")
        # Each mirror must independently MATCH, not merely hold rows.
        for marker in ("Zopfli-Omega-9915", "Quernstone-Policy-3308"):
            assert terms_matchable_in(project, table, marker), (
                f"{why}: {table} holds {total} rows but cannot match {marker!r}, so it "
                f"was rebuilt without indexing the text")


# ------------------------------- the repair itself ------------------------------


def test_a_sync_with_nothing_to_do_repairs_a_wiped_index(project):
    """The reported bug. Nothing changed in the corpus, so there was no work to do —
    but the index cannot answer, which is work by any useful definition.

    Both mirrors, in the precondition and the assertion: review's mutation rebuilt only
    the default one, which this test would have accepted while exact-term ranking stayed
    dead.
    """
    wipe_the_mirrors(project)
    stored = chunks_stored(project)
    for table in ("chunks_fts", "chunks_fts_exact"):
        assert rows_indexed(project, table) == 0, f"{table} was not wiped"

    sync_index(project)

    for table in ("chunks_fts", "chunks_fts_exact"):
        assert rows_indexed(project, table) == stored, (
            f"sync left {table} with {rows_indexed(project, table)} of {stored} chunks")
        assert terms_matchable_in(project, table, "Zopfli-Alpha-4471"), (
            f"{table} has the rows but cannot match a term in them")


@pytest.mark.parametrize("table", ["chunks_fts", "chunks_fts_exact"])
def test_a_sync_with_nothing_to_do_repairs_a_half_built_index(project, table):
    """An index that holds *some* terms is the more dangerous state: it answers, so
    nothing looks wrong, and it quietly misses most of the corpus.

    Parameterised over both mirrors after review: checking only the default one would
    pass while a repair left the exact-term space stuck on its single original row.
    """
    leave_only_the_first_chunk_indexed(project)
    stored = chunks_stored(project)
    assert 0 < rows_indexed(project, table) < stored, "fixture made no partial index"

    sync_index(project)

    assert rows_indexed(project, table) == stored
    assert terms_matchable_in(project, table, "Zopfli-Omega-9915"), (
        f"{table} reached full row count without indexing the last chunk's text")


def test_a_sync_with_nothing_to_do_repairs_one_empty_mirror(project):
    """Only the exact-term mirror is wiped. Search still returns hits, so this fails
    silently: it degrades ranking rather than breaking, which is how it would survive."""
    wipe_the_mirrors(project, tables=("chunks_fts_exact",))
    stored = chunks_stored(project)
    assert rows_indexed(project, "chunks_fts_exact") == 0
    assert rows_indexed(project, "chunks_fts") == stored

    sync_index(project)

    assert rows_indexed(project, "chunks_fts_exact") == stored


def test_the_repair_restores_every_document_not_just_one(project):
    """A repair that reindexed only the first file would pass every test above."""
    wipe_the_mirrors(project)
    sync_index(project)
    assert_whole_corpus_searchable(project, "after repairing a wiped index")


# --------------------- the advice we print has to actually work ------------------
#
# This is the class of test that was missing when v0.5.4 shipped. Both reviews looked
# at the code and at the tests; neither asked whether the instruction the product
# prints resolves the state it is printed about.


def test_search_works_after_following_the_advice_it_printed(project):
    """Through the real CLI, not a hand-composed equivalent — the advice names a
    command, so the command is what has to be tested."""
    wipe_the_mirrors(project)

    with pytest.raises(Exception) as first:
        index_db.search(project, "payment", limit=5)
    assert type(first.value).__name__ == "IndexEmptyError"
    assert "sync" in str(first.value).lower(), "the error should name its own remedy"

    run_the_advised_command(project)

    rows = index_db.search(project, "payment", limit=5)
    assert rows, ("after running the command the error told them to run, the user is "
                  "still stuck with an index that answers nothing")


def test_doctor_passes_after_following_the_advice_it_printed(project, capsys):
    from docdex.doctor import run_doctor

    wipe_the_mirrors(project)
    assert run_doctor(project, no_sha=True) != 0, "doctor passed on a wiped index"
    assert "sync" in capsys.readouterr().out.lower()

    run_the_advised_command(project)

    code = run_doctor(project, no_sha=True)
    out = capsys.readouterr().out
    assert code == 0, f"doctor still fails after the sync it asked for:\n{out}"


def test_the_repair_is_reported_not_silent(project, capsys):
    """A rebuild of the whole corpus is not something to do behind the user's back —
    and `reindexed=0` while both mirrors were rebuilt is a false statement about work
    that happened. `.get` rather than `[...]`: a missing key must fail as an assertion
    against the base tree, not error as a KeyError.

    Review's mutation returned `repaired=True` while still printing `reindexed 0`, so
    what the user sees is asserted here too, not only what the caller receives.
    """
    wipe_the_mirrors(project)

    result = sync_index(project, quiet=False)
    printed = capsys.readouterr().out

    assert result.get("repaired") is True, (
        f"the repair was not reported in {sorted(result)}")
    assert "repaired" in printed.lower(), (
        f"the user is told nothing about a full rebuild:\n{printed}")
    assert "reindexed 0" not in printed, (
        f"the output claims no work was done:\n{printed}")


# --------------------------- and it must not overcorrect -------------------------


def test_a_healthy_sync_does_not_reindex_the_corpus(project, monkeypatch):
    """The cost guard. Reindexing whenever the mirrors are merely *checked* would
    rebuild 92,507 chunks on every sync of the real corpus.

    Asserts on the SQL that ran, not on the returned flag — see `RecordedRebuilds`.
    """
    trace = RecordedRebuilds(monkeypatch)

    before = sync_index(project, quiet=False)

    assert before.get("repaired") is False, "a healthy index was reported as repaired"
    assert before["reindexed"] == 0
    assert trace.count == 0, (
        f"a healthy sync executed {trace.count} FTS rebuild(s): {trace.statements}")


def test_the_repair_runs_once_not_on_every_sync(project, monkeypatch):
    """Two mirrors, so exactly two rebuild statements — for the first sync only."""
    wipe_the_mirrors(project)
    trace = RecordedRebuilds(monkeypatch)

    assert sync_index(project).get("repaired") is True
    after_repair = trace.count
    again = sync_index(project)

    assert after_repair == 2, f"expected one rebuild per mirror, got {after_repair}"
    assert again.get("repaired") is False, "the repair repeats on an index it just fixed"
    assert trace.count == after_repair, (
        f"the second sync rebuilt again: {trace.statements[after_repair:]}")


def test_the_repair_covers_a_corpus_past_any_batching_boundary(tmp_path):
    """From adversarial review: every other fixture here is a handful of chunks, so a
    repair that indexed only its first 1,000 rows would pass all of them and lose
    everything past chunk 1,000 on the real 92,507-chunk corpus. This corpus crosses
    that line, with markers placed before, at and after it."""
    root = tmp_path / "big"
    root.mkdir()
    filler = "This clause restates the obligations of both parties at length. "
    # ~1800 chars per chunk, 250 overlap: ~1,150 chunks across 5 files.
    per_file = filler * 5600
    for n in range(5):
        (root / f"vol{n}.txt").write_text(per_file, encoding="utf-8")
    early, boundary, late = "Ashwagandha-0007", "Boundary-1000", "Zephyrine-9999"
    (root / "vol0.txt").write_text(f"{early}\n" + per_file, encoding="utf-8")
    (root / "vol2.txt").write_text(per_file + f"\n{boundary}\n", encoding="utf-8")
    (root / "vol4.txt").write_text(per_file + f"\n{late}\n", encoding="utf-8")
    p = run_init(root, quiet=True)
    sync_index(p)
    total = chunks_stored(p)
    assert total > 1000, f"corpus is only {total} chunks — too small to test the boundary"

    wipe_the_mirrors(p)
    result = sync_index(p)

    assert result.get("repaired") is True
    for table in ("chunks_fts", "chunks_fts_exact"):
        assert rows_indexed(p, table) == total, (
            f"{table} indexed {rows_indexed(p, table)} of {total} chunks")
    for marker in (early, boundary, late):
        assert index_db.search(p, marker, limit=5), (
            f"{marker!r} is unfindable after repairing a {total}-chunk corpus")


def test_an_unverifiable_index_does_not_reindex_on_every_sync(project, monkeypatch):
    """The deliberate exclusion, pinned. `unverified` is left out of the repair
    condition on purpose: a probe that cannot be answered would otherwise rebuild the
    whole corpus on every sync, forever. Search already refuses loudly in that state
    (v0.5.4), so the loop would buy nothing. Review asked for this case explicitly."""
    real = index_db.indexed_rows

    def unanswerable(conn, table):
        return None

    monkeypatch.setattr(index_db, "indexed_rows", unanswerable)
    first = sync_index(project)
    second = sync_index(project)
    monkeypatch.setattr(index_db, "indexed_rows", real)

    assert first.get("repaired") is False, "an unverifiable probe triggered a rebuild"
    assert second.get("repaired") is False
    # And the state is still reported as not-known rather than healthy.
    monkeypatch.setattr(index_db, "indexed_rows", unanswerable)
    conn = index_db.connect(project)
    try:
        assert index_db.index_state(conn)["unverified"] is True
    finally:
        conn.close()
        monkeypatch.setattr(index_db, "indexed_rows", real)


def test_a_corpus_of_only_punctuation_is_not_repaired_forever(tmp_path, monkeypatch):
    """The false-alarm guard, from adversarial review of v0.5.4: a corpus with no
    indexable words legitimately indexes zero *terms*. A health check based on terms
    would call it broken; this one counts indexed rows, so it must leave it alone.

    The precondition is asserted after review of *this* release: if chunking ever
    discarded punctuation-only text before storing it, there would be no rows at all and
    the test would pass without ever building the state it exists to protect.
    """
    root = tmp_path / "punct"
    root.mkdir()
    (root / "a.txt").write_text("... --- ... !!! ???\n", encoding="utf-8")
    (root / "b.txt").write_text("*** ((( ))) +++\n", encoding="utf-8")
    p = run_init(root, quiet=True)
    sync_index(p)

    stored = chunks_stored(p)
    assert stored > 0, "no chunks were stored, so this is not the state under test"
    for table in ("chunks_fts", "chunks_fts_exact"):
        assert rows_indexed(p, table) == stored, f"{table} did not index every row"
    # Scoped to the punctuation files: `run_init` also scaffolds documentation, which
    # does contain words, so "the whole corpus has zero terms" is not available here.
    # What matters is that these word-free rows ARE indexed as rows — the property that
    # makes a row-count probe correct where a term-count probe reports them as broken.
    conn = open_db(p)
    try:
        punct = conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE rel IN ('a.txt','b.txt')").fetchone()[0]
        indexed = conn.execute(
            "SELECT COUNT(*) FROM chunks_fts_exact_docsize d "
            "JOIN chunks c ON c.chunk_id = d.id "
            "WHERE c.rel IN ('a.txt','b.txt')").fetchone()[0]
    finally:
        conn.close()
    assert punct > 0, "the punctuation files stored no chunks at all"
    assert indexed == punct, (
        f"only {indexed} of {punct} word-free chunks are indexed, so this fixture is "
        f"not the zero-term-but-indexed state it claims to be")

    trace = RecordedRebuilds(monkeypatch)
    result = sync_index(p)

    assert result.get("repaired") is False, (
        "a corpus of punctuation is being reindexed on every sync")
    assert trace.count == 0, f"it rebuilt anyway: {trace.statements}"


def test_an_interrupted_repair_leaves_no_half_new_index(project, monkeypatch):
    """From adversarial review: the repair issues two rebuild statements, so a failure
    between them could leave one mirror fresh and the other stale — two term spaces
    disagreeing, which degrades ranking without failing. They share the transaction
    v0.5.4 opened, so the rollback should be all-or-nothing. Never asserted until now.
    """
    wipe_the_mirrors(project)
    stored = chunks_stored(project)
    armed = {"on": True}
    real_open = index_db._open_for_build

    class FailsOnTheSecondRebuild:
        """A connection that behaves normally until the exact-mirror rebuild.

        Wrapped rather than patched: `sqlite3.Connection` is a C type, so its methods
        cannot be monkeypatched at all.
        """

        def __init__(self, conn):
            self._conn = conn

        def execute(self, sql, *args):
            if armed["on"] and "chunks_fts_exact(chunks_fts_exact)" in sql:
                raise sqlite3.OperationalError("disk I/O error (injected)")
            return self._conn.execute(sql, *args)

        def __getattr__(self, item):
            return getattr(self._conn, item)

    monkeypatch.setattr(index_db, "_open_for_build",
                        lambda project, quiet=False: FailsOnTheSecondRebuild(
                            real_open(project, quiet=quiet)))
    with pytest.raises(sqlite3.OperationalError):
        sync_index(project)
    armed["on"] = False          # disarmed, not undone: monkeypatch.undo() would also
    # drop the autouse cache isolation, which shares this same monkeypatch instance.
    monkeypatch.setattr(index_db, "_open_for_build", real_open)

    for table in ("chunks_fts", "chunks_fts_exact"):
        assert rows_indexed(project, table) == 0, (
            f"{table} kept a partial rebuild after the repair failed — the two term "
            f"spaces now disagree")
    assert chunks_stored(project) == stored, "the stored text was damaged"

    sync_index(project)          # and the next attempt still fixes it
    assert_whole_corpus_searchable(project, "after an interrupted repair was retried")
