# docdex benchmark results

Corpus: **162 files** (1.92 MB raw, ~54,150 tokens of text), 12 planted facts behind misleading filenames. Deterministic (seed 42) — regenerate and rerun with `python3 benchmarks/run_benchmark.py`.

One-time indexing: sync 4.0s + semantic build 0.9s; index on disk 1.03 MB. Environment: Python 3.9.6, macOS-26.5-arm64-arm-64bit. Token counts are a chars/4 approximation.

| method | right file ranked #1 | in top 3 | answer reached | median tokens to answer | median ms |
|---|---|---|---|---|---|
| browse by filename (no docdex) | 0/12 | 0/12 | 0/12 | 976 | 0 |
| raw `grep -ril` (no docdex) | 0/12 | 0/12 | 0/12 | 1,017 | 259 |
| read everything (no docdex) | 12/12 | 12/12 | 12/12 | 28,312 | 158 |
| **`docdex search`** (exact-ish query) | 12/12 | 12/12 | 12/12 | 780 | 646 |
| `docdex semantic` (exact-ish query) | 4/12 | 5/12 | 4/12 | 694 | 155 |
| `docdex search` (fuzzy/paraphrased query) | 7/12 | 8/12 | 7/12 | 786 | 553 |
| **`docdex semantic`** (fuzzy query) | 0/12 | 0/12 | 0/12 | 803 | 157 |

Headline: to reach an answer, `docdex search` needs a median of **780 tokens** vs **28,312** for the read-everything fallback — **36× less context** per question, after a one-time 4.0s indexing cost. Filename browsing and raw grep are structurally blind to Office/PDF content and fail on most questions.

## Per-question detail

