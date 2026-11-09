# Revision — QUALITY PANEL 13 blocking (2026-09-29)

FILL IN
- Task tree: `secure-performance-optimized-ml-inference-pipeline/`
- Return: panel-13 (post oracle-flake harden); PGT/Det clean; Oracle/NOP already passed on platform
- Last packaged: oracle-flake zip (`run_ci_gate` timeout 180). Citations resolve.

## Scope (one line each)

| # | Answer | Disposition |
|---|---|---|
| 1 | MAKE IT TRUE | Pin page: offmean acceptance always requires M=364705 (integrity pin) |
| 2,3 | MAKE IT TRUE | Sci-notation via Rat mantissa × 10^exp (no Float64 round) |
| 4,10 | MAKE IT TRUE | `batch 0` alone fails; `batch 0` / `batch >MaxInt64` excluded beside sibling |
| 5 | MAKE IT TRUE | instruction.md ci-gate budget → 180s (matches verifier; closes flake/CC clash) |
| 6 | MAKE IT TRUE | HTTP EDGEU + sci POST → INVALID_INTAKE |
| 7 | MAKE IT TRUE | leftover positional `gone(--out)` |
| 8 | MAKE IT TRUE | score under live `mean tenure_months 49` |
| 9,11 | MAKE IT TRUE | HTTP bound rows assert full `{"id","score_micros"}` incl. `" "` |
| 12 | MAKE IT TRUE | CT media type exact; reject `application/json ;charset=utf-8` |
| 13 | MAKE IT TRUE | `type(score_micros) is int` on HTTP success |

## Files
- `environment/kelso/policy/serving-contract.txt` — 364705 pin; digest `6358ebe0…`
- `instruction.md` — ci-gate 180s
- `solution/internal/scorekit/scorekit.go` — sci Rat parse
- `solution/internal/httpapi/httpapi.go` — CT no pre-`;` whitespace
- `tests/test_outputs.py` — #4–11,#13 sites

## Both-sides (host @ `/app`)
| Probe | Reference | Wrong shape |
|---|---|---|
| util_ratio sci e0 | score rc=1 / HTTP 400 | Float64 ParseFloat accept |
| CT `json ;charset` | 400 | Trim media type accept |
| batch 0 alone | bench rc≠0 | clamp→1 success |
| batch MaxInt64+1 + good | 1/15/68 | admit huge batch |
| space id HTTP | `{"id":" ",…}` | trim to `""` |

## Container evidence
- Oracle ×3 Mean **1.000** — `/tmp/harbor-oracle-kelso-p13b/2026-09-29__12-14-33`
- NOP ×1 Mean **0.000** — `/tmp/harbor-nop-kelso-p13b/2026-09-29__12-14-33`


## Author prose
`instruction.md` timeout line updated (falsified by verifier 180). task.toml explanations untouched.
