#!/usr/bin/env python3
"""Independent sealed-report oracle derived from CAPABILITY.md (not the Go reference).

Walks a materialized tree and audience JSON using the page's DAC / path /
remediation / report rules, then emits the exact report bytes json.dumps would.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any


def _meta(path: Path) -> dict[str, Any]:
    st = os.lstat(path)
    um = st.st_mode & 0o7777
    return {
        "path": str(path),
        "uid": st.st_uid,
        "gid": st.st_gid,
        "unix_mode": um,
        "perm": um & 0o777,
    }


def _under_root(root_real: str, candidate: str) -> bool:
    root_real = os.path.normpath(root_real)
    candidate = os.path.normpath(candidate)
    if candidate == root_real:
        return True
    if root_real == os.sep:
        return candidate.startswith(os.sep)
    return candidate.startswith(root_real + os.sep)


def _class_bit(subj: dict[str, Any], node: dict[str, Any], mask: int) -> bool:
    if subj["uid"] == node["uid"]:
        return (node["perm"] & mask) != 0
    for g in subj["gids"]:
        if g == node["gid"]:
            return (node["perm"] & (mask >> 3)) != 0
    return (node["perm"] & (mask >> 6)) != 0


def _class_used(subj: dict[str, Any], node: dict[str, Any]) -> str:
    if subj["uid"] == node["uid"]:
        return "owner"
    for g in subj["gids"]:
        if g == node["gid"]:
            return "group"
    return "other"


def _can_exec(subj: dict[str, Any], node: dict[str, Any]) -> bool:
    return _class_bit(subj, node, 0o100)


def _can_read(subj: dict[str, Any], node: dict[str, Any]) -> bool:
    return _class_bit(subj, node, 0o400)


def _unintended(entry: dict[str, Any], subj: dict[str, Any]) -> bool:
    if subj["uid"] in entry["allow_uids"]:
        return False
    allow = set(entry["allow_gids"])
    for g in subj["gids"]:
        if g in allow:
            return False
    return True


def _dirs_from_root_to(root_real: str, resolved_abs: str) -> list[dict[str, Any]] | None:
    resolved_abs = os.path.normpath(resolved_abs)
    root_real = os.path.normpath(root_real)
    if resolved_abs == root_real:
        return []
    if not _under_root(root_real, resolved_abs):
        return None
    rel = os.path.relpath(resolved_abs, root_real)
    if rel == ".." or rel.startswith(".." + os.sep):
        return None
    out: list[dict[str, Any]] = []
    cur = root_real
    for part in rel.split(os.sep):
        if part in ("", "."):
            continue
        cur = os.path.join(cur, part)
        try:
            st = os.lstat(cur)
        except OSError:
            return None
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            return None
        out.append(_meta(Path(cur)))
    return out


def _merge_dirs(dst: list[dict[str, Any]], src: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Union ancestry by path so earlier permitted-link pivots stay checked."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for d in dst + src:
        p = d["path"]
        if p in seen:
            continue
        seen.add(p)
        out.append(d)
    return out


def _resolve_entry(root_real: str, rel: str) -> dict[str, Any]:
    parts = rel.split("/")
    cur = root_real
    dirs: list[dict[str, Any]] = []

    for i, part in enumerate(parts):
        nxt = os.path.join(cur, part)
        try:
            st = os.lstat(nxt)
        except OSError:
            return {"kind": "missing"}
        final = i == len(parts) - 1
        if stat.S_ISLNK(st.st_mode):
            try:
                # strict=True matches filepath.EvalSymlinks failing on dangling links.
                resolved = os.path.realpath(nxt, strict=True)
            except OSError:
                return {"kind": "unsupported" if final else "escaped"}
            resolved = os.path.normpath(resolved)
            if not _under_root(root_real, resolved):
                return {"kind": "unsupported" if final else "escaped"}
            if final:
                try:
                    rst = os.lstat(resolved)
                except OSError:
                    return {"kind": "unsupported"}
                if stat.S_ISLNK(rst.st_mode) or not stat.S_ISREG(rst.st_mode):
                    return {"kind": "unsupported"}
                parent = os.path.dirname(resolved)
                anc = _dirs_from_root_to(root_real, parent)
                if anc is None:
                    return {"kind": "escaped"}
                return {"kind": "ok", "dirs": _merge_dirs(dirs, anc), "file": _meta(Path(resolved))}
            try:
                rst = os.lstat(resolved)
            except OSError:
                return {"kind": "unsupported"}
            if stat.S_ISLNK(rst.st_mode) or not stat.S_ISDIR(rst.st_mode):
                return {"kind": "unsupported"}
            anc = _dirs_from_root_to(root_real, resolved)
            if anc is None:
                return {"kind": "escaped"}
            dirs = _merge_dirs(dirs, anc)
            cur = resolved
            continue
        if final:
            if not stat.S_ISREG(st.st_mode):
                return {"kind": "unsupported"}
            return {"kind": "ok", "dirs": dirs, "file": _meta(Path(nxt))}
        if not stat.S_ISDIR(st.st_mode):
            return {"kind": "unsupported"}
        dirs.append(_meta(Path(nxt)))
        cur = nxt
    return {"kind": "missing"}


def _effective_read(
    subj: dict[str, Any],
    root_meta: dict[str, Any],
    dirs: list[dict[str, Any]],
    file: dict[str, Any],
) -> tuple[bool, str]:
    if not _can_exec(subj, root_meta):
        return False, ""
    for d in dirs:
        if not _can_exec(subj, d):
            return False, ""
    if not _can_read(subj, file):
        return False, ""
    return True, _class_used(subj, file)


def _load_audience(path: Path) -> dict[str, Any]:
    # Minted fixtures are already contract-valid; parse as ordinary JSON.
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        "entries": [
            {
                "path": e["path"],
                "allow_uids": [int(x) for x in e["allow_uids"]],
                "allow_gids": [int(x) for x in e["allow_gids"]],
            }
            for e in data["entries"]
        ],
        "subjects": [
            {
                "uid": int(s["uid"]),
                "gids": [int(x) for x in s["gids"]],
            }
            for s in data["subjects"]
        ],
    }


def audit_tree(root: Path, audience_path: Path) -> dict[str, Any]:
    """Compute findings/skipped for a materialized --root and audience file."""
    root = Path(root)
    root_real = os.path.realpath(root)
    st = os.lstat(root)
    if not stat.S_ISDIR(st.st_mode) or stat.S_ISLNK(st.st_mode):
        raise ValueError("root must be a non-symlink directory")
    root_meta = _meta(root)
    aud = _load_audience(audience_path)

    findings: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for e in aud["entries"]:
        rr = _resolve_entry(root_real, e["path"])
        if rr["kind"] != "ok":
            skipped.append({"path": e["path"], "reason": rr["kind"]})
            continue
        exp_set: set[str] = set()
        used: dict[str, bool] = {}
        for s in aud["subjects"]:
            if not _unintended(e, s):
                continue
            ok_read, cls = _effective_read(s, root_meta, rr["dirs"], rr["file"])
            if not ok_read:
                continue
            exp_set.add(f"uid:{s['uid']}")
            used[cls] = True
        if not exp_set:
            continue
        mode = rr["file"]["unix_mode"]
        if used.get("other"):
            mode &= ~0o004
        if used.get("group"):
            if rr["file"]["gid"] not in e["allow_gids"]:
                mode &= ~0o040
        findings.append(
            {
                "path": e["path"],
                "exposure": sorted(exp_set),
                "remediation": {"mode": f"{mode:04o}"},
            }
        )

    findings.sort(key=lambda f: f["path"])
    skipped.sort(key=lambda s: s["path"])
    return {"findings": findings, "skipped": skipped}


def dumps_report(obj: dict[str, Any]) -> bytes:
    """Report bytes per CAPABILITY.md: json.dumps(indent=2, sort_keys=True, ensure_ascii=False)+LF."""
    text = json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    return text.encode("utf-8")


def mint_expect(root: Path, audience_path: Path) -> bytes:
    return dumps_report(audit_tree(root, audience_path))
