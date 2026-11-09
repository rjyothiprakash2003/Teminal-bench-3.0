#!/bin/bash
# Usage: gobuild.sh <out_dir> <src_dir>...  builds each src dir (holding main.go and go.mod)
# into <out_dir>/<basename> as a darwin/arm64 binary, using the task's pinned Go image.
set -euo pipefail
out=$(cd "$1" && pwd); shift
args=()
for s in "$@"; do args+=("/src/$(basename "$s")"); done
parent=$(cd "$(dirname "$1")" && pwd)
docker run --rm -v "$parent":/src:ro -v "$out":/out -v ilx-gocache:/root/.cache/go-build \
  -e GOOS=darwin -e GOARCH=arm64 -e CGO_ENABLED=0 -e GOTOOLCHAIN=local -e GOFLAGS=-mod=mod \
  public.ecr.aws/docker/library/golang:1.24-bookworm@sha256:1a6d4452c65dea36aac2e2d606b01b4a029ec90cc1ae53890540ce6173ea77ac \
  bash -c 'rc=0; for d in "$@"; do n=$(basename "$d"); (cd "$d" && go build -o /out/$n . ) || { echo "BUILD FAIL $n"; rc=1; }; done; exit $rc' _ "${args[@]}"
