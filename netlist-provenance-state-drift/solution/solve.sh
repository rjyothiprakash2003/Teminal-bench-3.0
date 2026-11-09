#!/bin/bash
# Replace the Kilnbridge reconstructor and rebuild the shipped desk outputs.
# /app/canon/synth-ordinance.txt is the authority for those outputs.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

mkdir -p /app/cmd/netloom
cp "$HERE/cmd/netloom/main.go" /app/cmd/netloom/main.go

cd /app
export GOFLAGS="${GOFLAGS:-}"
export GOPROXY="${GOPROXY:-off}"
export GOSUMDB="${GOSUMDB:-off}"
export GOTOOLCHAIN="${GOTOOLCHAIN:-local}"
export GO111MODULE="${GO111MODULE:-on}"
export CGO_ENABLED="${CGO_ENABLED:-0}"
go run /app/cmd/netloom \
  --journal /app/history/synth-epochs.ndjson \
  --modules /app/netlist/modules \
  --ordinance /app/canon/synth-ordinance.txt \
  --matrix /app/tables/liberty-eligibility.csv \
  --graph /app/out/provenance-graph.json \
  --attest /app/out/lineage-attest.npg

sha256sum /app/out/provenance-graph.json /app/out/lineage-attest.npg
