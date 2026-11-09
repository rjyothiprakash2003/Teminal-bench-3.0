#!/bin/bash
set -uo pipefail
mkdir -p /logs/verifier
chown root:root /logs/verifier 2>/dev/null || true
chmod 700 /logs/verifier
: >/logs/verifier/reward.txt
chown root:root /logs/verifier/reward.txt 2>/dev/null || true
chmod 600 /logs/verifier/reward.txt 2>/dev/null || true
if [ "$PWD" = "/" ]; then
    echo 0 > /logs/verifier/reward.txt
    chmod 400 /logs/verifier/reward.txt 2>/dev/null || true
    exit 0
fi
env -u PYTHONPATH -u PYTHONHOME -u GOFLAGS PYTHONSAFEPATH=1 PYTHONDONTWRITEBYTECODE=1 \
  /venv/bin/python -I -B -m pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA
rc=$?
pkill -9 -u 12000 2>/dev/null || true
if [ $rc -eq 0 ]; then
    echo 1 > /logs/verifier/reward.txt
else
    echo 0 > /logs/verifier/reward.txt
fi
chown root:root /logs/verifier/reward.txt 2>/dev/null || true
chmod 400 /logs/verifier/reward.txt 2>/dev/null || true
exit 0
