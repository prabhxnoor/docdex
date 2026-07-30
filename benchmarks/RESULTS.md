# docdex benchmark results

Corpus: **162 files** (1.92 MB raw, ~54,150 tokens of text), 12 planted facts behind misleading filenames. Deterministic (seed 42) — regenerate and rerun with `python3 benchmarks/run_benchmark.py`.

One-time indexing: sync 0.9s + semantic build 0.2s; index on disk 1.53 MB. Environment: Python 3.9.6, macOS-26.5.2-arm64-arm-64bit. Token counts are a chars/4 approximation.

| method | right file ranked #1 | in top 3 | answer reached | median tokens to answer | median ms |
|---|---|---|---|---|---|
| browse by filename (no docdex) | 0/12 | 0/12 | 0/12 | 976 | 0 |
| raw `grep -ril` (no docdex) | 0/12 | 0/12 | 0/12 | 1,017 | 223 |
| read everything (no docdex) | 12/12 | 12/12 | 12/12 | 28,312 | 7 |
| **`docdex search`** (exact-ish query) | 12/12 | 12/12 | 12/12 | 729 | 38 |
| `docdex semantic` (exact-ish query) | 4/12 | 5/12 | 4/12 | 694 | 45 |
| `docdex search` (fuzzy/paraphrased query) | 4/12 | 8/12 | 5/12 | 363 | 38 |
| **`docdex semantic`** (fuzzy query) | 0/12 | 0/12 | 0/12 | 609 | 45 |

Headline: to reach an answer, `docdex search` needs a median of **729 tokens** vs **28,312** for the read-everything fallback — **39× less context** per question, after a one-time 0.9s indexing cost. Filename browsing and raw grep are structurally blind to Office/PDF content and fail on most questions.

## Per-question detail

