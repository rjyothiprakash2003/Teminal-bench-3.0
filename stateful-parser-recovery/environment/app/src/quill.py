#!/usr/bin/env python3
"""Quill nest parser — starter with recovery bugs the sealed suite isolates."""

from __future__ import annotations

import argparse
import json
import re
import stat
import sys
from pathlib import Path
from typing import Any

NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,16}$")
INT_RE = re.compile(r"^-?(0|[1-9][0-9]*)$")
RANK = {"form": 0, "nest": 1, "dup": 2, "unbound": 3}


def refuse(msg: str) -> None:
    sys.stderr.write(msg + "\n")
    raise SystemExit(2)


def valid_name(tok: str) -> bool:
    return NAME_RE.fullmatch(tok) is not None and len(tok.encode("utf-8")) <= 16


def parse_int(tok: str) -> int | None:
    if INT_RE.fullmatch(tok) is None:
        return None
    try:
        value = int(tok, 10)
    except ValueError:
        return None
    if not (-(2**53) < value < 2**53):
        return None
    return 0 if value == 0 else value


def pick(faults: list[tuple[str, int]]) -> tuple[str, int] | None:
    if not faults:
        return None
    return min(faults, key=lambda f: (f[1], RANK[f[0]]))


def write_out(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(obj, indent=2, ensure_ascii=False) + "\n"
    path.write_text(data, encoding="utf-8")


def parse_quill(text: str) -> dict[str, Any]:
    if text == "":
        lines: list[str] = []
    else:
        parts = text.split("\n")
        if parts and parts[-1] == "":
            parts = parts[:-1]
        lines = parts

    stack: list[dict[str, Any]] = []
    applied_binds: list[dict[str, Any]] = []
    applied_uses: list[dict[str, Any]] = []
    faults: list[tuple[str, int]] = []
    # Defect: one halt flag also blocks end after DUP (should be halt_decls only).
    halted = False
    closer_mismatch = False

    def lookup(name: str) -> int | None:
        for frame in reversed(stack):
            binds = frame["binds"]
            if name not in binds:
                continue
            kind, value = binds[name]
            if kind == "VAL":
                return int(value)
            target = str(value)
            # Defect: resolve alias targets across parent frames.
            for fr in reversed(stack):
                b = fr["binds"]
                if target in b and b[target][0] == "VAL":
                    return int(b[target][1])
            return None
        return None

    for idx, raw in enumerate(lines, start=1):
        if raw.strip() == "":
            continue
        tokens = raw.strip().split()
        verb = tokens[0] if tokens else ""

        if verb not in {"open", "end", "bind", "use"}:
            faults.append(("form", idx))
            halted = True
            continue

        if verb == "open":
            bad = (
                len(tokens) != 3
                or tokens[1] not in {"UNIT", "BLOCK"}
                or not valid_name(tokens[2])
            )
            if bad:
                faults.append(("form", idx))
                halted = True
                continue
            if halted:
                continue
            stack.append(
                {
                    "kind": tokens[1],
                    "name": tokens[2],
                    "line": idx,
                    "binds": {},
                }
            )
            continue

        if verb == "end":
            bad = len(tokens) != 2 or tokens[1] not in {"UNIT", "BLOCK"}
            if bad:
                faults.append(("form", idx))
                halted = True
                continue
            if halted:
                continue
            want = tokens[1]
            if not stack or stack[-1]["kind"] != want:
                faults.append(("nest", idx))
                halted = True
                closer_mismatch = True
                continue
            stack.pop()
            continue

        if verb == "bind":
            if len(tokens) != 4 or tokens[1] not in {"VAL", "ALIAS"}:
                faults.append(("form", idx))
                halted = True
                continue
            bkind = tokens[1]
            name = tokens[2]
            if not valid_name(name):
                faults.append(("form", idx))
                halted = True
                continue
            if bkind == "VAL":
                payload: Any = parse_int(tokens[3])
                if payload is None:
                    faults.append(("form", idx))
                    halted = True
                    continue
            else:
                if not valid_name(tokens[3]):
                    faults.append(("form", idx))
                    halted = True
                    continue
                payload = tokens[3]
            if halted:
                continue
            if not stack:
                faults.append(("form", idx))
                halted = True
                continue
            cur = stack[-1]
            # Defect: duplicate only when kinds match.
            if name in cur["binds"] and cur["binds"][name][0] == bkind:
                faults.append(("dup", idx))
                halted = True
                continue
            cur["binds"][name] = (bkind, payload)
            applied_binds.append(
                {
                    "frame": cur["name"],
                    "kind": bkind,
                    "name": name,
                    "value": payload,
                    "line": idx,
                }
            )
            continue

        if len(tokens) != 2 or not valid_name(tokens[1]):
            faults.append(("form", idx))
            halted = True
            continue
        if halted:
            continue
        if not stack:
            faults.append(("form", idx))
            halted = True
            continue
        name = tokens[1]
        resolved = lookup(name)
        if resolved is None:
            faults.append(("unbound", idx))
            # Defect: unbound blocks later ends (should not halt).
            halted = True
            continue
        applied_uses.append({"name": name, "value": resolved, "line": idx})

    # Defect: any halt suppresses EOF nest (should only be closer_mismatch).
    if stack and not halted:
        faults.append(("nest", stack[0]["line"]))

    chosen = pick(faults)
    if chosen is not None:
        return {"status": "fault", "fault": chosen[0], "line": chosen[1]}

    return {
        "status": "ok",
        "frames": [
            {"kind": f["kind"], "name": f["name"], "line": f["line"]} for f in stack
        ],
        "bindings": applied_binds,
        "uses": applied_uses,
    }


def load_src(path: Path) -> str:
    try:
        st = path.lstat()
    except OSError:
        refuse("refuse: bad args")
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        refuse("refuse: bad args")
    try:
        raw = path.read_bytes()
    except OSError:
        refuse("refuse: bad args")
    if not raw:
        return ""
    if not raw.endswith(b"\n"):
        refuse("refuse: bad source")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        refuse("refuse: bad source")


def main() -> None:
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--src", default=None)
    ap.add_argument("--out", default=None)
    args, unknown = ap.parse_known_args()
    if unknown or args.src is None or args.out is None or args.src == "" or args.out == "":
        refuse("refuse: bad args")
    text = load_src(Path(args.src))
    report = parse_quill(text)
    write_out(Path(args.out), report)
    raise SystemExit(0)


if __name__ == "__main__":
    main()
