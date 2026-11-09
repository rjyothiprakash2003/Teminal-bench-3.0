# Revision note, return of version 3 (2026-10-13): Sound Verifier, 1 Major

| # | Finding | Disposition | Decided by | Before | After |
|---|---|---|---|---|---|
| 1 | token positions kept in eight bits, wrapping inside the 300-token range | BACKED | PHRASES_ACROSS_POSITIONS_128_AND_256 (R11): a 257-token document with phrases at positions 127/128 and 255/256 | mutants `r11_positions_wrap_at_256` (uint8) and `r11_positions_wrap_at_128` (int8) pass 16 of 16 | both fail R11 (and R15) |

Sibling of the same class, found by the session's sweep: offsets (position minus query
place) kept in a signed byte. An exact phrase wraps both offsets alike, so only a spread
across the boundary shows it. `r15_offsets_kept_in_int8` passed 16 of 16 before;
SPREAD_ACROSS_OFFSETS_128_AND_256 (R15, reversed phrases b a and d c across 128 and 256)
now refuses it. The unsigned-byte offset version was already refused by negative offsets.

Only tests/ changed. Chain after the last edit GREEN: 84 witnesses in 16 tests, 114 graded
journals in the domain and agreed by the independent model, 800 fuzzed with 0
disagreements, 75 mutants with 0 survivors, 5 tolerated trees pass, leak scan 0 pairs,
farm oracle 1, nop 0, lazy 0, hostile 0, thirdparty 0, multipackage 1.
