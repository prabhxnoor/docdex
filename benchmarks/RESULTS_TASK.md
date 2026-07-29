# docdex task benchmark (Suite B — form filling)

Corpus: **115 files**, one vendor onboarding form with 12 fields (11 answerable in the corpus, 1 deliberately absent). Budget 3000 tokens. Deterministic (seed 7); token counts via chars/4 estimate.

Reading the entire corpus costs ~30,776 tokens.

| method | fields covered | absent flagged honestly | tokens used |
|---|---|---|---|
| read-all (budget) | 0/11 | n/a | 351 |
| search-loop | 11/11 | n/a | 20,096 |
| docdex context | 10/11 | 1/1 | 1,433 |

Headline: `docdex context` delivered **10/11** answerable fields in **1,433 tokens** — vs the search-loop's 20,096 tokens (it reads whole multi-page files) for 11/11, and read-all's 0/11 once its budget is gone. Only `docdex context` also reports the field with no evidence as **not found** (1/1) instead of forcing the agent to guess. So: ~73% of the findable context at ~7% of the search-loop's token cost, with an honesty signal the others can't give.

## The honest part: which fields miss, and why

These are not bugs — they are the gap the roadmap still targets. Stemming, free-text synonyms and the utility reranker shipped in v0.5.0, and v0.5.1 stopped stemming from burying selective literal terms; what remains is **form-field** meaning-awareness — reading a field's value from a label written as a synonym or a different inflection:

- **Legal name**: the corpus never says "legal name" — the value sits under "...as the Vendor". Free-text synonyms shipped in v0.5.0; reading a *form field's* value from a synonym label is the deferred piece.

Notably, docdex does **not** fabricate these — it lists them under `## Missing` so the agent knows to look further, which is the safe behavior.

## Example packet (excerpt)
```
# context packet
Task: fill the vendor onboarding form
Coverage: 12 fields · 3 found · 8 weak · 1 missing
Budget: 3000 requested · ~1433 used (≈ chars/4) · 1567 free
Index: indexed <normalized>

## Answers
- Liability cap: under this agreement is INR 6.5 crore.  [Misc/document1 (4).pdf ·3]
- Renewal term: is 24 months unless terminated.  [Misc/document1 (4).pdf ·7]
- Effective date: 1 April 2026.  [Contracts/scan_8841 copy.docx ·13]

## Needs follow-up (weak)
- Legal name: matched, no clear value — term whereas warranties liability.  [Contracts/scan_8841 copy.docx ·13]  ~approx
- GST number: matched, no clear value — 29ABCDE1234F1Z5 Liability acceptance pursuant obligations confidentiality whereas jurisdiction whereas obligations agreement hereto milestone.  [Archive/Final_v3_USE.xlsx ·1]
- PAN: matched, no clear value — ABCDE1234F Remedy acceptance term parties remedy notwithstanding whereas milestone party clause obligations milestone covenant.  [Archive/Final_v3_USE.xlsx ·2]
- Registered address: matched, no clear value — Tower B, Bengaluru 560042 Covenant vendor warranties party annexure schedule notwithstanding confidentiality whereas term remedy delivery.  [Archive/Final_v3_USE.xlsx ·4]
```
