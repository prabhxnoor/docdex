"""v0.5.5 — `doctor` must describe the corpus accurately, including its own decisions.

Found while checking the real 10.5k-file corpus end to end. `cache coverage` reported
`failed=10 missing=19` and FAILED — but all 19 "missing" files were ones `sync`
deliberately declined to extract because they exceed the 50 MB cap. `sync` is explicit
that this is policy, not a gap (`sync.py`: *"intentionally not extracted (too large) —
not a gap"*), yet `check_cache_coverage` has no branch for `skipped`, so every one of
them fell through to `missing` and turned a healthy corpus into a red FAIL.

Two costs, and the second is the one that matters. A check that cries wolf gets ignored
— and while those 19 sat in `missing`, that number could not tell anyone whether a
*genuine* cache gap had appeared alongside them.

The remaining `failed=10` are real, and none of them is docdex's fault: six files are
truncated on disk (no zip end-of-central-directory, no PDF `%%EOF`) and four are
password-protected. But what docdex *said* about them was wrong in a way that cost
diagnosis time — a present-but-damaged file was reported as `PackageNotFoundError:
Package not found at '<path>'`, which reads as "this file is missing", and an encrypted
file with no configured password was reported as `PDFPasswordIncorrect:` with an empty
detail, which reads as "docdex tried a password and got it wrong".

Same family as the release's main fix: the product must not say untrue or unactionable
things about its own state. Helpers stay local and nothing this release introduces is
imported by name, so these fail as assertions against the base tree rather than erroring.
"""
from __future__ import annotations

import json

import pytest

from conftest import make_docx, make_encrypted_pdf, make_pdf
from docdex.doctor import run_doctor
from docdex.scaffold import run_init
from docdex.sync import run_sync


def set_size_cap(project, mb):
    """Lower the extract cap for this project, the way a user would."""
    path = project.root / ".docdex" / "config.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    config["max_extract_mb"] = mb
    path.write_text(json.dumps(config), encoding="utf-8")
    project.config.update(config)


def coverage_line(captured):
    for line in captured.splitlines():
        if "cache coverage" in line:
            return line
    raise AssertionError(f"doctor printed no cache-coverage line:\n{captured}")


def counters(line):
    """Pull `name=number` pairs out of a doctor line."""
    out = {}
    for token in line.replace(",", " ").split():
        if "=" in token:
            key, _, value = token.partition("=")
            if value.isdigit():
                out[key] = int(value)
    return out


def status_detail(project, rel):
    """What `sync` recorded about one file, read back the way `doctor` reads it."""
    from docdex.inventory import read_extract_status
    return (read_extract_status(project).get(rel) or {}).get("detail", "")


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "small.txt").write_text("Payment terms are Net-45.\n", encoding="utf-8")
    p = run_init(root, quiet=True)
    run_sync(p, quiet=True)
    return p


# ------------------- a deliberate skip is not a missing cache --------------------


def add_an_over_cap_file(project, name="huge.txt"):
    """Create a file the cap excludes, and prove the state before anything is asserted
    about it. Review pointed out that a test which never checks this could pass because
    the file was dropped from the inventory altogether — a present, unsearchable
    document silently vanishing from docdex's account of the corpus."""
    from docdex.inventory import read_extract_status, read_inventory

    set_size_cap(project, 1)
    (project.root / name).write_text("Huge marker HHH-555. " + "x" * (2 * 1024 * 1024),
                                     encoding="utf-8")
    run_sync(project, quiet=True)

    assert name in read_inventory(project.inventory_path), (
        f"{name} is not in the inventory at all, so it is not merely skipped")
    assert (read_extract_status(project).get(name) or {}).get("status") == "skipped"
    assert not project.cache_path_for(name).exists(), "expected no cache for it"


def test_a_file_skipped_for_size_is_not_reported_as_a_missing_cache(project, capsys):
    """The real-corpus false alarm, at its own scale: one file over the cap."""
    add_an_over_cap_file(project)

    code = run_doctor(project, no_sha=True)
    line = coverage_line(capsys.readouterr().out)

    assert "missing=0" in line, f"a file skipped by policy is counted as missing: {line}"
    assert code == 0, f"doctor failed over a deliberate skip:\n{line}"
    # Every supported file lands in exactly one bucket. Stronger than a hard-coded
    # count, and it is what stops a file being quietly dropped from the account
    # rather than reclassified — the failure mode review raised for this test.
    n = counters(line)
    assert n["ok"] + n["no-text"] + n["skipped"] + n["failed"] + n["missing"] \
        == n["supported"], f"the counters do not account for every file: {line}"


