"""Clause segmentation — where one fact stops and the next begins.

Everything that reads a field's value depends on this boundary: the value window
after a label is cut at it, conflicts are keyed by the clause a value came from, and
apposition reads backwards inside one clause and no further. So a boundary in the
wrong place is not a cosmetic problem — it decides whether a value is found, and
whose value it is.

**The gap this file closes:** a company that writes its legal form in full stops —
`Helios Components Pvt. Ltd.` — was never seen whole. Segmentation split on `. `
before anything read the text, leaving `Ltd.` alone in its own clause, so v0.5.7's
apposition reading found no name and the forward window stopped at `Pvt.`. It shipped
as a strict xfail in `test_apposition.py` because fixing it means changing a boundary
every other reading depends on.

The rule: a full stop does not end a clause when the word before it is an
abbreviation that belongs to a company name (`Pvt.`, `Ltd.`, `Co.`, `Inc.`) **and**
the next word does not begin a new sentence. "Does not begin a new sentence" is read
as "starts lowercase", which is the standard test and the only one available here —
which is why the negative cases below matter as much as the positive ones. In text
that is entirely upper-case that test carries no information, so the boundary stays
where it was and the name goes unread: a miss, not a wrong answer.
"""
from __future__ import annotations

import json

import pytest

from docdex import index_db
from docdex.context import _clauses, _cut_after, build_packet
from docdex.scaffold import run_init
from docdex.sync import run_sync

ALIASES = {"legal name": ["vendor", "supplier", "party", "parties", "legal entity"],
           "payment terms": ["payment schedule"]}


def corpus(tmp_path, text: str, aliases: dict = None):
    root = tmp_path / "seg"
    root.mkdir(parents=True, exist_ok=True)
    (root / "contract.txt").write_text(text, encoding="utf-8")
    project = run_init(root, quiet=True)
    project.aliases_path.write_text(
        json.dumps(ALIASES if aliases is None else aliases), encoding="utf-8")
    run_sync(project, quiet=True)
    index_db.build(project, quiet=True)
    return project


def _section(packet: str, name: str) -> str:
    header = {"answers": "## Answers", "weak": "## Needs follow-up"}[name]
    if header not in packet:
        return ""
    return packet.split(header, 1)[1].split("\n##", 1)[0]


def value_of(packet: str, label: str) -> str:
    """The value side of this field's line, from Answers or Needs-follow-up.

    Deliberately not "is the string anywhere in the packet": the source sentence is
    quoted again under `## Evidence`, so a packet that answered nothing still contains
    the characters.
    """
    for section in ("answers", "weak"):
        for ln in _section(packet, section).splitlines():
            if ln.strip().startswith(f"- {label}"):
                body = ln.strip()[len(f"- {label}"):].lstrip(": ").strip()
                return body.replace("matched, no clear value — ", "")
    return ""


def answered(packet: str, label: str) -> str:
    """The value side, but only from `## Answers` — a weak line is not an answer."""
    for ln in _section(packet, "answers").splitlines():
        if ln.strip().startswith(f"- {label}"):
            return ln.strip()[len(f"- {label}"):].lstrip(": ").strip()
    return ""


def fill(project, *labels: str) -> str:
    return build_packet(project, "fill the vendor form", budget=2000,
                        form_fields=list(labels))


# ============================================ 1. an abbreviated name stays whole

def test_an_abbreviated_corporate_form_does_not_end_a_clause():
    """`Pvt. Ltd.` is one company, written the way registrars write it."""
    got = _clauses("Signed by Helios Components Pvt. Ltd. as the Vendor.")
    assert len(got) == 1, f"an abbreviated name was split across clauses: {got}"
    assert "Pvt. Ltd." in got[0], got


@pytest.mark.parametrize("text", [
    "Agreement with Acme Industries Co. Ltd. as the Supplier.",
    "Beta Holdings Inc. hereinafter referred to as the Vendor.",
    "Gamma Systems Pvt. Ltd. acting as Supplier shall invoice.",
])
def test_common_abbreviated_forms_stay_in_one_clause(text):
    """The clause must be the WHOLE sentence, not a truncated prefix of it.

    Found by adversarial review: `len(got) == 1` also holds for a segmenter that
    returns only "Agreement with Acme Industries Co." and throws the role away — which
    would make the name unreadable in the very case this rule exists for.
    """
    got = _clauses(text)
    assert got == [text], f"{text!r} was segmented as {got}"


def test_the_value_window_after_a_label_keeps_an_abbreviation():
    """`_cut_after` stops the value window at a clause end, so it needs the same rule.

    Without this the forward reading of `Legal name: Helios Components Pvt. Ltd.`
    returns `Helios Components Pvt` — a name that is not the company's.
    """
    window = _cut_after("Legal name: Helios Components Pvt. Ltd.", len("Legal name"))
    assert window.startswith("Helios Components Pvt. Ltd"), (
        f"the value window was cut inside the name: {window!r}")


def test_an_abbreviated_name_is_read_end_to_end(tmp_path):
    """The gap as a user meets it: the field must report the whole company."""
    project = corpus(tmp_path,
                     "Signed by Helios Components Pvt. Ltd. as the Vendor.\n")
    got = value_of(fill(project, "Legal name"), "Legal name")
    assert "Helios Components Pvt. Ltd" in got, (
        f"an abbreviation dot broke the name: {got!r}")


# ================================= 2. real boundaries must still be boundaries

def test_a_sentence_end_still_ends_a_clause():
    """The whole point of the boundary. If this breaks, every field leaks."""
    got = _clauses("Payment terms are net-45. Vendor: Acme Industries Ltd.")
    assert len(got) == 2, f"a sentence boundary was lost: {got}"


