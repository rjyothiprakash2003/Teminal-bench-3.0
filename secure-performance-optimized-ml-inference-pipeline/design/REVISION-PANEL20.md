# Revision — QUALITY PANEL 20 blocking (2026-09-28)

FILL IN
- Task tree: `secure-performance-optimized-ml-inference-pipeline/`
- Return: panel-20 (UID 97fa78d0…, 28/09/2026 17:17)
- Last packaged: zip 16:30 (panel-26 cure). Citations resolve; none already closed.

## Scope (one line each)

| # | Answer | Disposition |
|---|---|---|
| 1–6,11,14 | STOP PROMISING | Repeat must be **positive** (zero not admitted). Reference already skipped 0; page now matches. Cuts the zero-repeat loop from panel-26 “non-negative”. |
| 2 | MAKE IT TRUE | `exactInRange` via `big.ParseFloat` before float64 scoring |
| 7 | MAKE IT TRUE | `--bogus` then `--out=PATH` cleanup |
| 8 | MAKE IT TRUE | Live `inq_6` coef + std mutations |
| 9 | MAKE IT TRUE | Live `mean delinq_24` shift; selfcheck still ok |
| 10 | MAKE IT TRUE | Shared-id cache varies `inq_6` / `revol_bal` / `open_trades` |
| 12 | MAKE IT TRUE | Giant S/C with percentiles equal C |
| 13 | MAKE IT TRUE | Equal-C recovery S=16,C=12 |
| 15 | MAKE IT TRUE | Reject `charset=UTF-8` |
| 16 | MAKE IT TRUE | `score -intake`, `bench -out` |
| 17 | MAKE IT TRUE | Seed `delinq_24`/`inq_6`/`revol_bal`/`open_trades` |
| 18 | MAKE IT TRUE | Illegal corpus includes `false` |
| 19 | MAKE IT TRUE | `ledger_obj` counts raw `"key"` occurrences == 1 |
| 20 | KEEP AMBER | Concurrent pool without forced barrier (scheduling-bound) |

## Files
- `environment/kelso/policy/serving-contract.txt` — positive repeat; digest `f08a5f09…`
- `solution/internal/scorekit/scorekit.go` — exactInRange
- `tests/ridge_helpers.py` — ledger duplicate-key count; bool false rows
- `tests/test_outputs.py` — #7–18 coverage
- `tests/holdout/sealed-expected.json` — reminted

## Container evidence
- Oracle ×3 Mean **1.000** — `/tmp/harbor-oracle-kelso-p20/2026-09-28__17-28-02`
- NOP ×1 Mean **0.000** — `/tmp/harbor-nop-kelso-p20/2026-09-28__17-31-26`

## Author prose
Instruction / task.toml explanations untouched. Quality-check list in return was all PASS.
