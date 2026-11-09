# Revision — QUALITY PANEL 24 blocking (2026-09-28)

## FILL IN FIRST
- Task tree: `/Users/jyothiprakash629/Downloads/v3/secure-performance-optimized-ml-inference-pipeline`
- Return: panel-24 blocking list (SV×22 + CC×1 + Ref×1); PGT + Deterministic clean
- Last packaged version: zip dated 2026-09-28 13:20 (post–panel-13 / gate-rm cure). Citations still resolve; none of the 24 were already closed in an unreleased tree.

## Scope decisions (MAKE IT TRUE vs STOP PROMISING)

| # | Axis | Answer | Disposition |
|---|---|---|---|
| 1 / 4 / 15 | SV | MAKE IT TRUE | CLI + POST reject top-level singleton JSON array |
| 2 | CC | STOP PROMISING (verifier) | Drop score≺bench; keep selfcheck≺score and selfcheck≺bench only |
| 3 / 12 | SV | MAKE IT TRUE | Giant S/C recovery + coalesced `batch 2` ledger |
| 5 | SV | MAKE IT TRUE | CRLF length boundary (ref already keeps CR in token) |
| 6 | SV | MAKE IT TRUE | Legal intake + nonempty-dir `--out` write fail cleanup |
| 7 | SV | MAKE IT TRUE | Live `std delinq_24` mutation changes micros |
| 8 | SV | MAKE IT TRUE | Live `w delinq_24` mutation changes micros |
| 9 | SV | MAKE IT TRUE | Bench rejects `batch_limit -1` |
| 10 | SV | MAKE IT TRUE | Six-row group under `batch_limit 6` |
| 11 | SV | MAKE IT TRUE | Shared id NEAR util collision pair |
| 13 | SV | MAKE IT TRUE | Spec with `batch 101` admitted |
| 14 | SV | MAKE IT TRUE | Exact body for off-mean CACHE tenure 12 |
| 16 | SV | MAKE IT TRUE | HEAD → METHOD/NOT_FOUND (status; body optional under HEAD) |
| 17 | SV | MAKE IT TRUE | Serve rejects `--spec` |
| 18 | SV | MAKE IT TRUE | Twin bench same `--out` → identical stdout |
| 19 | SV | MAKE IT TRUE | token_file A + sha(B) + Bearer B → UNAUTHORIZED |
| 20 | SV | STOP PROMISING (page) | Soften exact 72/0.36; keep fixed off-mean lab @ 364705 |
| 21 | SV | MAKE IT TRUE | Seed `util_ratio` alone must not change ledger |
| 22 | Ref | MAKE IT TRUE | `big.Int` scenario repeat parse (already in tickbench) |
| 23 | SV | MAKE IT TRUE | Score rejects `-config` |
| 24 | SV | KEEP / AMBER | Concurrent POST pool remains best-effort; no server barrier |

## Both-sides measurements (Harbor)

- Oracle ×3: Mean **1.000** — `/tmp/harbor-oracle-kelso-p24b/2026-09-28__14-20-53` (id `dc8dddad-…`)
- First oracle attempt failed only on #18 over-strict twin with *different* `--out` paths (stdout embeds path). Fixed to same path; re-run green.
- NOP ×1: Mean **0.000** — `/tmp/harbor-nop-kelso-p24/2026-09-28__14-24-51`
- Flip patches for each SV finding were not re-instrumented as separate Harbor agents this sitting; coverage is the new graded asserts + oracle green / nop red.

## Files touched
- `tests/test_outputs.py` — gate order, array intake, CRLF, write-dir, live scale/coef, bl=-1, 6-row, HTTP array/HEAD/CACHE exact/NEAR/token mismatch, `-config`, serve `--spec`, stdout twin, util seed, batch 101, coalesced giant C, huge repeat
- `tests/ridge_helpers.py` — `oracle_micros_assets`, sidecar `coef`/`scale`/`token_file`
- `environment/kelso/policy/serving-contract.txt` — selfcheck off-mean soften; digest already sealed `0ef060e6…`
- `solution/internal/tickbench/tickbench.go` — big.Int repeats (pre-existing this round)

## Author prose
Instruction / task.toml explanations left alone. Instruction enumeration “selfcheck, score, and bench” does not mandate score≺bench; matches relaxed gate assert. Contract no longer names exact 72/0.36 vector.

## Residual / next-round risk
- #24 concurrency remains scheduling-dependent (AMBER).
- Rival sweep / sentence×site not re-run after this round’s new asserts (BLACK on full rival count).
- Companion §7 full grid: see `design/panel-axes-table.md`.
