# Revision — QUALITY PANEL 12 blocking (return after p12b) — 2026-09-29

FILL IN
- Task tree: `secure-performance-optimized-ml-inference-pipeline/`
- Return: panel-12c (schema bounds, sci-exp exactness, uint64+1, 1e2, inconsistent avg, EDGE1000, race barrier, quoted charset, gate stamp↔exec, QC verification_explanation)
- Zip: `../secure-performance-optimized-ml-inference-pipeline.zip` (no `design/`)

## Scope (one line each)

| # | Axis | Disposition |
|---|---|---|
| 1 | SV schema hardcode | MAKE IT TRUE — mutate tenure max 600→601, accept 601 CLI+HTTP, restore |
| 2–4,9 | Ref sci-exp cap | MAKE IT TRUE — `exactInRange` via SetString / mant×10^exp normalize, no ±100000 substitute |
| 5 | SV uint64 ceiling | MAKE IT TRUE — `repeat 18446744073709551616` solo ledger |
| 6 | SV `1e2` tokens | MAKE IT TRUE — exclude `repeat 1e2` / `batch 1e2` beside good |
| 7 | SV inconsistent avg | MAKE IT TRUE — delivered recovery + avg=13 → bench fail |
| 8 | SV EDGE1000 | MAKE IT TRUE — 1000-zero excess reject CLI+HTTP |
| 10 | SV race overlap | MAKE IT TRUE — inject `VerifierCacheBarrier` (≥8) before ScoreOne under `-race` |
| 11 | SV quoted charset | MAKE IT TRUE — reject `charset="utf-8"` |
| 12 | SV stamp↔exec | MAKE IT TRUE — hold.go opens `/app/out/.ridge-ran-<tok>`; strace openat per ridgecast PID |
| QC | verification_explanation | MAKE IT TRUE — drop nonexistent `design/run_chain.sh`; describe stamp+open provenance |

## Files
- `solution/internal/scorekit/scorekit.go` — exact rational range (no exp-cap proxy)
- `tests/ridge_helpers.py` — `gate_exec_stamp_ok`, strace `open/openat`, 6-tuple `run_ci_gate`
- `tests/test_outputs.py` — fixtures for #1,#5–8,#10–12 + sci reach cases
- `task.toml` — verification_explanation congruent with verifier

## Both-sides
| Probe | Reference | Wrong shape |
|---|---|---|
| schema max 601 + tenure 601 | score | hardcoded 600 reject |
| M e-100002 in util | accept ~1 | capped → reject |
| `0.…1e100006` tenure | reject | capped → accept |
| `0.…3e100002` util | reject | capped → accept |
| M e-100001 (~0.3) | accept | capped → reject |
| repeat 2^64 | ledger | uint64 parse fail |
| `1e2` repeat/batch | exclude | admit as 100 |
| avg=13 delivered shape | bench fail | recover ignoring avg |
| EDGE1000 | reject | 1024-bit Float accept |
| barrier + -race | 200 | unsync map abort |
| charset="utf-8" | 400 | MIME-unquote accept |
| swap prebuilt after build | stamp open miss | ELF tok + execve only |

## Container evidence
- Oracle ×3 Mean **1.000** — `/tmp/harbor-oracle-kelso-p12c/2026-09-29__19-37-29`
- NOP ×1 Mean **0.000** — `/tmp/harbor-nop-kelso-p12c/2026-09-29__19-37-29`

## Author prose
Untouched except `task.toml` verification_explanation (QC-falsified `design/run_chain.sh` claim removed).
