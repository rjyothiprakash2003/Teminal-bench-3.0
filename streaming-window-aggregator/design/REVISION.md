# Revision round 21 — quality panel (2 blocking)

## FILL IN FIRST

- **Task tree:** `streaming-window-aggregator/`
- **Return:** Quality Panel NEEDS REVISION — 2 blockers (Coherent Contract Major + Sound Verifier Major). Clean: Correct Reference, PGT, Deterministic.
- **Last package:** round-20 zip ~148075 B.

## Establish-before

| # | Already closed? | Citation resolves? |
|---|---|---|
| 1–2 slide=2^53−1 success vs graded ≤10_000 | **No** — round-20 added `slide_max_bound_accepted` ×2 | yes — Limits + instruction permit assume ≤10_000 |

Before-cure (220 tests): `1 <= slide <= 10_000` refuse-larger mutant **fails exactly** `slide_max_bound_accepted` + `_second_site` → REWARD=0.

## Scope

| # | Earns place? | Answer |
|---|---|---|
| 1–2 Success at slide=2^53−1 | **No** — contradicts graded-domain guarantee | **STOP PROMISING** (delete both success fixtures) |

Instruction / explanations / Limits text untouched (already correct). Reference unchanged. Schema refuse at `slide == 2**53` retained.

## Dispositions + both-sides

| # | Change | Fixture | Measurement (218 suite) |
|---|---|---|---|
| 1–2 | delete over-strict success | removed `slide_max_bound_accepted` ×2 | `slide <= 10_000` mutant: **REWARD=1**; widen-to-include-2^53: still **fails** `bad_config_slide_upper_bound` |

Over-strictness: cured by deletion (companion: delete assert; do not add replacements).

## Companion audit (residual)

1. Graded domain (≤10_000) and JSON schema (`[1, 2^53)`) remain distinct: graded success stays inside Limits; refuse at `2**53` still grades the half-open schema upper bound.
2. Not extended: rivals.py; no new success at schema-max slide.

## Five-axis table

| Axis | Criterion | Mark | Evidence |
|---|---|---|---|
| 1 Coherent | Rules on page | GREEN **cured** | Limits + instruction assume ≤10_000; no success outside |
| 1 | Over-strict | GREEN **cured** | slide≤10k mutant PASS |
| 2 Reference | Ordinary | GREEN | oracle **218 REWARD=1** |
| 3 PGT | Leak | GREEN | leak 0 |
| 4 Sound | Coverage | GREEN | refuse@2^53 retained; false reject removed |
| 4 | Plausible wrong | GREEN | graded-bound reliance accepted |
| 5 Deterministic | Fixtures | GREEN | chain + oracle stable |

## Before → After

| Item | Returned | Now | Measurement |
|---|---|---|---|
| slide_max success ×2 | over-strict / incoherent | **deleted** | slide≤10k → REWARD=1 |
| slide refuse @ 2^53 | kept | kept | widen → still fails |

## Underneath

1. Carried: round-20 watermark / huge-val / nan-ts visibles; rivals.py.
2. Before submit: zip rebuild.
3. Container: oracle 218/REWARD=1; NOP REWARD=0.
