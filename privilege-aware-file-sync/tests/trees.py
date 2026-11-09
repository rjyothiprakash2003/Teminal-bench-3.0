#!/usr/bin/env python3
"""Build privilege-sync fixture trees from manifests (shared by mint and verifier)."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _rel_path(entry: dict[str, Any]) -> str:
    """Resolve relative path; ``path_hex`` is filesystem bytes (surrogateescape)."""
    if "path_hex" in entry:
        return os.fsdecode(bytes.fromhex(entry["path_hex"]))
    return entry["path"]


def apply_entries(root: Path, entries: list[dict[str, Any]]) -> None:
    """Create files/dirs/symlinks/fifos/sockets under root.

    Default order: directories first by depth. When ``create_order`` is set on
    an entry (integer), that value is the primary sort key so insertion order
    can be forced independently of path sort (needed so unsorted ``listdir``
    is distinguishable from byte-sorted discovery).
    """

    def depth(entry: dict[str, Any]) -> int:
        return _rel_path(entry).count("/")

    def sort_key(entry: dict[str, Any]) -> tuple:
        if "create_order" in entry:
            return (int(entry["create_order"]),)
        return (0 if entry["kind"] == "dir" else 1, depth(entry), _rel_path(entry))

    ordered = sorted(entries, key=sort_key)
    for entry in ordered:
        rel = _rel_path(entry)
        dest = root / rel if rel else root
        kind = entry["kind"]
        if kind == "dir":
            dest.mkdir(parents=True, exist_ok=True)
            if "mode" in entry:
                os.chmod(dest, int(entry["mode"]))
        elif kind == "file":
            _ensure_parent(dest)
            if "content_hex" in entry:
                dest.write_bytes(bytes.fromhex(entry["content_hex"]))
            else:
                data = entry.get("content", "")
                if isinstance(data, str):
                    dest.write_text(data, encoding="utf-8")
                else:
                    dest.write_bytes(bytes(data))
            if "mode" in entry:
                os.chmod(dest, int(entry["mode"]))
        elif kind == "symlink":
            _ensure_parent(dest)
            if dest.exists() or dest.is_symlink():
                dest.unlink()
            if "target_hex" in entry:
                os.symlink(bytes.fromhex(entry["target_hex"]), dest)
            else:
                target = entry["target"]
                if isinstance(target, (bytes, bytearray)):
                    os.symlink(bytes(target), dest)
                else:
                    os.symlink(target, dest)
        elif kind == "fifo":
            _ensure_parent(dest)
            if dest.exists() or dest.is_symlink():
                if dest.is_dir() and not dest.is_symlink():
                    raise ValueError("fifo path is directory")
                dest.unlink()
            os.mkfifo(dest, int(entry.get("mode", 0o644)))
        elif kind == "socket":
            import socket as _socket
            import tempfile

            _ensure_parent(dest)
            if dest.exists() or dest.is_symlink():
                if dest.is_dir() and not dest.is_symlink():
                    raise ValueError("socket path is directory")
                dest.unlink()
            # AF_UNIX bind path length is limited; bind under /tmp then rename.
            tdir = tempfile.mkdtemp(prefix="sk", dir="/tmp")
            short = Path(tdir) / "s"
            sock = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
            try:
                sock.bind(str(short))
            finally:
                sock.close()
            os.rename(short, dest)
            os.rmdir(tdir)
            if "mode" in entry:
                os.chmod(dest, int(entry["mode"]))
        else:
            raise ValueError(f"unknown kind {kind}")


def materialize_case(work: Path, manifest: dict[str, Any]) -> tuple[Path, Path]:
    """Build src/ and dst/ under work from a sealed manifest. Returns (src, dst)."""
    src = work / "src"
    dst = work / "dst"
    if src.exists():
        raise RuntimeError("src exists")
    src.mkdir(parents=True)
    dst.mkdir(parents=True)
    if "extra" in manifest:
        extra = work / "extra"
        extra.mkdir(parents=True, exist_ok=True)
        apply_entries(extra, manifest["extra"])
    apply_entries(src, manifest.get("src", []))
    apply_entries(dst, manifest.get("dst_pre", []))
    return src, dst


def apply_src_chmod(src: Path, manifest: dict[str, Any]) -> None:
    for item in manifest.get("src_chmod", []):
        if "path_hex" in item:
            rel = os.fsdecode(bytes.fromhex(item["path_hex"]))
        else:
            rel = item["path"]
        path = src if rel in ("", ".") else src / rel
        os.chmod(path, int(item["mode"]))


def lstat_kind(path: Path) -> str:
    st = os.lstat(path)
    if stat.S_ISLNK(st.st_mode):
        return "symlink"
    if stat.S_ISDIR(st.st_mode):
        return "dir"
    if stat.S_ISREG(st.st_mode):
        return "file"
    if stat.S_ISFIFO(st.st_mode):
        return "fifo"
    return "other"


def snapshot_tree(root: Path) -> dict[str, dict[str, Any]]:
    """Map relative path -> kind metadata for comparison (no follow).

    Includes FIFOs so type-conflict fixtures detect deletion of special files.
    """
    out: dict[str, dict[str, Any]] = {}

    def record(rel: str, path: Path) -> None:
        st = os.lstat(path)
        if stat.S_ISLNK(st.st_mode):
            target = os.readlink(path)
            if isinstance(target, bytes):
                target = target.decode("utf-8", "surrogateescape")
            out[rel] = {"kind": "symlink", "target": target}
        elif stat.S_ISDIR(st.st_mode):
            out[rel] = {"kind": "dir", "mode": st.st_mode & 0o7777}
        elif stat.S_ISREG(st.st_mode):
            out[rel] = {
                "kind": "file",
                "mode": st.st_mode & 0o7777,
                "content": path.read_bytes(),
            }
        elif stat.S_ISFIFO(st.st_mode):
            out[rel] = {"kind": "fifo", "mode": st.st_mode & 0o7777}
        elif stat.S_ISSOCK(st.st_mode):
            out[rel] = {"kind": "socket", "mode": st.st_mode & 0o7777}

    # os.walk skips non-dir specials at the top of each listing; walk dirs and
    # also scan each directory's entries with listdir so FIFOs are recorded.
    stack = [root]
    # Record the walk-root directory itself as "" so destination-root mode is graded.
    record("", root)
    while stack:
        dp = stack.pop()
        rel_dir = dp.relative_to(root)
        prefix = "" if str(rel_dir) == "." else str(rel_dir).replace(os.sep, "/")
        if prefix:
            record(prefix, dp)
        try:
            names = os.listdir(dp)
        except OSError:
            continue
        for name in sorted(names, key=os.fsencode):
            child = dp / name
            rel = name if not prefix else f"{prefix}/{name}"
            try:
                st = os.lstat(child)
            except OSError:
                continue
            if stat.S_ISDIR(st.st_mode) and not stat.S_ISLNK(st.st_mode):
                stack.append(child)
            else:
                record(rel, child)
    return out


# --- Symlink-target follow detection (inotify) ---

_IN_ATTRIB = 0x00000004
_IN_OPEN = 0x00000020
_IN_NONBLOCK = 0x00000800


class TargetFollowWatch:
    """Fail if a syncer opens/chmods/chowns a watched symlink target (follows)."""

    def __init__(self, targets: list[Path]):
        self.targets = [p for p in targets if p is not None]
        self._fd = -1
        self._libc = None

    def __enter__(self):
        if not self.targets:
            return self
        import ctypes

        self._libc = ctypes.CDLL(None, use_errno=True)
        self._fd = int(self._libc.inotify_init1(_IN_NONBLOCK))
        if self._fd < 0:
            raise OSError(ctypes.get_errno(), "inotify_init1")
        for path in self.targets:
            wd = int(
                self._libc.inotify_add_watch(
                    ctypes.c_int(self._fd),
                    os.fsencode(path),
                    _IN_OPEN | _IN_ATTRIB,
                )
            )
            if wd < 0:
                raise OSError(ctypes.get_errno(), f"inotify_add_watch {path}")
        return self

    def drain_violations(self) -> list[int]:
        if self._fd < 0:
            return []
        import select
        import struct

        masks: list[int] = []
        while select.select([self._fd], [], [], 0)[0]:
            try:
                data = os.read(self._fd, 65536)
            except BlockingIOError:
                break
            off = 0
            while off + 16 <= len(data):
                _wd, mask, _cookie, nlen = struct.unpack_from("iIII", data, off)
                off += 16 + nlen
                if mask & (_IN_OPEN | _IN_ATTRIB):
                    masks.append(mask)
        return masks

    def __exit__(self, exc_type, exc, tb):
        if self._fd >= 0:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = -1
        return False


def source_symlink_targets(src_root: Path) -> list[Path]:
    """Targets of source symlinks that lie *outside* the source tree.

    In-tree targets (``alias -> body.txt``) are opened when those paths are
    copied as ordinary files; watching them would false-positive. External
    traps (``../extra/secret.dat``) and out-of-tree specials are the sites that
    detect a following open/chmod/chown.
    """
    return _external_symlink_targets(src_root)


def destination_symlink_targets(dst_root: Path) -> list[Path]:
    """Targets of pre-existing destination symlinks outside the destination tree.

    Replacing a destination symlink must not open/chmod/chown its live target
    (SYNC.md: never open/chmod/chown the symlink target).
    """
    return _external_symlink_targets(dst_root)


def _external_symlink_targets(root: Path) -> list[Path]:
    try:
        root_real = Path(os.path.realpath(root))
    except OSError:
        root_real = root.resolve()
    out: list[Path] = []
    for dirpath, dirs, files in os.walk(root, followlinks=False):
        root_p = Path(dirpath)
        sym_dirs = [name for name in dirs if (root_p / name).is_symlink()]
        for name in sym_dirs:
            dirs.remove(name)
        for name in sym_dirs + list(files):
            path = root_p / name
            if not path.is_symlink():
                continue
            try:
                raw = os.readlink(path)
            except OSError:
                continue
            if isinstance(raw, bytes):
                raw = os.fsdecode(raw)
            cand = Path(raw)
            if not cand.is_absolute():
                cand = path.parent / cand
            try:
                st = os.lstat(cand)
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode) or stat.S_ISDIR(st.st_mode):
                try:
                    resolved = Path(os.path.realpath(cand))
                except OSError:
                    resolved = cand
                # Skip ordinary in-tree targets (opened when those entries copy).
                try:
                    resolved.relative_to(root_real)
                    continue
                except ValueError:
                    pass
            else:
                # FIFO/socket targets: watch even under root (copy never opens them).
                resolved = cand
            out.append(resolved)
    seen: set[str] = set()
    uniq: list[Path] = []
    for p in out:
        key = str(p)
        if key not in seen:
            seen.add(key)
            uniq.append(p)
    return uniq
