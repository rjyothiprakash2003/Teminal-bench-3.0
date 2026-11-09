#!/bin/bash
# Local farm: builds both images (linux/amd64, as the platform builder), then for each mode
# runs the agent image (native platform unless FARM_PLATFORM is set: Go crashes under amd64
# emulation on Apple Silicon), stages the mode's tree, collects the /app/logindex artifact and runs
# the verifier image on it with no network. Prints MODE reward. Modes: oracle nop lazy hostile thirdparty multipackage.
set -euo pipefail
task=$(cd "$(dirname "$0")/.." && pwd)
scratch=${1:?scratch dir}; shift
modes=${*:-oracle nop lazy}
plat=${FARM_PLATFORM:-}
docker build -q $plat -t ilx-env "$task/environment" >/dev/null
docker build -q $plat -t ilx-tests "$task/tests" >/dev/null
for mode in $modes; do
  out="$scratch/farm-$mode"; rm -rf "$out"; mkdir -p "$out/logs"
  cid=$(docker create $plat ilx-env sleep 600)
  docker start "$cid" >/dev/null
  case $mode in
    oracle) docker cp "$task/solution" "$cid:/solution" >/dev/null
            docker exec "$cid" bash /solution/solve.sh ;;
    nop) ;;
    lazy) docker cp "$scratch/trees/lazy/main.go" "$cid:/app/logindex/main.go" >/dev/null ;;
    hostile|thirdparty|multipackage) docker exec "$cid" rm -rf /app/logindex && docker cp "$task/design/farm-trees/$mode" "$cid:/app/logindex" >/dev/null ;;
  esac
  docker cp "$cid:/app/logindex" "$out/logindex" >/dev/null
  docker rm -f "$cid" >/dev/null
  docker volume rm -f ilx-logs >/dev/null; docker volume create ilx-logs >/dev/null
  docker run --rm $plat --network none -v "$out/logindex:/app/logindex:ro" -v ilx-logs:/logs/verifier \
    ilx-tests bash /tests/test.sh > "$out/pytest.txt" 2>&1 || true
  echo "$mode reward $(docker run --rm -v ilx-logs:/l ilx-tests cat /l/reward.txt) ($(grep -E '^(=+ .*(passed|failed|error).* =+)$' "$out/pytest.txt" | tail -1))"
done
