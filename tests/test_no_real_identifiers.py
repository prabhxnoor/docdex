"""No real party, company or document-set identifier may re-enter this repository.

This repository is public. It once carried, in its test suite, a counterparty's name
inside a verbatim confidentiality clause that forbade disclosing that name, a real
grant receipt, a supplier ledger row with its reference number, an order ID, and a
company's CIN and registered office. Those fixtures existed for a good reason — each
was a real line that broke the extractor, and no synthetic fixture had produced them —
so they were replaced with invented values of the SAME SHAPE rather than deleted, and
the regressions still bite.

This file stops it happening again. Three mechanisms, because each covers a different
way a value gets back in:

* **The watchlist, for names.** A name cannot be recognised by shape. The real values
  live in `.sanitisation-watchlist`, which is gitignored and never published. An
  earlier version published SHA-256 hashes of them instead; that was wrong. Unsalted
  hashes of one-word values — a surname, a city, a funding acronym — fall to a
  wordlist, so the hashes re-disclosed the very thing being removed. Keeping the
  plaintext out of the repo entirely is both safer and simpler: matching is a plain
  normalised substring test, with no n-gram window and no length cap to get wrong.

* **Allow-listed shapes, for identifiers.** A CIN or GSTIN *can* be recognised by
  shape, but our own invented fixtures share that shape, so a bare shape check would
  flag them. Every identifier-shaped token must therefore be one we chose — and the
  allow-list itself is checked for being obviously invented, because an allow-list you
  can add a real value to is not a control.

* **Artifacts, because that is where the residue actually was.** An adversarial review
  found the pre-sanitisation values still sitting in `.pytest_cache` node IDs, in
  `build/lib/`, and inside a built wheel in `dist/` that was ready to upload. None of
  those are tracked, so a scan of `git ls-files` said everything was clean while a
  publishable artifact still carried the values. Ignored is not the same as absent.

If this file fails, do not weaken it. Replace the value with an invented one of the
same shape, and check whether it also needs removing from git history and from any
built artifact — a clean working tree says nothing about either.
"""
from __future__ import annotations

import re
import subprocess
import unicodedata
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
GUARD = Path(__file__).resolve().relative_to(REPO).as_posix()
WATCHLIST = REPO / ".sanitisation-watchlist"

# Ignored trees that have actually held residue, plus archives whose members must be
# read rather than trusted. Scanned only if present, so a fresh clone is unaffected.
ARTIFACT_DIRS = ("build", "dist", ".pytest_cache", ".mypy_cache", ".ruff_cache")
ARCHIVE_SUFFIXES = (".whl", ".zip")

CIN_SHAPE = re.compile(r"\b[UL][0-9]{5}[A-Z]{2}[0-9]{4}[A-Z]{3}[0-9]{6}\b", re.I)
GSTIN_SHAPE = re.compile(r"\b[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9][A-Z][0-9A-Z]\b", re.I)
PAN_SHAPE = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")
IFSC_SHAPE = re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")
UDYAM_SHAPE = re.compile(r"\bUDYAM-[A-Z]{2}-[0-9]{2}-[0-9]{7}\b", re.I)
# The boundaries reject alphanumerics, not just digits: a ten-digit run inside a
# SHA-256 digest (`...c34df8450759209a4b2f...`) matched a digits-only lookbehind and
# reported a benchmark checksum as a phone number.
PHONE_SHAPE = re.compile(r"(?<![0-9A-Za-z])(?:\+91[\s-]?)?[6-9][0-9]{9}(?![0-9A-Za-z])")

# Every one of these is invented. The PAN-shaped ones are the documentation dummy
# (`ABCDE1234F`) or a keyboard mash; the CIN and GSTIN embed an obviously sequential
# run. `_test_allow_listed_identifiers_look_invented` enforces that, so a real value
# cannot be quietly added here to make the shape test pass.
ALLOWED_IDENTIFIERS = {
    "u72900mh2019ptc123456",     # CIN, sequential tail
    "27abcde1234f1z5",           # GSTIN over the dummy PAN
    "29abcde1234f1z5",           # GSTIN over the dummy PAN
    "abcde1234f",                # the documentation dummy PAN
    "zxcvb9876k",                # keyboard mash
}
SYNTHETIC_MARKS = ("abcde1234f", "123456", "zxcvb", "9876")

