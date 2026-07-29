# docdex benchmark results

Corpus: **162 files** (1.92 MB raw, ~54,150 tokens of text), 12 planted facts behind misleading filenames. Deterministic (seed 42) — regenerate and rerun with `python3 benchmarks/run_benchmark.py`.

One-time indexing: sync 0.7s + semantic build 0.2s; index on disk 1.53 MB. Environment: Python 3.9.6, macOS-26.5.2-arm64-arm-64bit. Token counts are a chars/4 approximation.

| method | right file ranked #1 | in top 3 | answer reached | median tokens to answer | median ms |
|---|---|---|---|---|---|
| browse by filename (no docdex) | 0/12 | 0/12 | 0/12 | 976 | 0 |
| raw `grep -ril` (no docdex) | 0/12 | 0/12 | 0/12 | 1,017 | 197 |
| read everything (no docdex) | 12/12 | 12/12 | 12/12 | 28,312 | 6 |
| **`docdex search`** (exact-ish query) | 12/12 | 12/12 | 12/12 | 728 | 35 |
| `docdex semantic` (exact-ish query) | 4/12 | 5/12 | 4/12 | 694 | 41 |
| `docdex search` (fuzzy/paraphrased query) | 4/12 | 8/12 | 5/12 | 363 | 34 |
| **`docdex semantic`** (fuzzy query) | 0/12 | 0/12 | 0/12 | 609 | 41 |

Headline: to reach an answer, `docdex search` needs a median of **728 tokens** vs **28,312** for the read-everything fallback — **39× less context** per question, after a one-time 0.7s indexing cost. Filename browsing and raw grep are structurally blind to Office/PDF content and fail on most questions.

## Per-question detail

