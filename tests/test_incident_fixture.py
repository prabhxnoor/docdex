"""v0.6.0 B0 — the retrieval incident, reproduced, and the gates any fix must clear.

The incident: a user asked *"find an atp ppt where milestones are mentioned"*. The
correct nine-slide deck was fully indexed and came back at **rank 33** against a
default window of 8.

This file exists before any ranking change, deliberately. Nothing in the suite
currently defines unique-file recall under duplicates, extension filtering,
cross-chunk term coverage, alternate-path provenance, or semantic matches past
character 500 — so a change could tidy the visible top 8 while quietly lowering
recall, which is the dangerous direction for a tool answering due-diligence
questions. A missed document has consequences; a scruffy top 8 does not.

The corpus below is built to make each failure mode reproducible in isolation:

* the wanted deck carries `atp` on slide 2 and `milestone` on slide 7, so no single
  chunk contains both — BM25 scores per chunk and fusion keys by `chunk_id`, so the
  deck earns no credit for covering both terms
* several byte-identical copies at different paths compete as separate rows
* distractor PDFs are dense in one term, which is what a per-chunk score rewards
* one file's name promises what its body does not deliver
* two different files carry an empty hash, which must not collapse them together
* one file's only matching term sits past character 500, where the semantic gate
  stops looking

Gates that describe behaviour docdex does not have yet are `xfail(strict=True)`, the
convention this repo already uses for tracked gaps: the suite stays green, and the
day the behaviour lands the gate turns XPASS and forces itself to be promoted to a
real assertion. Gates that pass today are controls — they pin behaviour that must NOT
regress while B1–B6 are built.
"""
from __future__ import annotations

import random
import shutil
import sqlite3

import pytest

from conftest import make_pdf, make_pptx  # noqa: E402
from docdex import index_db               # noqa: E402
from docdex.config import Project, ensure_state_dirs  # noqa: E402
from docdex.sync import run_sync          # noqa: E402

_VOCAB = ("annexure arbitration covenant indemnity warranty jurisdiction "
          "schedule termination remittance escrow tranche debenture affidavit "
          "stipulation forbearance novation subrogation liquidated damages").split()

QUERY = "find an atp ppt where milestones are mentioned"
WANTED = "Decks/programme review 2026.pptx"
WINDOW = 8

# Nine slides, each with a real slide's worth of body text. The length matters as
# much as the wording: chunking cuts at fixed 1,800-character offsets, so a deck of
# one-line slides collapses into a single chunk and the cross-chunk failure this
# fixture exists to reproduce quietly disappears. `test_both_query_terms_land_on_
# different_chunks` is what holds that honest — it caught exactly this.
_BODY = ("Prepared for the steering group and circulated in advance of the session. "
         "Figures are provisional and subject to confirmation by the finance team. "
         "Owners are named against each line so that follow-up actions are clear. ")
SLIDES = [
    "Programme Review 2026 quarterly briefing. " + _BODY,
    "Scope covers the ATP workstream and its dependencies. " + _BODY,   # term 1
    "Budget position and committed spend to date. " + _BODY,
    "Headcount and hiring plan for the coming quarter. " + _BODY,
    "Risk register summary with mitigation owners. " + _BODY,
    "Vendor engagement and contracting status. " + _BODY,
    "Open decisions requiring steering group input. " + _BODY,
    "Delivery milestone schedule and acceptance criteria. " + _BODY,    # term 2
    "Appendix glossary and reference links. " + _BODY,
]


