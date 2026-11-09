#!/usr/bin/env python3
"""Independent contract model — written from window-contract.md, not from the reference."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

KEY_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")


@dataclass
class Outcome:
    ok: bool
    result: dict[str, Any] | None = None
    stderr: str = ""
    exit_code: int = 0


def _refuse(msg: str) -> Outcome:
    return Outcome(ok=False, stderr=msg + "\n", exit_code=2)


def _reject_constant(c: str):
    raise json.JSONDecodeError(f"nonfinite {c}", c, 0)


def _no_duplicate_keys(pairs: list) -> dict:
    out: dict = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate json member name")
        out[key] = value
    return out


def _loads(text: str):
    return json.loads(
        text,
        parse_constant=_reject_constant,
        object_pairs_hook=_no_duplicate_keys,
    )


def window_starts(ts: int, size: int, slide: int) -> list[int]:
    k_min = (ts - size) // slide + 1
    k_max = ts // slide
    return [k * slide for k in range(max(0, k_min), k_max + 1)]


def parse_config_bytes(raw: bytes) -> tuple[int, int, int] | Outcome:
    try:
        text = raw.decode("utf-8")
        obj = _loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        return _refuse("refuse: bad config")
    if not isinstance(obj, dict) or set(obj.keys()) != {"size", "slide", "lateness"}:
        return _refuse("refuse: bad config")
    size, slide, lateness = obj["size"], obj["slide"], obj["lateness"]
    if type(size) is not int or isinstance(size, bool) or not (1 <= size < 2**53):
        return _refuse("refuse: bad config")
    if type(slide) is not int or isinstance(slide, bool) or not (1 <= slide < 2**53):
        return _refuse("refuse: bad config")
    if type(lateness) is not int or isinstance(lateness, bool) or not (0 <= lateness < 2**53):
        return _refuse("refuse: bad config")
    return size, slide, lateness


def parse_event(obj: Any) -> tuple[str, int, int] | Outcome:
    if not isinstance(obj, dict) or set(obj.keys()) != {"key", "ts", "val"}:
        return _refuse("refuse: bad event")
    key, ts, val = obj["key"], obj["ts"], obj["val"]
    if not isinstance(key, str) or KEY_RE.fullmatch(key) is None:
        return _refuse("refuse: bad event")
    if type(ts) is not int or isinstance(ts, bool) or not (0 <= ts < 2**53):
        return _refuse("refuse: bad event")
    if type(val) is not int or isinstance(val, bool) or not (-(2**53) < val < 2**53):
        return _refuse("refuse: bad event")
    return key, ts, val


def read_event_lines(path: Path) -> tuple[list[str] | None, Outcome | None]:
    raw = path.read_bytes()
    if not raw:
        return [], None
    if not raw.endswith(b"\n"):
        return None, _refuse("refuse: truncated events")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, _refuse("refuse: truncated events")
    parts = text.split("\n")
    if parts and parts[-1] == "":
        parts = parts[:-1]
    return parts, None


@dataclass
class State:
    buckets: dict[int, dict[str, int]] = field(default_factory=dict)
    max_seen: int | None = None
    watermark: int = 0


def run_stream(stream: Path) -> Outcome:
    if not stream.is_dir():
        return _refuse("refuse: bad stream")
    members = sorted(p.name for p in stream.iterdir())
    if members != ["config.json", "events.jsonl"]:
        return _refuse("refuse: bad stream")
    cfg_path = stream / "config.json"
    ev_path = stream / "events.jsonl"
    if (
        not cfg_path.is_file()
        or not ev_path.is_file()
        or cfg_path.is_symlink()
        or ev_path.is_symlink()
    ):
        return _refuse("refuse: bad stream")

    parsed = parse_config_bytes(cfg_path.read_bytes())
    if isinstance(parsed, Outcome):
        return parsed
    size, slide, lateness = parsed

    lines, err = read_event_lines(ev_path)
    if err is not None:
        return err
    assert lines is not None

    state = State()
    contrib: dict[tuple[str, int], int] = {}
    for line in lines:
        if not line.strip():
            continue
        try:
            obj = json.loads(line, parse_constant=_reject_constant)
        except (json.JSONDecodeError, RecursionError):
            return _refuse("refuse: truncated events")
        except ValueError:
            return _refuse("refuse: bad event")
        if not isinstance(obj, dict):
            return _refuse("refuse: truncated events")
        try:
            obj = _loads(line)
        except (json.JSONDecodeError, RecursionError):
            return _refuse("refuse: truncated events")
        except ValueError:
            return _refuse("refuse: bad event")
        ev = parse_event(obj)
        if isinstance(ev, Outcome):
            return ev
        key, ts, val = ev
        late = state.max_seen is not None and ts < state.watermark
        if not late:
            contrib[(key, ts)] = contrib.get((key, ts), 0) + val
        state.max_seen = ts if state.max_seen is None else max(state.max_seen, ts)
        state.watermark = state.max_seen - lateness

    for (key, ts), val in contrib.items():
        for w in window_starts(ts, size, slide):
            bucket = state.buckets.setdefault(w, {})
            bucket[key] = bucket.get(key, 0) + val

    windows = []
    for start in sorted(state.buckets):
        totals = {k: state.buckets[start][k] for k in sorted(state.buckets[start])}
        if totals:
            windows.append({"start": start, "end": start + size, "totals": totals})
    return Outcome(ok=True, result={"windows": windows}, exit_code=0)


def result_bytes(result: dict[str, Any]) -> bytes:
    return (json.dumps(result, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
