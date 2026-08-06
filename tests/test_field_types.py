"""What kind of value a field wants, and a name written after its label.

Two gaps v0.5.7 stated and did not close, which turn out to be one question asked
twice.

**A name after its label was not a value.** v0.5.7 taught docdex to read a company
named *before* its label ("Helios Components Pvt Ltd **as the Vendor**"), but
`Legal name: Beta Holdings Ltd` — the plainest form there is — still reported
"matched, no clear value", because a value had to look like a number, an amount, a
date or an email. A company name is none of those.

**Which fields may be answered with a company was decided by a deny-list.** v0.5.7
refused apposition for a field whose label contained one of about forty quantity
words (`cap`, `amount`, `rate`, …). Every other label was allowed, so
`Aggregate liability` — words in no list — was answered with a company, and so was a
label docdex had never seen. A deny-list is the wrong shape for this: it must
enumerate everything that could go wrong, and the cost of a missing entry is a
confidently wrong answer.

Both are now decided by one function that says what kind of value a field wants
(`party`, `quantity`, `date`, `identifier`, or `unknown`), and **only a field known to
want a party may be answered with a company** — from the label's own words, or from a
synonym the user declared in `aliases.json`. An unfamiliar label gets no company,
which is a miss rather than a wrong answer, and `aliases.json` is the way out.

A deny signal beats an allow signal: `Vendor turnover` contains a party word and a
quantity word, and it wants the quantity.
"""
from __future__ import annotations

import json
import re
import sqlite3

import pytest

from docdex import index_db
from docdex.context import build_packet
from docdex.scaffold import run_init
from docdex.sync import run_sync

ALIASES = {"legal name": ["vendor", "supplier", "party", "parties", "legal entity"],
           "payment terms": ["payment schedule"],
           "liability cap": ["limitation of liability"]}


def corpus(tmp_path, files: dict, aliases: dict = None):
    root = tmp_path / "types"
    root.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (root / name).write_text(text, encoding="utf-8")
    project = run_init(root, quiet=True)
    project.aliases_path.write_text(
        json.dumps(ALIASES if aliases is None else aliases), encoding="utf-8")
    run_sync(project, quiet=True)
    index_db.build(project, quiet=True)
    return project


def one_file(tmp_path, text: str, aliases: dict = None):
    return corpus(tmp_path, {"contract.txt": text}, aliases)


def _section(packet: str, name: str) -> str:
    header = {"answers": "## Answers", "weak": "## Needs follow-up"}[name]
    if header not in packet:
        return ""
    return packet.split(header, 1)[1].split("\n##", 1)[0]


def _line_for(packet: str, section: str, label: str) -> str:
    """This field's own line, matched on `- {label}:` and not merely `- {label}`.

    Found by adversarial review: `startswith("- Party")` also matches
    `- Party invoice number: 42`, so a test could validate one field against a
    different field's line.
    """
    for ln in _section(packet, section).splitlines():
        head = ln.strip()
        if head.startswith(f"- {label}:"):
            return head[len(f"- {label}:"):].strip()
    return ""


def answered(packet: str, label: str) -> str:
    """The value side of this field's line in `## Answers`, or ''.

    Only `## Answers` counts. A weak line says "matched, no clear value", and the
    source sentence is quoted again under `## Evidence`, so searching the whole packet
    for a string passes without the field ever being answered.
    """
    return _line_for(packet, "answers", label)


def exact_answer(packet: str, label: str) -> str:
    """The answered VALUE with its citation and tags stripped — for equality tests.

    Adversarial review's most repeated point about this suite: `expected in got` accepts
    a corrupted value that merely contains the right substring, so
    `Helios Components Pvt Ltd as the Vendor` and `Beta Holdings Ltd / Gamma Systems
    LLP` — neither of which is a company that exists — passed as correct answers.
    """
    return re.sub(r"\s*\[[^\]]*\](\s*~approx)?\s*$", "",
                  answered(packet, label)).strip()


def value_of(packet: str, label: str) -> str:
    for section in ("answers", "weak"):
        body = _line_for(packet, section, label)
        if body:
            return body.replace("matched, no clear value — ", "")
    return ""


def weak(packet: str, label: str) -> str:
    """The field's `## Needs follow-up` line, so "not answered" can be distinguished
    from "vanished entirely" — the latter would report present evidence as missing."""
    return _line_for(packet, "weak", label)


