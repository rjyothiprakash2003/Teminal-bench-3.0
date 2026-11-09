#!/usr/bin/env bash
# Design chain for kelso-ridgecast-week41. Runs in order; prints a verdict.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
cd "$ROOT"

echo "== inputs: fixture rows legal under schema =="
python3 - <<'PY'
import json, sys
from pathlib import Path
sys.path.insert(0, "design")
from model import intake_illegal, load_schema, score_fixture, bench_ledger
schema = load_schema()
fx = Path("environment/kelso/fixtures/week41-intake.jsonl")
for i, line in enumerate(fx.read_text().splitlines()):
    row = json.loads(line)
    tag = intake_illegal(row, schema)
    assert tag is None, (i, tag, row)
# refusal samples break the named rule
assert intake_illegal({**json.loads(fx.read_text().splitlines()[0]), "extra": 1}, schema) == "extra"
print("inputs: week41 legal; extra-key refusal tagged")
PY

echo "== mint =="
python3 design/mint_sealed.py

echo "== model self-check =="
python3 design/model.py

echo "== sentence × site =="
python3 design/sentence_sites.py

echo "== rivals =="
python3 design/rivals.py

echo "== overstrict =="
python3 design/overstrict.py

echo "== leak scan =="
python3 design/leak_scan.py

echo "== distance / duplicates / fuzz =="
python3 - <<'PY'
import hashlib, json, sys
from pathlib import Path
from collections import defaultdict
sys.path.insert(0, "design")
from model import score_micros, load_weights, load_schema

files = [p for p in Path("tests").rglob("*") if p.is_file() and p.name != ".DS_Store"]
by_hash, by_name = defaultdict(list), defaultdict(list)
for p in files:
    by_hash[hashlib.sha256(p.read_bytes()).hexdigest()].append(str(p))
    by_name[p.name].append(str(p))
dup_hash = {k: v for k, v in by_hash.items() if len(v) > 1}
# holdout weights twin agent weights — expected and declared in leak_scan
print(f"tests files {len(files)}; hash-colliding groups {len(dup_hash)}")
name_coll = {k: v for k, v in by_name.items() if len(v) > 1}
print(f"basename collisions {len(name_coll)}")
w, s = load_weights(), load_schema()
for seed in (0, 1):
    row = {
        "id": f"F{seed}",
        "tenure_months": 48 + seed,
        "util_ratio": 0.35,
        "delinq_24": 0.8,
        "inq_6": 1.2,
        "revol_bal": 8500,
        "open_trades": 7,
    }
    assert isinstance(score_micros(row, w, s), int)
print("fuzz seeds 0,1 score ok")
PY

echo "CHAIN VERDICT: GREEN"
