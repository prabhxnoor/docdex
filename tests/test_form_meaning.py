"""v0.5.2 — meaning-aware FORM FILLING.

v0.5.0 taught free-text search to match meaning (word endings, declared synonyms)
but deliberately left form fields literal: a field labelled `Legal name` could not
read its value from a clause saying "…as the Vendor", and `Governing law` could not
read one from "governed by the laws of…". These tests drive that half.

Two independent failures had to be fixed for a form field to find its value:

  1. RECOGNISING THE LABEL — `_label_window` required every label token present
     literally, so a synonym or a different word ending meant "no window, no value".
  2. REACHING THE CHUNK AT ALL — when many chunks tie on the label, ranking could not
     tell which one carried a value, so the answer could sit outside the per-field
     candidate window entirely (this was tracked as an xfail in
     test_retrieval_properties.py).

Honesty contract, per the tiering decision: an EXACT label match may be reported as
a found answer. A match found only through a word ending or a declared synonym is
tagged `~approx`, and may only be reported found when its window names no other
field; otherwise it is reported weak. A value is never invented or altered — the
synonym widens which *label* is recognised, never what the value says.
"""
from __future__ import annotations

import json

import pytest

from docdex import index_db
from docdex.context import build_packet
from docdex.scaffold import run_init
from docdex.sync import run_sync

ALIASES = {"legal name": ["vendor", "supplier", "the company"],
           "payment terms": ["payment schedule"]}


def _index(root, aliases: dict = None):
    project = run_init(root, quiet=True)
    if aliases is not None:
        project.aliases_path.write_text(json.dumps(aliases), encoding="utf-8")
    run_sync(project, quiet=True)
    index_db.build(project, quiet=True)
    return project


def _section(packet: str, name: str) -> str:
    """Text of one packet section, '' when absent."""
    header = {"answers": "## Answers", "weak": "## Needs follow-up",
              "missing": "## Missing"}[name]
    if header not in packet:
        return ""
    return packet.split(header, 1)[1].split("\n##", 1)[0]


def field_line(packet: str, label: str) -> str:
    """This field's own line from Answers or Needs-follow-up.

    Deliberately NOT "is the value somewhere in the packet": a short sentence gets
    echoed whole into the weak line and quoted again under `## Evidence`, so a
    packet reporting `0 found · 1 weak — matched, no clear value` still contains the
    value's characters. Asserting on the packet as a whole passes without the field
    ever being answered — the mistake this helper exists to prevent.
    """
    for section in ("answers", "weak"):
        for ln in _section(packet, section).splitlines():
            if ln.strip().startswith(f"- {label}"):
                return ln.strip()
    return ""


def field_value_part(packet: str, label: str) -> str:
    """Just the value side of the field's line, after `- <label>: `."""
    line = field_line(packet, label)
    if not line:
        return ""
    body = line[len(f"- {label}"):].lstrip(": ").strip()
    # Weak lines are prefixed with this phrase before the text.
    return body.replace("matched, no clear value — ", "")


def assert_label_local(packet: str, label: str, value: str,
                       label_word: str) -> str:
    """The value came from the text AFTER the label, not from a generic snippet.

    `_label_window` returns what follows the label, so the label's own word must be
    absent from the result. The fallback snippet, by contrast, is centred on the
    query terms and contains the label word itself.

    Planting a marker earlier in the sentence does NOT work as a locality proof:
    `snippet()` truncates to 160 chars and can cut the marker off, which made an
    earlier version of this test pass vacuously.
    """
    part = field_value_part(packet, label)
    assert part, f"{label!r} appears in no answer/weak line:\n{packet}"
    assert value in part, (
        f"{label!r}'s line does not carry the value {value!r}:\n{part}")
    assert label_word.lower() not in part.lower(), (
        f"{label!r}'s value came from a generic snippet, not the label's own "
        f"window — it still contains the label word {label_word!r}:\n{part}")
    return part


