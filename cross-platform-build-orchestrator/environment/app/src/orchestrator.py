#!/usr/bin/env python3
"""Cross-platform build orchestrator."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

ID_RE = re.compile(r"\A[a-z][a-z0-9_]{0,31}\Z")

ARCHES = frozenset({"x86_64", "aarch64", "riscv64"})
OSES = frozenset({"linux", "darwin", "windows"})
LINKERS = frozenset({"bfd", "lld", "mold", "ld64", "link"})
LEGAL_PAIRS = frozenset(
    {
        ("linux", "bfd"),
        ("linux", "lld"),
        ("linux", "mold"),
        ("darwin", "ld64"),
        ("darwin", "lld"),
        ("windows", "link"),
        ("windows", "lld"),
    }
)
ARCH_INJECT = {
    "x86_64": ["-m64"],
    "aarch64": ["-march=armv8-a"],
    "riscv64": ["-march=rv64gc"],
}
LINKER_PROFILES = {
    ("linux", "bfd"): ["-Wl,--hash-style=gnu"],
    ("linux", "lld"): ["-fuse-ld=lld"],
    ("linux", "mold"): ["-fuse-ld=mold"],
    ("darwin", "ld64"): ["-Wl,-dead_strip"],
    ("darwin", "lld"): ["-fuse-ld=lld"],
    ("windows", "link"): ["/INCREMENTAL:NO"],
    ("windows", "lld"): ["-fuse-ld=lld"],
}
ALLOWED_TOP = frozenset({"cflags", "ldflags", "env", "defines"})
ALLOWED_OVERLAY_NAMES = frozenset(
    {f"{os_name}.json" for os_name in OSES}
    | {f"{os_name}_{arch}.json" for os_name in OSES for arch in ARCHES}
)


def refuse(msg: str) -> None:
    sys.stderr.write(msg + "\n")
    raise SystemExit(2)


def check_map(obj: Any, path: str) -> dict[str, str]:
    if not isinstance(obj, dict):
        refuse(path)
    out: dict[str, str] = {}
    for k, v in obj.items():
        if not isinstance(k, str) or not isinstance(v, str):
            refuse(path)
        out[k] = v
    return out


def check_settings(obj: Any, path: str) -> dict[str, Any]:
    if not isinstance(obj, dict):
        refuse(path)
    for k in obj:
        if k not in ALLOWED_TOP:
            refuse(path)
    out: dict[str, Any] = {
        "cflags": [],
        "ldflags": [],
        "env": {},
        "defines": {},
    }
    if "cflags" in obj:
        if not isinstance(obj["cflags"], list) or not all(
            isinstance(x, str) for x in obj["cflags"]
        ):
            refuse(path)
        out["cflags"] = list(obj["cflags"])
    if "ldflags" in obj:
        if not isinstance(obj["ldflags"], list) or not all(
            isinstance(x, str) for x in obj["ldflags"]
        ):
            refuse(path)
        out["ldflags"] = list(obj["ldflags"])
    if "env" in obj:
        out["env"] = check_map(obj["env"], path)
    if "defines" in obj:
        out["defines"] = check_map(obj["defines"], path)
    return out


def apply_overlay(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = {
        "cflags": list(base.get("cflags", [])),
        "ldflags": list(base.get("ldflags", [])),
        "env": dict(base.get("env", {})),
        "defines": dict(base.get("defines", {})),
    }
    for k, v in overlay.items():
        if k in ("cflags", "ldflags"):
            result[k] = list(result[k]) + list(v)
        elif k in ("env", "defines"):
            m = dict(result[k])
            for s, sv in v.items():
                if sv == "$delete":
                    m.pop(s, None)
                else:
                    m[s] = sv
            result[k] = m
        else:
            refuse("refuse: bad overlay")
    return result


def concrete(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "cflags": list(state["cflags"]),
        "ldflags": list(state["ldflags"]),
        "env": {k: v for k, v in state["env"].items() if v != "$delete"},
        "defines": {k: v for k, v in state["defines"].items() if v != "$delete"},
    }


def load_json(path: Path, refuse_msg: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        refuse(refuse_msg)


def resolve_config(config: Path) -> dict[str, Any]:
    project_path = config / "project.json"
    targets_path = config / "targets.json"

    if not project_path.is_file():
        refuse("refuse: bad project")
    project = check_settings(
        load_json(project_path, "refuse: bad project"), "refuse: bad project"
    )

    if not targets_path.is_file():
        refuse("refuse: bad targets")
    raw_targets = load_json(targets_path, "refuse: bad targets")
    if not isinstance(raw_targets, dict) or set(raw_targets.keys()) != {"targets"}:
        refuse("refuse: bad targets")
    if not isinstance(raw_targets["targets"], list):
        refuse("refuse: bad targets")

    seen_ids: set[str] = set()
    parsed: list[tuple[str, str, str, str, str | None, dict[str, Any] | None]] = []
    for elem in raw_targets["targets"]:
        if not isinstance(elem, dict):
            refuse("refuse: bad targets")
        required = {"id", "arch", "os", "linker"}
        keys = set(elem.keys())
        if not required.issubset(keys):
            refuse("refuse: bad targets")
        if keys - required - {"override", "toolchain"}:
            refuse("refuse: bad targets")
        tid = elem["id"]
        arch = elem["arch"]
        os_name = elem["os"]
        linker = elem["linker"]
        if not isinstance(tid, str) or ID_RE.fullmatch(tid) is None:
            refuse("refuse: bad targets")
        if tid in seen_ids:
            refuse("refuse: bad targets")
        seen_ids.add(tid)
        if not isinstance(arch, str) or arch not in ARCHES:
            refuse("refuse: bad targets")
        if not isinstance(os_name, str) or os_name not in OSES:
            refuse("refuse: bad targets")
        if not isinstance(linker, str) or linker not in LINKERS:
            refuse("refuse: bad targets")
        toolchain = elem.get("toolchain")
        if toolchain is not None and not isinstance(toolchain, str):
            refuse("refuse: bad targets")
        override = None
        if "override" in elem:
            override = check_settings(elem["override"], "refuse: bad targets")
        parsed.append((tid, arch, os_name, linker, toolchain, override))


    overlays_dir = config / "overlays"
    overlays: dict[str, dict[str, Any]] = {}
    if overlays_dir.exists():
        if not overlays_dir.is_dir():
            refuse("refuse: bad overlay")
        for p in sorted(overlays_dir.iterdir()):
            name = p.name
            if name not in ALLOWED_OVERLAY_NAMES or not p.is_file():
                refuse("refuse: bad overlay")
            overlays[name] = check_settings(
                load_json(p, "refuse: bad overlay"), "refuse: bad overlay"
            )

    toolchains_dir = config / "toolchains"
    toolchains: dict[str, dict[str, Any]] = {}
    if toolchains_dir.exists():
        if not toolchains_dir.is_dir():
            refuse("refuse: bad toolchain")
        for p in sorted(toolchains_dir.iterdir()):
            name = p.name
            if not name.endswith(".json") or not p.is_file():
                refuse("refuse: bad toolchain")
            stem = name[: -len(".json")]
            if ID_RE.fullmatch(stem) is None:
                refuse("refuse: bad toolchain")
            raw = load_json(p, "refuse: bad toolchain")
            if isinstance(raw, dict):
                raw = {k: v for k, v in raw.items() if k != "extends"}
            toolchains[stem] = check_settings(raw, "refuse: bad toolchain")

    artifacts: list[dict[str, Any]] = []
    for tid, arch, os_name, linker, toolchain, override in parsed:
        if (os_name, linker) not in LEGAL_PAIRS:
            refuse("refuse: unknown platform")

        state = deepcopy(project)
        os_file = f"{os_name}.json"
        if os_file in overlays:
            state = apply_overlay(state, overlays[os_file])
        arch_file = f"{os_name}_{arch}.json"
        if arch_file in overlays:
            state = apply_overlay(state, overlays[arch_file])
        state = apply_overlay(state, {"cflags": list(ARCH_INJECT[arch])})
        state = apply_overlay(
            state, {"ldflags": list(LINKER_PROFILES[(os_name, linker)])}
        )
        if toolchain is not None:
            if toolchain not in toolchains:
                refuse("refuse: bad toolchain")
            state = apply_overlay(state, toolchains[toolchain])
        if override is not None:
            state = apply_overlay(state, override)
        state = concrete(state)
        state["cflags"] = list(state["cflags"])
        state["ldflags"] = list(state["ldflags"])
        parts = ["cflags", *state["cflags"], "ldflags", *state["ldflags"], "env"]
        for k in sorted(state["env"]):
            parts.append(f"{k}={state['env'][k]}")
        parts.append("defines")
        for k in sorted(state["defines"]):
            parts.append(f"{k}={state['defines'][k]}")
        stamp = hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()

        artifacts.append(
            {
                "id": tid,
                "triple": f"{arch}-{os_name}-{linker}",
                "cflags": list(state["cflags"]),
                "ldflags": list(state["ldflags"]),
                "env": dict(state["env"]),
                "defines": dict(state["defines"]),
                "stamp": stamp,
            }
        )

    artifacts.sort(key=lambda a: a["id"])
    return {"artifacts": artifacts}


def emit(obj: dict[str, Any]) -> str:
    def sort_keys(x: Any) -> Any:
        if isinstance(x, dict):
            return {k: sort_keys(x[k]) for k in sorted(x)}
        if isinstance(x, list):
            return [sort_keys(i) for i in x]
        return x

    return json.dumps(sort_keys(obj), indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    result = resolve_config(Path(args.config))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(emit(result))


if __name__ == "__main__":
    main()
