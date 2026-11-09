#!/bin/bash
# The concurrency sprint left a shared scratch row in scorekit, tenure-keyed
# cache aliasing, dashboard debug headers, a wall-clock bench, a no-op schema
# gate, and a skipped ci-gate. Restore the week-41 contract from the serving
# notes, rebuild, and run the floor gate.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cp "$HERE/internal/scorekit/scorekit.go" /app/internal/scorekit/scorekit.go
cp "$HERE/internal/httpapi/httpapi.go" /app/internal/httpapi/httpapi.go
cp "$HERE/internal/tickbench/tickbench.go" /app/internal/tickbench/tickbench.go
cp "$HERE/internal/ridgeio/ridgeio.go" /app/internal/ridgeio/ridgeio.go
cp "$HERE/cmd/ridgecast/ridgecast.go" /app/cmd/ridgecast/ridgecast.go
cp "$HERE/ops/ci-gate.sh" /app/ops/ci-gate.sh
chmod +x /app/ops/ci-gate.sh
/app/ops/ci-gate.sh
