#!/usr/bin/env python3
"""Mint sealed expect.json bytes from CAPABILITY.md via contract_oracle (not the Go binary).

Optional CHECK_GO=1 compares each mint against /tmp/casaudit and aborts on drift.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from contract_oracle import mint_expect
from trees import materialize

ROOT = Path("/task")
BIN = Path("/tmp/casaudit")
SEALED = ROOT / "tests" / "sealed"


def _go_report(tree: Path, aud: Path) -> bytes | None:
    if not BIN.is_file():
        return None
    report = aud.parent / "go-report.json"
    proc = subprocess.run(
        [str(BIN), "--root", str(tree), "--audience", str(aud), "--report", str(report)],
        capture_output=True,
        timeout=60,
    )
    if proc.returncode != 0:
        raise SystemExit(
            f"Go cross-check failed rc={proc.returncode} stderr={proc.stderr!r}"
        )
    return report.read_bytes()


def main() -> None:
    check_go = os.environ.get("CHECK_GO", "") == "1"
    for case_dir in sorted(SEALED.iterdir()):
        if not case_dir.is_dir():
            continue
        name = case_dir.name
        manifest = json.loads((case_dir / "manifest.json").read_text())
        roster_bytes = (case_dir / "roster.json").read_bytes()
        work = Path(tempfile.mkdtemp(prefix=f"mint-{name}-"))
        try:
            tree = materialize(work / "fs", manifest)
            aud = work / "roster.json"
            aud.write_bytes(roster_bytes)
            os.chmod(aud, 0o644)
            expect = mint_expect(tree, aud)
            if check_go:
                go_bytes = _go_report(tree, aud)
                if go_bytes is not None and go_bytes != expect:
                    raise SystemExit(
                        f"DRIFT {name}: contract_oracle != Go reference "
                        f"({len(expect)} vs {len(go_bytes)} bytes)"
                    )
            (case_dir / "expect.json").write_bytes(expect)
            data = json.loads(expect)
            print(f"{name}: findings={len(data['findings'])} skipped={len(data['skipped'])}")
        finally:
            shutil.rmtree(work, ignore_errors=True)
    print("done")


if __name__ == "__main__":
    main()
