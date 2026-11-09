# Revision disposition — quality panel return (26 blocking)

FILL IN
- Task tree: `secure-performance-optimized-ml-inference-pipeline/`
- Return file: quality panel paste (UID 97fa78d0…, 28/09/2026 15:57)
- Last packaged version: zip dated 2026-09-28 ~15:07 (`secure-performance-optimized-ml-inference-pipeline.zip`). Prior PANEL24 softened the off-mean vector; that softening is what this panel charged as #1–#3/#5/#7.

## Scope decisions (one line each)

| # | Axis | Answer | Disposition |
|---|---|---|---|
| 1–3,5,7 | CC | MAKE IT TRUE | Pin off-mean lab `tenure_months=72`, `util_ratio=0.36`, other fields at means |
| 4 | CC | MAKE IT TRUE | Only `charset=utf-8` parameter allowed; other params INVALID_INTAKE |
| 6 | CC | MAKE IT TRUE | Loadspec `repeat` is unbounded non-negative decimal (may exceed int64) |
| 8 | PGT | MAKE IT TRUE | Mint WEEK41 from sealed `tests/holdout/week41-intake.jsonl`, not `/app/fixtures` |
| 9 | SV | MAKE IT TRUE | Unknown-path matrix includes PATCH |
| 10 | SV | MAKE IT TRUE | `run_ci_gate` timeout 120 (matches instruction) |
| 11 | SV | MAKE IT TRUE | Failing selfcheck must not print `selfcheck ok` |
| 12,24 | SV | MAKE IT TRUE | Mutate live `util_ratio` coef (−1.2 and 0); selfcheck fails |
| 13 | SV | MAKE IT TRUE | `batch_limit 1.5` rejected for bench/selfcheck/serve |
| 14 | SV | MAKE IT TRUE | Seven-row intake under `batch_limit 7` |
| 15 | SV | MAKE IT TRUE | Shared-id POST varying `delinq_24` only |
| 16 | SV | MAKE IT TRUE | Throughput half-up tie `27e6/384 → 70313`; recovery asserts `7813` |
| 17 | SV | MAKE IT TRUE | `POST /v1/score/extra` → NOT_FOUND |
| 18 | SV | MAKE IT TRUE | Relative sidecar cwd for serve and bench |
| 19 | SV | MAKE IT TRUE | Repeated score/selfcheck stdout byte-identical |
| 20 | SV | MAKE IT TRUE | Recovery loadspec with `tenure_months 49` still recovers |
| 21 | SV | MAKE IT TRUE | Accept `application/json;charset=utf-8` |
| 22 | SV | MAKE IT TRUE | Trailing valueless `--config` fails |
| 23 | SV | MAKE IT TRUE | `serve --verbose` rejected |
| 25 | SV | MAKE IT TRUE | `seed_id LS-1` does not change ledger |
| 26 | SV | MAKE IT TRUE | Gate `extra_env` rebuild token must appear in gate I/O |

## Files touched
- `environment/kelso/policy/serving-contract.txt` — lab pin, CT params, repeat/seed costing
- `tests/holdout/sealed-expected.json` — new contract digest `7c5e07bf…`
- `tests/holdout/week41-intake.jsonl` — sealed WEEK41 mint source
- `tests/ridge_helpers.py` — sealed WEEK41 mint; gate timeout 120; `extra_env`
- `tests/test_outputs.py` — coverage for #9–#26

## Both-sides (Harbor)
- Oracle ×3 Mean **1.000** — `/tmp/harbor-oracle-kelso-p26/2026-09-28__16-23-57`
- NOP ×1 Mean **0.000** — `/tmp/harbor-nop-kelso-p26/2026-09-28__16-27-03`
- Throughput ties: `(1e6*27+192)//384 == 70313`, `(1e6*2+128)//256 == 7813`.
- Contract digest `7c5e07bf…` matches sealed-expected.
- `py_compile` clean on `test_outputs.py` / `ridge_helpers.py`.

## Instruction / explanation fields
- Instruction already states 120s for ridgecast/ci-gate — verifier now matches (#10). Not falsified.
- Left four author prose surfaces in `task.toml` untouched.

## Audit extras (not charged this panel)
1. AMBER: 120s gate under cold go cache may flake on contested hosts (was 180 for that reason); `go build -o` mitigates.
2. AMBER: rebuild-token-in-I/O proves init of rebuilt binary under gate env; a wrapper that re-execs into an identical twin still passes (bounded).
3. Quality-check list in the return was all PASS — no extra FAIL outside the panel.
