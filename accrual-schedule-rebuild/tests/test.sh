#!/bin/bash
set -uo pipefail
mkdir -p /logs/verifier
: > /logs/verifier/reward.txt
chmod 0700 /logs/verifier
chmod 0600 /logs/verifier/reward.txt
if [ "$PWD" = "/" ]; then
    echo 0 > /logs/verifier/reward.txt
    chmod 0755 /logs /logs/verifier
    chmod 0644 /logs/verifier/reward.txt
    exit 0
fi
# The build is given its own environment, so none of the ambient Go settings can reach it.
unset GOFLAGS GOPROXY GOSUMDB GOTOOLCHAIN GOENV GODEBUG GO111MODULE CGO_ENABLED || true
export PYTHONPATH=/tests
python -m pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA
rc=$?
pkill -KILL -u 2000 >/dev/null 2>&1 || true
sleep 1
pkill -KILL -u 2000 >/dev/null 2>&1 || true
# No candidate code is left running, so the reward channel no longer needs closing, and the
# harness that collects it may read it as another account. The verifier closed /logs as well
# as /logs/verifier while candidate code ran, so both are opened again.
chmod 0755 /logs /logs/verifier
chmod 0644 /logs/verifier/reward.txt /logs/verifier/ctrf.json 2>/dev/null
if [ $rc -eq 0 ]; then
    echo 1 > /logs/verifier/reward.txt
else
    echo 0 > /logs/verifier/reward.txt
fi
exit 0
