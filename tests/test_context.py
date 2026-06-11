"""Tests for `docdex context` evidence packets (Workstream C)."""
from __future__ import annotations

import pytest

from docdex import context as ctxmod
from docdex import index_db
from docdex.scaffold import run_init
from docdex.sync import run_sync


@pytest.fixture
def vendor(tmp_path):
    root = tmp_path / "vendor"
    (root / "Contracts").mkdir(parents=True)
    (root / "Finance").mkdir(parents=True)
    (root / "Contracts" / "scan_0231 copy.md").write_text(
        "Master services agreement with Meridian Systems Pvt Ltd (the Vendor).\n"
        "The aggregate liability cap under this agreement is INR 4.2 crore.\n"
        "Payment terms are net-45 from invoice date.\n", encoding="utf-8")
    (root / "Finance" / "untitled (8).md").write_text(
        "Vendor registration sheet.\nGST number: 29ABCDE1234F1Z5.\n"
        "Registered address: Tower B, Bengaluru 560001.\n", encoding="utf-8")
    (root / "Finance" / "distractor.md").write_text(
        "travel policy and headcount forecasts and budget review notes\n",
        encoding="utf-8")
    project = run_init(root, quiet=True)
    run_sync(project, quiet=True)
    index_db.build(project, quiet=True)
    return project


def test_task_packet_surfaces_cited_answer(vendor):
    packet = ctxmod.build_packet(vendor, "liability cap with Meridian", budget=1500)
    assert "# context packet" in packet
    assert "INR 4.2 crore" in packet
    assert "Contracts/scan_0231 copy.md" in packet
    assert "## Likely answers" in packet and "## Evidence" in packet


def test_packet_excludes_scaffold_readmes(vendor):
    packet = ctxmod.build_packet(vendor, "liability cap Meridian payment", budget=2000)
    assert "Update/README.md" not in packet
    assert "vision_notes/README.md" not in packet


def test_packet_respects_budget(vendor):
    small = ctxmod.build_packet(vendor, "vendor agreement liability payment terms", budget=120)
    # "Used: ~N" must not blow far past the budget
    used_line = [l for l in small.splitlines() if l.startswith("Budget:")][0]
    used = int(used_line.split("Used: ~")[1].split()[0].rstrip("(≈chars/4) "))
    assert used <= 220  # budget + small reserve


def test_form_mode_fills_found_and_flags_missing(vendor):
    fields = ["GST number", "Liability cap", "Bank IFSC"]
    packet = ctxmod.build_packet(vendor, "fill vendor form", budget=2000,
                                 form_fields=fields)
    assert "29ABCDE1234F1Z5" in packet           # GST found
    assert "INR 4.2 crore" in packet             # liability found
    assert "Bank IFSC: — not found" in packet    # genuinely absent
    assert "## Missing" in packet and "Bank IFSC" in packet


def test_parse_form_fields():
    text = ("Vendor Onboarding Form\nLegal name: ____\nGST number: ____\n"
            "Liability cap ______\n\nNotes go here\n")
    fields = ctxmod.parse_form_fields(text)
    assert "Legal name" in fields
    assert "GST number" in fields
    assert "Liability cap" in fields


def test_empty_task_raises(vendor):
    with pytest.raises(ctxmod.EmptyTask):
        ctxmod.build_packet(vendor, "!!! ???")


def test_explain_section(vendor):
    packet = ctxmod.build_packet(vendor, "liability cap", budget=1000, explain=True)
    assert "## Explain" in packet
    assert "engine:" in packet and "FTS5" in packet


def test_cli_context_end_to_end(vendor):
    import os
    import subprocess
    import sys
    from pathlib import Path

    src = str(Path(__file__).resolve().parents[1] / "src")
    env = dict(os.environ, PYTHONPATH=src)
    r = subprocess.run(
        [sys.executable, "-m", "docdex", "context", "liability cap Meridian",
         "--budget", "1000"],
        cwd=str(vendor.root), env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "INR 4.2 crore" in r.stdout

    r2 = subprocess.run(
        [sys.executable, "-m", "docdex", "context", "anything"],
        cwd=str(vendor.root.parent), env=env, capture_output=True, text=True)
    assert r2.returncode != 0  # outside any project
