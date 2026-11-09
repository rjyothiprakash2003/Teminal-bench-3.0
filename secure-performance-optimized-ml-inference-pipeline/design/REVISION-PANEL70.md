# Revision disposition — quality panel return (70 blocking)

FILL IN FIRST
- Task tree: `secure-performance-optimized-ml-inference-pipeline`
- Return file: pasted Quality Panel NEEDS REVISION (70 blocking) in chat 2026-09-28
- Last packaged version: post–design-chain zip (2026-09-27); no later unreleased package closed these findings

Line citations in the return largely still resolve after helper growth; `assert code == want` was at test_outputs.py:83 and is cured.

## Scope decisions (before cures)

| # | Axis | Answer | Why |
|---|---|---|---|
| 2 | Coherent | STOP PROMISING (verifier) | Exact rc match not on page; keep non-zero + no score/bench |
| 3–6, 8 | Reference | MAKE IT TRUE | Big-int S/C is load-bearing contract text |
| 7 | Reference | MAKE IT TRUE | Gate must allow non-root rebuild |
| 9 | Reference | DISAGREE (already true) | `ListenAndServe(..., srv)` already; Register now also wires mux |
| 1,10–68 | Sound Verifier | MAKE IT TRUE | Core schema/HTTP/CLI/bench levers; expanded fixtures |
| 69 | Sound Verifier | AMBER carry | Swap-during-exec is verifier-specific Minor |
| 70 | Sound Verifier | MAKE IT TRUE | Align gate timeout to disclosed 120s |

## Per-finding record

1. MAKE IT TRUE — `illegal_rows` missing every SCHEMA field; open_trades absence included
2. STOP PROMISING — `assert code != 0` (not `== want`)
3–6,8. MAKE IT TRUE — `tickbench` recoverBigPair/`tickRun.cost` now `*big.Int`
7. MAKE IT TRUE — `ci-gate.sh` chmod 0777 on bin/out
9. DISAGREE + harden — already used `srv`; Register installs `/`
10–17,55–57. MAKE IT TRUE — forbidden keys, null/bool/string/range/bounds per field
18–23. MAKE IT TRUE — blank line, dup-id different features, line length, `--out=`, trailing tokens
24–25,61–62. MAKE IT TRUE — util_ratio std + tiny bias in selfcheck
26–28,63. MAKE IT TRUE — omit batch_limit on selfcheck/bench; 1.5/-1; batch 4
29–34,64. MAKE IT TRUE — identical POST; S/C>int64; avg/p95/p99/throughput/memory fixtures
35–43,65,68. MAKE IT TRUE — Bearer spacing, alt token, missing CT, dual JSON, CT/X-Ridge, POST health/nope
44–54,66–67. MAKE IT TRUE — CLI negatives across commands; gate bench fail + varied loadspec
69. AMBER — rebuild token + ELF still; full inode swap not graded
70. MAKE IT TRUE — `run_ci_gate` / rebuild timeout 120

## Both-sides notes (host)

- Large S/C: docker oracle bench with S=C>int64 wrote `total_ticks=9223372036854775810` (rc 0)
- batch_limit 1.5: oracle score rc 1 (cannot load config)
- Harbor oracle/nop/lazy: **not re-run this sitting** after the cure batch
