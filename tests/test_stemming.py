"""Stemming: morphological variants must collide, but identifiers / amounts /
non-English text must stay literal (recall without breaking exact-answer honesty).
"""
from __future__ import annotations

from docdex.stemming import stem


def test_inflections_collide_to_one_stem():
    assert stem("governing") == stem("governed") == stem("governs")
    assert stem("deal") == stem("deals")
    assert stem("close") == stem("closed")


def test_stem_is_idempotent():
    for w in ("governing", "organizations", "closed", "happiness"):
        assert stem(stem(w)) == stem(w)


def test_stem_lowercases_plain_words():
    assert stem("Governing") == stem("governing")


def test_identifiers_and_amounts_are_never_stemmed():
    # Anything with a digit is an identifier/amount/date — returned unchanged.
    for tok in ("gstr3b", "42000000", "27abcde1234f1z5", "31"):
        assert stem(tok) == tok.lower()


def test_non_ascii_is_never_stemmed():
    for tok in ("échéance", "naïve", "शीर्षक", "café"):
        assert stem(tok) == tok.lower()
