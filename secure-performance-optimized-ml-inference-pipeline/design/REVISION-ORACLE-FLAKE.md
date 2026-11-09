# Oracle 2/3 flake cure (round 2) — 2026-09-29

## State
Platform Oracle/NOP: **2/3** valid oracle attempts. Quality panel not run.
Local Harbor before this cure: oracle ×6 Mean **1.000** (`/tmp/harbor-oracle-kelso-flake2/2026-09-29__10-57-15`).
Root cause class matches the prior flake note: contested-host wall time under root `strace -f`, not a logic regression.

## What regressed
Panel-26 “aligned” `run_ci_gate` timeout back to **120s** to match `instruction.md`. That undid the REVISION-ORACLE-FLAKE harden (verifier wait **180s** for strace + cold `go build`). The gate test now issues **8** full straced rebuilds; one slow build past 120s fails the trial.

## Fixes
1. **MAKE IT TRUE (verifier)**: restore `run_ci_gate(..., timeout=180)` — agent-visible budget remains 120s without strace overhead.
2. **Gate speed**: `GOFLAGS=-buildvcs=false` in `solution/ops/ci-gate.sh` and verifier `env()`.
3. Retained: immediate-overlay-only PID filter; `go build -o` (not `go install`).

## Author note
`instruction.md` still says ci-gate ≤120s. Verifier waits 180s for instrumentation only. Optional author edit: bump the instruction figure to 180.

## Container evidence (this sitting)
- Pre-fix local ×6: Mean **1.000** — `/tmp/harbor-oracle-kelso-flake2/2026-09-29__10-57-15`
- Post-fix oracle ×6: Mean **1.000** — `/tmp/harbor-oracle-kelso-stable2/2026-09-29__11-08-08`
- Post-fix nop ×1: Mean **0.000** — `/tmp/harbor-nop-kelso-stable2/2026-09-29__11-08-08`
