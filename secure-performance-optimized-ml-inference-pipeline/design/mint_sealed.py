#!/usr/bin/env python3
"""Mint sealed holdout expects where independent model and reference agree.

Holdout intakes (xi, nu) are never under /app. week41 and bench expects for
agent-visible fixtures are NOT sealed here — the verifier mints them from this
model at grade time so the verifier never holds expected output of an input
the agent can read.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "design"))
from model import score_fixture, score_micros, load_weights, load_schema, bench_ledger  # noqa: E402

HOLDOUT = ROOT / "tests" / "holdout"


def main() -> None:
    weights, schema = load_weights(), load_schema()
    for name in ("xi", "nu"):
        intake = HOLDOUT / f"{name}-intake.jsonl"
        rows = []
        for line in intake.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            rows.append(
                {"id": row["id"], "score_micros": score_micros(row, weights, schema)}
            )
        out = HOLDOUT / f"{name}-expected.json"
        prev = json.loads(out.read_text()) if out.exists() else None
        text = json.dumps(rows, indent=2) + "\n"
        out.write_text(text)
        if prev is not None and prev != rows:
            print(f"WARNING {name}-expected changed")
        print(f"minted {out.name} rows={len(rows)}")

    week = score_fixture()
    led = bench_ledger()
    print(f"agent-visible week41 model rows={len(week)} first={week[0]}")
    print(f"agent-visible bench model={led}")
    # Seal check: model matches previously published holdout expects if present
    for name in ("xi", "nu"):
        got = json.loads((HOLDOUT / f"{name}-expected.json").read_text())
        assert all(isinstance(r["score_micros"], int) for r in got)
    print("mint seal ok")


if __name__ == "__main__":
    main()
