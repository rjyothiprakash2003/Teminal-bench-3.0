# Revision note, return of version 5 (2026-10-13): Sound Verifier, 1 Major

| # | Finding | Disposition | Decided by | Before | After |
|---|---|---|---|---|---|
| 1 | posting list capped at 256 occurrences | BACKED | THE_LAST_OF_299_OCCURRENCES (R14): 299 occurrences of `a`, then `b`; `a b` needs the 299th, `a a a a a b` the last five | `r14_occurrences_capped_at_256` passes 16 of 16 | fails R14 |

The loop of 59.5 c (Prompt B: the same axis failing with a new finding every round), so
this round closed the capacity class whole: for every count the notes let grow, one witness
puts the deciding element at the largest legal index, which refuses every smaller cap.

| Count | Witness (rule) | Probe before | After |
|---|---|---|---|
| occurrences of one word (up to 299) | THE_LAST_OF_299_OCCURRENCES (R14) | cap 256 passes everything | fails R14 |
| raw query tokens (up to 56 within 120 characters) | WORDS_AFTER_FIFTY_FOUR_STOP_TOKENS (R12) | cap 32 passes everything | fails R12 |
| queries in a journal (up to 395 here) | THREE_HUNDRED_NINETY_FIVE_QUERIES (R17) | cap 256 passes everything | fails R17 |
| batches in a journal (132 here) | ONE_HUNDRED_THIRTY_TWO_BATCHES (R4, R5) | cap 128 passes everything | fails R4 and R5 |
| ids used over time (196 here) | ONE_HUNDRED_NINETY_SIX_IDS_OVER_TIME (R11) | cap 192 passes everything | fails R11 |
| appends to one document (394 here) | THREE_HUNDRED_NINETY_THREE_APPENDS (R9) | cap 350 passes everything | fails R9 |
| tokens in the index (30000) | ONE_HUNDRED_FULL_DOCUMENTS (R11) | cap 16384 passes everything | fails R11 |

Already at their maximum before this round: 400 lines, 300 tokens a document, 100 live
documents, 100 hits, 20 stop words, 6 query words, 200- and 120-character texts and their
longest lines, positions and offsets across 128 and 256.

Only tests/ changed. Chain after the last edit GREEN: 92 witnesses in 16 tests, 122 graded
journals in the domain and agreed by the independent model, 800 fuzzed with 0
disagreements, 90 mutants with 0 survivors, 5 tolerated trees pass, leak scan 0 pairs,
slowest graded reference run 0.015 s, farm oracle 1, nop 0, lazy 0, hostile 0,
thirdparty 0, multipackage 1.