def assert_field_carries_value(packet: str, label: str, value: str,
                               absent: str = None) -> str:
    """The field's OWN line carries the value, from the label's own window.

    Deliberately accepts either tier, because docdex only *extracts* values it can
    recognise — numbers, dates, amounts, IDs, emails (`VALUE_RE`). A company name is
    none of those, so `Legal name: Acme Quantum Pvt Ltd` can never become a
    confidently-stated answer, and it should not: asserting arbitrary prose after a
    colon as a certain value is how a retrieval tool starts being wrong with
    confidence. What IS required is that the field's line shows the value, taken from
    the text following its own label — so the agent can read and fill it.
    """
    line = field_line(packet, label)
    assert line, f"{label!r} appears in no answer/weak line:\n{packet}"
    assert value in line, (
        f"{label!r}'s line does not carry the value {value!r}:\n{line}")
    if absent is not None:
        # Proves the text came from AFTER the label. When `_label_window` fails to
        # recognise the label, context.py falls back to a generic snippet centred on
        # the query terms, which dumps the whole sentence — including everything
        # BEFORE the label. So a value visible via that fallback is not evidence the
        # label was recognised; `absent` is a marker planted before the label.
        assert absent not in line, (
            f"{label!r}'s line came from a generic snippet, not the label's own "
            f"window (it still contains {absent!r}):\n{line}")
    return line


def assert_field_stated_as_answer(packet: str, label: str, value: str) -> str:
    """Stronger: the value was extracted and stated, not just shown."""
    line = assert_field_carries_value(packet, label, value)
    assert "no clear value" not in line, (
        f"{label!r} matched but no value was extracted:\n{line}")
    assert label in _section(packet, "answers"), (
        f"{label!r} was not stated under ## Answers:\n{packet}")
    return line


# ------------------------------------------------- 1. recognising the label ----

def test_synonym_label_yields_the_value(tmp_path):
    """`Legal name` must read its value from a clause that says `Vendor`.

    This is the benchmark's long-standing `Legal name` miss: the corpus never uses
    the phrase the form asks for.
    """
    root = tmp_path / "syn"
    root.mkdir()
    (root / "contract.txt").write_text(
        "PREAMBLEMARK recitals whereby the parties agree, and with respect to the "
        "engagement, Vendor: Acme Quantum Pvt Ltd.\n", encoding="utf-8")
    project = _index(root, ALIASES)

    packet = build_packet(project, "fill the vendor form", budget=2000,
                          form_fields=["Legal name"])
    assert_label_local(packet, "Legal name", "Acme Quantum", "Vendor")


def test_inflected_label_yields_the_value(tmp_path):
    """`Governing law` must read a value from "governed by the laws of…"."""
    root = tmp_path / "inf"
    root.mkdir()
    (root / "contract.txt").write_text(
        "PREAMBLEMARK recitals and definitions apply, and this agreement is "
        "governed by the laws of Karnataka, India.\n", encoding="utf-8")
    project = _index(root)

    packet = build_packet(project, "fill the vendor form", budget=2000,
                          form_fields=["Governing law"])
    assert_label_local(packet, "Governing law", "Karnataka", "governed")


def test_exact_label_still_wins_over_a_synonym_elsewhere(tmp_path):
    """Precedence: when the literal label exists, it decides — synonyms never
    outrank it. Guards against a synonym hijacking a field that was already right."""
    root = tmp_path / "prec"
    root.mkdir()
    (root / "a_synonym.txt").write_text(
        "Vendor: Wrong Entity Ltd.\n", encoding="utf-8")
    (root / "b_exact.txt").write_text(
        "Legal name: Correct Entity Pvt Ltd.\n", encoding="utf-8")
    project = _index(root, ALIASES)

    packet = build_packet(project, "fill the vendor form", budget=2000,
                          form_fields=["Legal name"])
    assert_label_local(packet, "Legal name", "Correct Entity", "Legal name")
    assert "Wrong Entity" not in _section(packet, "answers"), (
        "a synonym match was asserted as the answer while an exact label existed:\n"
        + packet)


# --------------------------------------------------- 2. honesty of the tier ----

