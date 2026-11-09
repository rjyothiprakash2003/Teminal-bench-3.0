#!/bin/bash
# Oracle wrapper for the SMF-2026 scorecard folder.
# Installs the reference folder and folds the staged pack.
set -e
cd /app
cp /solution/fold.py /app/fold.py
chmod +x /app/fold.py
python /app/fold.py --cards /app/cards --spec /app/scorecard --out /app/output
