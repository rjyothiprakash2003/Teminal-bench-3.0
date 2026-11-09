#!/usr/bin/env python3
"""Transactional cache with durable commit, idempotent ops, and crash recovery."""

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


def json_safe(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError):
        raise ValueError("bad value") from None


def sorted_entries(entries: dict[str, Any]) -> dict[str, Any]:
    return {k: entries[k] for k in sorted(entries)}


def sorted_ops(ops: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in sorted(ops):
        rec = ops[k]
        writes = rec.get("writes", {})
        out[k] = {
            "status": rec["status"],
            "writes": {wk: writes[wk] for wk in sorted(writes)},
        }
    return out


def empty_state() -> dict[str, Any]:
    return {"format": 1, "entries": {}, "ops": {}}


class CacheStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.committed_path = root / COMMITTED
        self.tmp_path = root / TMP
        self.wal_path = root / WAL
        self.cache: dict[str, dict[str, Any]] = {}
        self.ops: dict[str, dict[str, Any]] = {}
        self.txns: dict[str, dict[str, dict[str, Any]]] = {}
        self._last_recover: dict[str, Any] = self.recover()

    def _load_committed_bytes(self) -> dict[str, Any]:
        if not self.committed_path.exists():
            return empty_state()
        data = json.loads(self.committed_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("format") != 1:
            return empty_state()
        entries = data.get("entries") or {}
        ops = data.get("ops") or {}
        if not isinstance(entries, dict) or not isinstance(ops, dict):
            return empty_state()
        return {
            "format": 1,
            "entries": {
                k: {"value": v["value"], "version": int(v["version"])}
                for k, v in entries.items()
            },
            "ops": {
                k: {
                    "status": v["status"],
                    "writes": {
                        wk: {
                            "value": wv["value"],
                            "version": int(wv["version"]),
                        }
                        for wk, wv in (v.get("writes") or {}).items()
                    },
                }
                for k, v in ops.items()
            },
        }

    def _count_pending_intents(self, ops: dict[str, Any]) -> int:
        if not self.wal_path.exists():
            return 0
        pending = 0
        try:
            text = self.wal_path.read_text(encoding="utf-8")
        except OSError:
            return 0
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("type") == "intent":
                op_id = rec.get("op_id")
                if isinstance(op_id, str) and op_id not in ops:
                    pending += 1
        return pending

    def recover(self) -> dict[str, Any]:
        discarded_tmp = False
        if self.tmp_path.exists() or self.tmp_path.is_symlink():
            try:
                self.tmp_path.unlink()
                discarded_tmp = True
            except OSError:
                discarded_tmp = self.tmp_path.exists()

        state = self._load_committed_bytes()
        self.cache = {
            k: {"value": v["value"], "version": v["version"]}
            for k, v in state["entries"].items()
        }
        self.ops = {
            k: {"status": v["status"], "writes": dict(v["writes"])}
            for k, v in state["ops"].items()
        }
        pending = self._count_pending_intents(self.ops)
        self.txns.clear()
        result = {
            "status": "ok",
            "discarded_tmp": discarded_tmp,
            "pending_intents": pending,
        }
        self._last_recover = result
        return result

    def snapshot(self) -> dict[str, Any]:
        return {
            "entries": sorted_entries(
                {
                    k: {"value": v["value"], "version": v["version"]}
                    for k, v in self.cache.items()
                }
            ),
            "ops": sorted_ops(self.ops),
        }

    def begin(self, txn: str) -> dict[str, Any]:
        if not valid_ident(txn):
            return {"status": "error", "error": "bad txn"}
        if txn in self.txns:
            return {"status": "error", "error": "txn open"}
        self.txns[txn] = {}
        return {"status": "ok"}

    def _stage(
        self, txn: str, key: str, value: Any | None, expected: int, deleting: bool
    ) -> dict[str, Any]:
        if not valid_ident(txn):
            return {"status": "error", "error": "bad txn"}
        if txn not in self.txns:
            return {"status": "error", "error": "txn missing"}
        if not valid_ident(key):
            return {"status": "error", "error": "bad key"}
        if not isinstance(expected, int) or isinstance(expected, bool) or expected < 0:
            return {"status": "error", "error": "bad key"}
        if deleting:
            staged_value = None
        else:
            try:
                staged_value = json_safe(value)
            except ValueError:
                return {"status": "error", "error": "bad key"}
        self.txns[txn][key] = {
            "value": staged_value,
            "expected": expected,
            "delete": deleting,
        }
        return {"status": "ok"}

    def put(self, txn: str, key: str, value: Any, expected: int) -> dict[str, Any]:
        return self._stage(txn, key, value, expected, False)

    def delete(self, txn: str, key: str, expected: int) -> dict[str, Any]:
        return self._stage(txn, key, None, expected, True)

    def abort(self, txn: str) -> dict[str, Any]:
        if not valid_ident(txn):
            return {"status": "error", "error": "bad txn"}
        if txn not in self.txns:
            return {"status": "error", "error": "txn missing"}
        del self.txns[txn]
        return {"status": "ok"}

    def get(self, key: str) -> dict[str, Any]:
        if not valid_ident(key):
            return {"status": "error", "error": "bad key"}
        item = self.cache.get(key)
        if item is None:
            return {"found": False}
        return {"found": True, "value": item["value"], "version": item["version"]}

    def _append_wal(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, separators=(",", ":"), allow_nan=False) + "\n"
        with self.wal_path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())

    def commit(
        self, txn: str, op_id: str, fault: str | None = None
    ) -> dict[str, Any]:
        if not valid_ident(txn):
            return {"status": "error", "error": "bad txn"}
        if not valid_ident(op_id):
            return {"status": "error", "error": "bad op_id"}
        if txn not in self.txns:
            return {"status": "error", "error": "txn missing"}

        if op_id in self.ops:
            prior = self.ops[op_id]
            del self.txns[txn]
            return {
                "status": prior["status"],
                "op_id": op_id,
                "idempotent": True,
                "writes": {k: prior["writes"][k] for k in sorted(prior["writes"])},
            }

        staged = self.txns[txn]
        for key, change in staged.items():
            current = self.cache.get(key)
            cur_ver = 0 if current is None else int(current["version"])
            if change["expected"] != cur_ver:
                new_entries = {
                    k: {"value": v["value"], "version": v["version"]}
                    for k, v in self.cache.items()
                }
                new_ops = {
                    k: {"status": v["status"], "writes": dict(v["writes"])}
                    for k, v in self.ops.items()
                }
                new_ops[op_id] = {"status": "conflict", "writes": {}}
                self._persist(new_entries, new_ops)
                self.ops = new_ops
                del self.txns[txn]
                return {
                    "status": "conflict",
                    "op_id": op_id,
                    "idempotent": False,
                    "writes": {},
                }

        new_entries = {
            k: {"value": v["value"], "version": v["version"]}
            for k, v in self.cache.items()
        }
        writes: dict[str, dict[str, Any]] = {}
        for key in sorted(staged):
            change = staged[key]
            if change["delete"]:
                new_entries.pop(key, None)
                writes[key] = {"value": None, "version": 0}
            else:
                prev = new_entries.get(key)
                ver = 1 if prev is None else int(prev["version"]) + 1
                new_entries[key] = {"value": change["value"], "version": ver}
                writes[key] = {"value": change["value"], "version": ver}

        new_ops = {
            k: {"status": v["status"], "writes": dict(v["writes"])}
            for k, v in self.ops.items()
        }
        new_ops[op_id] = {"status": "committed", "writes": writes}

        intent = {
            "type": "intent",
            "op_id": op_id,
            "txn": txn,
            "writes": {
                k: {
                    "value": staged[k]["value"],
                    "expected": staged[k]["expected"],
                    "delete": staged[k]["delete"],
                }
                for k in sorted(staged)
            },
        }
        self._append_wal(intent)
        if fault == "after_wal":
            del self.txns[txn]
            return {"status": "crashed", "fault": fault}

        payload = {
            "format": 1,
            "entries": sorted_entries(new_entries),
            "ops": sorted_ops(new_ops),
        }
        text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        data = text.encode("utf-8")

        if fault == "after_tmp_partial":
            with self.tmp_path.open("wb") as handle:
                handle.write(data[: max(1, len(data) // 3)])
                handle.flush()
                os.fsync(handle.fileno())
            del self.txns[txn]
            return {"status": "crashed", "fault": fault}

        with self.tmp_path.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if fault == "after_tmp":
            del self.txns[txn]
            return {"status": "crashed", "fault": fault}

        os.replace(self.tmp_path, self.committed_path)
        if fault == "before_cache":
            del self.txns[txn]
            return {"status": "crashed", "fault": fault}

        self.cache = {
            k: {"value": v["value"], "version": v["version"]}
            for k, v in new_entries.items()
        }
        self.ops = new_ops
        self._append_wal({"type": "commit", "op_id": op_id})
        del self.txns[txn]
        return {
            "status": "committed",
            "op_id": op_id,
            "idempotent": False,
            "writes": {k: writes[k] for k in sorted(writes)},
        }

    def _persist(self, entries: dict[str, Any], ops: dict[str, Any]) -> None:
        payload = {
            "format": 1,
            "entries": sorted_entries(entries),
            "ops": sorted_ops(ops),
        }
        text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        with self.tmp_path.open("w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(self.tmp_path, self.committed_path)


def run_scenario(store_dir: Path, scenario: dict[str, Any]) -> dict[str, Any]:
    steps = scenario.get("steps")
    if not isinstance(steps, list):
        refuse()
    store = CacheStore(store_dir)
    results: list[dict[str, Any]] = []
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


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


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
    write_report(report_path, run_scenario(store_dir, scenario))


if __name__ == "__main__":
    main()
