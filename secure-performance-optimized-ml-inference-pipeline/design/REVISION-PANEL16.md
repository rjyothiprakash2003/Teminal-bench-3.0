# Revision — QUALITY PANEL 16 blocking (2026-09-28)

FILL IN
- Task tree: `secure-performance-optimized-ml-inference-pipeline/`
- Return: panel-16 (UID 97fa78d0…, 28/09/2026 18:15); QC all PASS; Oracle/NOP already passed on platform; Coherent/PGT/Det clean
- Last packaged: zip after panel-20 (Harbor `/tmp/harbor-oracle-kelso-p20/…`). Citations resolve; Float256/`ServeMux` findings were still open in that tree (exactInRange was 256-bit Float; serve used direct Handler already in ridgecast but Register path existed).

## Scope (one line each)

| # | Answer | Disposition |
|---|---|---|
| 1 | MAKE IT TRUE | `delinq_24=20.5` in `illegal_rows` (CLI+HTTP) |
| 2,3,14 | MAKE IT TRUE | `exactInRange` via `big.Rat` / `SetString` (no 256-bit Float round) |
| 4 | MAKE IT TRUE | `ListenAndServe(..., srv)` only; test `GET /v1//score` → 404 |
| 5 | MAKE IT TRUE | score live `std util_ratio` + `w util_ratio` mutations |
| 6 | MAKE IT TRUE | selfcheck live `mean inq_6` → 2.6 still ok + offmean line |
| 7 | STOP PROMISING | page: batches “sized by batch_limit” (no “any configured”); keep 7-row site |
| 8 | MAKE IT TRUE | mixed `repeat 0` + `repeat 1.5` with admitted sibling |
| 9 | MAKE IT TRUE | `Bearer\t` → UNAUTHORIZED |
| 10 | MAKE IT TRUE | `Charset=utf-8` → INVALID_INTAKE |
| 11 | MAKE IT TRUE | bench `weights/alternate.coef` cwd-only |
| 12 | MAKE IT TRUE | `repeat 0 batch 100` + solo → memory_units=68 |
| 13 | MAKE IT TRUE | selfcheck prints `offmean_micros 364705` then `selfcheck ok` |
| 15 | MAKE IT TRUE | `ledger_obj` via `object_pairs_hook` (all member occurrences) |
| 16 | KEEP AMBER | concurrent pool; no forced CS barrier (scheduler-bound) |

## Files
- `environment/kelso/policy/serving-contract.txt` — offmean print; batch wording; digest `fc621a17…`
- `solution/internal/scorekit/scorekit.go` — Rat exact range
- `solution/cmd/ridgecast/ridgecast.go` — offmean_micros; ServeMux note
- `tests/ridge_helpers.py` — D205; ledger pairs hook
- `tests/test_outputs.py` — #1,#4–6,#8–13 sites
- `tests/holdout/sealed-expected.json` — reminted contract digest

## Both-sides measurements (host overlay @ `/app`)
| Probe | Reference | Wrong shape named by finding |
|---|---|---|
| delinq 20.5 | score rc=1, no out | int-truncate would accept (now graded) |
| util 2.5+10^-82 / 10^-100 | score rc=1 | Float256 accepted (Rat rejects) |
| GET /v1//score | 404 NOT_FOUND | ServeMux would 301 |
| Bearer\\t / Charset= | 401 / 400 | `\s` / mime.Parse would accept |
| repeat0 batch100 + solo | mem=68 | parse-max-batch → 464 |
| selfcheck stdout | `offmean_micros 364705` + ok | tenure=73 fake cleared by printed micros |

## Container evidence
- Oracle ×3 Mean **1.000** — `/tmp/harbor-oracle-kelso-p16/2026-09-28__19-02-31`
- NOP ×1 Mean **0.000** — `/tmp/harbor-nop-kelso-p16/2026-09-28__19-02-31`

## Author prose
Instruction / task.toml explanations untouched. Quality-check list in return was all PASS.
