"""Verifier for cross-platform-build-orchestrator.

Visible literals decide contested rules. Sealed configs under /tests/sealed
scale the same rules on inputs the agent never sees.
"""

from __future__ import annotations
import hashlib
import json
import os
import secrets
import shutil
import subprocess
from pathlib import Path
import pytest

APP = Path("/app")
SRC = APP / "src" / "orchestrator.py"
SEALED = Path("/tests/sealed")
WORK = Path("/work/out")
SANDBOX_UID = 12000
DROP = ["setpriv", f"--reuid={SANDBOX_UID}", f"--regid={SANDBOX_UID}", "--clear-groups"]
RUN_TIMEOUT = 20
_BASE_ENV = {
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "HOME": "/home/sandbox",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "PYTHONSAFEPATH": "1",
}
_OPAQUE: dict[Path, Path] = {}


def _opaque(path: Path) -> Path:
    """Map a descriptive test path to a random one under WORK of the same depth."""
    if path not in _OPAQUE:
        depth = len(path.relative_to(WORK).parts)
        parts = ["c" + secrets.token_hex(8)] + [f"d{i}" for i in range(1, depth - 1)]
        if depth > 1:
            parts.append("out.json")
        _OPAQUE[path] = WORK.joinpath(*parts)
    return _OPAQUE[path]


@pytest.fixture(autouse=True, scope="session")
def sandbox_boundary():
    """The sandbox user can neither read a sealed expect nor write the reward."""
    expect = next(SEALED.glob("*/expect.json"))
    probes = [
        f"open({str(expect)!r}).read()",
        "open('/logs/verifier/reward.txt','w').write('1\\n')",
    ]
    for code in probes:
        proc = subprocess.run(
            DROP + ["python3", "-c", code],
            capture_output=True,
            text=True,
            timeout=10,
            env=dict(_BASE_ENV),
        )
        assert proc.returncode != 0, code
    yield


def _reject_symlinks(root: Path) -> None:
    for p in [root, *root.rglob("*")]:
        if p.is_symlink():
            raise AssertionError(f"refusing symlink in staged tree: {p}")


def stage_config(files: dict[str, str | bytes], dest: Path) -> Path:
    dest = _opaque(dest)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for rel, body in files.items():
        p = dest / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(body, bytes):
            p.write_bytes(body)
        else:
            p.write_text(body if body.endswith("\n") else body + "\n")
    return dest


def stage_sealed(case: Path, dest: Path) -> Path:
    dest = _opaque(dest)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for name in ("project.json", "targets.json"):
        shutil.copy2(case / name, dest / name)
    for sub in ("overlays", "toolchains"):
        if (case / sub).is_dir():
            shutil.copytree(case / sub, dest / sub, symlinks=True)
    _reject_symlinks(dest)
    return dest


def run_orch(
    config: Path, out: Path, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    WORK.mkdir(parents=True, exist_ok=True)
    if out.exists() or out.is_symlink():
        out.unlink()
    run_env = dict(_BASE_ENV)
    if env:
        run_env.update(env)
    return subprocess.run(
        DROP + ["python3", str(SRC), "--config", str(config), "--out", str(out)],
        capture_output=True,
        timeout=RUN_TIMEOUT,
        env=run_env,
    )


def _sorted_pairs(pairs):
    keys = [k for k, _ in pairs]
    assert len(set(keys)) == len(keys), f"duplicate keys: {keys}"
    assert keys == sorted(
        keys, key=lambda k: k.encode("utf-8")
    ), f"keys not bytewise sorted: {keys}"
    return dict(pairs)


def _same_typed(a, b) -> bool:
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return list(a) == list(b) and all(_same_typed(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(_same_typed(x, y) for x, y in zip(a, b))
    return a == b


def assert_artifact_text(got: bytes, expected: str) -> None:
    text = got.decode("utf-8")
    assert "\r" not in text, "CR in output"
    assert text.endswith("\n") and not text.endswith(
        "\n\n"
    ), "output must end with exactly one LF"
    obj = json.loads(text, object_pairs_hook=_sorted_pairs)
    want = json.loads(expected)
    assert _same_typed(obj, want), (obj, want)
    layout = json.dumps(obj, indent=2, ensure_ascii=False).split("\n")
    lines = text[:-1].split("\n")
    assert len(lines) == len(
        layout
    ), "output is not laid out one member per line with two-space indentation"
    for got_line, ref_line in zip(lines, layout):
        indent = len(got_line) - len(got_line.lstrip(" "))
        assert indent == len(ref_line) - len(ref_line.lstrip(" ")), repr(got_line)
        assert not got_line[indent:].startswith("\t"), repr(got_line)


def assert_success_bytes(config: Path, expected: str, out: Path, env=None) -> None:
    out = _opaque(out)
    proc = run_orch(config, out, env=env)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == b""
    assert not out.is_symlink(), "output must not be a symlink"
    assert out.is_file()
    assert_artifact_text(out.read_bytes(), expected)


def assert_refuse(config: Path, msg: str, out: Path, env=None) -> None:
    out = _opaque(out)
    proc = run_orch(config, out, env=env)
    assert proc.returncode == 2, (proc.returncode, proc.stderr)
    assert proc.stdout == b""
    assert proc.stderr == (msg + "\n").encode("utf-8")
    assert not out.exists() and (not out.is_symlink())


def _seed_out(out: Path, data: bytes) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    for d in [out.parent, *out.parent.parents]:
        if d == WORK.parent:
            break
        os.chown(d, SANDBOX_UID, SANDBOX_UID)
    out.write_bytes(data)
    os.chown(out, SANDBOX_UID, SANDBOX_UID)


def _run_seeded(config: Path, out: Path, data: bytes, env=None):
    WORK.mkdir(parents=True, exist_ok=True)
    _seed_out(out, data)
    run_env = dict(_BASE_ENV)
    if env:
        run_env.update(env)
    return subprocess.run(
        DROP + ["python3", str(SRC), "--config", str(config), "--out", str(out)],
        capture_output=True,
        timeout=RUN_TIMEOUT,
        env=run_env,
    )


def assert_refuse_keeps_existing(
    config: Path, msg: str, out: Path, data: bytes
) -> None:
    out = _opaque(out)
    proc = _run_seeded(config, out, data)
    assert proc.returncode == 2, (proc.returncode, proc.stderr)
    assert proc.stdout == b""
    assert proc.stderr == (msg + "\n").encode("utf-8")
    assert out.is_file() and not out.is_symlink()
    assert out.read_bytes() == data


def assert_success_over_existing(
    config: Path, expected: str, out: Path, data: bytes
) -> None:
    out = _opaque(out)
    proc = _run_seeded(config, out, data)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == b""
    assert out.is_file() and not out.is_symlink()
    assert_artifact_text(out.read_bytes(), expected)


def stamp_of(
    cflags: list, ldflags: list, env: dict, defines: dict, used_toolchain: bool = False
) -> str:
    parts = ["cflags", *cflags, "ldflags", *ldflags, "env"]
    for k in sorted(env):
        parts.append(f"{k}={env[k]}")
    parts.append("defines")
    for k in sorted(defines):
        parts.append(f"{k}={defines[k]}")
    parts.append("tc" if used_toolchain else "notc")
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()


def art(*artifacts: dict, used_toolchain: bool = False) -> str:
    enriched = []
    for a in artifacts:
        item = dict(a)
        tc = bool(item.pop("_tc", used_toolchain))
        if "stamp" not in item:
            item["stamp"] = stamp_of(
                item["cflags"], item["ldflags"], item["env"], item["defines"], tc
            )
        enriched.append(item)
    obj = {"artifacts": enriched}
    return json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def tgt(
    tid, arch="x86_64", os_name="linux", linker="bfd", override=None, toolchain=None
):
    o = {"id": tid, "arch": arch, "os": os_name, "linker": linker}
    if override is not None:
        o["override"] = override
    if toolchain is not None:
        o["toolchain"] = toolchain
    return o


def test_source_present():
    """Drive: the program to grade exists at /app/src/orchestrator.py."""
    assert SRC.is_file()


def test_merge_os_cflags_concat():
    """Apply/Resolution 2: the linux overlay's cflags are appended after project cflags and before the arch inject."""
    cfg = stage_config(
        {
            "project.json": '{"cflags": ["-O2"]}',
            "targets.json": json.dumps({"targets": [tgt("vis_a")]}),
            "overlays/linux.json": '{"cflags": ["-pthread"]}',
        },
        WORK / "m_concat_a",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-O2", "-pthread", "-m64"],
                "defines": {},
                "env": {},
                "id": "vis_a",
                "ldflags": ["-Wl,--hash-style=gnu"],
                "triple": "x86_64-linux-bfd",
            }
        ),
        WORK / "nested_a" / "out.json",
    )


def test_out_creates_nested_parents():
    """Output: missing parent directories of --out are created (two levels)."""
    cfg = stage_config(
        {"project.json": "{}", "targets.json": json.dumps({"targets": [tgt("nest")]})},
        WORK / "m_deep_cfg",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-m64"],
                "defines": {},
                "env": {},
                "id": "nest",
                "ldflags": ["-Wl,--hash-style=gnu"],
                "triple": "x86_64-linux-bfd",
            }
        ),
        WORK / "deep" / "child" / "out.json",
    )


def test_out_creates_three_level_parents():
    """Output: missing parent directories of --out are created (three levels)."""
    cfg = stage_config(
        {"project.json": "{}", "targets.json": json.dumps({"targets": [tgt("nest3")]})},
        WORK / "m_deep3_cfg",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-m64"],
                "defines": {},
                "env": {},
                "id": "nest3",
                "ldflags": ["-Wl,--hash-style=gnu"],
                "triple": "x86_64-linux-bfd",
            }
        ),
        WORK / "deep2" / "mid" / "leaf" / "out.json",
    )


def test_merge_os_ldflags_concat():
    """Apply/Resolution 2: the linux overlay's ldflags follow project ldflags and precede the linker profile."""
    cfg = stage_config(
        {
            "project.json": '{"ldflags": ["-L/opt"]}',
            "targets.json": json.dumps({"targets": [tgt("vis_b")]}),
            "overlays/linux.json": '{"ldflags": ["-Wl,-z,now"]}',
        },
        WORK / "m_concat_b",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-m64"],
                "defines": {},
                "env": {},
                "id": "vis_b",
                "ldflags": ["-L/opt", "-Wl,-z,now", "-Wl,--hash-style=gnu"],
                "triple": "x86_64-linux-bfd",
            }
        ),
        WORK / "nested_b" / "out.json",
    )


def test_merge_arch_ldflags_concat():
    """Apply/Resolution 3: the <os>_<arch> overlay's ldflags follow project ldflags and precede the lld profile."""
    cfg = stage_config(
        {
            "project.json": '{"ldflags": ["-Lbase"]}',
            "targets.json": json.dumps({"targets": [tgt("al", linker="lld")]}),
            "overlays/linux_x86_64.json": '{"ldflags": ["-Larch"]}',
        },
        WORK / "m_arch_ld",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-m64"],
                "defines": {},
                "env": {},
                "id": "al",
                "ldflags": ["-Lbase", "-Larch", "-fuse-ld=lld"],
                "triple": "x86_64-linux-lld",
            }
        ),
        WORK / "m_arch_ld.json",
    )


def test_merge_override_cflags_after_profile():
    """Resolution 8: override cflags and ldflags are appended after the arch inject and linker profile."""
    cfg = stage_config(
        {
            "project.json": '{"cflags": ["-O"], "ldflags": ["-Lx"]}',
            "targets.json": json.dumps(
                {
                    "targets": [
                        tgt("ov", override={"cflags": ["-g"], "ldflags": ["-ly"]})
                    ]
                }
            ),
        },
        WORK / "m_ov_flags",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-O", "-m64", "-g"],
                "defines": {},
                "env": {},
                "id": "ov",
                "ldflags": ["-Lx", "-Wl,--hash-style=gnu", "-ly"],
                "triple": "x86_64-linux-bfd",
            }
        ),
        WORK / "m_ov_flags.json",
    )


def test_merge_overlay_then_override():
    """Resolution 2 then 8: an os overlay's cflags and an override's env both apply to one target."""
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps(
                {"targets": [tgt("a", override={"env": {"K": "v"}})]}
            ),
            "overlays/linux.json": '{"cflags": ["-DOS"]}',
        },
        WORK / "m_ov_both",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-DOS", "-m64"],
                "defines": {},
                "env": {"K": "v"},
                "id": "a",
                "ldflags": ["-Wl,--hash-style=gnu"],
                "triple": "x86_64-linux-bfd",
            }
        ),
        WORK / "m_ov_both.json",
    )


def test_merge_arch_overlay_after_os():
    """Resolution 2-3: the <os>_<arch> overlay is Applied after the <os> overlay, so its cflags come later."""
    cfg = stage_config(
        {
            "project.json": '{"cflags": ["-O2"]}',
            "targets.json": json.dumps(
                {"targets": [tgt("arm1", arch="aarch64", linker="lld")]}
            ),
            "overlays/linux.json": '{"cflags": ["-pthread"]}',
            "overlays/linux_aarch64.json": '{"cflags": ["-march=armv8-a"]}',
        },
        WORK / "m_arch_a",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-O2", "-pthread", "-march=armv8-a"],
                "defines": {},
                "env": {},
                "id": "arm1",
                "ldflags": ["-fuse-ld=lld"],
                "triple": "aarch64-linux-lld",
            }
        ),
        WORK / "m_arch_a.json",
    )


def test_merge_darwin_os_overlay():
    """Overlays: darwin.json applies to a darwin target, ahead of the x86_64 inject and ld64 profile."""
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps(
                {"targets": [tgt("dar_ov", os_name="darwin", linker="ld64")]}
            ),
            "overlays/darwin.json": '{"cflags": ["-mmacosx-version-min=11.0"]}',
        },
        WORK / "m_dar_ov",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-mmacosx-version-min=11.0", "-m64"],
                "defines": {},
                "env": {},
                "id": "dar_ov",
                "ldflags": ["-Wl,-dead_strip"],
                "triple": "x86_64-darwin-ld64",
            }
        ),
        WORK / "m_dar_ov.json",
    )


def test_merge_windows_os_overlay():
    """Overlays: windows.json applies to a windows target, ahead of the inject and link profile."""
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps(
                {"targets": [tgt("win_ov", os_name="windows", linker="link")]}
            ),
            "overlays/windows.json": '{"cflags": ["-DWIN"]}',
        },
        WORK / "m_win_ov",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-DWIN", "-m64"],
                "defines": {},
                "env": {},
                "id": "win_ov",
                "ldflags": ["/INCREMENTAL:NO"],
                "triple": "x86_64-windows-link",
            }
        ),
        WORK / "m_win_ov.json",
    )


def test_merge_darwin_arch_overlay():
    """Overlays: darwin_aarch64.json applies alone (no darwin.json) before the aarch64 inject."""
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps(
                {
                    "targets": [
                        tgt("dar_arch", arch="aarch64", os_name="darwin", linker="ld64")
                    ]
                }
            ),
            "overlays/darwin_aarch64.json": '{"cflags": ["-DARM"]}',
        },
        WORK / "m_dar_arch",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-DARM", "-march=armv8-a"],
                "defines": {},
                "env": {},
                "id": "dar_arch",
                "ldflags": ["-Wl,-dead_strip"],
                "triple": "aarch64-darwin-ld64",
            }
        ),
        WORK / "m_dar_arch.json",
    )


def test_merge_windows_arch_overlay_env():
    """Overlays: windows_x86_64.json contributes cflags, ldflags and env to a windows/link target."""
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps(
                {"targets": [tgt("win_arch", os_name="windows", linker="link")]}
            ),
            "overlays/windows_x86_64.json": '{"cflags":["-DARCH"],"ldflags":["-Larch"],"env":{"ARCH":"yes"}}',
        },
        WORK / "m_win_arch",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-DARCH", "-m64"],
                "defines": {},
                "env": {"ARCH": "yes"},
                "id": "win_arch",
                "ldflags": ["-Larch", "/INCREMENTAL:NO"],
                "triple": "x86_64-windows-link",
            }
        ),
        WORK / "m_win_arch.json",
    )


def test_profile_mold():
    """Linker profiles: linux/mold appends -fuse-ld=mold after existing project ldflags."""
    cfg = stage_config(
        {
            "project.json": '{"ldflags": ["-Wl,-z,relro"]}',
            "targets.json": json.dumps({"targets": [tgt("mold1", linker="mold")]}),
        },
        WORK / "p_mold",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-m64"],
                "defines": {},
                "env": {},
                "id": "mold1",
                "ldflags": ["-Wl,-z,relro", "-fuse-ld=mold"],
                "triple": "x86_64-linux-mold",
            }
        ),
        WORK / "p_mold.json",
    )


