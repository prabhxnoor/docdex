"""No real party, company or document-set identifier may re-enter this repository.

This repository is public. It once carried, in its test suite, a counterparty's name
inside a verbatim confidentiality clause that forbade disclosing that name, a real
grant receipt, a real order ID, and a company's CIN and registered office. Those
fixtures existed for a good reason — each was a real line that broke the extractor,
and no synthetic fixture had produced them — so they were replaced with synthetic
values of the SAME SHAPE rather than deleted, and the regressions still bite.

This file stops it happening again. Two mechanisms, because neither alone is enough:

* **Hashes, for names.** A name cannot be recognised by shape, and writing the real
  names here to forbid them would re-publish the very thing being removed. So the
  watch list is SHA-256 digests of normalised word n-grams. The digests reveal
  nothing; a match means the plaintext is back.
* **An allow-list, for identifiers.** A CIN or GSTIN *can* be recognised by shape,
  but our own synthetic fixtures are the same shape, so a bare shape check would flag
  them. Instead every identifier-shaped token in the tree must be one we chose.

If this file fails, do not weaken it. Replace the value with a synthetic one of the
same shape, and check whether it also needs removing from git history — the working
tree being clean says nothing about what is still reachable by commit SHA.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

# SHA-256 of normalised word n-grams of every value removed in the v0.6.0
# sanitisation. Generated, never hand-edited. Plaintext is deliberately absent.
FORBIDDEN_HASHES = {
    "2245c9a2470ef490f8585bbd10d1c9e7bd10ffe3e4fccbf73360421d6bdeb862",
    "2ba38c657de10f7b0956809d5770e4db4a5e4025413e22f93c1b38820f37595d",
    "339fd5ce6dbbff6ccc83ce017050cf9aff0d5623708bf121da1f2a4a297f81c5",
    "3a0b029a4495d4973e995bd25c10ccac1d8ab881be26eaf5144e91a672c36e1e",
    "3ca679504af3ae137cf44cf187a804fdd997d4aa116b09460c7ceab865b0ffec",
    "41396fb04a91e966847a8d1b9002720b70e9ad785d1bac48de5dd4eaa8d4c9f0",
    "49b94d2d06ef3f1db878bc32339928bef540a54b71aed53d3dcfaa24e4742390",
    "4c252dd8085bfb64a8598394045a646deca57708bf2d93695365829d85898700",
    "6228a5824a1e8d1554519125795ad14c1b7be681c6e9efbbbc66c91bbf2f89e5",
    "6854541d3abf20be242897593454adf5ff82e92989bdf621aece09606f95b8f0",
    "8988cb5e32c5590e629c603b0da3189c3eb6c36862f177d190ec95ff30a9990d",
    "8c0b0fb70efd58545636dc76efa0ce89b59609c34cb8001c13a5a0caf5ee245b",
    "8cc6810cef67dc52d33c6495d0ce6f92f9468a59f44ee9432405da8de93a3118",
    "92bb6d4b7b035b285c07037bbd1e020fbb6b23ae08baf23ed4128e475e4a705e",
    "9592286f050bfa5e7da9b20a23d1d86ba73927bfba09bb0527046ca051086f74",
    "9cd11f3c55046683bd97e712213871d5cebdd10a9bd4306bcd25f454e440bc8a",
    "a0b7bc6cc83c0356f3f1be0d6c8f235a3affe7b61bba6e13ca9314fd04ed4057",
    "a15e196b12a72da96d93f07c6c9693f35f6fe6f8e56af60e015d11fc8f080f81",
    "ac0d5a930d4caf24306388fb84907ad8589673d60bee16636d981885e2245fe9",
    "adfb268a569fa2e5ecd09c117b378c0d2d4cfb4e8606f0cb3cc617d4c1cb55f0",
    "af5b5872006dab39653c09335a3e0043f0088d145b34cab0e33b16d30e0c08ce",
    "b0cc7279eabf70bd29db6ac55af67b6fe75546866a6ffdff305cb034cb62c3d3",
    "b693fb8ca4bc20534ceb6a5e6b7a49c09f4649cd3a4188dc91b8093000ceaaa3",
    "b6eda7fd0019b30e00a5cc0fce16f6cc8f8e35ec8f80f75be20032a9df724fb1",
    "b76092a2cc54aa888ec36983c70ac4a7309dcd78591e36e1f452996b023becb1",
    "b9e8c14b70a0c2c87f92702419ebbde892746bdaca98c67fcc702c34193f19f9",
    "c9dbc1598c24b8f21c54a366066fb582d5a41b220b71c6dc4f464eea96ace385",
    "d96d3596996f98423e01d5fbb0af76fd17959494a5b3d5d8798bd3f9675478aa",
    "e595ce68b9d881026725ae17ffdc912f47beff6735f1f02afaf81cda45ed032a",
    "f4b2594af1596d6415c3a283a3ff2adaa6bb57bcdab7fb11998405cfe06bddf5",
    "fc86d987b1ce90de02d8d42bf7cf0faeffc66b5e4efc28f4869c5e2981bae754",
    "fef80763034fc3790aa603a67a7f1e7485cd56009f78db76addabf55665f31ad",
}
# Capped at 5 words, not at the longest removed value (15). Verified lossless: every
# longer value contains a kept shorter window — the grant sentence carries the funding
# body and the party, the address carries the building — so nothing escapes by being
# long, and the scan stays 3x cheaper.
MAX_NGRAM = 5

CIN_SHAPE = re.compile(r"\b[UL][0-9]{5}[A-Z]{2}[0-9]{4}[A-Z]{3}[0-9]{6}\b", re.I)
GSTIN_SHAPE = re.compile(r"\b[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9][A-Z][0-9A-Z]\b", re.I)
# Deliberately kept, and every one of them invented.
ALLOWED_IDENTIFIERS = {"u72900mh2019ptc123456", "27abcde1234f1z5", "29abcde1234f1z5"}

# An absolute home directory names the machine's owner and leaks a local layout.
DEV_PATH = re.compile(r"/Users/[A-Za-z0-9._-]+/")

# This file necessarily talks about the scan, so it would match itself.
SELF = Path(__file__).name


def _normalise(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _ngram_digests(words: list[str]):
    """Every 1..MAX_NGRAM word window, as (digest, joined) pairs."""
    for n in range(1, MAX_NGRAM + 1):
        for i in range(len(words) - n + 1):
            joined = " ".join(words[i:i + n])
            yield hashlib.sha256(joined.encode("utf-8")).hexdigest(), joined


def _tracked_text_files():
    out = subprocess.run(["git", "-C", str(REPO), "ls-files"],
                         capture_output=True, text=True, check=True).stdout
    for rel in out.splitlines():
        if not rel or Path(rel).name == SELF:
            continue
        path = REPO / rel
        try:
            yield rel, path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue          # binary or unreadable: nothing to match


ALL_FILES = list(_tracked_text_files())


def test_the_scan_can_actually_fail():
    """A guard that cannot fail guards nothing.

    Proves the n-gram mechanism detects a planted value, using a canary whose
    plaintext is safe to write here — so the proof costs no disclosure.
    """
    canary = "zzcanary forbidden identifier zz"
    digest = hashlib.sha256(" ".join(_normalise(canary)).encode()).hexdigest()
    found = {d for d, _ in _ngram_digests(_normalise("text with %s inside" % canary))}
    assert digest in found, "the n-gram scan cannot see a value that is present"
    assert digest not in FORBIDDEN_HASHES, "the canary must not be a real entry"


def test_no_tracked_file_contains_a_removed_identifier():
    hits = []
    for rel, text in ALL_FILES:
        for digest, joined in _ngram_digests(_normalise(text)):
            if digest in FORBIDDEN_HASHES:
                hits.append("%s: %r" % (rel, joined))
    assert not hits, (
        "a real identifier removed in the v0.6.0 sanitisation is back:\n  "
        + "\n  ".join(sorted(set(hits))))


@pytest.mark.parametrize("shape,label", [(CIN_SHAPE, "CIN"), (GSTIN_SHAPE, "GSTIN")])
def test_every_identifier_shaped_token_is_one_we_invented(shape, label):
    unknown = []
    for rel, text in ALL_FILES:
        for token in shape.findall(text):
            if token.lower() not in ALLOWED_IDENTIFIERS:
                unknown.append("%s: %s" % (rel, token))
    assert not unknown, (
        "%s-shaped token that is not in the invented allow-list — if it is real it "
        "must not be here, and if it is synthetic add it to ALLOWED_IDENTIFIERS:\n  "
        % label + "\n  ".join(sorted(set(unknown))))


def test_no_absolute_home_directory_paths():
    hits = ["%s: %s" % (rel, m) for rel, text in ALL_FILES
            for m in set(DEV_PATH.findall(text))]
    assert not hits, (
        "an absolute home directory names the machine's owner:\n  "
        + "\n  ".join(sorted(set(hits))))
