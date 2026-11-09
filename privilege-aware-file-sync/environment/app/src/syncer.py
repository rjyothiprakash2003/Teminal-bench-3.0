#!/usr/bin/env python3
"""Shipped syncer — follows links and applies metadata to targets (incorrect)."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import sys
from pathlib import Path


def refuse() -> None:
    sys.stderr.write("refuse: bad args\n")
    raise SystemExit(2)


def is_dir_not_link(path: Path) -> bool:
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISDIR(st.st_mode) and not stat.S_ISLNK(st.st_mode)


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--src")
    parser.add_argument("--dst")
    parser.add_argument("--report")
    args, unknown = parser.parse_known_args()
    if unknown or not args.src or not args.dst or not args.report:
        refuse()
    src = Path(args.src)
    dst = Path(args.dst)
    report = Path(args.report)
    if not is_dir_not_link(src) or not is_dir_not_link(dst):
        refuse()
    try:
        if src.resolve() == dst.resolve():
            refuse()
    except OSError:
        refuse()

    copied: list[str] = []
    skipped: list[dict[str, str]] = []

    # Naive walk: follow directory symlinks and copy through file symlinks.
    for root, dirnames, filenames in os.walk(src, followlinks=True):
        root_path = Path(root)
        rel_root = root_path.relative_to(src)
        rel_prefix = "" if str(rel_root) == "." else str(rel_root).replace(os.sep, "/")
        dirnames.sort()
        filenames.sort()
        for name in sorted(dirnames):
            child = root_path / name
            rel = name if not rel_prefix else f"{rel_prefix}/{name}"
            dest = dst / rel
            try:
                st = child.stat()  # follows
            except OSError:
                skipped.append({"path": rel, "reason": "unsupported"})
                continue
            dest.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(dest, st.st_mode & 0o7777)
            except OSError:
                pass
            copied.append(rel)
        for name in filenames:
            child = root_path / name
            rel = name if not rel_prefix else f"{rel_prefix}/{name}"
            dest = dst / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                # Follow symlinks: materialize target bytes and apply chmod through the link.
                if child.is_symlink():
                    try:
                        st = child.stat()
                        os.chmod(child, st.st_mode | 0o222)  # follows — corrupts target
                    except OSError:
                        pass
                shutil.copy2(child, dest, follow_symlinks=True)
            except OSError:
                skipped.append({"path": rel, "reason": "unsupported"})
                continue
            copied.append(rel)

    copied.sort()
    skipped.sort(key=lambda row: row["path"])
    obj = {"copied": copied, "skipped": skipped}
    text = json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(text, encoding="utf-8")
    raise SystemExit(0)


if __name__ == "__main__":
    main()
