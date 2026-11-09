#!/usr/bin/env python3
"""Mint sealed streams and expects where the independent model and reference agree."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from model import result_bytes, run_stream  # noqa: E402

REF = ROOT / "solution" / "src" / "aggregator.py"
SEALED = ROOT / "tests" / "sealed"


def write_stream(dest: Path, config: dict, events: str | bytes) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    (dest / "config.json").write_text(json.dumps(config, separators=(",", ":")) + "\n")
    if isinstance(events, bytes):
        (dest / "events.jsonl").write_bytes(events)
    else:
        (dest / "events.jsonl").write_text(events)


CASES: dict[str, tuple[dict, str]] = {
    "empty": ({"size": 10, "slide": 5, "lateness": 0}, ""),
    # Distinct from /app/examples/regression (not a +1 timestamp shift of it).
    "arrival_late_drop": (
        {"size": 7, "slide": 3, "lateness": 3},
        '{"key":"p","ts":30,"val":2}\n'
        '{"key":"q","ts":22,"val":8}\n'
        '{"key":"p","ts":25,"val":1}\n'
        '{"key":"p","ts":36,"val":5}\n',
    ),
    "arrival_late_drop_b": (
        {"size": 8, "slide": 4, "lateness": 1},
        '{"key":"k","ts":20,"val":2}\n'
        '{"key":"k","ts":14,"val":9}\n'
        '{"key":"k","ts":18,"val":1}\n'
        '{"key":"k","ts":24,"val":3}\n',
    ),
    "overlap_assign": (
        {"size": 10, "slide": 5, "lateness": 100},
        '{"key":"x","ts":7,"val":2}\n',
    ),
    "overlap_assign_b": (
        {"size": 12, "slide": 3, "lateness": 100},
        '{"key":"y","ts":9,"val":5}\n',
    ),
    "half_open_boundary": (
        {"size": 10, "slide": 5, "lateness": 100},
        '{"key":"x","ts":10,"val":1}\n',
    ),
    "half_open_boundary_b": (
        {"size": 6, "slide": 3, "lateness": 100},
        '{"key":"z","ts":6,"val":4}\n',
    ),
    "multi_key_totals": (
        {"size": 10, "slide": 10, "lateness": 0},
        '{"key":"a","ts":1,"val":1}\n'
        '{"key":"B","ts":2,"val":2}\n',
    ),
    "multi_key_totals_b": (
        {"size": 5, "slide": 5, "lateness": 0},
        '{"key":"m","ts":0,"val":1}\n'
        '{"key":"k","ts":1,"val":2}\n'
        '{"key":"m","ts":2,"val":3}\n',
    ),
    "whitespace_lines": (
        {"size": 10, "slide": 10, "lateness": 0},
        "\n  \n\u2003\n" '{"key":"w","ts":1,"val":1}\n\n',
    ),
    "many_windows": (
        {"size": 1, "slide": 1, "lateness": 0},
        "".join(f'{{"key":"a","ts":{i},"val":1}}\n' for i in range(1025)),
    ),
    "negative_val": (
        {"size": 10, "slide": 10, "lateness": 0},
        '{"key":"n","ts":1,"val":5}\n'
        '{"key":"n","ts":2,"val":-2}\n',
    ),
    "zero_val": (
        {"size": 10, "slide": 10, "lateness": 0},
        '{"key":"z","ts":1,"val":0}\n',
    ),
    "lateness_subtract": (
        {"size": 10, "slide": 10, "lateness": 2},
        '{"key":"a","ts":10,"val":1}\n'
        '{"key":"a","ts":9,"val":1}\n',
    ),
    "watermark_exact": (
        {"size": 10, "slide": 10, "lateness": 2},
        '{"key":"a","ts":10,"val":1}\n'
        '{"key":"a","ts":8,"val":1}\n',
    ),
    "max_seen_preserve": (
        {"size": 10, "slide": 10, "lateness": 2},
        '{"key":"a","ts":10,"val":1}\n'
        '{"key":"a","ts":9,"val":1}\n'
        '{"key":"a","ts":7,"val":1}\n',
    ),
    # Late event must still advance max_seen so a later lower ts becomes late.
    "discard_advances_watermark": (
        {"size": 10, "slide": 10, "lateness": 2},
        '{"key":"a","ts":10,"val":1}\n'
        '{"key":"a","ts":7,"val":9}\n'
        '{"key":"a","ts":8,"val":3}\n'
        '{"key":"a","ts":12,"val":1}\n',
    ),
    "discard_advances_watermark_b": (
        {"size": 6, "slide": 3, "lateness": 1},
        '{"key":"b","ts":20,"val":1}\n'
        '{"key":"b","ts":15,"val":7}\n'
        '{"key":"b","ts":18,"val":2}\n'
        '{"key":"b","ts":22,"val":4}\n',
    ),
    # Compound: overlapping assignment after a late drop in the same stream.
    "overlap_after_late": (
        {"size": 10, "slide": 5, "lateness": 2},
        '{"key":"x","ts":20,"val":1}\n'
        '{"key":"x","ts":15,"val":8}\n'
        '{"key":"x","ts":17,"val":2}\n',
    ),
    "overlap_after_late_b": (
        {"size": 8, "slide": 4, "lateness": 3},
        '{"key":"y","ts":30,"val":1}\n'
        '{"key":"y","ts":24,"val":5}\n'
        '{"key":"y","ts":28,"val":2}\n',
    ),
    "slide_gt_size": (
        {"size": 2, "slide": 5, "lateness": 0},
        '{"key":"a","ts":3,"val":1}\n',
    ),
    "neg_start_filter": (
        {"size": 10, "slide": 5, "lateness": 0},
        '{"key":"a","ts":0,"val":1}\n',
    ),
    "window_sort_ooo": (
        {"size": 2, "slide": 2, "lateness": 10},
        '{"key":"a","ts":4,"val":1}\n'
        '{"key":"a","ts":0,"val":1}\n',
    ),
    "empty_gap_windows": (
        {"size": 2, "slide": 2, "lateness": 0},
        '{"key":"a","ts":0,"val":1}\n'
        '{"key":"a","ts":4,"val":1}\n',
    ),
    "large_ts": (
        {"size": 2, "slide": 2, "lateness": 0},
        '{"key":"a","ts":9007199254740991,"val":1}\n',
    ),
    "large_sum": (
        {"size": 10, "slide": 10, "lateness": 0},
        '{"key":"a","ts":1,"val":9007199254740991}\n'
        '{"key":"a","ts":1,"val":9007199254740991}\n',
    ),
}


def mint() -> None:
    SEALED.mkdir(parents=True, exist_ok=True)
    digests = []
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        all_cases = dict(CASES)
        # 10_000 short lines — in-bounds scale; O(n) finishes, O(n^2) misses 20s.
        scale_events = "".join('{"key":"a","ts":1,"val":1}\n' for _ in range(10_000))
        all_cases["scale_10k"] = (
            {"size": 10_000, "slide": 10_000, "lateness": 0},
            scale_events,
        )
        # 1001 lines — distinguishes a 1000-line quiet cap.
        all_cases["scale_1001"] = (
            {"size": 10_000, "slide": 10_000, "lateness": 0},
            "".join('{"key":"a","ts":1,"val":1}\n' for _ in range(1001)),
        )
        # 10_000 tumbling windows — requires full stated window capacity.
        all_cases["cap_windows"] = (
            {"size": 1, "slide": 1, "lateness": 0},
            "".join(f'{{"key":"a","ts":{i},"val":1}}\n' for i in range(10_000)),
        )
        # ~524 KiB single line — distinguishes a 512 KiB whole-file cap.
        prefix = b'{"key":"a","ts":1,"val":1'
        suffix = b"}\n"
        pad = 524_289 - len(prefix) - len(suffix)
        all_cases["wide_file"] = (
            {"size": 10, "slide": 10, "lateness": 0},
            prefix + (b" " * pad) + suffix,
        )

        for name, (config, events) in all_cases.items():
            sdir = tdp / name
            write_stream(sdir, config, events)
            model = run_stream(sdir)
            assert model.ok, (name, model.stderr)
            out = tdp / f"{name}.out"
            proc = subprocess.run(
                ["python3", str(REF), "--stream", str(sdir), "--out", str(out)],
                capture_output=True,
                text=True,
                timeout=20,
            )
            assert proc.returncode == 0, (name, proc.stderr)
            ref_bytes = out.read_bytes()
            model_b = result_bytes(model.result)
            assert ref_bytes == model_b, name
            dest = SEALED / name
            write_stream(dest, config, events)
            (dest / "expect.json").write_bytes(model_b)
            digests.append(f"{name} {hashlib.sha256(model_b).hexdigest()}")
    # Provenance digests stay under design/, never in the shipped sealed tree.
    (ROOT / "design" / "sealed_digests.txt").write_text("\n".join(digests) + "\n")
    print(f"minted {len(all_cases)} sealed cases")


if __name__ == "__main__":
    mint()