| case | method | hit@1 | hit@3 | answered | tokens | ms |
|---|---|---|---|---|---|---|
| Q01 | filename | - | - | - | 976 | 0 |
| Q01 | rawgrep | - | - | - | 1,017 | 258 |
| Q01 | readall | Y | Y | Y | 42,660 | 402 |
| Q01 | docdex | Y | Y | Y | 810 | 797 |
| Q01 | docdex-sem-x | - | Y | - | 893 | 161 |
| Q01 | docdex-fuz | - | Y | - | 887 | 728 |
| Q01 | docdex-sem | - | - | - | 897 | 194 |
| Q02 | filename | - | - | - | 976 | 0 |
| Q02 | rawgrep | - | - | - | 1,017 | 253 |
| Q02 | readall | Y | Y | Y | 128 | 2 |
| Q02 | docdex | Y | Y | Y | 303 | 771 |
| Q02 | docdex-sem-x | Y | Y | Y | 375 | 144 |
| Q02 | docdex-fuz | - | - | - | 817 | 742 |
| Q02 | docdex-sem | - | - | - | 610 | 159 |
| Q03 | filename | - | - | - | 1,973 | 0 |
| Q03 | rawgrep | - | - | - | 1,017 | 214 |
| Q03 | readall | Y | Y | Y | 50,023 | 595 |
| Q03 | docdex | Y | Y | Y | 902 | 710 |
| Q03 | docdex-sem-x | - | - | - | 673 | 196 |
| Q03 | docdex-fuz | Y | Y | Y | 920 | 590 |
| Q03 | docdex-sem | - | - | - | 920 | 202 |
| Q04 | filename | - | - | - | 976 | 0 |
| Q04 | rawgrep | - | - | - | 1,017 | 335 |
| Q04 | readall | Y | Y | Y | 33,235 | 297 |
| Q04 | docdex | Y | Y | Y | 717 | 757 |
| Q04 | docdex-sem-x | - | - | - | 716 | 154 |
| Q04 | docdex-fuz | Y | Y | Y | 707 | 663 |
| Q04 | docdex-sem | - | - | - | 884 | 154 |
| Q05 | filename | - | - | - | 976 | 0 |
| Q05 | rawgrep | - | - | - | 1,017 | 277 |
| Q05 | readall | Y | Y | Y | 9,742 | 117 |
| Q05 | docdex | Y | Y | Y | 872 | 661 |
| Q05 | docdex-sem-x | - | - | - | 921 | 185 |
| Q05 | docdex-fuz | Y | Y | Y | 286 | 517 |
| Q05 | docdex-sem | - | - | - | 859 | 162 |
| Q06 | filename | - | - | - | 976 | 0 |
| Q06 | rawgrep | - | - | - | 1,017 | 233 |
| Q06 | readall | Y | Y | Y | 25,328 | 256 |
| Q06 | docdex | Y | Y | Y | 424 | 682 |
| Q06 | docdex-sem-x | Y | Y | Y | 423 | 143 |
| Q06 | docdex-fuz | - | - | - | 896 | 632 |
| Q06 | docdex-sem | - | - | - | 609 | 137 |
| Q07 | filename | - | - | - | 976 | 0 |
| Q07 | rawgrep | - | - | - | 0 | 220 |
| Q07 | readall | Y | Y | Y | 51,845 | 548 |
| Q07 | docdex | Y | Y | Y | 102 | 631 |
| Q07 | docdex-sem-x | Y | Y | Y | 380 | 133 |
| Q07 | docdex-fuz | Y | Y | Y | 308 | 591 |
| Q07 | docdex-sem | - | - | - | 828 | 121 |
| Q08 | filename | - | - | - | 976 | 0 |
| Q08 | rawgrep | - | - | - | 1,017 | 271 |
| Q08 | readall | Y | Y | Y | 4,750 | 32 |
| Q08 | docdex | Y | Y | Y | 751 | 554 |
| Q08 | docdex-sem-x | - | - | - | 1,039 | 166 |
| Q08 | docdex-fuz | Y | Y | Y | 755 | 481 |
| Q08 | docdex-sem | - | - | - | 517 | 171 |
| Q09 | filename | - | - | - | 976 | 0 |
| Q09 | rawgrep | - | - | - | 1,017 | 261 |
| Q09 | readall | Y | Y | Y | 31,296 | 200 |
| Q09 | docdex | Y | Y | Y | 822 | 455 |
| Q09 | docdex-sem-x | - | - | - | 786 | 157 |
| Q09 | docdex-fuz | - | - | - | 425 | 399 |
| Q09 | docdex-sem | - | - | - | 559 | 153 |
| Q10 | filename | - | - | - | 976 | 0 |
| Q10 | rawgrep | - | - | - | 1,017 | 251 |
| Q10 | readall | Y | Y | Y | 15,508 | 106 |
| Q10 | docdex | Y | Y | Y | 881 | 370 |
| Q10 | docdex-sem-x | - | - | - | 563 | 163 |
| Q10 | docdex-fuz | Y | Y | Y | 887 | 241 |
| Q10 | docdex-sem | - | - | - | 779 | 175 |
| Q11 | filename | - | - | - | 976 | 0 |
| Q11 | rawgrep | - | - | - | 1,044 | 305 |
| Q11 | readall | Y | Y | Y | 19,402 | 15 |
| Q11 | docdex | Y | Y | Y | 893 | 335 |
| Q11 | docdex-sem-x | - | - | - | 864 | 74 |
| Q11 | docdex-fuz | Y | Y | Y | 898 | 122 |
| Q11 | docdex-sem | - | - | - | 511 | 41 |
| Q12 | filename | - | - | - | 976 | 0 |
| Q12 | rawgrep | - | - | - | 1,017 | 350 |
| Q12 | readall | Y | Y | Y | 37,374 | 6 |
| Q12 | docdex | Y | Y | Y | 297 | 110 |
| Q12 | docdex-sem-x | Y | Y | Y | 406 | 59 |
| Q12 | docdex-fuz | - | - | - | 433 | 43 |
| Q12 | docdex-sem | - | - | - | 1,033 | 41 |
