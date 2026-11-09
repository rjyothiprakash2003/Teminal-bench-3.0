#!/usr/bin/env python3
"""Verifier for stateful-parser-recovery.

Snapshot sealed expects into memory, remove them from disk, and run
`/app/src/quill.py` unprivileged with setpriv. Visible literals decide contested
fault-machine rules; sealed sources scale the same rules on inputs the agent
never sees. Refusal checks plant a pre-existing `--out` sentinel so "write
nothing" means the bytes are unchanged.
"""

from __future__ import annotations

import json
import os
import resource
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

APP = Path("/app")
SRC = APP / "src" / "quill.py"
TESTS = Path("/tests")
SEALED = TESTS / "sealed"
WORK = Path("/work/out")
SANDBOX_ROOT = Path("/sandbox")

SANDBOX_UID = 2000
SANDBOX_GID = 2000
RUN_TIMEOUT = 20
OUT_SENTINEL = b"sentinel\n"


def _snapshot_sealed() -> dict[str, dict[str, bytes]]:
    out: dict[str, dict[str, bytes]] = {}
    for case in sorted(p for p in SEALED.iterdir() if p.is_dir()):
        out[case.name] = {
            "input": (case / "input.quill").read_bytes(),
            "expect": (case / "expect.json").read_bytes(),
        }
    return out


SEALED_SNAP = _snapshot_sealed()
SEALED_CASES = sorted(SEALED_SNAP)


def _prepare_sandbox() -> None:
    assert SRC.is_file() and not SRC.is_symlink()
    SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)
    os.chown(SANDBOX_ROOT, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    try:
        os.chmod(APP, 0o711)
        os.chmod(SRC.parent, 0o711)
        os.chmod(SRC, 0o755)
    except OSError:
        pass


_prepare_sandbox()
shutil.rmtree(SEALED)
os.chmod(TESTS, 0o700)
WORK.mkdir(parents=True, exist_ok=True)
os.chown(WORK, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)


def _apply_resource_limits() -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (RUN_TIMEOUT + 5, RUN_TIMEOUT + 5))
    resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024 * 1024, 32 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    if hasattr(resource, "RLIMIT_NPROC"):
        resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))


def _live_sandbox_pids() -> list[int]:
    alive = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != SANDBOX_UID:
                continue
            with (entry / "stat").open("rb") as handle:
                state = handle.read().rsplit(b")", 1)[1].split()[0]
        except (OSError, IndexError):
            continue
        if state != b"Z":
            alive.append(int(entry.name))
    return alive


def _drain_sandbox(deadline: float = 5.0) -> None:
    waited = 0.0
    while waited < deadline:
        if not _live_sandbox_pids():
            return
        time.sleep(0.02)
        waited += 0.02
    for pid in _live_sandbox_pids():
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _scrub_sandbox_scratch() -> None:
    if not SANDBOX_ROOT.is_dir():
        return
    for path in sorted(SANDBOX_ROOT.rglob("*"), reverse=True):
        try:
            if path.is_symlink() or path.is_file():
                path.unlink(missing_ok=True)
            elif path.is_dir():
                try:
                    path.rmdir()
                except OSError:
                    pass
        except OSError:
            pass


def _vs(name: str) -> Path:
    return WORK / name


