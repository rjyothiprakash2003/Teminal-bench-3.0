#!/usr/bin/env python3
"""Unfinished SMF-2026 scorecard folder.

The desk left CLI wiring only. Implement folding, dispositions, policy recovery,
live/frozen effective members, and pack pools per /app/scorecard/SMF_SPEC.md.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="SMF-2026 scorecard folder")
    parser.add_argument("--cards", type=Path, required=True)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(
        "fold.py is unfinished: implement SMF-2026 folding for "
        f"{args.cards} using {args.spec} into {args.out}"
    )


if __name__ == "__main__":
    main()
