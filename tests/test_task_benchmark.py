"""Smoke test for the form-filling benchmark and its core honesty guarantee."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BENCH = Path(__file__).resolve().parents[1] / "benchmarks"
sys.path.insert(0, str(BENCH))

task_benchmark = pytest.importorskip("task_benchmark")

from docdex import context as ctxmod  # noqa: E402
from docdex import index_db  # noqa: E402
from docdex.scaffold import run_init  # noqa: E402
from docdex.sync import run_sync  # noqa: E402


def test_packet_never_fabricates_absent_field(tmp_path):
    root = tmp_path / "corpus"
    gt = task_benchmark.build_corpus(root)
    project = run_init(root, quiet=True)
    run_sync(project, quiet=True)
    index_db.build(project, quiet=True)

    packet = ctxmod.build_packet(
        project, "fill the vendor onboarding form", budget=3000,
        form_fields=task_benchmark.FORM_FIELDS)

    # The honesty guarantee: a field with no evidence is reported "not found",
    # never invented — and reported in the MISSING section, on its own line. Found by
    # adversarial review: a whole-packet substring check also passes when the line has
    # moved into Evidence or a footer, where an agent reading the field list would
    # never see it.
    missing = packet.split("## Missing", 1)[1].split("\n##", 1)[0] \
        if "## Missing" in packet else ""
    for absent in task_benchmark.ABSENT:
        assert any(ln.strip().startswith(f"- {absent}: not found")
                   for ln in missing.splitlines()), (
            f"{absent!r} is not reported absent on its own line under ## Missing:\n"
            + packet)

    # And it must actually deliver real context cheaply: a majority of the
    # answerable fields, well under the cost of reading everything.
    covered = task_benchmark.covered(packet, gt)
    assert len(covered) >= len(task_benchmark.FINDABLE) // 2
    from docdex import tokens as tok
    assert tok.count_tokens(packet) < 4000


def test_packet_hash_ignores_only_the_index_timestamp():
    """The determinism hash must survive a minute boundary but nothing else.

    The packet header carries a wall-clock "Index: indexed ..." line. Hashing it raw
    made `qa_release.py`'s determinism gate pass only when its runs happened to land
    inside the same minute, and cry nondeterminism otherwise.
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmarks"))
    from task_benchmark import canonical_packet

    a = ("# context packet\nIndex: indexed 2026-07-29 23:55 — not re-checked\n"
         "- Payment terms: net-45  [a.txt ·0]\n")
    b = a.replace("23:55", "23:56")
    assert canonical_packet(a) == canonical_packet(b), \
        "hash still moves with the clock"

    # But a real change — different citation — must still change the hash.
    c = a.replace("[a.txt ·0]", "[b.txt ·3]")
    assert canonical_packet(a) != canonical_packet(c), \
        "normalisation is too aggressive; it hid a citation change"


def test_attribution_reads_the_source_of_an_approximate_answer():
    """The `~approx` tag comes AFTER the citation, and the parser anchored at the end.

    So every approximate answer recorded `source: ""` — including `Legal name`, the
    field v0.5.7 was built for — and `qa_release.py`'s per-field source comparison
    silently skipped them, because it only compares when both sides have a source. Nine
    fields of eleven were being checked while the gate reported checking all of them.

    Note this test cannot fail against the previous release inside the gate: gate 2
    overlays today's `benchmarks/` onto the base worktree before gate 3 runs, so the base
    tree is already running this parser. It is declared with a `-` in FIXED_BY.tsv for
    exactly that reason.
    """
    from task_benchmark import field_attribution

    packet = (
        "## Answers\n"
        "- Legal name: Helios Components Pvt Ltd  [Contracts/a.docx ·3]  ~approx\n"
        "- Liability cap: INR 6.5 crore  [Misc/b.pdf ·3]\n"
        "\n## Needs follow-up (weak)\n"
        "- Governing law: matched, no clear value — Karnataka  [c.docx ·9]  ~approx\n"
        "\n## Missing\n"
        "- Bank IFSC: not found\n")
    got = field_attribution(packet, ["Legal name", "Liability cap", "Governing law",
                                     "Bank IFSC"])
    assert got["Legal name"] == {"section": "answers",
                                 "source": "Contracts/a.docx ·3", "approx": True}
    assert got["Liability cap"] == {"section": "answers",
                                    "source": "Misc/b.pdf ·3", "approx": False}
    assert got["Governing law"] == {"section": "weak",
                                    "source": "c.docx ·9", "approx": True}
    # A field with no citation at all still records its section, not a phantom source.
    assert got["Bank IFSC"]["section"] == "missing"
    assert got["Bank IFSC"]["source"] == ""
