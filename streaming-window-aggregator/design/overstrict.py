#!/usr/bin/env python3
"""Over-strictness guard: reference control, then tolerated buffer-then-assign tree."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REF = ROOT / "solution" / "src" / "aggregator.py"
TOL = ROOT / "design" / "tolerated" / "buffer_then_assign.py"
SEALED = ROOT / "tests" / "sealed"


def surface(aggregator: Path, work: Path) -> list[str]:
    fails = []
    for case in sorted(p for p in SEALED.iterdir() if p.is_dir()):
        expect = case / "expect.json"
        if not expect.is_file():
            continue
        staged = work / f"staged_{case.name}"
        if staged.exists():
            shutil.rmtree(staged)
        staged.mkdir(parents=True)
        shutil.copy2(case / "config.json", staged / "config.json")
        shutil.copy2(case / "events.jsonl", staged / "events.jsonl")
        out = work / f"{case.name}.json"
        proc = subprocess.run(
            ["python3", str(aggregator), "--stream", str(staged), "--out", str(out)],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if proc.returncode != 0 or not out.is_file() or out.read_bytes() != expect.read_bytes():
            fails.append(case.name)
    return fails


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        control = tdp / "ref.py"
        shutil.copy(REF, control)
        cfails = surface(control, tdp / "cwork")
        if cfails:
            raise SystemExit(f"REFERENCE CONTROL FAILED: {cfails}")
        print("control PASS")

        tol = tdp / "tol.py"
        shutil.copy(TOL, tol)
        tfails = surface(tol, tdp / "twork")
        if tfails:
            raise SystemExit(f"TOLERATED TREE FAILED (over-strict?): {tfails}")
        print("tolerated_buffer_then_assign PASS")
        print("overstrict trees 1, failures 0")


if __name__ == "__main__":
    main()