| case | method | hit@1 | hit@3 | answered | tokens | ms |
|---|---|---|---|---|---|---|
| Q01 | filename | - | - | - | 976 | 0 |
| Q01 | rawgrep | - | - | - | 1,017 | 236 |
| Q01 | readall | Y | Y | Y | 42,660 | 9 |
| Q01 | docdex | Y | Y | Y | 758 | 35 |
| Q01 | docdex-sem-x | - | Y | - | 893 | 42 |
| Q01 | docdex-fuz | Y | Y | Y | 599 | 34 |
| Q01 | docdex-sem | - | - | - | 0 | 40 |
| Q02 | filename | - | - | - | 976 | 0 |
| Q02 | rawgrep | - | - | - | 1,017 | 191 |
| Q02 | readall | Y | Y | Y | 128 | 0 |
| Q02 | docdex | Y | Y | Y | 243 | 35 |
| Q02 | docdex-sem-x | Y | Y | Y | 375 | 41 |
| Q02 | docdex-fuz | - | - | - | 796 | 35 |
| Q02 | docdex-sem | - | - | - | 610 | 41 |
| Q03 | filename | - | - | - | 1,973 | 0 |
| Q03 | rawgrep | - | - | - | 1,017 | 193 |
| Q03 | readall | Y | Y | Y | 50,023 | 11 |
| Q03 | docdex | Y | Y | Y | 849 | 36 |
| Q03 | docdex-sem-x | - | - | - | 673 | 40 |
| Q03 | docdex-fuz | Y | Y | Y | 771 | 34 |
| Q03 | docdex-sem | - | - | - | 814 | 41 |
| Q04 | filename | - | - | - | 976 | 0 |
| Q04 | rawgrep | - | - | - | 1,017 | 197 |
| Q04 | readall | Y | Y | Y | 33,235 | 7 |
| Q04 | docdex | Y | Y | Y | 661 | 35 |
| Q04 | docdex-sem-x | - | - | - | 716 | 42 |
| Q04 | docdex-fuz | Y | Y | Y | 662 | 34 |
| Q04 | docdex-sem | - | - | - | 884 | 41 |
| Q05 | filename | - | - | - | 976 | 0 |
| Q05 | rawgrep | - | - | - | 1,017 | 234 |
| Q05 | readall | Y | Y | Y | 9,742 | 2 |
| Q05 | docdex | Y | Y | Y | 820 | 35 |
| Q05 | docdex-sem-x | - | - | - | 921 | 41 |
| Q05 | docdex-fuz | Y | Y | Y | 235 | 35 |
| Q05 | docdex-sem | - | - | - | 801 | 41 |
| Q06 | filename | - | - | - | 976 | 0 |
| Q06 | rawgrep | - | - | - | 1,017 | 194 |
| Q06 | readall | Y | Y | Y | 25,328 | 5 |
| Q06 | docdex | Y | Y | Y | 368 | 35 |
| Q06 | docdex-sem-x | Y | Y | Y | 423 | 40 |
| Q06 | docdex-fuz | - | Y | - | 435 | 34 |
| Q06 | docdex-sem | - | - | - | 609 | 42 |
| Q07 | filename | - | - | - | 976 | 0 |
| Q07 | rawgrep | - | - | - | 0 | 193 |
| Q07 | readall | Y | Y | Y | 51,845 | 11 |
| Q07 | docdex | Y | Y | Y | 241 | 35 |
| Q07 | docdex-sem-x | Y | Y | Y | 143 | 39 |
| Q07 | docdex-fuz | - | Y | Y | 218 | 34 |
| Q07 | docdex-sem | - | - | - | 828 | 40 |
| Q08 | filename | - | - | - | 976 | 0 |
| Q08 | rawgrep | - | - | - | 1,017 | 196 |
| Q08 | readall | Y | Y | Y | 4,750 | 1 |
| Q08 | docdex | Y | Y | Y | 698 | 35 |
| Q08 | docdex-sem-x | - | - | - | 1,039 | 59 |
| Q08 | docdex-fuz | - | - | - | 291 | 79 |
| Q08 | docdex-sem | - | - | - | 518 | 44 |
| Q09 | filename | - | - | - | 976 | 0 |
| Q09 | rawgrep | - | - | - | 1,017 | 235 |
| Q09 | readall | Y | Y | Y | 31,296 | 7 |
| Q09 | docdex | Y | Y | Y | 770 | 35 |
| Q09 | docdex-sem-x | - | - | - | 786 | 42 |
| Q09 | docdex-fuz | - | - | - | 290 | 35 |
| Q09 | docdex-sem | - | - | - | 559 | 41 |
| Q10 | filename | - | - | - | 976 | 0 |
| Q10 | rawgrep | - | - | - | 1,017 | 196 |
| Q10 | readall | Y | Y | Y | 15,508 | 3 |
| Q10 | docdex | Y | Y | Y | 823 | 34 |
| Q10 | docdex-sem-x | - | - | - | 563 | 41 |
| Q10 | docdex-fuz | - | Y | - | 662 | 34 |
| Q10 | docdex-sem | - | - | - | 779 | 44 |
| Q11 | filename | - | - | - | 976 | 0 |
| Q11 | rawgrep | - | - | - | 1,044 | 249 |
| Q11 | readall | Y | Y | Y | 19,402 | 4 |
| Q11 | docdex | Y | Y | Y | 835 | 36 |
| Q11 | docdex-sem-x | - | - | - | 864 | 44 |
| Q11 | docdex-fuz | - | Y | - | 291 | 35 |
| Q11 | docdex-sem | - | - | - | 492 | 42 |
| Q12 | filename | - | - | - | 976 | 0 |
| Q12 | rawgrep | - | - | - | 1,017 | 232 |
| Q12 | readall | Y | Y | Y | 37,374 | 8 |
| Q12 | docdex | Y | Y | Y | 239 | 34 |
| Q12 | docdex-sem-x | Y | Y | Y | 406 | 41 |
| Q12 | docdex-fuz | - | - | - | 239 | 36 |
| Q12 | docdex-sem | - | - | - | 0 | 41 |