def test_profile_darwin_lld():
    """Linker profiles: darwin/lld on x86_64 gets -fuse-ld=lld and no Profile extra."""
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps(
                {"targets": [tgt("dlld", os_name="darwin", linker="lld")]}
            ),
        },
        WORK / "p_dlld",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-m64"],
                "defines": {},
                "env": {},
                "id": "dlld",
                "ldflags": ["-fuse-ld=lld"],
                "triple": "x86_64-darwin-lld",
            }
        ),
        WORK / "p_dlld.json",
    )


def test_profile_windows_lld():
    """Linker profiles: windows/lld gets -fuse-ld=lld; project cflags precede the inject."""
    cfg = stage_config(
        {
            "project.json": '{"cflags": ["-O1"]}',
            "targets.json": json.dumps(
                {"targets": [tgt("wlld", os_name="windows", linker="lld")]}
            ),
        },
        WORK / "p_wlld",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-O1", "-m64"],
                "defines": {},
                "env": {},
                "id": "wlld",
                "ldflags": ["-fuse-ld=lld"],
                "triple": "x86_64-windows-lld",
            }
        ),
        WORK / "p_wlld.json",
    )


def test_profile_darwin_ld64():
    """Linker profiles: darwin/ld64 gets -Wl,-dead_strip."""
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps(
                {"targets": [tgt("dar1", os_name="darwin", linker="ld64")]}
            ),
        },
        WORK / "p_dld64",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-m64"],
                "defines": {},
                "env": {},
                "id": "dar1",
                "ldflags": ["-Wl,-dead_strip"],
                "triple": "x86_64-darwin-ld64",
            }
        ),
        WORK / "p_dld64.json",
    )


def test_profile_darwin_ld64_appends_existing():
    """Linker profiles: the darwin/ld64 profile is appended after existing project ldflags."""
    cfg = stage_config(
        {
            "project.json": '{"ldflags": ["-Lbase"]}',
            "targets.json": json.dumps(
                {"targets": [tgt("dar_ap", os_name="darwin", linker="ld64")]}
            ),
        },
        WORK / "p_dar_ap",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-m64"],
                "defines": {},
                "env": {},
                "id": "dar_ap",
                "ldflags": ["-Lbase", "-Wl,-dead_strip"],
                "triple": "x86_64-darwin-ld64",
            }
        ),
        WORK / "p_dar_ap.json",
    )


def test_profile_darwin_preserves_env_defines():
    """Linker profiles contribute only ldflags: darwin/ld64 keeps populated env and defines."""
    cfg = stage_config(
        {
            "project.json": '{"env": {"A": "1"}, "defines": {"D": "1"}}',
            "targets.json": json.dumps(
                {"targets": [tgt("a", os_name="darwin", linker="ld64")]}
            ),
        },
        WORK / "p_dar_maps",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-m64"],
                "defines": {"D": "1"},
                "env": {"A": "1"},
                "id": "a",
                "ldflags": ["-Wl,-dead_strip"],
                "triple": "x86_64-darwin-ld64",
            }
        ),
        WORK / "p_dar_maps.json",
    )


def test_profile_darwin_lld_preserves_env_defines():
    """Profile and extra contribute only ldflags: darwin/lld/aarch64 keeps populated env and defines."""
    cfg = stage_config(
        {
            "project.json": '{"env": {"B": "2"}, "defines": {"E": "2"}}',
            "targets.json": json.dumps(
                {
                    "targets": [
                        tgt("dll", arch="aarch64", os_name="darwin", linker="lld")
                    ]
                }
            ),
        },
        WORK / "p_dar_lld_maps",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-march=armv8-a"],
                "defines": {"E": "2"},
                "env": {"B": "2"},
                "id": "dll",
                "ldflags": ["-fuse-ld=lld", "-Wl,-dead_strip_dylibs"],
                "triple": "aarch64-darwin-lld",
            }
        ),
        WORK / "p_dar_lld_maps.json",
    )


def test_profile_windows_link_appends_existing():
    """Linker profiles: windows/link appends /INCREMENTAL:NO after existing project ldflags."""
    cfg = stage_config(
        {
            "project.json": '{"ldflags": ["-Lbase"]}',
            "targets.json": json.dumps(
                {"targets": [tgt("win_ap", os_name="windows", linker="link")]}
            ),
        },
        WORK / "p_win_ap",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-m64"],
                "defines": {},
                "env": {},
                "id": "win_ap",
                "ldflags": ["-Lbase", "/INCREMENTAL:NO"],
                "triple": "x86_64-windows-link",
            }
        ),
        WORK / "p_win_ap.json",
    )


def test_host_no_cross_inject():
    """Host isolation: CROSS_HOST_INJECT in the process environment adds nothing to graded fields."""
    cfg = stage_config(
        {
            "project.json": '{"env": {"PROJECT": "p"}}',
            "targets.json": json.dumps({"targets": [tgt("h1")]}),
        },
        WORK / "h_a",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-m64"],
                "defines": {},
                "env": {"PROJECT": "p"},
                "id": "h1",
                "ldflags": ["-Wl,--hash-style=gnu"],
                "triple": "x86_64-linux-bfd",
            }
        ),
        WORK / "h_a.json",
        env={"CROSS_HOST_INJECT": "leak"},
    )


def test_host_no_cc():
    """Host isolation: CC in the process environment adds nothing to env or flags."""
    cfg = stage_config(
        {"project.json": "{}", "targets.json": json.dumps({"targets": [tgt("h2")]})},
        WORK / "h_b",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-m64"],
                "defines": {},
                "env": {},
                "id": "h2",
                "ldflags": ["-Wl,--hash-style=gnu"],
                "triple": "x86_64-linux-bfd",
            }
        ),
        WORK / "h_b.json",
        env={"CC": "host-compiler"},
    )


def test_host_no_cflags_env():
    """Host isolation: CFLAGS in the process environment is not appended to cflags."""
    cfg = stage_config(
        {"project.json": "{}", "targets.json": json.dumps({"targets": [tgt("h3")]})},
        WORK / "h_c",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-m64"],
                "defines": {},
                "env": {},
                "id": "h3",
                "ldflags": ["-Wl,--hash-style=gnu"],
                "triple": "x86_64-linux-bfd",
            }
        ),
        WORK / "h_c.json",
        env={"CFLAGS": "-march=native"},
    )


def test_host_no_build_host_triple():
    """Host isolation: BUILD_HOST_TRIPLE does not replace the triple derived from the target."""
    cfg = stage_config(
        {"project.json": "{}", "targets.json": json.dumps({"targets": [tgt("h4")]})},
        WORK / "h_d",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-m64"],
                "defines": {},
                "env": {},
                "id": "h4",
                "ldflags": ["-Wl,--hash-style=gnu"],
                "triple": "x86_64-linux-bfd",
            }
        ),
        WORK / "h_d.json",
        env={"BUILD_HOST_TRIPLE": "leak"},
    )


def test_host_no_sdkroot_on_darwin():
    """Host isolation: SDKROOT adds nothing to a darwin target's ldflags or env."""
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps(
                {"targets": [tgt("h5", os_name="darwin", linker="ld64")]}
            ),
        },
        WORK / "h_e",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-m64"],
                "defines": {},
                "env": {},
                "id": "h5",
                "ldflags": ["-Wl,-dead_strip"],
                "triple": "x86_64-darwin-ld64",
            }
        ),
        WORK / "h_e.json",
        env={"SDKROOT": "/sdk"},
    )


def test_delete_override_env():
    """Apply: an override env value of exactly $delete removes the project key and keeps the rest."""
    cfg = stage_config(
        {
            "project.json": '{"env": {"KEEP": "1", "DROP": "x"}}',
            "targets.json": json.dumps(
                {"targets": [tgt("del1", override={"env": {"DROP": "$delete"}})]}
            ),
        },
        WORK / "d_a",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-m64"],
                "defines": {},
                "env": {"KEEP": "1"},
                "id": "del1",
                "ldflags": ["-Wl,--hash-style=gnu"],
                "triple": "x86_64-linux-bfd",
            }
        ),
        WORK / "d_a.json",
    )


def test_delete_overlay_defines():
    """Apply: an overlay defines value of exactly $delete removes the project key and keeps the rest."""
    cfg = stage_config(
        {
            "project.json": '{"defines": {"LIVE": "1", "GONE": "1"}}',
            "targets.json": json.dumps({"targets": [tgt("del2")]}),
            "overlays/linux.json": '{"defines": {"GONE": "$delete"}}',
        },
        WORK / "d_b",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-m64"],
                "defines": {"LIVE": "1"},
                "env": {},
                "id": "del2",
                "ldflags": ["-Wl,--hash-style=gnu"],
                "triple": "x86_64-linux-bfd",
            }
        ),
        WORK / "d_b.json",
    )


def test_delete_override_defines():
    """Apply: an override defines value of exactly $delete removes the project key and keeps the rest."""
    cfg = stage_config(
        {
            "project.json": '{"defines": {"LIVE": "1", "DROP": "x"}}',
            "targets.json": json.dumps(
                {"targets": [tgt("del3", override={"defines": {"DROP": "$delete"}})]}
            ),
        },
        WORK / "d_c",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-m64"],
                "defines": {"LIVE": "1"},
                "env": {},
                "id": "del3",
                "ldflags": ["-Wl,--hash-style=gnu"],
                "triple": "x86_64-linux-bfd",
            }
        ),
        WORK / "d_c.json",
    )


def test_override_defines_add():
    """Apply: an override defines entry is added when the project has no defines."""
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps(
                {"targets": [tgt("odadd", override={"defines": {"NEW": "1"}})]}
            ),
        },
        WORK / "d_odadd",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-m64"],
                "defines": {"NEW": "1"},
                "env": {},
                "id": "odadd",
                "ldflags": ["-Wl,--hash-style=gnu"],
                "triple": "x86_64-linux-bfd",
            }
        ),
        WORK / "d_odadd.json",
    )


def test_project_delete_concreted():
    """Concrete: $delete values that arrive from the project itself are removed from env and defines."""
    cfg = stage_config(
        {
            "project.json": '{"env": {"GONE": "$delete"}, "defines": {"D": "$delete"}}',
            "targets.json": json.dumps({"targets": [tgt("pc")]}),
        },
        WORK / "d_proj",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-m64"],
                "defines": {},
                "env": {},
                "id": "pc",
                "ldflags": ["-Wl,--hash-style=gnu"],
                "triple": "x86_64-linux-bfd",
            }
        ),
        WORK / "d_proj.json",
    )


def test_delete_absent_noop():
    """Apply: a $delete for a key that is absent is a no-op and leaves no entry behind."""
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps({"targets": [tgt("abs")]}),
            "overlays/linux.json": '{"env": {"MISSING": "$delete"}, "defines": {"ALSO": "$delete"}}',
        },
        WORK / "d_abs",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-m64"],
                "defines": {},
                "env": {},
                "id": "abs",
                "ldflags": ["-Wl,--hash-style=gnu"],
                "triple": "x86_64-linux-bfd",
            }
        ),
        WORK / "d_abs.json",
    )


def test_overlay_env_overwrite_and_empty_base():
    """Apply: an overlay env value replaces the project's value; new keys are added and flags appended."""
    cfg = stage_config(
        {
            "project.json": '{"env": {"MODE": "project"}}',
            "targets.json": json.dumps({"targets": [tgt("ow")]}),
            "overlays/linux.json": '{"cflags": ["-g"], "ldflags": ["-L/x"], "env": {"A": "new", "MODE": "overlay"}}',
        },
        WORK / "m_ow",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-g", "-m64"],
                "defines": {},
                "env": {"A": "new", "MODE": "overlay"},
                "id": "ow",
                "ldflags": ["-L/x", "-Wl,--hash-style=gnu"],
                "triple": "x86_64-linux-bfd",
            }
        ),
        WORK / "m_ow.json",
    )


def test_overlay_env_into_absent_project_env():
    """Apply: an overlay env entry is added when the project has no env key at all."""
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps({"targets": [tgt("eabs")]}),
            "overlays/linux.json": '{"env": {"A": "1"}}',
        },
        WORK / "m_eabs",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-m64"],
                "defines": {},
                "env": {"A": "1"},
                "id": "eabs",
                "ldflags": ["-Wl,--hash-style=gnu"],
                "triple": "x86_64-linux-bfd",
            }
        ),
        WORK / "m_eabs.json",
    )


def test_defines_overlay_replaces_existing():
    """Apply: an overlay defines value replaces the project's value for the same key."""
    cfg = stage_config(
        {
            "project.json": '{"defines": {"D": "1"}}',
            "targets.json": json.dumps({"targets": [tgt("drep")]}),
            "overlays/linux.json": '{"defines": {"D": "2"}}',
        },
        WORK / "m_drep",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-m64"],
                "defines": {"D": "2"},
                "env": {},
                "id": "drep",
                "ldflags": ["-Wl,--hash-style=gnu"],
                "triple": "x86_64-linux-bfd",
            }
        ),
        WORK / "m_drep.json",
    )


def test_near_delete_survives_concrete():
    """Concrete: project values $delete-plus-space, $deleteX and x$delete are not the sentinel and are kept."""
    cfg = stage_config(
        {
            "project.json": json.dumps(
                {
                    "env": {"KEEP": "$delete ", "E": "$deleteX", "K": "x$delete"},
                    "defines": {"D": "$deleteX"},
                }
            ),
            "targets.json": json.dumps({"targets": [tgt("near")]}),
        },
        WORK / "d_near",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-m64"],
                "defines": {"D": "$deleteX"},
                "env": {"E": "$deleteX", "K": "x$delete", "KEEP": "$delete "},
                "id": "near",
                "ldflags": ["-Wl,--hash-style=gnu"],
                "triple": "x86_64-linux-bfd",
            }
        ),
        WORK / "d_near.json",
    )


def test_near_delete_survives_apply_overlay():
    """Apply: overlay values that only resemble $delete set the key rather than removing it."""
    cfg = stage_config(
        {
            "project.json": '{"env": {"A": "old"}}',
            "targets.json": json.dumps({"targets": [tgt("nap")]}),
            "overlays/linux.json": '{"env": {"A": "$deleteX", "KEEP": "$deleteX", "SP": "$delete "}}',
        },
        WORK / "d_nap",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-m64"],
                "defines": {},
                "env": {"A": "$deleteX", "KEEP": "$deleteX", "SP": "$delete "},
                "id": "nap",
                "ldflags": ["-Wl,--hash-style=gnu"],
                "triple": "x86_64-linux-bfd",
            }
        ),
        WORK / "d_nap.json",
    )


def test_nested_keys_sorted():
    """Output: env and defines keys are written in ascending bytewise order."""
    cfg = stage_config(
        {
            "project.json": json.dumps(
                {"env": {"Z": "z", "A": "a"}, "defines": {"Z": "z", "A": "a"}}
            ),
            "targets.json": json.dumps({"targets": [tgt("sk")]}),
        },
        WORK / "m_sortk",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-m64"],
                "defines": {"A": "a", "Z": "z"},
                "env": {"A": "a", "Z": "z"},
                "id": "sk",
                "ldflags": ["-Wl,--hash-style=gnu"],
                "triple": "x86_64-linux-bfd",
            }
        ),
        WORK / "m_sortk.json",
    )


def test_targets_independent():
    """Resolution: one target's override env does not reach another target."""
    cfg = stage_config(
        {
            "project.json": '{"env": {"BASE": "1"}}',
            "targets.json": json.dumps(
                {
                    "targets": [
                        tgt("second", override={"env": {"FIRST": "1"}}),
                        tgt("first"),
                    ]
                }
            ),
        },
        WORK / "ind",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-m64"],
                "defines": {},
                "env": {"BASE": "1"},
                "id": "first",
                "ldflags": ["-Wl,--hash-style=gnu"],
                "triple": "x86_64-linux-bfd",
            },
            {
                "cflags": ["-m64"],
                "defines": {},
                "env": {"BASE": "1", "FIRST": "1"},
                "id": "second",
                "ldflags": ["-Wl,--hash-style=gnu"],
                "triple": "x86_64-linux-bfd",
            },
        ),
        WORK / "ind.json",
    )


