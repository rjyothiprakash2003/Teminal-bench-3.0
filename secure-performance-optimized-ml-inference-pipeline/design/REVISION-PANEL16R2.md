# Revision — QUALITY PANEL 16 round-2 (2026-09-28 evening)

FILL IN
- Task tree: `secure-performance-optimized-ml-inference-pipeline/`
- Return: panel-16r2 (UID 97fa78d0…); PGT/Det clean; QC gate failed pre-difficulty
- Last packaged: panel-16 zip (Harbor `/tmp/harbor-oracle-kelso-p16/…`). Citations resolve; none already closed.

## Scope (one line each)

| # | Answer | Disposition |
|---|---|---|
| 1 | MAKE IT TRUE | score under live `mean delinq_24` |
| 2 | MAKE IT TRUE | contract: exact JSON number text vs bounds before binary64 |
| 3 | MAKE IT TRUE | live `w tenure` / `w revol_bal` / `w open_trades` |
| 4 | MAKE IT TRUE | print `offmean_micros` only after both labs; assert absent on mean fail |
| 5,6,13 | MAKE IT TRUE | selfcheck live `mean revol_bal` + `mean open_trades` |
| 7 | MAKE IT TRUE | `repeat -1` excluded beside `repeat 2` |
| 8 | DISAGREE | Go `net/http` strips trailing OWS from header values; Reach never hits AuthOK (`ReadRequest` → `"Bearer tok"`). No graded HTTP case can enforce it. |
| 9 | MAKE IT TRUE | accept `application/json;  charset=utf-8` |
| 10 | MAKE IT TRUE | identical illegal score → equal stdout/stderr |
| 11 | MAKE IT TRUE | gate active fixture with 7 rows |
| 12 | MAKE IT TRUE | live `std revol` / `std open` + coefs in #3 |
| 14 | STOP PROMISING | page: scenario batch fits signed 64-bit (matches ParseInt) |
| 15 | MAKE IT TRUE | serve invalid-flag runs use default 120s budget |
| 16 | KEEP AMBER | concurrent pool; no forced CS barrier |

## Files
- `environment/kelso/policy/serving-contract.txt` — exact-decimal sentence; batch int64; digest `9de5fa55…`
- `solution/cmd/ridgecast/ridgecast.go` — offmean print after both labs
- `tests/test_outputs.py` — #1–7,#9–12,#15 sites
- `tests/holdout/sealed-expected.json` — reminted

## Both-sides (host overlay)
| Probe | Reference | Wrong shape |
|---|---|---|
| mean delinq score | live micros | embedded delivered mean |
| bias selfcheck fail | no `offmean_micros` | premature print |
| revol mean selfcheck | ok + 364705 | hardcoded 8500 |
| repeat -1 + good 2 | req=2 ticks=30 | `!=0` parse → 1/15 |
| CT two spaces | 200 | whitelist of two spellings |
| trailing Auth space | DISAGREE (stripped) | — |
| batch >MaxInt64 | STOP page | — |

## Container evidence
- Oracle ×3 Mean **1.000** — `/tmp/harbor-oracle-kelso-p16r2/2026-09-28__20-26-51`
- NOP ×1 Mean **0.000** — `/tmp/harbor-nop-kelso-p16r2/2026-09-28__20-26-51`

## Author prose
Instruction / task.toml explanations untouched.
