#!/usr/bin/env python3
"""Deterministic configuration merger — reference implementation."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

ID_RE = re.compile(r"\A[a-z][a-z0-9_]{0,31}\Z")
DELETE = "$delete"


def refuse(msg: str) -> None:
    sys.stderr.write(msg + "\n")
    raise SystemExit(2)


def _reject_nonstandard(_s: str) -> Any:
    raise ValueError("nonstandard JSON constant")


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate object key")
        out[key] = value
    return out


def loads_strict(text: str) -> Any:
    try:
        return json.loads(
            text,
            parse_constant=_reject_nonstandard,
            object_pairs_hook=_no_duplicate_keys,
        )
    except RecursionError as exc:
        raise ValueError("JSON nesting exceeded decoder limits") from exc


def is_regular_file(path: Path) -> bool:
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return stat_isreg(st.st_mode) and not path.is_symlink()


def is_real_dir(path: Path) -> bool:
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return stat_isdir(st.st_mode) and not path.is_symlink()


def stat_isreg(mode: int) -> bool:
    return (mode & 0o170000) == 0o100000


def stat_isdir(mode: int) -> bool:
    return (mode & 0o170000) == 0o040000


def apply_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in overlay.items():
        if v == DELETE:
            out.pop(k, None)
        elif isinstance(v, dict):
            prior = out.get(k)
            prior_obj = prior if isinstance(prior, dict) else {}
            out[k] = apply_merge(prior_obj, v)
        else:
            out[k] = v
    return out


def stamp(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in overlay.items():
        if v == DELETE:
            out[k] = DELETE
        elif isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = stamp(out[k], v)
        else:
            out[k] = v
    return out


def concrete(obj: dict[str, Any]) -> dict[str, Any]:
    return apply_merge({}, obj)


def load_ordered_sources(root: Path) -> list[tuple[str, int]]:
    man_path = root / "manifest.json"
    sources_dir = root / "sources"
    if not is_regular_file(man_path) or not is_real_dir(sources_dir):
        refuse("refuse: bad manifest")
    try:
        man = loads_strict(man_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        refuse("refuse: bad manifest")
    if not isinstance(man, dict) or set(man.keys()) != {"sources"}:
        refuse("refuse: bad manifest")
    entries = man["sources"]
    if not isinstance(entries, list):
        refuse("refuse: bad manifest")
    seen: set[str] = set()
    parsed: list[tuple[str, int]] = []
    for el in entries:
        if not isinstance(el, dict) or set(el.keys()) != {"id", "priority"}:
            refuse("refuse: bad manifest")
        sid = el["id"]
        pri = el["priority"]
        if not isinstance(sid, str) or not ID_RE.match(sid):
            refuse("refuse: bad manifest")
        if type(pri) is not int or isinstance(pri, bool) or not (0 <= pri <= 1_000_000):
            refuse("refuse: bad manifest")
        if sid in seen:
            refuse("refuse: bad manifest")
        seen.add(sid)
        parsed.append((sid, pri))

    try:
        members = list(sources_dir.iterdir())
    except OSError:
        refuse("refuse: bad manifest")
    for entry in members:
        if entry.is_symlink() or not is_regular_file(entry):
            refuse("refuse: bad manifest")
        name = entry.name
        if not name.endswith(".json"):
            refuse("refuse: bad manifest")
        stem = name[: -len(".json")]
        if stem not in seen:
            refuse("refuse: bad manifest")
    for sid, _ in parsed:
        path = sources_dir / f"{sid}.json"
        if not is_regular_file(path):
            refuse("refuse: bad manifest")

    parsed.sort(key=lambda t: (t[1], t[0]))
    return parsed


def merge_root(root: Path) -> dict[str, Any]:
    ordered = load_ordered_sources(root)
    raw: dict[str, dict[str, Any]] = {}
    known = {s for s, _ in ordered}
    for sid, _ in ordered:
        path = root / "sources" / f"{sid}.json"
        try:
            obj = loads_strict(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            refuse("refuse: bad source")
        if not isinstance(obj, dict):
            refuse("refuse: bad source")
        for k in obj:
            if k.startswith("$") and k != "$extends":
                refuse("refuse: bad source")
        if "$extends" in obj:
            ext = obj["$extends"]
            if not isinstance(ext, list) or not all(isinstance(x, str) for x in ext):
                refuse("refuse: bad source")
            if len(ext) != len(set(ext)):
                refuse("refuse: bad source")
            for x in ext:
                if x not in known:
                    refuse("refuse: bad source")
        raw[sid] = obj

    memo: dict[str, dict[str, Any]] = {}
    open_set: set[str] = set()

    def resolve(sid: str) -> dict[str, Any]:
        if sid in memo:
            return memo[sid]
        if sid in open_set:
            refuse("refuse: bad source")
        open_set.add(sid)
        obj = raw[sid]
        acc: dict[str, Any] = {}
        for parent in obj.get("$extends", []):
            acc = apply_merge(acc, concrete(resolve(parent)))
        data = {k: v for k, v in obj.items() if k != "$extends"}
        acc = stamp(acc, data)
        open_set.discard(sid)
        memo[sid] = acc
        return acc

    final: dict[str, Any] = {}
    for sid, _ in ordered:
        final = apply_merge(final, resolve(sid))
    return final


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    result = merge_root(Path(args.root))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(result, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