def test_targets_independent_flag_arrays():
    """Resolution: an arch overlay for one target's arch does not reach a target of another arch."""
    cfg = stage_config(
        {
            "project.json": '{"cflags": ["-O"]}',
            "targets.json": json.dumps(
                {"targets": [tgt("a_arm", arch="aarch64"), tgt("b_x64")]}
            ),
            "overlays/linux_aarch64.json": '{"cflags": ["-march=armv8-a"]}',
        },
        WORK / "ind_flags",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-O", "-march=armv8-a"],
                "defines": {},
                "env": {},
                "id": "a_arm",
                "ldflags": ["-Wl,--hash-style=gnu"],
                "triple": "aarch64-linux-bfd",
            },
            {
                "cflags": ["-O", "-m64"],
                "defines": {},
                "env": {},
                "id": "b_x64",
                "ldflags": ["-Wl,--hash-style=gnu"],
                "triple": "x86_64-linux-bfd",
            },
        ),
        WORK / "ind_flags.json",
    )


def test_targets_independent_cflags_override():
    """Resolution: one target's override cflags do not reach the next target."""
    cfg = stage_config(
        {
            "project.json": '{"cflags": ["-O"]}',
            "targets.json": json.dumps(
                {"targets": [tgt("a", override={"cflags": ["-DA"]}), tgt("b")]}
            ),
        },
        WORK / "ind_cflags",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-O", "-m64", "-DA"],
                "defines": {},
                "env": {},
                "id": "a",
                "ldflags": ["-Wl,--hash-style=gnu"],
                "triple": "x86_64-linux-bfd",
            },
            {
                "cflags": ["-O", "-m64"],
                "defines": {},
                "env": {},
                "id": "b",
                "ldflags": ["-Wl,--hash-style=gnu"],
                "triple": "x86_64-linux-bfd",
            },
        ),
        WORK / "ind_cflags.json",
    )


def test_targets_independent_cflags_override_mid():
    """Resolution: an override on the first-listed target leaves two later targets untouched."""
    cfg = stage_config(
        {
            "project.json": '{"cflags": ["-O2"]}',
            "targets.json": json.dumps(
                {
                    "targets": [
                        tgt("mid", override={"cflags": ["-DMID"]}),
                        tgt("later"),
                        tgt("zlast"),
                    ]
                }
            ),
        },
        WORK / "ind_cflags_mid",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-O2", "-m64"],
                "defines": {},
                "env": {},
                "id": "later",
                "ldflags": ["-Wl,--hash-style=gnu"],
                "triple": "x86_64-linux-bfd",
            },
            {
                "cflags": ["-O2", "-m64", "-DMID"],
                "defines": {},
                "env": {},
                "id": "mid",
                "ldflags": ["-Wl,--hash-style=gnu"],
                "triple": "x86_64-linux-bfd",
            },
            {
                "cflags": ["-O2", "-m64"],
                "defines": {},
                "env": {},
                "id": "zlast",
                "ldflags": ["-Wl,--hash-style=gnu"],
                "triple": "x86_64-linux-bfd",
            },
        ),
        WORK / "ind_cflags_mid.json",
    )


def test_targets_independent_env_presorted():
    """Resolution: an override env on one target leaves another target's env empty."""
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps(
                {
                    "targets": [
                        tgt("alpha", override={"env": {"LEAK": "1"}}),
                        tgt("zulu"),
                    ]
                }
            ),
        },
        WORK / "ind_env",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-m64"],
                "defines": {},
                "env": {"LEAK": "1"},
                "id": "alpha",
                "ldflags": ["-Wl,--hash-style=gnu"],
                "triple": "x86_64-linux-bfd",
            },
            {
                "cflags": ["-m64"],
                "defines": {},
                "env": {},
                "id": "zulu",
                "ldflags": ["-Wl,--hash-style=gnu"],
                "triple": "x86_64-linux-bfd",
            },
        ),
        WORK / "ind_env.json",
    )


def test_targets_independent_defines():
    """Resolution: an override defines on one target leaves another target's defines empty."""
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps(
                {
                    "targets": [
                        tgt("alpha", override={"defines": {"K": "1"}}),
                        tgt("zulu"),
                    ]
                }
            ),
        },
        WORK / "ind_def",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-m64"],
                "defines": {"K": "1"},
                "env": {},
                "id": "alpha",
                "ldflags": ["-Wl,--hash-style=gnu"],
                "triple": "x86_64-linux-bfd",
            },
            {
                "cflags": ["-m64"],
                "defines": {},
                "env": {},
                "id": "zulu",
                "ldflags": ["-Wl,--hash-style=gnu"],
                "triple": "x86_64-linux-bfd",
            },
        ),
        WORK / "ind_def.json",
    )


def test_id_length_boundary_accepted():
    """Targets: a 32-character id, the pattern's upper bound, is accepted."""
    tid = "a" + "_" * 31
    assert len(tid) == 32
    cfg = stage_config(
        {"project.json": "{}", "targets.json": json.dumps({"targets": [tgt(tid)]})},
        WORK / "id32",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-m64"],
                "defines": {},
                "env": {},
                "id": "a_______________________________",
                "ldflags": ["-Wl,--hash-style=gnu"],
                "triple": "x86_64-linux-bfd",
            }
        ),
        WORK / "id32.json",
    )


def test_artifacts_sorted_by_id():
    """Output: artifacts are sorted by id, not by their order in targets.json."""
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps(
                {
                    "targets": [
                        tgt("zulu", os_name="windows", linker="link"),
                        tgt("alpha"),
                    ]
                }
            ),
        },
        WORK / "sort",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-m64"],
                "defines": {},
                "env": {},
                "id": "alpha",
                "ldflags": ["-Wl,--hash-style=gnu"],
                "triple": "x86_64-linux-bfd",
            },
            {
                "cflags": ["-m64"],
                "defines": {},
                "env": {},
                "id": "zulu",
                "ldflags": ["/INCREMENTAL:NO"],
                "triple": "x86_64-windows-link",
            },
        ),
        WORK / "sort.json",
    )


def test_eight_targets_all_emitted():
    """Limits: eight targets, the stated maximum, each produce their own artifact."""
    targets = [tgt(f"t{i}", override={"env": {f"K{i}": str(i)}}) for i in range(8)]
    expected = art(
        *[
            {
                "cflags": ["-m64"],
                "defines": {},
                "env": {f"K{i}": str(i)},
                "id": f"t{i}",
                "ldflags": ["-Wl,--hash-style=gnu"],
                "triple": "x86_64-linux-bfd",
            }
            for i in range(8)
        ]
    )
    cfg = stage_config(
        {"project.json": "{}", "targets.json": json.dumps({"targets": targets})},
        WORK / "eight",
    )
    assert_success_bytes(cfg, expected, WORK / "eight.json")


def test_four_targets_independent_overrides():
    """Resolution: an override on the fourth target does not leak to the first three."""
    expected = art(
        {
            "cflags": ["-m64"],
            "defines": {},
            "env": {},
            "id": "a",
            "ldflags": ["-Wl,--hash-style=gnu"],
            "triple": "x86_64-linux-bfd",
        },
        {
            "cflags": ["-m64"],
            "defines": {},
            "env": {},
            "id": "b",
            "ldflags": ["-Wl,--hash-style=gnu"],
            "triple": "x86_64-linux-bfd",
        },
        {
            "cflags": ["-m64"],
            "defines": {},
            "env": {},
            "id": "c",
            "ldflags": ["-Wl,--hash-style=gnu"],
            "triple": "x86_64-linux-bfd",
        },
        {
            "cflags": ["-m64"],
            "defines": {},
            "env": {"FOURTH": "1"},
            "id": "d",
            "ldflags": ["-Wl,--hash-style=gnu"],
            "triple": "x86_64-linux-bfd",
        },
    )
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps(
                {
                    "targets": [
                        tgt("a"),
                        tgt("b"),
                        tgt("c"),
                        tgt("d", override={"env": {"FOURTH": "1"}}),
                    ]
                }
            ),
        },
        WORK / "four_ind",
    )
    assert_success_bytes(cfg, expected, WORK / "four_ind.json")


def test_r_unknown_linux_ld64():
    """Refusals: the illegal pair linux/ld64 refuses with unknown platform."""
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps({"targets": [tgt("bad1", linker="ld64")]}),
        },
        WORK / "qx7m2",
    )
    assert_refuse(cfg, "refuse: unknown platform", WORK / "qx7m2.out")


def test_r_unknown_windows_bfd():
    """Refusals: the illegal pair windows/bfd refuses with unknown platform."""
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps(
                {"targets": [tgt("bad2", os_name="windows", linker="bfd")]}
            ),
        },
        WORK / "k9v4p",
    )
    assert_refuse(cfg, "refuse: unknown platform", WORK / "k9v4p.out")


def test_r_unknown_after_valid_leaves_no_out():
    """Refusals: an illegal pair on a later target refuses and leaves no output from the earlier one."""
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps(
                {"targets": [tgt("a_ok"), tgt("z_bad", linker="ld64")]}
            ),
        },
        WORK / "w3n8c",
    )
    assert_refuse(cfg, "refuse: unknown platform", WORK / "w3n8c.out")


def test_r_project_missing():
    """Refusals: a configuration without project.json refuses with bad project."""
    dest = WORK / "h2s6j"
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    (dest / "targets.json").write_text('{"targets": []}\n')
    assert_refuse(dest, "refuse: bad project", WORK / "h2s6j.out")


def test_r_project_array():
    """Refusals: a project.json that is a JSON array refuses with bad project."""
    cfg = stage_config(
        {"project.json": "[]", "targets.json": '{"targets": []}'}, WORK / "b8f1d"
    )
    assert_refuse(cfg, "refuse: bad project", WORK / "b8f1d.out")


def test_r_project_null():
    """Refusals: a project.json that is JSON null refuses with bad project."""
    cfg = stage_config(
        {"project.json": "null", "targets.json": '{"targets": []}'}, WORK / "q2n7w"
    )
    assert_refuse(cfg, "refuse: bad project", WORK / "q2n7w.out")


def test_r_project_cflags_type():
    """Refusals: project cflags with a non-string element refuses with bad project."""
    cfg = stage_config(
        {"project.json": '{"cflags": [1]}', "targets.json": '{"targets": []}'},
        WORK / "y4t0e",
    )
    assert_refuse(cfg, "refuse: bad project", WORK / "y4t0e.out")


def test_r_project_env_not_object():
    """Refusals: project env given as an array refuses with bad project."""
    cfg = stage_config(
        {"project.json": '{"env": []}', "targets.json": '{"targets": []}'},
        WORK / "m5c2a",
    )
    assert_refuse(cfg, "refuse: bad project", WORK / "m5c2a.out")


def test_r_project_extra_key():
    """Refusals: a top-level project key outside the settings keys refuses with bad project."""
    cfg = stage_config(
        {
            "project.json": '{"cflags": [], "extra": 1}',
            "targets.json": '{"targets": []}',
        },
        WORK / "z6u9r",
    )
    assert_refuse(cfg, "refuse: bad project", WORK / "z6u9r.out")


def test_r_targets_missing():
    """Refusals: a configuration without targets.json refuses with bad targets."""
    dest = WORK / "p1g7v"
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    (dest / "project.json").write_text("{}\n")
    assert_refuse(dest, "refuse: bad targets", WORK / "p1g7v.out")


def test_r_targets_missing_key():
    """Refusals: targets.json that is the empty object {} refuses with bad targets."""
    cfg = stage_config({"project.json": "{}", "targets.json": "{}"}, WORK / "n4j8s")
    assert_refuse(cfg, "refuse: bad targets", WORK / "n4j8s.out")


def test_r_targets_null_root():
    """Refusals: targets.json that is JSON null refuses with bad targets."""
    cfg = stage_config({"project.json": "{}", "targets.json": "null"}, WORK / "x8m4k")
    assert_refuse(cfg, "refuse: bad targets", WORK / "x8m4k.out")


def test_r_targets_array_root():
    """Refusals: targets.json that is a JSON array refuses with bad targets."""
    cfg = stage_config({"project.json": "{}", "targets.json": "[]"}, WORK / "r5c1v")
    assert_refuse(cfg, "refuse: bad targets", WORK / "r5c1v.out")


def test_r_targets_extra_top():
    """Refusals: targets.json with a key besides targets refuses with bad targets."""
    cfg = stage_config(
        {"project.json": "{}", "targets.json": '{"targets": [], "extra": 1}'},
        WORK / "c3x5w",
    )
    assert_refuse(cfg, "refuse: bad targets", WORK / "c3x5w.out")


def test_r_targets_not_array():
    """Refusals: a targets value that is an object, not an array, refuses with bad targets."""
    cfg = stage_config(
        {"project.json": "{}", "targets.json": '{"targets": {}}'}, WORK / "f0l6q"
    )
    assert_refuse(cfg, "refuse: bad targets", WORK / "f0l6q.out")


def test_r_targets_dup_id():
    """Refusals: two adjacent targets with the same id refuse with bad targets."""
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps(
                {"targets": [tgt("same"), tgt("same", arch="aarch64", linker="lld")]}
            ),
        },
        WORK / "d7a2m",
    )
    assert_refuse(cfg, "refuse: bad targets", WORK / "d7a2m.out")


def test_r_targets_id_too_long():
    """Refusals: a 33-character id, one past the pattern's bound, refuses with bad targets."""
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps({"targets": [tgt("a" + "_" * 32)]}),
        },
        WORK / "s9e3k",
    )
    assert_refuse(cfg, "refuse: bad targets", WORK / "s9e3k.out")


def test_r_targets_bad_arch():
    """Refusals: the arch spelling mips refuses with bad targets."""
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps({"targets": [tgt("a", arch="mips")]}),
        },
        WORK / "v2q8h",
    )
    assert_refuse(cfg, "refuse: bad targets", WORK / "v2q8h.out")


def test_r_targets_arch_array():
    """Refusals: an arch that is not a string (an array) refuses with bad targets."""
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": '{"targets":[{"id":"a","arch":[],"os":"linux","linker":"bfd"}]}',
        },
        WORK / "u8k1t",
    )
    assert_refuse(cfg, "refuse: bad targets", WORK / "u8k1t.out")


def test_r_targets_bad_override():
    """Refusals: override cflags holding a number is not an array of strings and refuses with bad targets."""
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps(
                {"targets": [tgt("a", override={"cflags": [17]})]}
            ),
        },
        WORK / "g5n0y",
    )
    assert_refuse(cfg, "refuse: bad targets", WORK / "g5n0y.out")


def test_r_targets_override_cflags_not_array():
    """Refusals: override cflags given as a string refuses with bad targets."""
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps(
                {"targets": [tgt("a", override={"cflags": "-g"})]}
            ),
        },
        WORK / "j6r4b",
    )
    assert_refuse(cfg, "refuse: bad targets", WORK / "j6r4b.out")


def test_r_targets_non_object_elem():
    """Refusals: a targets element that is a number refuses with bad targets."""
    cfg = stage_config(
        {"project.json": "{}", "targets.json": '{"targets":[42]}'}, WORK / "e1w9z"
    )
    assert_refuse(cfg, "refuse: bad targets", WORK / "e1w9z.out")


def test_r_targets_extra_key():
    """Refusals: a target object with a key outside the allowed set refuses with bad targets."""
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps(
                {
                    "targets": [
                        {
                            "id": "a",
                            "arch": "x86_64",
                            "os": "linux",
                            "linker": "bfd",
                            "extra": 1,
                        }
                    ]
                }
            ),
        },
        WORK / "k3f8n",
    )
    assert_refuse(cfg, "refuse: bad targets", WORK / "k3f8n.out")


def test_r_targets_id_uppercase():
    """Refusals: an id with an uppercase initial fails the pattern and refuses with bad targets."""
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": '{"targets":[{"id":"A","arch":"x86_64","os":"linux","linker":"bfd"}]}',
        },
        WORK / "m7p2s",
    )
    assert_refuse(cfg, "refuse: bad targets", WORK / "m7p2s.out")


def test_r_overlay_weird_name():
    """Refusals: overlays/weird.json is not a whitelisted name and refuses with bad overlay."""
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": '{"targets": []}',
            "overlays/weird.json": "{}",
        },
        WORK / "a0c7x",
    )
    assert_refuse(cfg, "refuse: bad overlay", WORK / "a0c7x.out")


def test_r_overlay_non_json_member():
    """Refusals: an overlays/README member refuses with bad overlay."""
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": '{"targets": []}',
            "overlays/README": "x",
        },
        WORK / "t4m5p",
    )
    assert_refuse(cfg, "refuse: bad overlay", WORK / "t4m5p.out")


def test_r_overlay_bad_arch_suffix():
    """Refusals: overlays/linux_mips.json names no allowed arch and refuses with bad overlay."""
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": '{"targets": []}',
            "overlays/linux_mips.json": "{}",
        },
        WORK / "l3h8f",
    )
    assert_refuse(cfg, "refuse: bad overlay", WORK / "l3h8f.out")


