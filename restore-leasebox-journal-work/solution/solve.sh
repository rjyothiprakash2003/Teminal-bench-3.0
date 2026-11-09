#!/bin/bash
set -euo pipefail
export PATH="/usr/local/go/bin:${PATH:-/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cp "$HERE/leasebox.go" /app/leasebox/leasebox.go
cp "$HERE/gate.ini" /app/conf/gate.ini
mkdir -p /app/var /app/out
chmod 1777 /app/var /app/out
cd /app/leasebox
go build -o /tmp/leasebox .
export LEASEBOX_NOW=1700000000
export LEASEBOX_JOURNAL=/app/var/journal.bin
/tmp/leasebox grant demo-a crew-1
/tmp/leasebox grant demo-b crew-2
/tmp/leasebox compact
/tmp/leasebox report
/tmp/leasebox verify
chmod 1777 /app/var /app/out
# Leave default journal artifacts owned so the verifier sandbox (uid 12000) can recreate locks.
chown -R 12000:12000 /app/var /app/out 2>/dev/null || true
chmod -R a+rwX /app/var /app/out
