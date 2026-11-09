#!/bin/bash
set -e
cd /app
cp /solution/src/aggregator.py /app/src/aggregator.py
chmod +x /app/src/aggregator.py
python /app/src/aggregator.py --stream /app/stream --out /app/out/windows.json
python /app/src/aggregator.py --stream /app/examples/regression --out /app/out/regression.json
