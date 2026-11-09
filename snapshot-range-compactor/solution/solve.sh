#!/bin/bash
set -euo pipefail
# Replace the latest-only reducer with the snapshot-aware event sweep.
cp /solution/compact.go /app/src/compact.go
cd /app/src
gofmt -w compact.go
go build -o /app/compact .
/app/compact < /app/examples/regression.json > /tmp/compaction-check.json
