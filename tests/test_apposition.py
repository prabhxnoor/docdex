"""v0.5.7 — apposition: a value written BEFORE the label that names it.

Contracts name a party by stating it and then saying what role it plays:

    Master agreement with Helios Components Pvt Ltd **as the Vendor**.
    Acme Corporation (**the "Supplier"**) shall deliver.

Every earlier release read a field's value from the text *after* its label, so in
both lines the window after the label is empty and the field reported "matched, no
clear value". This is the form benchmark's last miss (`Legal name`, 10/11 → 11/11).

**Reading backwards is the dangerous direction**, which is why it waited for its own
release. Unbounded lookback is the DDX-029 cross-field leakage class that v0.4.0
fixed: given "Payment terms are net-45. Vendor: Acme" a backwards reader hands
`net-45` to `Legal name` — a confidently wrong answer, which costs far more than the
missing one it replaces. So the negative tests below are the point of this file, not
an afterthought; the positives are the easy half.

The rule that makes it safe: a value read backwards must be a run of proper nouns
immediately before a required apposition connective, **ending in a corporate form**
(Ltd, Pvt Ltd, LLP, Inc, GmbH …) unless the label was introduced as a quoted or
parenthesised defined term. Values are not capitalised proper nouns, so `net-45`,
`24 months` and `INR 6.5 crore` cannot be read this way at all — the leakage class is
closed by construction rather than by a list of exceptions.

The corporate-form requirement came from running the feature over the real 92,709-chunk
corpus rather than from any review. Without it, three of the four names it read were
nonsense: two from a title-cased investor deck ("TCL Confirmed Northwind Systems as the
vendor") and one from an ALL-CAPS invoice note ("LINES 1 AND 22 MAY DELAY THE ORDER AS
THE SUPPLIER"). In title-cased and upper-cased text, "is this word capitalised" carries
no information at all, and extracted slide text has no sentence punctuation to stop the
scan either. So this reads a corporate ENTITY defined by apposition, not apposition in
general: `IBM as the Vendor` is deliberately missed.
"""
from __future__ import annotations

import json
import re

import pytest

from docdex import index_db
from docdex.context import build_packet
from docdex.scaffold import run_init
from docdex.sync import run_sync

# `party` and `parties` are included deliberately: they are the most dangerous
# synonyms for this feature, because contracts use them in sentences that also carry
# other fields' values ("the parties agree the renewal term is 24 months").
# `counterparty` is declared so the confidentiality-clause case below actually reaches
# the reader. Without it that fixture matched nothing at all — the packet came back
# `Legal name: not found (tried: legal, name)` and the test's "no name was read"
# assertion passed on the words "not found", proving nothing.
ALIASES = {"legal name": ["vendor", "supplier", "party", "parties", "legal entity",
                          "counterparty"],
           "payment terms": ["payment schedule"],
           "liability cap": ["limitation of liability"]}


def corpus(tmp_path, text: str, aliases: dict = None):
    root = tmp_path / "appos"
    root.mkdir(parents=True, exist_ok=True)
    (root / "contract.txt").write_text(text, encoding="utf-8")
    project = run_init(root, quiet=True)
    project.aliases_path.write_text(
        json.dumps(ALIASES if aliases is None else aliases), encoding="utf-8")
    run_sync(project, quiet=True)
    index_db.build(project, quiet=True)
    return project


def _section(packet: str, name: str) -> str:
    header = {"answers": "## Answers", "weak": "## Needs follow-up",
              "missing": "## Missing"}[name]
    if header not in packet:
        return ""
    return packet.split(header, 1)[1].split("\n##", 1)[0]


def field_line(packet: str, label: str) -> str:
    """This field's own line from Answers or Needs-follow-up.

    Deliberately NOT "is the value somewhere in the packet": the source sentence is
    quoted again under `## Evidence`, so a packet that answered nothing still
    contains the value's characters. Asserting on the whole packet passes without the
    field ever being answered.
    """
    for section in ("answers", "weak"):
        for ln in _section(packet, section).splitlines():
            if ln.strip().startswith(f"- {label}"):
                return ln.strip()
    return ""


def value_of(packet: str, label: str) -> str:
    """The value side of the field's line, '' when the field has no line."""
    line = field_line(packet, label)
    if not line:
        return ""
    body = line[len(f"- {label}"):].lstrip(": ").strip()
    return body.replace("matched, no clear value — ", "")


