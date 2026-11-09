#!/bin/bash
set -uo pipefail
mkdir -p /logs/verifier
: > /logs/verifier/reward.txt
chown root:root /logs/verifier /logs/verifier/reward.txt
chmod 0700 /logs/verifier
chmod 0600 /logs/verifier/reward.txt
if [ "$PWD" = "/" ]; then
    echo 0 > /logs/verifier/reward.txt
    exit 0
fi
python -m pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA
rc=$?
pkill -9 -u sandbox >/dev/null 2>&1 || true
sleep 1
pkill -9 -u sandbox >/dev/null 2>&1 || true
if [ $rc -eq 0 ]; then
    echo 1 > /logs/verifier/reward.txt
else
    echo 0 > /logs/verifier/reward.txt
fi
exit 0
