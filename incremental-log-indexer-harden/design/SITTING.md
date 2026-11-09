# Early sittings (KT-26 64 d), 2026-10-13

Writers: four Claude Code subagents a sitting (Opus 5.5, the session's own allowance),
each alone in a room holding only instruction.md, /app/docs, /app/examples and the stub,
building Go through the pinned golang:1.24 image. Graded by hand on telling inputs, then
by `design/grade.py` on the whole graded surface. These writers are not the platform's
Opus 5 and GPT-5.6; the figures are a screen, never a measurement of the gate.

## Card 1 (this task): journal, analysis and phrase search

| Sitting | Page | Cell | Writing | Wrong of 4 | Minutes a writer |
|---|---|---|---|---|---|
| 1 | first draft (exact match) | A: query stop words keep positions | GLOSS on R12 | 0 | 1.2 to 2.4 |
| 1 | | B: a repeated word takes two tokens | GLOSS on R13 | 0 | |
| 1 | | C: append after a trailing stop word | GLOSS on R9 | 0 | |
| 1 | | open method: nearest-token greedy | stand-in | 0 | |
| 2 | rewrite (fuzz added) | 1: fuzzy word never lands on a stop token | GLOSS on R7 at the join with R14 | 0 | 6.1 to 10.7 |
| 2 | | 2: least edits with swaps (true Damerau-Levenshtein) | open method, silent cell | 0 | |

Every sitting-2 program passes all 66 named witnesses and all 30 held-out journals; they
are now the over-strictness guard's tolerated trees (design/tolerated/). By 64 d and 64.1
step 4 both cells are dropped, and no third sitting is held on this page. The
independent builder named one doubt (R13: unrestricted against restricted edit
distance); it was cured with "The edits are made one after another, each on the text that
the one before it left", which states neither the link nor the outcome.

## Card 2 (tried and set aside): syslog year inference with a lag what-if

Kept in design/card2-syslog-year/ (page, reference, examples, witnesses, mutants, model).

| Sitting | Cell | Wrong of 4 | Note |
|---|---|---|---|
| 1 | W: second lag "were its batch received at the query's instant" | 4 | the builder also read it the same way, no doubt raised |
| 1 | G: Feb 29 read in a leap year, nearest year beyond one either side | 0 | |
| 2 (R8 cured) | W | 4 | a fresh builder again read it the writers' way |
| 2 | G | 0 | |

W bit only because every reader (8 writers and 2 page-only builders) read the page the
other way: by 63.7 a that is a page defect, not a silent cell; graded, it would draw a
coherent_contract Major and fail Solvable. On the author's word (2026-10-13) card 1 was
restored and finished.