def answered(packet: str, label: str) -> str:
    """The value side, but only from `## Answers` — weak lines are not answers."""
    for ln in _section(packet, "answers").splitlines():
        if ln.strip().startswith(f"- {label}"):
            return ln.strip()[len(f"- {label}"):].lstrip(": ").strip()
    return ""


def fill(project, label: str, task: str = "fill the vendor form") -> str:
    return build_packet(project, task, budget=2000, form_fields=[label])


# ======================================================= the shapes that must work
@pytest.mark.parametrize("text,expected", [
    # the benchmark's own line
    ("Master agreement with Helios Components Pvt Ltd as the Vendor.\n",
     "Helios Components Pvt Ltd"),
    # a defined term in parentheses, quoted and unquoted
    ('Acme Corporation (the "Supplier") shall deliver the services.\n',
     "Acme Corporation"),
    ("Acme Corporation (the Supplier) shall deliver the services.\n",
     "Acme Corporation"),
    # the long-form legal phrasing
    ("Gamma Industries LLP hereinafter referred to as the Vendor agrees.\n",
     "Gamma Industries LLP"),
    # no article after the connective
    ("Delta Systems Inc acting as Supplier shall invoice monthly.\n",
     "Delta Systems Inc"),
    # an all-caps name, and a lowercase corporate suffix
    ("Signed by HELIOS COMPONENTS PVT LTD as the Vendor.\n",
     "HELIOS COMPONENTS PVT LTD"),
    ("Signed by Helios Components pvt ltd as the Vendor.\n",
     "Helios Components pvt ltd"),
])
def test_a_value_before_its_label_is_read(tmp_path, text, expected):
    """The field must report the name, not "matched, no clear value".

    Asserted against `## Answers` and by EQUALITY, both on adversarial review's advice:
    `value_of` accepts the weak tier, so demoting every apposition reading to "matched,
    no clear value" would have left this whole set green; and `expected in got` accepts
    `Helios Components Pvt Ltd as the Vendor`, which is not a company that exists.
    """
    project = corpus(tmp_path, text)
    packet = fill(project, "Legal name")
    got = answered(packet, "Legal name")
    stated = re.sub(r"\s*\[[^\]]*\](\s*~approx)?\s*$", "", got).strip()
    assert stated == expected, (
        f"apposition not read from {text.strip()!r} — answered {stated!r}, "
        f"expected {expected!r}:\n{packet}")


def test_an_apposition_value_is_tagged_approximate(tmp_path):
    """Reading a value backwards is an inference about sentence structure, not a
    label-then-value adjacency, so it can never be presented as certain."""
    project = corpus(tmp_path,
                     "Master agreement with Helios Components Pvt Ltd as the Vendor.\n")
    line = field_line(fill(project, "Legal name"), "Legal name")
    assert "Helios Components" in line, line
    assert "~approx" in line, (
        "a value read from sentence structure was presented as an exact match: "
        + line)


# ============================================ the leakage class this must not open
@pytest.mark.parametrize("text,forbidden", [
    # DDX-029 itself: a value in the preceding sentence, label after it
    ("Payment terms are net-45. Vendor: Acme Industries Ltd.\n", "net-45"),
    # a comma appositive is NOT a licence to read backwards
    ("Payment terms are net-45, the Vendor is Acme Industries Ltd.\n", "net-45"),
    # a bare parenthesis is not a defined-term marker
    ("The aggregate liability cap is INR 6.5 crore (Vendor: Acme).\n", "6.5"),
    # `as the` present, but what precedes it is a value, not a name
    ("The renewal term is 24 months as the parties have agreed.\n", "24 months"),
    ("Payment terms are net-45 as the Vendor requires.\n", "net-45"),
    # a value immediately before a legitimate connective
    ("The fee of INR 1.8 crore as the Supplier invoices it.\n", "1.8"),
])
def test_a_value_from_another_field_is_never_read_backwards(tmp_path, text, forbidden):
    """The whole reason this feature waited for its own release.

    Each line puts another field's value before a label for `Legal name`. Reporting
    any of them as the legal name is a confidently wrong answer — strictly worse than
    the missing answer it would replace, because the agent cannot tell.
    """
    project = corpus(tmp_path, text)
    got = value_of(fill(project, "Legal name"), "Legal name")
    assert forbidden not in got, (
        f"another field's value leaked into Legal name from {text.strip()!r}: "
        f"{got!r}")


