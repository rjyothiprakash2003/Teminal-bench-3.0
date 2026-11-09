#!/usr/bin/env python3
"""Leak scan: hash agent-visible bytes vs verifier-held bytes; declare pairs."""

from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / "environment" / "kelso"
TESTS = ROOT / "tests"

# Verifier may mirror inputs. It must not hold expected output of agent-visible runs.
DECLARED = {
    (
        "agent:weights/ridge-v3.coef",
        "verifier:holdout/ridge-v3.coef",
        "sealed weight twin for independent model; not an expected score product",
    ),
    (
        "agent:weights/ridge-v3.scale",
        "verifier:holdout/ridge-v3.scale",
        "sealed scale twin for independent model; not an expected score product",
    ),
}


def walk(root: Path, prefix: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if p.name == ".DS_Store" or "__pycache__" in p.parts:
            continue
        # design/ is not under tests for agent; skip requirements.lock noise if needed
        rel = str(p.relative_to(root))
        out[prefix + rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def main() -> None:
    agent = walk(ENV, "agent:")
    ver = walk(TESTS, "verifier:")
    by_hash: dict[str, list[str]] = {}
    for rel, h in agent.items():
        by_hash.setdefault(h, []).append(rel)

    pairs: list[tuple[str, str]] = []
    for rel, h in ver.items():
        for a in by_hash.get(h, []):
            pairs.append((a, rel))

    print(f"agent files {len(agent)}, verifier files {len(ver)}")
    print(f"identical-byte pairs {len(pairs)}")
    bad = []
    declared_hit = set()
    for a, v in pairs:
        reason = None
        for da, dv, why in DECLARED:
            if a == da and v == dv:
                reason = why
                declared_hit.add((da, dv))
                break
        if reason:
            print(f"DECLARED {a} == {v} :: {reason}")
            continue
        if "expected" in v.lower():
            bad.append((a, v, "expected-like bytes twin of agent-visible file"))
        else:
            bad.append((a, v, "undeclared identical pair"))
    for da, dv, why in DECLARED:
        if (da, dv) not in declared_hit:
            bad.append((da, dv, "declared pair missing on disk"))
    # Semantic ban: week41-expected / bench-expected must not live under tests/holdout
    for name in ("week41-expected.json", "bench-expected.json"):
        p = TESTS / "holdout" / name
        if p.exists():
            bad.append(
                (
                    f"agent:fixtures-or-bench",
                    f"verifier:holdout/{name}",
                    "forbidden: sealed expected output of agent-visible inputs",
                )
            )
    if bad:
        print("LEAKS:")
        for row in bad:
            print(" ", row)
        raise SystemExit(1)
    print(f"leak scan clean; declared {len(declared_hit)}")


if __name__ == "__main__":
    main()