def test_r_overlay_bad_value():
    """Refusals: an overlay env value that is a number refuses with bad overlay."""
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": '{"targets": []}',
            "overlays/linux.json": '{"env": {"A": 1}}',
        },
        WORK / "i9p2d",
    )
    assert_refuse(cfg, "refuse: bad overlay", WORK / "i9p2d.out")


def test_r_overlay_extra_key():
    """Refusals: an overlay key outside the settings keys refuses with bad overlay."""
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": '{"targets": []}',
            "overlays/linux.json": '{"cflags": [], "nope": 1}',
        },
        WORK / "o5y1g",
    )
    assert_refuse(cfg, "refuse: bad overlay", WORK / "o5y1g.out")


def test_arch_inject_x86_64():
    """Arch inject: an x86_64 target gets -m64 in cflags."""
    cfg = stage_config(
        {"project.json": "{}", "targets.json": json.dumps({"targets": [tgt("ax")]})},
        WORK / "arch_x86",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-m64"],
                "defines": {},
                "env": {},
                "id": "ax",
                "ldflags": ["-Wl,--hash-style=gnu"],
                "triple": "x86_64-linux-bfd",
            }
        ),
        WORK / "arch_x86.json",
    )


def test_arch_inject_aarch64():
    """Arch inject: an aarch64 target gets -march=armv8-a in cflags."""
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps({"targets": [tgt("aa", arch="aarch64")]}),
        },
        WORK / "arch_aa",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-march=armv8-a"],
                "defines": {},
                "env": {},
                "id": "aa",
                "ldflags": ["-Wl,--hash-style=gnu"],
                "triple": "aarch64-linux-bfd",
            }
        ),
        WORK / "arch_aa.json",
    )


def test_arch_inject_riscv64():
    """Arch inject: a riscv64 target gets -march=rv64gc (here with mold and its riscv64 extra)."""
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps(
                {"targets": [tgt("ar", arch="riscv64", linker="mold")]}
            ),
        },
        WORK / "arch_rv",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-march=rv64gc"],
                "defines": {},
                "env": {},
                "id": "ar",
                "ldflags": ["-fuse-ld=mold", "-Wl,--no-relax"],
                "triple": "riscv64-linux-mold",
            }
        ),
        WORK / "arch_rv.json",
    )


def test_arch_inject_after_os_overlay():
    """Arch inject comes after the os overlay, so -m64 follows the overlay's cflags."""
    cfg = stage_config(
        {
            "project.json": '{"cflags": ["-O2"]}',
            "targets.json": json.dumps({"targets": [tgt("ao")]}),
            "overlays/linux.json": '{"cflags": ["-pthread"]}',
        },
        WORK / "arch_after_os",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-O2", "-pthread", "-m64"],
                "defines": {},
                "env": {},
                "id": "ao",
                "ldflags": ["-Wl,--hash-style=gnu"],
                "triple": "x86_64-linux-bfd",
            }
        ),
        WORK / "arch_after_os.json",
    )


def test_toolchain_after_profile():
    """Resolution 7: a named toolchain's flags follow the inject and profile; the stamp ends in tc."""
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps({"targets": [tgt("tc", toolchain="ci")]}),
            "toolchains/ci.json": '{"cflags": ["-g"], "ldflags": ["-Lci"], "defines": {"CI": "1"}}',
        },
        WORK / "tc_after",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-m64", "-g"],
                "defines": {"CI": "1"},
                "env": {},
                "id": "tc",
                "ldflags": ["-Wl,--hash-style=gnu", "-Lci"],
                "triple": "x86_64-linux-bfd",
                "_tc": True,
            }
        ),
        WORK / "tc_after.json",
    )


def test_toolchain_before_override_delete():
    """Resolution 7-8: the override applies after the toolchain, so its $delete removes the toolchain's define."""
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps(
                {
                    "targets": [
                        tgt(
                            "tb",
                            linker="lld",
                            toolchain="ci",
                            override={
                                "cflags": ["-Wall"],
                                "defines": {"CI": "$delete"},
                            },
                        )
                    ]
                }
            ),
            "toolchains/ci.json": '{"cflags": ["-g"], "defines": {"CI": "1"}}',
        },
        WORK / "tc_before_ov",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-m64", "-g", "-Wall"],
                "defines": {},
                "env": {},
                "id": "tb",
                "ldflags": ["-fuse-ld=lld"],
                "triple": "x86_64-linux-lld",
                "_tc": True,
            }
        ),
        WORK / "tc_before_ov.json",
    )


def test_compact_adjacent_cflags():
    """Compact: an adjacent duplicate cflag is dropped while a non-adjacent repeat is kept."""
    cfg = stage_config(
        {
            "project.json": '{"cflags": ["-O", "-O", "-g", "-O"]}',
            "targets.json": json.dumps({"targets": [tgt("cp")]}),
        },
        WORK / "compact_adj",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-O", "-g", "-O", "-m64"],
                "defines": {},
                "env": {},
                "id": "cp",
                "ldflags": ["-Wl,--hash-style=gnu"],
                "triple": "x86_64-linux-bfd",
            }
        ),
        WORK / "compact_adj.json",
    )


def test_compact_adjacent_ldflags():
    """Compact: an adjacent duplicate ldflag is dropped."""
    cfg = stage_config(
        {
            "project.json": '{"ldflags": ["-Lx", "-Lx", "-Ly"]}',
            "targets.json": json.dumps({"targets": [tgt("cl")]}),
        },
        WORK / "compact_ld",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-m64"],
                "defines": {},
                "env": {},
                "id": "cl",
                "ldflags": ["-Lx", "-Ly", "-Wl,--hash-style=gnu"],
                "triple": "x86_64-linux-bfd",
            }
        ),
        WORK / "compact_ld.json",
    )


def test_stamp_changes_with_cflags():
    """Stamp: the digest covers the compacted cflags, so project cflags change it."""
    cfg = stage_config(
        {
            "project.json": '{"cflags": ["-O"]}',
            "targets.json": json.dumps({"targets": [tgt("s0")]}),
        },
        WORK / "stamp_c",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-O", "-m64"],
                "defines": {},
                "env": {},
                "id": "s0",
                "ldflags": ["-Wl,--hash-style=gnu"],
                "triple": "x86_64-linux-bfd",
            }
        ),
        WORK / "stamp_c.json",
    )


def test_stamp_sorted_env_keys():
    """Stamp: env entries are hashed in bytewise key order, whatever their order in the input."""
    cfg = stage_config(
        {
            "project.json": '{"env": {"Z": "2", "A": "1"}}',
            "targets.json": json.dumps({"targets": [tgt("se")]}),
        },
        WORK / "stamp_env",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-m64"],
                "defines": {},
                "env": {"A": "1", "Z": "2"},
                "id": "se",
                "ldflags": ["-Wl,--hash-style=gnu"],
                "triple": "x86_64-linux-bfd",
            }
        ),
        WORK / "stamp_env.json",
    )


def test_r_toolchain_missing_file():
    """Refusals: a target naming a toolchain with no toolchains/ directory refuses with bad toolchain."""
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps({"targets": [tgt("x", toolchain="missing")]}),
        },
        WORK / "tc_miss",
    )
    assert_refuse(cfg, "refuse: bad toolchain", WORK / "tc_miss.out")


def test_r_toolchain_missing_file_with_dir():
    """Refusals: a target naming a toolchain whose file is absent from toolchains/ refuses with bad toolchain."""
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps({"targets": [tgt("x", toolchain="missing")]}),
            "toolchains/other.json": "{}",
        },
        WORK / "tc_miss2",
    )
    assert_refuse(cfg, "refuse: bad toolchain", WORK / "tc_miss2.out")


def test_r_toolchain_bad_member_name():
    """Refusals: toolchains/Weird.json fails the name pattern and refuses with bad toolchain."""
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": '{"targets": []}',
            "toolchains/Weird.json": "{}",
        },
        WORK / "tc_weird",
    )
    assert_refuse(cfg, "refuse: bad toolchain", WORK / "tc_weird.out")


def test_r_toolchain_bad_member_txt():
    """Refusals: toolchains/ci.txt is not a .json member and refuses with bad toolchain."""
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": '{"targets": []}',
            "toolchains/ci.txt": "x",
        },
        WORK / "tc_txt",
    )
    assert_refuse(cfg, "refuse: bad toolchain", WORK / "tc_txt.out")


def test_r_toolchain_forbidden_key():
    """Refusals: a toolchain key outside the settings keys and extends refuses with bad toolchain."""
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": '{"targets": []}',
            "toolchains/ci.json": '{"nope": 1}',
        },
        WORK / "tc_key",
    )
    assert_refuse(cfg, "refuse: bad toolchain", WORK / "tc_key.out")


def test_r_targets_toolchain_uppercase():
    """Refusals: a target toolchain value with an uppercase initial refuses with bad targets."""
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps(
                {
                    "targets": [
                        {
                            "id": "x",
                            "arch": "x86_64",
                            "os": "linux",
                            "linker": "bfd",
                            "toolchain": "Ci",
                        }
                    ]
                }
            ),
            "toolchains/ci.json": "{}",
        },
        WORK / "tc_up",
    )
    assert_refuse(cfg, "refuse: bad targets", WORK / "tc_up.out")


def test_toolchain_name_len17():
    """Toolchains: a 17-character toolchain name is legal and its file is applied."""
    name = "abbbbbbbbbbbbbbbb"
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps({"targets": [tgt("tl", toolchain=name)]}),
            f"toolchains/{name}.json": "{}",
        },
        WORK / "tc_len17",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-m64"],
                "defines": {},
                "env": {},
                "id": "tl",
                "ldflags": ["-Wl,--hash-style=gnu"],
                "triple": "x86_64-linux-bfd",
                "_tc": True,
            }
        ),
        WORK / "tc_len17.json",
    )


def test_toolchain_name_len32():
    """Toolchains: a 32-character toolchain name, the pattern's upper bound, is legal."""
    name = "a" + "b" * 31
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps({"targets": [tgt("t32", toolchain=name)]}),
            f"toolchains/{name}.json": "{}",
        },
        WORK / "tc_len32",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-m64"],
                "defines": {},
                "env": {},
                "id": "t32",
                "ldflags": ["-Wl,--hash-style=gnu"],
                "triple": "x86_64-linux-bfd",
                "_tc": True,
            }
        ),
        WORK / "tc_len32.json",
    )


def test_toolchain_name_digit_underscore():
    """Toolchains: a name with an underscore and a digit (ci_2) is legal."""
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps({"targets": [tgt("td", toolchain="ci_2")]}),
            "toolchains/ci_2.json": "{}",
        },
        WORK / "tc_ci2",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-m64"],
                "defines": {},
                "env": {},
                "id": "td",
                "ldflags": ["-Wl,--hash-style=gnu"],
                "triple": "x86_64-linux-bfd",
                "_tc": True,
            }
        ),
        WORK / "tc_ci2.json",
    )


def test_toolchain_name_underscore_digit_mid():
    """Toolchains: a name with an inner underscore and digit (build_v3) is legal."""
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps(
                {"targets": [tgt("tu", linker="lld", toolchain="build_v3")]}
            ),
            "toolchains/build_v3.json": "{}",
        },
        WORK / "tc_buildv3",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-m64"],
                "defines": {},
                "env": {},
                "id": "tu",
                "ldflags": ["-fuse-ld=lld"],
                "triple": "x86_64-linux-lld",
                "_tc": True,
            }
        ),
        WORK / "tc_buildv3.json",
    )


def test_toolchain_applies_env():
    """Resolution 7: a toolchain's cflags, ldflags, env and defines all reach the target."""
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps({"targets": [tgt("te", toolchain="ci")]}),
            "toolchains/ci.json": '{"cflags": ["-g"], "ldflags": ["-Lci"], "defines": {"CI": "1"}, "env": {"FROM_TC": "1"}}',
        },
        WORK / "tc_env",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-m64", "-g"],
                "defines": {"CI": "1"},
                "env": {"FROM_TC": "1"},
                "id": "te",
                "ldflags": ["-Wl,--hash-style=gnu", "-Lci"],
                "triple": "x86_64-linux-bfd",
                "_tc": True,
            }
        ),
        WORK / "tc_env.json",
    )


def test_toolchain_applies_env_second():
    """Resolution 7: a toolchain env entry reaches an aarch64/mold target."""
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps(
                {
                    "targets": [
                        tgt("te2", arch="aarch64", linker="mold", toolchain="envy")
                    ]
                }
            ),
            "toolchains/envy.json": '{"env": {"TC": "yes"}}',
        },
        WORK / "tc_env2",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-march=armv8-a"],
                "defines": {},
                "env": {"TC": "yes"},
                "id": "te2",
                "ldflags": ["-fuse-ld=mold"],
                "triple": "aarch64-linux-mold",
                "_tc": True,
            }
        ),
        WORK / "tc_env2.json",
    )


def test_r_targets_override_forbidden_key():
    """Refusals: an override key outside the settings keys refuses with bad targets."""
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps({"targets": [tgt("x", override={"extra": 1})]}),
        },
        WORK / "ov_extra",
    )
    assert_refuse(cfg, "refuse: bad targets", WORK / "ov_extra.out")


def test_r_targets_override_forbidden_key_second():
    """Refusals: a different forbidden override key on another target refuses with bad targets."""
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps(
                {
                    "targets": [
                        {
                            "id": "y",
                            "arch": "x86_64",
                            "os": "linux",
                            "linker": "bfd",
                            "override": {"nope": "x"},
                        }
                    ]
                }
            ),
        },
        WORK / "ov_extra2",
    )
    assert_refuse(cfg, "refuse: bad targets", WORK / "ov_extra2.out")


def test_toolchain_delete_concreted():
    """Concrete: $delete values in a standalone toolchain leave no entry in the artifact."""
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps({"targets": [tgt("tdel", toolchain="ci")]}),
            "toolchains/ci.json": '{"env": {"E": "$delete"}, "defines": {"D": "$delete"}}',
        },
        WORK / "tc_del",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-m64"],
                "defines": {},
                "env": {},
                "id": "tdel",
                "ldflags": ["-Wl,--hash-style=gnu"],
                "triple": "x86_64-linux-bfd",
                "_tc": True,
            }
        ),
        WORK / "tc_del.json",
    )


def test_toolchain_delete_over_project():
    """Toolchain expansion: a file without extends keeps its $delete values, so they delete project keys."""
    cfg = stage_config(
        {
            "project.json": '{"env": {"E": "1"}, "defines": {"D": "1"}}',
            "targets.json": json.dumps({"targets": [tgt("td2", toolchain="wipe")]}),
            "toolchains/wipe.json": '{"env": {"E": "$delete"}, "defines": {"D": "$delete"}}',
        },
        WORK / "tc_del2",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-m64"],
                "defines": {},
                "env": {},
                "id": "td2",
                "ldflags": ["-Wl,--hash-style=gnu"],
                "triple": "x86_64-linux-bfd",
                "_tc": True,
            }
        ),
        WORK / "tc_del2.json",
    )


def test_compact_ldflags_nonadjacent():
    """Compact: a non-adjacent duplicate ldflag is kept."""
    cfg = stage_config(
        {
            "project.json": '{"ldflags": ["-Lx", "-Ly", "-Lx"]}',
            "targets.json": json.dumps({"targets": [tgt("cn")]}),
        },
        WORK / "compact_ld_na",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-m64"],
                "defines": {},
                "env": {},
                "id": "cn",
                "ldflags": ["-Lx", "-Ly", "-Lx", "-Wl,--hash-style=gnu"],
                "triple": "x86_64-linux-bfd",
            }
        ),
        WORK / "compact_ld_na.json",
    )


def test_compact_ldflags_nonadjacent_second():
    """Compact: a non-adjacent duplicate ldflag is kept ahead of the lld profile."""
    cfg = stage_config(
        {
            "project.json": '{"ldflags": ["-La", "-Lb", "-La"]}',
            "targets.json": json.dumps({"targets": [tgt("cn2", linker="lld")]}),
        },
        WORK / "compact_ld_na2",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-m64"],
                "defines": {},
                "env": {},
                "id": "cn2",
                "ldflags": ["-La", "-Lb", "-La", "-fuse-ld=lld"],
                "triple": "x86_64-linux-lld",
            }
        ),
        WORK / "compact_ld_na2.json",
    )


