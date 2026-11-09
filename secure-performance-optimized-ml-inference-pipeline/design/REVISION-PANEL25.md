# Revision disposition — quality panel return (25 blocking + #26)

FILL IN
- Task tree: `secure-performance-optimized-ml-inference-pipeline/`
- Return file: quality panel paste in chat (25 blocking + PGT #26)
- Last packaged version: zip dated 2026-09-28 09:29 (post–panel-70 cure). No later unreleased package had already closed these findings; line citations still resolve in this tree.

## Scope decisions (one line each)

| # | Axis | Answer | Disposition |
|---|---|---|---|
| 1 | SV | MAKE IT TRUE | `illegal_rows` adds below-min for util/delinq/inq/revol/open_trades |
| 2 | SV | MAKE IT TRUE | null for every SCHEMA field in `illegal_rows` |
| 3 | CC | MAKE IT TRUE | `rebuild()` timeout 180 (matches instruction) |
| 4 | CC | STOP PROMISING (application key order) | contract: JSON key order not significant; schema order = logit only |
| 5 | CC | STOP PROMISING (mandatory bench after score fail) | drop `gate_ran(..., bench)` after `{}` intake; nonzero + selfcheck/score only |
| 6 | SV | MAKE IT TRUE | same as #1 (below-min cases) |
| 7 | SV | MAKE IT TRUE | CLI accept exact 1048576-byte line |
| 8 | SV | MAKE IT TRUE | POST accept exact 1048576-byte body |
| 9 | SV | MAKE IT TRUE | `--out` + `--verbose` / `--out=` cleanup via `gone()` |
| 10 | SV | MAKE IT TRUE | five distinct rows; batch_limit=5 and >int64 |
| 11 | SV | MAKE IT TRUE | omit/nonpositive batch_limit on score/selfcheck/bench/serve |
| 12 | SV | MAKE IT TRUE | 16 concurrent POSTs same application `OVLP` |
| 13 | SV | MAKE IT TRUE | recovery anchor p95=p99=C (batch 99 + solo) |
| 14 | SV | MAKE IT TRUE | n=100 ledger asserts p99=12 (ceil index, not floor) |
| 15 | SV | MAKE IT TRUE | reject `application/jsonx`, `charset=latin1`, `iso-8859-1`, `foo=bar` |
| 16 | SV | MAKE IT TRUE | bench `--config` nonexistent / omit-batch must fail |
| 17 | SV | MAKE IT TRUE | relative assets scored with `cwd=/work/rel-assets` |
| 18 | SV | MAKE IT TRUE | `go_rebuilt_bin` accepts any go build/install of `cmd/ridgecast` |
| 19 | SV | MAKE IT TRUE | same drop as #5 |
| 20 | SV | MAKE IT TRUE | tests reject arbitrary CT params; solution already charset=utf-8 only |
| 21 | SV | MAKE IT TRUE | default score (no `--config`) fails when sidecar omits batch_limit |
| 22 | Ref | MAKE IT TRUE | `recoverBigPair` singleton-only admits unique (S=2,C=1) |
| 23 | SV | MAKE IT TRUE | batch 5 + solo avg_latency_ticks==13 (half-up, not ties-to-even) |
| 24 | SV | MAKE IT TRUE | same as #3 |
| 25 | SV | MAKE IT TRUE | null/bool/str/array/object × every SCHEMA field |
| 26 | PGT | MAKE IT TRUE | BENCH minted from sealed holdout bytes, not `/app` |

## Files touched
- `environment/kelso/policy/serving-contract.txt` — key-order clarification; sealed digest updated
- `tests/holdout/sealed-expected.json` — new contract digest
- `tests/holdout/bench-anchor.json`, `bench-recovery-loadspec.txt` — sealed mint sources
- `tests/ridge_helpers.py` — rebuild 180; BENCH mint; go_rebuilt_bin; illegal_rows; run(cwd=)
- `tests/test_outputs.py` — coverage + gate score-fail relaxation
- `solution/internal/tickbench/tickbench.go` — singleton-only big recovery

## Both-sides (host-measurable without Harbor)
- Oracle overlay `go build ./cmd/ridgecast`: BUILD_OK after tickbench/httpapi/scorekit/ridgeio/cmd overlay.
- Sealed digests: 10/10 match environment/kelso bytes after contract edit.
- Contract #4: W41-D/E key order remains legal under clarified page rule (fixture unchanged).
- #22: source branch `Singleton-only recovery` + `sBig.Cmp(two) == 0` present; graded fixture in test_cli_flags_and_bench_recovery.
- Full both-sides rival counts against pytest: **BLACK this sitting** (no Harbor /app sandbox). Author must run oracle×3 / nop before submit.

## Instruction / explanation fields
- `instruction.md` already allows 180s rebuild — not falsified; verifier now matches.
- `difficulty_explanation` / `solution_explanation` still say schema-order logit — still true after #4.
- Left all four author prose surfaces untouched.

## Audit (companion §7 extras not charged by panel)
1. AMBER: overlapping same-id POST stress may miss a rare zero-init race without a hold-open hook.
2. AMBER: week41 mint still reads `/app/fixtures` at import (digest-checked later); #26 closed only BENCH.
3. BLACK: Harbor oracle/nop/lazy and rival re-sweep not run this sitting.
4. Near-miss watch: gate bench-after-empty-loadspec still requires bench (score may succeed) — contract-aligned, keep.
