#!/bin/bash
set -euo pipefail
cp /solution/cmd/casaudit/main.go /app/cmd/casaudit/main.go
cp /solution/go.mod /app/go.mod
mkdir -p /app/bin /app/out
cd /app
go build -o /app/bin/casaudit ./cmd/casaudit
chmod 755 /app/bin/casaudit
/app/bin/casaudit --root /app/samples/plain/tree --audience /app/samples/plain/roster.json --report /app/out/plain-report.json
/app/bin/casaudit --root /app/samples/linked/tree --audience /app/samples/linked/roster.json --report /app/out/linked-report.json
