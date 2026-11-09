# Revision — QUALITY PANEL 12 blocking (2026-09-29)

FILL IN
- Task tree: `secure-performance-optimized-ml-inference-pipeline/`
- Return: panel-12 (post panel-18); Coherent/PGT/Det clean
- Last packaged: panel-18 zip

## Scope (one line each)

| # | Answer | Disposition |
|---|---|---|
| 1 | MAKE IT TRUE | Sealed `modes` + assert `S_IMODE` on every protected file |
| 2 | STOP PROMISING | Stale score/bench may be removed on failed selfcheck; only no score/bench invoc |
| 3,8 | MAKE IT TRUE | Verifier-owned `rebuild_race()` + 32 concurrent POSTs (drop env barrier) |
| 4 | MAKE IT TRUE | 9-row batch_limit=9 isolation |
| 5 | MAKE IT TRUE | `batch -1` / `batch 1.5` excluded beside good |
| 6 | MAKE IT TRUE | POST body `null` → INVALID_INTAKE 400 |
| 7 | MAKE IT TRUE | Multi-fault intake stderr/stdout identical ×2 |
| 9 | MAKE IT TRUE | Reject `charset =utf-8` / `charset= utf-8` |
| 10 | MAKE IT TRUE | String trailing-zero cancel before scale; util `1…0e-1000001` → micros 261 |
| 11 | MAKE IT TRUE | Unique `ridgeRebuildToken` const in ELF (proves invoked binary is this rebuild) |
| 12 | MAKE IT TRUE | `go_rebuilt_bin` / buildinfo require `/app/cmd/ridgecast` exact path |

## Files
- `solution/internal/scorekit/scorekit.go` — cancelTrailingPow10; removed env barrier
- `tests/holdout/sealed-expected.json` — `modes` map (0644)
- `tests/ridge_helpers.py` — MODES, rebuild_race, tighter provenance
- `tests/test_outputs.py` — fixtures #1–9,11–12

## Both-sides
| Probe | Reference | Wrong shape |
|---|---|---|
| chmod 0666 coef | mode assert fail | digest-only pass |
| rm stale on selfcheck fail | pass | required sentinel survive |
| -race + 32 OVLP | 200 | unsync map abort |
| 9-batch | 9 micros | 8-slot reuse |
| batch -1 / 1.5 | ledger 1/15/68 | admit bad |
| POST null | 400 INVALID_INTAKE | 500 |
| multi-fault ×2 | identical I/O | map-order churn |
| `1…0e-1000001` util | micros 261 | 977023 |
| swap prebuilt ELF | tok missing | build+exec decoupled |
| `./alternate/cmd/ridgecast` | go_rebuilt false | suffix match |

## Container evidence
- Oracle ×3 Mean **1.000** — `/tmp/harbor-oracle-kelso-p12b/2026-09-29__16-21-33`
- NOP ×1 Mean **0.000** — `/tmp/harbor-nop-kelso-p12b/2026-09-29__16-21-33`

## Author prose
Untouched (#2 dropped verifier-only overstrict sentinel survival; page already stop-on-selfcheck-fail).
