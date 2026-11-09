#!/bin/bash
set -uo pipefail
mkdir -p /logs/verifier
: > /logs/verifier/reward.txt
chown root:root /logs/verifier /logs/verifier/reward.txt
chmod 0700 /logs/verifier
chmod 0600 /logs/verifier/reward.txt
# WORKDIR guard: Harbor starts test.sh from /app (tests/Dockerfile WORKDIR).
# A harness that leaves PWD at / is misconfigured; refuse before pytest so the
# failure is visible instead of reading submission-relative paths from root.
if [ "$PWD" = "/" ]; then
    echo "test.sh: refusing to grade with PWD=/; expected WORKDIR /app" >&2
    echo 0 > /logs/verifier/reward.txt
    exit 0
fi
# Verifier-only pins (pytest 9.1.1, pytest-json-ctrf) are not on the probe path.
export PYTHONPATH="/tests/_verifier_python${PYTHONPATH:+:$PYTHONPATH}"
python -m pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA
rc=$?
if [ $rc -eq 0 ]; then
    echo 1 > /logs/verifier/reward.txt
else
    echo 0 > /logs/verifier/reward.txt
fi
