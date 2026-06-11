# docdex task benchmark (Suite B — form filling)

Corpus: **115 files**, one vendor onboarding form with 12 fields (11 answerable in the corpus, 1 deliberately absent). Budget 3000 tokens. Deterministic (seed 7); token counts via chars/4 estimate.

Reading the entire corpus costs ~30,635 tokens.

| method | fields covered | absent flagged honestly | tokens used |
|---|---|---|---|
| read-all (budget) | 0/11 | n/a | 203 |
| search-loop | 11/11 | n/a | 20,228 |
| docdex context | 8/11 | 1/1 | 1,338 |

Headline: `docdex context` delivered **8/11** answerable fields in **1,338 tokens** — vs the search-loop's 20,228 tokens (it reads whole multi-page files) for 11/11, and read-all's 0/11 once its budget is gone. Only `docdex context` also reports the field with no evidence as **not found** (1/1) instead of forcing the agent to guess. So: ~73% of the findable context at ~7% of the search-loop's token cost, with an honesty signal the others can't give.

## The honest part: which fields miss, and why

These are not bugs — they are the known limits of lexical-only retrieval that the v0.3 roadmap targets (field-alias registry, stemming/synonyms, reranking):

- **Legal name**: the corpus never says "legal name" — the value is under "...as the Vendor" (needs a field-alias registry).
- **Governing law**: a short distractor containing "governing law" out-ranks the long real contract ("governed by the laws of...") — needs stemming + length-aware reranking.
- **Renewal term**: same shape — "renewal term" the phrase loses to distractors while the value sits deep in a large PDF.

Notably, docdex does **not** fabricate these — it lists them under `## Missing` so the agent knows to look further, which is the safe behavior.

## Example packet (excerpt)
```
# context packet
Task: fill the vendor onboarding form
Coverage: 12 fields · 8 found · 0 weak · 4 missing
Budget: 3000 requested · ~1176 used (≈ chars/4) · 1824 free
Index: indexed 2026-06-11 21:38 — not re-checked (run `docdex status` to find new files)

## Answers
- GST number: GST number 29ABCDE1234F1Z5 Liability acceptance pursuant obligations confidentiality whereas jurisdiction whereas obligations agreement hereto milestone.  [Archive/Final_v3_USE.xlsx ·1]
- PAN: PAN ABCDE1234F Remedy acceptance term parties remedy notwithstanding whereas milestone party clause obligations milestone covenant.  [Archive/Final_v3_USE.xlsx ·2]
- Registered address: Registered address Tower B, Bengaluru 560042 Covenant vendor warranties party annexure schedule notwithstanding confidentiality whereas term remedy delivery.  [Archive/Final_v3_USE.xlsx ·4]
- Liability cap: The aggregate liability cap under this agreement is INR 6.5 crore.  [Misc/document1 (4).pdf ·3]
- Payment terms: Payment terms are net-45 from the date of invoice.  [Contracts/scan_8841 copy.docx ·6]
- Primary contact email: Primary contact: ops@helios-components.example for escalations.  [Operations/notes_final.md ·3]
- Annual contract value: Annual contract value INR 1.8 crore Confidentiality whereas delivery term delivery parties provision vendor acceptance herein schedule delivery term.  [Finance/untitled (5).xlsx ·1]
- Effective date: 1 April 2026.  [Contracts/scan_8841 copy.docx ·13]

```
