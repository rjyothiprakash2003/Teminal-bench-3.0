from __future__ import annotations

import os
import shutil
import socket
import stat
import subprocess
from pathlib import Path
from typing import Any

SANDBOX_UID = 2000


def apply_nodes(root: Path, nodes: list[dict[str, Any]]) -> None:
    def depth(n: dict[str, Any]) -> int:
        return str(n["path"]).count("/")

    ordered = sorted(
        nodes,
        key=lambda n: (
            0 if n["kind"] == "dir" else 1 if n["kind"] not in ("symlink",) else 2,
            depth(n),
            n["path"],
        ),
    )
    for n in ordered:
        dest = root / n["path"]
        kind = n["kind"]
        mode = int(str(n.get("mode", "0755")), 8)
        uid = int(n.get("uid", 0))
        gid = int(n.get("gid", 0))
        if kind == "dir":
            dest.mkdir(parents=True, exist_ok=True)
            os.chown(dest, uid, gid)
            os.chmod(dest, mode)
        elif kind == "file":
            dest.parent.mkdir(parents=True, exist_ok=True)
            content = n.get("content", "x")
            if isinstance(content, str) and content.startswith("bytes:"):
                size = int(content.split(":", 1)[1])
                dest.write_bytes(b"Z" * size)
            elif isinstance(content, (bytes, bytearray)):
                dest.write_bytes(bytes(content))
            else:
                dest.write_text(str(content), encoding="utf-8")
            os.chown(dest, uid, gid)
            os.chmod(dest, mode)
        elif kind == "symlink":
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists() or dest.is_symlink():
                dest.unlink()
            os.symlink(n["target"], dest)
        elif kind == "fifo":
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists() or dest.is_symlink():
                dest.unlink()
            os.mkfifo(dest, mode)
            os.chown(dest, uid, gid)
            os.chmod(dest, mode)
        elif kind == "socket":
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists() or dest.is_symlink():
                dest.unlink()
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                sock.bind(str(dest))
            finally:
                sock.close()
            os.chown(dest, uid, gid)
            os.chmod(dest, mode)
        elif kind == "device":
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists() or dest.is_symlink():
                dest.unlink()
            os.mknod(dest, mode | stat.S_IFCHR, os.makedev(1, 3))
            os.chown(dest, uid, gid)
            os.chmod(dest, mode)
        else:
            raise ValueError(kind)


def grant_sandbox_search(root: Path) -> None:
    """Allow the unprivileged auditor uid to search directories via ACL.

    Skip directories that already grant other-execute: adding a named user ACL
    would raise the ACL mask and inflate st_mode group bits, corrupting DAC
    class checks the auditor reads via lstat. When other-execute is absent,
    a named --x ACL is required so uid 2000 can still lstat under setpriv.
    """
    if not root.exists():
        return
    for dirpath, _dirnames, _filenames in os.walk(root, followlinks=False):
        mode = os.lstat(dirpath).st_mode & 0o777
        if mode & 0o001:
            continue
        subprocess.run(
            ["setfacl", "-m", f"u:{SANDBOX_UID}:--x", dirpath],
            check=True,
            capture_output=True,
            text=True,
        )


def materialize(work: Path, manifest: dict[str, Any]) -> Path:
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    tree = work / "tree"
    tree.mkdir(parents=True)
    os.chown(tree, 0, 0)
    os.chmod(tree, 0o755)
    extra_nodes = manifest.get("extra") or []
    if extra_nodes:
        extra = work / "extra"
        extra.mkdir(parents=True)
        os.chown(extra, 0, 0)
        os.chmod(extra, 0o755)
        apply_nodes(extra, extra_nodes)
    # optional sibling whose name is a prefix extension of "tree"
    tree_extra_nodes = manifest.get("tree_extra") or []
    if tree_extra_nodes:
        te = work / "tree_extra"
        te.mkdir(parents=True)
        os.chown(te, 0, 0)
        os.chmod(te, 0o755)
        apply_nodes(te, tree_extra_nodes)
    apply_nodes(tree, manifest.get("nodes") or [])
    root_mode = manifest.get("root_mode", "0755")
    os.chmod(tree, int(str(root_mode), 8))
    if manifest.get("hostile_parent"):
        parent = tree.parent  # .../fs
        os.chown(parent, SANDBOX_UID, SANDBOX_UID)
        os.chmod(parent, 0o700)
    if manifest.get("root_via_alias"):
        # Place the audit root behind a symlinked ancestor while keeping the
        # final --root component a real directory (lstat-ok).
        physical = work / "physical"
        physical.mkdir(parents=True)
        dest = physical / "tree"
        tree.rename(dest)
        alias = work / "alias"
        if alias.exists() or alias.is_symlink():
            alias.unlink()
        os.symlink(physical, alias)
        tree = alias / "tree"
    grant_sandbox_search(work)
    return tree