def test_a_deliberate_skip_does_not_hide_a_real_gap_beside_it(project, capsys):
    """The case the release is actually about, and the one every isolated fixture
    misses. Review's mutation: `healthy = failed == 0 and (missing == 0 or skipped > 0)`
    passes every other test in this file while a corpus with one policy skip AND one
    deleted cache reports healthy. Both counters must stay independent."""
    add_an_over_cap_file(project)
    project.cache_path_for("small.txt").unlink()

    code = run_doctor(project, no_sha=True)
    line = coverage_line(capsys.readouterr().out)

    assert "skipped=1" in line and "missing=1" in line, (
        f"a skip and a real gap are not reported independently: {line}")
    assert code != 0, f"a genuine gap was hidden behind a deliberate skip:\n{line}"


def test_the_skipped_files_are_still_reported_not_hidden(project, capsys):
    """Fixing a false alarm by deleting the information would be worse than the alarm.
    The user still needs to know some documents are not searchable, and why."""
    add_an_over_cap_file(project)

    run_doctor(project, no_sha=True)
    line = coverage_line(capsys.readouterr().out)

    # Exact equality, not a substring: `"skipped=1" in line` also accepts `skipped=10`,
    # so a miscounted total would pass. Review's mutation, and a fair one.
    assert counters(line)["skipped"] == 1, (
        f"the skipped file is misreported or gone from the report: {line}")

    (project.root / "second-huge.txt").write_text("y" * (2 * 1024 * 1024),
                                                  encoding="utf-8")
    run_sync(project, quiet=True)
    run_doctor(project, no_sha=True)
    line = coverage_line(capsys.readouterr().out)
    assert counters(line)["skipped"] == 2, f"the count does not track reality: {line}"


@pytest.mark.parametrize("also_forget_the_status", [False, True])
def test_a_genuinely_missing_cache_still_fails(project, capsys, also_forget_the_status):
    """The guard against overcorrecting: this check must not become a rubber stamp.

    Parameterised after review. With the status row left in place the file is `ok` with
    no cache; with it removed, nothing records the file at all — which is what a crash
    between extraction and the status write leaves behind. Both are real gaps and both
    must be red, or a product that treated "untracked" as "not my problem" would pass.
    """
    project.cache_path_for("small.txt").unlink()
    if also_forget_the_status:
        project.extract_status_path.unlink()

    code = run_doctor(project, no_sha=True)
    line = coverage_line(capsys.readouterr().out)

    assert "missing=1" in line, line
    assert code != 0, f"doctor passed with a genuinely missing cache:\n{line}"
    assert "backfill" in line, f"a red check with no next step: {line}"


@pytest.mark.parametrize("name,payload", [
    ("broken.docx", b"PK\x03\x04 truncated, no directory"),
    ("broken.pdf", b"%PDF-1.7\ntruncated before any xref"),
    ("broken.xlsx", b"PK\x03\x04 also not a workbook"),
])
def test_a_failed_extraction_still_fails(project, capsys, name, payload):
    """Parameterised across file types after review: a product that counted failures
    only for one extension would pass a single-`.docx` test while an unreadable PDF
    slipped through as healthy."""
    (project.root / name).write_bytes(payload)
    run_sync(project, quiet=True)

    code = run_doctor(project, no_sha=True)
    line = coverage_line(capsys.readouterr().out)

    assert "failed=1" in line, line
    assert code != 0, f"doctor passed with a file it could not read:\n{line}"


# ----------------- and it has to say what is actually wrong ---------------------


