#!/bin/bash
set -e
cd /app
cp /solution/src/cachebox.py /app/src/cachebox.py
chmod +x /app/src/cachebox.py
rm -rf /app/out/simple-store /app/out/regression-store
mkdir -p /app/out/simple-store /app/out/regression-store
python3 /app/src/cachebox.py --store /app/out/simple-store --scenario /app/examples/simple/scenario.json --report /app/out/simple-report.json
python3 /app/src/cachebox.py --store /app/out/regression-store --scenario /app/examples/regression/scenario.json --report /app/out/regression-report.json