def fill(project, *labels: str) -> str:
    return build_packet(project, "fill the vendor form", budget=2000,
                        form_fields=list(labels))


# ================================= 1. a name written after its label is a value

@pytest.mark.parametrize("text,label,expected", [
    ("Legal name: Beta Holdings Ltd\n", "Legal name", "Beta Holdings Ltd"),
    ("Vendor: Acme Industries Pvt Ltd\n", "Legal name", "Acme Industries Pvt Ltd"),
    ("Legal entity: Gamma Systems LLP\n", "Legal name", "Gamma Systems LLP"),
    ("Supplier - Delta Components Inc\n", "Legal name", "Delta Components Inc"),
    ("Legal name: Helios Components Pvt. Ltd.\n", "Legal name",
     "Helios Components Pvt. Ltd"),
    # Every corporate form the release claims to know, not only the three that were
    # convenient to write. Found by adversarial review: a suffix registry tested by
    # four examples is four examples, not a registry.
    ("Legal name: Muster Technik GmbH\n", "Legal name", "Muster Technik GmbH"),
    ("Legal name: Northwind Systems LLC\n", "Legal name", "Northwind Systems LLC"),
    ("Legal name: Orion Group PLC\n", "Legal name", "Orion Group PLC"),
    ("Legal name: Sakura Kaisha KK\n", "Legal name", "Sakura Kaisha KK"),
    ("Legal name: Aurora Ventures Pte Ltd\n", "Legal name", "Aurora Ventures Pte Ltd"),
])
def test_a_name_after_its_label_is_answered(tmp_path, text, label, expected):
    """The value must be the company EXACTLY — a substring check accepts a corruption.

    `expected in got` would pass for "Beta Holdings Ltd shall deliver" and for
    "Beta Holdings Ltd / Gamma Systems LLP", neither of which names the company that
    was asked for.
    """
    project = one_file(tmp_path, text)
    got = exact_answer(fill(project, label), label)
    assert got == expected, (
        f"{text.strip()!r} answered {label!r} with {got!r}, not {expected!r}")


def test_a_name_beats_a_number_in_the_same_window(tmp_path):
    """The release claims this in as many words, and nothing tested it.

    Found by adversarial review. A party field prefers the company over any identifier
    that follows it in the same window; otherwise a GST number is confidently filed as
    the counterparty's legal name.
    """
    project = one_file(
        tmp_path, "Legal name: Beta Holdings Ltd, GST 29ABCDE1234F1Z5\n")
    assert exact_answer(fill(project, "Legal name"), "Legal name") == \
        "Beta Holdings Ltd"


def test_a_longer_label_that_merely_starts_with_a_synonym_is_not_this_field(tmp_path):
    """`Supplier reference:` is not `Supplier:`, and the separator is what says so.

    Found by adversarial review — the release states this rule in a code comment and
    tested it nowhere. The separator has to follow the synonym itself; here it follows
    "reference", so `Legal name` must not take the value.
    """
    project = one_file(tmp_path, "Supplier reference: Acme Industries Ltd\n")
    assert answered(fill(project, "Legal name"), "Legal name") == "", (
        "another field's value was answered as the legal name")


@pytest.mark.parametrize("text", [
    "Legal name: Beta Holdings Ltd / Gamma Systems LLP\n",
    "Legal name: Beta Holdings Ltd & Gamma Systems LLP\n",
    "Beta Holdings Ltd / Gamma Systems LLP as the Vendor.\n",
])
def test_two_companies_joined_are_not_one_company(tmp_path, text):
    """Found by adversarial review, and it was real in BOTH reading directions.

    `&` and `/` belong inside a name — "Smith & Sons Ltd", "B S R & Co. LLP" — but once
    a legal form has been stated they join two different parties, and both readers
    returned the whole run as a single company that does not exist. Two parties where
    the form asked for one is a disagreement, so neither is picked.
    """
    project = one_file(tmp_path, text)
    got = answered(fill(project, "Legal name"), "Legal name")
    assert "Gamma" not in got and "Beta" not in got, (
        f"two companies were merged into one name and asserted: {got!r}")