@pytest.mark.parametrize("name", ["cut.docx", "cut.pdf"])
def test_a_damaged_file_is_not_described_as_missing(project, tmp_path, name):
    """A truncated .docx reported `Package not found at '<abs path>'` — the file is
    right there. Six files on the real corpus said this, and it sent me looking for a
    path problem instead of a damaged document.

    Parameterised across both damaged-file paths after review: the release claims a
    truncated PDF is diagnosed too (`PSEOF`), and only the `.docx` branch was tested,
    so removing the PDF translation would have gone unnoticed.
    """
    if name.endswith(".docx"):
        good = tmp_path / "whole.docx"
        make_docx(good, "Governing law is the laws of Karnataka.")
    else:
        good = tmp_path / "whole.pdf"
        good.write_bytes(make_pdf("Governing law is the laws of Karnataka."))
    payload = good.read_bytes()
    (project.root / name).write_bytes(payload[:len(payload) * 2 // 3])
    run_sync(project, quiet=True)

    detail = status_detail(project, name).lower()

    assert "not found" not in detail, (
        f"a file that exists is described as not found: {detail!r}")
    assert any(word in detail for word in ("damaged", "truncated", "incomplete")), (
        f"the detail does not say the file is damaged: {detail!r}")


def test_an_encrypted_file_says_how_to_supply_the_password(project):
    """`PDFPasswordIncorrect:` with an empty detail reads as "docdex guessed a password
    and got it wrong". No password was configured at all, and the fix — a key in
    `.docdex/secrets.json` — is something the message should name."""
    (project.root / "locked.pdf").write_bytes(
        make_encrypted_pdf("LOCKEDSTMT balance due 1234", "hunter2"))
    run_sync(project, quiet=True)

    detail = status_detail(project, "locked.pdf").lower()

    assert "encrypted" in detail or "password-protected" in detail, (
        f"the detail does not say the file is encrypted: {detail!r}")
    # The full path, not just the filename: review's mutation was `~/secrets.json`,
    # which satisfies a bare "secrets.json" check while naming a location docdex
    # never reads. `test_following_the_password_advice_...` then proves it works.
    assert ".docdex/secrets.json" in detail, (
        f"the detail does not name the location docdex actually reads: {detail!r}")


@pytest.mark.parametrize("name", ["protected.docx", "protected.pptx", "protected.xlsx"])
def test_an_encrypted_office_file_is_not_told_to_use_secrets_json(project, name):
    """From external review of this release, sharpened by measurement.

    A password-protected .docx/.pptx/.xlsx is written as an OLE2/CDFV2 container, so it
    raises the same "not a package" error as a truncated file — and `secrets.json`
    passwords reach PDFs only (see `extract.extract`), so offering one here would be
    advice that cannot work. The same branch also catches a legacy binary document
    misnamed `.docx`, so the message has to name the container it actually found rather
    than assert which of the two it is.
    """
    ole2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"legacy or encrypted body" * 8
    (project.root / name).write_bytes(ole2)
    run_sync(project, quiet=True)

    detail = status_detail(project, name).lower()

    # No mention of secrets.json in ANY wording, not merely the one phrasing I wrote.
    # Review's mutation offered the password "through .docdex/secrets.json, or re-save
    # it" — which dodges a forbidden-phrase check while still sending the user to a file
    # docdex cannot use for this format. `.xlsx` is here because openpyxl raises
    # `BadZipFile` rather than `PackageNotFoundError`, and that path was untranslated:
    # a real encrypted workbook reached the user as "BadZipFile: File is not a zip file".
    assert "secrets.json" not in detail, (
        f"points at a password file docdex cannot use for an Office file: {detail!r}")
    assert "ole2" in detail or "cdfv2" in detail, (
        f"does not name the container found, so the reader cannot tell an encrypted "
        f"file from a mislabelled legacy one: {detail!r}")
    assert "re-save" in detail, f"names no remedy: {detail!r}"


def test_a_wrong_password_still_reads_as_a_wrong_password(project):
    """The counterpart guard: when a password IS configured and fails, the message must
    not claim none was supplied."""
    (project.root / "locked.pdf").write_bytes(
        make_encrypted_pdf("LOCKEDSTMT balance due 1234", "hunter2"))
    (project.root / ".docdex" / "secrets.json").write_text(
        json.dumps({"locked.pdf": "wrong-one"}), encoding="utf-8")
    run_sync(project, quiet=True)

    detail = status_detail(project, "locked.pdf").lower()

    # Positive wording, not the absence of a phrase. Review's mutation reverted this to
    # a bare `pdfpasswordincorrect:` — which lowercased contains "password" and does not
    # contain "no password", so both of the assertions I had written passed while the
    # empty, unactionable message was back.
    assert "did not work" in detail, (
        f"does not say the configured password was tried and rejected: {detail!r}")
    assert ".docdex/secrets.json" in detail, (
        f"does not point at the file holding the password that failed: {detail!r}")
    assert "no password configured" not in detail, (
        f"a configured-but-wrong password is reported as no password at all: {detail!r}")


def test_following_the_password_advice_makes_the_document_searchable(project):
    """The release's own standard, applied to its own message. This file asserts what
    several messages *say*; review's sharpest point was that none of them proved the
    advice works. So: hit the failure, do exactly what the message says — put a
    path-keyed password in `.docdex/secrets.json` — re-sync, and require the text to be
    extracted and findable. Without this, `describe_failure` could name a remedy docdex
    had quietly stopped honouring and every other test here would stay green.
    """
    from docdex import index_db

    (project.root / "locked.pdf").write_bytes(
        make_encrypted_pdf("LOCKEDSTMT balance due 1234", "hunter2"))
    run_sync(project, quiet=True)
    detail = status_detail(project, "locked.pdf")
    assert "secrets.json" in detail, detail
    assert not project.cache_path_for("locked.pdf").exists()

    (project.root / ".docdex" / "secrets.json").write_text(
        json.dumps({"locked.pdf": "hunter2"}), encoding="utf-8")
    run_sync(project, quiet=True)
    index_db.build(project, quiet=True)

    assert (status_detail(project, "locked.pdf") == ""
            or "encrypted" not in status_detail(project, "locked.pdf").lower()), (
        f"still reported as locked after the advised password was supplied: "
        f"{status_detail(project, 'locked.pdf')!r}")
    cache = project.cache_path_for("locked.pdf")
    assert cache.exists() and cache.stat().st_size > 0, "no text was extracted"
    assert "LOCKEDSTMT" in cache.read_text(encoding="utf-8", errors="replace")
    assert index_db.search(project, "LOCKEDSTMT", limit=5), (
        "the unlocked document's text is not searchable")
