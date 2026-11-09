# Revision — QUALITY PANEL 15 blocking (2026-09-30)

FILL IN
- Task tree: `secure-performance-optimized-ml-inference-pipeline/`
- Return: panel-15 (post p13c + static/flake); Coherent/PGT/Det prior clean; New: CC×3 + SV×12
- Last packaged: p13c / flake-postfix zip

## Scope (one line each)

| # | Disposition | Change |
|---|---|---|
| 1 | STOP PROMISING | `-out` alone: nonzero+clean only; no require path gone (contract is `--out` forms) |
| 2 | MAKE IT TRUE | Contract: live off-mean must equal 364705 or selfcheck fails (drop contradictory pin exception) |
| 3 | MAKE IT TRUE | Contract: every HTTP response incl. NOT_FOUND sets Content-Type application/json |
| 4,9,11 | STOP PROMISING | Drop cache/mutex source needles + barrier injection; behavioral -race concurrent POSTs only |
| 5,8 | MAKE IT TRUE | `stale_symlink` + gone() lexists; dangling symlink --out removed on fail |
| 6 | MAKE IT TRUE | `run_ci_gate` timeout back to 180s |
| 7 | MAKE IT TRUE | HTTP POST util `1`+4096 zeros+`e-4096` accept |
| 10 | MAKE IT TRUE | Delivered recovery + p95=12 alone → bench fail |
| 12 | MAKE IT TRUE | `bench --spec` no --out from `/work/rel-assets` → `out/bench-ledger.json` there |
| 13 | MAKE IT TRUE | Dual unknown keys `extra_a`/`extra_b` ×2 identical stderr; Parse sorts keys |
| 14 | MAKE IT TRUE | bound_rows fractional tenure/revol_bal/open_trades |
| 15 | MAKE IT TRUE | selfcheck with `batch_limit -1` fails |

## Files
- `environment/kelso/policy/serving-contract.txt` + sealed digest
- `solution/internal/scorekit/scorekit.go` (sorted unknown-key walk)
- `tests/ridge_helpers.py`, `tests/test_outputs.py`

## Container evidence
- Oracle ×3 Mean **1.000** — `/tmp/harbor-oracle-kelso-p15/2026-09-30__02-32-51`
- NOP ×1 Mean **0.000** — `/tmp/harbor-nop-kelso-p15/2026-09-30__02-32-51`

## Author prose
`serving-contract.txt` updated for #2 and #3. instruction.md / task.toml explanations untouched.
