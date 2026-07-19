"""Vendored, dependency-free Porter stemmer (Porter 1980, public domain) with
docdex guardrails.

Collides morphological variants (governing/governed/governs) so retrieval
matches meaning, not just surface form. This mirrors the algorithm SQLite FTS5's
built-in `porter` tokenizer uses, so the fast index and this authoritative Python
check stem the same families.

English/ASCII only by design: any token containing a digit or a non-ASCII
character is returned lowercased-but-unstemmed, so identifiers, amounts, dates,
and non-English text stay literal and exact-answer honesty is preserved.
"""
from __future__ import annotations

_VOWELS = frozenset("aeiou")


def _is_consonant(word: str, i: int) -> bool:
    ch = word[i]
    if ch in _VOWELS:
        return False
    if ch == "y":
        return i == 0 or not _is_consonant(word, i - 1)
    return True


def _form(word: str) -> str:
    return "".join("c" if _is_consonant(word, i) else "v" for i in range(len(word)))


def _measure(word: str) -> int:
    """Porter's m: the number of vowel-consonant sequences in the stem."""
    return _form(word).count("vc")


def _has_vowel(word: str) -> bool:
    return "v" in _form(word)


def _ends_double_consonant(word: str) -> bool:
    return (len(word) >= 2 and word[-1] == word[-2]
            and _is_consonant(word, len(word) - 1))


def _ends_cvc(word: str) -> bool:
    if len(word) < 3:
        return False
    if not (_is_consonant(word, len(word) - 3)
            and not _is_consonant(word, len(word) - 2)
            and _is_consonant(word, len(word) - 1)):
        return False
    return word[-1] not in "wxy"


def _step1a(w: str) -> str:
    if w.endswith("sses"):
        return w[:-2]
    if w.endswith("ies"):
        return w[:-2]
    if w.endswith("ss"):
        return w
    if w.endswith("s"):
        return w[:-1]
    return w


def _step1b_post(w: str) -> str:
    if w.endswith(("at", "bl", "iz")):
        return w + "e"
    if _ends_double_consonant(w) and not w.endswith(("l", "s", "z")):
        return w[:-1]
    if _measure(w) == 1 and _ends_cvc(w):
        return w + "e"
    return w


def _step1b(w: str) -> str:
    if w.endswith("eed"):
        return w[:-1] if _measure(w[:-3]) > 0 else w
    if w.endswith("ed"):
        stem_ = w[:-2]
        return _step1b_post(stem_) if _has_vowel(stem_) else w
    if w.endswith("ing"):
        stem_ = w[:-3]
        return _step1b_post(stem_) if _has_vowel(stem_) else w
    return w


def _step1c(w: str) -> str:
    if w.endswith("y") and _has_vowel(w[:-1]):
        return w[:-1] + "i"
    return w


def _replace_if_m(w: str, suffix: str, repl: str, min_m: int):
    stem_ = w[:-len(suffix)]
    return stem_ + repl if _measure(stem_) > min_m else None


_STEP2 = [
    ("ational", "ate"), ("tional", "tion"), ("enci", "ence"), ("anci", "ance"),
    ("izer", "ize"), ("bli", "ble"), ("alli", "al"), ("entli", "ent"),
    ("eli", "e"), ("ousli", "ous"), ("ization", "ize"), ("ation", "ate"),
    ("ator", "ate"), ("alism", "al"), ("iveness", "ive"), ("fulness", "ful"),
    ("ousness", "ous"), ("aliti", "al"), ("iviti", "ive"), ("biliti", "ble"),
    ("logi", "log"),
]
_STEP3 = [
    ("icate", "ic"), ("ative", ""), ("alize", "al"), ("iciti", "ic"),
    ("ical", "ic"), ("ful", ""), ("ness", ""),
]
_STEP4 = [
    "al", "ance", "ence", "er", "ic", "able", "ible", "ant", "ement",
    "ment", "ent", "ou", "ism", "ate", "iti", "ous", "ive", "ize",
]


def _step2(w: str) -> str:
    for suf, repl in _STEP2:
        if w.endswith(suf):
            out = _replace_if_m(w, suf, repl, 0)
            return out if out is not None else w
    return w


def _step3(w: str) -> str:
    for suf, repl in _STEP3:
        if w.endswith(suf):
            out = _replace_if_m(w, suf, repl, 0)
            return out if out is not None else w
    return w


def _step4(w: str) -> str:
    if w.endswith("ion"):
        stem_ = w[:-3]
        if _measure(stem_) > 1 and stem_.endswith(("s", "t")):
            return stem_
        return w
    for suf in _STEP4:
        if w.endswith(suf):
            stem_ = w[:-len(suf)]
            return stem_ if _measure(stem_) > 1 else w
    return w


def _step5a(w: str) -> str:
    if w.endswith("e"):
        stem_ = w[:-1]
        m = _measure(stem_)
        if m > 1 or (m == 1 and not _ends_cvc(stem_)):
            return stem_
    return w


def _step5b(w: str) -> str:
    if _measure(w) > 1 and _ends_double_consonant(w) and w.endswith("l"):
        return w[:-1]
    return w


def _porter(w: str) -> str:
    if len(w) <= 2:
        return w
    for step in (_step1a, _step1b, _step1c, _step2, _step3, _step4,
                 _step5a, _step5b):
        w = step(w)
    return w


def stem(token: str) -> str:
    """Porter stem of a single token, lowercased.

    Guardrail: a token that is not a plain ASCII word (contains a digit,
    punctuation, or any non-ASCII letter) is returned lowercased-but-unstemmed,
    so IDs (`gstr3b`), amounts, dates, and non-English text (`échéance`,
    Devanagari) stay literal and exact.
    """
    t = token.lower()
    if not (t.isascii() and t.isalpha()):
        return t
    return _porter(t)
