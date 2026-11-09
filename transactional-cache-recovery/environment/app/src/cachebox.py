#!/usr/bin/env python3
"""Starter transactional cache — incomplete recovery and consistency rules."""

from __future__ import annotations

import json
import os
import re
import stat as sm
import sys
from pathlib import Path
from typing import Any

FLAGS = {"--store", "--scenario", "--report"}
IDENT_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
COMMITTED = "committed.json"
TMP = "committed.json.tmp"
WAL = "wal.jsonl"


def refuse() -> None:
    sys.stderr.write("refuse: bad args\n")
    raise SystemExit(2)


def parse_args(argv: list[str]) -> tuple[str, str, str]:
    store = scenario = report = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a not in FLAGS:
            refuse()
        if i + 1 >= len(argv):
            refuse()
        val = argv[i + 1]
        if not val or val in FLAGS:
            refuse()
        if a == "--store":
            store = val
        elif a == "--scenario":
            scenario = val
        else:
            report = val
        i += 2
    if store is None or scenario is None or report is None:
        refuse()
    return store, scenario, report


def check_store(path: str) -> Path:
    p = Path(path)
    try:
        st = p.lstat()
    except OSError:
        refuse()
    if not sm.S_ISDIR(st.st_mode) or p.is_symlink():
        refuse()
    return p


def check_scenario(path: str) -> Path:
    p = Path(path)
    try:
        st = p.lstat()
    except OSError:
        refuse()
    if not sm.S_ISREG(st.st_mode) or p.is_symlink():
        refuse()
    return p


def valid_ident(s: Any) -> bool:
    return isinstance(s, str) and IDENT_RE.fullmatch(s) is not None


