# Revision note, return of version 4 (2026-10-13): Sound Verifier, 1 Major

| # | Finding | Disposition | Decided by | Before | After |
|---|---|---|---|---|---|
| 1 | lowercasing that stops one short of Z | BACKED | EVERY_LETTER_AND_DIGIT_AT_BOTH_ENDS_OF_ITS_RANGE (R6): whole-alphabet tokens in both cases, all ten digits, single-character documents A, Z, a, z, 0, 9, queried in both cases | `r6_lowercasing_stops_before_Z` passes 16 of 16 | fails R6 |

The loop of 59.5 c showed (one narrow coverage finding a round), so this round closed R6's
whole input space at once: every boundary of every character class. Measured before and
after through the R6 test alone:

| Wrong program | Before (R6 witnesses failed) | After |
|---|---|---|
| lowercasing stops before Z | 0 (passes everything) | 1 |
| capitals end before Z | 0 (passes everything) | 1 |
| letters end before z | 0 (caught only incidentally elsewhere) | 1 |
| digits end before 9 | 0 (caught only incidentally elsewhere) | 1 |
| digits start after 0 | 0 (caught only incidentally elsewhere) | 1 |
| lowercasing starts after A, capitals start after A, letters start after a | already caught by R6 | caught |

With EVERY_PRINTABLE_MARK_SEPARATES_TOKENS (every printable mark as a separator, which
includes each neighbour of the letter and digit ranges: / : @ [ ` {), every printable ASCII
character now has an R6 witness for its class.

Only tests/ changed. Chain after the last edit GREEN: 85 witnesses in 16 tests, 115 graded
journals in the domain and agreed by the independent model, 800 fuzzed with 0
disagreements, 83 mutants with 0 survivors, 5 tolerated trees pass, leak scan 0 pairs,
farm oracle 1, nop 0, lazy 0, hostile 0, thirdparty 0, multipackage 1.
