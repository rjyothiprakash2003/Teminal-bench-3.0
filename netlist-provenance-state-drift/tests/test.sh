#!/bin/bash
set -uo pipefail
mkdir -p /logs/verifier
chmod 700 /logs/verifier
: > /logs/verifier/reward.txt
chmod 600 /logs/verifier/reward.txt
if [ "$PWD" = "/" ]; then
    echo 0 > /logs/verifier/reward.txt
    exit 0
fi
python -m pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA
rc=$?
if [ "$rc" -eq 0 ]; then
    echo 1 > /logs/verifier/reward.txt
else
    echo 0 > /logs/verifier/reward.txt
fi
exit 0
