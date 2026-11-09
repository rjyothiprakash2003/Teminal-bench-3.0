# Revision — QUALITY PANEL 15 blocking (2026-09-29)

FILL IN
- Task tree: `secure-performance-optimized-ml-inference-pipeline/`
- Return: panel-15 (post panel-13b); PGT / Coherent / Det clean; Oracle/NOP already passed on platform for prior package
- Last packaged: panel-13b zip. Citations resolve (scorekit jsonNumberRat; test_outputs range/bench sites).

## Scope (one line each)

| # | Answer | Disposition |
|---|---|---|
| 1–5 | MAKE IT TRUE | Drop undocumented ±100000 sci-exp cap; zero-mantissa short-circuit; accept `0e100001` / `1e-100001` when exact value in range |
| 6 | MAKE IT TRUE | EDGEU200 (2.5 + 200 zeros + 1) CLI+HTTP reject — beats 512-bit Float round |
| 7 | MAKE IT TRUE | HTTP POST `tenure_months:-1e-400` → INVALID_INTAKE (parity with CLI) |
| 8 | MAKE IT TRUE | Score under live `mean util_ratio 0.36` with util 0.35 |
| 9 | MAKE IT TRUE | Selfcheck fails when `w tenure_months` doubles (live coef for off-mean lab) |
| 10 | MAKE IT TRUE | `scenario coal repeat 9223372036854775808 batch 2` ledger asserts |
| 11 | MAKE IT TRUE | `batch 129` admitted (no silent 128-cap) |
| 12 | MAKE IT TRUE | Recovery anchor p95=12 p99=15 (coal 19 + solo) → singleton ticks=15 |
| 13 | MAKE IT TRUE | Solo-then-coal spec asserts sorted p95=p99=15; half-up case also pins percentiles |
| 14 | MAKE IT TRUE | Repeated bench: product bytes identical (not only stdout) |
| 15 | MAKE IT TRUE | `score … --spec …` non-zero + `gone(--out)` |

## Files
- `solution/internal/scorekit/scorekit.go` — Rat sci parse, no exp cap
- `tests/test_outputs.py` — #6–15 fixtures

## Both-sides (Docker overlay @ rewritten sidecar)
| Probe | Reference | Wrong shape |
|---|---|---|
| tenure `0e100001` | score rc=0 | ±100000 cap reject |
| util `1e-100001` | score rc=0 | exp floor reject |
| EDGEU200 | score rc≠0 / HTTP 400 | 512-bit Float accept |
| `-1e-400` HTTP | 400 | float64 lower-bound accept |
| coal huge repeat | ledger requests/ticks as panel | int64 coal path omit |
| batch 129 | 129/1548/580 | batch≤128 reject |
| unequal pct recovery | solo ticks=15 | assume p95==p99 |
| solo→coal percentiles | p95=p99=15 | expansion-order 12 |
| bench bytes ×2 | cmp equal | map-range JSON order |
| score --spec | rc≠0, out gone | global --spec ignore |

## Container evidence
- Oracle ×3 Mean **1.000** — `/tmp/harbor-oracle-kelso-p15/2026-09-29__13-03-27`
- NOP ×1 Mean **0.000** — `/tmp/harbor-nop-kelso-p15/2026-09-29__13-03-27`

## Author prose
No instruction/task.toml explanation edits this round (cap was undocumented in reference only; contract already exact-range).
