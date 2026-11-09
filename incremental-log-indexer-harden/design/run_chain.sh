#!/bin/bash
# The chain, in order, after the last edit: build the reference, mint the verifier from the
# witness table (refusing any witness outside the domain or where reference and model
# disagree), then every proof; it ends with one verdict line. Usage: run_chain.sh <scratch>
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd); task=$(dirname "$here"); s=${1:?scratch dir}
mkdir -p "$s/ref/reference" "$s/refbin"
cp "$task/solution/main.go" "$task/environment/app/logindex/go.mod" "$s/ref/reference/"
fail=0
step() { echo "== $1"; shift; "$@" || { echo "STEP FAILED"; fail=1; }; }
step build "$here/gobuild.sh" "$s/refbin" "$s/ref/reference"
ref="$s/refbin/reference"
step mint python3 -I "$here/gen_tests.py" "$ref"
step examples bash -c "for f in '$task'/environment/app/examples/*.journal; do cmp -s <('$ref' < \"\$f\") \"\${f%.journal}.answers\" || { echo \"example \$f differs\"; exit 1; }; done; echo 'examples: reference prints all 8 exactly'"
step inputs python3 -I "$here/inputs_gate.py"
step fuzz python3 -I "$here/fuzz.py" "$ref"
step sweep python3 -I "$here/sweep.py" "$s"
step guard python3 -I "$here/overstrict.py" "$s"
step leak python3 -I "$here/leak_scan.py"
step checks python3 -I "$here/checks.py" "$ref"
step farm "$here/farm.sh" "$s" oracle nop lazy hostile thirdparty multipackage
echo "CHAIN VERDICT: $([ $fail = 0 ] && echo GREEN || echo RED)"