# An absolute home directory names the machine's owner; `~/Projects/...` leaks the
# same local layout without the username, and the first version missed it.
LOCAL_PATH = re.compile(r"(?:/(?:Users|home)/[A-Za-z0-9._-]+|~/(?:Projects|Documents|Desktop)/)")

_CONFUSABLES = str.maketrans({
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M",
    "Н": "H", "О": "O", "Р": "P", "С": "C", "Т": "T",
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c",
    "і": "i", "Α": "A", "Β": "B", "Ε": "E", "Ο": "O",
})


def normalise(text: str) -> str:
    """Words only, lowercased, single-spaced, homoglyphs folded to ASCII.

    Collapsing whitespace is what lets this see a value wrapped across a line break —
    the case a literal search missed in CHANGELOG.md. Folding confusables closes the
    trivial Cyrillic-'о' substitution. It does NOT constant-fold Python string
    concatenation, so a value assembled as `"Ald" + "ridge"` still escapes; that is a
    known limit, recorded rather than pretended away.
    """
    text = unicodedata.normalize("NFKD", text).translate(_CONFUSABLES)
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def load_watchlist() -> list[str]:
    if not WATCHLIST.exists():
        return []
    out = []
    for line in WATCHLIST.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def scan(items, watch: list[str]) -> list[str]:
    """THE scanner. `items` is (label, text); `watch` is plaintext values.

    Both the real test and the canary go through this one function. The previous
    version had the canary exercise a helper the real test did not depend on, so
    emptying the watchlist left every test passing — a guard asleep and silent.
    """
    needles = [(v, normalise(v)) for v in watch]
    needles = [(v, n) for v, n in needles if n]
    hits = []
    for label, text in items:
        haystack = normalise(text)
        for value, needle in needles:
            if needle in haystack:
                hits.append("%s: %s" % (label, value))
    return hits


def _tracked_paths() -> list[str]:
    # -z, because a filename containing a newline would otherwise split into two
    # bogus paths and the real one would go unscanned.
    out = subprocess.run(["git", "-C", str(REPO), "ls-files", "-z"],
                         capture_output=True, text=True, check=True).stdout
    return [p for p in out.split("\0") if p]


def _artifact_paths() -> list[Path]:
    found = []
    for d in ARTIFACT_DIRS:
        root = REPO / d
        if root.is_dir():
            found += [p for p in root.rglob("*") if p.is_file()]
    found += [p for p in REPO.rglob("*")
              if p.is_file() and p.suffix in ARCHIVE_SUFFIXES
              and ".venv" not in p.parts and ".git" not in p.parts]
    return found


def collect():
    """(items, undecodable) — every text we can read, and every one we cannot."""
    items, undecodable = [], []
    for rel in _tracked_paths():
        if rel == GUARD:                     # the exact path, not any file so named
            continue
        path = REPO / rel
        try:
            items.append((rel, path.read_text(encoding="utf-8")))
        except UnicodeDecodeError:
            undecodable.append(rel)          # fail closed, do not skip silently
        except OSError:
            undecodable.append(rel)
    for path in _artifact_paths():
        rel = path.relative_to(REPO).as_posix()
        if path.suffix in ARCHIVE_SUFFIXES:
            try:
                zf = zipfile.ZipFile(path)
            except (zipfile.BadZipFile, OSError):
                undecodable.append(rel)
                continue
            for member in zf.namelist():
                try:
                    items.append(("%s!%s" % (rel, member),
                                  zf.read(member).decode("utf-8")))
                except (UnicodeDecodeError, OSError, zipfile.BadZipFile):
                    continue                 # binary member of a build artifact
        else:
            try:
                items.append((rel, path.read_text(encoding="utf-8")))
            except (UnicodeDecodeError, OSError):
                continue                     # ignored caches hold binaries too
    return items, undecodable


ITEMS, UNDECODABLE = collect()
WATCH = load_watchlist()


