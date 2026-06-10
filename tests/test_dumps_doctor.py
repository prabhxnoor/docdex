from __future__ import annotations

from docdex.doctor import run_doctor
from docdex.dumps import build_dumps
from docdex.sync import run_sync


def test_dumps_per_top_folder(synced):
    (synced.update_dir / "inbox doc.md").write_text("inbox ZEBRAINBOX content",
                                                    encoding="utf-8")
    run_sync(synced, quiet=True)
    build_dumps(synced, quiet=True)

    reports = synced.dumps_dir / "CONTEXT_Reports.txt"
    assert "FILE: Reports/Q1 report.md" in reports.read_text(encoding="utf-8")
    assert "ZEPHYRTOKEN" in reports.read_text(encoding="utf-8")
    update = synced.dumps_dir / "CONTEXT_Update.txt"
    assert "ZEBRAINBOX" in update.read_text(encoding="utf-8")
    assert (synced.dumps_dir / "_manifest.tsv").exists()


def test_dumps_split_parts(synced):
    build_dumps(synced, folder="Reports", max_bytes=500, quiet=True)
    parts = sorted(synced.dumps_dir.glob("CONTEXT_Reports*"))
    assert len(parts) >= 2


def test_doctor_green_on_healthy_project(synced, capsys):
    assert run_doctor(synced, no_sha=True) == 0
    out = capsys.readouterr().out
    assert "FAIL" not in out


def test_doctor_e2e_sentinel(synced, capsys):
    assert run_doctor(synced, no_sha=True, e2e=True) == 0
    out = capsys.readouterr().out
    assert "e2e sentinel" in out and "FAIL" not in out