def test_an_ampersand_inside_one_company_still_reads(tmp_path):
    """...and the refusal above must not eat a name that legitimately contains `&`."""
    project = one_file(tmp_path, "Legal name: Smith & Sons Ltd\n")
    assert exact_answer(fill(project, "Legal name"), "Legal name") == "Smith & Sons Ltd"


def test_the_name_is_the_whole_value_and_nothing_after_it(tmp_path):
    """A value is read, not a window quoted. What follows the name is not the name."""
    project = one_file(
        tmp_path, "Legal name: Beta Holdings Ltd shall deliver the services.\n")
    got = answered(fill(project, "Legal name"), "Legal name")
    assert got.split("  [")[0].strip() == "Beta Holdings Ltd", (
        f"the value ran past the end of the name: {got!r}")


def test_a_capitalised_run_that_is_not_a_company_is_not_a_value(tmp_path):
    """The same corporate-form rule apposition uses. "Karnataka Region" is a place.

    Without it, any capitalised words after any label become that field's value, which
    is the "confidently wrong" direction this whole feature is fenced against.
    """
    project = one_file(tmp_path, "Legal name: Karnataka Region Office\n")
    assert not answered(fill(project, "Legal name"), "Legal name"), (
        "a capitalised phrase with no legal form was asserted as a company name")


def test_a_following_label_is_not_read_as_part_of_the_name(tmp_path):
    """Two fields on one line: each gets its own value, neither gets both."""
    project = one_file(
        tmp_path, "Legal name: Beta Holdings Ltd Payment terms: net-45\n")
    packet = fill(project, "Legal name", "Payment terms")
    name = answered(packet, "Legal name")
    assert "net-45" not in name, f"the next field's value entered the name: {name!r}"
    assert "net-45" in value_of(packet, "Payment terms"), (
        "the second field lost its value:\n" + packet)


def test_a_number_after_the_label_still_wins_over_a_name(tmp_path):
    """A name reading is a last resort, never a competitor to a real value.

    "Renewal term: 24 months" must stay `24 months` even in a document full of
    companies — the ordinary reading is not allowed to change.
    """
    project = one_file(
        tmp_path,
        "Renewal term: 24 months\nVendor: Acme Industries Pvt Ltd\n")
    packet = fill(project, "Renewal term")
    assert "24 months" in answered(packet, "Renewal term"), (
        "a plain value reading regressed:\n" + packet)


# ------------------------------------------------ the findability half of the same

def test_a_labelled_name_makes_its_chunk_value_bearing(tmp_path):
    """Reading it is half the feature; being reachable is the other half.

    Retrieval breaks ties toward chunks that carry a value, and every candidate for a
    common label ties. v0.5.7 learned this the hard way: the chunk holding the
    benchmark's own apposition line was not in a pool of sixty, because every chunk
    containing a digit sorted above the one chunk that could answer. A company name
    has no digits, so a labelled name has to count as a value here too — asserted
    against the stored column, not against what the code reports about itself.
    """
    project = one_file(tmp_path, "Legal name: Beta Holdings Ltd\n")
    conn = sqlite3.connect(str(project.index_db_path))
    try:
        rows = conn.execute(
            "SELECT text, has_value FROM chunks WHERE rel = 'contract.txt'"
        ).fetchall()
    finally:
        conn.close()
    assert rows, "the chunk was not indexed at all"
    assert any(r[1] for r in rows), (
        f"a chunk whose only value is a labelled company name was stored as "
        f"carrying no value: {rows}")


def test_a_labelled_name_outranks_sixty_digit_bearing_decoys(tmp_path):
    """The end-to-end shape of the same problem, on a corpus like a real one."""
    files = {"z_answer.txt": "Legal name: Beta Holdings Ltd\n"}
    for i in range(60):
        files[f"a_decoy_{i:02d}.txt"] = (
            f"Legal name is described in annexure {i} at clause 4.2.\n")
    project = corpus(tmp_path, files)
    got = answered(fill(project, "Legal name"), "Legal name")
    assert "Beta Holdings Ltd" in got, (
        f"the only chunk that could answer never reached the field: {got!r}")


# ============================== 1b. a pointer to a place is not a value at all

