# docdex benchmark history

Every release, every suite, one table. Regenerate with `python3 benchmarks/bench_all.py show`; add a release with `record`, or backfill old ones with `sweep`.

All rows are produced by **today's** harness — `sweep` overlays the current `benchmarks/` onto each checked-out release, so only `src/` differs between rows. Rows recorded by an older harness would compare the oracle instead of the product.

## Suite A — single-fact retrieval (12 planted facts, 162 files)

`hit1` = right file ranked first. `answered` = the answer string was reached. `tok` = median tokens to the answer.

| release | search exact hit1 · answered · tok | search **fuzzy** hit1 · answered · tok | semantic exact hit1 | read-all tok |
|---|---|---|---|---|
| `v0.2.0` | 12/12 · 12/12 · 728 | 4/12 · 5/12 · 363 | 4/12 | 28,312 |
| `v0.2.1` | 12/12 · 12/12 · 728 | 4/12 · 5/12 · 363 | 4/12 | 28,312 |
| `v0.3.0` | 12/12 · 12/12 · 728 | 4/12 · 5/12 · 363 | 4/12 | 28,312 |
| `v0.4.0` | 12/12 · 12/12 · 728 | 4/12 · 5/12 · 363 | 4/12 | 28,312 |
| `v0.5.0` | 12/12 · 12/12 · 728 | 4/12 · 5/12 · 363 | 4/12 | 28,312 |
| `v0.5.1` | 12/12 · 12/12 · 728 | 4/12 · 5/12 · 363 | 4/12 | 28,312 |
| `v0.5.2` | 12/12 · 12/12 · 729 | 4/12 · 5/12 · 363 | 4/12 | 28,312 |
| `v0.5.3` | 12/12 · 12/12 · 729 | 4/12 · 5/12 · 363 | 4/12 | 28,312 |
| `v0.5.4` | 12/12 · 12/12 · 729 | 4/12 · 5/12 · 363 | 4/12 | 28,312 |
| `v0.5.5` | 12/12 · 12/12 · 729 | 4/12 · 5/12 · 363 | 4/12 | 28,312 |
| `v0.5.6` | 12/12 · 12/12 · 729 | 4/12 · 5/12 · 363 | 4/12 | 28,312 |
| `v0.5.7` | 12/12 · 12/12 · 729 | 4/12 · 5/12 · 363 | 4/12 | 28,312 |

## Suite B — multi-field form filling (12 fields, 1 absent)

| release | fields covered | packet tokens | absent flagged honestly |
|---|---|---|---|
| `v0.2.0` | 8/11 | 1,464 | 0 |
| `v0.2.1` | 8/11 | 1,465 | 0 |
| `v0.3.0` | 8/11 | 1,338 | 1 |
| `v0.4.0` | 8/11 | 1,571 | 1 |
| `v0.5.0` | 8/11 | 1,708 | 1 |
| `v0.5.1` | 9/11 | 1,595 | 1 |
| `v0.5.2` | 10/11 | 1,433 | 1 |
| `v0.5.3` | 10/11 | 1,433 | 1 |
| `v0.5.4` | 10/11 | 1,433 | 1 |
| `v0.5.5` | 10/11 | 1,433 | 1 |
| `v0.5.6` | 10/11 | 1,433 | 1 |
| `v0.5.7` | 11/11 | 1,424 | 1 |

## Regressions between recorded releases

- v0.4.0 -> v0.5.0: suite B lost ['Payment terms']

Each line is a metric that went **down** from one release to the next. A line here is not automatically a bug — a deliberate trade belongs in the changelog — but it must never be a surprise.
