"""Token accounting and chunking shared by the index and the context packer.

Uses `tiktoken` (cl100k_base) when it is installed for accurate counts, and
falls back to a chars/4 approximation otherwise. The fallback is stated
wherever counts are reported so nobody mistakes an estimate for ground truth.
"""
from __future__ import annotations

import re
from typing import Iterator, Optional, Tuple

CHUNK_CHARS = 1800
OVERLAP = 250

_encoder = None
_encoder_tried = False


def _get_encoder():
    global _encoder, _encoder_tried
    if not _encoder_tried:
        _encoder_tried = True
        try:
            import tiktoken
            _encoder = tiktoken.get_encoding("cl100k_base")
        except Exception:  # noqa: BLE001 - tiktoken optional/absent
            _encoder = None
    return _encoder


def using_real_tokenizer() -> bool:
    return _get_encoder() is not None


def count_tokens(text: str) -> int:
    if not text:
        return 0
    enc = _get_encoder()
    if enc is not None:
        return len(enc.encode(text, disallowed_special=()))
    return max(1, (len(text) + 3) // 4)


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def iter_chunks(text: str, size: int = CHUNK_CHARS,
                overlap: int = OVERLAP) -> Iterator[Tuple[int, int, str]]:
    """Yield (start, end, chunk_text) over whitespace-normalized text."""
    text = normalize(text)
    if not text:
        return
    start = 0
    n = len(text)
    while start < n:
        end = min(n, start + size)
        yield start, end, text[start:end]
        if end == n:
            break
        start = max(0, end - overlap)
