#!/bin/bash
set -euo pipefail
cp /solution/main.go /app/logindex/main.go
cd /app/logindex
go build -o /tmp/logindex-check .
/tmp/logindex-check < /app/examples/01.journal | cmp - /app/examples/01.answers
rm -f /tmp/logindex-check
