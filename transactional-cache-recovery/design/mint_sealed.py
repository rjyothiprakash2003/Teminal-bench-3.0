#!/usr/bin/env python3
"""Mint sealed expect reports from the oracle implementation."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORACLE = ROOT / "solution" / "src" / "cachebox.py"
SEALED = ROOT / "tests" / "sealed"

SCENARIOS: dict[str, dict] = {
    "happy_commit": {
        "steps": [
            {"op": "begin", "txn": "t1"},
            {"op": "put", "txn": "t1", "key": "a", "value": 10, "expected": 0},
            {"op": "get", "key": "a"},
            {"op": "commit", "txn": "t1", "op_id": "c1"},
            {"op": "get", "key": "a"},
            {"op": "snapshot"},
        ]
    },
    "stale_read": {
        "steps": [
            {"op": "begin", "txn": "t1"},
            {"op": "put", "txn": "t1", "key": "k", "value": "staged", "expected": 0},
            {"op": "get", "key": "k"},
            {"op": "abort", "txn": "t1"},
            {"op": "get", "key": "k"},
            {"op": "snapshot"},
        ]
    },
    "version_conflict": {
        "steps": [
            {"op": "begin", "txn": "t1"},
            {"op": "put", "txn": "t1", "key": "x", "value": "v1", "expected": 0},
            {"op": "commit", "txn": "t1", "op_id": "o1"},
            {"op": "begin", "txn": "t2"},
            {"op": "begin", "txn": "t3"},
            {"op": "put", "txn": "t3", "key": "x", "value": "v3", "expected": 1},
            {"op": "commit", "txn": "t3", "op_id": "o3"},
            {"op": "put", "txn": "t2", "key": "x", "value": "v2", "expected": 1},
            {"op": "commit", "txn": "t2", "op_id": "o2"},
            {"op": "get", "key": "x"},
            {"op": "snapshot"},
        ]
    },
    "idempotent_retry": {
        "steps": [
            {"op": "begin", "txn": "t1"},
            {"op": "put", "txn": "t1", "key": "n", "value": 1, "expected": 0},
            {"op": "commit", "txn": "t1", "op_id": "same"},
            {"op": "begin", "txn": "t2"},
            {"op": "put", "txn": "t2", "key": "n", "value": 99, "expected": 1},
            {"op": "commit", "txn": "t2", "op_id": "same"},
            {"op": "get", "key": "n"},
            {"op": "snapshot"},
        ]
    },
    "crash_after_wal": {
        "steps": [
            {"op": "begin", "txn": "t1"},
            {"op": "put", "txn": "t1", "key": "w", "value": "wal", "expected": 0},
            {"op": "commit", "txn": "t1", "op_id": "w1", "fault": "after_wal"},
            {"op": "open"},
            {"op": "get", "key": "w"},
            {"op": "recover"},
            {"op": "recover"},
            {"op": "snapshot"},
        ]
    },
    "crash_after_tmp": {
        "steps": [
            {"op": "begin", "txn": "t1"},
            {"op": "put", "txn": "t1", "key": "t", "value": "tmp", "expected": 0},
            {"op": "commit", "txn": "t1", "op_id": "t1", "fault": "after_tmp"},
            {"op": "open"},
            {"op": "get", "key": "t"},
            {"op": "snapshot"},
        ]
    },
    "crash_tmp_partial": {
        "steps": [
            {"op": "begin", "txn": "t1"},
            {"op": "put", "txn": "t1", "key": "p", "value": "partial", "expected": 0},
            {"op": "commit", "txn": "t1", "op_id": "p1", "fault": "after_tmp_partial"},
            {"op": "open"},
            {"op": "get", "key": "p"},
            {"op": "snapshot"},
        ]
    },
    "crash_before_cache": {
        "steps": [
            {"op": "begin", "txn": "t1"},
            {"op": "put", "txn": "t1", "key": "c", "value": "cache", "expected": 0},
            {"op": "commit", "txn": "t1", "op_id": "c1", "fault": "before_cache"},
            {"op": "open"},
            {"op": "get", "key": "c"},
            {"op": "snapshot"},
        ]
    },
    "delete_and_reuse": {
        "steps": [
            {"op": "begin", "txn": "t1"},
            {"op": "put", "txn": "t1", "key": "d", "value": "live", "expected": 0},
            {"op": "commit", "txn": "t1", "op_id": "d1"},
            {"op": "begin", "txn": "t2"},
            {"op": "delete", "txn": "t2", "key": "d", "expected": 1},
            {"op": "get", "key": "d"},
            {"op": "commit", "txn": "t2", "op_id": "d2"},
            {"op": "get", "key": "d"},
            {"op": "begin", "txn": "t3"},
            {"op": "put", "txn": "t3", "key": "d", "value": "again", "expected": 0},
            {"op": "commit", "txn": "t3", "op_id": "d3"},
            {"op": "get", "key": "d"},
            {"op": "snapshot"},
        ]
    },
    "repeated_recover": {
        "steps": [
            {"op": "begin", "txn": "t1"},
            {"op": "put", "txn": "t1", "key": "r", "value": [1, 2], "expected": 0},
            {"op": "commit", "txn": "t1", "op_id": "r1"},
            {"op": "recover"},
            {"op": "recover"},
            {"op": "open"},
            {"op": "snapshot"},
        ]
    },
    "ooo_multi_key": {
        "steps": [
            {"op": "begin", "txn": "a"},
            {"op": "put", "txn": "a", "key": "k1", "value": "A", "expected": 0},
            {"op": "put", "txn": "a", "key": "k2", "value": "B", "expected": 0},
            {"op": "commit", "txn": "a", "op_id": "m1"},
            {"op": "begin", "txn": "b"},
            {"op": "begin", "txn": "c"},
            {"op": "put", "txn": "c", "key": "k1", "value": "C1", "expected": 1},
            {"op": "put", "txn": "c", "key": "k2", "value": "C2", "expected": 1},
            {"op": "commit", "txn": "c", "op_id": "m3"},
            {"op": "put", "txn": "b", "key": "k1", "value": "B1", "expected": 1},
            {"op": "put", "txn": "b", "key": "k2", "value": "B2", "expected": 1},
            {"op": "commit", "txn": "b", "op_id": "m2"},
            {"op": "snapshot"},
        ]
    },
    "conflict_idempotent": {
        "steps": [
            {"op": "begin", "txn": "t1"},
            {"op": "put", "txn": "t1", "key": "z", "value": "base", "expected": 0},
            {"op": "commit", "txn": "t1", "op_id": "z0"},
            {"op": "begin", "txn": "t2"},
            {"op": "put", "txn": "t2", "key": "z", "value": "bad", "expected": 0},
            {"op": "commit", "txn": "t2", "op_id": "zbad"},
            {"op": "begin", "txn": "t3"},
            {"op": "put", "txn": "t3", "key": "z", "value": "worse", "expected": 0},
            {"op": "commit", "txn": "t3", "op_id": "zbad"},
            {"op": "get", "key": "z"},
            {"op": "snapshot"},
        ]
    },
}


def main() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("cachebox_oracle", ORACLE)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    SEALED.mkdir(parents=True, exist_ok=True)
    for name, scenario in SCENARIOS.items():
        case = SEALED / name
        if case.exists():
            shutil.rmtree(case)
        case.mkdir(parents=True)
        (case / "scenario.json").write_text(
            json.dumps(scenario, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "store"
            store.mkdir()
            report = mod.run_scenario(store, scenario)
        (case / "expect_report.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(name, "ok", len(report["results"]))


if __name__ == "__main__":
    main()