def test_synonym_derived_answer_is_flagged_approximate(tmp_path):
    """A value reached through a synonym must be marked `~approx`.

    The agent has to be able to tell "the document said Legal name" from "the
    document said Vendor and I assumed that's the same thing".
    """
    root = tmp_path / "flag"
    root.mkdir()
    (root / "contract.txt").write_text(
        "PREAMBLEMARK recitals apply. Vendor: Acme Quantum Pvt Ltd.\n",
        encoding="utf-8")
    project = _index(root, ALIASES)

    packet = build_packet(project, "fill the vendor form", budget=2000,
                          form_fields=["Legal name"])
    line = field_line(packet, "Legal name")
    assert_label_local(packet, "Legal name", "Acme Quantum", "Vendor")
    assert "~approx" in line, (
        "a synonym-derived value was presented without the ~approx flag:\n" + line)


def test_exact_label_answer_is_not_flagged_approximate(tmp_path):
    """The flag must mean something: an exact label match keeps a clean answer."""
    root = tmp_path / "noflag"
    root.mkdir()
    (root / "contract.txt").write_text(
        "Legal name: Acme Quantum Pvt Ltd.\n", encoding="utf-8")
    project = _index(root, ALIASES)

    packet = build_packet(project, "fill the vendor form", budget=2000,
                          form_fields=["Legal name"])
    line = assert_field_carries_value(packet, "Legal name", "Acme Quantum")
    assert "~approx" not in line, (
        "an exact label match was flagged approximate, which makes the flag "
        "meaningless:\n" + line)


def test_synonym_value_is_never_invented(tmp_path):
    """A declared synonym must not manufacture a value where none exists.

    The synonym is present but carries no value; the field must be reported honestly
    rather than filled from a neighbouring number.
    """
    root = tmp_path / "noinvent"
    root.mkdir()
    (root / "contract.txt").write_text(
        "The Vendor shall perform the services described herein.\n"
        "Invoice reference 998877 applies to prior work.\n", encoding="utf-8")
    project = _index(root, ALIASES)

    packet = build_packet(project, "fill the vendor form", budget=2000,
                          form_fields=["Legal name"])
    assert "998877" not in _section(packet, "answers"), (
        "an unrelated number became the answer for a synonym-labelled field:\n"
        + packet)


# ------------------------------------------- 3. reaching the chunk at all ------

def test_value_bearing_chunk_beats_exact_label_decoys(tmp_path):
    """The tracked xfail: 60 chunks tie on the label, one carries the value.

    Ranking must break that tie toward the chunk that actually contains a value,
    so it lands inside the per-field candidate window.
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
    assert_field_stated_as_answer(packet, "Payment terms", "net-45")


def test_value_tiebreak_never_overrides_real_relevance(tmp_path):
    """The tie-break must only settle TIES.

    A chunk with no digits but a genuinely better keyword match must still outrank a
    weakly-matching chunk that happens to contain a number.
    """
    root = tmp_path / "tie"
    root.mkdir()
    (root / "relevant.txt").write_text(
        "Force majeure force majeure force majeure clause applies here.\n",
        encoding="utf-8")
    (root / "numeric.txt").write_text(
        "Unrelated schedule 12345 mentions force once.\n", encoding="utf-8")
    for i in range(20):
        (root / f"f_{i:02d}.txt").write_text(
            "filler content about invoices and annexures\n", encoding="utf-8")
    project = _index(root)

    hits = index_db.search(project, "force majeure clause", limit=5)
    rels = [h["rel"] for h in hits]
    assert rels and rels[0] == "relevant.txt", (
        f"a numeric chunk displaced a genuinely more relevant one: {rels}")


# ------------------------------------------------- known gap (tracked) ---------

@pytest.mark.xfail(strict=True, reason=(
    "v0.5.3 target: apposition — the value PRECEDES the label. Contracts routinely "
    "name a party as 'Helios Components Pvt Ltd as the Vendor' or \"Acme (the "
    "'Supplier')\", so the value sits before the label docdex recognises and the "
    "window after the label is empty. Reading backwards is deliberately not done "
    "here: unbounded lookback is the cross-field leakage class DDX-029 fixed in "
    "v0.4.0 ('Payment terms are net-45. Vendor: Acme' would hand net-45 to Legal "
    "name). Doing it safely needs a required connective, a bounded lookback, and a "
    "clause-boundary stop — its own change, with its own review."))
def test_apposition_value_before_the_label(tmp_path):
    """The benchmark's last remaining miss (`Legal name`, 10/11 -> 11/11)."""
    root = tmp_path / "appos"
    root.mkdir()
    (root / "contract.txt").write_text(
        "Master agreement with Helios Components Pvt Ltd as the Vendor.\n",
        encoding="utf-8")
    project = _index(root, ALIASES)

    packet = build_packet(project, "fill the vendor form", budget=2000,
                          form_fields=["Legal name"])
    assert "Helios Components" in field_value_part(packet, "Legal name")


