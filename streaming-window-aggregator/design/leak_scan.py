#!/usr/bin/env python3
"""Leak scan: hash agent-visible bytes vs verifier-held bytes; declare identical pairs."""

from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / "environment" / "app"
TESTS = ROOT / "tests"


def walk(root: Path) -> dict[str, str]:
    out = {}
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if p.name == ".DS_Store" or "__pycache__" in p.parts:
            continue
        rel = str(p.relative_to(root))
        out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def main() -> None:
    agent = walk(ENV)
    ver = {}
    for p in TESTS.rglob("*"):
        if not p.is_file():
            continue
        if p.name == ".DS_Store" or "__pycache__" in p.parts:
            continue
        rel = str(p.relative_to(TESTS))
        ver[rel] = hashlib.sha256(p.read_bytes()).hexdigest()

    by_hash_agent: dict[str, list[str]] = {}
    for rel, h in agent.items():
        by_hash_agent.setdefault(h, []).append("agent:" + rel)
    pairs = []
    for rel, h in ver.items():
        if h in by_hash_agent:
            for a in by_hash_agent[h]:
                pairs.append((a, "verifier:" + rel, h))

    print(f"agent files {len(agent)}, verifier files {len(ver)}")
    print(f"identical-byte pairs {len(pairs)}")
    bad = []
    for a, v, h in pairs:
        if v.endswith("expect.json") or "expect" in v:
            bad.append((a, v, h, "expect paired with agent-visible bytes"))
        else:
            print(f"DECLARED {a} == {v}")

    if bad:
        print("LEAKS:")
        for row in bad:
            print(row)
        raise SystemExit(1)
    print("leak scan clean")


if __name__ == "__main__":
    main()