def test_compact_after_override_arch_dup():
    """Compact runs after override: an override -m64 next to the inject collapses to one."""
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps(
                {"targets": [tgt("co", override={"cflags": ["-m64"]})]}
            ),
        },
        WORK / "compact_ov",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-m64"],
                "defines": {},
                "env": {},
                "id": "co",
                "ldflags": ["-Wl,--hash-style=gnu"],
                "triple": "x86_64-linux-bfd",
            }
        ),
        WORK / "compact_ov.json",
    )


def test_compact_after_toolchain_dup():
    """Compact runs after the toolchain: a toolchain -m64 next to the inject collapses to one."""
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps({"targets": [tgt("ct", toolchain="dup")]}),
            "toolchains/dup.json": '{"cflags": ["-m64"]}',
        },
        WORK / "compact_tc",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-m64"],
                "defines": {},
                "env": {},
                "id": "ct",
                "ldflags": ["-Wl,--hash-style=gnu"],
                "triple": "x86_64-linux-bfd",
                "_tc": True,
            }
        ),
        WORK / "compact_tc.json",
    )


def test_targets_independent_ldflags_override():
    """Resolution: one target's override ldflags do not reach the next target."""
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps(
                {"targets": [tgt("a", override={"ldflags": ["-LA"]}), tgt("b")]}
            ),
        },
        WORK / "ind_ldflags",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-m64"],
                "defines": {},
                "env": {},
                "id": "a",
                "ldflags": ["-Wl,--hash-style=gnu", "-LA"],
                "triple": "x86_64-linux-bfd",
            },
            {
                "cflags": ["-m64"],
                "defines": {},
                "env": {},
                "id": "b",
                "ldflags": ["-Wl,--hash-style=gnu"],
                "triple": "x86_64-linux-bfd",
            },
        ),
        WORK / "ind_ldflags.json",
    )


def test_targets_independent_ldflags_override_mid():
    """Resolution: an override ldflags on the first-listed target leaves two later targets untouched."""
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps(
                {
                    "targets": [
                        tgt("mid", override={"ldflags": ["-LMID"]}),
                        tgt("later"),
                        tgt("zlast"),
                    ]
                }
            ),
        },
        WORK / "ind_ldflags_mid",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-m64"],
                "defines": {},
                "env": {},
                "id": "later",
                "ldflags": ["-Wl,--hash-style=gnu"],
                "triple": "x86_64-linux-bfd",
            },
            {
                "cflags": ["-m64"],
                "defines": {},
                "env": {},
                "id": "mid",
                "ldflags": ["-Wl,--hash-style=gnu", "-LMID"],
                "triple": "x86_64-linux-bfd",
            },
            {
                "cflags": ["-m64"],
                "defines": {},
                "env": {},
                "id": "zlast",
                "ldflags": ["-Wl,--hash-style=gnu"],
                "triple": "x86_64-linux-bfd",
            },
        ),
        WORK / "ind_ldflags_mid.json",
    )


def test_toolchain_extends_applies_parent():
    """Toolchain expansion: the parent's settings come first and the child's are Applied over them."""
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps({"targets": [tgt("ex", toolchain="ci")]}),
            "toolchains/base.json": '{"cflags": ["-fPIC"], "ldflags": ["-Lbase"]}',
            "toolchains/ci.json": '{"extends": "base", "cflags": ["-g"], "defines": {"CI": "1"}}',
        },
        WORK / "tc_ext",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-m64", "-fPIC", "-g"],
                "defines": {"CI": "1"},
                "env": {},
                "id": "ex",
                "ldflags": ["-Wl,--hash-style=gnu", "-Lbase"],
                "triple": "x86_64-linux-bfd",
                "_tc": True,
            }
        ),
        WORK / "tc_ext.json",
    )


def test_toolchain_extends_second_site():
    """Toolchain expansion: a parent's env reaches an aarch64/lld target through one extends hop."""
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps(
                {
                    "targets": [
                        tgt("ex2", arch="aarch64", linker="lld", toolchain="leaf")
                    ]
                }
            ),
            "toolchains/root.json": '{"env": {"P": "1"}}',
            "toolchains/leaf.json": '{"extends": "root", "cflags": ["-O"]}',
        },
        WORK / "tc_ext2",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-march=armv8-a", "-O"],
                "defines": {},
                "env": {"P": "1"},
                "id": "ex2",
                "ldflags": ["-fuse-ld=lld"],
                "triple": "aarch64-linux-lld",
                "_tc": True,
            }
        ),
        WORK / "tc_ext2.json",
    )


def test_r_toolchain_extends_cycle():
    """Refusals: two toolchains that extend each other refuse with bad toolchain when expanded."""
    dest = WORK / "extends_cycle_ab"
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps({"targets": [tgt("x", toolchain="a")]}),
            "toolchains/a.json": '{"extends": "b"}',
            "toolchains/b.json": '{"extends": "a"}',
        },
        dest,
    )
    assert_refuse(cfg, "refuse: bad toolchain", dest.with_suffix(".out"))


def test_r_toolchain_extends_ghost():
    """Refusals: an extends parent with no file refuses with bad toolchain when expanded."""
    dest = WORK / "extends_missing_parent"
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps({"targets": [tgt("x", toolchain="ci")]}),
            "toolchains/ci.json": '{"extends": "ghost"}',
        },
        dest,
    )
    assert_refuse(cfg, "refuse: bad toolchain", dest.with_suffix(".out"))


def test_profile_extra_linux_mold_riscv():
    """Profile extras: linux/mold/riscv64 adds -Wl,--no-relax after the profile."""
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps(
                {"targets": [tgt("pe", arch="riscv64", linker="mold")]}
            ),
        },
        WORK / "pe_mold",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-march=rv64gc"],
                "defines": {},
                "env": {},
                "id": "pe",
                "ldflags": ["-fuse-ld=mold", "-Wl,--no-relax"],
                "triple": "riscv64-linux-mold",
            }
        ),
        WORK / "pe_mold.json",
    )


def test_profile_extra_darwin_lld_aarch64():
    """Profile extras: darwin/lld/aarch64 adds -Wl,-dead_strip_dylibs after the profile."""
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps(
                {"targets": [tgt("pd", arch="aarch64", os_name="darwin", linker="lld")]}
            ),
        },
        WORK / "pe_dar",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-march=armv8-a"],
                "defines": {},
                "env": {},
                "id": "pd",
                "ldflags": ["-fuse-ld=lld", "-Wl,-dead_strip_dylibs"],
                "triple": "aarch64-darwin-lld",
            }
        ),
        WORK / "pe_dar.json",
    )


def test_stamp_tc_tag_differs_from_notc():
    """Stamp: naming an empty toolchain switches the final tag to tc, changing the digest."""
    with_tc = art(
        {
            "cflags": ["-m64"],
            "defines": {},
            "env": {},
            "id": "s",
            "ldflags": ["-Wl,--hash-style=gnu"],
            "triple": "x86_64-linux-bfd",
            "_tc": True,
        }
    )
    without = art(
        {
            "cflags": ["-m64"],
            "defines": {},
            "env": {},
            "id": "s",
            "ldflags": ["-Wl,--hash-style=gnu"],
            "triple": "x86_64-linux-bfd",
        }
    )
    assert with_tc != without
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps({"targets": [tgt("s", toolchain="empty")]}),
            "toolchains/empty.json": "{}",
        },
        WORK / "stamp_tc",
    )
    assert_success_bytes(cfg, with_tc, WORK / "stamp_tc.json")


def test_stamp_notc_plain_target():
    """Stamp: a target with no toolchain key ends its stamp recipe in notc."""
    cfg = stage_config(
        {"project.json": "{}", "targets.json": json.dumps({"targets": [tgt("n")]})},
        WORK / "stamp_notc",
    )
    assert_success_bytes(
        cfg,
        art(
            {
                "cflags": ["-m64"],
                "defines": {},
                "env": {},
                "id": "n",
                "ldflags": ["-Wl,--hash-style=gnu"],
                "triple": "x86_64-linux-bfd",
            }
        ),
        WORK / "stamp_notc.json",
    )


def test_extends_parent_digit_underscore():
    """Toolchains: an extends parent named ci_2 matches the name pattern and is inherited."""
    expected = art(
        {
            "_tc": True,
            "cflags": ["-m64", "-g"],
            "defines": {},
            "env": {},
            "id": "ep",
            "ldflags": ["-Wl,--hash-style=gnu"],
            "triple": "x86_64-linux-bfd",
        }
    )
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps({"targets": [tgt("ep", toolchain="child")]}),
            "toolchains/ci_2.json": '{"cflags": ["-g"]}',
            "toolchains/child.json": '{"extends": "ci_2"}',
        },
        WORK / "ext_ci2",
    )
    assert_success_bytes(cfg, expected, WORK / "ext_ci2.json")


def test_extends_parent_digit_underscore_second():
    """Toolchains: an extends parent named build_v3 matches the name pattern and is inherited."""
    expected = art(
        {
            "_tc": True,
            "cflags": ["-m64", "-O"],
            "defines": {},
            "env": {},
            "id": "ep2",
            "ldflags": ["-fuse-ld=lld"],
            "triple": "x86_64-linux-lld",
        }
    )
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps(
                {"targets": [tgt("ep2", linker="lld", toolchain="leaf")]}
            ),
            "toolchains/build_v3.json": '{"cflags": ["-O"]}',
            "toolchains/leaf.json": '{"extends": "build_v3"}',
        },
        WORK / "ext_bv3",
    )
    assert_success_bytes(cfg, expected, WORK / "ext_bv3.json")


def test_profile_extra_before_toolchain():
    """Resolution 6-7: the riscv64 extra lands before the toolchain's ldflags."""
    expected = art(
        {
            "_tc": True,
            "cflags": ["-march=rv64gc"],
            "defines": {},
            "env": {},
            "id": "px",
            "ldflags": ["-fuse-ld=mold", "-Wl,--no-relax", "-Ltc"],
            "triple": "riscv64-linux-mold",
        }
    )
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps(
                {"targets": [tgt("px", arch="riscv64", linker="mold", toolchain="ci")]}
            ),
            "toolchains/ci.json": '{"ldflags": ["-Ltc"]}',
        },
        WORK / "pe_tc",
    )
    assert_success_bytes(cfg, expected, WORK / "pe_tc.json")


def test_profile_extra_before_override():
    """Resolution 6 and 8: the riscv64 extra lands before the override's ldflags."""
    expected = art(
        {
            "cflags": ["-march=rv64gc"],
            "defines": {},
            "env": {},
            "id": "py",
            "ldflags": ["-fuse-ld=mold", "-Wl,--no-relax", "-U"],
            "triple": "riscv64-linux-mold",
        }
    )
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps(
                {
                    "targets": [
                        tgt(
                            "py",
                            arch="riscv64",
                            linker="mold",
                            override={"ldflags": ["-U"]},
                        )
                    ]
                }
            ),
        },
        WORK / "pe_ov",
    )
    assert_success_bytes(cfg, expected, WORK / "pe_ov.json")


def test_compact_exact_not_whitespace():
    """Compact: -O and -O-with-a-space are different flags; both are kept."""
    expected = art(
        {
            "cflags": ["-O", "-O ", "-m64"],
            "defines": {},
            "env": {},
            "id": "cw",
            "ldflags": ["-Lx", "-Lx ", "-Wl,--hash-style=gnu"],
            "triple": "x86_64-linux-bfd",
        }
    )
    cfg = stage_config(
        {
            "project.json": '{"cflags": ["-O", "-O "], "ldflags": ["-Lx", "-Lx "]}',
            "targets.json": json.dumps({"targets": [tgt("cw")]}),
        },
        WORK / "compact_ws",
    )
    assert_success_bytes(cfg, expected, WORK / "compact_ws.json")


def test_compact_exact_not_whitespace_second():
    """Compact: -g and -g-with-a-space are different cflags; both are kept."""
    expected = art(
        {
            "cflags": ["-g", "-g ", "-m64"],
            "defines": {},
            "env": {},
            "id": "cw2",
            "ldflags": ["-Wl,--hash-style=gnu"],
            "triple": "x86_64-linux-bfd",
        }
    )
    cfg = stage_config(
        {
            "project.json": '{"cflags": ["-g", "-g "]}',
            "targets.json": json.dumps({"targets": [tgt("cw2")]}),
        },
        WORK / "compact_ws2",
    )
    assert_success_bytes(cfg, expected, WORK / "compact_ws2.json")


def test_compact_ldflags_across_profile():
    """Compact runs after the profile: a project ldflag equal to the bfd profile collapses with it."""
    expected = art(
        {
            "cflags": ["-m64"],
            "defines": {},
            "env": {},
            "id": "cpf",
            "ldflags": ["-Wl,--hash-style=gnu"],
            "triple": "x86_64-linux-bfd",
        }
    )
    cfg = stage_config(
        {
            "project.json": '{"ldflags": ["-Wl,--hash-style=gnu"]}',
            "targets.json": json.dumps({"targets": [tgt("cpf")]}),
        },
        WORK / "compact_prof",
    )
    assert_success_bytes(cfg, expected, WORK / "compact_prof.json")


def test_compact_ldflags_across_profile_second():
    """Compact runs after the profile: a project -fuse-ld=lld collapses with the lld profile."""
    expected = art(
        {
            "cflags": ["-m64"],
            "defines": {},
            "env": {},
            "id": "cpf2",
            "ldflags": ["-fuse-ld=lld"],
            "triple": "x86_64-linux-lld",
        }
    )
    cfg = stage_config(
        {
            "project.json": '{"ldflags": ["-fuse-ld=lld"]}',
            "targets.json": json.dumps({"targets": [tgt("cpf2", linker="lld")]}),
        },
        WORK / "compact_prof2",
    )
    assert_success_bytes(cfg, expected, WORK / "compact_prof2.json")


def test_unused_toolchain_ghost_extends_ok():
    """Toolchain expansion: an unnamed member whose extends parent has no file is not a refusal."""
    expected = art(
        {
            "cflags": ["-m64"],
            "defines": {},
            "env": {},
            "id": "u",
            "ldflags": ["-Wl,--hash-style=gnu"],
            "triple": "x86_64-linux-bfd",
        }
    )
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps({"targets": [tgt("u")]}),
            "toolchains/ci.json": '{"extends": "ghost"}',
        },
        WORK / "unused_ghost",
    )
    assert_success_bytes(cfg, expected, WORK / "unused_ghost.json")


def test_unused_orphan_extends_ok_with_live():
    """Toolchain expansion: an orphaned unnamed member does not stop a target naming another toolchain."""
    expected = art(
        {
            "_tc": True,
            "cflags": ["-m64"],
            "defines": {},
            "env": {},
            "id": "a",
            "ldflags": ["-Wl,--hash-style=gnu"],
            "triple": "x86_64-linux-bfd",
        }
    )
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps({"targets": [tgt("a", toolchain="live")]}),
            "toolchains/live.json": "{}",
            "toolchains/orphan.json": '{"extends": "ghost"}',
        },
        WORK / "orphan_ok",
    )
    assert_success_bytes(cfg, expected, WORK / "orphan_ok.json")


def test_r_toolchain_extends_bad_ident():
    """Refusals: an extends value Ci fails the name pattern and refuses with bad toolchain."""
    dest = WORK / "extends_bad_ident"
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps({"targets": [tgt("x", toolchain="ci")]}),
            "toolchains/ci.json": '{"extends": "Ci"}',
        },
        dest,
    )
    assert_refuse(cfg, "refuse: bad toolchain", dest.with_suffix(".out"))


def test_r_toolchain_extends_depth_four():
    """Refusals: a four-file extends chain exceeds three files and refuses with bad toolchain."""
    dest = WORK / "extends_depth_four"
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps({"targets": [tgt("x", toolchain="d")]}),
            "toolchains/a.json": "{}",
            "toolchains/b.json": '{"extends": "a"}',
            "toolchains/c.json": '{"extends": "b"}',
            "toolchains/d.json": '{"extends": "c"}',
        },
        dest,
    )
    assert_refuse(cfg, "refuse: bad toolchain", dest.with_suffix(".out"))


def test_near_delete_toolchain_env():
    """Concrete: a toolchain env value of $delete-plus-space is not the sentinel and is kept."""
    expected = art(
        {
            "_tc": True,
            "cflags": ["-m64"],
            "defines": {},
            "env": {"K": "$delete "},
            "id": "nd",
            "ldflags": ["-Wl,--hash-style=gnu"],
            "triple": "x86_64-linux-bfd",
        }
    )
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps({"targets": [tgt("nd", toolchain="ci")]}),
            "toolchains/ci.json": '{"env": {"K": "$delete "}}',
        },
        WORK / "near_tc",
    )
    assert_success_bytes(cfg, expected, WORK / "near_tc.json")


