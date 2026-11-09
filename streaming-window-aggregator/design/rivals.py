#!/usr/bin/env python3
"""Executable rival sweep against the graded surface (sealed + key visibles)."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REF_PATH = ROOT / "solution" / "src" / "aggregator.py"
REF = REF_PATH.read_text()
SEALED = ROOT / "tests" / "sealed"

RIVALS: list[tuple[str, str, str, str]] = [
    (
        "sort_by_ts_first",
        "S3 arrival order before watermark",
        "    contrib: dict[tuple[str, int], int] = {}\n"
        "    max_seen: int | None = None\n"
        "    watermark = 0\n"
        "\n"
        "    for line in lines:\n",
        "    contrib: dict[tuple[str, int], int] = {}\n"
        "    max_seen: int | None = None\n"
        "    watermark = 0\n"
        "    parsed_events: list[tuple[str, int, int]] = []\n"
        "\n"
        "    for line in lines:\n",
    ),
    (
        "sort_by_ts_first_b",
        "S3 arrival order before watermark",
        "        key, ts, val = parse_event(obj)\n"
        "        late = max_seen is not None and ts < watermark\n"
        "        if not late:\n"
        "            contrib[(key, ts)] = contrib.get((key, ts), 0) + val\n"
        "        max_seen = ts if max_seen is None else max(max_seen, ts)\n"
        "        watermark = max_seen - lateness\n",
        "        parsed_events.append(parse_event(obj))\n"
        "    parsed_events.sort(key=lambda e: e[1])\n"
        "    for key, ts, val in parsed_events:\n"
        "        late = max_seen is not None and ts < watermark\n"
        "        if not late:\n"
        "            contrib[(key, ts)] = contrib.get((key, ts), 0) + val\n"
        "        max_seen = ts if max_seen is None else max(max_seen, ts)\n"
        "        watermark = max_seen - lateness\n",
    ),
    (
        "ignore_lateness",
        "S4 late discard when ts < watermark",
        "        late = max_seen is not None and ts < watermark\n",
        "        late = False\n",
    ),
    (
        "tumbling_only",
        "S5 every overlapping window start",
        "        for w in window_starts(ts, size, slide):\n"
        "            bucket = buckets.setdefault(w, {})\n"
        "            bucket[key] = bucket.get(key, 0) + val\n",
        "        w = (ts // size) * size\n"
        "        if w >= 0:\n"
        "            bucket = buckets.setdefault(w, {})\n"
        "            bucket[key] = bucket.get(key, 0) + val\n",
    ),
    (
        "inclusive_end",
        "S5 half-open upper bound",
        "    k_min = (ts - size) // slide + 1\n"
        "    k_max = ts // slide\n",
        "    k_min = (ts - size) // slide\n"
        "    k_max = ts // slide\n",
    ),
    (
        "ignore_truncation",
        "S2 truncated events refusal",
        "    if not raw.endswith(b\"\\n\"):\n"
        "        refuse(\"refuse: truncated events\")\n",
        "    if not raw.endswith(b\"\\n\"):\n"
        "        raw = raw + b\"\\n\"\n",
    ),
    (
        "late_le_watermark",
        "S4 exact watermark accepted (strict <)",
        "        late = max_seen is not None and ts < watermark\n",
        "        late = max_seen is not None and ts <= watermark\n",
    ),
]


def apply_all(src: str, pairs: list[tuple[str, str, str]]) -> str:
    out = src
    for name, old, new in pairs:
        if old not in out:
            raise SystemExit(f"RIVAL PATCH NO LONGER APPLIES: {name}")
        if out.count(old) != 1:
            raise SystemExit(f"RIVAL PATCH AMBIGUOUS ({out.count(old)}): {name}")
        out = out.replace(old, new, 1)
    return out


def write_stream(dest: Path, config: dict, events: str) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    (dest / "config.json").write_text(json.dumps(config) + "\n")
    (dest / "events.jsonl").write_text(events)


def run_surface(aggregator: Path, work: Path) -> tuple[int, list[str]]:
    fails: list[str] = []
    total = 0

    def run_one(stream: Path, out: Path) -> subprocess.CompletedProcess[str]:
        if out.exists() or out.is_symlink():
            out.unlink()
        out.parent.mkdir(parents=True, exist_ok=True)
        return subprocess.run(
            ["python3", str(aggregator), "--stream", str(stream), "--out", str(out)],
            capture_output=True,
            text=True,
            timeout=20,
        )

    for case in sorted(p for p in SEALED.iterdir() if p.is_dir()):
        expect = case / "expect.json"
        if not expect.is_file():
            continue
        # Skip huge scale cases in rival loop for speed; still covered by sealed pytest.
        if case.name.startswith("scale_") or case.name in {"many_windows", "wide_file", "cap_windows"}:
            continue
        total += 1
        staged = work / f"staged_{case.name}"
        if staged.exists():
            shutil.rmtree(staged)
        staged.mkdir(parents=True)
        shutil.copy2(case / "config.json", staged / "config.json")
        shutil.copy2(case / "events.jsonl", staged / "events.jsonl")
        out = work / f"{case.name}.json"
        proc = run_one(staged, out)
        if proc.returncode != 0 or not out.is_file() or out.read_bytes() != expect.read_bytes():
            fails.append(f"sealed:{case.name}")

    total += 1
    s = work / "trunc"
    write_stream(s, {"size": 10, "slide": 10, "lateness": 0}, "")
    (s / "events.jsonl").write_bytes(b'{"key":"t","ts":1,"val":1}')
    out = work / "trunc.json"
    proc = run_one(s, out)
    if proc.returncode != 2 or proc.stderr != "refuse: truncated events\n" or out.exists():
        fails.append("visible:truncated")

    total += 1
    s = work / "trunc2"
    write_stream(s, {"size": 10, "slide": 10, "lateness": 0}, "")
    (s / "events.jsonl").write_bytes(
        b'{"key":"t","ts":1,"val":1}\n{"key":"u","ts":2,"val":2}'
    )
    out = work / "trunc2.json"
    proc = run_one(s, out)
    if proc.returncode != 2 or proc.stderr != "refuse: truncated events\n" or out.exists():
        fails.append("visible:truncated2")

    total += 1
    s = work / "late"
    write_stream(
        s,
        {"size": 10, "slide": 5, "lateness": 2},
        '{"key":"a","ts":15,"val":1}\n'
        '{"key":"a","ts":8,"val":10}\n'
        '{"key":"a","ts":20,"val":4}\n',
    )
    out = work / "late.json"
    proc = run_one(s, out)
    expect_late = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 10,
              "end": 20,
              "totals": {
                "a": 1
              }
            },
            {
              "start": 15,
              "end": 25,
              "totals": {
                "a": 5
              }
            },
            {
              "start": 20,
              "end": 30,
              "totals": {
                "a": 4
              }
            }
          ]
        }
        """
    )
    if proc.returncode != 0 or not out.is_file() or out.read_text() != expect_late:
        fails.append("visible:arrival_late")

    total += 1
    s = work / "ov"
    write_stream(
        s,
        {"size": 10, "slide": 5, "lateness": 100},
        '{"key":"x","ts":7,"val":2}\n',
    )
    out = work / "ov.json"
    proc = run_one(s, out)
    expect_ov = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 0,
              "end": 10,
              "totals": {
                "x": 2
              }
            },
            {
              "start": 5,
              "end": 15,
              "totals": {
                "x": 2
              }
            }
          ]
        }
        """
    )
    if proc.returncode != 0 or not out.is_file() or out.read_text() != expect_ov:
        fails.append("visible:overlap")

    total += 1
    s = work / "bound"
    write_stream(
        s,
        {"size": 10, "slide": 5, "lateness": 100},
        '{"key":"x","ts":10,"val":1}\n',
    )
    out = work / "bound.json"
    proc = run_one(s, out)
    expect_b = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 5,
              "end": 15,
              "totals": {
                "x": 1
              }
            },
            {
              "start": 10,
              "end": 20,
              "totals": {
                "x": 1
              }
            }
          ]
        }
        """
    )
    if proc.returncode != 0 or not out.is_file() or out.read_text() != expect_b:
        fails.append("visible:boundary")

    return total, fails