class CacheStore:
    """Naive write-through cache: mutates early, replays WAL, promotes tmp."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.committed_path = root / COMMITTED
        self.tmp_path = root / TMP
        self.wal_path = root / WAL
        self.cache: dict[str, dict[str, Any]] = {}
        self.ops: dict[str, dict[str, Any]] = {}
        self.txns: dict[str, dict[str, dict[str, Any]]] = {}
        self._last_recover: dict[str, Any] = self.recover()

    def recover(self) -> dict[str, Any]:
        discarded_tmp = False
        # Bug: promote tmp into committed if present (partial writes become truth).
        if self.tmp_path.exists():
            try:
                os.replace(self.tmp_path, self.committed_path)
                discarded_tmp = False
            except OSError:
                discarded_tmp = True

        entries: dict[str, Any] = {}
        ops: dict[str, Any] = {}
        if self.committed_path.exists():
            try:
                data = json.loads(self.committed_path.read_text(encoding="utf-8"))
                entries = dict(data.get("entries") or {})
                ops = dict(data.get("ops") or {})
            except (OSError, json.JSONDecodeError):
                entries, ops = {}, {}

        pending = 0
        # Bug: replay every WAL intent into entries (duplicates / uncommitted apply).
        if self.wal_path.exists():
            try:
                for line in self.wal_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("type") != "intent":
                        continue
                    pending += 1
                    for key, change in (rec.get("writes") or {}).items():
                        if change.get("delete"):
                            entries.pop(key, None)
                        else:
                            prev = entries.get(key)
                            ver = 1 if prev is None else int(prev["version"]) + 1
                            entries[key] = {"value": change.get("value"), "version": ver}
            except OSError:
                pass

        self.cache = {
            k: {"value": v["value"], "version": int(v["version"])}
            for k, v in entries.items()
        }
        self.ops = ops
        self.txns.clear()
        self._last_recover = {
            "status": "ok",
            "discarded_tmp": discarded_tmp,
            "pending_intents": pending,
        }
        return self._last_recover

    def snapshot(self) -> dict[str, Any]:
        return {
            "entries": {k: dict(v) for k, v in sorted(self.cache.items())},
            "ops": {k: dict(v) for k, v in sorted(self.ops.items())},
        }

    def begin(self, txn: str) -> dict[str, Any]:
        if not valid_ident(txn):
            return {"status": "error", "error": "bad txn"}
        if txn in self.txns:
            return {"status": "error", "error": "txn open"}
        self.txns[txn] = {}
        return {"status": "ok"}

    def put(self, txn: str, key: str, value: Any, expected: int) -> dict[str, Any]:
        if not valid_ident(txn) or txn not in self.txns:
            return {"status": "error", "error": "txn missing"}
        if not valid_ident(key):
            return {"status": "error", "error": "bad key"}
        self.txns[txn][key] = {"value": value, "expected": expected, "delete": False}
        # Bug: staged write becomes visible immediately.
        prev = self.cache.get(key)
        ver = 1 if prev is None else int(prev["version"]) + 1
        self.cache[key] = {"value": value, "version": ver}
        return {"status": "ok"}

    def delete(self, txn: str, key: str, expected: int) -> dict[str, Any]:
        if not valid_ident(txn) or txn not in self.txns:
            return {"status": "error", "error": "txn missing"}
        if not valid_ident(key):
            return {"status": "error", "error": "bad key"}
        self.txns[txn][key] = {"value": None, "expected": expected, "delete": True}
        self.cache.pop(key, None)
        return {"status": "ok"}

    def abort(self, txn: str) -> dict[str, Any]:
        if txn not in self.txns:
            return {"status": "error", "error": "txn missing"}
        del self.txns[txn]
        return {"status": "ok"}

    def get(self, key: str) -> dict[str, Any]:
        item = self.cache.get(key)
        if item is None:
            return {"found": False}
        return {"found": True, "value": item["value"], "version": item["version"]}

    def commit(
        self, txn: str, op_id: str, fault: str | None = None
    ) -> dict[str, Any]:
        if txn not in self.txns:
            return {"status": "error", "error": "txn missing"}
        staged = self.txns.pop(txn)

        # Bug: no idempotent short-circuit on known op_id; retries re-apply.

        # Bug: ignore expected versions (last writer wins).
        writes: dict[str, Any] = {}
        for key, change in staged.items():
            if change["delete"]:
                self.cache.pop(key, None)
                writes[key] = {"value": None, "version": 0}
            else:
                prev = self.cache.get(key)
                ver = 1 if prev is None else int(prev["version"])  # already bumped on put
                self.cache[key] = {"value": change["value"], "version": ver}
                writes[key] = {"value": change["value"], "version": ver}

        self.ops[op_id] = {"status": "committed", "writes": writes}
        intent = {"type": "intent", "op_id": op_id, "txn": txn, "writes": staged}
        with self.wal_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(intent) + "\n")

        if fault == "after_wal":
            return {"status": "crashed", "fault": fault}

        payload = {
            "format": 1,
            "entries": dict(self.cache),
            "ops": dict(self.ops),
        }
        text = json.dumps(payload, indent=2) + "\n"
        # Bug: non-atomic truncate-write of committed.json
        if fault == "after_tmp_partial":
            self.tmp_path.write_bytes(text.encode()[: max(1, len(text) // 3)])
            return {"status": "crashed", "fault": fault}
        if fault == "after_tmp":
            self.tmp_path.write_text(text, encoding="utf-8")
            return {"status": "crashed", "fault": fault}

        self.committed_path.write_text(text, encoding="utf-8")
        if fault == "before_cache":
            # Cache already updated earlier — still "crash".
            return {"status": "crashed", "fault": fault}

        with self.wal_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"type": "commit", "op_id": op_id}) + "\n")
        return {
            "status": "committed",
            "op_id": op_id,
            "idempotent": False,
            "writes": writes,
        }


def run_scenario(store_dir: Path, scenario: dict[str, Any]) -> dict[str, Any]:
    steps = scenario.get("steps")
    if not isinstance(steps, list):
        refuse()
    store = CacheStore(store_dir)
    results: list[Any] = []
    for step in steps:
        if not isinstance(step, dict) or "op" not in step:
            refuse()
        op = step["op"]
        if op == "open":
            store = CacheStore(store_dir)
            results.append(store._last_recover)
        elif op == "begin":
            results.append(store.begin(step.get("txn")))
        elif op == "put":
            results.append(
                store.put(
                    step.get("txn"),
                    step.get("key"),
                    step.get("value"),
                    step.get("expected"),
                )
            )
        elif op == "delete":
            results.append(
                store.delete(step.get("txn"), step.get("key"), step.get("expected"))
            )
        elif op == "abort":
            results.append(store.abort(step.get("txn")))
        elif op == "commit":
            results.append(
                store.commit(step.get("txn"), step.get("op_id"), step.get("fault"))
            )
        elif op == "get":
            results.append(store.get(step.get("key")))
        elif op == "recover":
            results.append(store.recover())
        elif op == "snapshot":
            snap = store.snapshot()
            results.append({"status": "ok", "entries": snap["entries"], "ops": snap["ops"]})
        else:
            refuse()
    return {"ok": True, "results": results, "final": store.snapshot()}


def main(argv: list[str] | None = None) -> None:
    store_s, scenario_s, report_s = parse_args(
        list(argv if argv is not None else sys.argv[1:])
    )
    store_dir = check_store(store_s)
    scenario_path = check_scenario(scenario_s)
    report_path = Path(report_s)
    try:
        scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        refuse()
    if not isinstance(scenario, dict):
        refuse()
    report = run_scenario(store_dir, scenario)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
