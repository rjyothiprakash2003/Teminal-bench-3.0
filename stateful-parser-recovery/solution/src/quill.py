#!/usr/bin/env python3
"""Quill nest parser — stateful frames with earliest-fault recovery."""

from __future__ import annotations

import json
import re
import stat
import sys
from pathlib import Path
from typing import Any

NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,16}$")
INT_RE = re.compile(r"^-?(0|[1-9][0-9]*)$")
RANK = {"form": 0, "nest": 1, "dup": 2, "unbound": 3}
FLAGS = {"--src", "--out"}


def refuse(msg: str = "refuse: bad args") -> None:
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


def tokenize(raw: str) -> list[str]:
    """Strip line edges (full Unicode), then split only on ASCII space/tab."""
    s = raw.strip()
    if not s:
        return []
    parts: list[str] = []
    cur: list[str] = []
    for ch in s:
        if ch == " " or ch == "\t":
            if cur:
                parts.append("".join(cur))
                cur = []
        else:
            cur.append(ch)
    if cur:
        parts.append("".join(cur))
    return parts


def pick(faults: list[tuple[str, int]]) -> tuple[str, int] | None:
    if not faults:
        return None
    return min(faults, key=lambda f: (f[1], RANK[f[0]]))


def write_out(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(obj, indent=2, ensure_ascii=False) + "\n"
    path.write_text(data, encoding="utf-8")


def parse_args(argv: list[str]) -> tuple[str, str]:
    """Parse --src/--out without argparse missing-value diagnostics.

    A following token that is itself ``--src`` or ``--out`` is not a value: that
    is a required flag lacking a value. An empty string value is also refused.
    """
    src = out = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in FLAGS:
            if i + 1 >= len(argv):
                refuse()
            val = argv[i + 1]
            if not val or val in FLAGS:
                refuse()
            if a == "--src":
                src = val
            else:
                out = val
            i += 2
            continue
        refuse()
    if src is None or out is None:
        refuse()
    return src, out


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
    halt_all = False
    halt_decls = False
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
            if target in binds and binds[target][0] == "VAL":
                return int(binds[target][1])
            return None
        return None

    for idx, raw in enumerate(lines, start=1):
        if raw.strip() == "":
            continue
        tokens = tokenize(raw)
        verb = tokens[0] if tokens else ""

        if verb not in {"open", "end", "bind", "use"}:
            faults.append(("form", idx))
            halt_all = True
            continue

        if verb == "open":
            bad = (
                len(tokens) != 3
                or tokens[1] not in {"UNIT", "BLOCK"}
                or not valid_name(tokens[2])
            )
            if bad:
                faults.append(("form", idx))
                halt_all = True
                continue
            if halt_all or halt_decls:
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
                halt_all = True
                continue
            want = tokens[1]
            # Even after FORM halt: still discover mismatch NEST + closer_mismatch
            # for pick/EOF gating. Do not apply (no pop) when halted.
            if not stack or stack[-1]["kind"] != want:
                faults.append(("nest", idx))
                halt_all = True
                closer_mismatch = True
                continue
            if halt_all:
                continue
            stack.pop()
            continue

        if verb == "bind":
            if len(tokens) != 4 or tokens[1] not in {"VAL", "ALIAS"}:
                faults.append(("form", idx))
                halt_all = True
                continue
            bkind = tokens[1]
            name = tokens[2]
            if not valid_name(name):
                faults.append(("form", idx))
                halt_all = True
                continue
            if bkind == "VAL":
                payload: Any = parse_int(tokens[3])
                if payload is None:
                    faults.append(("form", idx))
                    halt_all = True
                    continue
            else:
                if not valid_name(tokens[3]):
                    faults.append(("form", idx))
                    halt_all = True
                    continue
                payload = tokens[3]
            if halt_all or halt_decls:
                continue
            if not stack:
                faults.append(("form", idx))
                halt_all = True
                continue
            cur = stack[-1]
            if name in cur["binds"]:
                faults.append(("dup", idx))
                halt_decls = True
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
            halt_all = True
            continue
        if halt_all or halt_decls:
            continue
        if not stack:
            faults.append(("form", idx))
            halt_all = True
            continue
        name = tokens[1]
        resolved = lookup(name)
        if resolved is None:
            faults.append(("unbound", idx))
            continue
        applied_uses.append({"name": name, "value": resolved, "line": idx})

    if stack and not closer_mismatch:
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
        refuse()
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        refuse()
    try:
        raw = path.read_bytes()
    except OSError:
        refuse()
    if not raw:
        return ""
    if not raw.endswith(b"\n"):
        refuse("refuse: bad source")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        refuse("refuse: bad source")


def main() -> None:
    src_s, out_s = parse_args(sys.argv[1:])
    text = load_src(Path(src_s))
    report = parse_quill(text)
    write_out(Path(out_s), report)
    raise SystemExit(0)


if __name__ == "__main__":
    main()
