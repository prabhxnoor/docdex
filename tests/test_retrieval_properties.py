"""Retrieval properties that must hold for ANY corpus.

The v0.5.0 regression slipped through a 226-test suite because every test asserted
behaviour on a hand-built corpus where the answer was easy to find. These are
*properties* instead: statements about the relationship between an input change
and the output, which is where flooding/ranking bugs actually live.

P1 and P3 are the shipped bug generalized (both fail on v0.5.0). P2 is the core
honesty guarantee — no evidence, no answer. P4/P5/P6 come from adversarial review
findings that were refuted but whose mechanism was real, so they are pinned before
they can become true. P7 checks determinism across processes and hash seeds.

Two `xfail(strict=True)` cases at the bottom track real gaps that reproduce
identically on v0.5.0 and v0.5.1 — pre-existing, not regressions.

Each test names the production change that would break it; where a fixture could
pass vacuously, an explicit precondition asserts the fixture is doing its job.
"""
from __future__ import annotations

import sqlite3
import subprocess
import sys
import textwrap

import pytest

from docdex import index_db
from docdex.context import build_packet
from docdex.scaffold import run_init
from docdex.sync import run_sync


import os
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"


def _base_env() -> dict:
    """A minimal parent environment for subprocess determinism checks."""
    return {k: v for k, v in os.environ.items() if k != "PYTHONHASHSEED"}


def _has_fts5() -> bool:
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        conn.close()


# These properties are about BM25 ranking in the FTS engine; on a SQLite build
# without FTS5 docdex uses the pure-Python fallback scorer and they don't apply.
requires_fts5 = pytest.mark.skipif(not _has_fts5(), reason="SQLite lacks FTS5")


def _index(root):
    project = run_init(root, quiet=True)
    run_sync(project, quiet=True)
    index_db.build(project, quiet=True)
    return project


# ------------------------------------------------------------------ P1 --------

@requires_fts5
@pytest.mark.parametrize("literal,stem_form,value", [
    ("terms", "term", "net-45"),            # plural -> singular
    ("governing", "govern", "Karnataka"),   # -ing  -> base
    ("closes", "close", "31/12/2026"),      # -es   -> base
    ("liabilities", "liabilit", "5 crore"),  # -ies  -> stem
])
def test_p1_a_uniquely_selective_term_is_always_retrievable(
        tmp_path, literal, stem_form, value):
    """A term occurring in exactly ONE chunk must be findable, however common its
    stem class is.

    Parameterised across morphologies so this is a property rather than a single
    plural fixture: each case makes `literal` unique to the answer while flooding
    the corpus with `stem_form`, which porter collapses them onto.
    """
    root = tmp_path / f"p1_{literal}"
    root.mkdir()
    (root / "unique.txt").write_text(
        f"Settlement {literal} are {value} as agreed.\n", encoding="utf-8")
    for i in range(50):
        (root / f"f_{i:02d}.txt").write_text(
            (f"settlement {stem_form} {stem_form} filler {stem_form} " * 5) + "\n",
            encoding="utf-8")
    project = _index(root)

    hits = index_db.search(project, f"settlement {literal}", limit=5)
    assert "unique.txt" in [h["rel"] for h in hits], (
        f"{literal!r} is unique to one chunk but was buried by the "
        f"{stem_form!r} stem class")


# ------------------------------------------------------------------ P2 --------

