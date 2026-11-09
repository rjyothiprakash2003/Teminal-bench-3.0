#!/usr/bin/env bash
# Design chain: mint, seal, model↔ref, rivals, sentence×site, overstrict, leak.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
cd "$ROOT"

echo "== mint sealed =="
python3 design/mint_sealed.py

echo "== model agrees with reference on sealed =="
python3 - <<'PY'
import sys, tempfile, subprocess, shutil
from pathlib import Path
sys.path.insert(0, "design")
from model import run_stream, result_bytes
ROOT = Path(".")
REF = ROOT / "solution/src/aggregator.py"
SEALED = ROOT / "tests/sealed"
bad = []
n = 0
for case in sorted(p for p in SEALED.iterdir() if p.is_dir()):
    n += 1
    with tempfile.TemporaryDirectory() as td:
        staged = Path(td) / "stream"
        staged.mkdir()
        shutil.copy2(case / "config.json", staged / "config.json")
        shutil.copy2(case / "events.jsonl", staged / "events.jsonl")
        m = run_stream(staged)
        assert m.ok, (case, m.stderr)
        out = Path(td) / "out.json"
        proc = subprocess.run(
            ["python3", str(REF), "--stream", str(staged), "--out", str(out)],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0 or out.read_bytes() != result_bytes(m.result):
            bad.append(case.name)
if bad:
    raise SystemExit(f"model/ref disagree: {bad}")
print(f"model↔ref agree on {n} sealed dirs")
PY

echo "== sentence × site =="
python3 design/sentence_sites.py

echo "== rivals =="
python3 design/rivals.py

echo "== overstrict =="
python3 design/overstrict.py

echo "== leak scan =="
python3 design/leak_scan.py

echo "== broken shipped fails regression =="
python3 - <<'PY'
import importlib.util
from pathlib import Path

def load(path):
    spec = importlib.util.spec_from_file_location("m", path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
ref = load("solution/src/aggregator.py")
broken = load("environment/app/src/aggregator.py")
j = Path("environment/app/examples/regression")
assert ref.aggregate(j) != broken.aggregate(j)
j2 = Path("environment/app/stream")
assert ref.aggregate(j2) == broken.aggregate(j2)
print("broken: simple pass, regression fail — ok")
PY

echo "CHAIN VERDICT: GREEN"
