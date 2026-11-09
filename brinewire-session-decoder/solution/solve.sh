#!/bin/bash
set -e
cd /app
cp /solution/decode.py /app/decode.py
chmod +x /app/decode.py
python /app/decode.py --captures /app/captures --spec /app/brinewire --out /app/output
