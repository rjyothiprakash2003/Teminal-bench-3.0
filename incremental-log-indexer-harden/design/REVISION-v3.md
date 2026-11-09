# Revision note, return of version 2 (2026-10-13): Sound Verifier, 1 Major

| # | Finding | Disposition | Decided by | Before | After |
|---|---|---|---|---|---|
| 1 | tokens cut to the 120-character query-text limit | BACKED | TOKEN_LONGER_THAN_A_QUERY_TEXT_IS_KEPT_WHOLE (R6): a 121-character token against a 120-character query at fuzz 0 (no hit), fuzz 1 (hit), and a 200-character token at fuzz 2 (no hit) | mutants `r6_tokens_cut_at_120` and `r6_tokens_cut_at_122` pass 16 of 16 | both fail R6 |

Why this closes the class: a query token is at most 120 characters and the fuzz at most 2,
so a cap of 123 or more can change no output; caps of 120 to 122 are refused by the new
witness and shorter caps by LONG_TOKENS_ARE_KEPT_WHOLE and LONG_TOKENS_ARE_COMPARED_IN_FULL.

Siblings found by the session's sweep of the same class (a buffer shorter than a stated
limit), each passing 16 of 16 before:

| Sibling | Witness (rule) | After |
|---|---|---|
| a line cut at 220 characters (the longest add and append lines are 221 and 224) | LONGEST_ADD_AND_APPEND_LINES (R3) | fails R3 |
| the first line cut at 256 characters (20 stop words of 12 letters make 269) | LONGEST_STOP_WORD_LINE (R1, R2) | fails R1 and R2 |

Only tests/ changed. Chain after the last edit GREEN: 82 witnesses in 16 tests, 112 graded
journals in the domain and agreed by the independent model, 800 fuzzed with 0
disagreements, 71 mutants with 0 survivors, 5 tolerated trees pass, leak scan 0 pairs,
farm oracle 1, nop 0, lazy 0, hostile 0, thirdparty 0, multipackage 1.