def test_p2_a_field_with_no_evidence_is_never_answered(tmp_path):
    """The core honesty guarantee: no evidence must mean no answer.

    The corpus is seeded with plausible *numeric* distractors near other labels, so
    a value-hungry extractor has something wrong to grab. `Bank IFSC` appears
    nowhere, and must never surface as an answer.
    """
    root = tmp_path / "p2"
    root.mkdir()
    (root / "a.txt").write_text(
        "Payment terms are net-45 from the date of invoice.\n"
        "Account reference is 500123 for internal use.\n"
        "Branch code 400072 applies to the Mumbai office.\n", encoding="utf-8")
    (root / "b.txt").write_text(
        "Bank details will be provided separately upon request.\n", encoding="utf-8")
    project = _index(root)

    packet = build_packet(project, "fill the vendor form", budget=2000,
                          form_fields=["Payment terms", "Bank IFSC"])
    answers = packet.split("## Answers")[-1].split("##")[0] if "## Answers" in packet else ""
    assert "Bank IFSC" not in answers, (
        "a field with no evidence in the corpus was given an answer:\n" + packet)
    for distractor in ("500123", "400072"):
        assert not (f"Bank IFSC" in answers and distractor in answers), (
            f"Bank IFSC answered with the unrelated number {distractor}")


# ------------------------------------------------------------------ P3 --------

def test_p3_adding_irrelevant_files_never_removes_a_found_answer(tmp_path):
    """Monotonic non-destruction: growing the corpus with documents that do NOT
    contain the answer must not evict the answer from the packet.

    This is the shipped regression stated as a property — v0.5.0 lost the value
    precisely because filler documents flooded the candidate window.
    """
    root = tmp_path / "p3"
    root.mkdir()
    (root / "answer.txt").write_text(
        "Payment terms are net-45 from the date of invoice.\n", encoding="utf-8")
    project = _index(root)
    before = build_packet(project, "vendor form", budget=2000,
                          form_fields=["Payment terms"])
    assert "net-45" in before, "precondition: answer found in the small corpus"

    for i in range(60):
        (root / f"noise_{i:02d}.txt").write_text(
            ("payment term contract term renewal term budget term " * 4) + "\n",
            encoding="utf-8")
    run_sync(project, quiet=True)
    index_db.build(project, quiet=True)

    after = build_packet(project, "vendor form", budget=2000,
                         form_fields=["Payment terms"])
    assert "net-45" in after, (
        "adding documents that do not contain the answer destroyed it:\n" + after)


# ------------------------------------------------------------------ P4 --------

@requires_fts5
def test_p4_ranking_is_independent_of_file_indexing_order(tmp_path):
    """Determinism: the same documents must rank the same however they got indexed.

    Writing files in a different order is NOT enough to test this — the sync walks
    paths in sorted order, so chunk rowids come out identical either way and the
    test would pass even if ranking fell back to rowid. So each corpus is built
    **incrementally in opposing sequences, rebuilding after every file**, which
    genuinely assigns different rowids to the same documents.
    """
    docs = {
        "alpha.txt": "Payment terms are net-45 on invoice.\n",
        "beta.txt": "payment term renewal term budget term\n",
        "gamma.txt": "contract term payment term schedule term\n",
        "delta.txt": "payment terms summary of the agreement\n",
    }
    forward = ["alpha.txt", "beta.txt", "gamma.txt", "delta.txt"]

    results, rowids = [], []
    for tag, order in (("a", forward), ("b", list(reversed(forward)))):
        root = tmp_path / f"p4_{tag}"
        root.mkdir()
        project = run_init(root, quiet=True)
        for name in order:                     # add one file at a time, reindexing
            (root / name).write_text(docs[name], encoding="utf-8")
            run_sync(project, quiet=True)
            index_db.build(project, quiet=True)
        hits = index_db.search(project, "payment terms", limit=4)
        results.append([(h["rel"], h["score"]) for h in hits])
        conn = sqlite3.connect(str(project.index_db_path))
        try:
            rowids.append(dict(conn.execute("SELECT rel, chunk_id FROM chunks")))
        finally:
            conn.close()

    # Precondition: the two corpora really did get different internal ids, so a
    # rowid-dependent ranking would diverge and this test would catch it.
    assert rowids[0] != rowids[1], (
        "both corpora assigned identical chunk_ids — the fixture failed to vary "
        "indexing order, so this test would not detect rowid dependence")
    assert results[0] == results[1], (
        f"ranking depends on indexing order:\n  {results[0]}\n  {results[1]}")


# ------------------------------------------------------------------ P5 --------

