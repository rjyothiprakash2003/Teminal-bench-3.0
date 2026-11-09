#!/usr/bin/env python3
"""Privilege-aware file syncer — reference implementation for SYNC.md."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path


def refuse() -> None:
    sys.stderr.write("refuse: bad args\n")
    raise SystemExit(2)


def parse_args(argv: list[str]) -> tuple[str, str, str]:
    """Parse --src/--dst/--report without argparse missing-value diagnostics.

    A value may itself begin with ``--`` (a legal pathname). Only a missing
    token or an empty string is treated as a missing flag value.
    """
    src = dst = report = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--src", "--dst", "--report"):
            if i + 1 >= len(argv):
                refuse()
            val = argv[i + 1]
            if not val:
                refuse()
            if a == "--src":
                src = val
            elif a == "--dst":
                dst = val
            else:
                report = val
            i += 2
            continue
        refuse()
    if src is None or dst is None or report is None:
        refuse()
    return src, dst, report


def is_dir_not_link(path: str) -> bool:
    """lstat the caller string literally — do not Path-normalize trailing ``/.``."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISDIR(st.st_mode) and not stat.S_ISLNK(st.st_mode)


def under_root(root: Path, candidate: Path) -> bool:
    try:
        root_r = root.resolve()
        cand_r = Path(os.path.realpath(candidate))
    except OSError:
        return False
    if cand_r == root_r:
        return True
    root_s = str(root_r)
    cand_s = str(cand_r)
    # When the destination real path is the filesystem root, root_s + sep is
    # "//", which never prefixes a normal absolute path. Every absolute path
    # is beneath "/".
    if root_s == os.sep:
        return cand_s.startswith(os.sep)
    return cand_s.startswith(root_s + os.sep)


def parent_safe(dst_root: Path, rel: str) -> bool:
    if not rel or "/" not in rel:
        return True
    cur = dst_root
    for part in rel.split("/")[:-1]:
        cur = cur / part
        try:
            st = os.lstat(cur)
        except FileNotFoundError:
            return True
        except OSError:
            return False
        if stat.S_ISLNK(st.st_mode):
            try:
                resolved = Path(os.path.realpath(cur))
            except OSError:
                return False
            if not under_root(dst_root, resolved):
                return False
        elif not stat.S_ISDIR(st.st_mode):
            return False
    return True


def ensure_parent(dst_root: Path, rel: str) -> bool:
    """Ensure destination parents exist for ``rel``.

    Permitted intermediate symlinks whose realpath lies under ``dst_root`` may
    dangle: create the missing referent directories, then leave the symlink in
    place so later opens traverse it.
    """
    if not rel or "/" not in rel:
        return True
    cur = dst_root
    for part in rel.split("/")[:-1]:
        cur = cur / part
        try:
            st = os.lstat(cur)
        except FileNotFoundError:
            try:
                cur.mkdir(parents=False, exist_ok=True)
            except OSError:
                return False
            continue
        except OSError:
            return False
        if stat.S_ISLNK(st.st_mode):
            try:
                resolved = Path(os.path.realpath(cur))
            except OSError:
                return False
            if not under_root(dst_root, resolved):
                return False
            try:
                resolved.mkdir(parents=True, exist_ok=True)
            except OSError:
                return False
        elif not stat.S_ISDIR(st.st_mode):
            return False
    return True


def open_nofollow(path: Path, flags: int) -> int:
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return os.open(path, flags)


def name_sort_key(name: str) -> bytes:
    return os.fsencode(name)


