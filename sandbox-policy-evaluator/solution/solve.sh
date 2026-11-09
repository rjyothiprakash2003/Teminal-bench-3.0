#!/bin/bash
set -e
cd /app
cp /solution/evaluate.go /app/evaluate.go
cp /solution/go.mod /app/go.mod
go build -o /app/evaluate .
./evaluate --policy /app/policy --book /app/cordon --ops /app/probes --out /app/out
