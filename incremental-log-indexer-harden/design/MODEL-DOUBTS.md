# Doubts

1. R13, edit distance with swaps. "The least number of edits that turn the one
   into the other", where an edit can swap two adjacent characters, can be read as:
   (a) true (unrestricted) Damerau-Levenshtein distance: any sequence of edits, so
       a swapped pair may be edited again later; or
   (b) optimal string alignment (restricted Damerau-Levenshtein): no substring is
       edited more than once.
   They differ inside the fuzz limit, e.g. `ca` vs `abc` is 2 under (a)
   (ca -> ac -> abc) and 3 under (b). So with fuzz 2 the query word `ca` matches
   the token `abc` only under (a).
   Implemented: (a). Read literally, the sentence asks for the least number of edits
   with no further restriction. Checked against a breadth-first search over edit
   sequences.

Everything else read one way only. Notes on readings that could be questioned:
- A query word never matches a stop-word token, even when the fuzz would allow it
  (R7: "no query word ever matches it").
- Joining an append "with a space between them" (R9) gives the same tokens as
  putting the two token lists one after the other, so nothing else is needed.
- R11 is checked against the working copy after every add, append and delete,
  rolled-back batches included.