# ------------------ hardening from external review of this release --------------

def test_repeated_label_token_does_not_skip_the_value():
    """A label token appearing again later must not push the window past the value.

    Found by adversarial review of v0.5.2 (pre-existing: the old `rfind()` took the
    LAST occurrence too). "Payment terms: Net-45 and general terms apply" has
    "terms" twice, so a last-occurrence rule started the window after the SECOND one
    and returned "apply" — dropping a value that was present and labelled.
    """
    from docdex.context import label_window
    window, how = label_window("Payment terms: Net-45 and general terms apply",
                               "Payment terms", {"payment", "terms"})
    assert how == "exact"
    assert "Net-45" in window, (
        f"the value was skipped by a later repeat of the label token: {window!r}")


def test_stem_match_outranks_an_alias_match_in_a_better_hit():
    """Precedence must hold ACROSS candidate chunks, not just within one.

    Found by adversarial review: only an `exact` match could displace an incumbent,
    so an `alias` match in a higher-ranked chunk kept its place over a `stem` match
    in a lower-ranked one — inverting the documented exact→stem→alias order.
    """
    from docdex.context import _field_answer
    aliases = [["governing law", "jurisdiction"]]
    # Alias route: "Jurisdiction: 560042" — matches only via the declared synonym.
    alias_ans = _field_answer("Jurisdiction: 560042", "Governing law",
                              {"governing", "law"}, set(), aliases)
    # Stem route: "governed by the laws" — matches by word ending, higher precedence.
    stem_ans = _field_answer("This is governed by the laws of 560099",
                             "Governing law", {"governing", "law"}, set(), aliases)
    assert alias_ans and alias_ans[3] == "alias"
    assert stem_ans and stem_ans[3] == "stem"

    # Simulating build_packet's loop: the alias hit is seen FIRST (better BM25).
    rank = {"exact": 0, "stem": 1, "alias": 2}
    ans = None
    for cand in (alias_ans, stem_ans):
        if ans is None or rank[cand[3]] < rank[ans[3]]:
            ans = cand
    assert ans[3] == "stem", "an alias match outranked a stem match"


def test_alias_takes_the_first_synonym_in_reading_order():
    """Two synonyms of one field in one clause: prefer the first, as a reader would.

    Found by adversarial review: `max()` over alias matches took the RIGHTMOST, so
    "Vendor: Acme Corp, Supplier: Beta Ltd" yielded Beta rather than Acme.
    """
    from docdex.context import label_window
    window, how = label_window("Vendor: Acme Corp, Supplier: Beta Ltd",
                               "Legal name", {"legal", "name"},
                               [["legal name", "vendor", "supplier"]])
    assert how == "alias"
    assert "Acme" in window, f"took the rightmost synonym, not the first: {window!r}"


def test_conflict_detection_ignores_another_fields_labelled_value():
    """The conflict path needs the same cross-field guard as the answer path.

    A synonym can match the start of a DIFFERENT field's label ("Vendor" inside
    "Vendor turnover"), and `_field_values` had no foreign-label check, so a value
    belonging to another field could be logged as this field's — inventing a
    disagreement. The answer path already downgraded such windows to weak.
    """
    from docdex.context import _field_values
    aliases = [["legal name", "vendor"]]
    values = _field_values("Vendor turnover 998877", "Legal name",
                           {"legal", "name"}, aliases, foreign_terms={"turnover"})
    assert values == [], (
        f"another field's value was recorded against this field: {values}")
