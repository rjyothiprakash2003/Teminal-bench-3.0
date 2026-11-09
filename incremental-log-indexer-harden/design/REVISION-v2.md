# Revision note, return of version 1 (2026-10-13): Sound Verifier, 8 Majors

Every finding was measured through the verifier's own tests before and after, with the
panel's wrong program built as an exact patch on the reference (design/mutants.py) or a
farm tree (design/farm-trees/). All eight were BACKED (made true); none removed. No
page, example, stub or reference changed; one assertion and 13 witnesses were added.

| # | Finding | Disposition | Decided by | Before | After |
|---|---|---|---|---|---|
| 1 | third-party package through a local replace or vendor | BACKED: after the build, `go list -deps` as the sandbox user; any package neither standard nor in the submission's own module fails the build step | build fixture, every test | farm tree `thirdparty` reward 1 (16 of 16) | reward 0, "packages outside the Go standard library: example.com/minx"; the `multipackage` tree (an internal package of its own module) still reward 1 |
| 2 | separator list without `#` | BACKED | EVERY_PRINTABLE_MARK_SEPARATES_TOKENS (R6): all 32 printable marks between words in a document and in queries | passes 16 of 16 | fails R6 (2 witnesses) |
| 3 | separator list without `!` | BACKED | same | passes 16 of 16 | fails R6 (2 witnesses) |
| 4 | apostrophes kept in tokens | BACKED | same, and APOSTROPHE_SEPARATES_TOKENS (R6) | passes 16 of 16 | fails R6 (2 witnesses) |
| 5 | a tokenless append takes a position | BACKED | APPEND_WITH_NO_TOKEN_TAKES_NO_POSITION (R9) | passes 16 of 16 | fails R9 (2 witnesses) |
| 6 | append text cut at 199 characters | BACKED | APPEND_TEXT_OF_TWO_HUNDRED_CHARACTERS (R3) | passes 16 of 16 | fails R3 |
| 7 | at most 300 appends a document | BACKED | MORE_THAN_THREE_HUNDRED_APPENDS (R9), 301 appends | passes 16 of 16 | fails R9 |
| 8 | fuzzy comparison refused past 32 characters | BACKED | LONG_TOKENS_ARE_COMPARED_IN_FULL (R13): 33- and 102-character tokens, exact, one edit and two edits | passes 16 of 16 | fails R13 |

Found by the session's own sweep of the same class (domain extremes a capped or listed
implementation never meets), each measured the same way, each passing 16 of 16 before:

| Sibling | Witness (rule) | After |
|---|---|---|
| an id must start with a letter | IDS_MAY_START_WITH_A_DIGIT_OR_A_HYPHEN (R3) | fails R3 |
| at most 100 ids ever used | MORE_THAN_ONE_HUNDRED_IDS_OVER_TIME (R11) | fails R11 |
| tokens cut at 64 characters | LONG_TOKENS_ARE_KEPT_WHOLE (R6) | fails R6 |
| a query read to 8 tokens before stop words drop | MANY_STOP_WORDS_BEFORE_THE_WORDS (R12) | fails R12 |
| no fuzz for words no longer than the fuzz | SHORT_WORDS_ARE_FUZZY_TOO (R13) | fails R13 |
| candidate tokens capped at 100 a word | OCCURRENCE_BEYOND_THE_HUNDREDTH (R14) | fails R14 |
| an empty batch rolled back | EMPTY_BATCH_ROLLED_BACK (R5) | covered |

Probed and already caught: the first letter of a fuzzy match must agree (fails R13).

Over-strictness, after the round: reference control PASS; the 5 tolerated trees (four
independently written programs and a stderr variant) pass all 79 witnesses; the
multi-package tree passes the dependency check. Cells unchanged: each victim still fails
only its two owner tests, and the lazy tree fails the same four.

Chain after the last edit: GREEN (design/CHAIN-LOG.txt). 79 witnesses in 16 tests, 109
graded journals in the domain and agreed by the independent model, 800 fuzzed journals
with 0 disagreements, 67 mutants with 0 survivors, leak scan 0 pairs, farm oracle 1,
nop 0, lazy 0, hostile 0, thirdparty 0, multipackage 1.

Slots: version 2 of 8 used by this upload, 6 left after it.
