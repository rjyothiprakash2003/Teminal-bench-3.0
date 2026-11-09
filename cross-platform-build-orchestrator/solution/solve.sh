#!/bin/bash
# Oracle: install the reference orchestrator and produce shipped outputs.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cp "$HERE/src/orchestrator.py" /app/src/orchestrator.py

python3 /app/src/orchestrator.py --config /app/sample --out /app/out/sample.json
python3 /app/src/orchestrator.py --config /app/examples/regression --out /app/out/regression.json

sha256sum /app/out/sample.json /app/out/regression.json