def test_near_delete_override_defines():
    """Apply: an override defines value $deleteX is not the sentinel and is set."""
    expected = art(
        {
            "cflags": ["-m64"],
            "defines": {"K": "$deleteX"},
            "env": {},
            "id": "no",
            "ldflags": ["-Wl,--hash-style=gnu"],
            "triple": "x86_64-linux-bfd",
        }
    )
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps(
                {"targets": [tgt("no", override={"defines": {"K": "$deleteX"}})]}
            ),
        },
        WORK / "near_ov",
    )
    assert_success_bytes(cfg, expected, WORK / "near_ov.json")


def test_r_toolchain_extends_cycle_second():
    """Refusals: a second two-file extends cycle (x and y) refuses with bad toolchain."""
    dest = WORK / "extends_cycle_xy"
    cfg = stage_config(
        {
            "project.json": "{}",
            "targets.json": json.dumps({"targets": [tgt("z", toolchain="x")]}),
            "toolchains/x.json": '{"extends": "y"}',
            "toolchains/y.json": '{"extends": "x"}',
        },
        dest,
    )
    assert_refuse(cfg, "refuse: bad toolchain", dest.with_suffix(".out"))


HOST_ENV = {
    "CFLAGS": "-g",
    "SDKROOT": "/sdk",
    "CC": "host-compiler",
    "BUILD_HOST_TRIPLE": "host-triple",
    "CROSS_HOST_INJECT": "host",
}
BFD = ["-Wl,--hash-style=gnu"]


def plain(
    tid,
    cflags=("-m64",),
    ldflags=tuple(BFD),
    env=None,
    defines=None,
    triple="x86_64-linux-bfd",
    tc=False,
):
    a = {
        "cflags": list(cflags),
        "defines": dict(defines or {}),
        "env": dict(env or {}),
        "id": tid,
        "ldflags": list(ldflags),
        "triple": triple,
    }
    if tc:
        a["_tc"] = True
    return a


def stage(dest, project, targets, **files):
    body = {
        "project.json": json.dumps(project),
        "targets.json": json.dumps({"targets": targets}),
    }
    for name, obj in files.items():
        body[name.replace("__", "/") + ".json"] = json.dumps(obj)
    return stage_config(body, WORK / dest)


def test_compact_run_of_three():
    """A run of three equal flags compacts to one; pair-wise collapsing leaves two."""
    cfg = stage(
        "cr3",
        {"cflags": ["-O", "-O", "-O"], "ldflags": ["-Lx", "-Lx", "-Lx"]},
        [tgt("a")],
    )
    assert_success_bytes(
        cfg, art(plain("a", ["-O", "-m64"], ["-Lx", *BFD])), WORK / "cr3.json"
    )


def test_compact_long_runs_across_layers():
    """Runs of four and three, and runs closed by an override, compact to one element each."""
    cfg = stage(
        "cr4",
        {"cflags": ["-a", "-a", "-a", "-a", "-b", "-b", "-b"]},
        [tgt("a", override={"cflags": ["-m64", "-m64"], "ldflags": BFD + BFD})],
    )
    assert_success_bytes(cfg, art(plain("a", ["-a", "-b", "-m64"])), WORK / "cr4.json")


def test_toolchain_child_delete_spares_target():
    """A $delete in a file with extends removes the parent's entry, never the target's own."""
    cfg = stage(
        "tcd1",
        {"env": {"K": "project"}, "defines": {"D": "project"}},
        [tgt("a", toolchain="child")],
        toolchains__base={"env": {"K": "parent"}, "defines": {"D": "parent"}},
        toolchains__child={
            "extends": "base",
            "env": {"K": "$delete"},
            "defines": {"D": "$delete"},
        },
    )
    want = art(plain("a", env={"K": "project"}, defines={"D": "project"}, tc=True))
    assert_success_bytes(cfg, want, WORK / "tcd1.json")


def test_toolchain_child_delete_beside_standalone_delete():
    """An inherited $delete with no parent entry is a no-op; a standalone file's $delete still deletes."""
    cfg = stage(
        "tcd2",
        {"env": {"K": "project"}},
        [tgt("a", toolchain="child"), tgt("b", toolchain="solo")],
        toolchains__base={},
        toolchains__child={"extends": "base", "env": {"K": "$delete"}},
        toolchains__solo={"env": {"K": "$delete"}},
    )
    want = art(plain("a", env={"K": "project"}, tc=True), plain("b", tc=True))
    assert_success_bytes(cfg, want, WORK / "tcd2.json")


def test_toolchain_root_delete_reaches_target():
    """A parent without extends keeps its $delete values in the expansion, so they delete on the target."""
    cfg = stage(
        "tcr1",
        {"env": {"E": "p"}, "defines": {"D": "p"}},
        [tgt("a", toolchain="child")],
        toolchains__base={"defines": {"D": "$delete"}},
        toolchains__child={"extends": "base", "cflags": ["-g"]},
    )
    want = art(plain("a", ["-m64", "-g"], env={"E": "p"}, tc=True))
    assert_success_bytes(cfg, want, WORK / "tcr1.json")


def test_toolchain_root_delete_with_child_delete():
    """The root's $delete reaches the target while the child's $delete of a key the root lacks does not."""
    cfg = stage(
        "tcr2",
        {"env": {"K": "p", "M": "p"}},
        [tgt("a", "aarch64", "linux", "lld", toolchain="child"), tgt("b")],
        toolchains__base={"env": {"K": "$delete", "L": "x"}},
        toolchains__child={"extends": "base", "env": {"M": "$delete"}},
    )
    want = art(
        plain(
            "a",
            ["-march=armv8-a"],
            ["-fuse-ld=lld"],
            env={"L": "x", "M": "p"},
            triple="aarch64-linux-lld",
            tc=True,
        ),
        plain("b", env={"K": "p", "M": "p"}),
    )
    assert_success_bytes(cfg, want, WORK / "tcr2.json")


def test_unused_toolchain_cycle_ok():
    """A cycle among members no target names is never walked and is not a refusal."""
    cfg = stage(
        "uc1",
        {},
        [tgt("u")],
        toolchains__a={"extends": "b"},
        toolchains__b={"extends": "a"},
    )
    assert_success_bytes(cfg, art(plain("u")), WORK / "uc1.json")


def test_unused_cycle_beside_live_toolchain():
    """Unused two-node and self cycles do not stop a target whose own toolchain is acyclic."""
    cfg = stage(
        "uc2",
        {},
        [tgt("u", toolchain="live"), tgt("v")],
        toolchains__live={"cflags": ["-g"]},
        toolchains__x={"extends": "y"},
        toolchains__y={"extends": "x"},
        toolchains__s={"extends": "s"},
    )
    want = art(plain("u", ["-m64", "-g"], tc=True), plain("v"))
    assert_success_bytes(cfg, want, WORK / "uc2.json")


def test_unused_toolchain_depth_four_ok():
    """A four-file chain no target names is never walked and is not a refusal."""
    cfg = stage(
        "ud1",
        {},
        [tgt("u")],
        toolchains__a={},
        toolchains__b={"extends": "a"},
        toolchains__c={"extends": "b"},
        toolchains__d={"extends": "c"},
    )
    assert_success_bytes(cfg, art(plain("u")), WORK / "ud1.json")


def test_unused_depth_four_beside_two_file_chain():
    """A target walking two files succeeds although an unnamed member heads a four-file chain."""
    cfg = stage(
        "ud2",
        {},
        [tgt("u", toolchain="b")],
        toolchains__a={"cflags": ["-A"]},
        toolchains__b={"extends": "a", "cflags": ["-B"]},
        toolchains__c={"extends": "b"},
        toolchains__d={"extends": "c"},
    )
    assert_success_bytes(
        cfg, art(plain("u", ["-m64", "-A", "-B"], tc=True)), WORK / "ud2.json"
    )


def test_r_toolchain_unused_extends_bad_ident():
    """An extends value failing the name pattern refuses even on a member no target names."""
    cfg = stage("ubi1", {}, [tgt("u")], toolchains__unused={"extends": "Ci"})
    assert_refuse(cfg, "refuse: bad toolchain", WORK / "ubi1.json")


def test_r_toolchain_unused_extends_bad_ident_beside_live():
    """The member pattern check applies to an unnamed file while the named toolchain is valid."""
    cfg = stage(
        "ubi2",
        {},
        [tgt("u", toolchain="ok")],
        toolchains__ok={"cflags": ["-g"]},
        toolchains__other={"extends": "Ci"},
    )
    assert_refuse(cfg, "refuse: bad toolchain", WORK / "ubi2.json")


def test_near_delete_defines_whitespace_project():
    """Project defines whose value is $delete plus whitespace are kept; only the exact sentinel goes."""
    cfg = stage(
        "ndw1",
        {"defines": {"D": "$delete ", "E": " $delete", "F": "$delete"}},
        [tgt("a")],
    )
    want = art(plain("a", defines={"D": "$delete ", "E": " $delete"}))
    assert_success_bytes(cfg, want, WORK / "ndw1.json")


def test_near_delete_defines_whitespace_override():
    """An override defines value of $delete plus a tab sets the key rather than deleting it."""
    cfg = stage(
        "ndw2",
        {"defines": {"D": "keep"}},
        [tgt("a", override={"defines": {"D": "$delete\t", "G": "\t$delete"}})],
    )
    want = art(plain("a", defines={"D": "$delete\t", "G": "\t$delete"}))
    assert_success_bytes(cfg, want, WORK / "ndw2.json")


def test_profile_extra_darwin_before_override():
    """The darwin/lld/aarch64 extra lands right after the profile, before the override's ldflags."""
    cfg = stage(
        "pxd1", {}, [tgt("a", "aarch64", "darwin", "lld", override={"ldflags": ["-U"]})]
    )
    want = art(
        plain(
            "a",
            ["-march=armv8-a"],
            ["-fuse-ld=lld", "-Wl,-dead_strip_dylibs", "-U"],
            triple="aarch64-darwin-lld",
        )
    )
    assert_success_bytes(cfg, want, WORK / "pxd1.json")


def test_profile_extra_darwin_before_toolchain():
    """The darwin/lld/aarch64 extra precedes both toolchain and override ldflags."""
    cfg = stage(
        "pxd2",
        {},
        [
            tgt(
                "a",
                "aarch64",
                "darwin",
                "lld",
                toolchain="ci",
                override={"ldflags": ["-U"]},
            )
        ],
        toolchains__ci={"ldflags": ["-Ltc"]},
    )
    ld = ["-fuse-ld=lld", "-Wl,-dead_strip_dylibs", "-Ltc", "-U"]
    want = art(plain("a", ["-march=armv8-a"], ld, triple="aarch64-darwin-lld", tc=True))
    assert_success_bytes(cfg, want, WORK / "pxd2.json")


def test_host_isolation_named_toolchain():
    """Host compiler variables add nothing when the target names a toolchain."""
    cfg = stage(
        "hit1",
        {},
        [tgt("a", "aarch64", "darwin", "lld", toolchain="ci")],
        toolchains__ci={},
    )
    want = art(
        plain(
            "a",
            ["-march=armv8-a"],
            ["-fuse-ld=lld", "-Wl,-dead_strip_dylibs"],
            triple="aarch64-darwin-lld",
            tc=True,
        )
    )
    assert_success_bytes(cfg, want, WORK / "hit1.json", env=HOST_ENV)


def test_host_isolation_inherited_toolchain():
    """Host compiler variables add nothing to a target whose toolchain inherits through extends."""
    cfg = stage(
        "hit2",
        {},
        [tgt("a", toolchain="child")],
        toolchains__base={"cflags": ["-B"]},
        toolchains__child={"extends": "base", "env": {"E": "1"}},
    )
    want = art(plain("a", ["-m64", "-B"], env={"E": "1"}, tc=True))
    assert_success_bytes(cfg, want, WORK / "hit2.json", env=HOST_ENV)


def test_output_keys_bytewise_case():
    """Map keys sort by byte: uppercase before underscore before lowercase, not case-folded."""
    cfg = stage(
        "okb1",
        {"env": {"a": "1", "B": "2"}, "defines": {"b": "1", "A": "2", "_x": "3"}},
        [tgt("a")],
    )
    want = art(
        plain("a", env={"a": "1", "B": "2"}, defines={"b": "1", "A": "2", "_x": "3"})
    )
    assert want.index('"B"') < want.index('"a"') and want.index('"A"') < want.index(
        '"_x"'
    ) < want.index('"b"')
    assert_success_bytes(cfg, want, WORK / "okb1.json")


def test_output_keys_bytewise_case_from_layers():
    """Mixed-case keys arriving from an overlay and a toolchain sort by byte in the output."""
    cfg = stage(
        "okb2",
        {},
        [tgt("a", toolchain="tc")],
        overlays__linux={"env": {"zeta": "1", "Alpha": "2"}},
        toolchains__tc={"env": {"beta": "3", "Gamma": "4"}},
    )
    want = art(
        plain("a", env={"zeta": "1", "Alpha": "2", "beta": "3", "Gamma": "4"}, tc=True)
    )
    assert want.index('"Gamma"') < want.index('"beta"')
    assert_success_bytes(cfg, want, WORK / "okb2.json")


def test_r_missing_toolchain_after_valid_target():
    """A toolchain refusal on a later target leaves no output from the earlier valid target."""
    cfg = stage("mtv1", {}, [tgt("a"), tgt("b", toolchain="missing")])
    assert_refuse(cfg, "refuse: bad toolchain", WORK / "mtv1" / "out.json")


def test_r_extends_cycle_after_valid_targets():
    """An extends cycle met on the third target leaves no output from the first two."""
    cfg = stage(
        "mtv2",
        {},
        [tgt("a"), tgt("b", toolchain="ok"), tgt("c", toolchain="x")],
        toolchains__ok={},
        toolchains__x={"extends": "y"},
        toolchains__y={"extends": "x"},
    )
    assert_refuse(cfg, "refuse: bad toolchain", WORK / "mtv2" / "out.json")


def test_profile_mold_preserves_maps():
    """The linux/mold profile contributes only ldflags and keeps populated env and defines."""
    cfg = stage(
        "pmm1", {"env": {"A": "1"}, "defines": {"D": "1"}}, [tgt("m", linker="mold")]
    )
    want = art(
        plain(
            "m",
            ldflags=["-fuse-ld=mold"],
            env={"A": "1"},
            defines={"D": "1"},
            triple="x86_64-linux-mold",
        )
    )
    assert_success_bytes(cfg, want, WORK / "pmm1.json")


def test_profile_extra_riscv_preserves_maps():
    """The linux/mold/riscv64 extra contributes only ldflags and keeps populated env and defines."""
    cfg = stage(
        "pxr1",
        {"env": {"A": "1"}, "defines": {"D": "1"}},
        [tgt("r", "riscv64", "linux", "mold")],
    )
    want = art(
        plain(
            "r",
            ["-march=rv64gc"],
            ["-fuse-ld=mold", "-Wl,--no-relax"],
            {"A": "1"},
            {"D": "1"},
            "riscv64-linux-mold",
        )
    )
    assert_success_bytes(cfg, want, WORK / "pxr1.json")


def test_profile_extras_preserve_maps_multi():
    """Both extras and the plain mold profile keep env and defines on targets resolved together."""
    cfg = stage(
        "pxm1",
        {"env": {"A": "1"}, "defines": {"D": "1"}},
        [
            tgt("r", "riscv64", "linux", "mold"),
            tgt("d", "aarch64", "darwin", "lld"),
            tgt("m", linker="mold"),
        ],
    )
    maps = {"env": {"A": "1"}, "defines": {"D": "1"}}
    want = art(
        plain(
            "d",
            ["-march=armv8-a"],
            ["-fuse-ld=lld", "-Wl,-dead_strip_dylibs"],
            triple="aarch64-darwin-lld",
            **maps,
        ),
        plain("m", ldflags=["-fuse-ld=mold"], triple="x86_64-linux-mold", **maps),
        plain(
            "r",
            ["-march=rv64gc"],
            ["-fuse-ld=mold", "-Wl,--no-relax"],
            triple="riscv64-linux-mold",
            **maps,
        ),
    )
    assert_success_bytes(cfg, want, WORK / "pxm1.json")


def test_extends_parent_len17():
    """A 17-character extends parent name is legal and its file is inherited."""
    parent = "a" + "b" * 16
    cfg = stage(
        "epl17",
        {},
        [tgt("a", toolchain="child")],
        toolchains__child={"extends": parent, "cflags": ["-c"]},
        **{f"toolchains__{parent}": {"cflags": ["-p"]}},
    )
    assert_success_bytes(
        cfg, art(plain("a", ["-m64", "-p", "-c"], tc=True)), WORK / "epl17.json"
    )