def test_a_labelled_value_still_wins_over_an_apposition(tmp_path):
    """Apposition is a FALLBACK. A value properly labelled in the ordinary
    direction must always decide, so this feature cannot reorder existing answers.

    Uses `Liability cap`, whose value is one docdex already recognises going
    forwards, so the two directions genuinely compete.
    """
    project = corpus(
        tmp_path,
        "Liability cap: INR 6.5 crore.\n"
        "Helios Components Pvt Ltd as the limitation of liability.\n")
    got = value_of(fill(project, "Liability cap", "fill the contract form"),
                   "Liability cap")
    assert "6.5" in got, (
        f"a forward-labelled value lost to an apposition elsewhere: {got!r}")
    assert "Helios" not in got, got


def test_a_forward_name_is_read_as_a_value(tmp_path):
    """Closed in v0.5.8 (v0.5.7 asserted the opposite, deliberately).

    v0.5.7 read a name written BEFORE its label and could not read the plainest form
    there is, so this case shipped as an explicit statement of that boundary: "if this
    is now intended, delete this test and say so in the changelog". It is now intended.
    A name is read forward only when a separator presents it as the field's value, and
    only for a field known to want a party — see `tests/test_field_types.py`, which owns
    the rules; this case stays here so the two directions are exercised side by side.
    """
    project = corpus(tmp_path, "Legal name: Beta Holdings Ltd.\n")
    assert "Beta Holdings Ltd" in answered(fill(project, "Legal name"), "Legal name")


def test_apposition_does_not_cross_a_sentence_boundary(tmp_path):
    """The name must come from the label's own clause.

    "Zeta Corporation completed the audit. Acme Industries as the Vendor" must not
    reach back into the previous sentence for a more impressive-looking name.
    """
    project = corpus(
        tmp_path,
        "Zeta Corporation completed the audit. Acme Industries Ltd as the Vendor.\n")
    got = value_of(fill(project, "Legal name"), "Legal name")
    assert "Acme Industries Ltd" in got, got
    assert "Zeta" not in got, (
        f"the lookback crossed a sentence boundary: {got!r}")


def test_apposition_stops_at_a_clause_boundary(tmp_path):
    """A neighbouring field in its own clause must not bleed into the name."""
    project = corpus(
        tmp_path,
        "Governing law: Karnataka; Helios Pvt Ltd as the Vendor.\n")
    got = value_of(fill(project, "Legal name"), "Legal name")
    assert "Helios Pvt Ltd" in got, got
    assert "Karnataka" not in got and "Governing" not in got, (
        f"the lookback swallowed a neighbouring field: {got!r}")


def test_a_dense_line_naming_another_field_is_not_reported_as_found(tmp_path):
    """When one clause carries two fields, the reading is ambiguous — say so.

    "Governing law: Karnataka Helios Pvt Ltd as the Vendor" has no delimiter between
    the two, so a backwards scan cannot know where the previous field's value ended.
    docdex must not assert a name it may have merged with a neighbour's value; the
    existing 'weak' tier is exactly for this.

    Both fields are requested, because "names another field" is only meaningful
    relative to the other fields on the form — with one field there are no others.
    """
    project = corpus(
        tmp_path,
        "Governing law: Karnataka Helios Pvt Ltd as the Vendor.\n")
    packet = build_packet(project, "fill the contract form", budget=2000,
                          form_fields=["Governing law", "Legal name"])
    assert not answered(packet, "Legal name"), (
        "an ambiguous dense line was asserted as a found answer:\n" + packet)


def test_the_lookback_is_bounded(tmp_path):
    """A long run of capitalised words must not all become the value.

    Document headings and party recitals are often many capitalised words long;
    without a bound the "name" becomes a paragraph, which is not an answer.
    """
    long_name = " ".join(f"Word{i}" for i in range(30))
    project = corpus(tmp_path, f"{long_name} Ltd as the Vendor.\n")
    got = value_of(fill(project, "Legal name"), "Legal name")
    assert len(got.split()) <= 12, (
        f"the lookback swallowed {len(got.split())} words: {got!r}")