@pytest.fixture
def incident(tmp_path):
    """The corpus that reproduces the incident. Returns a synced, indexed Project."""
    root = tmp_path / "incident"
    for sub in ("Decks", "Archive/2026 copy", "Archive/backup", "Reports",
                "Notes", "Misc", "Background"):
        (root / sub).mkdir(parents=True, exist_ok=True)

    # The wanted deck, plus two byte-identical copies at different paths.
    deck = root / WANTED
    make_pptx(deck, SLIDES)
    for dup in ("Archive/2026 copy/programme review 2026.pptx",
                "Archive/backup/programme review 2026.pptx"):
        shutil.copyfile(deck, root / dup)

    # Background, and it is load-bearing. BM25 needs vocabulary variety to produce
    # meaningful IDF; in a small uniform corpus every document matches the same
    # terms, every score comes back 0.0000, and ranking falls silently through to
    # the tiebreak — which is alphabetical by path, so `Archive/` wins for being
    # spelled with an A. Three earlier attempts at this fixture "passed" on that
    # artefact rather than on relevance.
    rnd = random.Random(7)
    for i in range(60):
        (root / "Background" / f"filing {i}.md").write_text(
            " ".join(rnd.choice(_VOCAB) for _ in range(120)), encoding="utf-8")

    # The competitors that actually bury the deck: each carries several query terms
    # in ONE chunk, which is precisely the credit a nine-slide deck cannot earn when
    # its two terms sit on different slides. They are also the wrong file type, which
    # is why the extension filter does the most work of any single fix.
    for i in range(20):
        (root / "Reports" / f"acceptance record {i}.pdf").write_bytes(
            make_pdf("Where the ATP milestones are mentioned, find the acceptance "
                     "record. ATP milestone owners are mentioned in the schedule "
                     "where applicable. " * 3))

    # A filename that lies about its contents.
    (root / "Decks" / "atp milestones summary.md").write_text(
        "This note is about catering arrangements and room bookings.\n",
        encoding="utf-8")

    # One long file, so several chunks come from the same source.
    (root / "Notes" / "long notes.md").write_text(
        ("Programme governance narrative. " * 90) + "\nmilestone tracking appears here\n"
        + ("Further governance narrative. " * 90), encoding="utf-8")

    # The only matching term sits past character 500, where the semantic gate stops.
    (root / "Notes" / "late match.md").write_text(
        ("Preamble filler sentence with nothing relevant in it. " * 12)
        + "\nThe ATP milestone acceptance note is recorded here.\n", encoding="utf-8")

    # Two DIFFERENT files that will be given an empty hash below.
    (root / "Misc" / "unhashed one.md").write_text(
        "First unhashed document about ATP scheduling.\n", encoding="utf-8")
    (root / "Misc" / "unhashed two.md").write_text(
        "Second unhashed document about milestone review.\n", encoding="utf-8")

    project = Project.create(root)
    ensure_state_dirs(project)
    project.save()
    run_sync(project, quiet=True)
    index_db.build(project, quiet=True)

    # An empty hash is what `--no-hash` and the size cap actually produce. Two
    # different files must never be collapsed just because neither has one.
    conn = sqlite3.connect(project.index_db_path)
    conn.execute("UPDATE files SET sha1='' WHERE rel LIKE 'Misc/unhashed%'")
    conn.commit()
    conn.close()
    return project


def rels(hits):
    return [h["rel"] for h in hits]


# ----------------------------------------------------------------- the incident
def test_the_incident_reproduces(incident):
    """The wanted deck is indexed and reachable — the bug is its RANK, not absence.

    A control, and the one that makes the rest meaningful: if the deck were simply
    missing, every gate below would be measuring the wrong thing.
    """
    deep = index_db.search(incident, QUERY, limit=300)
    assert WANTED in rels(deep), (
        "the wanted deck is not retrievable at all, so this fixture no longer "
        "reproduces the incident:\n" + "\n".join(rels(deep)[:15]))
    rank = rels(deep).index(WANTED) + 1
    assert rank > WINDOW, (
        "the deck ranks %d, inside the window — the fixture has stopped reproducing "
        "the burial it exists to demonstrate" % rank)
    window = rels(index_db.search(incident, QUERY, limit=WINDOW))
    assert not any(r.endswith(".pptx") for r in window), (
        "the user asked for a ppt; the window is supposed to contain none:\n"
        + "\n".join(window))


