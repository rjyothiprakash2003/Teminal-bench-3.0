#!/usr/bin/env python3
"""Tolerated alternative: buffer watermark-accepted events, then assign windows."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

KEY_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")


def refuse(msg: str) -> None:
    sys.stderr.write(msg + "\n")
    raise SystemExit(2)


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


def load_config(path: Path) -> tuple[int, int, int]:
    try:
        text = path.read_bytes().decode("utf-8")
        obj = _loads(text)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        refuse("refuse: bad config")
    if not isinstance(obj, dict) or set(obj.keys()) != {"size", "slide", "lateness"}:
        refuse("refuse: bad config")
    size, slide, lateness = obj["size"], obj["slide"], obj["lateness"]
    if type(size) is not int or isinstance(size, bool) or not (1 <= size < 2**53):
        refuse("refuse: bad config")
    if type(slide) is not int or isinstance(slide, bool) or not (1 <= slide < 2**53):
        refuse("refuse: bad config")
    if type(lateness) is not int or isinstance(lateness, bool) or not (0 <= lateness < 2**53):
        refuse("refuse: bad config")
    return size, slide, lateness


def read_event_lines(path: Path) -> list[str]:
    raw = path.read_bytes()
    if not raw:
        return []
    if not raw.endswith(b"\n"):
        refuse("refuse: truncated events")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        refuse("refuse: truncated events")
    parts = text.split("\n")
    if parts and parts[-1] == "":
        parts = parts[:-1]
    return parts


def parse_event(obj: object) -> tuple[str, int, int]:
    if not isinstance(obj, dict) or set(obj.keys()) != {"key", "ts", "val"}:
        refuse("refuse: bad event")
    key, ts, val = obj["key"], obj["ts"], obj["val"]
    if not isinstance(key, str) or KEY_RE.fullmatch(key) is None:
        refuse("refuse: bad event")
    if type(ts) is not int or isinstance(ts, bool) or not (0 <= ts < 2**53):
        refuse("refuse: bad event")
    if type(val) is not int or isinstance(val, bool) or not (-(2**53) < val < 2**53):
        refuse("refuse: bad event")
    return key, ts, val


def window_starts(ts: int, size: int, slide: int) -> list[int]:
    k_min = (ts - size) // slide + 1
    k_max = ts // slide
    return [k * slide for k in range(max(0, k_min), k_max + 1)]


def load_stream(stream: Path) -> tuple[int, int, int, list[str]]:
    if not stream.is_dir():
        refuse("refuse: bad stream")
    members = sorted(p.name for p in stream.iterdir())
    if members != ["config.json", "events.jsonl"]:
        refuse("refuse: bad stream")
    cfg = stream / "config.json"
    ev = stream / "events.jsonl"
    if not cfg.is_file() or not ev.is_file() or cfg.is_symlink() or ev.is_symlink():
        refuse("refuse: bad stream")
    size, slide, lateness = load_config(cfg)
    return size, slide, lateness, read_event_lines(ev)


def accepted_events(stream: Path) -> tuple[int, int, list[tuple[str, int, int]]]:
    size, slide, lateness, lines = load_stream(stream)
    accepted: list[tuple[str, int, int]] = []
    max_seen: int | None = None
    watermark = 0
    for line in lines:
        if not line.strip():
            continue
        try:
            obj = json.loads(line, parse_constant=_reject_constant)
        except (json.JSONDecodeError, RecursionError):
            refuse("refuse: truncated events")
        except ValueError:
            refuse("refuse: bad event")
        if not isinstance(obj, dict):
            refuse("refuse: truncated events")
        try:
            obj = _loads(line)
        except (json.JSONDecodeError, RecursionError):
            refuse("refuse: truncated events")
        except ValueError:
            refuse("refuse: bad event")
        key, ts, val = parse_event(obj)
        late = max_seen is not None and ts < watermark
        if not late:
            accepted.append((key, ts, val))
        max_seen = ts if max_seen is None else max(max_seen, ts)
        watermark = max_seen - lateness
    return size, slide, accepted


def aggregate(stream: Path) -> dict:
    size, slide, accepted = accepted_events(stream)
    buckets: dict[int, dict[str, int]] = {}
    for key, ts, val in accepted:
        for w in window_starts(ts, size, slide):
            bucket = buckets.setdefault(w, {})
            bucket[key] = bucket.get(key, 0) + val
    windows = []
    for start in sorted(buckets):
        totals = {k: buckets[start][k] for k in sorted(buckets[start])}
        if totals:
            windows.append({"start": start, "end": start + size, "totals": totals})
    return {"windows": windows}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stream", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    result = aggregate(Path(args.stream))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
