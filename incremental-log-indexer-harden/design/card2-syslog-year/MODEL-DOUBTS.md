# Doubts

1. Word limit on `lag` queries. R11 states "`<word>` is 1 to 32 lowercase letters and digits" for `count`;
   R13 says the lag query matches `<word>` "(as R11 reads it)", and R16 names "the limits of R11".
   - Reading A: the 1..32 lowercase/digit limit applies only to `count` words.
   - Reading B: it applies to `lag` words too, through "as R11 reads it".
   Implemented: B (the lag word is checked as well).

2. Year range on query instants. R4 says "the year of a written instant is from 2001 to 2097".
   - Reading A: this covers every written instant (batch, count, lag).
   - Reading B: it covers only batch instants.
   Implemented: A ("written instant" covers all of them; R8's escape clause is only for line years found by inference).

3. R15 "the index never holds more than 500 lines". With at most 500 log lines in all, the index cannot
   exceed 500 anyway. It is checked during replay, after each filing, but it can never fire on its own.

4. The pid in a tag ("1 to 7 digits"): leading zeros are not forbidden, so `x[007]` is accepted.