| case | method | hit@1 | hit@3 | answered | tokens | ms |
|---|---|---|---|---|---|---|
| Q01 | filename | - | - | - | 976 | 0 |
| Q01 | rawgrep | - | - | - | 1,017 | 256 |
| Q01 | readall | Y | Y | Y | 42,660 | 11 |
| Q01 | docdex | Y | Y | Y | 760 | 38 |
| Q01 | docdex-sem-x | - | Y | - | 893 | 44 |
| Q01 | docdex-fuz | Y | Y | Y | 599 | 37 |
| Q01 | docdex-sem | - | - | - | 0 | 45 |
| Q02 | filename | - | - | - | 976 | 0 |
| Q02 | rawgrep | - | - | - | 1,017 | 215 |
| Q02 | readall | Y | Y | Y | 128 | 0 |
| Q02 | docdex | Y | Y | Y | 244 | 38 |
| Q02 | docdex-sem-x | Y | Y | Y | 375 | 45 |
| Q02 | docdex-fuz | - | - | - | 796 | 38 |
| Q02 | docdex-sem | - | - | - | 610 | 45 |
| Q03 | filename | - | - | - | 1,973 | 0 |
| Q03 | rawgrep | - | - | - | 1,017 | 224 |
| Q03 | readall | Y | Y | Y | 50,023 | 13 |
| Q03 | docdex | Y | Y | Y | 851 | 39 |
| Q03 | docdex-sem-x | - | - | - | 673 | 46 |
| Q03 | docdex-fuz | Y | Y | Y | 771 | 37 |
| Q03 | docdex-sem | - | - | - | 814 | 46 |
| Q04 | filename | - | - | - | 976 | 0 |
| Q04 | rawgrep | - | - | - | 1,017 | 222 |
| Q04 | readall | Y | Y | Y | 33,235 | 8 |
| Q04 | docdex | Y | Y | Y | 662 | 38 |
| Q04 | docdex-sem-x | - | - | - | 716 | 46 |
| Q04 | docdex-fuz | Y | Y | Y | 662 | 38 |
| Q04 | docdex-sem | - | - | - | 884 | 45 |
| Q05 | filename | - | - | - | 976 | 0 |
| Q05 | rawgrep | - | - | - | 1,017 | 285 |
| Q05 | readall | Y | Y | Y | 9,742 | 2 |
| Q05 | docdex | Y | Y | Y | 820 | 39 |
| Q05 | docdex-sem-x | - | - | - | 921 | 45 |
| Q05 | docdex-fuz | Y | Y | Y | 235 | 38 |
| Q05 | docdex-sem | - | - | - | 801 | 46 |
| Q06 | filename | - | - | - | 976 | 0 |
| Q06 | rawgrep | - | - | - | 1,017 | 217 |
| Q06 | readall | Y | Y | Y | 25,328 | 6 |
| Q06 | docdex | Y | Y | Y | 366 | 38 |
| Q06 | docdex-sem-x | Y | Y | Y | 423 | 43 |
| Q06 | docdex-fuz | - | Y | - | 435 | 37 |
| Q06 | docdex-sem | - | - | - | 609 | 43 |
| Q07 | filename | - | - | - | 976 | 0 |
| Q07 | rawgrep | - | - | - | 0 | 216 |
| Q07 | readall | Y | Y | Y | 51,845 | 13 |
| Q07 | docdex | Y | Y | Y | 240 | 37 |
| Q07 | docdex-sem-x | Y | Y | Y | 143 | 42 |
| Q07 | docdex-fuz | - | Y | Y | 218 | 35 |
| Q07 | docdex-sem | - | - | - | 828 | 44 |
| Q08 | filename | - | - | - | 976 | 0 |
| Q08 | rawgrep | - | - | - | 1,017 | 221 |
| Q08 | readall | Y | Y | Y | 4,750 | 1 |
| Q08 | docdex | Y | Y | Y | 698 | 38 |
| Q08 | docdex-sem-x | - | - | - | 1,039 | 45 |
| Q08 | docdex-fuz | - | - | - | 291 | 39 |
| Q08 | docdex-sem | - | - | - | 518 | 46 |
| Q09 | filename | - | - | - | 976 | 0 |
| Q09 | rawgrep | - | - | - | 1,017 | 261 |
| Q09 | readall | Y | Y | Y | 31,296 | 9 |
| Q09 | docdex | Y | Y | Y | 770 | 38 |
| Q09 | docdex-sem-x | - | - | - | 786 | 44 |
| Q09 | docdex-fuz | - | - | - | 290 | 36 |
| Q09 | docdex-sem | - | - | - | 559 | 43 |
| Q10 | filename | - | - | - | 976 | 0 |
| Q10 | rawgrep | - | - | - | 1,017 | 220 |
| Q10 | readall | Y | Y | Y | 15,508 | 3 |
| Q10 | docdex | Y | Y | Y | 824 | 39 |
| Q10 | docdex-sem-x | - | - | - | 563 | 45 |
| Q10 | docdex-fuz | - | Y | - | 662 | 38 |
| Q10 | docdex-sem | - | - | - | 779 | 46 |
| Q11 | filename | - | - | - | 976 | 0 |
| Q11 | rawgrep | - | - | - | 1,044 | 260 |
| Q11 | readall | Y | Y | Y | 19,402 | 4 |
| Q11 | docdex | Y | Y | Y | 836 | 38 |
| Q11 | docdex-sem-x | - | - | - | 864 | 45 |
| Q11 | docdex-fuz | - | Y | - | 291 | 37 |
| Q11 | docdex-sem | - | - | - | 492 | 45 |
| Q12 | filename | - | - | - | 976 | 0 |
| Q12 | rawgrep | - | - | - | 1,017 | 279 |
| Q12 | readall | Y | Y | Y | 37,374 | 9 |
| Q12 | docdex | Y | Y | Y | 240 | 36 |
| Q12 | docdex-sem-x | Y | Y | Y | 406 | 46 |
| Q12 | docdex-fuz | - | - | - | 239 | 38 |
| Q12 | docdex-sem | - | - | - | 0 | 45 |