LOGICAL = [
    ("sort_by_ts_first", ["sort_by_ts_first", "sort_by_ts_first_b"]),
    ("ignore_lateness", ["ignore_lateness"]),
    ("tumbling_only", ["tumbling_only"]),
    ("inclusive_end", ["inclusive_end"]),
    ("ignore_truncation", ["ignore_truncation"]),
    ("late_le_watermark", ["late_le_watermark"]),
]

PATCH_BY_NAME = {n: (n, s, o, nw) for n, s, o, nw in RIVALS}


def main() -> None:
    survivors = []
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        control = tdp / "control.py"
        control.write_text(REF)
        total, fails = run_surface(control, tdp / "cwork")
        if fails:
            raise SystemExit(f"REFERENCE CONTROL FAILED {len(fails)}/{total}: {fails}")
        print(f"control PASS {total}/{total}")

        for logical_name, patch_names in LOGICAL:
            pairs = []
            sentence = PATCH_BY_NAME[patch_names[0]][1]
            for pn in patch_names:
                _, _, old, new = PATCH_BY_NAME[pn]
                pairs.append((pn, old, new))
            patched = apply_all(REF, pairs)
            path = tdp / f"{logical_name}.py"
            path.write_text(patched)
            total, fails = run_surface(path, tdp / f"w_{logical_name}")
            print(f"{logical_name} fails {len(fails)}/{total} :: {fails}")
            if len(fails) < 2:
                survivors.append((logical_name, sentence, len(fails), fails))

    if survivors:
        print("SURVIVORS:")
        for row in survivors:
            print(row)
        raise SystemExit(1)
    print(f"rivals {len(LOGICAL)}, survivors 0")


if __name__ == "__main__":
    main()
