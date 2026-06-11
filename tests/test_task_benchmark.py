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
    # never invented.
    for absent in task_benchmark.ABSENT:
        assert f"{absent}: not found" in packet

    # And it must actually deliver real context cheaply: a majority of the
    # answerable fields, well under the cost of reading everything.
    covered = task_benchmark.covered(packet, gt)
    assert len(covered) >= len(task_benchmark.FINDABLE) // 2
    from docdex import tokens as tok
    assert tok.count_tokens(packet) < 4000
