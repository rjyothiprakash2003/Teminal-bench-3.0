#!/bin/bash
set -e
cd /app
mkdir -p /app/out
cp /solution/merge.py /app/merge.py
chmod +x /app/merge.py
python /app/merge.py --root /app/examples/single/root --out /app/out/single.json
python /app/merge.py --root /app/examples/extends/root --out /app/out/extends.json