def test_no_connective_means_no_apposition(tmp_path):
    """A name simply sitting before the label is not an apposition.

    "Acme Industries Vendor" says nothing about which is which; guessing would be
    the same class of error as reading backwards past a value.
    """
    project = corpus(tmp_path, "Acme Industries Ltd Vendor of record.\n")
    assert not answered(fill(project, "Legal name"), "Legal name"), (
        "a name adjacent to a label with no connective was reported as an answer")


# ================================ hardening from review of this release ==========
@pytest.mark.parametrize("text,expected,forbidden", [
    ("In January, Helios Components Pvt Ltd as the Vendor.\n",
     "Helios Components Pvt Ltd", "January"),
    ("Section 1: Acme Corp as the Vendor.\n", "Acme Corp", "Section"),
    ("Signed at Bengaluru, Delta Systems Inc as the Supplier.\n",
     "Delta Systems Inc", "Bengaluru"),
])
def test_the_name_does_not_cross_punctuation(tmp_path, text, expected, forbidden):
    """A comma or a colon ends the name, even when a capitalised word follows it.

    Found by adversarial review. The backward scan looked only at whether each token
    was capitalised, never at what sat BETWEEN tokens, so "In January, Helios
    Components Pvt Ltd" was returned whole — punctuation and unrelated words included
    — and asserted as the entity's legal name. A corrupted name is worse than none.
    """
    project = corpus(tmp_path, text)
    got = value_of(fill(project, "Legal name"), "Legal name")
    assert expected in got, got
    assert forbidden not in got, (
        f"the name ran back across punctuation: {got!r}")


def test_a_repeated_label_word_does_not_hide_the_apposition(tmp_path):
    """An earlier stray label word must not move the lookback to the wrong place.

    Found by adversarial review: the label's start was recorded at the FIRST token
    that matched any part of it, so for the two-word label `Legal name` a stray
    "legal" earlier in the clause pointed the lookback at the start of the sentence,
    where there is no connective — and a present apposition was reported missing.
    """
    project = corpus(
        tmp_path,
        "The legal team reviewed it and Acme Corp as the legal name applies.\n")
    got = value_of(fill(project, "Legal name"), "Legal name")
    assert "Acme Corp" in got, (
        f"a present apposition was missed because a label word appeared earlier: "
        f"{got!r}")


def test_a_clean_apposition_beats_an_ambiguous_one_in_the_same_chunk(tmp_path):
    """The apposition fallback must rank like the forward path: clean first.

    Found by adversarial review — it returned the first clause that produced a name,
    so an ambiguous reading in an earlier clause suppressed an unambiguous one right
    after it, reporting a weak answer where a found one was available.
    """
    project = corpus(
        tmp_path,
        "Effective date: 2025 and Acme Corp as the Vendor; "
        "Beta Industries Ltd as the Vendor.\n")
    packet = build_packet(project, "fill the contract form", budget=2000,
                          form_fields=["Effective date", "Legal name"])
    assert answered(packet, "Legal name"), (
        "an unambiguous apposition was suppressed by an ambiguous earlier one:\n"
        + packet)
    assert "Beta Industries Ltd" in answered(packet, "Legal name"), (
        answered(packet, "Legal name"))


def test_a_number_inside_a_name_is_kept(tmp_path):
    """Real company names contain digits, and a truncated name is a wrong name.

    Found by adversarial review: the scan stopped at any non-capitalised token, so
    "Group 4 Sentinel" was reported as "Sentinel" — a different entity, asserted as
    the legal name. A digit is allowed INSIDE the run only; the token touching the
    connective must still be a name word, which is what keeps `net-45` and
    `24 months` unreadable.
    """
    project = corpus(tmp_path, "Group 4 Sentinel Ltd as the Vendor.\n")
    got = value_of(fill(project, "Legal name"), "Legal name")
    assert "Group 4 Sentinel Ltd" in got, (
        f"a name containing a digit was truncated: {got!r}")


def test_a_number_touching_the_connective_is_still_refused(tmp_path):
    """The guard on the test above: allowing interior digits must not open the leak.

    The leakage cases all put the value immediately before the connective, so the
    rightmost token must still be a name word.
    """
    for text, forbidden in [
        ("Payment terms are net-45 as the Vendor.\n", "45"),
        ("The term is 24 as the Vendor.\n", "24"),
    ]:
        project = corpus(tmp_path / text[:8].replace(" ", "_"), text)
        got = value_of(fill(project, "Legal name"), "Legal name")
        assert forbidden not in got, (
            f"a value touching the connective was read as a name from "
            f"{text.strip()!r}: {got!r}")