def test_the_scanner_catches_a_planted_value():
    """End to end through the same `scan` the real test uses.

    The canary's plaintext is safe to write here, so proving the mechanism costs no
    disclosure. It must fail when a watched value is present and stay quiet when the
    watchlist is empty — that second half is what makes the first half mean anything.
    """
    canary = "Zzcanary Forbidden Identifier Zz"
    planted = [("fake.md", "prose mentioning %s in passing" % canary)]
    assert scan(planted, [canary]), "the scanner cannot see a value that is present"
    assert not scan(planted, []), "the scanner reports a hit with nothing to watch"
    # wrapped across a line break, and recased — both must still match
    wrapped = [("fake.md", "prose mentioning zzcanary forbidden\n  IDENTIFIER zz here")]
    assert scan(wrapped, [canary]), "a value wrapped across lines escaped the scanner"


def test_the_watchlist_is_present_and_populated():
    """A guard with nothing to watch passes everything and says nothing.

    Skipped rather than failed when the file is absent, so a fresh clone is usable —
    but never silently: the reason names the file. This is a local pre-commit control,
    not a CI gate, because the values must not live in the repository.
    """
    if not WATCHLIST.exists():
        pytest.skip("no %s in this checkout — the name watchlist cannot run. The "
                    "shape and path checks below still do." % WATCHLIST.name)
    assert len(WATCH) >= 20, (
        "%s has only %d entries; it is meant to hold every value removed in the "
        "v0.6.0 sanitisation" % (WATCHLIST.name, len(WATCH)))


def test_nothing_scannable_contains_a_removed_value():
    if not WATCHLIST.exists():
        pytest.skip("no %s in this checkout" % WATCHLIST.name)
    hits = scan(ITEMS, WATCH)
    assert not hits, (
        "a value removed in the v0.6.0 sanitisation is back. Ignored artifacts count: "
        "the review found these in .pytest_cache, build/ and a built wheel while the "
        "tracked tree was clean.\n  " + "\n  ".join(sorted(set(hits))))


def test_every_tracked_file_could_actually_be_read():
    """Fail closed. Skipping what it cannot decode is how a scanner lies."""
    assert not UNDECODABLE, (
        "these tracked files could not be decoded, so nothing above examined them:\n  "
        + "\n  ".join(sorted(UNDECODABLE)))


@pytest.mark.parametrize("shape,label", [
    (CIN_SHAPE, "CIN"), (GSTIN_SHAPE, "GSTIN"), (PAN_SHAPE, "PAN"),
    (IFSC_SHAPE, "IFSC"), (UDYAM_SHAPE, "UDYAM"), (PHONE_SHAPE, "phone"),
])
def test_every_identifier_shaped_token_is_one_we_invented(shape, label):
    unknown = []
    for name, text in ITEMS:
        for token in shape.findall(text):
            if token.lower().replace(" ", "").replace("-", "") not in ALLOWED_IDENTIFIERS:
                unknown.append("%s: %s" % (name, token))
    assert not unknown, (
        "%s-shaped token that is not in the invented allow-list. If it is real it must "
        "not be here; if it is invented, add it to ALLOWED_IDENTIFIERS — and it has to "
        "carry one of the synthetic marks:\n  " % label
        + "\n  ".join(sorted(set(unknown))))


def test_allow_listed_identifiers_look_invented():
    """An allow-list you can drop a real value into is not a control.

    Every entry must carry a mark that no issued identifier would: the documentation
    dummy PAN, a sequential run, or a keyboard mash.
    """
    bad = [v for v in ALLOWED_IDENTIFIERS
           if not any(mark in v for mark in SYNTHETIC_MARKS)]
    assert not bad, (
        "allow-listed identifier with nothing marking it as invented: %s" % sorted(bad))


def test_no_local_filesystem_paths():
    hits = ["%s: %s" % (name, m)
            for name, text in ITEMS for m in set(LOCAL_PATH.findall(text))]
    assert not hits, (
        "a local path leaks the machine's owner or layout:\n  "
        + "\n  ".join(sorted(set(hits))))