def stage_src(text: str | bytes, dest: Path) -> Path:
    if dest.exists():
        if dest.is_dir():
            shutil.rmtree(dest)
        else:
            dest.unlink()
    dest.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        dest.write_bytes(text)
    else:
        raw = text.encode("utf-8")
        if raw and not raw.endswith(b"\n"):
            raw += b"\n"
        dest.write_bytes(raw)
    os.chown(dest.parent, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    os.chown(dest, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    return dest


def materialize_sealed(name: str, dest: Path) -> Path:
    snap = SEALED_SNAP[name]
    return stage_src(snap["input"], dest)


def read_out_bytes(out: Path) -> bytes:
    if out.is_symlink():
        raise AssertionError(f"output is a symlink: {out}")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(out, flags)
    try:
        chunks: list[bytes] = []
        while True:
            block = os.read(fd, 65536)
            if not block:
                break
            chunks.append(block)
        return b"".join(chunks)
    finally:
        os.close(fd)


def run_quill(
    src: Path, out: Path, *, clear_out: bool = True, extra_args: list[str] | None = None
) -> subprocess.CompletedProcess[bytes]:
    if clear_out and (out.exists() or out.is_symlink()):
        out.unlink()
    if out.parent.exists():
        os.chown(out.parent, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    env = {
        "HOME": "/sandbox",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "PYTHONHASHSEED": "0",
        "PYTHONSAFEPATH": "1",
        "TZ": "UTC",
    }
    command = [
        "/usr/bin/setpriv",
        "--reuid",
        str(SANDBOX_UID),
        "--regid",
        str(SANDBOX_GID),
        "--clear-groups",
        "--no-new-privs",
        "--",
        "python3",
        str(SRC),
        "--src",
        str(src),
        "--out",
        str(out),
    ]
    if extra_args:
        command.extend(extra_args)
    return subprocess.run(
        command,
        capture_output=True,
        timeout=RUN_TIMEOUT,
        env=env,
        cwd="/sandbox",
        preexec_fn=_apply_resource_limits,
    )


def assert_report_bytes(src: Path, expected: bytes, out: Path) -> None:
    proc = run_quill(src, out, clear_out=True)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == b""
    got = read_out_bytes(out)
    if got != expected:
        n = min(len(got), len(expected))
        idx = next((i for i in range(n) if got[i] != expected[i]), n)
        raise AssertionError(
            f"--out mismatch: got {len(got)} bytes, expected {len(expected)} bytes; "
            f"first difference at offset {idx}"
        )
    _drain_sandbox()


def _run_raw(args: list[str], out: Path) -> subprocess.CompletedProcess[bytes]:
    out.parent.mkdir(parents=True, exist_ok=True)
    os.chown(out.parent, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    if out.exists() or out.is_symlink():
        out.unlink()
    out.write_bytes(OUT_SENTINEL)
    os.chown(out, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    env = {
        "HOME": "/sandbox",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "PYTHONHASHSEED": "0",
        "PYTHONSAFEPATH": "1",
        "TZ": "UTC",
    }
    command = [
        "/usr/bin/setpriv",
        "--reuid",
        str(SANDBOX_UID),
        "--regid",
        str(SANDBOX_GID),
        "--clear-groups",
        "--no-new-privs",
        "--",
        "python3",
        str(SRC),
        *args,
    ]
    proc = subprocess.run(
        command,
        capture_output=True,
        timeout=RUN_TIMEOUT,
        env=env,
        cwd="/sandbox",
        preexec_fn=_apply_resource_limits,
    )
    assert proc.returncode == 2, (proc.returncode, proc.stderr)
    assert proc.stdout == b""
    assert proc.stderr == b"refuse: bad args\n"
    assert out.exists() and not out.is_symlink()
    assert read_out_bytes(out) == OUT_SENTINEL
    _drain_sandbox()
    return proc




def _env() -> dict[str, str]:
    return {
        "HOME": "/sandbox",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "PYTHONHASHSEED": "0",
        "PYTHONSAFEPATH": "1",
        "TZ": "UTC",
    }


def _run_cli(args: list[str]) -> subprocess.CompletedProcess[bytes]:
    command = [
        "/usr/bin/setpriv",
        "--reuid",
        str(SANDBOX_UID),
        "--regid",
        str(SANDBOX_GID),
        "--clear-groups",
        "--no-new-privs",
        "--",
        "python3",
        str(SRC),
        *args,
    ]
    return subprocess.run(
        command,
        capture_output=True,
        timeout=RUN_TIMEOUT,
        env=_env(),
        cwd="/sandbox",
        preexec_fn=_apply_resource_limits,
    )


def assert_refuse_bad_args(args: list[str], out: Path | None = None, *, expect_absent: bool = False) -> None:
    """Refuse bad args: exact stderr bytes, empty stdout, exit 2, out untouched/absent."""
    if out is not None and not expect_absent:
        out.parent.mkdir(parents=True, exist_ok=True)
        os.chown(out.parent, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    # Snapshot output kind BEFORE the candidate runs (do not trust post-run shape).
    was_symlink = False
    link_target: Path | None = None
    if out is not None and not expect_absent and out.is_symlink():
        was_symlink = True
        link_target = Path(os.readlink(out))
        if not link_target.is_absolute():
            link_target = out.parent / link_target
    proc = _run_cli(args)
    assert proc.returncode == 2, (proc.returncode, proc.stderr, proc.stdout)
    assert proc.stdout == b""
    assert proc.stderr == b"refuse: bad args\n"
    if out is not None:
        if expect_absent:
            assert not out.exists() and not out.is_symlink()
        elif was_symlink:
            # Candidate must leave the symlink in place and not follow/mutate the target.
            assert out.is_symlink()
            assert link_target is not None
            assert link_target.read_bytes() == OUT_SENTINEL
        else:
            assert out.exists() and not out.is_symlink()
            assert read_out_bytes(out) == OUT_SENTINEL
    _drain_sandbox()


def assert_refuse_bad_source(src: Path, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    os.chown(out.parent, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    if out.exists() or out.is_symlink():
        out.unlink()
    out.write_bytes(OUT_SENTINEL)
    os.chown(out, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    proc = run_quill(src, out, clear_out=False)
    assert proc.returncode == 2
    assert proc.stdout == b""
    assert proc.stderr == b"refuse: bad source\n"
    assert read_out_bytes(out) == OUT_SENTINEL
    _drain_sandbox()


def expect_json(obj: dict) -> bytes:
    return (json.dumps(obj, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def test_quill_source_present():
    """Graded invocations use the documented /app/src/quill.py path."""
    assert SRC.is_file() and not SRC.is_symlink()
    assert SRC.resolve() == Path("/app/src/quill.py").resolve()


def test_visible_ok_simple_bytes():
    """Balanced UNIT with one VAL use matches the contract dump."""
    src = stage_src(
        "open UNIT root\nbind VAL x 1\nuse x\nend UNIT\n",
        _vs("s03a.quill"),
    )
    expected = expect_json(
        {
            "status": "ok",
            "frames": [],
            "bindings": [
                {"frame": "root", "kind": "VAL", "name": "x", "value": 1, "line": 2}
            ],
            "uses": [{"name": "x", "value": 1, "line": 3}],
        }
    )
    assert_report_bytes(src, expected, _vs("s03a.json"))


def test_visible_ok_simple_second_site():
    """Second site for the ordinary success dump shape (not the shipped example)."""
    src = stage_src(
        "open UNIT cell\nbind VAL n 7\nuse n\nend UNIT\n",
        _vs("s03b.quill"),
    )
    expected = expect_json(
        {
            "status": "ok",
            "frames": [],
            "bindings": [
                {
                    "frame": "cell",
                    "kind": "VAL",
                    "name": "n",
                    "value": 7,
                    "line": 2,
                }
            ],
            "uses": [{"name": "n", "value": 7, "line": 3}],
        }
    )
    assert_report_bytes(src, expected, _vs("s03b.json"))


def test_visible_eof_nest_beats_later_form():
    """Still-open header nest beats a later FORM inside the frame."""
    src = stage_src("open UNIT root\nbind VAL\n", _vs("v01a.quill"))
    expected = expect_json({"status": "fault", "fault": "nest", "line": 1})
    assert_report_bytes(src, expected, _vs("v01a.json"))


def test_visible_eof_nest_beats_later_form_b():
    """Second site: bad verb inside an open UNIT loses to EOF nest."""
    src = stage_src("open UNIT root\nnope x\n", _vs("v01b.quill"))
    expected = expect_json({"status": "fault", "fault": "nest", "line": 1})
    assert_report_bytes(src, expected, _vs("v01b.json"))


def test_visible_failed_open_does_not_push():
    """Malformed open does not push; EOF nest stays on the outer header."""
    src = stage_src("open UNIT root\nopen BLOCK\n", _vs("v02a.quill"))
    expected = expect_json({"status": "fault", "fault": "nest", "line": 1})
    assert_report_bytes(src, expected, _vs("v02a.json"))


def test_visible_failed_open_does_not_push_b():
    """Second site: arity-short open alone is FORM with empty stack."""
    src = stage_src("open UNIT\n", _vs("v02b.quill"))
    expected = expect_json({"status": "fault", "fault": "form", "line": 1})
    assert_report_bytes(src, expected, _vs("v02b.json"))


def test_visible_closer_mismatch_skips_eof_nest():
    """Closer kind mismatch nests on the closer line and suppresses EOF nest."""
    src = stage_src(
        "open UNIT root\nopen BLOCK c\nend UNIT\n",
        _vs("v03a.quill"),
    )
    expected = expect_json({"status": "fault", "fault": "nest", "line": 3})
    assert_report_bytes(src, expected, _vs("v03a.json"))


def test_visible_closer_mismatch_skips_eof_nest_b():
    """Second site: empty-stack end is nest on that line."""
    src = stage_src("end UNIT\n", _vs("v03b.quill"))
    expected = expect_json({"status": "fault", "fault": "nest", "line": 1})
    assert_report_bytes(src, expected, _vs("v03b.json"))


def test_visible_dup_cross_kind():
    """VAL then ALIAS on the same name is DUP; end still closes after DUP."""
    src = stage_src(
        "open UNIT root\nbind VAL x 1\nbind ALIAS x y\nend UNIT\n",
        _vs("v04a.quill"),
    )
    expected = expect_json({"status": "fault", "fault": "dup", "line": 3})
    assert_report_bytes(src, expected, _vs("v04a.json"))


def test_visible_dup_cross_kind_b():
    """Second site: ALIAS then VAL on the same name is DUP."""
    src = stage_src(
        "open UNIT root\nbind VAL y 3\nbind ALIAS x y\nbind VAL x 9\nend UNIT\n",
        _vs("v04b.quill"),
    )
    expected = expect_json({"status": "fault", "fault": "dup", "line": 4})
    assert_report_bytes(src, expected, _vs("v04b.json"))


def test_visible_unbound_then_close():
    """UNBOUND does not halt end, so EOF nest does not steal the unbound line."""
    src = stage_src(
        "open UNIT root\nuse missing\nend UNIT\n",
        _vs("v05a.quill"),
    )
    expected = expect_json({"status": "fault", "fault": "unbound", "line": 2})
    assert_report_bytes(src, expected, _vs("v05a.json"))


def test_visible_unbound_then_close_b():
    """Second site: child binding is invisible after the child closes."""
    src = stage_src(
        "open UNIT root\n"
        "open BLOCK c\n"
        "bind VAL only 1\n"
        "end BLOCK\n"
        "use only\n"
        "end UNIT\n",
        _vs("v05b.quill"),
    )
    expected = expect_json({"status": "fault", "fault": "unbound", "line": 5})
    assert_report_bytes(src, expected, _vs("v05b.json"))


def test_visible_alias_same_frame_only():
    """ALIAS target must be a VAL in the same frame, not a parent VAL."""
    src = stage_src(
        "open UNIT root\n"
        "bind VAL a 10\n"
        "open BLOCK inner\n"
        "bind ALIAS b a\n"
        "use b\n"
        "end BLOCK\n"
        "end UNIT\n",
        _vs("s02a.quill"),
    )
    expected = expect_json({"status": "fault", "fault": "unbound", "line": 5})
    assert_report_bytes(src, expected, _vs("s02a.json"))


def test_visible_alias_same_frame_ok():
    """Second site: same-frame ALIAS resolves."""
    src = stage_src(
        "open UNIT root\nbind VAL a 10\nbind ALIAS b a\nuse b\nend UNIT\n",
        _vs("s02b.quill"),
    )
    expected = expect_json(
        {
            "status": "ok",
            "frames": [],
            "bindings": [
                {"frame": "root", "kind": "VAL", "name": "a", "value": 10, "line": 2},
                {
                    "frame": "root",
                    "kind": "ALIAS",
                    "name": "b",
                    "value": "a",
                    "line": 3,
                },
            ],
            "uses": [{"name": "b", "value": 10, "line": 4}],
        }
    )
    assert_report_bytes(src, expected, _vs("s02b.json"))


def test_visible_name_max_ok():
    """Sixteen ASCII bytes is an accepted name length."""
    name = "a" * 16
    src = stage_src(
        f"open UNIT root\nbind VAL {name} 1\nuse {name}\nend UNIT\n",
        _vs("name_max.quill"),
    )
    expected = expect_json(
        {
            "status": "ok",
            "frames": [],
            "bindings": [
                {
                    "frame": "root",
                    "kind": "VAL",
                    "name": name,
                    "value": 1,
                    "line": 2,
                }
            ],
            "uses": [{"name": name, "value": 1, "line": 3}],
        }
    )
    assert_report_bytes(src, expected, _vs("name_max.json"))


def test_visible_name_max_plus_one():
    """Seventeen ASCII bytes is FORM after a balanced prefix."""
    name = "a" * 17
    src = stage_src(
        f"open UNIT root\nbind VAL x 1\nend UNIT\nbind VAL {name} 1\n",
        _vs("name_over.quill"),
    )
    expected = expect_json({"status": "fault", "fault": "form", "line": 4})
    assert_report_bytes(src, expected, _vs("name_over.json"))


def test_visible_name_non_ascii_form():
    """Non-ASCII name bytes are FORM (byte alphabet, not rune count)."""
    src = stage_src(
        "open UNIT root\nbind VAL x 1\nend UNIT\nbind VAL café 1\n",
        _vs("name_utf.quill"),
    )
    expected = expect_json({"status": "fault", "fault": "form", "line": 4})
    assert_report_bytes(src, expected, _vs("name_utf.json"))


@pytest.mark.parametrize("extra", [64, 128])
def test_visible_bind_extra_arity_leaves_out(extra: int):
    """Extra bind operands are FORM; pre-existing --out is overwritten on success path only.

    Here the run succeeds as a CLI process with a fault report; --out is written.
    """
    tokens = " ".join(["x"] * extra)
    text = f"open UNIT root\nbind VAL x 1 {tokens}\nend UNIT\n"
    # After FORM on bind, end does not apply → EOF nest beats form.
    src = stage_src(text, _vs(f"arity_{extra}.quill"))
    expected = expect_json({"status": "fault", "fault": "nest", "line": 1})
    assert_report_bytes(src, expected, _vs(f"arity_{extra}.json"))


def test_visible_bind_extra_arity_top_level():
    """Large extra arity after a balanced prefix is FORM on that line."""
    tokens = " ".join(["z"] * 64)
    src = stage_src(
        f"open UNIT root\nbind VAL x 1\nend UNIT\nbind VAL y 2 {tokens}\n",
        _vs("arity_top.quill"),
    )
    expected = expect_json({"status": "fault", "fault": "form", "line": 4})
    assert_report_bytes(src, expected, _vs("arity_top.json"))


def test_visible_bind_extra_arity_top_level_b():
    """Second large extra-arity site (128)."""
    tokens = " ".join(["z"] * 128)
    src = stage_src(
        f"open UNIT root\nbind VAL x 1\nend UNIT\nbind VAL y 2 {tokens}\n",
        _vs("arity_top_b.quill"),
    )
    expected = expect_json({"status": "fault", "fault": "form", "line": 4})
    assert_report_bytes(src, expected, _vs("arity_top_b.json"))


def test_visible_unicode_whitespace_ignored():
    """EM SPACE-only lines are ignored like other str.strip whitespace."""
    src = stage_src(
        "open UNIT root\n\u2003\nbind VAL x 1\nend UNIT\n",
        _vs("ws.quill"),
    )
    expected = expect_json(
        {
            "status": "ok",
            "frames": [],
            "bindings": [
                {"frame": "root", "kind": "VAL", "name": "x", "value": 1, "line": 3}
            ],
            "uses": [],
        }
    )
    assert_report_bytes(src, expected, _vs("ws.json"))


def test_visible_parent_use():
    """Child frame may use a parent VAL."""
    src = stage_src(
        "open UNIT root\n"
        "bind VAL n 7\n"
        "open BLOCK c\n"
        "use n\n"
        "end BLOCK\n"
        "end UNIT\n",
        _vs("s02c.quill"),
    )
    expected = expect_json(
        {
            "status": "ok",
            "frames": [],
            "bindings": [
                {"frame": "root", "kind": "VAL", "name": "n", "value": 7, "line": 2}
            ],
            "uses": [{"name": "n", "value": 7, "line": 4}],
        }
    )
    assert_report_bytes(src, expected, _vs("s02c.json"))


def test_drawn_success_family():
    """Expectations derived at grade time from a small generator (anti lookup-table)."""
    rows = []
    for i in range(5):
        name = f"k{i}"
        val = 100 + i
        rows.append((name, val))
    lines = ["open UNIT drawn"]
    for name, val in rows:
        lines.append(f"bind VAL {name} {val}")
    for name, val in rows:
        lines.append(f"use {name}")
    lines.append("end UNIT")
    text = "\n".join(lines) + "\n"
    bindings = [
        {
            "frame": "drawn",
            "kind": "VAL",
            "name": name,
            "value": val,
            "line": 2 + i,
        }
        for i, (name, val) in enumerate(rows)
    ]
    uses = [
        {"name": name, "value": val, "line": 2 + len(rows) + i}
        for i, (name, val) in enumerate(rows)
    ]
    expected = expect_json(
        {"status": "ok", "frames": [], "bindings": bindings, "uses": uses}
    )
    src = stage_src(text, _vs("drawn.quill"))
    assert_report_bytes(src, expected, _vs("drawn.json"))


def test_refuse_missing_src():
    """Missing --src refuses with bad args and leaves --out untouched."""
    _run_raw(["--out", str(_vs("r1.json"))], _vs("r1.json"))


def test_refuse_missing_out():
    """Missing --out refuses with bad args."""
    src = stage_src("open UNIT root\nend UNIT\n", _vs("r2.quill"))
    out = _vs("r2.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    os.chown(out.parent, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    if out.exists() or out.is_symlink():
        out.unlink()
    out.write_bytes(OUT_SENTINEL)
    os.chown(out, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    env = {
        "HOME": "/sandbox",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "PYTHONHASHSEED": "0",
        "PYTHONSAFEPATH": "1",
        "TZ": "UTC",
    }
    command = [
        "/usr/bin/setpriv",
        "--reuid",
        str(SANDBOX_UID),
        "--regid",
        str(SANDBOX_GID),
        "--clear-groups",
        "--no-new-privs",
        "--",
        "python3",
        str(SRC),
        "--src",
        str(src),
    ]
    proc = subprocess.run(
        command,
        capture_output=True,
        timeout=RUN_TIMEOUT,
        env=env,
        cwd="/sandbox",
        preexec_fn=_apply_resource_limits,
    )
    assert proc.returncode == 2
    assert proc.stdout == b""
    assert proc.stderr == b"refuse: bad args\n"
    assert read_out_bytes(out) == OUT_SENTINEL
    _drain_sandbox()


def test_refuse_src_directory():
    """Directory --src is bad args; sentinel --out survives."""
    d = _vs("srcdir")
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    os.chown(d, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    _run_raw(["--src", str(d), "--out", str(_vs("r3.json"))], _vs("r3.json"))


def test_refuse_src_symlink():
    """Symlink --src is bad args even when the target is a regular file."""
    real = stage_src("open UNIT root\nend UNIT\n", _vs("real.quill"))
    link = _vs("link.quill")
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to(real)
    os.chown(link, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    _run_raw(["--src", str(link), "--out", str(_vs("r4.json"))], _vs("r4.json"))


def test_refuse_bad_source_no_final_lf():
    """Partial final line refuses with bad source and leaves --out untouched."""
    src = _vs("r10a.quill")
    if src.exists():
        src.unlink()
    src.write_bytes(b"open UNIT root\nend UNIT")  # no trailing LF
    os.chown(src, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    out = _vs("r10a.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    os.chown(out.parent, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    if out.exists() or out.is_symlink():
        out.unlink()
    out.write_bytes(OUT_SENTINEL)
    os.chown(out, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    proc = run_quill(src, out, clear_out=False)
    assert proc.returncode == 2
    assert proc.stdout == b""
    assert proc.stderr == b"refuse: bad source\n"
    assert read_out_bytes(out) == OUT_SENTINEL
    _drain_sandbox()


def test_refuse_bad_source_second_site():
    """Second site for missing-final-LF source refusal."""
    src = _vs("r10b.quill")
    src.write_bytes(b"nope")
    os.chown(src, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    out = _vs("r10b.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    os.chown(out.parent, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    out.write_bytes(OUT_SENTINEL)
    os.chown(out, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    proc = run_quill(src, out, clear_out=False)
    assert proc.returncode == 2
    assert proc.stdout == b""
    assert proc.stderr == b"refuse: bad source\n"
    assert read_out_bytes(out) == OUT_SENTINEL
    _drain_sandbox()


def test_success_overwrites_existing_out():
    """A successful report run replaces a pre-existing --out."""
    src = stage_src(
        "open UNIT root\nbind VAL x 1\nend UNIT\n",
        _vs("ow.quill"),
    )
    out = _vs("ow.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    os.chown(out.parent, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    out.write_bytes(OUT_SENTINEL)
    os.chown(out, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    expected = expect_json(
        {
            "status": "ok",
            "frames": [],
            "bindings": [
                {"frame": "root", "kind": "VAL", "name": "x", "value": 1, "line": 2}
            ],
            "uses": [],
        }
    )
    proc = run_quill(src, out, clear_out=False)
    assert proc.returncode == 0
    assert proc.stdout == b""
    assert read_out_bytes(out) == expected
    _drain_sandbox()


# --- Panel cure: isolating fixtures for returned Majors (ASCII sep, argv, domain) ---


def test_panel_ascii_sep_nbsp_form():
    """Interior NBSP is not an ASCII space/tab separator → FORM, not ok."""
    src = stage_src("open\u00a0UNIT\u00a0x\nend UNIT\n", _vs("s01a.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "form", "line": 1}), _vs("s01a.json")
    )


def test_panel_ascii_sep_nbsp_form_b():
    """Second site: NBSP between UNIT and name → FORM."""
    src = stage_src("open UNIT\u00a0x\nend UNIT\n", _vs("s01b.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "form", "line": 1}), _vs("s01b.json")
    )


def test_panel_ascii_sep_vt_form():
    """Vertical tab is not an allowed token separator → FORM."""
    src = stage_src("open UNIT\vfoo\nend UNIT\n", _vs("s01c.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "form", "line": 1}), _vs("s01c.json")
    )


def test_panel_ascii_sep_emspace_interior():
    """Interior EM SPACE between tokens → FORM; EOF nest beats later if open left."""
    src = stage_src("open UNIT root\nbind\u2003VAL x 1\nend UNIT\n", _vs("s01d.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("s01d.json")
    )


def test_panel_tab_separators_ok():
    """ASCII tabs are legal separators and edges strip."""
    src = stage_src("open\tUNIT\troot\nend\tUNIT\n", _vs("s01e.quill"))
    assert_report_bytes(
        src,
        expect_json({"status": "ok", "frames": [], "bindings": [], "uses": []}),
        _vs("s01e.json"),
    )


def test_panel_strip_edges_ok():
    """Leading/trailing Unicode whitespace strip before tokenize."""
    src = stage_src("\u2003open UNIT root\u2003\nend UNIT\n", _vs("s01f.quill"))
    assert_report_bytes(
        src,
        expect_json({"status": "ok", "frames": [], "bindings": [], "uses": []}),
        _vs("s01f.json"),
    )


def test_panel_empty_physical_line():
    """A lone LF is an empty physical line (strip==''), not FORM."""
    src = stage_src(b"\n", _vs("empty_line.quill"))
    assert_report_bytes(
        src,
        expect_json({"status": "ok", "frames": [], "bindings": [], "uses": []}),
        _vs("empty_line.json"),
    )


def test_panel_vt_only_physical_line_lf_split():
    """Vertical-tab-only physical line is ignored; binding keeps LF line number."""
    src = stage_src(b"open UNIT root\n\v\nbind VAL x 1\nend UNIT\n", _vs("s01g.quill"))
    assert_report_bytes(
        src,
        expect_json(
            {
                "status": "ok",
                "frames": [],
                "bindings": [
                    {"frame": "root", "kind": "VAL", "name": "x", "value": 1, "line": 3}
                ],
                "uses": [],
            }
        ),
        _vs("s01g.json"),
    )


def test_panel_fault_line_after_whitespace():
    """Fault line numbers are physical, after an ignored whitespace line."""
    src = stage_src("\u2003\nnope\n", _vs("s01h.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "form", "line": 2}), _vs("s01h.json")
    )


def test_panel_use_line_after_whitespace():
    """Applied use line is physical after an ignored blank line."""
    src = stage_src("open UNIT r\n\nbind VAL x 1\nuse x\nend UNIT\n", _vs("s01i.quill"))
    assert_report_bytes(
        src,
        expect_json(
            {
                "status": "ok",
                "frames": [],
                "bindings": [
                    {"frame": "r", "kind": "VAL", "name": "x", "value": 1, "line": 3}
                ],
                "uses": [{"name": "x", "value": 1, "line": 4}],
            }
        ),
        _vs("s01i.json"),
    )


def test_panel_refuse_src_without_value():
    """`--src` followed by another flag lacks a value → refuse: bad args."""
    out = _vs("r40a.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    os.chown(out.parent, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    out.write_bytes(OUT_SENTINEL)
    os.chown(out, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    assert_refuse_bad_args(["--src", "--out", str(out)], out)


def test_panel_refuse_out_without_value():
    """Trailing `--out` without a value → refuse: bad args."""
    src = stage_src("open UNIT r\nend UNIT\n", _vs("r40b.quill"))
    out = _vs("r40b.json")
    out.write_bytes(OUT_SENTINEL)
    os.chown(out, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    assert_refuse_bad_args(["--src", str(src), "--out"], out)


def test_panel_refuse_empty_out_value():
    """Explicit empty `--out` value → refuse: bad args."""
    src = stage_src("open UNIT r\nend UNIT\n", _vs("empty_out.quill"))
    # No regular out path to plant; ensure no accidental create under work.
    ghost = _vs("should_not_exist.json")
    if ghost.exists() or ghost.is_symlink():
        ghost.unlink()
    proc = _run_cli(["--src", str(src), "--out", ""])
    assert proc.returncode == 2
    assert proc.stdout == b""
    assert proc.stderr == b"refuse: bad args\n"
    assert not ghost.exists()
    _drain_sandbox()


def test_panel_refuse_nonexistent_src():
    """Nonexistent --src → refuse: bad args."""
    out = _vs("nosrc.json")
    out.write_bytes(OUT_SENTINEL)
    os.chown(out, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    assert_refuse_bad_args(
        ["--src", str(_vs("does-not-exist.quill")), "--out", str(out)], out
    )


def test_panel_refuse_fifo_src():
    """FIFO --src is a non-regular lstat result → refuse: bad args."""
    fifo = _vs("source.fifo")
    if fifo.exists() or fifo.is_symlink():
        fifo.unlink()
    os.mkfifo(fifo)
    os.chown(fifo, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    out = _vs("fifo.json")
    out.write_bytes(OUT_SENTINEL)
    os.chown(out, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    assert_refuse_bad_args(["--src", str(fifo), "--out", str(out)], out)


def test_panel_refuse_absent_out_not_created():
    """On refusal, an absent --out must not be created."""
    d = _vs("absdir")
    if d.exists():
        shutil.rmtree(d)
    d.mkdir()
    os.chown(d, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    ghost = _vs("absent_out.json")
    if ghost.exists() or ghost.is_symlink():
        ghost.unlink()
    assert_refuse_bad_args(
        ["--src", str(d), "--out", str(ghost)], ghost, expect_absent=True
    )


def test_panel_refuse_out_symlink_not_followed():
    """On refusal, --out symlink must not be followed; target bytes stay."""
    target = _vs("sym_target.json")
    target.write_bytes(OUT_SENTINEL)
    os.chown(target, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    link = _vs("sym_out.json")
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to(target)
    os.chown(link, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    d = _vs("symdir")
    if d.exists():
        shutil.rmtree(d)
    d.mkdir()
    os.chown(d, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    assert_refuse_bad_args(["--src", str(d), "--out", str(link)], link)


def test_panel_refuse_bad_utf8_source():
    """Invalid UTF-8 with trailing LF → refuse: bad source."""
    src = _vs("r26a.quill")
    src.write_bytes(b"\xff\n")
    os.chown(src, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    out = _vs("r26a.json")
    assert_refuse_bad_source(src, out)


def test_panel_refuse_bad_utf8_second_site():
    """Second opaque basename for invalid UTF-8 refusal (not basename-keyed)."""
    src = _vs("r26b.quill")
    src.write_bytes(b"open UNIT r\xff\n")
    os.chown(src, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    out = _vs("r26b.json")
    assert_refuse_bad_source(src, out)


def test_panel_refuse_bad_utf8_absent_out():
    """Invalid UTF-8 must not create an absent --out (no append preflight before decode)."""
    src = _vs("r26c.quill")
    src.write_bytes(b"\xff\n")
    os.chown(src, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    ghost = _vs("r26c.json")
    if ghost.exists() or ghost.is_symlink():
        ghost.unlink()
    proc = run_quill(src, ghost, clear_out=False)
    assert proc.returncode == 2
    assert proc.stdout == b""
    assert proc.stderr == b"refuse: bad source\n"
    assert not ghost.exists() and not ghost.is_symlink()
    _drain_sandbox()


def test_panel_refuse_bad_source_out_symlink():
    """Bad-source refusal must leave --out symlink identity and target bytes untouched."""
    src = _vs("r36a.quill")
    src.write_bytes(b"\xff\n")
    os.chown(src, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    target = _vs("r36a_target.json")
    if target.exists() or target.is_symlink():
        target.unlink()
    target.write_bytes(OUT_SENTINEL)
    os.chown(target, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    link = _vs("r36a.json")
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to(target)
    os.chown(link, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    assert link.is_symlink()
    proc = run_quill(src, link, clear_out=False)
    assert proc.returncode == 2
    assert proc.stdout == b""
    assert proc.stderr == b"refuse: bad source\n"
    assert link.is_symlink()
    resolved = Path(os.readlink(link))
    if not resolved.is_absolute():
        resolved = link.parent / resolved
    assert resolved.resolve() == target.resolve()
    assert target.read_bytes() == OUT_SENTINEL
    _drain_sandbox()


def test_panel_case_sensitive_verb():
    """`Open` is not a verb → FORM."""
    src = stage_src("Open UNIT r\nend UNIT\n", _vs("case_v.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "form", "line": 1}), _vs("case_v.json")
    )


def test_panel_unknown_kind_open():
    """`open WIDGET` → FORM."""
    src = stage_src("open WIDGET r\nend BLOCK\n", _vs("kind.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "form", "line": 1}), _vs("kind.json")
    )


def test_panel_junk_prefix_verb():
    """Unanchored verb match must not accept `junk open ...`."""
    src = stage_src("junk open UNIT r\nend UNIT\n", _vs("junk.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "form", "line": 1}), _vs("junk.json")
    )


def test_panel_open_extra_arity():
    """Extra open operand → FORM."""
    src = stage_src("open UNIT r extra\nend UNIT\n", _vs("open_x.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "form", "line": 1}), _vs("open_x.json")
    )


def test_panel_end_extra_arity_leaves_stack():
    """Malformed end does not pop; EOF nest at open beats FORM."""
    src = stage_src("open UNIT root\nend\n", _vs("end_x.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("end_x.json")
    )


def test_panel_use_extra_arity():
    """Extra use operand → FORM then EOF nest."""
    src = stage_src(
        "open UNIT r\nbind VAL x 1\nuse x extra\nend UNIT\n", _vs("use_x.quill")
    )
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("use_x.json")
    )


def test_panel_name_invalid_inside_frame():
    """Invalid bind name inside an open frame → FORM then EOF nest."""
    src = stage_src("open UNIT r\nbind VAL café 1\nend UNIT\n", _vs("name_in.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("name_in.json")
    )


def test_panel_frame_name_underscore_ok():
    """Underscore-only frame name is legal."""
    src = stage_src("open UNIT _\nend UNIT\n", _vs("under.quill"))
    assert_report_bytes(
        src,
        expect_json({"status": "ok", "frames": [], "bindings": [], "uses": []}),
        _vs("under.json"),
    )


def test_panel_alias_target_too_long():
    """Overlong ALIAS target → FORM then EOF nest."""
    long = "a" * 17
    src = stage_src(
        f"open UNIT r\nbind ALIAS alias {long}\nend UNIT\n", _vs("alias_long.quill")
    )
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("alias_long.json")
    )


def test_panel_int_plus_inside_frame():
    """`+3` inside an open frame → FORM then EOF nest."""
    src = stage_src("open UNIT r\nbind VAL x +3\nend UNIT\n", _vs("plus_in.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("plus_in.json")
    )


def test_panel_int_neg_zero_ok():
    """`-0` is legal and stores 0."""
    src = stage_src("open UNIT r\nbind VAL x -0\nuse x\nend UNIT\n", _vs("neg0.quill"))
    assert_report_bytes(
        src,
        expect_json(
            {
                "status": "ok",
                "frames": [],
                "bindings": [
                    {"frame": "r", "kind": "VAL", "name": "x", "value": 0, "line": 2}
                ],
                "uses": [{"name": "x", "value": 0, "line": 3}],
            }
        ),
        _vs("neg0.json"),
    )


def test_panel_int_boundary_ok():
    """2^53-1 is legal; exact integer must round-trip."""
    n = 9007199254740991
    src = stage_src(
        f"open UNIT r\nbind VAL n {n}\nuse n\nend UNIT\n", _vs("big.quill")
    )
    assert_report_bytes(
        src,
        expect_json(
            {
                "status": "ok",
                "frames": [],
                "bindings": [
                    {"frame": "r", "kind": "VAL", "name": "n", "value": n, "line": 2}
                ],
                "uses": [{"name": "n", "value": n, "line": 3}],
            }
        ),
        _vs("big.json"),
    )


def test_panel_int_boundary_form():
    """2^53 is out of range → FORM then EOF nest."""
    src = stage_src(
        "open UNIT r\nbind VAL x 9007199254740992\nend UNIT\n", _vs("bigf.quill")
    )
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("bigf.json")
    )


def test_panel_int_neg_leading_zero():
    """`-01` is FORM inside an open frame."""
    src = stage_src("open UNIT r\nbind VAL x -01\nend UNIT\n", _vs("n01.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("n01.json")
    )


def test_panel_bind_alias_empty_stack():
    """Empty-stack ALIAS bind is FORM, not UNBOUND."""
    src = stage_src("bind ALIAS x y\n", _vs("alias_empty.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "form", "line": 1}), _vs("alias_empty.json")
    )


def test_panel_end_block_empty_stack():
    """Empty-stack `end BLOCK` is NEST."""
    src = stage_src("end BLOCK\n", _vs("end_b.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("end_b.json")
    )


def test_panel_dup_alias_alias():
    """ALIAS then ALIAS on the same name is DUP."""
    src = stage_src(
        "open UNIT r\nbind ALIAS a x\nbind ALIAS a y\nend UNIT\n", _vs("s04a.quill")
    )
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "dup", "line": 3}), _vs("s04a.json")
    )


def test_panel_dup_identical_val_payload():
    """Same-frame VAL with identical payload is still DUP (not idempotent)."""
    src = stage_src(
        "open UNIT r\nbind VAL x 1\nbind VAL x 1\nend UNIT\n", _vs("r45a.quill")
    )
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "dup", "line": 3}), _vs("r45a.json")
    )


def test_panel_dup_identical_alias_payload():
    """Same-frame ALIAS with identical target is still DUP (not idempotent)."""
    src = stage_src(
        "open UNIT r\nbind ALIAS a missing\nbind ALIAS a missing\nend UNIT\n",
        _vs("r45b.quill"),
    )
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "dup", "line": 3}), _vs("r45b.json")
    )


def test_panel_dup_suppresses_later_open():
    """After DUP, later open is ignored; end BLOCK mismatches → DUP wins."""
    src = stage_src(
        "open UNIT r\nbind VAL x 1\nbind VAL x 2\nopen BLOCK c\nend BLOCK\n",
        _vs("q17f.quill"),
    )
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "dup", "line": 3}), _vs("q17f.json")
    )


def test_panel_dup_block_end_applies():
    """Matching BLOCK end still applies after DUP."""
    src = stage_src(
        "open BLOCK r\nbind VAL x 1\nbind VAL x 2\nend BLOCK\n", _vs("s04b.quill")
    )
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "dup", "line": 3}), _vs("s04b.json")
    )


def test_panel_alias_shadow_stops_outer():
    """Inner unusable ALIAS stops lookup; does not fall through to outer VAL."""
    src = stage_src(
        "open UNIT r\nbind VAL a 1\nopen BLOCK c\nbind ALIAS a missing\nuse a\nend BLOCK\nend UNIT\n",
        _vs("s04c.quill"),
    )
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "unbound", "line": 5}), _vs("s04c.json")
    )


def test_panel_alias_chain_one_step():
    """ALIAS→ALIAS is UNBOUND (one-step only)."""
    src = stage_src(
        "open UNIT r\nbind VAL v 7\nbind ALIAS b v\nbind ALIAS a b\nuse a\nend UNIT\n",
        _vs("s04d.quill"),
    )
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "unbound", "line": 5}), _vs("s04d.json")
    )


def test_panel_unbound_allows_later_open():
    """UNBOUND does not halt; later open/close still run; EOF nest beats unbound."""
    src = stage_src(
        "open UNIT r\nuse missing\nopen BLOCK c\nend BLOCK\n", _vs("q17e.quill")
    )
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("q17e.json")
    )


def test_panel_parent_alias_use():
    """Child may use a parent ALIAS that resolves in the parent frame."""
    src = stage_src(
        "open UNIT root\nbind VAL a 1\nbind ALIAS b a\nopen BLOCK c\nuse b\nend BLOCK\nend UNIT\n",
        _vs("s04e.quill"),
    )
    assert_report_bytes(
        src,
        expect_json(
            {
                "status": "ok",
                "frames": [],
                "bindings": [
                    {"frame": "root", "kind": "VAL", "name": "a", "value": 1, "line": 2},
                    {
                        "frame": "root",
                        "kind": "ALIAS",
                        "name": "b",
                        "value": "a",
                        "line": 3,
                    },
                ],
                "uses": [{"name": "b", "value": 1, "line": 5}],
            }
        ),
        _vs("s04e.json"),
    )


def test_panel_eof_nest_after_unbound():
    """Unclosed UNBOUND still gets EOF nest at outermost header."""
    src = stage_src("open UNIT r\nuse missing\n", _vs("q17a.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("q17a.json")
    )


def test_panel_eof_nest_after_dup():
    """Unclosed DUP still gets EOF nest at outermost header."""
    src = stage_src("open UNIT r\nbind VAL x 1\nbind VAL x 2\n", _vs("q17b.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("q17b.json")
    )


def test_panel_eof_nest_outermost_of_many():
    """Multiple still-open frames → nest at outermost header."""
    src = stage_src("open UNIT r\nopen BLOCK c\n", _vs("q17c.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("q17c.json")
    )


def test_panel_unbound_beats_later_nest():
    """Earlier UNBOUND beats later closer NEST when mismatch suppresses EOF."""
    src = stage_src("open UNIT r\nuse missing\nend BLOCK\n", _vs("q17d.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "unbound", "line": 2}), _vs("q17d.json")
    )


def test_panel_out_creates_parent_dirs():
    """Success creates missing parent directories of --out."""
    src = stage_src("open UNIT root\nend UNIT\n", _vs("mkdir.quill"))
    out = _vs("new") / "report.json"
    if out.parent.exists():
        shutil.rmtree(out.parent)
    assert_report_bytes(
        src,
        expect_json({"status": "ok", "frames": [], "bindings": [], "uses": []}),
        out,
    )



def test_panel_refuse_empty_src_value():
    """Explicit empty `--src` value → refuse: bad args."""
    out = _vs("empty_src.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    os.chown(out.parent, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    if out.exists() or out.is_symlink():
        out.unlink()
    out.write_bytes(OUT_SENTINEL)
    os.chown(out, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    assert_refuse_bad_args(["--src", "", "--out", str(out)], out)


def test_panel_refuse_bad_source_absent_out():
    """Bad-source refusal must not create an absent --out."""
    src = _vs("r10c.quill")
    src.write_bytes(b"nope")
    os.chown(src, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    ghost = _vs("r10c.json")
    if ghost.exists() or ghost.is_symlink():
        ghost.unlink()
    proc = run_quill(src, ghost, clear_out=False)
    assert proc.returncode == 2
    assert proc.stdout == b""
    assert proc.stderr == b"refuse: bad source\n"
    assert not ghost.exists() and not ghost.is_symlink()
    _drain_sandbox()


def test_panel_repeated_ascii_separators_ok():
    """Adjacent ASCII spaces collapse; no empty tokens."""
    src = stage_src("open  UNIT  r\nend  UNIT\n", _vs("multi_sp.quill"))
    assert_report_bytes(
        src,
        expect_json({"status": "ok", "frames": [], "bindings": [], "uses": []}),
        _vs("multi_sp.json"),
    )


def test_panel_nbsp_whitespace_only_line():
    """NBSP-only physical line is ignored under full str.strip()."""
    src = stage_src("\u00a0\n", _vs("s01j.quill"))
    assert_report_bytes(
        src,
        expect_json({"status": "ok", "frames": [], "bindings": [], "uses": []}),
        _vs("s01j.json"),
    )


def test_panel_nbsp_statement_edges_ok():
    """NBSP at statement edges strips; interior ASCII separators remain."""
    src = stage_src("\u00a0open UNIT r\u00a0\nend UNIT\n", _vs("s01k.quill"))
    assert_report_bytes(
        src,
        expect_json({"status": "ok", "frames": [], "bindings": [], "uses": []}),
        _vs("s01k.json"),
    )


def test_panel_open_block_extra_arity():
    """Extra operand on BLOCK open → FORM."""
    src = stage_src("open BLOCK b extra\nend BLOCK\n", _vs("open_bx.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "form", "line": 1}), _vs("open_bx.json")
    )


def test_panel_end_unit_extra_arity():
    """Trailing operand on bare `end UNIT` → FORM."""
    src = stage_src("end UNIT extra\n", _vs("end_ux.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "form", "line": 1}), _vs("end_ux.json")
    )


def test_panel_end_block_extra_arity():
    """Trailing operand on bare `end BLOCK` → FORM."""
    src = stage_src("end BLOCK extra\n", _vs("end_bx.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "form", "line": 1}), _vs("end_bx.json")
    )


def test_panel_end_unit_extra_leaves_open():
    """Malformed `end UNIT extra` does not pop; EOF nest wins."""
    src = stage_src("open UNIT r\nend UNIT extra\n", _vs("end_ux2.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("end_ux2.json")
    )


def test_panel_alias_extra_arity():
    """Extra ALIAS operand → FORM then EOF nest."""
    src = stage_src(
        "open UNIT r\nbind ALIAS a b extra\nend UNIT\n", _vs("alias_x.quill")
    )
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("alias_x.json")
    )


def test_panel_bind_val_short_arity():
    """Abbreviated VAL bind → FORM then EOF nest."""
    src = stage_src("open UNIT r\nbind VAL x\nend UNIT\n", _vs("bind_short.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("bind_short.json")
    )


def test_panel_use_bare_form():
    """Operand-free `use` → FORM."""
    src = stage_src("use\n", _vs("use_bare.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "form", "line": 1}), _vs("use_bare.json")
    )


def test_panel_unknown_kind_end():
    """`end WIDGET` is FORM (not defaulted to BLOCK)."""
    src = stage_src("end WIDGET\n", _vs("end_kind.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "form", "line": 1}), _vs("end_kind.json")
    )


def test_panel_unknown_kind_end_leaves_open():
    """`end WIDGET` after open is FORM; EOF nest selected."""
    src = stage_src("open UNIT r\nend WIDGET\nend UNIT\n", _vs("end_kind2.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("end_kind2.json")
    )


def test_panel_unknown_kind_bind():
    """`bind REF` is FORM; EOF nest selected."""
    src = stage_src("open UNIT r\nbind REF a b\nend UNIT\n", _vs("bind_kind.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("bind_kind.json")
    )


def test_panel_digit_leading_name_ok():
    """Digit-only frame name is legal."""
    src = stage_src("open UNIT 1\nend UNIT\n", _vs("digit_name.quill"))
    assert_report_bytes(
        src,
        expect_json({"status": "ok", "frames": [], "bindings": [], "uses": []}),
        _vs("digit_name.json"),
    )


def test_panel_frame_name_non_ascii():
    """Non-ASCII frame name → FORM."""
    src = stage_src("open UNIT café\nend UNIT\n", _vs("frame_utf.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "form", "line": 1}), _vs("frame_utf.json")
    )


def test_panel_use_name_non_ascii():
    """Non-ASCII use name → FORM then EOF nest."""
    src = stage_src("open UNIT r\nuse café\nend UNIT\n", _vs("use_utf.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("use_utf.json")
    )


def test_panel_alias_target_non_ascii():
    """Non-ASCII ALIAS target → FORM then EOF nest."""
    src = stage_src(
        "open UNIT r\nbind ALIAS a café\nend UNIT\n", _vs("alias_utf.quill")
    )
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("alias_utf.json")
    )


def test_panel_frame_name_too_long():
    """17-byte frame name → FORM."""
    n = "a" * 17
    src = stage_src(f"open UNIT {n}\nend UNIT\n", _vs("frame_long.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "form", "line": 1}), _vs("frame_long.json")
    )


def test_panel_bind_name_too_long_inside():
    """17-byte bind name inside an open frame → FORM then EOF nest."""
    n = "a" * 17
    src = stage_src(f"open UNIT r\nbind VAL {n} 1\nend UNIT\n", _vs("bind_long.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("bind_long.json")
    )


def test_panel_use_name_too_long():
    """17-byte use name → FORM then EOF nest."""
    n = "a" * 17
    src = stage_src(f"open UNIT r\nuse {n}\nend UNIT\n", _vs("use_long.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("use_long.json")
    )


def test_panel_alias_target_len16_ok():
    """Length-16 ALIAS target is legal."""
    n = "a" * 16
    src = stage_src(
        f"open UNIT r\nbind VAL {n} 1\nbind ALIAS b {n}\nuse b\nend UNIT\n",
        _vs("alias_t16.quill"),
    )
    assert_report_bytes(
        src,
        expect_json(
            {
                "status": "ok",
                "frames": [],
                "bindings": [
                    {"frame": "r", "kind": "VAL", "name": n, "value": 1, "line": 2},
                    {"frame": "r", "kind": "ALIAS", "name": "b", "value": n, "line": 3},
                ],
                "uses": [{"name": "b", "value": 1, "line": 4}],
            }
        ),
        _vs("alias_t16.json"),
    )


def test_panel_int_unicode_digit_form():
    """Unicode decimal digit in integer token → FORM then EOF nest."""
    src = stage_src("open UNIT r\nbind VAL x 1٢\nend UNIT\n", _vs("uni_digit.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("uni_digit.json")
    )


def test_panel_int_leading_zero_inside():
    """Positive leading-zero `01` inside a frame → FORM then EOF nest."""
    src = stage_src("open UNIT r\nbind VAL x 01\nend UNIT\n", _vs("pos01.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("pos01.json")
    )


def test_panel_int_neg_boundary_form():
    """Excluded negative endpoint -2^53 → FORM then EOF nest."""
    src = stage_src(
        "open UNIT r\nbind VAL x -9007199254740992\nend UNIT\n", _vs("negn.quill")
    )
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("negn.json")
    )


def test_panel_eof_nest_after_leading_blank():
    """EOF nest reports physical header line after ignored blank."""
    src = stage_src("\nopen UNIT r\n", _vs("q18a.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 2}), _vs("q18a.json")
    )


def test_panel_alias_resolve_at_use_time():
    """ALIAS may precede its VAL target in the same frame; resolve at use."""
    src = stage_src(
        "open UNIT r\nbind ALIAS b a\nbind VAL a 1\nuse b\nend UNIT\n",
        _vs("q19a.quill"),
    )
    assert_report_bytes(
        src,
        expect_json(
            {
                "status": "ok",
                "frames": [],
                "bindings": [
                    {"frame": "r", "kind": "ALIAS", "name": "b", "value": "a", "line": 2},
                    {"frame": "r", "kind": "VAL", "name": "a", "value": 1, "line": 3},
                ],
                "uses": [{"name": "b", "value": 1, "line": 4}],
            }
        ),
        _vs("q19a.json"),
    )


def test_panel_eof_nest_current_stack_not_first_open():
    """EOF nest uses the still-open stack bottom, not the first open in the file."""
    src = stage_src("open UNIT a\nend UNIT\nopen UNIT b\n", _vs("q20a.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 3}), _vs("q20a.json")
    )


def test_panel_out_creates_parent_dirs_fault():
    """Fault reports also create missing parent directories of --out."""
    src = stage_src("nope\n", _vs("mkdir_f.quill"))
    out = _vs("new-fault") / "report.json"
    if out.parent.exists():
        shutil.rmtree(out.parent)
    assert_report_bytes(
        src,
        expect_json({"status": "fault", "fault": "form", "line": 1}),
        out,
    )



def test_panel_form_then_mismatch_suppresses_eof():
    """After FORM, a later mismatching closer sets closer_mismatch; FORM wins over EOF nest."""
    src = stage_src("open UNIT u\nbogus\nend BLOCK\n", _vs("q24a.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "form", "line": 2}), _vs("q24a.json")
    )


def test_panel_cr_inside_physical_line_form():
    """CR is not a line or token separator; one physical LF line stays FORM."""
    src = stage_src(b"open UNIT r\rend UNIT\n", _vs("q04a.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "form", "line": 1}), _vs("q04a.json")
    )


def test_panel_refuse_whitespace_no_final_lf():
    """Whitespace-only partial line without final LF → refuse: bad source."""
    src = _vs("r10d.quill")
    src.write_bytes(b" ")
    os.chown(src, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    out = _vs("r10d.json")
    assert_refuse_bad_source(src, out)


def test_panel_alias_short_arity():
    """Short `bind ALIAS a` → FORM then EOF nest."""
    src = stage_src("open UNIT r\nbind ALIAS a\nend UNIT\n", _vs("q06a.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("q06a.json")
    )


def test_panel_open_block_short_arity():
    """Short `open BLOCK` → FORM."""
    src = stage_src("open BLOCK\n", _vs("q23a.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "form", "line": 1}), _vs("q23a.json")
    )


def test_panel_hyphen_name_form():
    """Hyphen is not a legal name character → FORM."""
    src = stage_src("open UNIT a-b\nend UNIT\n", _vs("q07a.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "form", "line": 1}), _vs("q07a.json")
    )


def test_panel_alias_decl_name_too_long():
    """Overlong ALIAS declaration identifier → FORM then EOF nest."""
    n = "a" * 17
    src = stage_src(
        f"open UNIT r\nbind ALIAS {n} x\nend UNIT\n", _vs("q09a.quill")
    )
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("q09a.json")
    )


def test_panel_int_float_form():
    """Floating-point token `1.5` → FORM then EOF nest."""
    src = stage_src("open UNIT r\nbind VAL x 1.5\nend UNIT\n", _vs("q10a.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("q10a.json")
    )


def test_panel_int_neg_boundary_ok():
    """Legal lower adjacent -2^53+1 round-trips exactly."""
    n = -9007199254740991
    src = stage_src(
        f"open UNIT r\nbind VAL x {n}\nuse x\nend UNIT\n", _vs("q11a.quill")
    )
    assert_report_bytes(
        src,
        expect_json(
            {
                "status": "ok",
                "frames": [],
                "bindings": [
                    {"frame": "r", "kind": "VAL", "name": "x", "value": n, "line": 2}
                ],
                "uses": [{"name": "x", "value": n, "line": 3}],
            }
        ),
        _vs("q11a.json"),
    )


def test_panel_unit_closer_pops_only_one():
    """Matching `end UNIT` pops only the innermost frame, not the whole stack."""
    src = stage_src(
        "open BLOCK b\nopen UNIT u\nend UNIT\nbind VAL x 1\nend BLOCK\n",
        _vs("q12a.quill"),
    )
    assert_report_bytes(
        src,
        expect_json(
            {
                "status": "ok",
                "frames": [],
                "bindings": [
                    {"frame": "b", "kind": "VAL", "name": "x", "value": 1, "line": 4}
                ],
                "uses": [],
            }
        ),
        _vs("q12a.json"),
    )


def test_panel_malformed_block_end_leaves_open():
    """Malformed `end BLOCK extra` on nonempty stack does not pop."""
    src = stage_src("open BLOCK b\nend BLOCK extra\n", _vs("q13a.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("q13a.json")
    )


def test_panel_form_halts_matching_block_end():
    """FORM freezes a BLOCK; matching end is not applied → EOF nest."""
    src = stage_src("open BLOCK b\nnope\nend BLOCK\n", _vs("q14a.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("q14a.json")
    )


def test_panel_same_name_different_frames_ok():
    """Bindings collide by frame identity, not by shared frame name."""
    src = stage_src(
        "open UNIT r\nbind VAL x 1\nopen BLOCK r\nbind VAL x 2\nend BLOCK\nend UNIT\n",
        _vs("q15a.quill"),
    )
    assert_report_bytes(
        src,
        expect_json(
            {
                "status": "ok",
                "frames": [],
                "bindings": [
                    {"frame": "r", "kind": "VAL", "name": "x", "value": 1, "line": 2},
                    {"frame": "r", "kind": "VAL", "name": "x", "value": 2, "line": 4},
                ],
                "uses": [],
            }
        ),
        _vs("q15a.json"),
    )


def test_panel_parent_alias_ignores_child_shadow():
    """Parent ALIAS resolves in the defining frame, not the using child's shadow."""
    src = stage_src(
        "open UNIT r\nbind VAL a 1\nbind ALIAS b a\nopen BLOCK c\nbind VAL a 2\nuse b\nend BLOCK\nend UNIT\n",
        _vs("q16a.quill"),
    )
    assert_report_bytes(
        src,
        expect_json(
            {
                "status": "ok",
                "frames": [],
                "bindings": [
                    {"frame": "r", "kind": "VAL", "name": "a", "value": 1, "line": 2},
                    {"frame": "r", "kind": "ALIAS", "name": "b", "value": "a", "line": 3},
                    {"frame": "c", "kind": "VAL", "name": "a", "value": 2, "line": 5},
                ],
                "uses": [{"name": "b", "value": 1, "line": 6}],
            }
        ),
        _vs("q16a.json"),
    )


def test_panel_unbound_allows_later_bind_dup():
    """UNBOUND does not halt binds; later DUP + mismatch → UNBOUND wins."""
    src = stage_src(
        "open UNIT r\nuse missing\nbind VAL x 1\nbind VAL x 2\nopen BLOCK c\nend BLOCK\n",
        _vs("q17g.quill"),
    )
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "unbound", "line": 2}), _vs("q17g.json")
    )


def test_panel_unbound_beats_later_form():
    """Earlier UNBOUND beats later FORM when mismatch suppresses EOF nest."""
    src = stage_src("open UNIT r\nuse missing\nnope\nend BLOCK\n", _vs("q18b.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "unbound", "line": 2}), _vs("q18b.json")
    )


def test_panel_nnbsp_whitespace_only():
    """NARROW NO-BREAK SPACE (U+202F) is str.strip whitespace."""
    src = stage_src("\u202f\n", _vs("q22a.quill"))
    assert_report_bytes(
        src,
        expect_json({"status": "ok", "frames": [], "bindings": [], "uses": []}),
        _vs("q22a.json"),
    )


def test_panel_nnbsp_statement_edges():
    """U+202F at statement edges strips under full str.strip()."""
    src = stage_src("\u202fopen UNIT r\u202f\nend UNIT\n", _vs("q22b.quill"))
    assert_report_bytes(
        src,
        expect_json({"status": "ok", "frames": [], "bindings": [], "uses": []}),
        _vs("q22b.json"),
    )


def test_panel_nel_whitespace_only():
    """U+0085 (NEXT LINE) is Python str.strip() whitespace; blank line → empty ok."""
    # Distinguishes full str.strip from ASCII-or-Unicode-Z-category trimming.
    src = stage_src(b"\xc2\x85\n", _vs("r41a.quill"))
    assert_report_bytes(
        src,
        expect_json({"status": "ok", "frames": [], "bindings": [], "uses": []}),
        _vs("r41a.json"),
    )


def test_panel_nel_statement_edges():
    """U+0085 at statement edges strips under full str.strip()."""
    src = stage_src("\u0085open UNIT r\u0085\nend UNIT\n", _vs("r41b.quill"))
    assert_report_bytes(
        src,
        expect_json({"status": "ok", "frames": [], "bindings": [], "uses": []}),
        _vs("r41b.json"),
    )


def test_panel_ogham_space_whitespace_only():
    """OGHAM SPACE MARK (U+1680) is Python str.strip() whitespace."""
    src = stage_src("\u1680\n", _vs("r46a.quill"))
    assert_report_bytes(
        src,
        expect_json({"status": "ok", "frames": [], "bindings": [], "uses": []}),
        _vs("r46a.json"),
    )


def test_panel_nel_interior_not_line_separator():
    """Interior U+0085 stays on one LF-delimited physical line → FORM (not splitlines)."""
    src = stage_src("open UNIT r\u0085end UNIT\n", _vs("r47a.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "form", "line": 1}), _vs("r47a.json")
    )


def test_panel_digit_leading_bind_use_alias():
    """Digit-leading bind names, ALIAS targets, and use names are legal."""
    src = stage_src(
        "open UNIT r\nbind VAL 1 7\nbind ALIAS a 1\nuse a\nuse 1\nend UNIT\n",
        _vs("s40a.quill"),
    )
    assert_report_bytes(
        src,
        expect_json(
            {
                "status": "ok",
                "frames": [],
                "bindings": [
                    {"frame": "r", "kind": "VAL", "name": "1", "value": 7, "line": 2},
                    {"frame": "r", "kind": "ALIAS", "name": "a", "value": "1", "line": 3},
                ],
                "uses": [
                    {"name": "a", "value": 7, "line": 4},
                    {"name": "1", "value": 7, "line": 5},
                ],
            }
        ),
        _vs("s40a.json"),
    )


def test_panel_frame_name_len16_ok():
    """Length-16 frame name is accepted."""
    n = "a" * 16
    src = stage_src(f"open UNIT {n}\nend UNIT\n", _vs("s41a.quill"))
    assert_report_bytes(
        src,
        expect_json({"status": "ok", "frames": [], "bindings": [], "uses": []}),
        _vs("s41a.json"),
    )


def test_panel_alias_decl_len16_ok():
    """Length-16 ALIAS declaration identifier is accepted."""
    n = "a" * 16
    src = stage_src(
        f"open UNIT r\nbind VAL x 1\nbind ALIAS {n} x\nuse {n}\nend UNIT\n",
        _vs("s41b.quill"),
    )
    assert_report_bytes(
        src,
        expect_json(
            {
                "status": "ok",
                "frames": [],
                "bindings": [
                    {"frame": "r", "kind": "VAL", "name": "x", "value": 1, "line": 2},
                    {"frame": "r", "kind": "ALIAS", "name": n, "value": "x", "line": 3},
                ],
                "uses": [{"name": n, "value": 1, "line": 4}],
            }
        ),
        _vs("s41b.json"),
    )


def test_panel_int_underscore_form():
    """Embedded underscore integer `1_0` is FORM (not Python int)."""
    src = stage_src("open UNIT r\nbind VAL x 1_0\nend UNIT\n", _vs("s42a.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("s42a.json")
    )


def test_panel_int_beyond_endpoint_form():
    """Value beyond +2^53 (not merely equal) is FORM."""
    src = stage_src(
        "open UNIT r\nbind VAL x 9007199254740993\nend UNIT\n", _vs("s43a.quill")
    )
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("s43a.json")
    )


def test_panel_form_suppresses_later_open():
    """FORM freezes opens: later open ignored; mismatch closer → FORM wins."""
    src = stage_src("open UNIT r\nnope\nopen BLOCK c\nend BLOCK\n", _vs("s44a.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "form", "line": 2}), _vs("s44a.json")
    )


def test_panel_malformed_open_no_speculative_push():
    """Malformed nested open must not push; later closer mismatches UNIT → FORM."""
    src = stage_src("open UNIT r\nopen BLOCK\nend BLOCK\n", _vs("s45a.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "form", "line": 2}), _vs("s45a.json")
    )


def test_panel_dup_pop_only_inner():
    """After DUP, matching end pops only innermost; outer remains → EOF nest wins."""
    src = stage_src(
        "open UNIT r\nopen BLOCK c\nbind VAL x 1\nbind VAL x 2\nend BLOCK\n",
        _vs("s46a.quill"),
    )
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("s46a.json")
    )


def test_panel_use_before_bind_unbound():
    """Lookup uses bindings already applied at the use line, not later binds."""
    src = stage_src("open UNIT r\nuse x\nbind VAL x 1\nend UNIT\n", _vs("s47a.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "unbound", "line": 2}), _vs("s47a.json")
    )


def test_panel_alias_use_before_target_unbound():
    """ALIAS use before its VAL target is applied → UNBOUND (not deferred)."""
    src = stage_src(
        "open UNIT r\nbind ALIAS b a\nuse b\nbind VAL a 1\nend UNIT\n",
        _vs("s47b.quill"),
    )
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "unbound", "line": 3}), _vs("s47b.json")
    )


def test_panel_two_unbound_earliest_line():
    """Two UNBOUND uses: pick keeps the earlier line (not a last-wins slot)."""
    src = stage_src("open UNIT r\nuse a\nuse b\nend UNIT\n", _vs("r43a.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "unbound", "line": 2}), _vs("r43a.json")
    )


def test_panel_three_unbound_earliest_line():
    """Three UNBOUND uses: earliest line still wins (second site)."""
    src = stage_src("open UNIT r\nuse x\nuse y\nuse z\nend UNIT\n", _vs("r43b.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "unbound", "line": 2}), _vs("r43b.json")
    )


def test_panel_long_statement_line_ok():
    """Valid nonblank statement beyond a 32768-char physical-line buffer still succeeds."""
    # "open" + 32759 spaces + "UNIT r" = 4+32759+6 = 32769 chars; source 32779 bytes
    line1 = "open" + (" " * 32759) + "UNIT r"
    assert len(line1) == 32769
    body = line1 + "\nend UNIT\n"
    assert len(body.encode("utf-8")) == 32779
    assert len(body.encode("utf-8")) < 65536
    src = stage_src(body, _vs("s48a.quill"))
    assert_report_bytes(
        src,
        expect_json({"status": "ok", "frames": [], "bindings": [], "uses": []}),
        _vs("s48a.json"),
    )



def test_panel_long_source_past_32k_form():
    """Substantive source past a 32KiB parse buffer: trailing verb is FORM, not empty-ok."""
    body = (" " * 32768) + "x\n"
    assert len(body.encode("utf-8")) == 32770
    assert len(body.encode("utf-8")) < 65536
    src = stage_src(body, _vs("r23a.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "form", "line": 1}), _vs("r23a.json")
    )


def test_panel_max_bytes_substantive_form():
    """Inclusive 65536-byte domain with a nonblank statement → FORM (not whitespace-only)."""
    body = (" " * 65534) + "x\n"
    assert len(body.encode("utf-8")) == 65536
    src = stage_src(body, _vs("r23b.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "form", "line": 1}), _vs("r23b.json")
    )


def test_panel_dup_beats_later_form():
    """Earlier DUP beats later FORM under earliest-line pick."""
    src = stage_src(
        "open UNIT r\nbind VAL x 1\nbind VAL x 2\nend UNIT\nbogus\n",
        _vs("s49a.quill"),
    )
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "dup", "line": 3}), _vs("s49a.json")
    )



def test_panel_refuse_char_device_src():
    """Character device --src (/dev/null) → refuse: bad args (S_ISREG required)."""
    out = _vs("r11a.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    os.chown(out.parent, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    if out.exists() or out.is_symlink():
        out.unlink()
    out.write_bytes(OUT_SENTINEL)
    os.chown(out, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    assert_refuse_bad_args(["--src", "/dev/null", "--out", str(out)], out)


def test_panel_refuse_unix_socket_src():
    """Unix-domain socket --src is a non-regular lstat result → refuse: bad args."""
    import socket

    sock_path = _vs("r11b.sock")
    if sock_path.exists() or sock_path.is_symlink():
        sock_path.unlink()
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        srv.bind(str(sock_path))
    finally:
        srv.close()
    os.chown(sock_path, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    out = _vs("r11b.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    os.chown(out.parent, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    if out.exists() or out.is_symlink():
        out.unlink()
    out.write_bytes(OUT_SENTINEL)
    os.chown(out, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    assert_refuse_bad_args(["--src", str(sock_path), "--out", str(out)], out)


def test_panel_refuse_block_device_src():
    """Block-device --src → refuse: bad args (S_ISREG required, not a deny-list)."""
    import stat as stmod
    import subprocess

    out = _vs("r11c.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    os.chown(out.parent, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    if out.exists() or out.is_symlink():
        out.unlink()
    out.write_bytes(OUT_SENTINEL)
    os.chown(out, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)

    payload = b" " * 511 + b"\n"
    img = _vs("r11c.img")
    img.write_bytes(payload)
    os.chown(img, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)

    blk_path = None
    attached = False
    # Prefer a readable loop device so a deny-list that reads would see whitespace.
    try:
        proc = subprocess.run(
            ["losetup", "-f", "--show", str(img)],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0:
            cand = proc.stdout.strip()
            if cand and stmod.S_ISBLK(os.lstat(cand).st_mode):
                blk_path = cand
                attached = True
    except OSError:
        pass

    mknod_path = _vs("r11c.blk")
    if blk_path is None:
        if mknod_path.exists() or mknod_path.is_symlink():
            mknod_path.unlink()
        os.mknod(mknod_path, 0o666 | stmod.S_IFBLK, os.makedev(7, 200))
        os.chmod(mknod_path, 0o666)
        assert stmod.S_ISBLK(os.lstat(mknod_path).st_mode)
        blk_path = str(mknod_path)

    try:
        assert_refuse_bad_args(["--src", blk_path, "--out", str(out)], out)
    finally:
        if attached:
            subprocess.run(["losetup", "-d", blk_path], capture_output=True, check=False)
        if mknod_path.exists() or mknod_path.is_symlink():
            mknod_path.unlink()


def test_panel_bom_not_stripped_form():
    """Leading UTF-8 BOM (U+FEFF) is not stripped; statement is FORM."""
    src = stage_src(b"\xef\xbb\xbf\n", _vs("r12a.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "form", "line": 1}), _vs("r12a.json")
    )


def test_panel_file_separator_whitespace_only():
    """U+001C is Python str.strip() whitespace; blank line → empty ok."""
    src = stage_src(b"\x1c\n", _vs("r13a.quill"))
    assert_report_bytes(
        src,
        expect_json({"status": "ok", "frames": [], "bindings": [], "uses": []}),
        _vs("r13a.json"),
    )


def test_panel_uppercase_frame_name_ok():
    """Uppercase frame names are preserved in binding records (no case folding)."""
    src = stage_src("open UNIT A\nbind VAL x 1\nend UNIT\n", _vs("r14a.quill"))
    assert_report_bytes(
        src,
        expect_json(
            {
                "status": "ok",
                "frames": [],
                "bindings": [
                    {"frame": "A", "kind": "VAL", "name": "x", "value": 1, "line": 2}
                ],
                "uses": [],
            }
        ),
        _vs("r14a.json"),
    )


def test_panel_uppercase_bind_use_preserved():
    """Uppercase bind/use names are preserved exactly (no lowercase canonicalization)."""
    src = stage_src("open UNIT r\nbind VAL X 1\nuse X\nend UNIT\n", _vs("r14d.quill"))
    assert_report_bytes(
        src,
        expect_json(
            {
                "status": "ok",
                "frames": [],
                "bindings": [
                    {"frame": "r", "kind": "VAL", "name": "X", "value": 1, "line": 2}
                ],
                "uses": [{"name": "X", "value": 1, "line": 3}],
            }
        ),
        _vs("r14d.json"),
    )


def test_panel_underscore_bind_use_alias_ok():
    """Underscore-only names are legal for bind, ALIAS target, and use."""
    src = stage_src(
        "open UNIT r\nbind VAL _ 1\nbind ALIAS a _\nuse a\nuse _\nend UNIT\n",
        _vs("r14b.quill"),
    )
    assert_report_bytes(
        src,
        expect_json(
            {
                "status": "ok",
                "frames": [],
                "bindings": [
                    {"frame": "r", "kind": "VAL", "name": "_", "value": 1, "line": 2},
                    {"frame": "r", "kind": "ALIAS", "name": "a", "value": "_", "line": 3},
                ],
                "uses": [
                    {"name": "a", "value": 1, "line": 4},
                    {"name": "_", "value": 1, "line": 5},
                ],
            }
        ),
        _vs("r14b.json"),
    )


def test_panel_hyphen_bind_name_form():
    """Hyphen in a bind name is FORM (not isascii()-only acceptance)."""
    src = stage_src("open UNIT r\nbind VAL a-b 1\nend UNIT\n", _vs("r14c.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("r14c.json")
    )


def test_panel_int_double_zero_form():
    """Repeated-zero literal `00` is FORM (not a permissive zero branch)."""
    src = stage_src("open UNIT r\nbind VAL x 00\nend UNIT\n", _vs("r15a.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("r15a.json")
    )


def test_panel_int_bare_minus_form():
    """Bare `-` is not a legal integer → FORM then EOF nest."""
    src = stage_src("open UNIT r\nbind VAL x -\nend UNIT\n", _vs("r15b.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("r15b.json")
    )


def test_panel_int_double_minus_form():
    """Repeated leading minus `--1` is FORM (not lstrip-all-minuses) → nest@1."""
    src = stage_src("open UNIT r\nbind VAL x --1\nend UNIT\n", _vs("r42a.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("r42a.json")
    )


def test_panel_int_double_minus_zero_form():
    """Repeated leading minus `--0` is FORM → nest@1 (second site)."""
    src = stage_src("open UNIT r\nbind VAL x --0\nend UNIT\n", _vs("r42b.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("r42b.json")
    )


def test_panel_alias_same_name_different_frames_ok():
    """ALIAS names collide per frame, not globally across the file."""
    src = stage_src(
        "open UNIT r\nbind ALIAS a missing\nopen BLOCK c\nbind ALIAS a missing\nend BLOCK\nend UNIT\n",
        _vs("r16a.quill"),
    )
    assert_report_bytes(
        src,
        expect_json(
            {
                "status": "ok",
                "frames": [],
                "bindings": [
                    {
                        "frame": "r",
                        "kind": "ALIAS",
                        "name": "a",
                        "value": "missing",
                        "line": 2,
                    },
                    {
                        "frame": "c",
                        "kind": "ALIAS",
                        "name": "a",
                        "value": "missing",
                        "line": 4,
                    },
                ],
                "uses": [],
            }
        ),
        _vs("r16a.json"),
    )


def test_panel_grandparent_lookup_ok():
    """Lookup walks nine frames (rejects an eight-innermost-frame budget)."""
    opens = "".join(f"open BLOCK c{i}\n" for i in range(1, 9))
    closes = "".join("end BLOCK\n" for _ in range(8))
    body = (
        "open UNIT r\nbind VAL x 7\n"
        + opens
        + "use x\n"
        + closes
        + "end UNIT\n"
    )
    assert body.count("\n") == 20
    src = stage_src(body, _vs("r17a.quill"))
    assert_report_bytes(
        src,
        expect_json(
            {
                "status": "ok",
                "frames": [],
                "bindings": [
                    {"frame": "r", "kind": "VAL", "name": "x", "value": 7, "line": 2}
                ],
                "uses": [{"name": "x", "value": 7, "line": 11}],
            }
        ),
        _vs("r17a.json"),
    )



def test_panel_lowercase_kind_form():
    """Kind keywords are case-sensitive: `unit` is not `UNIT` → FORM."""
    src = stage_src("open unit r\nend UNIT\n", _vs("r18a.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "form", "line": 1}), _vs("r18a.json")
    )


def test_panel_hyphen_use_name_form():
    """Hyphen in a use operand is FORM (alphabet, not isascii-only)."""
    src = stage_src("open UNIT r\nuse a-b\nend UNIT\n", _vs("r19a.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("r19a.json")
    )


def test_panel_hyphen_alias_name_form():
    """Hyphen in an ALIAS identifier is FORM."""
    src = stage_src("open UNIT r\nbind ALIAS a-b x\nend UNIT\n", _vs("r19b.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("r19b.json")
    )


def test_panel_hyphen_alias_target_form():
    """Hyphen in an ALIAS target is FORM."""
    src = stage_src("open UNIT r\nbind ALIAS a x-y\nend UNIT\n", _vs("r19c.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("r19c.json")
    )



def test_panel_mixed_space_tab_separators_ok():
    """Adjacent space+tab separators collapse; no empty tokens."""
    src = stage_src("open \tUNIT r\nend UNIT\n", _vs("r20a.quill"))
    assert_report_bytes(
        src,
        expect_json({"status": "ok", "frames": [], "bindings": [], "uses": []}),
        _vs("r20a.json"),
    )


def test_panel_int_integral_decimal_form():
    """Integral decimal spelling `1.0` is not an integer literal → FORM then nest."""
    src = stage_src("open UNIT r\nbind VAL x 1.0\nend UNIT\n", _vs("r21a.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("r21a.json")
    )


def test_panel_int_scientific_form():
    """Scientific spelling `1e0` is not an integer literal → FORM then nest."""
    src = stage_src("open UNIT r\nbind VAL x 1e0\nend UNIT\n", _vs("r21b.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("r21b.json")
    )


def test_panel_int_below_neg_bound_form():
    """Value strictly below -(2^53) is FORM then EOF nest."""
    src = stage_src(
        "open UNIT r\nbind VAL x -9007199254740993\nend UNIT\n", _vs("r22a.quill")
    )
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("r22a.json")
    )



def test_panel_nested_unit_closer_pops_one():
    """`end UNIT` pops only the innermost UNIT, not every UNIT below it."""
    src = stage_src("open UNIT a\nopen UNIT b\nend UNIT\n", _vs("r24a.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("r24a.json")
    )


def test_panel_verb_only_open_form():
    """Bare `open` with no operands is FORM (arity before operand indexing)."""
    src = stage_src("open\n", _vs("r25a.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "form", "line": 1}), _vs("r25a.json")
    )


def test_panel_verb_only_bind_form():
    """Bare `bind` with no operands is FORM (arity before operand indexing)."""
    src = stage_src("bind\n", _vs("r25b.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "form", "line": 1}), _vs("r25b.json")
    )



def test_panel_lowercase_bind_kind_form():
    """`bind val` is not `bind VAL` → FORM then EOF nest."""
    src = stage_src("open UNIT r\nbind val x 1\nend UNIT\n", _vs("r27a.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("r27a.json")
    )


def test_panel_lowercase_end_kind_form():
    """`end unit` is not `end UNIT` → FORM then EOF nest."""
    src = stage_src("open UNIT r\nend unit\n", _vs("r27b.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("r27b.json")
    )


def test_panel_lowercase_block_kind_form():
    """`open block` is not `open BLOCK` → FORM."""
    src = stage_src("open block r\nend BLOCK\n", _vs("r27c.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "form", "line": 1}), _vs("r27c.json")
    )


def test_panel_lowercase_alias_kind_form():
    """`bind alias` is not `bind ALIAS` → FORM then EOF nest."""
    src = stage_src("open UNIT r\nbind alias a x\nend UNIT\n", _vs("r27d.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("r27d.json")
    )


def test_panel_alias_decl_nonascii_form():
    """Non-ASCII word char in ALIAS declaration identifier is FORM then nest."""
    src = stage_src("open UNIT r\nbind ALIAS é x\nend UNIT\n", _vs("r28a.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("r28a.json")
    )


def test_panel_case_distinct_bindings_ok():
    """X and x are distinct identifiers (no case-folded symbol table)."""
    src = stage_src(
        "open UNIT r\nbind VAL X 1\nbind VAL x 2\nend UNIT\n", _vs("r29a.quill")
    )
    assert_report_bytes(
        src,
        expect_json(
            {
                "status": "ok",
                "frames": [],
                "bindings": [
                    {"frame": "r", "kind": "VAL", "name": "X", "value": 1, "line": 2},
                    {"frame": "r", "kind": "VAL", "name": "x", "value": 2, "line": 3},
                ],
                "uses": [],
            }
        ),
        _vs("r29a.json"),
    )



def test_panel_int_overlong_digits_form():
    """Decimal token longer than Python's default int conversion limit → FORM then nest."""
    body = "open UNIT r\nbind VAL x " + ("9" * 4301) + "\nend UNIT\n"
    assert len(body.encode("utf-8")) == 4334
    src = stage_src(body, _vs("r30a.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("r30a.json")
    )


def test_panel_int_overlong_neg_digits_form():
    """Negative overlong decimal token → FORM then nest (not an uncaught conversion)."""
    body = "open UNIT r\nbind VAL x -" + ("9" * 4301) + "\nend UNIT\n"
    assert len(body.encode("utf-8")) == 4335
    src = stage_src(body, _vs("r30b.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("r30b.json")
    )


def test_panel_form_then_dup_keeps_halt():
    """FORM freeze persists through a later duplicate bind; matching end stays frozen."""
    src = stage_src(
        "open UNIT r\nbind VAL x 1\nbogus\nbind VAL x 2\nend UNIT\n",
        _vs("r31a.quill"),
    )
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("r31a.json")
    )


def test_panel_rebind_after_closed_same_depth_ok():
    """Closing a frame frees its depth; a new frame may rebind the same name."""
    src = stage_src(
        "open UNIT a\nbind VAL x 1\nend UNIT\nopen UNIT b\nbind VAL x 2\nend UNIT\n",
        _vs("r32a.quill"),
    )
    assert_report_bytes(
        src,
        expect_json(
            {
                "status": "ok",
                "frames": [],
                "bindings": [
                    {"frame": "a", "kind": "VAL", "name": "x", "value": 1, "line": 2},
                    {"frame": "b", "kind": "VAL", "name": "x", "value": 2, "line": 5},
                ],
                "uses": [],
            }
        ),
        _vs("r32a.json"),
    )



def test_panel_case_sensitive_end_verb():
    """`End` is not a verb → FORM."""
    src = stage_src("End UNIT\n", _vs("r33a.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "form", "line": 1}), _vs("r33a.json")
    )


def test_panel_case_sensitive_bind_verb():
    """`Bind` is not a verb → FORM then EOF nest."""
    src = stage_src("open UNIT r\nBind VAL x 1\nend UNIT\n", _vs("r33b.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("r33b.json")
    )


def test_panel_case_sensitive_use_verb():
    """`Use` is not a verb → FORM then EOF nest."""
    src = stage_src("open UNIT r\nbind VAL x 1\nUse x\nend UNIT\n", _vs("r33c.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("r33c.json")
    )


def test_panel_alias_uppercase_target_ok():
    """Uppercase ALIAS target is legal and resolves."""
    src = stage_src(
        "open UNIT r\nbind VAL X 1\nbind ALIAS b X\nuse b\nend UNIT\n",
        _vs("r34a.quill"),
    )
    assert_report_bytes(
        src,
        expect_json(
            {
                "status": "ok",
                "frames": [],
                "bindings": [
                    {"frame": "r", "kind": "VAL", "name": "X", "value": 1, "line": 2},
                    {"frame": "r", "kind": "ALIAS", "name": "b", "value": "X", "line": 3},
                ],
                "uses": [{"name": "b", "value": 1, "line": 4}],
            }
        ),
        _vs("r34a.json"),
    )


def test_panel_alias_uppercase_decl_ok():
    """Uppercase ALIAS declaration name is legal and usable."""
    src = stage_src(
        "open UNIT r\nbind VAL x 1\nbind ALIAS B x\nuse B\nend UNIT\n",
        _vs("r34b.quill"),
    )
    assert_report_bytes(
        src,
        expect_json(
            {
                "status": "ok",
                "frames": [],
                "bindings": [
                    {"frame": "r", "kind": "VAL", "name": "x", "value": 1, "line": 2},
                    {"frame": "r", "kind": "ALIAS", "name": "B", "value": "x", "line": 3},
                ],
                "uses": [{"name": "B", "value": 1, "line": 4}],
            }
        ),
        _vs("r34b.json"),
    )


def test_panel_nested_invalid_frame_name_form():
    """Invalid nested frame name is FORM; closer mismatch keeps FORM (no speculative push)."""
    src = stage_src("open UNIT r\nopen BLOCK a-b\nend BLOCK\n", _vs("r35a.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "form", "line": 2}), _vs("r35a.json")
    )



def test_panel_dup_then_form_freezes_end():
    """FORM after DUP still freezes the matching end → EOF nest (not DUP report)."""
    src = stage_src(
        "open UNIT r\nbind VAL x 1\nbind VAL x 2\nbogus\nend UNIT\n",
        _vs("r37a.quill"),
    )
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("r37a.json")
    )


def test_panel_close_not_a_verb():
    """`close` is not a documented verb (not an `end` synonym) → FORM then nest."""
    src = stage_src("open UNIT r\nclose UNIT\n", _vs("r38a.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("r38a.json")
    )


def test_panel_dollar_frame_name_form():
    """`$` is outside the name alphabet → FORM."""
    src = stage_src("open UNIT r$\nend UNIT\n", _vs("r39a.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "form", "line": 1}), _vs("r39a.json")
    )


def test_panel_dollar_bind_name_form():
    """`$` in a bind name is FORM then nest."""
    src = stage_src("open UNIT r\nbind VAL a$ 1\nend UNIT\n", _vs("r39b.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("r39b.json")
    )


def test_panel_dollar_use_name_form():
    """`$` in a use operand is FORM then nest."""
    src = stage_src("open UNIT r\nuse a$\nend UNIT\n", _vs("r39c.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("r39c.json")
    )


def test_panel_dollar_alias_target_form():
    """`$` in an ALIAS target is FORM then nest."""
    src = stage_src("open UNIT r\nbind ALIAS a x$\nend UNIT\n", _vs("r39d.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("r39d.json")
    )


def test_panel_period_frame_name_form():
    """`.` is outside the name alphabet → FORM (not `[A-Za-z0-9_.]`)."""
    src = stage_src("open UNIT a.b\nend UNIT\n", _vs("r44a.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "form", "line": 1}), _vs("r44a.json")
    )


def test_panel_period_bind_name_form():
    """`.` in a bind name is FORM then nest."""
    src = stage_src("open UNIT r\nbind VAL a.b 1\nend UNIT\n", _vs("r44b.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("r44b.json")
    )


def test_panel_period_use_name_form():
    """`.` in a use operand is FORM then nest."""
    src = stage_src("open UNIT r\nuse a.b\nend UNIT\n", _vs("r44c.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("r44c.json")
    )


def test_panel_period_alias_target_form():
    """`.` in an ALIAS target is FORM then nest."""
    src = stage_src("open UNIT r\nbind ALIAS a x.y\nend UNIT\n", _vs("r44d.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "nest", "line": 1}), _vs("r44d.json")
    )


def test_panel_scale_lines_ok():
    """Inclusive 2000-line domain with substantive statements (not blank-only)."""
    lines = ["open UNIT r", "bind VAL x 1"] + ["use x"] * 1997 + ["end UNIT"]
    body = "\n".join(lines) + "\n"
    assert len(body.splitlines()) == 2000
    assert len(body.encode("utf-8")) < 65536
    uses = [{"name": "x", "value": 1, "line": 3 + i} for i in range(1997)]
    src = stage_src(body, _vs("s90a.quill"))
    assert_report_bytes(
        src,
        expect_json(
            {
                "status": "ok",
                "frames": [],
                "bindings": [
                    {"frame": "r", "kind": "VAL", "name": "x", "value": 1, "line": 2}
                ],
                "uses": uses,
            }
        ),
        _vs("s90a.json"),
    )


def test_panel_scale_fault_line_at_domain_limit():
    """Selected FORM line serializes as 2000 near the physical-line domain limit."""
    body = ("\n" * 1999) + "nope\n"
    assert len(body.splitlines()) == 2000
    assert len(body.encode("utf-8")) < 65536
    src = stage_src(body, _vs("r48a.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "form", "line": 2000}), _vs("r48a.json")
    )


def test_panel_scale_fault_line_near_domain_limit():
    """Selected FORM line serializes as 1999 inside a 2000-line source (second site)."""
    body = ("\n" * 1998) + "nope\n" + "\n"
    assert len(body.splitlines()) == 2000
    src = stage_src(body, _vs("r48b.quill"))
    assert_report_bytes(
        src, expect_json({"status": "fault", "fault": "form", "line": 1999}), _vs("r48b.json")
    )


def test_panel_scale_statements_ok():
    """Legal source above a 1024-statement processing cap still succeeds."""
    lines = ["open UNIT r", "bind VAL x 1"] + ["use x"] * 1022 + ["end UNIT"]
    body = "\n".join(lines) + "\n"
    assert len(body.splitlines()) == 1025
    uses = [{"name": "x", "value": 1, "line": 3 + i} for i in range(1022)]
    src = stage_src(body, _vs("s90b.quill"))
    assert_report_bytes(
        src,
        expect_json(
            {
                "status": "ok",
                "frames": [],
                "bindings": [
                    {"frame": "r", "kind": "VAL", "name": "x", "value": 1, "line": 2}
                ],
                "uses": uses,
            }
        ),
        _vs("s90b.json"),
    )


def test_panel_scale_bytes_ok():
    """Inclusive 65536-byte domain bound: whitespace-only source succeeds."""
    body = " " * 65535 + "\n"
    assert len(body.encode("utf-8")) == 65536
    src = stage_src(body, _vs("s90c.quill"))
    assert_report_bytes(
        src,
        expect_json({"status": "ok", "frames": [], "bindings": [], "uses": []}),
        _vs("s90c.json"),
    )


def test_panel_scale_depth_ok():
    """Peak depth 64 with root→deepest lookup (full outward stack walk)."""
    lines = ["open UNIT f0", "bind VAL x 7"]
    lines += [f"open BLOCK f{i}" for i in range(1, 64)]
    lines += ["use x"]
    lines += ["end BLOCK"] * 63 + ["end UNIT"]
    body = "\n".join(lines) + "\n"
    assert sum(1 for ln in lines if ln.startswith("open ")) == 64
    src = stage_src(body, _vs("s90d.quill"))
    assert_report_bytes(
        src,
        expect_json(
            {
                "status": "ok",
                "frames": [],
                "bindings": [
                    {"frame": "f0", "kind": "VAL", "name": "x", "value": 7, "line": 2}
                ],
                "uses": [{"name": "x", "value": 7, "line": 66}],
            }
        ),
        _vs("s90d.json"),
    )


def test_panel_scale_bindings_ok():
    """256 applied bindings (inclusive documented maximum) still succeed."""
    binds = [f"bind VAL b{i} {i}" for i in range(256)]
    text = "\n".join(["open UNIT root"] + binds + ["end UNIT"]) + "\n"
    bindings = [
        {"frame": "root", "kind": "VAL", "name": f"b{i}", "value": i, "line": 2 + i}
        for i in range(256)
    ]
    src = stage_src(text, _vs("s90e.quill"))
    assert_report_bytes(
        src,
        expect_json(
            {"status": "ok", "frames": [], "bindings": bindings, "uses": []}
        ),
        _vs("s90e.json"),
    )


def test_panel_scale_depth_and_bindings_ok():
    """Peak depth 64 with 256 applied bindings in the innermost frame (combined)."""
    lines = ["open UNIT f0"]
    lines += [f"open BLOCK f{i}" for i in range(1, 64)]
    lines += [f"bind VAL b{i} {i}" for i in range(256)]
    lines += ["end BLOCK"] * 63 + ["end UNIT"]
    body = "\n".join(lines) + "\n"
    assert sum(1 for ln in lines if ln.startswith("open ")) == 64
    assert sum(1 for ln in lines if ln.startswith("bind ")) == 256
    assert len(body.splitlines()) == 384
    assert len(body.encode("utf-8")) < 65536
    bindings = [
        {"frame": "f63", "kind": "VAL", "name": f"b{i}", "value": i, "line": 65 + i}
        for i in range(256)
    ]
    src = stage_src(body, _vs("s90f.quill"))
    assert_report_bytes(
        src,
        expect_json(
            {"status": "ok", "frames": [], "bindings": bindings, "uses": []}
        ),
        _vs("s90f.json"),
    )


@pytest.mark.parametrize("name", SEALED_CASES)
def test_sealed_matches_expect(name: str):
    """Held-out source reproduces the sealed expect bytes (expects never on disk)."""
    snap = SEALED_SNAP[name]
    dest = materialize_sealed(name, _vs(f"case-{SEALED_CASES.index(name):03d}.quill"))
    assert_report_bytes(dest, snap["expect"], _vs(f"case-{SEALED_CASES.index(name):03d}.json"))


def test_output_deterministic_on_nest_case():
    """Two independent runs each match the sealed expect."""
    snap = SEALED_SNAP["nest_beats_later_form"]
    expected = snap["expect"]
    a = materialize_sealed("nest_beats_later_form", _vs("det_a.quill"))
    assert_report_bytes(a, expected, _vs("det_oa.json"))
    _scrub_sandbox_scratch()
    _drain_sandbox()
    b = materialize_sealed("nest_beats_later_form", _vs("det_b.quill"))
    assert_report_bytes(b, expected, _vs("det_ob.json"))


def test_no_expect_bytes_under_app():
    assert not list(APP.rglob("expect.json"))
    assert not (APP / "tests").exists()


def test_sandbox_cannot_read_reward_or_sealed():
    """Sandbox uid must not list /tests or resurrect sealed expects from disk.

    Re-locks ``/logs/verifier`` when possible. Some harbor ``/logs`` bind mounts
    ignore mode bits (stat may look locked while reads still succeed), so the
    hard assertion is the ``/tests`` tree the verifier controls.
    """
    reward = Path("/logs/verifier/reward.txt")
    reward_dir = reward.parent
    reward_dir.mkdir(parents=True, exist_ok=True)
    if not reward.exists():
        reward.write_text("0\n")
    os.chmod(reward_dir, 0o700)
    os.chmod(reward, 0o600)
    try:
        os.chown(reward_dir, 0, 0)
        os.chown(reward, 0, 0)
    except OSError:
        pass
    os.chmod(TESTS, 0o700)
    assert not SEALED.exists()
    probe = (
        "from pathlib import Path\n"
        "paths = [Path('/tests'), Path('/tests/sealed')]\n"
        "for p in paths:\n"
        "    try:\n"
        "        list(p.iterdir()) if (p.exists() and p.is_dir()) else p.read_bytes()\n"
        "    except Exception:\n"
        "        pass\n"
        "    else:\n"
        "        raise SystemExit('readable:'+str(p))\n"
        "print('OK')\n"
    )
    proc = subprocess.run(
        [
            "/usr/bin/setpriv",
            "--reuid",
            str(SANDBOX_UID),
            "--regid",
            str(SANDBOX_GID),
            "--clear-groups",
            "--no-new-privs",
            "--",
            sys.executable,
            "-c",
            probe,
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert proc.returncode == 0, (proc.stderr, proc.stdout)
    assert "OK" in proc.stdout