@requires_fts5
def test_p5_more_query_terms_matched_outranks_fewer(tmp_path):
    """Guard, not a law: for THIS shape, a 3-term match must beat a repeated
    1-term match.

    This is not a general BM25 theorem — length normalisation can legitimately
    reorder such a pair. It is pinned because adversarial review argued max-score
    fusion across two tokenizers could invert it; measured refuted (11.00 vs 7.15)
    since BM25 accumulates across matched terms while single-term frequency
    saturates. If a future scoring change flips this, that is a decision to make
    deliberately, not by accident.
    """
    root = tmp_path / "p5"
    root.mkdir()
    (root / "complete.txt").write_text(
        "The liabilities limitations clauses are capped at INR 5 crore.\n",
        encoding="utf-8")
    (root / "single.txt").write_text(
        "liability liability liability liability liability\n", encoding="utf-8")
    for i in range(30):
        (root / f"f_{i:02d}.txt").write_text(
            "unrelated schedule annexure warranty indemnity\n", encoding="utf-8")
    project = _index(root)

    hits = index_db.search(project, "liability limitation clause", limit=5)
    rels = [h["rel"] for h in hits]
    assert rels.index("complete.txt") < rels.index("single.txt"), (
        f"single-term distractor outranked the fuller match: {rels}")


# ------------------------------------------------------------------ P6 --------

def test_p6_a_field_with_retrievable_evidence_is_never_reported_missing(tmp_path):
    """The relevance floor must not be able to turn present evidence into a
    'missing' verdict. Review claimed a score spike in one mirror could raise
    `rel_floor` enough to do this; field answers are resolved before the floor is
    applied, so it cannot — pinned here."""
    root = tmp_path / "p6"
    root.mkdir()
    # A deliberate score spike: a rare literal term repeated, unrelated to the field.
    (root / "spike.txt").write_text(
        ("zqxjv " * 40) + "\n", encoding="utf-8")
    (root / "quiet.txt").write_text(
        "Payment terms are net-45 from the date of invoice.\n", encoding="utf-8")
    for i in range(20):
        (root / f"f_{i:02d}.txt").write_text(
            "payment term contract term filler\n", encoding="utf-8")
    project = _index(root)

    packet = build_packet(project, "zqxjv payment terms", budget=2000,
                          form_fields=["Payment terms"])
    # Asserting only "not under ## Missing" would also pass if the field vanished
    # from the packet altogether, so require it to be PRESENT and answered.
    answered = ""
    for header in ("## Answers", "## Needs follow-up"):
        if header in packet:
            answered += packet.split(header)[-1].split("\n##")[0]
    assert "Payment terms" in answered, (
        "a field with retrievable evidence is neither answered nor weak — it "
        "disappeared or was reported missing:\n" + packet)
    assert "net-45" in answered, (
        "the field is present but carries no value:\n" + packet)


# ------------------------------------------------------------------ P7 --------