def test_an_abbreviation_before_a_new_sentence_still_splits():
    """`Ltd.` genuinely ends a sentence when a capitalised word follows it."""
    got = _clauses("Signed by Helios Components Pvt. Ltd. The parties agree.")
    assert len(got) == 2, f"two sentences were merged into one clause: {got}"
    assert got[-1].startswith("The parties"), got


@pytest.mark.parametrize("text,expected", [
    # A unit word right after a number belongs to the value.
    ("Renewal term: 24 months Vendor: Acme Industries Ltd", "24 months"),
    # ...but a field label that merely STARTS with a unit word is still a boundary.
    ("Renewal term: 24 months Working days: Monday to Friday", "24 months"),
    ("Renewal term: 24 months Business days: five", "24 months"),
])
def test_a_unit_word_belongs_to_the_value_but_a_label_still_stops_it(text, expected):
    """Both halves of the same rule, and the second was found by adversarial review.

    The value window stops before the next `Label:`, and that lookahead allows spaces,
    so in "24 months Vendor:" it read "months Vendor" as the label and cut the value to
    a bare `24`. Refusing to start a label with a unit word fixed that and broke the
    other direction: `Working days:` is a real label whose first word is a unit, and it
    stopped being a boundary at all, so `Renewal term` swallowed the next field.

    A unit only belongs to the value when a NUMBER precedes it. That is the whole rule.
    """
    assert _cut_after(text, len("Renewal term")) == expected, (
        f"window was {_cut_after(text, len('Renewal term'))!r}")


def test_a_word_that_is_not_an_abbreviation_still_ends_a_clause():
    """Only name abbreviations are exempt — not every short word before a dot.

    A blanket "a dot followed by a lowercase word is not a boundary" rule would merge
    most of a document into one clause, and a clause is the unit that keeps one
    field's value away from another's.
    """
    got = _clauses("The renewal term is 24 months. the parties agree to it.")
    assert len(got) == 2, f"a plain sentence end was treated as an abbreviation: {got}"


def test_a_neighbouring_value_is_not_pulled_into_a_name(tmp_path):
    """The leakage class this boundary exists to prevent, with the fix in place.

    "Payment terms are net-45." and the naming clause are separate sentences, and the
    backward reading must not cross between them: `net-45` is not a legal name and
    `Helios Components Pvt. Ltd.` is not a payment term.
    """
    project = corpus(
        tmp_path,
        "Payment terms are net-45 from invoice. "
        "Signed by Helios Components Pvt. Ltd. as the Vendor.\n")
    packet = fill(project, "Legal name", "Payment terms")
    name = value_of(packet, "Legal name")
    terms = value_of(packet, "Payment terms")
    assert "net-45" not in name, f"a payment term leaked into the name: {name!r}"
    assert "Helios" not in terms, f"a company leaked into the payment terms: {terms!r}"
    assert "net-45" in terms, f"the payment term was lost: {terms!r}"


def test_an_abbreviation_does_not_join_two_companies(tmp_path):
    """Two companies in consecutive sentences must not become one name.

    "Zeta Corporation Ltd." ends a sentence; "Acme" starts the next. Merging them
    would assert a company that does not exist.
    """
    project = corpus(
        tmp_path,
        "Work was done by Zeta Corporation Ltd. Acme Industries Ltd as the Vendor.\n")
    got = value_of(fill(project, "Legal name"), "Legal name")
    assert "Zeta" not in got, f"two companies were joined into one name: {got!r}"


@pytest.mark.xfail(strict=True, reason=(
    "The abbreviation rule covers COMPANY forms only, so 'Invoice No. 42' is still cut "
    "into 'Invoice No.' and '42' and the label loses its value — exactly the way "
    "'Pvt. Ltd.' lost its name before this release. Continuing after a reference "
    "abbreviation (No., Sr., Cl., Art.) means continuing on a DIGIT rather than a "
    "lowercase word, which is a second rule with its own blast radius: every stray "
    "'see No. 5' cross-reference in the corpus would join the sentence after it. "
    "Tracked in ROADMAP for v0.5.9."))
def test_a_reference_abbreviation_keeps_its_number():
    """Known gap: "Invoice No. 42" is a label and a value, split apart."""
    got = _clauses("Invoice No. 42 is attached for your records.")
    assert len(got) == 1, f"a label was split from its own value: {got}"


def test_a_labelled_field_after_an_abbreviation_still_splits(tmp_path):
    """A following `Label:` ends the value window even mid-sentence.

    Both fields are asserted by their exact values, not by the absence of one string:
    adversarial review pointed out that dropping BOTH fields entirely also satisfies
    "net-45 is not in the legal name", and losing two present values is not a pass.
    """
    project = corpus(
        tmp_path,
        "Legal name: Helios Components Pvt. Ltd. Payment terms: net-45.\n")
    packet = fill(project, "Legal name", "Payment terms")
    assert answered(packet, "Legal name").split("  [")[0].strip() == \
        "Helios Components Pvt. Ltd", (
        "the name was lost or absorbed the next field:\n" + packet)
    assert "net-45" in value_of(packet, "Payment terms"), (
        "the second field lost its value:\n" + packet)


def test_a_dense_line_keeps_the_unit_end_to_end(tmp_path):
    """The unit rule, through the packet rather than through `_cut_after`.

    Found by adversarial review: the helper-level test would stay green if
    `_field_answer` re-extracted only the numeric part after the window was cut
    correctly, and `Renewal term: 24` is the exact wrong answer this release set out
    to fix.
    """
    project = corpus(
        tmp_path, "Renewal term: 24 months Vendor: Acme Industries Ltd\n")
    got = value_of(fill(project, "Renewal term", "Legal name"), "Renewal term")
    assert "24 months" in got, f"the value lost its unit in the packet: {got!r}"