def test_extends_parent_len32():
    """A 32-character extends parent name, the pattern's upper bound, is legal and inherited."""
    parent = "a" + "b" * 31
    cfg = stage(
        "epl32",
        {},
        [tgt("a", "aarch64", "linux", "lld", toolchain="child")],
        toolchains__child={"extends": parent},
        **{f"toolchains__{parent}": {"ldflags": ["-Lp"]}},
    )
    want = art(
        plain(
            "a",
            ["-march=armv8-a"],
            ["-fuse-ld=lld", "-Lp"],
            triple="aarch64-linux-lld",
            tc=True,
        )
    )
    assert_success_bytes(cfg, want, WORK / "epl32.json")


def test_toolchain_child_overwrites_parent_maps():
    """A child's ordinary map value replaces the parent's value for the same key."""
    cfg = stage(
        "tco1",
        {},
        [tgt("a", toolchain="child")],
        toolchains__base={"env": {"K": "parent"}, "defines": {"D": "parent"}},
        toolchains__child={
            "extends": "base",
            "env": {"K": "child"},
            "defines": {"D": "child"},
        },
    )
    want = art(plain("a", env={"K": "child"}, defines={"D": "child"}, tc=True))
    assert_success_bytes(cfg, want, WORK / "tco1.json")


def test_toolchain_child_overwrites_parent_and_project_maps():
    """The child's value wins over both the parent's and the project's value for a shared key."""
    cfg = stage(
        "tco2",
        {"env": {"K": "proj"}, "defines": {"D": "proj"}},
        [tgt("a", "riscv64", "linux", "bfd", toolchain="child")],
        toolchains__base={"env": {"K": "parent", "J": "p"}, "defines": {"D": "parent"}},
        toolchains__child={
            "extends": "base",
            "env": {"K": "child"},
            "defines": {"D": "child", "E": "c"},
        },
    )
    want = art(
        plain(
            "a",
            ["-march=rv64gc"],
            env={"J": "p", "K": "child"},
            defines={"D": "child", "E": "c"},
            triple="riscv64-linux-bfd",
            tc=True,
        )
    )
    assert_success_bytes(cfg, want, WORK / "tco2.json")


def test_toolchain_inherited_ldflags_concat():
    """A child's ldflags follow the parent's ldflags; they do not replace them."""
    cfg = stage(
        "til1",
        {},
        [tgt("a", toolchain="child")],
        toolchains__base={"ldflags": ["-Lparent"]},
        toolchains__child={"extends": "base", "ldflags": ["-Lchild"]},
    )
    want = art(plain("a", ldflags=[*BFD, "-Lparent", "-Lchild"], tc=True))
    assert_success_bytes(cfg, want, WORK / "til1.json")


def test_toolchain_inherited_flags_concat_after_profile():
    """Inherited cflags and ldflags both concatenate parent then child after the darwin/ld64 profile."""
    cfg = stage(
        "til2",
        {"ldflags": ["-Lproj"]},
        [tgt("a", os_name="darwin", linker="ld64", toolchain="child")],
        toolchains__base={"cflags": ["-B"], "ldflags": ["-Lp1", "-Lp2"]},
        toolchains__child={"extends": "base", "cflags": ["-C"], "ldflags": ["-Lc"]},
    )
    ld = ["-Lproj", "-Wl,-dead_strip", "-Lp1", "-Lp2", "-Lc"]
    want = art(
        plain("a", ["-m64", "-B", "-C"], ld, triple="x86_64-darwin-ld64", tc=True)
    )
    assert_success_bytes(cfg, want, WORK / "til2.json")


def test_profile_windows_lld_preserves_maps():
    """The windows/lld profile contributes only ldflags and keeps populated env and defines."""
    cfg = stage(
        "pwl1",
        {"env": {"E": "1"}, "defines": {"D": "1"}},
        [tgt("w", os_name="windows", linker="lld")],
    )
    want = art(
        plain(
            "w",
            ldflags=["-fuse-ld=lld"],
            env={"E": "1"},
            defines={"D": "1"},
            triple="x86_64-windows-lld",
        )
    )
    assert_success_bytes(cfg, want, WORK / "pwl1.json")


def test_profile_windows_link_preserves_maps():
    """The windows/link profile contributes only ldflags and keeps populated env and defines."""
    cfg = stage(
        "pwk1",
        {"env": {"E": "1"}, "defines": {"D": "1"}},
        [tgt("w", os_name="windows", linker="link")],
    )
    want = art(
        plain(
            "w",
            ldflags=["/INCREMENTAL:NO"],
            env={"E": "1"},
            defines={"D": "1"},
            triple="x86_64-windows-link",
        )
    )
    assert_success_bytes(cfg, want, WORK / "pwk1.json")


def test_profile_windows_both_linkers_preserve_maps():
    """Both windows profiles keep maps built from the project and the windows overlay."""
    cfg = stage(
        "pwb1",
        {"env": {"E": "1"}, "defines": {"D": "1"}},
        [
            tgt("wl", "aarch64", "windows", "lld"),
            tgt("wk", os_name="windows", linker="link"),
        ],
        overlays__windows={"defines": {"W": "1"}},
    )
    maps = {"env": {"E": "1"}, "defines": {"D": "1", "W": "1"}}
    want = art(
        plain("wk", ldflags=["/INCREMENTAL:NO"], triple="x86_64-windows-link", **maps),
        plain(
            "wl",
            ["-march=armv8-a"],
            ["-fuse-ld=lld"],
            triple="aarch64-windows-lld",
            **maps,
        ),
    )
    assert_success_bytes(cfg, want, WORK / "pwb1.json")


def test_defines_equal_values_kept():
    """Two defines keys with the same value both survive; maps are never deduplicated by value."""
    cfg = stage("dev1", {"defines": {"A": "1", "B": "1"}}, [tgt("a")])
    assert_success_bytes(
        cfg, art(plain("a", defines={"A": "1", "B": "1"})), WORK / "dev1.json"
    )


def test_defines_equal_values_from_layers():
    """Equal-valued defines and env entries arriving from four layers all survive."""
    cfg = stage(
        "dev2",
        {"defines": {"A": "1"}, "env": {"X": "1"}},
        [tgt("a", toolchain="tc", override={"defines": {"E": "1"}, "env": {"Y": "1"}})],
        overlays__linux={"defines": {"B": "1"}},
        toolchains__tc={"defines": {"C": "1"}},
    )
    want = art(
        plain(
            "a",
            env={"X": "1", "Y": "1"},
            defines={"A": "1", "B": "1", "C": "1", "E": "1"},
            tc=True,
        )
    )
    assert_success_bytes(cfg, want, WORK / "dev2.json")


def test_shared_toolchain_override_isolation():
    """One target's override does not reach another target naming the same toolchain."""
    cfg = stage(
        "sto1",
        {},
        [
            tgt(
                "a",
                toolchain="ci",
                override={"env": {"LEAK": "1"}, "cflags": ["-x"], "ldflags": ["-Lx"]},
            ),
            tgt("b", toolchain="ci"),
        ],
        toolchains__ci={},
    )
    want = art(
        plain("a", ["-m64", "-x"], [*BFD, "-Lx"], env={"LEAK": "1"}, tc=True),
        plain("b", tc=True),
    )
    assert_success_bytes(cfg, want, WORK / "sto1.json")


def test_shared_inherited_toolchain_override_isolation():
    """A middle target's override, including a $delete, leaves earlier and later sharers of the toolchain intact."""
    cfg = stage(
        "sto2",
        {},
        [
            tgt("p", toolchain="child"),
            tgt(
                "q",
                toolchain="child",
                override={
                    "env": {"B": "$delete", "Q": "1"},
                    "defines": {"QD": "1"},
                    "ldflags": ["-Lq"],
                },
            ),
            tgt("r", toolchain="child"),
        ],
        toolchains__base={"env": {"B": "1"}},
        toolchains__child={"extends": "base", "cflags": ["-c"]},
    )
    want = art(
        plain("p", ["-m64", "-c"], env={"B": "1"}, tc=True),
        plain(
            "q",
            ["-m64", "-c"],
            [*BFD, "-Lq"],
            env={"Q": "1"},
            defines={"QD": "1"},
            tc=True,
        ),
        plain("r", ["-m64", "-c"], env={"B": "1"}, tc=True),
    )
    assert_success_bytes(cfg, want, WORK / "sto2.json")


def test_artifacts_sorted_bytewise_digits():
    """Ids sort by byte, so a10 precedes a2; a natural numeric sort is wrong."""
    cfg = stage("asd1", {}, [tgt("a2"), tgt("a10")])
    assert_success_bytes(cfg, art(plain("a10"), plain("a2")), WORK / "asd1.json")


def test_artifacts_sorted_bytewise_digit_runs():
    """Ids with digit runs and underscores sort by byte: t1, t10, t100, t9, t_1."""
    cfg = stage("asd2", {}, [tgt("t9"), tgt("t_1"), tgt("t100"), tgt("t1"), tgt("t10")])
    want = art(*(plain(t) for t in ["t1", "t10", "t100", "t9", "t_1"]))
    assert_success_bytes(cfg, want, WORK / "asd2.json")


def test_arch_overlay_maps_overwrite_and_delete():
    """An arch overlay overwrites and deletes project map entries and appends both flag arrays."""
    cfg = stage(
        "aom1",
        {
            "cflags": ["-cp"],
            "ldflags": ["-Lp"],
            "env": {"K": "p", "X": "p"},
            "defines": {"D": "p", "Y": "p"},
        },
        [tgt("a")],
        overlays__linux_x86_64={
            "cflags": ["-ca"],
            "ldflags": ["-La"],
            "env": {"K": "arch", "X": "$delete"},
            "defines": {"D": "arch", "Y": "$delete"},
        },
    )
    want = art(
        plain(
            "a",
            ["-cp", "-ca", "-m64"],
            ["-Lp", "-La", *BFD],
            env={"K": "arch"},
            defines={"D": "arch"},
        )
    )
    assert_success_bytes(cfg, want, WORK / "aom1.json")


def test_darwin_arch_overlay_maps_through_ld64_profile():
    """darwin_aarch64 overwrites and deletes over the os overlay, and the ld64 profile keeps the maps."""
    cfg = stage(
        "aom2",
        {
            "ldflags": ["-Lp"],
            "env": {"K": "p", "X": "p"},
            "defines": {"D": "p", "Y": "p"},
        },
        [tgt("a", "aarch64", "darwin", "ld64")],
        overlays__darwin={"env": {"O": "1"}},
        overlays__darwin_aarch64={
            "ldflags": ["-La"],
            "env": {"K": "arch", "X": "$delete"},
            "defines": {"D": "arch", "Y": "$delete"},
        },
    )
    want = art(
        plain(
            "a",
            ["-march=armv8-a"],
            ["-Lp", "-La", "-Wl,-dead_strip"],
            env={"K": "arch", "O": "1"},
            defines={"D": "arch"},
            triple="aarch64-darwin-ld64",
        )
    )
    assert_success_bytes(cfg, want, WORK / "aom2.json")


def test_toolchain_child_delete_removes_parent_entry():
    """A child's $delete removes the matching parent entry from the expansion and keeps the rest."""
    cfg = stage(
        "tcp1",
        {},
        [tgt("a", toolchain="child")],
        toolchains__base={"env": {"K": "p", "L": "l"}, "defines": {"D": "p"}},
        toolchains__child={
            "extends": "base",
            "env": {"K": "$delete"},
            "defines": {"D": "$delete"},
        },
    )
    assert_success_bytes(
        cfg, art(plain("a", env={"L": "l"}, tc=True)), WORK / "tcp1.json"
    )


def test_os_overlay_maps_through_lld_profile():
    """The os overlay overwrites and deletes map entries, and the inject and linux/lld profile keep everything."""
    cfg = stage(
        "oom1",
        {"ldflags": ["-Lp"], "env": {"K": "p", "X": "p"}, "defines": {"D": "p"}},
        [tgt("a", "aarch64", "linux", "lld")],
        overlays__linux={"env": {"X": "$delete"}, "defines": {"D": "os"}},
    )
    want = art(
        plain(
            "a",
            ["-march=armv8-a"],
            ["-Lp", "-fuse-ld=lld"],
            env={"K": "p"},
            defines={"D": "os"},
            triple="aarch64-linux-lld",
        )
    )
    assert_success_bytes(cfg, want, WORK / "oom1.json")


def test_override_maps_overwrite_on_riscv_mold():
    """Override values replace existing map values, and project ldflags survive the riscv64 inject."""
    cfg = stage(
        "ovm1",
        {"ldflags": ["-Lp"], "env": {"K": "p"}, "defines": {"D": "p"}},
        [
            tgt(
                "a",
                "riscv64",
                "linux",
                "mold",
                override={"env": {"K": "ov"}, "defines": {"D": "ov"}},
            )
        ],
    )
    want = art(
        plain(
            "a",
            ["-march=rv64gc"],
            ["-Lp", "-fuse-ld=mold", "-Wl,--no-relax"],
            env={"K": "ov"},
            defines={"D": "ov"},
            triple="riscv64-linux-mold",
        )
    )
    assert_success_bytes(cfg, want, WORK / "ovm1.json")


def test_toolchain_and_override_overwrite_project_maps():
    """A standalone toolchain and an override each replace the project's value for an existing key."""
    cfg = stage(
        "ovm2",
        {"ldflags": ["-Lp"], "env": {"K": "p"}, "defines": {"D": "p", "P": "1"}},
        [
            tgt("a", toolchain="solo"),
            tgt(
                "b",
                "aarch64",
                "linux",
                "lld",
                override={"env": {"K": "ov"}, "defines": {"D": "ov"}},
            ),
        ],
        toolchains__solo={"env": {"K": "tc"}, "defines": {"D": "tc"}},
    )
    want = art(
        plain(
            "a",
            ldflags=["-Lp", *BFD],
            env={"K": "tc"},
            defines={"D": "tc", "P": "1"},
            tc=True,
        ),
        plain(
            "b",
            ["-march=armv8-a"],
            ["-Lp", "-fuse-ld=lld"],
            env={"K": "ov"},
            defines={"D": "ov", "P": "1"},
            triple="aarch64-linux-lld",
        ),
    )
    assert_success_bytes(cfg, want, WORK / "ovm2.json")


ALL_PLATFORMS = [
    ("lb", "x86_64", "linux", "bfd", ["-m64"], [*BFD]),
    ("ll", "aarch64", "linux", "lld", ["-march=armv8-a"], ["-fuse-ld=lld"]),
    (
        "lm",
        "riscv64",
        "linux",
        "mold",
        ["-march=rv64gc"],
        ["-fuse-ld=mold", "-Wl,--no-relax"],
    ),
    ("dk", "x86_64", "darwin", "ld64", ["-m64"], ["-Wl,-dead_strip"]),
    (
        "dl",
        "aarch64",
        "darwin",
        "lld",
        ["-march=armv8-a"],
        ["-fuse-ld=lld", "-Wl,-dead_strip_dylibs"],
    ),
    ("wk", "x86_64", "windows", "link", ["-m64"], ["/INCREMENTAL:NO"]),
    ("wl", "aarch64", "windows", "lld", ["-march=armv8-a"], ["-fuse-ld=lld"]),
]


def test_compact_initial_empty_flags():
    """A leading run of empty-string flags compacts to one empty string; an empty map value is kept."""
    cfg = stage(
        "cef1",
        {"cflags": ["", "", "-a"], "ldflags": ["", ""], "env": {"P": ""}},
        [tgt("a")],
    )
    want = art(plain("a", ["", "-a", "-m64"], ["", *BFD], env={"P": ""}))
    assert_success_bytes(cfg, want, WORK / "cef1.json")


def test_compact_empty_flags_from_layers():
    """Empty-string flags from overlay, toolchain and override are legal and compact only when adjacent."""
    cfg = stage(
        "cef2",
        {"ldflags": ["", ""]},
        [tgt("a", toolchain="tc", override={"cflags": [""]})],
        overlays__linux={"cflags": [""]},
        toolchains__tc={"cflags": ["", ""], "ldflags": [""]},
    )
    want = art(plain("a", ["", "-m64", ""], ["", *BFD, ""], tc=True))
    assert_success_bytes(cfg, want, WORK / "cef2.json")


