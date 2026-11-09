#!/bin/bash
# Verifier entrypoint. Runs in the separate verifier container.
#
# Deliberately NOT `set -e`: a failing pytest must still reach the reward write below.
set -uo pipefail
unset GOFLAGS || true

mkdir -p /logs/verifier
chmod 700 /logs/verifier

if [ "$PWD" = "/" ]; then
    echo 0 > /logs/verifier/reward.txt
    exit 0
fi

/venv/bin/python -m pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA
rc=$?
pkill -KILL -u 12000 >/dev/null 2>&1 || true
if [ "$rc" -eq 0 ]; then
    echo 1 > /logs/verifier/reward.txt
else
    echo 0 > /logs/verifier/reward.txt
fi
exit 0