@pytest.mark.parametrize("text,label", [
    ("Legal name is described in annexure 4 at clause 4.2 of the agreement.\n",
     "Legal name"),
    ("Payment terms are set out in clause 7.3 of the annexure.\n", "Payment terms"),
    ("Liability cap: refer to section 9 for details.\n", "Liability cap"),
])
def test_a_cross_reference_is_not_a_field_value(tmp_path, text, label):
    """"See clause 7.3" tells you where to look. It is not the answer.

    docdex already knows this: `carries_value` refuses a bare number introduced by
    "clause", "annexure", "section" — that rule exists precisely so the ranking
    tie-break is not spent on a chunk that only points elsewhere. The answer path never
    applied it, so all three of these were presented under `## Answers`, with `4`, `7.3`
    and `9` as the values. Weak is the honest section for them.

    Third time this shape has appeared: one question ("does this text carry a value?")
    answered by more than one rule. v0.5.6 found three rules for aliases; this release
    found `_pick_field_hit` scanning for numbers while the reading looked for names.
    """
    project = one_file(tmp_path, text)
    packet = fill(project, label)
    got = answered(packet, label)
    assert not got, f"a document cross-reference was answered as {label!r}: {got!r}"
    # ...and it must still be SHOWN. Reporting present text as missing is the other
    # half of the same rule, and "not answered" alone would allow it. Found by
    # adversarial review.
    assert weak(packet, label), (
        f"{label!r} vanished from the packet instead of being shown as weak:\n"
        + packet)


def test_a_real_value_beside_a_cross_reference_is_still_found(tmp_path):
    """Refusing pointers must not refuse the value standing next to one."""
    project = one_file(
        tmp_path,
        "Liability cap: as described in clause 9, capped at INR 6.5 crore.\n")
    got = answered(fill(project, "Liability cap"), "Liability cap")
    # The literal amount, currency and scale word — `"6.5" in got` would also accept a
    # normalised `6.50` or a bare `6.5`, and this project's hard rule is that amounts
    # stay literal. Found by adversarial review.
    assert "INR 6.5 crore" in got, (
        f"a real amount was lost or altered beside a cross-reference: {got!r}")


# ============ 1c. what the real corpus said, reconstructed in its shape ---------
#
# Both lines below reconstruct the shape of real corpus lines. Running this release over 104,168
# real chunks changed 20 field answers, and every one of them was newly WRONG — no
# fixture and neither review pass produced a single one of them.

def test_a_number_further_down_the_sentence_is_not_the_date(tmp_path):
    """The shape of a signed NDA's preamble. "2nd Floor" is not an effective date.

    Keeping `Pvt. Ltd.` inside one clause made clauses longer, and a longer clause lets
    the value window run past the label until it finds *some* number. Here the window
    after "Effective Date" reached "Meridian House, 2nd Floor" and answered `2`.

    So a full stop after a name is still a boundary for a value window: the window may
    cross one only when what follows continues the NAME (`Pvt.` → `Ltd.`), not merely
    because the sentence continues.
    """
    project = one_file(
        tmp_path,
        "This Confidentiality Agreement is made this 24th day of April, 2025 "
        "( Effective Date ) by and between Helios Components Pvt. Ltd. with offices at "
        "Meridian House, 2nd Floor, North Wing, 14 Park Street, Pune.\n")
    got = answered(fill(project, "Effective date"), "Effective date")
    assert "2nd" not in got and got.strip() not in ("2", "2,"), (
        f"a floor number was answered as the effective date: {got!r}")


def test_a_transaction_id_is_not_a_legal_name(tmp_path):
    """The shape of an exported ledger row. A company is not a 19-digit number.

    `vendor_payment` tokenises to `vendor`, which is a declared synonym of `Legal name`,
    so the window after it reached the next column and answered a transaction ID as the
    counterparty's legal name.

    The registry this release added is what makes the fix one line rather than a
    heuristic: a field that wants a **party** is answered by a name or not at all.
    """
    project = one_file(
        tmp_path,
        "2026-06-06,Vendor Advances,Kestrel India Pvt. Ltd.,8461920000075310642,"
        ",,,vendor_payment,FCM-260605NBARC1,26-27D3023137,20513.00\n")
    got = answered(fill(project, "Legal name"), "Legal name")
    assert "8461920000075310642" not in got, (
        f"a transaction ID was answered as the legal name: {got!r}")


