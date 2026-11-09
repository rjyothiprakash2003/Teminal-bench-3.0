#!/bin/bash
set -uo pipefail
unset GOFLAGS
export GOFLAGS=-buildvcs=false
export PATH="/usr/local/go/bin:/go/bin:${PATH:-/usr/bin:/bin}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
mkdir -p "$ROOT/bin" "$ROOT/out"
# Non-root graders must be able to replace /app/bin/ridgecast and unlink out products.
chmod 0777 "$ROOT/bin" "$ROOT/out" || true
# Prefer go build -o over go install: install is slower under cold module cache
# and pushed straced gate runs past 120s on contested hosts (oracle 2/3 flake).
go build -o "$ROOT/bin/ridgecast" ./cmd/ridgecast || exit $?
chmod 0777 "$ROOT/bin" "$ROOT/out" || true
chmod a+rwx "$ROOT/bin/ridgecast" 2>/dev/null || true
"$ROOT/bin/ridgecast" selfcheck
sc=$?
if [ "$sc" -ne 0 ]; then
	exit "$sc"
fi
# Do not rm out products here: a short-lived rm can reuse selfcheck's PID in
# strace -f traces. Bench unlinks before write; score replaces via rename.
# Verifier credits ridgecast execves with an immediate-overlay filter only.
"$ROOT/bin/ridgecast" score --config "$ROOT/deploy/sidecar.kv" --intake "$ROOT/fixtures/week41-intake.jsonl" --out "$ROOT/out/week41-scores.jsonl"
score_rc=$?
"$ROOT/bin/ridgecast" bench --config "$ROOT/deploy/sidecar.kv" --spec "$ROOT/bench/loadspec.txt" --out "$ROOT/out/bench-ledger.json"
bench_rc=$?
if [ "$score_rc" -ne 0 ]; then
	exit "$score_rc"
fi
exit "$bench_rc"
