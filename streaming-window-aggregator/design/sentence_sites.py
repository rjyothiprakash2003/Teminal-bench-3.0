#!/usr/bin/env python3
"""Sentence × site gate."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "environment" / "app" / "docs" / "window-contract.md"

SENTENCES = [
    ("S1", "exactly two members named"),
    ("S2", "print\nexactly `refuse: truncated events`"),
    ("S3", "Do not reorder by\ntimestamp before applying the watermark rule"),
    ("S4", "the event is late: discard it"),
    ("S5", "ts - size < w <= ts"),
    ("S6", "print exactly `refuse: bad config`"),
    ("S7", "print exactly `refuse: bad event`"),
    ("S8", "print exactly `refuse: bad stream`"),
    ("S9", "Order the `windows` array by ascending `start`"),
    ("S10", "exactly one key, `windows`"),
]

SITES = {
    "S1": ["load_stream:exact_members"],
    "S2": ["read_event_lines:endswith_lf", "aggregate:json_decode"],
    "S3": ["aggregate:arrival_order_loop"],
    "S4": ["aggregate:late_check"],
    "S5": ["window_starts:half_open_range", "aggregate:multi_window_add"],
    "S6": ["load_config:refuse"],
    "S7": ["parse_event:refuse"],
    "S8": ["load_stream:refuse"],
    "S9": ["aggregate:sorted_starts"],
    "S10": ["main:windows_only_object"],
}

RIVAL_FOR = {
    "S1": "fixture:bad_stream",
    "S2": "ignore_truncation",
    "S3": "sort_by_ts_first",
    "S4": "ignore_lateness+late_le_watermark",
    "S5": "tumbling_only+inclusive_end",
    "S6": "fixture:bad_config",
    "S7": "fixture:bad_event",
    "S8": "fixture:bad_stream",
    "S9": "fixture:window_order_in_expects",
    "S10": "fixture:key_shape_in_expects",
}


def main() -> None:
    text = PAGE.read_text()
    gaps = []
    for sid, anchor in SENTENCES:
        n = text.count(anchor)
        if n != 1:
            gaps.append(f"{sid} anchor count {n} for {anchor!r}")
        if sid not in SITES or not SITES[sid]:
            gaps.append(f"{sid} has no sites")
        if sid not in RIVAL_FOR:
            gaps.append(f"{sid} missing rival/fixture mapping")
    if gaps:
        print("GAPS:")
        for g in gaps:
            print(g)
        raise SystemExit(1)
    pairs = sum(len(SITES[s]) for s, _ in SENTENCES)
    print(f"sentences {len(SENTENCES)}, sentence×site pairs {pairs}, gaps 0")
    for sid, _anchor in SENTENCES:
        print(f"  {sid} sites={SITES[sid]} via {RIVAL_FOR[sid]}")


if __name__ == "__main__":
    main()
