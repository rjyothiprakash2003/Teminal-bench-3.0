#!/bin/bash
set -e
cd /app
cp /solution/src/syncer.py /app/src/syncer.py
chmod +x /app/src/syncer.py
rm -rf /app/out/simple /app/out/regression
mkdir -p /app/out/simple /app/out/regression
python3 /app/src/syncer.py --src /app/examples/simple --dst /app/out/simple --report /app/out/simple-report.json
python3 /app/src/syncer.py --src /app/examples/regression --dst /app/out/regression --report /app/out/regression-report.json
