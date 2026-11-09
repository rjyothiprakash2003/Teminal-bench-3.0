#!/usr/bin/env python3
"""Independent Kelso contract model — from serving-contract.txt + intake-schema.json.

Never import solution/ or environment Go. Where the page is silent (exact error
message wording beyond forbidden substrings; JSON key order in HTTP error
objects; whether serve logs to stdout), this file records a comment rather than
inventing a graded rule.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / "environment" / "kelso"
SCHEMA_PATH = ENV / "policy" / "intake-schema.json"
COEF_PATH = ENV / "weights" / "ridge-v3.coef"
SCALE_PATH = ENV / "weights" / "ridge-v3.scale"
PAGE = ENV / "policy" / "serving-contract.txt"
FIXTURE = ENV / "fixtures" / "week41-intake.jsonl"
ANCHOR = ENV / "policy" / "bench-anchor.json"
RECOVERY = ENV / "policy" / "bench-recovery-loadspec.txt"
LOADSPEC = ENV / "bench" / "loadspec.txt"

# Page silent: HTTP error message prose beyond token/filename bans.
# Page silent: whether Encode adds a trailing newline on success POST bodies.


def load_schema(path: Path = SCHEMA_PATH) -> dict[str, Any]:
    return json.loads(path.read_text())


def load_weights(
    coef_path: Path = COEF_PATH, scale_path: Path = SCALE_PATH
) -> tuple[float, dict[str, float], dict[str, float], dict[str, float]]:
    bias = 0.0
    coef: dict[str, float] = {}
    for line in coef_path.read_text().splitlines():
        p = line.split()
        if len(p) == 2 and p[0] == "bias":
            bias = float(p[1])
        elif len(p) == 3 and p[0] == "w":
            coef[p[1]] = float(p[2])
    mean: dict[str, float] = {}
    std: dict[str, float] = {}
    for line in scale_path.read_text().splitlines():
        p = line.split()
        if len(p) == 3 and p[0] == "mean":
            mean[p[1]] = float(p[2])
        elif len(p) == 3 and p[0] == "std":
            std[p[1]] = float(p[2])
    return bias, coef, mean, std


def field_names(schema: dict[str, Any] | None = None) -> list[str]:
    schema = schema or load_schema()
    return [f["name"] for f in schema["fields"]]


def score_micros(row: dict[str, Any], weights=None, schema=None) -> int:
    """Schema-order logit; std in the denominator once (not std*std)."""
    schema = schema or load_schema()
    if weights is None:
        weights = load_weights()
    bias, coef, mean, std = weights
    logit = bias
    for f in schema["fields"]:
        name = f["name"]
        z = (float(row[name]) - mean[name]) / std[name]
        logit += coef[name] * z
    return int(math.floor((1.0 / (1.0 + math.exp(-logit))) * 1_000_000 + 0.5))


def intake_illegal(row: dict[str, Any], schema=None) -> str | None:
    """Return refusal tag or None if legal. Tags name the rule broken."""
    schema = schema or load_schema()
    fields = {f["name"]: f for f in schema["fields"]}
    forbidden = set(schema.get("forbidden", []))
    if "id" not in row:
        return "missing_id"
    if not isinstance(row["id"], str) or row["id"] == "":
        return "bad_id"
    for k in row:
        if k == "id":
            continue
        if k in forbidden:
            return "forbidden"
        if k not in fields:
            return "extra"
    for name, f in fields.items():
        if name not in row:
            return "missing_field"
        v = row[name]
        if isinstance(v, bool) or v is None or isinstance(v, str):
            return "bad_type"
        if not isinstance(v, (int, float)):
            return "bad_type"
        if v < f["min"] or v > f["max"]:
            return "range"
    return None


def score_fixture(path: Path = FIXTURE) -> list[dict[str, Any]]:
    weights, schema = load_weights(), load_schema()
    out = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        assert intake_illegal(row, schema) is None, row
        out.append({"id": row["id"], "score_micros": score_micros(row, weights, schema)})
    return out


def _parse_scenarios(text: str) -> list[tuple[int, int]]:
    """Return list of (batch, count) from scenario lines (repeat * batch members)."""
    specs: list[tuple[int, int]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("scenario "):
            continue
        parts = line.split()
        # scenario NAME repeat R batch B
        try:
            ri = parts.index("repeat")
            bi = parts.index("batch")
            repeat = int(parts[ri + 1])
            batch = int(parts[bi + 1])
        except (ValueError, IndexError):
            continue
        specs.append((batch, repeat * batch))
    return specs


def recover_sc(anchor: Path = ANCHOR, recovery: Path = RECOVERY) -> tuple[int, int]:
    """Unique positive S,C with C < S matching recovery expansion to anchor ticks."""
    num = json.loads(anchor.read_text())
    specs = _parse_scenarios(recovery.read_text())
    n_solo = sum(c for b, c in specs if b == 1)
    n_coal = sum(c for b, c in specs if b > 1)
    if n_solo <= 0 or n_coal <= 0:
        raise ValueError("unique S/C not recovered")
    total = int(num["total_ticks"])
    n_req = int(num["total_requests"])
    p95 = int(num["p95_ticks"])
    p99 = int(num["p99_ticks"])
    avg = int(num["avg_latency_ticks"])
    cand: list[tuple[int, int]] = []
    for s in {p95, p99}:
        rem = total - n_solo * s
        if rem <= 0 or rem % n_coal != 0:
            continue
        c = rem // n_coal
        if c < 1 or c >= s:
            continue
        if _ledger_from_specs(specs, s, c)["total_requests"] != n_req:
            continue
        led = _ledger_from_specs(specs, s, c)
        if (
            led["total_ticks"] == total
            and led["avg_latency_ticks"] == avg
            and led["p95_ticks"] == p95
            and led["p99_ticks"] == p99
        ):
            cand.append((s, c))
    uniq = sorted(set(cand))
    if len(uniq) != 1:
        raise ValueError(f"S/C not unique: {uniq}")
    return uniq[0]


def _ledger_from_specs(specs: list[tuple[int, int]], s: int, c: int) -> dict[str, int]:
    costs: list[int] = []
    max_b = 1
    for batch, count in specs:
        cost = s if batch == 1 else c
        costs.extend([cost] * count)
        if batch > max_b:
            max_b = batch
    if not costs:
        raise ValueError("empty")
    n = len(costs)
    total = sum(costs)
    ordered = sorted(costs)
    def pct(numer: int, denom: int) -> int:
        idx = (numer * n + denom - 1) // denom - 1
        if idx < 0:
            idx = 0
        if idx >= n:
            idx = n - 1
        return ordered[idx]
    return {
        "total_requests": n,
        "total_ticks": total,
        "throughput": (1000000 * n + total // 2) // total if total else 0,
        "avg_latency_ticks": (total + n // 2) // n,
        "p95_ticks": pct(19, 20),
        "p99_ticks": pct(99, 100),
        "cpu_units": total,
        "memory_units": 64 + 4 * max_b,
    }


def bench_ledger(spec_path: Path = LOADSPEC) -> dict[str, int]:
    s, c = recover_sc()
    specs = _parse_scenarios(spec_path.read_text())
    if not specs:
        raise ValueError("no admitted scenarios")
    return _ledger_from_specs(specs, s, c)


def mean_lab_micros() -> int:
    schema = load_schema()
    weights = load_weights()
    _, _, mean, _ = weights
    row = {"id": "MEAN", **{f["name"]: mean[f["name"]] for f in schema["fields"]}}
    return score_micros(row, weights, schema)


def off_mean_lab_micros() -> int:
    schema = load_schema()
    weights = load_weights()
    _, _, mean, _ = weights
    row = {"id": "OFF", **{f["name"]: mean[f["name"]] for f in schema["fields"]}}
    row["tenure_months"] = 72
    row["util_ratio"] = 0.36
    return score_micros(row, weights, schema)


def main() -> None:
    page = PAGE.read_text()
    assert "serving contract" in page.lower() or "ridgecast" in page.lower()
    week = score_fixture()
    assert week[0]["score_micros"] == 389361
    assert mean_lab_micros() == 389361
    assert off_mean_lab_micros() == 364705
    led = bench_ledger()
    assert led["total_requests"] == 21
    assert led["cpu_units"] == led["total_ticks"]
    print(f"model ok: week41 rows={len(week)} bench={led}")


if __name__ == "__main__":
    main()