def test_p7_packet_is_identical_across_processes_and_hash_seeds(tmp_path):
    """Determinism is a product guarantee, not an accident of dict ordering.

    Two calls in one process share a PYTHONHASHSEED, so they cannot detect ranking
    that depends on set/dict iteration order. This builds and queries in separate
    subprocesses under different hash seeds and compares the packets.
    """
    root = tmp_path / "p7"
    root.mkdir()
    (root / "a.txt").write_text(
        "Payment terms are net-45. Liability cap is INR 4.2 crore.\n",
        encoding="utf-8")
    for i in range(15):
        (root / f"f_{i:02d}.txt").write_text(
            "payment term liability cap filler content\n", encoding="utf-8")

    script = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(SRC)!r})
        from pathlib import Path
        from docdex import index_db
        from docdex.context import build_packet
        from docdex.scaffold import run_init
        from docdex.sync import run_sync
        root = Path({str(root)!r})
        p = run_init(root, quiet=True)
        run_sync(p, quiet=True)
        index_db.build(p, quiet=True)
        sys.stdout.write(build_packet(p, "payment terms liability cap", budget=1500))
    """)

    packets = []
    for seed in ("0", "1", "524287"):
        env = {"PYTHONHASHSEED": seed,
               "DOCDEX_CACHE_DIR": str(tmp_path / f"cache_{seed}")}
        proc = subprocess.run([sys.executable, "-c", script], capture_output=True,
                              text=True, env={**_base_env(), **env})
        assert proc.returncode == 0, proc.stderr[-2000:]
        packets.append(proc.stdout)

    assert packets[0] == packets[1] == packets[2], (
        "packet depends on PYTHONHASHSEED — ranking is using dict/set iteration "
        "order somewhere")


# ---------------------------------------------------- known gaps (tracked) ----
# Both were found by external adversarial review of this release's QA suite
# (2026-07-29) and reproduce IDENTICALLY on v0.5.0 and v0.5.1 — they are
# pre-existing gaps, not regressions from the dual-mirror fix. Marked
# xfail(strict=True) so they stay visible and FAIL LOUDLY the moment they start
# passing, which is the signal to delete the marker.

@requires_fts5
def test_value_bearing_chunk_survives_exact_label_decoys(tmp_path):
    """FIXED in v0.5.2 (was xfail here from v0.5.1).

    When many chunks tie on a field's label, retrieval had no way to prefer the one
    carrying a value: 60 decoys containing the exact phrase `Payment terms` but no
    value buried the answer at rank 60. The scores were not even tied — they differed
    at the 9th decimal on length-normalisation noise — so `chunks.has_value` now
    breaks ties bucketed at `SCORE_GRAIN`.
    """
    root = tmp_path / "decoy"
    root.mkdir()
    (root / "z_answer.txt").write_text(
        "Payment terms are net-45 from the date of invoice.\n", encoding="utf-8")
    for i in range(60):
        (root / f"a_decoy_{i:02d}.txt").write_text(
            "Payment terms are described in the annexure.\n", encoding="utf-8")
    project = _index(root)

    packet = build_packet(project, "vendor form", budget=2000,
                          form_fields=["Payment terms"])
    assert "net-45" in packet


@requires_fts5
@pytest.mark.xfail(strict=True, reason=(
    "Unicode normalisation: a query written in NFD (e + combining acute) does not "
    "match text stored as NFC. Fixing it means normalising both the cached text and "
    "the query, which changes indexed content — deferred to a release that already "
    "carries a schema bump for other reasons."))
def test_nfd_query_matches_nfc_text(tmp_path):
    import unicodedata
    root = tmp_path / "nfd"
    root.mkdir()
    (root / "u.txt").write_text(
        unicodedata.normalize("NFC", "Échéance de paiement: 31/12/2026") + "\n",
        encoding="utf-8")
    project = _index(root)

    hits = index_db.search(project, unicodedata.normalize("NFD", "Échéance"), limit=5)
    assert hits, "NFD query found nothing in NFC-normalised text"


@requires_fts5
def test_label_and_value_survive_a_chunk_boundary(tmp_path):
    """A field's label and value can land near a chunk boundary in a long file.

    Every other fixture here is one short sentence, so nothing exercised chunking
    at all. Real documents are long: this pads a file so the answer sentence sits
    right at the first boundary, where the overlap window has to keep it whole.
    """
    from docdex.tokens import iter_chunks

    root = tmp_path / "boundary"
    root.mkdir()
    pad = "Recitals and definitions of the agreement follow in order. "
    filler = (pad * 60)[:1780]           # push the answer to the chunk edge
    body = filler + "Payment terms are net-45 from the date of invoice. " + (pad * 40)
    (root / "long.txt").write_text(body + "\n", encoding="utf-8")
    project = _index(root)

    # Precondition: the file really is multi-chunk, or this proves nothing.
    assert len(list(iter_chunks(body))) > 1, "fixture did not span chunks"

    packet = build_packet(project, "vendor form", budget=2000,
                          form_fields=["Payment terms"])
    assert "net-45" in packet, (
        "the value was lost at a chunk boundary:\n" + packet)
