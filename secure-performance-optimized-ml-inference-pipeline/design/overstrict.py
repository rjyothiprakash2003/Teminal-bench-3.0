#!/usr/bin/env python3
"""Over-strictness guard: tolerated trees graded inside Docker (same as rivals)."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOL = ROOT / "solution"
ENV = ROOT / "environment" / "kelso"
IMAGE = (
    "public.ecr.aws/docker/library/golang:1.24-bookworm@"
    "sha256:1a6d4452c65dea36aac2e2d606b01b4a029ec90cc1ae53890540ce6173ea77ac"
)

TOLERATED = [
    (
        "rename_unexported_cache_key",
        "internal/scorekit/scorekit.go",
        "func (k *Kit) cacheKey(x []float64) string {\n",
        "func (k *Kit) featureKey(x []float64) string {\n",
        "\tkey := k.cacheKey(app.X)\n",
        "\tkey := k.featureKey(app.X)\n",
    ),
]


def _stage(dest: Path) -> None:
    shutil.copytree(ENV, dest, dirs_exist_ok=True)
    (dest / "bin").mkdir(parents=True, exist_ok=True)
    for rel in (
        "internal/scorekit/scorekit.go",
        "internal/httpapi/httpapi.go",
        "internal/tickbench/tickbench.go",
        "internal/ridgeio/ridgeio.go",
        "cmd/ridgecast/ridgecast.go",
        "ops/ci-gate.sh",
    ):
        (dest / rel).write_bytes((SOL / rel).read_bytes())


def _selfcheck(tree: Path) -> None:
    r = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{tree}:/app",
            "-w",
            "/app",
            IMAGE,
            "bash",
            "-c",
            "set -euo pipefail; export PATH=/usr/local/go/bin:/go/bin:$PATH; "
            "go build -o /app/bin/ridgecast ./cmd/ridgecast; "
            "/app/bin/ridgecast selfcheck | tee /app/_sc.out; "
            "grep -qx 'selfcheck ok' /app/_sc.out",
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if r.returncode != 0:
        raise RuntimeError(r.stdout + "\n" + r.stderr)


def main() -> None:
    if shutil.which("docker") is None:
        print("overstrict SKIP: no docker")
        raise SystemExit(0)

    with tempfile.TemporaryDirectory(prefix="kelso-os-") as td:
        control = Path(td) / "control"
        _stage(control)
        try:
            _selfcheck(control)
        except RuntimeError as e:
            print(f"CONTROL FAIL: {e}")
            raise SystemExit(1) from e
        print("overstrict control PASS")

        for name, rel, a, b, c, d in TOLERATED:
            t = Path(td) / name
            shutil.copytree(control, t)
            text = (t / rel).read_text()
            if text.count(a) != 1 or text.count(c) != 1:
                print(f"STOP: tolerated patch {name} no longer applies")
                raise SystemExit(1)
            (t / rel).write_text(text.replace(a, b, 1).replace(c, d, 1))
            try:
                _selfcheck(t)
            except RuntimeError as e:
                print(f"OVERSTRICT FAIL {name}: {e}")
                raise SystemExit(1) from e
            print(f"tolerated PASS {name}")

    print(f"overstrict: {len(TOLERATED)} trees PASS")


if __name__ == "__main__":
    main()
