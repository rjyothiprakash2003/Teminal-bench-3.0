#!/bin/bash
set -e
cd /app
cp /solution/src/quill.py /app/src/quill.py
chmod +x /app/src/quill.py
python /app/src/quill.py --src /app/examples/simple/input.quill --out /app/out/simple.json
python /app/src/quill.py --src /app/examples/regression/input.quill --out /app/out/regression.json