# =========================== what the REAL corpus taught this feature ============
# Shape-preserving reconstructions of real corpus lines this feature read wrongly
# before the
# corporate-form requirement. Kept as tests because no review produced them and no
# synthetic fixture would have: they are title-cased slide text, upper-cased invoice
# notes and a role description, and all three are ordinary in real document sets.
@pytest.mark.parametrize("text,forbidden", [
    # an investor deck bullet — every word title-cased, no sentence punctuation
    ("Receipt of 4.56Cr from NDIF against the AB XYZQ Grant PQR Confirmed Northwind Systems "
     "as the vendor for the Secure Fabric programme\n", "Confirmed"),
    # an ALL-CAPS invoice note: capitalisation carries no information here
    ("ORDER ID: 820473155 NOTE: LINES 1 AND 22 MAY DELAY THE ORDER AS THE SUPPLIER "
     "ONLY FURNISHES PART QUANTITIES\n", "DELAY"),
    # "as A counterparty" describes a role, it does not define a party
    ("Not to disclose the identity of Aldridge Vance or any of its Affiliates as a "
     "counterparty in any publicity\n", "Affiliates"),
])
def test_real_corpus_lines_that_are_not_names(tmp_path, text, forbidden):
    """None of these is a legal name, and each was read as one."""
    project = corpus(tmp_path, text)
    got = value_of(fill(project, "Legal name"), "Legal name")
    assert forbidden not in got, (
        f"a non-name was read as the legal name: {got!r}")


def test_the_real_corpus_line_that_IS_a_name_still_works(tmp_path):
    """The one correct reading on the real corpus must survive the tightening.

    A shape-preserving reconstruction of a partner POC agreement's parties clause —
    the wording and punctuation are the original's, the company is invented. A guard
    against over-correcting: it would be easy to make the negatives above pass by
    disabling the feature.

    Two things this pins deliberately. It asserts the value EXACTLY, because substring
    containment also accepts a longer wrong reading that happens to span the right
    name. And it records that the reading lands in `## Needs follow-up (weak)`, not
    `## Answers`: that is what docdex does today, and a test that quietly tolerated
    either would not notice the day it changes in the wrong direction.
    """
    project = corpus(
        tmp_path,
        "Trial Agreement 1 Background 1.1 Parties The parties to the present "
        "agreement are: Helios Components Private Limited (“Supplier”), a company "
        "incorporated under the laws of India\n")
    packet = fill(project, "Legal name")
    stated = re.sub(r"\s*\[[^\]]*\](\s*~approx)?\s*$", "",
                    value_of(packet, "Legal name")).strip()
    assert stated == "Helios Components Private Limited", (
        f"the one apposition docdex read correctly was lost or blurred: {stated!r}")
    assert not answered(packet, "Legal name"), (
        "this reading is expected to be weak, not asserted under ## Answers — if that "
        "improved, tighten this test rather than deleting it")


def test_a_company_is_not_offered_as_a_quantity(tmp_path):
    """A field that asks for an amount must not be answered with a company.

    Found by adversarial review. Apposition was field-agnostic, so a document reading
    "Helios Components Pvt Ltd as the limitation of liability" put a company name
    under `## Answers` for `Liability cap`. Reading it faithfully is one thing;
    asserting it as the cap is another.
    """
    project = corpus(tmp_path, "Helios Components Pvt Ltd as the limitation of "
                               "liability.\n")
    packet = build_packet(project, "fill the contract form", budget=2000,
                          form_fields=["Liability cap"])
    assert "Helios" not in answered(packet, "Liability cap"), (
        "a company was asserted as an amount:\n" + packet)


def test_a_name_does_not_cross_a_sentence_end_without_a_space(tmp_path):
    """`.` joins an abbreviation ("Pvt. Ltd") but must not glue two sentences.

    Found by adversarial review: extracted PDF text often loses the space after a full
    stop, and "Zeta Corporation.Acme Industries Ltd" was returned as one company.
    """
    project = corpus(
        tmp_path,
        "Agreement with Zeta Corporation.Acme Industries Ltd as the Vendor.\n")
    got = value_of(fill(project, "Legal name"), "Legal name")
    assert "Zeta" not in got, f"two sentences were joined into one name: {got!r}"