def test_both_query_terms_land_on_different_chunks(incident):
    """The mechanism, pinned. If a chunking change ever puts both terms in one
    chunk, the fixture stops exercising cross-chunk coverage and the gates below
    would pass for the wrong reason."""
    conn = sqlite3.connect(incident.index_db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT chunk_index, lower(text) t FROM chunks WHERE rel=?",
                        (WANTED,)).fetchall()
    conn.close()
    with_atp = {r["chunk_index"] for r in rows if "atp" in r["t"]}
    with_ms = {r["chunk_index"] for r in rows if "milestone" in r["t"]}
    assert with_atp and with_ms, f"terms missing from the deck's chunks: {len(rows)} chunk(s)"
    assert not (with_atp & with_ms), (
        "both terms share a chunk, so this fixture no longer tests cross-chunk "
        f"coverage (atp in {sorted(with_atp)}, milestone in {sorted(with_ms)})")


# --------------------------------------------------------------------- the gates
@pytest.mark.xfail(strict=True, reason="B1/B3 not built: no file-level aggregation, "
                                       "so a deck split across chunks never ranks")
def test_gate_wanted_file_is_in_the_default_window(incident):
    hits = index_db.search(incident, QUERY, limit=WINDOW)
    assert WANTED in rels(hits), (
        "the deck that answers the question is outside the default window of %d:\n%s"
        % (WINDOW, "\n".join(rels(hits))))


@pytest.mark.xfail(strict=True, reason="B3 not built: duplicates are independent rows")
def test_gate_no_duplicate_group_takes_more_than_one_slot(incident):
    # A query the deck itself answers, because under the incident query the window
    # is all PDFs and the copies never appear — the gate would pass for the wrong
    # reason and XPASS the moment anyone looked.
    hits = index_db.search(incident, "programme review steering group briefing",
                           limit=WINDOW)
    names = [r.rsplit("/", 1)[-1] for r in rels(hits)]
    dupes = {n for n in names if names.count(n) > 1}
    assert not dupes, (
        "byte-identical copies are each consuming a slot in the window: %s\n%s"
        % (sorted(dupes), "\n".join(rels(hits))))


@pytest.mark.xfail(strict=True, reason="B3 not built: search never reads files.sha1, "
                                       "so alternate paths are not reported")
def test_gate_alternate_paths_are_reported_not_hidden(incident):
    hits = index_db.search(incident, "programme review steering group briefing",
                           limit=WINDOW)
    deck = next((h for h in hits if h["rel"] == WANTED), None)
    assert deck is not None, "the deck is not in the window at all"
    assert deck.get("also_at"), (
        "collapsing duplicates without reporting where the other copies live loses "
        "provenance — in diligence, the same file under two counterparty folders "
        "means something")


def test_control_empty_hashes_are_never_grouped_together(incident):
    """A control, not a gap: nothing groups anything yet, so this holds trivially
    today. It is here to keep holding once B3 starts grouping by `sha1` — an empty
    hash is what `--no-hash` and the size cap produce, and collapsing every one of
    them into a single group would hide unrelated documents behind each other."""
    hits = index_db.search(incident, "unhashed document", limit=WINDOW)
    found = [r for r in rels(hits) if r.startswith("Misc/unhashed")]
    assert len(found) == 2, (
        "two different files with no hash were treated as copies of each other: %s"
        % found)


def test_gate_extension_filter_is_complete_and_applied_before_the_limit(incident):
    """B1, and the single highest-leverage fix: rank 22 to inside the window.

    The deck is NOT in the unfiltered top 8 — `test_the_incident_reproduces` asserts
    that. So a filter applied to the window afterwards would have nothing to keep and
    would report the deck absent. Finding it here is what proves the filter runs
    inside candidate selection, before each mirror's LIMIT.
    """
    hits = index_db.search(incident, QUERY, limit=WINDOW, ext=[".pptx"])
    assert hits, ("filtering to .pptx returned nothing. A filter applied after the "
                  "chunk window is worse than no filter: it silently answers 'absent' "
                  "for a document that is present")
    assert all(h["rel"].endswith(".pptx") for h in hits), rels(hits)
    assert WANTED in rels(hits), rels(hits)