def sync_tree(src: Path, dst: Path) -> tuple[list[str], list[dict[str, str]]]:
    copied: list[str] = []
    skipped: list[dict[str, str]] = []

    def skip(rel: str, reason: str) -> None:
        skipped.append({"path": rel, "reason": reason})

    def ensure_and_mode_dir(src_dir: Path, dest: Path, rel: str) -> str:
        """Apply the directory rule. Returns 'ok', 'conflict', or 'fail'."""
        if dest.exists() or dest.is_symlink():
            try:
                dst_st = os.lstat(dest)
            except OSError:
                skip(rel, "type conflict")
                return "conflict"
            if stat.S_ISLNK(dst_st.st_mode) or not stat.S_ISDIR(dst_st.st_mode):
                skip(rel, "type conflict")
                return "conflict"
        else:
            if rel and not ensure_parent(dst, rel):
                skip(rel, "unsupported")
                return "fail"
            try:
                dest.mkdir(parents=False, exist_ok=True)
            except OSError:
                skip(rel, "unsupported")
                return "fail"
        try:
            src_st = os.lstat(src_dir)
            os.chmod(dest, src_st.st_mode & 0o7777)
        except OSError:
            skip(rel, "unsupported")
            return "fail"
        copied.append(rel)
        return "ok"

    def walk(src_dir: Path, rel: str) -> None:
        try:
            names = os.listdir(src_dir)
        except PermissionError:
            skip(rel, "inaccessible")
            return
        except OSError:
            skip(rel, "inaccessible")
            return

        for name in sorted(names, key=name_sort_key):
            if name in (".", ".."):
                continue
            child_rel = name if not rel else f"{rel}/{name}"
            child_src = src_dir / name
            try:
                st = os.lstat(child_src)
            except OSError:
                skip(child_rel, "unsupported")
                continue

            # Unsupported source types are classified from lstat alone, before
            # destination-safety checks (no create/replace applies).
            if not (
                stat.S_ISREG(st.st_mode)
                or (stat.S_ISDIR(st.st_mode) and not stat.S_ISLNK(st.st_mode))
                or stat.S_ISLNK(st.st_mode)
            ):
                skip(child_rel, "unsupported")
                continue

            unsafe = not parent_safe(dst, child_rel)
            if unsafe:
                skip(child_rel, "unsafe")
                # Still discover children of ordinary source directories.
                if stat.S_ISDIR(st.st_mode) and not stat.S_ISLNK(st.st_mode):
                    walk(child_src, child_rel)
                continue

            dest = dst / child_rel

            if stat.S_ISLNK(st.st_mode):
                try:
                    target = os.readlink(child_src)
                except OSError:
                    skip(child_rel, "unsupported")
                    continue
                if isinstance(target, bytes):
                    target = target.decode("utf-8", "surrogateescape")
                if dest.exists() or dest.is_symlink():
                    try:
                        dst_st = os.lstat(dest)
                    except OSError:
                        skip(child_rel, "type conflict")
                        continue
                    if not stat.S_ISLNK(dst_st.st_mode):
                        skip(child_rel, "type conflict")
                        continue
                    dest.unlink()
                if not ensure_parent(dst, child_rel):
                    skip(child_rel, "unsupported")
                    continue
                os.symlink(target, dest)
                copied.append(child_rel)
                continue

            if stat.S_ISDIR(st.st_mode):
                outcome = ensure_and_mode_dir(child_src, dest, child_rel)
                # Type conflict still discovers children; hard fail does not.
                if outcome != "fail":
                    walk(child_src, child_rel)
                continue

            if stat.S_ISREG(st.st_mode):
                if dest.exists() or dest.is_symlink():
                    try:
                        dst_st = os.lstat(dest)
                    except OSError:
                        skip(child_rel, "type conflict")
                        continue
                    if not stat.S_ISREG(dst_st.st_mode) or stat.S_ISLNK(dst_st.st_mode):
                        skip(child_rel, "type conflict")
                        continue
                # Read source before replacing destination (same-tree safety).
                try:
                    infd = open_nofollow(child_src, os.O_RDONLY)
                except OSError:
                    skip(child_rel, "unsupported")
                    continue
                try:
                    with os.fdopen(infd, "rb") as inf:
                        data = inf.read()
                except OSError:
                    skip(child_rel, "unsupported")
                    continue
                if not ensure_parent(dst, child_rel):
                    skip(child_rel, "unsupported")
                    continue
                if dest.exists() or dest.is_symlink():
                    try:
                        dest.unlink()
                    except OSError:
                        skip(child_rel, "unsupported")
                        continue
                try:
                    with open(dest, "wb") as outf:
                        outf.write(data)
                except OSError:
                    skip(child_rel, "unsupported")
                    continue
                os.chmod(dest, st.st_mode & 0o7777)
                copied.append(child_rel)
                continue

            skip(child_rel, "unsupported")

    # Walk root "" is a source directory: ensure-and-mode before listing.
    root_outcome = ensure_and_mode_dir(src, dst, "")
    if root_outcome != "fail":
        walk(src, "")
    copied.sort()
    skipped.sort(key=lambda row: row["path"])
    return copied, skipped


def main() -> None:
    src_s, dst_s, report_s = parse_args(sys.argv[1:])
    if not is_dir_not_link(src_s) or not is_dir_not_link(dst_s):
        refuse()
    try:
        src_r = os.path.realpath(src_s)
        dst_r = os.path.realpath(dst_s)
    except OSError:
        refuse()
    # Same tree or nested (dst under src or src under dst) would extend the
    # walk into freshly created destinations; refuse rather than hang.
    if under_root(Path(src_r), Path(dst_r)) or under_root(Path(dst_r), Path(src_r)):
        refuse()

    src = Path(src_r)
    dst = Path(dst_r)
    report = Path(report_s)
    # Walk and root mode-setting must use the resolved directory inodes, not a
    # Path() that drops a trailing slash from a symlink pathname (lstat of
    # ``slink/`` is the directory; lstat of ``slink`` is the symlink).
    copied, skipped = sync_tree(src, dst)
    obj = {"copied": copied, "skipped": skipped}
    text = json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    report.parent.mkdir(parents=True, exist_ok=True)
    # Replace report without following a symlink at the path.
    if report.is_symlink() or report.exists():
        report.unlink()
    report.write_text(text, encoding="utf-8")
    raise SystemExit(0)


if __name__ == "__main__":
    main()
