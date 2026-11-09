# Revision — QUALITY PANEL 18 blocking (2026-09-29)

FILL IN
- Task tree: `secure-performance-optimized-ml-inference-pipeline/`
- Return: panel-18 (post panel-15); Coherent/PGT/Det clean; Oracle/NOP already passed for prior package
- Last packaged: panel-15 zip. Citations resolve.

## Scope (one line each)

| # | Answer | Disposition |
|---|---|---|
| 1 | STOP PROMISING | Drop rebuild-token env inheritance as gate proof; keep ELF/buildinfo/strace |
| 2 | MAKE IT TRUE | HTTP POST `0e100001` tenure accepts (parity with CLI ZHI) |
| 3 | MAKE IT TRUE | `--bogus --out PATH` (split) gone |
| 4 | MAKE IT TRUE | Empty intake → rc=0, empty product |
| 5,17 | MAKE IT TRUE | Score live means inq_6/revol_bal/open_trades (off-mean + at-mean) |
| 6 | MAKE IT TRUE | Selfcheck tiny tenure/util mean shifts still pin 364705 |
| 7 | MAKE IT TRUE | `batch 768614336404564651` ticks above MaxInt64 |
| 8 | MAKE IT TRUE | `batch 2305843009213693936` memory_units = 2^63 |
| 9 | MAKE IT TRUE | CT `application/json;\tcharset=utf-8` 200 |
| 10 | MAKE IT TRUE | Empty-loadspec failure stdout/stderr byte-identical ×2 |
| 11 | MAKE IT TRUE | `RIDGE_CACHE_BARRIER` forces 8-way ScoreOne overlap |
| 12 | MAKE IT TRUE | bench+serve accept batch_limit 2^63 |
| 13 | MAKE IT TRUE | `--out .` / `--out=.` cwd removed on failure |
| 14 | MAKE IT TRUE | 4 rows / batch_limit 3 residual chunk |
| 15 | MAKE IT TRUE | jsonNumberRat: no unbounded 10^|exp|; extreme `1e-10^20` accepts |
| 16 | MAKE IT TRUE | lstat protected top dirs are real directories (not symlinks) |
| 18 | MAKE IT TRUE | TRACE on known/unknown routes |

## Files
- `solution/internal/scorekit/scorekit.go` — extreme-exp shortcut; cache barrier
- `tests/test_outputs.py` — #1–14,16–18 fixtures

## Both-sides
| Probe | Reference | Wrong shape |
|---|---|---|
| gate env -i | pass (no token assert) | token-in-I/O required |
| HTTP 0e100001 | 200 | HTTP-only exp cap |
| empty intake | empty out | treat as error |
| 1e-10^20 tenure | score rc=0 | Exp hang/OOM |
| cwd --out . | dir gone | RemoveAll only |
| barrier OVLP | 200×8 | unsync map crash |

## Container evidence
- Oracle ×3 Mean **1.000** — `/tmp/harbor-oracle-kelso-p18/2026-09-29__13-45-42`
- NOP ×1 Mean **0.000** — `/tmp/harbor-nop-kelso-p18/2026-09-29__13-45-42`

## Author prose
Untouched (token env was verifier-only overstrict; page already exact-range / batch_limit / determinism).