def test_an_abbreviation_dot_still_joins_a_name(tmp_path):
    """Closed in v0.5.8 (a strict xfail from v0.5.7).

    "Pvt. Ltd." is one company, and every release up to v0.5.7 read it as three
    clauses: segmentation split on '. ' before anything read the text, so the backward
    scan from `as` found only "Ltd.". The boundary is now abbreviation-aware —
    `tests/test_clause_segmentation.py` owns that rule and the cases where a full stop
    must still end a clause. This case stays here because it is what apposition needs
    from the boundary.
    """
    project = corpus(tmp_path, "Signed by Helios Components Pvt. Ltd. as the "
                               "Vendor.\n")
    got = value_of(fill(project, "Legal name"), "Legal name")
    assert "Helios Components Pvt. Ltd" in got, (
        f"an abbreviation dot broke the name: {got!r}")


def test_a_dense_line_is_not_asserted_even_for_a_single_field(tmp_path):
    """The dense-line guard must not depend on which fields were requested.

    Found by adversarial review: "names another field" was decided from the OTHER
    fields on the form, so asking for `Legal name` alone left nothing foreign and
    "Governing law: Karnataka Helios Pvt Ltd as the Vendor" was confidently answered
    as "Karnataka Helios Pvt Ltd" — a neighbour's value merged into the name. A
    colon before the name is a structural signal that needs no other field to see.
    """
    project = corpus(tmp_path,
                     "Governing law: Karnataka Helios Pvt Ltd as the Vendor.\n")
    assert not answered(fill(project, "Legal name"), "Legal name"), (
        "a value from before a colon was merged into the name and asserted")


def test_an_over_long_name_is_refused_not_truncated(tmp_path):
    """Cutting a name to fit is silently changing it.

    Found by adversarial review: a 30-word capitalised run was trimmed to its last
    eight tokens and asserted as the legal name — a different entity. When the run is
    longer than can be read safely, docdex does not know where the name begins, and
    the honest answer is not to answer.
    """
    long_name = " ".join(f"Word{i}" for i in range(30))
    project = corpus(tmp_path, f"{long_name} Ltd as the Vendor.\n")
    assert not answered(fill(project, "Legal name"), "Legal name"), (
        "a truncated name was asserted as the name")


def test_an_existing_index_is_rebuilt_so_the_new_signal_applies(tmp_path):
    """This release changes DERIVED data, so an index already on disk must be redone.

    Found by running v0.5.7 against the real corpus: the sync reindexed only the 22
    files that had changed, which is correct for text — but `has_value` is computed at
    index time, and an apposition-defined party only becomes FINDABLE once it is
    recomputed. Without a schema bump the extraction half would work while the
    retrieval half stayed inert on every index that already existed, which is exactly
    how v0.5.2 shipped a column no existing database ever got.
    """
    import sqlite3
    project = corpus(tmp_path,
                     "Master agreement with Helios Components Pvt Ltd as the Vendor.\n")
    conn = sqlite3.connect(project.index_db_path)
    try:
        # Look like an index built by the previous release: older schema recorded, and
        # this chunk not yet known to carry a value.
        conn.execute("UPDATE meta SET value='5' WHERE key='schema'")
        conn.execute("UPDATE chunks SET has_value=0")
        conn.commit()
    finally:
        conn.close()

    index_db.build(project, quiet=True)

    conn = sqlite3.connect(project.index_db_path)
    try:
        flagged = conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE has_value=1 AND text LIKE "
            "'%as the Vendor%'").fetchone()[0]
    finally:
        conn.close()
    assert flagged, (
        "an index from the previous release was left with the old value signal, so "
        "the apposition chunk stays unreachable for the field it answers")


def test_a_field_with_nothing_to_read_is_still_reported_honestly(tmp_path):
    """The guard against fabrication: no value anywhere means no value reported."""
    project = corpus(tmp_path, "The parties acknowledge the recitals above.\n")
    packet = fill(project, "Legal name")
    assert not answered(packet, "Legal name"), (
        "a value was invented for a field the document does not answer:\n" + packet)