@pytest.mark.parametrize("text", [
    # A number sitting exactly where the value goes. Every release before this one
    # answered `Legal name: 998877` here, which is the general form of the transaction-ID
    # case above and not a regression this release introduced.
    "Vendor: 998877\n",
    "Legal entity: 4521\n",
    "The Vendor shall perform the services. Invoice reference 998877.\n",
])
def test_a_party_field_is_never_answered_with_a_number(tmp_path, text):
    """The general rule behind the case above, stated once.

    A company name is not a number, in any document. Before this, any digits inside a
    party field's window could become its value — the same defect class as
    `test_a_cross_reference_is_not_a_field_value`, one field-kind along.
    """
    project = one_file(tmp_path, text)
    got = answered(fill(project, "Legal name"), "Legal name")
    assert not re.search(r"\d", got), (
        f"a number was answered as a company's legal name: {got!r}")


# ==================================== 2. only a party field may get a company

@pytest.mark.parametrize("label", [
    # None of these contain a word from v0.5.7's forty-word deny-list, so every one
    # of them was answered with a company.
    "Aggregate liability",
    "Consideration payable",
    "Security deposit",
    "Royalty",
    "Indemnity",
])
@pytest.mark.parametrize("shape", ["backward", "forward"])
def test_a_field_that_does_not_want_a_party_is_not_given_a_company(tmp_path, label,
                                                                   shape):
    """The deny-list's cost, made concrete — in BOTH reading directions.

    `Helios Components Pvt Ltd as the aggregate liability` is a sentence a real
    contract can produce, and answering a liability field with a company is the
    failure mode this feature is most likely to have.

    The forward shape is here because adversarial review pointed out that every
    non-party fixture used backward apposition, so deleting the field-kind test from the
    forward branch alone would have left the whole set green while
    `Aggregate liability: Helios Components Pvt Ltd` answered with the company.

    Asserted as "no answer at all", not as "the word Helios is absent": a reader that
    dropped the first word and returned `Components Pvt Ltd` would satisfy the second.
    """
    text = (f"Helios Components Pvt Ltd as the {label.lower()}.\n"
            if shape == "backward" else f"{label}: Helios Components Pvt Ltd\n")
    project = one_file(tmp_path, text)
    assert answered(fill(project, label), label) == "", (
        f"{label!r} ({shape}) was answered with a company name")


def test_an_unfamiliar_label_is_not_given_a_company(tmp_path):
    """A label docdex has never seen is not evidence that it wants a party.

    Under a deny-list every unknown label was allowed one; under a registry it is
    refused until the user declares what it means. A miss an agent can act on beats a
    wrong answer it cannot.
    """
    project = one_file(tmp_path, "Helios Components Pvt Ltd as the Sprocket.\n")
    assert answered(fill(project, "Sprocket"), "Sprocket") == "", (
        "an unrecognised field was answered with a company name")


def test_a_quantity_word_beats_a_party_word_in_the_same_label(tmp_path):
    """`Vendor turnover` names a party and wants a number. The number wins."""
    project = one_file(
        tmp_path, "Helios Components Pvt Ltd as the vendor turnover.\n")
    assert "Helios" not in answered(fill(project, "Vendor turnover"),
                                    "Vendor turnover"), (
        "a field naming a party but asking for a quantity was given the party")


@pytest.mark.parametrize("label", [
    "Legal name", "Counterparty", "Licensee", "Service provider", "Contractor",
])
def test_a_field_that_does_want_a_party_still_gets_one(tmp_path, label):
    """The allow-list has to be wide enough to be useful."""
    project = one_file(
        tmp_path, f"Helios Components Pvt Ltd as the {label.lower()}.\n")
    assert "Helios" in value_of(fill(project, label), label), (
        f"{label!r} is a party field and was not answered with the company")


