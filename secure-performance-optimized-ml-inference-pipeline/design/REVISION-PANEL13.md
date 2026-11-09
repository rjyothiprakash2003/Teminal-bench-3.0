# Revision — QUALITY PANEL 13 blocking (2026-09-29/30)

FILL IN
- Task tree: `secure-performance-optimized-ml-inference-pipeline/`
- Return: panel-13 (post p12c); Coherent/PGT/Det clean
- Last packaged: p12c zip / Harbor `/tmp/harbor-oracle-kelso-p12c`

## Scope (one line each)

| # | Disposition | Change |
|---|---|---|
| 1 | MAKE IT TRUE | Live schema also mutates util_ratio max 2.5→2.6; score/POST util 2.6 |
| 2 | STOP PROMISING | Contract: `--out` cleanup is regular file/symlink only; dirs/`.` left alone; removeOut matches; flipped stale_dir/cwd tests |
| 3 | MAKE IT TRUE | String-valued extra/forbidden keys in `illegal_rows` |
| 4 | MAKE IT TRUE | Single-hyphen score cases assert `not x.exists()` |
| 5 | MAKE IT TRUE | HTTP POST tenure 72 + util 0.36 → 364705 |
| 6 | MAKE IT TRUE | Delivered recovery + total_requests 22 → bench fail |
| 7 | MAKE IT TRUE | Recovery S=2,C=3 metrics with C≥S → bench fail |
| 8 | STOP PROMISING | `ridgecast_buildinfo_ok` accepts any module path ending in `cmd/ridgecast` |
| 9 | MAKE IT TRUE | Relative cwd distinguishes scale std, schema max, token_file |
| 10 | MAKE IT TRUE | `batch 9223372036854775807` admitted |
| 11 | MAKE IT TRUE | `big.Rat.Float64()` direct (no SetPrec(64) intermediate) |
| 12 | MAKE IT TRUE | HTTP reordered keys (`open_trades` first, `id` last) |
| 13 | MAKE IT TRUE | Barrier before cache critical section; GORACE=halt_on_error=1; assert no DATA RACE + server alive |

## Files
- `environment/kelso/policy/serving-contract.txt` + sealed digest
- `solution/cmd/ridgecast/ridgecast.go`, `solution/internal/scorekit/scorekit.go`
- `tests/ridge_helpers.py`, `tests/test_outputs.py`, `tests/holdout/sealed-expected.json`

## Both-sides (panel reach)
| Probe | Reference | Wrong shape |
|---|---|---|
| util max 2.6 + util 2.6 | accept | hardcode 2.5 reject |
| `--out .` bad intake | nonzero, cwd remains | (old) require delete /app |
| `"extra":"ignored"` | reject | number-only extra check |
| `-intake ... --out PATH` | PATH gone | leave stale |
| HTTP 72/0.36 | 364705 | first-field-only HTTP |
| total_requests 22 | bench fail | ignore nReq |
| C=3,S=2 ticks | bench fail | omit C<S |
| module rename | buildinfo ok | kelso.internal lock |
| relative scale std 48 | distinct micros | /app scale fallback |
| batch MaxInt64 | admit | `B < MaxInt64` reject |
| Rat→f64 | direct Float64 | double round via prec 64 |
| reordered HTTP JSON | 200 | order-sensitive HTTP |
| barrier+GORACE | 200, no DATA RACE | unsync map abort / silent |

## Container evidence
- Oracle ×3 Mean **1.000** — `/tmp/harbor-oracle-kelso-p13c/2026-09-30__00-06-14`
- NOP ×1 Mean **0.000** — `/tmp/harbor-nop-kelso-p13c/2026-09-30__00-06-14`

## Author prose
`serving-contract.txt` narrowed for #2 (directory/`--out .` absence). instruction.md + task.toml explanation fields untouched.
