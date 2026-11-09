#!/bin/bash
# Patch the pinned checkout, then copy the package to the path grading imports.
set -euo pipefail
cd /app/repo
git apply /solution/feature.patch
cp -R src/pybamm /app/submission/pybamm