def test_a_declared_synonym_makes_an_unfamiliar_field_a_party_field(tmp_path):
    """`aliases.json` is the way out of an unrecognised label.

    The label must be one docdex does NOT already know, or the test proves nothing:
    adversarial review caught the first version using `Manufacturer`, which is in the
    party vocabulary already, so deleting the whole alias branch left it green. `Maker`
    is in no list, and both halves are asserted — refused without the declaration,
    answered with it. The registry-level statement of the same rule is
    `test_the_kind_of_a_declared_synonym_comes_from_its_group`.
    """
    aliases = {"legal name": ["maker"]}
    plain = one_file(tmp_path / "a", "Helios Components Pvt Ltd as the Maker.\n",
                     {"payment terms": ["payment schedule"]})
    assert answered(fill(plain, "Maker"), "Maker") == "", (
        "an undeclared label was given a company")

    declared = one_file(tmp_path / "b",
                        "Helios Components Pvt Ltd as the Maker.\n", aliases)
    assert "Helios" in value_of(fill(declared, "Maker"), "Maker"), (
        "a field declared a synonym of a party field was refused a company")


def test_a_synonym_of_a_quantity_field_stays_a_quantity_field(tmp_path):
    """Declaring a synonym must not smuggle a company into a quantity field.

    The synonym contains no word docdex recognises on its own — adversarial review
    caught `exposure ceiling`, where `ceiling` is directly a quantity word, so the test
    passed with alias handling deleted entirely. `Headroom bucket` is classified only
    through the group it was declared in.

    Stated as behaviour, with no import of anything this release introduced: a test that
    reaches for a new symbol *errors* against the previous release instead of failing an
    assertion, and the release gate rightly refuses that as evidence. The registry-level
    view of the same rule is the test below.
    """
    aliases = {"liability cap": ["headroom bucket"]}
    project = one_file(
        tmp_path, "Helios Components Pvt Ltd as the headroom bucket.\n", aliases)
    assert answered(fill(project, "Headroom bucket"), "Headroom bucket") == "", (
        "a synonym of a quantity field was answered with a company")


def test_the_kind_of_a_declared_synonym_comes_from_its_group():
    """The registry's own view of the test above — and of the party case.

    Both fixtures use labels no list recognises, which is the point: adversarial review
    found the first versions using `Manufacturer` (already a party word) and `Exposure
    ceiling` (`ceiling` is already a quantity word), so each passed with the entire alias
    branch deleted.
    """
    from docdex.context import field_kind

    assert field_kind("Maker") == "unknown", "the fixture picked a known label"
    assert field_kind("Maker", [["legal name", "maker"]]) == "party"

    assert field_kind("Headroom bucket") == "unknown"
    assert field_kind("Headroom bucket",
                      [["liability cap", "headroom bucket"]]) == "quantity"


# ---------------------------------------------------------- the registry itself

def test_a_party_field_named_with_a_money_word_is_still_a_party_field(tmp_path):
    """`Tax Entity` wants a company. "tax" appearing in it does not change that.

    Found by adversarial review. The first rule was "any quantity word anywhere wins",
    which reads the label as a bag of words; `Tax Entity`, `Invoice party` and `Billing
    entity` were all classified as quantities and answered "matched, no clear value"
    with the company sitting right there.
    """
    project = one_file(tmp_path, "Tax Entity: Beta Holdings Ltd\n")
    assert "Beta Holdings Ltd" in answered(fill(project, "Tax Entity"), "Tax Entity"), (
        "a party field was misread as wanting a quantity")


def test_the_head_word_of_a_label_decides_its_kind():
    """English compounds put the head noun last, so the last recognised word decides.

    Except when a preposition inverts the order — "Fees payable to the vendor" is about
    fees — and then the kind that refuses a party wins, which is the safe direction.
    """
    from docdex.context import field_kind

    assert field_kind("Tax Entity") == "party"
    assert field_kind("Vendor turnover") == "quantity"
    assert field_kind("Invoice party") == "party"
    assert field_kind("Party invoice number") == "identifier"
    assert field_kind("Fees payable to the vendor") == "quantity"
    assert field_kind("Limitation of liability") == "quantity"


def test_the_registry_names_the_kind_each_field_wants():
    """One place answers "what kind of value does this field want".

    Imported inside the test body on purpose: a module-level import of something this
    release introduces makes the whole file fail to *collect* against the previous
    release, which turns every discrimination check in the gate into a false pass.
    """
    from docdex.context import field_kind

    assert field_kind("Legal name") == "party"
    assert field_kind("Liability cap") == "quantity"
    assert field_kind("Effective date") == "date"
    assert field_kind("GST number") == "identifier"
    assert field_kind("Sprocket") == "unknown"
    # A deny signal wins over an allow signal in the same label.
    assert field_kind("Vendor turnover") == "quantity"