def test_the_word_a_user_types_finds_the_file_type_on_disk(incident):
    """"ppt" is what the incident query actually said; `.pptx` is what is on disk.

    Legacy `.ppt` is not extractable by this build, so mapping the word to the
    extension it can read is the difference between finding the deck and a refusal
    that is accurate and useless.
    """
    for spelling in ("ppt", "pptx", ".PPTX", "PowerPoint", "deck"):
        hits = index_db.search(incident, QUERY, limit=WINDOW, ext=[spelling])
        assert WANTED in rels(hits), "%r found nothing: %s" % (spelling, rels(hits))


def test_an_extension_we_cannot_read_is_a_diagnostic_not_an_empty_result(incident):
    """"No .ppt matched" and "this build cannot read .ppt" are different answers.

    Returning nothing would be true and misleading — the deck IS there. Anything
    that silently narrows the corpus has to say so.
    """
    from docdex.config import DocdexError
    with pytest.raises(DocdexError) as exc:
        index_db.search(incident, QUERY, limit=WINDOW, ext=[".xyz"])
    assert ".xyz" in str(exc.value)
    assert ".pptx" in str(exc.value), "the message should say what it CAN read"


def test_filtering_narrows_the_corpus_without_reordering_it(incident):
    """A filter must remove rows, never re-rank the survivors.

    If it changed relative order it would be a ranking change wearing a filter's
    clothes, and the release gate 'unfiltered behaviour unchanged' would not catch
    it — that gate only watches the unfiltered path.
    """
    wide = [h["rel"] for h in index_db.search(incident, QUERY, limit=300)]
    pptx_in_order = [r for r in wide if r.endswith(".pptx")]
    filtered = rels(index_db.search(incident, QUERY, limit=300, ext=["pptx"]))
    assert filtered == pptx_in_order, (
        "filtering changed the order of the surviving rows:\n  filtered: %s\n  "
        "expected: %s" % (filtered[:6], pptx_in_order[:6]))


@pytest.mark.xfail(strict=True, reason="B2 not built: _content_terms drops any term "
                                       "shorter than 4 characters")
def test_gate_short_content_terms_survive_preprocessing(incident):
    from docdex.context import _content_terms
    terms = _content_terms(QUERY)
    assert "atp" in terms, (
        "the acronym naming the document was deleted before retrieval; what survived "
        "was %s — instruction scaffolding, not intent" % terms)


@pytest.mark.xfail(strict=True, reason="B4 not built: semantic stores chunk[:500] "
                                       "but gates term overlap against that prefix")
def test_gate_a_match_after_character_500_is_not_dropped(incident):
    from docdex import semantic
    rows = semantic.search(incident, "ATP milestone acceptance", limit=WINDOW)
    assert any(r.get("path", "").endswith("late match.md") for _s, r in rows), (
        "a chunk whose only matching term sits past character 500 was discarded, "
        "even though its vector was computed over the whole chunk")


# ------------------------------------------------------------------- the controls
def test_control_unfiltered_search_still_returns_results(incident):
    """Whatever B1–B6 do, the plain path must keep working."""
    hits = index_db.search(incident, QUERY, limit=WINDOW)
    assert hits, "the unfiltered query returned nothing at all"
    assert len(hits) <= WINDOW


def test_control_a_lying_filename_does_not_win_on_its_name_alone(incident):
    """B5 adds a path signal. This pins the limit on it before it exists: a name
    must never outrank a body that actually contains the terms."""
    hits = index_db.search(incident, QUERY, limit=3)
    top = rels(hits)[:1]
    assert top != ["Decks/atp milestones summary.md"], (
        "a file whose name promises both terms and whose body has neither took the "
        "top slot")