def test_empty_strings_everywhere_in_project():
    """Empty flags, empty map values and empty map keys in the project are all legal and kept."""
    cfg = stage(
        "ese1",
        {
            "cflags": [""],
            "ldflags": [""],
            "env": {"E": "", "": "k"},
            "defines": {"D": "", "": "v"},
        },
        [tgt("a")],
    )
    want = art(
        plain(
            "a",
            ["", "-m64"],
            ["", *BFD],
            env={"": "k", "E": ""},
            defines={"": "v", "D": ""},
        )
    )
    assert_success_bytes(cfg, want, WORK / "ese1.json")


def test_empty_map_values_from_layers():
    """Empty map values and keys arriving from overlay, toolchain and override are kept, not deleted."""
    cfg = stage(
        "ese2",
        {},
        [tgt("a", toolchain="tc", override={"env": {"": "o"}, "defines": {"F": ""}})],
        overlays__linux={"env": {"A": ""}, "defines": {"B": ""}},
        toolchains__tc={"env": {"C": ""}, "defines": {"": ""}},
    )
    want = art(
        plain(
            "a",
            env={"": "o", "A": "", "C": ""},
            defines={"": "", "B": "", "F": ""},
            tc=True,
        )
    )
    assert_success_bytes(cfg, want, WORK / "ese2.json")


def test_empty_values_on_windows_and_toolchain_targets():
    """Empty strings from a windows overlay, an override and a toolchain survive on two platforms."""
    cfg = stage(
        "ese3",
        {},
        [
            tgt(
                "w",
                os_name="windows",
                linker="link",
                override={"env": {"W": ""}, "defines": {"X": ""}, "cflags": [""]},
            ),
            tgt("l", "aarch64", "linux", "lld", toolchain="tc2"),
        ],
        overlays__windows={"env": {"O": ""}, "ldflags": [""]},
        toolchains__tc2={"defines": {"T": ""}, "cflags": [""]},
    )
    want = art(
        plain(
            "l",
            ["-march=armv8-a", ""],
            ["-fuse-ld=lld"],
            defines={"T": ""},
            triple="aarch64-linux-lld",
            tc=True,
        ),
        plain(
            "w",
            ["-m64", ""],
            ["", "/INCREMENTAL:NO"],
            env={"O": "", "W": ""},
            defines={"X": ""},
            triple="x86_64-windows-link",
        ),
    )
    assert_success_bytes(cfg, want, WORK / "ese3.json")


def test_host_isolation_every_platform():
    """With all five host variables set, every os/linker branch, windows included, yields configuration-only output."""
    cfg = stage("hap1", {}, [tgt(t, a, o, ln) for t, a, o, ln, _, _ in ALL_PLATFORMS])
    want = art(
        *(
            plain(t, cf, ld, triple=f"{a}-{o}-{ln}")
            for t, a, o, ln, cf, ld in sorted(ALL_PLATFORMS)
        )
    )
    assert_success_bytes(cfg, want, WORK / "hap1.json", env=HOST_ENV)


def test_host_isolation_every_platform_with_toolchain():
    """Other host values leave populated maps unchanged on every platform, with and without a toolchain."""
    targets = [
        tgt(t, a, o, ln, toolchain="ci" if i % 2 else None)
        for i, (t, a, o, ln, _, _) in enumerate(ALL_PLATFORMS)
    ]
    cfg = stage(
        "hap2",
        {"env": {"E": "1"}, "defines": {"D": "1"}},
        targets,
        toolchains__ci={"cflags": ["-t"]},
    )
    tc_ids = {t["id"] for t in targets if "toolchain" in t}
    arts = []
    for t, a, o, ln, cf, ld in sorted(ALL_PLATFORMS):
        used = t in tc_ids
        arts.append(
            plain(
                t,
                cf + (["-t"] if used else []),
                ld,
                {"E": "1"},
                {"D": "1"},
                f"{a}-{o}-{ln}",
                used,
            )
        )
    env = {
        "CROSS_HOST_INJECT": "x",
        "CC": "cc2",
        "CFLAGS": "-O3",
        "BUILD_HOST_TRIPLE": "t2",
        "SDKROOT": "/other",
    }
    assert_success_bytes(cfg, art(*arts), WORK / "hap2.json", env=env)


INJECT = {
    "x86_64": ["-m64"],
    "aarch64": ["-march=armv8-a"],
    "riscv64": ["-march=rv64gc"],
}
PROFILE_LD = {
    ("linux", "bfd"): [*BFD],
    ("linux", "lld"): ["-fuse-ld=lld"],
    ("linux", "mold"): ["-fuse-ld=mold"],
    ("darwin", "ld64"): ["-Wl,-dead_strip"],
    ("darwin", "lld"): ["-fuse-ld=lld"],
    ("windows", "link"): ["/INCREMENTAL:NO"],
    ("windows", "lld"): ["-fuse-ld=lld"],
}
EXTRA_LD = {
    ("linux", "mold", "riscv64"): ["-Wl,--no-relax"],
    ("darwin", "lld", "aarch64"): ["-Wl,-dead_strip_dylibs"],
}


def _triple_case(dest, triples):
    targets = [tgt(f"{o[0]}{ln}_{a}", a, o, ln) for o, ln, a in triples]
    cfg = stage(dest, {"ldflags": ["-Lp"]}, targets)
    arts = [
        plain(
            f"{o[0]}{ln}_{a}",
            INJECT[a],
            ["-Lp", *PROFILE_LD[(o, ln)], *EXTRA_LD.get((o, ln, a), [])],
            triple=f"{a}-{o}-{ln}",
        )
        for o, ln, a in triples
    ]
    assert_success_bytes(
        cfg, art(*sorted(arts, key=lambda x: x["id"])), WORK / f"{dest}.json"
    )


def test_profile_extras_every_pair_x86_64():
    """No legal os/linker pair on x86_64 takes a Profile extra."""
    _triple_case("pxt1", [(o, ln, "x86_64") for o, ln in PROFILE_LD])


def test_profile_extras_every_pair_aarch64():
    """On aarch64 only darwin/lld takes its extra; linux/mold does not."""
    _triple_case("pxt2", [(o, ln, "aarch64") for o, ln in PROFILE_LD])


def test_profile_extras_every_pair_riscv64():
    """On riscv64 only linux/mold takes its extra; darwin/lld and linux/lld do not."""
    _triple_case("pxt3", [(o, ln, "riscv64") for o, ln in PROFILE_LD])


def test_profile_extras_near_miss_triples():
    """Triples one field away from an extra row, including darwin/lld/riscv64, take no extra."""
    _triple_case(
        "pxt4",
        [
            ("darwin", "lld", "riscv64"),
            ("linux", "mold", "aarch64"),
            ("linux", "lld", "riscv64"),
            ("darwin", "lld", "x86_64"),
            ("linux", "mold", "x86_64"),
            ("windows", "lld", "aarch64"),
            ("darwin", "ld64", "aarch64"),
        ],
    )


def test_r_refusal_keeps_existing_out():
    """A refusal leaves bytes already at --out exactly as they were; it neither truncates nor removes them."""
    cfg = stage("kex1", {}, [tgt("a", linker="ld64")])
    assert_refuse_keeps_existing(
        cfg, "refuse: unknown platform", WORK / "kex1.json", b"KEEP\n"
    )


def test_r_refusal_after_valid_target_keeps_existing_out():
    """A toolchain refusal on a later target leaves an existing --out untouched, nested path included."""
    cfg = stage("kex2", {}, [tgt("a"), tgt("b", toolchain="missing")])
    assert_refuse_keeps_existing(
        cfg, "refuse: bad toolchain", WORK / "kex2" / "sub" / "out.json", b"KEEP\n"
    )


def test_success_replaces_longer_existing_out():
    """A success replaces a longer existing --out completely; no old bytes remain after the new content."""
    cfg = stage("kex3", {}, [tgt("a")])
    assert_success_over_existing(
        cfg, art(plain("a")), WORK / "kex3.json", b"X" * 4096 + b"\n"
    )


def test_success_replaces_existing_out_multi_target():
    """Two targets resolved over an existing longer --out yield exactly the new artifact list."""
    cfg = stage(
        "kex4",
        {"env": {"E": "1"}},
        [tgt("b", toolchain="ci"), tgt("a")],
        toolchains__ci={},
    )
    want = art(plain("a", env={"E": "1"}), plain("b", env={"E": "1"}, tc=True))
    assert_success_over_existing(cfg, want, WORK / "kex4" / "out.json", b"{" * 9000)


def _refuse_targets_raw(
    dest, targets_text, msg="refuse: bad targets", project="{}", **files
):
    body = {"project.json": project, "targets.json": targets_text}
    for name, obj in files.items():
        body[name.replace("__", "/") + ".json"] = json.dumps(obj)
    cfg = stage_config(body, WORK / dest)
    assert_refuse(cfg, msg, WORK / f"{dest}.out")


def test_r_targets_null_value():
    """A null targets value is not an array and refuses; it is not read as an empty list."""
    _refuse_targets_raw("tnv1", '{"targets": null}')


def test_r_targets_null_value_with_layers():
    """targets null refuses even when overlays and toolchains that would otherwise apply are present."""
    _refuse_targets_raw(
        "tnv2",
        '{"targets": null}',
        project='{"cflags": ["-O2"]}',
        overlays__linux={"cflags": ["-x"]},
        toolchains__ci={},
    )


def test_r_targets_null_element():
    """A null element is a non-object element and refuses; it is not skipped."""
    _refuse_targets_raw("tne1", '{"targets": [null]}')


def test_r_targets_null_element_after_valid():
    """A null element after a valid target refuses and leaves no output."""
    _refuse_targets_raw("tne2", json.dumps({"targets": [tgt("a"), None]}))


def test_r_targets_false_element():
    """A false element is a non-object element and refuses; falsy elements are not skipped."""
    _refuse_targets_raw("tne3", json.dumps({"targets": [tgt("a"), False]}))


def test_r_targets_empty_array_element():
    """An empty-array element is a non-object element and refuses."""
    _refuse_targets_raw("tne4", json.dumps({"targets": [[], tgt("a")]}))


def test_r_targets_arch_null():
    """A null arch is not a string and refuses; it does not default to any architecture."""
    _refuse_targets_raw(
        "tan1", '{"targets":[{"id":"a","arch":null,"os":"linux","linker":"bfd"}]}'
    )


def test_r_targets_arch_null_second_target():
    """A null arch on a second target refuses after a valid first target."""
    _refuse_targets_raw(
        "tan2",
        json.dumps(
            {
                "targets": [
                    tgt("a"),
                    {"id": "b", "arch": None, "os": "darwin", "linker": "lld"},
                ]
            }
        ),
    )


def test_r_targets_override_cflags_null():
    """An override cflags of null is not an array of strings and refuses; it is not read as empty."""
    _refuse_targets_raw(
        "toc1", json.dumps({"targets": [tgt("a", override={"cflags": None})]})
    )


def test_r_targets_override_cflags_null_item():
    """An override cflags array holding null is not an array of strings and refuses."""
    _refuse_targets_raw(
        "toc2",
        json.dumps(
            {
                "targets": [
                    tgt("a"),
                    tgt("b", toolchain="ci", override={"cflags": ["-g", None]}),
                ]
            }
        ),
        toolchains__ci={},
    )


def test_r_targets_override_cflags_null_beside_maps():
    """An override with cflags null and valid maps, on a second target with a toolchain, refuses."""
    _refuse_targets_raw(
        "toc3",
        json.dumps(
            {
                "targets": [
                    tgt("a", "aarch64", "darwin", "lld"),
                    tgt(
                        "b",
                        toolchain="ci",
                        override={"cflags": None, "env": {"A": "1"}},
                    ),
                ]
            }
        ),
        toolchains__ci={"cflags": ["-t"]},
    )


def test_r_project_cflags_null_item():
    """A project cflags array holding null has a non-string element and refuses."""
    _refuse_targets_raw(
        "pcn1",
        json.dumps({"targets": [tgt("a")]}),
        msg="refuse: bad project",
        project='{"cflags": ["-O2", null]}',
    )


def test_r_targets_dup_id_nonadjacent():
    """A duplicate id separated by another target refuses; uniqueness is over the whole array."""
    _refuse_targets_raw("dna1", json.dumps({"targets": [tgt("a"), tgt("b"), tgt("a")]}))


def test_r_targets_dup_id_far_apart_keeps_existing_out():
    """A duplicate id four entries apart refuses and leaves an existing --out untouched."""
    targets = [
        tgt("a"),
        tgt("b", "aarch64", "linux", "lld"),
        tgt("c"),
        tgt("d", os_name="windows", linker="link"),
        tgt("a", "riscv64"),
    ]
    cfg = stage("dna2", {}, targets)
    assert_refuse_keeps_existing(
        cfg, "refuse: bad targets", WORK / "dna2.json", b"KEEP\n"
    )


def test_delete_string_flag_kept_from_project():
    """A $delete element in project cflags and ldflags is an ordinary flag and stays in the artifact."""
    cfg = stage("dsf1", {"cflags": ["$delete"], "ldflags": ["$delete"]}, [tgt("a")])
    assert_success_bytes(
        cfg, art(plain("a", ["$delete", "-m64"], ["$delete", *BFD])), WORK / "dsf1.json"
    )


def test_delete_string_flag_kept_from_layers():
    """$delete flags from overlay, toolchain and override are kept, and an adjacent pair compacts to one."""
    cfg = stage(
        "dsf2",
        {"env": {"E": "$delete"}},
        [tgt("a", toolchain="tc", override={"cflags": ["$delete", "$delete"]})],
        overlays__linux={"cflags": ["$delete"]},
        toolchains__tc={"ldflags": ["$delete"]},
    )
    want = art(plain("a", ["$delete", "-m64", "$delete"], [*BFD, "$delete"], tc=True))
    assert_success_bytes(cfg, want, WORK / "dsf2.json")


def test_delete_string_flag_kept_from_arch_overlay():
    """A $delete ldflags element from the darwin_aarch64 overlay is kept ahead of the profile and extra."""
    cfg = stage(
        "dsf3",
        {},
        [tgt("a", "aarch64", "darwin", "lld")],
        overlays__darwin_aarch64={"ldflags": ["$delete"]},
    )
    want = art(
        plain(
            "a",
            ["-march=armv8-a"],
            ["$delete", "-fuse-ld=lld", "-Wl,-dead_strip_dylibs"],
            triple="aarch64-darwin-lld",
        )
    )
    assert_success_bytes(cfg, want, WORK / "dsf3.json")


def test_empty_configuration_exact_bytes():
    """The empty configuration writes exactly the bytes the contract shows, with one LF."""
    cfg = stage("emp1", {}, [])
    out = _opaque(WORK / "emp1" / "out.json")
    proc = run_orch(cfg, out)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == b""
    assert out.read_bytes() == b'{\n  "artifacts": []\n}\n'


def test_r_targets_bad_arch_with_illegal_pair():
    """arch mips with linux/ld64 is a bad target: unknown platform needs all three fields permitted first."""
    _refuse_targets_raw(
        "bap1", '{"targets":[{"id":"a","arch":"mips","os":"linux","linker":"ld64"}]}'
    )


def test_r_targets_bad_arch_with_illegal_pair_after_valid():
    """arch mips with windows/bfd after a valid target refuses as bad targets, not unknown platform."""
    _refuse_targets_raw(
        "bap2",
        json.dumps(
            {
                "targets": [
                    tgt("a"),
                    {"id": "b", "arch": "mips", "os": "windows", "linker": "bfd"},
                ]
            }
        ),
    )


def test_r_targets_array_arch_with_illegal_pair():
    """A non-string arch with linux/ld64 refuses as bad targets before any pair check."""
    _refuse_targets_raw(
        "bap3", '{"targets":[{"id":"a","arch":[],"os":"linux","linker":"ld64"}]}'
    )


def test_r_targets_null_arch_with_illegal_pair():
    """A null arch with windows/bfd refuses as bad targets before any pair check."""
    _refuse_targets_raw(
        "bap4", '{"targets":[{"id":"a","arch":null,"os":"windows","linker":"bfd"}]}'
    )


@pytest.mark.parametrize(
    "case_name",
    (
        sorted(
            (
                p.name
                for p in SEALED.iterdir()
                if p.is_dir() and (p / "expect.json").is_file()
            )
        )
        if SEALED.is_dir()
        else []
    ),
)
def test_sealed_success(case_name: str):
    """Sealed configurations: held-out inputs must produce the artifact list minted from the reference."""
    case = SEALED / case_name
    expected = (case / "expect.json").read_text()
    tag = hashlib.sha256(case_name.encode()).hexdigest()[:16]
    cfg = stage_sealed(case, WORK / f"sc_{tag}")
    assert_success_bytes(cfg, expected, WORK / f"so_{tag}" / "out.json")
