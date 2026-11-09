# Five-axis table — revision round 21 (2 blocking)

## Axis marks

| Axis | Criterion | Mark | Evidence |
|---|---|---|---|
| 1 Coherent | Rules on page | GREEN **cured** | Limits ≤10_000 + instruction assume; no success outside |
| 1 | Over-strict | GREEN **cured** | slide≤10k mutant **REWARD=1**; overstrict failures **0** |
| 2 Reference | Ordinary case | GREEN | oracle **218/218 REWARD=1** |
| 3 PGT | Leak / sandbox | GREEN | identical-byte pairs **0** |
| 4 Sound | Isolating coverage | GREEN | refuse@2^53 retained; schema-max success **deleted** |
| 4 | Plausible wrong | GREEN | graded-bound reliance accepted |
| 4 | Rivals pack | GREEN | **6 rivals, survivors 0** |
| 5 Deterministic | Stable grading | GREEN | chain GREEN |

## Before → After

| Criterion | Returned | Now | Measurement |
|---|---|---|---|
| F1–F2 slide=2^53−1 success | over-strict / incoherent | **deleted** ×2 | slide≤10k → **REWARD=1** |
| slide refuse @ 2^53 | kept | kept | widen → fails `bad_config_slide_upper_bound` |

## Underneath

1. Carried: round-20 watermark / huge-val / nan-ts; rivals.py.
2. Before submit: zip rebuild.
3. Container: chain GREEN; oracle REWARD=1; NOP REWARD=0.
