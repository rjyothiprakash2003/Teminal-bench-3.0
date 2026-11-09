# Five-axis table — panel-12 cure sitting 2026-09-29

## Axis 1 — Coherent Contracts
| Criterion | Mark | Evidence |
|---|---|---|
| No over-strict | GREEN cured | #2 stale product survival dropped |
| Carry | GREEN | Panel Coherent clean |

## Axis 2 — Correct Reference Solution
| Criterion | Mark | Evidence |
|---|---|---|
| Extreme sci mantissa | GREEN cured | #10 cancelTrailingPow10 → micros 261 |
| Harbor | GREEN | Oracle×3 Mean 1.000 |

## Axis 3 — Protected Ground Truth
| Criterion | Mark | Evidence |
|---|---|---|
| Carry | GREEN | Panel PGT clean |

## Axis 4 — Sound Verifier
| Criterion | Mark | Evidence |
|---|---|---|
| Modes | GREEN cured | #1 sealed modes |
| Concurrency | GREEN cured | #3/#8 rebuild_race + 32 OVLP |
| Batch 9 / batch -1,1.5 / null / multi-fault / CT spaces | GREEN cured | #4–7,#9 |
| Gate provenance | GREEN cured | #11 stamp in ELF; #12 exact /app/cmd/ridgecast |

## Axis 5 — Deterministic Execution
| Criterion | Mark | Evidence |
|---|---|---|
| Stable | GREEN | Oracle 3/3; multi-fault I/O identical |

## Before → After (moved cells)
| Cell | Before | After | Measurement |
|---|---|---|---|
| SV concurrency | env-var barrier | -race rebuild | unsync map fails under race |
| Ref extreme sci | mantissa discarded | cancel zeros | util=1 → 261 not 977023 |
| Gate stamp | absent / deadstripped | init-kept const | tok in ELF |
| Stale sentinel | required survive | optional absent | conventional rm gate passes |

## Harbor
- Oracle: `/tmp/harbor-oracle-kelso-p12b/2026-09-29__16-21-33` Mean 1.000
- NOP: `/tmp/harbor-nop-kelso-p12b/2026-09-29__16-21-33` Mean 0.000
