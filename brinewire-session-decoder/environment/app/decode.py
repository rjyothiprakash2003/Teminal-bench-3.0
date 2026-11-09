#!/usr/bin/env python3
"""Unfinished BW-2026 stub. Replace with a faithful Brinewire session decoder."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--captures", required=True, type=Path)
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    manifest = args.captures / "manifest.jsonl"
    spec_md = args.spec / "BW_SPEC.md"
    examples = args.spec / "worked_examples"
    if not manifest.is_file() or not spec_md.is_file() or not examples.is_dir():
        print("missing BW pack or spec surfaces", file=sys.stderr)
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "sessions.jsonl").write_text("")
    (args.out / "summary.json").write_text(
        '{"session_count":0,"by_disposition":{"ok":0,"reject_syntax":0,'
        '"reject_flag":0,"reject_nested_encap":0},"ok_count":0}\n'
    )
    print("decode stub: not yet implemented", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
