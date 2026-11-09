#!/bin/bash
# The builder was lost with the old control host, so this puts a working one back
# in the tool directory. The sources beside this script implement the facility
# file reading, the period and day count rules and the accrual of SN-208.
set -e
here="$(cd "$(dirname "$0")" && pwd)"
cd /app/tools/schedbuild
cp "$here/reference/dates.go" dates.go
cp "$here/reference/parse.go" parse.go
cp "$here/reference/accrue.go" accrue.go
cp "$here/reference/assignment.go" assignment.go
cp "$here/reference/main.go" main.go
