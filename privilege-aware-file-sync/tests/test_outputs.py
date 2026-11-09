"""Verifier harness for privilege-aware-file-sync.

At import time the harness loads every sealed manifest and expect artifact into
memory, deletes ``/tests/sealed`` from disk, and chmods ``/tests`` to ``0700``.
Each graded sync invocation runs ``/app/src/syncer.py`` under ``setpriv`` as uid/gid
2000 with cleared groups, ``--no-new-privs``, a 20s wall timeout, tightened
resource limits, and a post-run sandbox drain.  Sealed cases use neutral
``case-NNN`` work directories so argv never carries fixture vocabulary.
"""

from __future__ import annotations

import json
import os
import resource
import shutil
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from trees import (
    TargetFollowWatch,
    apply_src_chmod,
    destination_symlink_targets,
    materialize_case,
    snapshot_tree,
    source_symlink_targets,
)

APP = Path("/app")
SRC = APP / "src" / "syncer.py"
TESTS = Path("/tests")
SEALED = TESTS / "sealed"
WORK = Path("/work/out")


def _opaque_work(key: str) -> Path:
    """Work dir whose name carries no graded vocabulary (PGT-safe in argv)."""
    import hashlib

    digest = hashlib.sha256(f"pafs:{key}".encode()).hexdigest()[:16]
    return WORK / f"w{digest}"

SANDBOX_ROOT = Path("/sandbox")

SANDBOX_UID = 2000
SANDBOX_GID = 2000
RUN_TIMEOUT = 20
REPORT_SENTINEL = b"sentinel\n"
REFUSE_STDERR = "refuse: bad args\n"

def _snapshot_sealed() -> dict[str, dict[str, bytes]]:
    out: dict[str, dict[str, bytes]] = {}
    for case in sorted(p for p in SEALED.iterdir() if p.is_dir()):
        out[case.name] = {
            "manifest": (case / "manifest.json").read_bytes(),
            "expect_report": (case / "expect_report.json").read_bytes(),
            "expect_tree": (case / "expect_tree.json").read_bytes(),
        }
    return out


SEALED_SNAP = _snapshot_sealed()
SEALED_CASES = sorted(SEALED_SNAP)
CASE_ID = {name: f"case-{i:03d}" for i, name in enumerate(SEALED_CASES)}


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
    alive: list[int] = []
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


def _chown_tree(path: Path) -> None:
    for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
        os.chown(dirpath, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
        for name in dirnames + filenames:
            try:
                os.chown(Path(dirpath) / name, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
            except OSError:
                pass


def load_expect_tree(raw: bytes) -> dict:
    data = json.loads(raw)
    out: dict = {}
    for k, meta in data.items():
        row = dict(meta)
        if "content_hex" in row:
            row["content"] = bytes.fromhex(row.pop("content_hex"))
        out[k] = row
    return out


def stage_manifest(manifest: dict, dest: Path) -> tuple[Path, Path]:
    if dest.exists():
        shutil.rmtree(dest)
    src, dst = materialize_case(dest, manifest)
    _chown_tree(dest)
    apply_src_chmod(src, manifest)
    return src, dst


def _sync_env() -> dict[str, str]:
    return {
        "HOME": "/sandbox",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "PYTHONHASHSEED": "0",
        "PYTHONSAFEPATH": "1",
        "TZ": "UTC",
    }


# Runs the candidate syncer under an LD_PRELOAD libc guard that records
# follow-through open/chmod/chown of symlink pathnames (SYNC.md: never
# open/chmod/chown the symlink target). The preload is inherited by child
# processes, so an uninstrumented subprocess open of an in-tree symlink is
# still logged. In-tree targets cannot be inotify-watched without
# false-positing on ordinary O_NOFOLLOW file copies.
_NOFOLLOW_GUARD_SO = Path("/usr/local/lib/pafs_nofollow_guard.so")


def _symlink_open_log() -> Path:
    return WORK / ".pafs_symlink_open.log"


def _prepare_symlink_open_log() -> Path:
    """Empty violation log writable by the sandbox uid."""
    WORK.mkdir(parents=True, exist_ok=True)
    os.chown(WORK, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    log = _symlink_open_log()
    log.write_text("", encoding="utf-8")
    os.chown(log, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    return log


def run_syncer_argv(extra_argv: list[str]) -> subprocess.CompletedProcess[str]:
    log = _prepare_symlink_open_log()
    env = _sync_env()
    env["PAFS_SYMLINK_OPEN_LOG"] = str(log)
    # Preload must be readable by uid 2000; the .so lives under /usr/local/lib.
    env["LD_PRELOAD"] = str(_NOFOLLOW_GUARD_SO)
    command = [
        "/usr/bin/setpriv",
        "--reuid",
        str(SANDBOX_UID),
        "--regid",
        str(SANDBOX_GID),
        "--clear-groups",
        "--no-new-privs",
        "--",
        sys.executable,
        str(SRC),
        *extra_argv,
    ]
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=RUN_TIMEOUT,
        env=env,
        cwd="/sandbox",
        preexec_fn=_apply_resource_limits,
    )


def run_syncer(
    src: Path, dst: Path, report: Path, *, clear_report: bool = True
) -> subprocess.CompletedProcess[str]:
    if clear_report and (report.exists() or report.is_symlink()):
        report.unlink()
    if report.parent.exists():
        os.chown(report.parent, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    return run_syncer_argv(
        ["--src", str(src), "--dst", str(dst), "--report", str(report)]
    )


def read_nofollow(path: Path) -> bytes:
    if path.is_symlink():
        raise AssertionError(f"path is symlink: {path}")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags)
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


def assert_success(
    src: Path, dst: Path, expect_report: bytes, expect_tree: dict, report: Path
) -> None:
    extra = src.parent / "extra"
    extra_before = snapshot_tree(extra) if extra.is_dir() else None
    secret = extra / "secret.dat"
    secret_mode = secret.stat().st_mode & 0o7777 if secret.is_file() else None
    secret_gid = secret.stat().st_gid if secret.is_file() else None
    targets = source_symlink_targets(src) + destination_symlink_targets(dst)
    # Deduplicate while preserving order (same path may appear from both trees).
    seen: set[str] = set()
    uniq_targets: list[Path] = []
    for t in targets:
        key = str(t)
        if key not in seen:
            seen.add(key)
            uniq_targets.append(t)
    with TargetFollowWatch(uniq_targets) as watch:
        proc = run_syncer(src, dst, report, clear_report=True)
        violations = watch.drain_violations()
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == ""
    assert read_nofollow(report) == expect_report
    assert snapshot_tree(dst) == expect_tree
    assert not violations, f"symlink target followed (inotify masks={list(map(hex, violations))})"
    open_log = _symlink_open_log()
    open_hits = open_log.read_text(encoding="utf-8") if open_log.is_file() else ""
    assert not open_hits.strip(), f"symlink follow op ({open_hits!r})"
    # Unsafe / type-conflict paths must not create siblings under extra/ via
    # escaping destination links (dst snapshot never follows those links).
    # Live old destination-link targets under extra/ must keep bytes/mode too.
    if extra_before is not None:
        assert snapshot_tree(extra) == extra_before
    if secret_mode is not None:
        assert (secret.stat().st_mode & 0o7777) == secret_mode
    if secret_gid is not None:
        assert secret.stat().st_gid == secret_gid
    _drain_sandbox()


def assert_refuse(src: Path, dst: Path, report: Path) -> None:
    """Exit 2, exact stderr, empty stdout, regular report sentinel preserved."""
    report.parent.mkdir(parents=True, exist_ok=True)
    os.chown(report.parent, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    if report.exists() or report.is_symlink():
        report.unlink()
    report.write_bytes(REPORT_SENTINEL)
    os.chown(report, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    proc = run_syncer(src, dst, report, clear_report=False)
    assert proc.returncode == 2, (proc.returncode, proc.stderr, proc.stdout)
    assert proc.stdout == ""
    assert proc.stderr == REFUSE_STDERR
    assert read_nofollow(report) == REPORT_SENTINEL
    _drain_sandbox()


def _assert_refuse_argv(extra_argv: list[str], *, report: Path | None = None) -> None:
    """Refuse: status 2, empty stdout, exact stderr; preserve report if supplied."""
    if report is not None:
        report.parent.mkdir(parents=True, exist_ok=True)
        os.chown(report.parent, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
        if report.exists() or report.is_symlink():
            report.unlink()
        report.write_bytes(REPORT_SENTINEL)
        os.chown(report, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    proc = run_syncer_argv(extra_argv)
    assert proc.returncode == 2, (proc.returncode, proc.stderr, proc.stdout)
    assert proc.stdout == ""
    assert proc.stderr == REFUSE_STDERR
    if report is not None:
        assert read_nofollow(report) == REPORT_SENTINEL
    _drain_sandbox()


def test_syncer_source_present():
    """Graded runs target the documented syncer path under /app."""
    assert SRC.is_file() and not SRC.is_symlink()
    assert SRC.resolve() == Path("/app/src/syncer.py").resolve()


# --- Symlink leaf (file + directory symlink) ---


def test_bench_symlink_a_file_alias_not_materialized():
    """Symlink entries must remain links, not copied target bytes (site A)."""
    work = _opaque_work("bench_symlink_a")
    manifest = {
        "src": [
            {"path": "body.txt", "kind": "file", "content": "body\n", "mode": 0o644},
            {"path": "alias", "kind": "symlink", "target": "body.txt"},
        ]
    }
    src, dst = stage_manifest(manifest, work / "tree")
    proc = run_syncer(src, dst, work / "out.json")
    assert proc.returncode == 0
    snap = snapshot_tree(dst)
    assert snap["alias"]["kind"] == "symlink"
    assert snap["alias"]["target"] == "body.txt"
    assert snap["body.txt"]["content"] == b"body\n"
    _drain_sandbox()


def test_bench_symlink_b_dir_link_is_leaf():
    """Directory symlinks must not be entered during discovery (site B)."""
    work = _opaque_work("bench_symlink_b")
    manifest = {
        "src": [
            {"path": "box", "kind": "dir", "mode": 0o755},
            {"path": "box/x.txt", "kind": "file", "content": "x\n", "mode": 0o644},
            {"path": "door", "kind": "symlink", "target": "box"},
        ]
    }
    src, dst = stage_manifest(manifest, work / "tree")
    report = work / "out.json"
    proc = run_syncer(src, dst, report)
    assert proc.returncode == 0
    snap = snapshot_tree(dst)
    assert snap["door"]["kind"] == "symlink"
    assert "door/x.txt" not in snap
    data = json.loads(read_nofollow(report))
    assert "door/x.txt" not in data["copied"]
    _drain_sandbox()


# --- Trap symlink: never chmod through link ---


def test_bench_trap_a_extra_mode_unchanged():
    """Trap symlink copy must not alter modes or ownership on the link target (site A)."""
    work = _opaque_work("bench_trap_a")
    manifest = {
        "extra": [{"path": "secret.dat", "kind": "file", "content": "secret\n", "mode": 0o640}],
        "src": [
            {"path": "trap", "kind": "symlink", "target": "../extra/secret.dat"},
            {"path": "ok.txt", "kind": "file", "content": "ok\n", "mode": 0o644},
        ],
    }
    src, dst = stage_manifest(manifest, work / "tree")
    secret = work / "tree" / "extra" / "secret.dat"
    # Distinct group so a following chown(link) is observable.
    os.chown(secret, SANDBOX_UID, 2001, follow_symlinks=False)
    assert (secret.stat().st_mode & 0o7777) == 0o640
    assert secret.stat().st_gid == 2001
    with TargetFollowWatch([secret]) as watch:
        proc = run_syncer(src, dst, work / "out.json")
        violations = watch.drain_violations()
    assert proc.returncode == 0
    assert (secret.stat().st_mode & 0o7777) == 0o640
    assert secret.stat().st_gid == 2001
    assert not violations, f"trap target followed (masks={list(map(hex, violations))})"
    _drain_sandbox()


def test_bench_trap_b_second_trap_path():
    """A second trap symlink must also leave its target mode and group intact (site B)."""
    work = _opaque_work("bench_trap_b")
    manifest = {
        "extra": [{"path": "guard.bin", "kind": "file", "content": "g\n", "mode": 0o600}],
        "src": [{"path": "hook", "kind": "symlink", "target": "../extra/guard.bin"}],
    }
    src, dst = stage_manifest(manifest, work / "tree")
    guard = work / "tree" / "extra" / "guard.bin"
    os.chown(guard, SANDBOX_UID, 2001, follow_symlinks=False)
    with TargetFollowWatch([guard]) as watch:
        proc = run_syncer(src, dst, work / "out.json")
        violations = watch.drain_violations()
    assert proc.returncode == 0
    assert (guard.stat().st_mode & 0o7777) == 0o600
    assert guard.stat().st_gid == 2001
    assert not violations, f"trap target followed (masks={list(map(hex, violations))})"
    _drain_sandbox()


# --- Escaping destination intermediate (two sites) ---


def test_bench_escape_a_intermediate_link():
    """Entries whose parent path escapes via a link must not write (site A)."""
    work = _opaque_work("bench_escape_a")
    manifest = {
        "extra": [{"path": "outside.txt", "kind": "file", "content": "out\n", "mode": 0o644}],
        "src": [
            {"path": "sub", "kind": "dir", "mode": 0o755},
            {"path": "sub/nested.txt", "kind": "file", "content": "n\n", "mode": 0o644},
        ],
        "dst_pre": [{"path": "sub", "kind": "symlink", "target": "../extra"}],
    }
    src, dst = stage_manifest(manifest, work / "tree")
    outside = work / "tree" / "extra" / "outside.txt"
    before = outside.read_bytes()
    report = work / "out.json"
    proc = run_syncer(src, dst, report)
    assert proc.returncode == 0
    data = json.loads(read_nofollow(report))
    reasons = {row["path"]: row["reason"] for row in data["skipped"]}
    assert reasons.get("sub") == "type conflict"
    assert reasons.get("sub/nested.txt") == "unsafe"
    assert outside.read_bytes() == before
    _drain_sandbox()


def test_bench_escape_b_deeper_intermediate():
    """Deeper intermediate links that escape must still block writes (site B)."""
    work = _opaque_work("bench_escape_b")
    manifest = {
        "extra": [{"path": "out.dat", "kind": "file", "content": "o\n", "mode": 0o644}],
        "src": [
            {"path": "a", "kind": "dir", "mode": 0o755},
            {"path": "a/b", "kind": "dir", "mode": 0o755},
            {"path": "a/b/c.txt", "kind": "file", "content": "c\n", "mode": 0o644},
        ],
        "dst_pre": [
            {"path": "a", "kind": "dir", "mode": 0o755},
            {"path": "a/b", "kind": "symlink", "target": "../../extra"},
        ],
    }
    src, dst = stage_manifest(manifest, work / "tree")
    report = work / "out.json"
    proc = run_syncer(src, dst, report)
    assert proc.returncode == 0
    reasons = {row["path"]: row["reason"] for row in json.loads(read_nofollow(report))["skipped"]}
    assert reasons.get("a/b") == "type conflict"
    assert reasons.get("a/b/c.txt") == "unsafe"
    _drain_sandbox()


def test_bench_escape_third_intermediate_unsafe():
    """Unsafe disposition at the third intermediate component (visible)."""
    work = _opaque_work("bench_escape_third")
    snap = SEALED_SNAP["unsafe_third_intermediate"]
    manifest = json.loads(snap["manifest"])
    src, dst = stage_manifest(manifest, work / "tree")
    assert_success(
        src, dst, snap["expect_report"], load_expect_tree(snap["expect_tree"]), work / "out.json"
    )
    reasons = {
        row["path"]: row["reason"]
        for row in json.loads(read_nofollow(work / "out.json"))["skipped"]
    }
    assert reasons.get("a/b/c/d") == "unsafe"
    assert reasons.get("a/b/c/d/x") == "unsafe"


def test_bench_escape_third_intermediate_safe():
    """Safe symlink at the third intermediate must still permit the copy."""
    work = _opaque_work("bench_safe_third")
    snap = SEALED_SNAP["safe_third_intermediate"]
    manifest = json.loads(snap["manifest"])
    src, dst = stage_manifest(manifest, work / "tree")
    assert_success(
        src, dst, snap["expect_report"], load_expect_tree(snap["expect_tree"]), work / "out.json"
    )


def test_bench_escape_prefix_reenter():
    """Unsafe intermediate must skip even when a later symlink re-enters dst."""
    work = _opaque_work("bench_prefix_reenter")
    snap = SEALED_SNAP["unsafe_prefix_reenter"]
    manifest = json.loads(snap["manifest"])
    src, dst = stage_manifest(manifest, work / "tree")
    assert_success(
        src, dst, snap["expect_report"], load_expect_tree(snap["expect_tree"]), work / "out.json"
    )
    reasons = {
        row["path"]: row["reason"]
        for row in json.loads(read_nofollow(work / "out.json"))["skipped"]
    }
    assert reasons.get("a/back") == "unsafe"
    assert reasons.get("a/back/f") == "unsafe"
    assert not (dst / "inner" / "f").exists()


def test_bench_escape_prefix_reenter_b():
    """Second site: unsafe prefix with later re-entry into destination."""
    work = _opaque_work("bench_prefix_reenter_b")
    snap = SEALED_SNAP["unsafe_prefix_reenter_b"]
    manifest = json.loads(snap["manifest"])
    src, dst = stage_manifest(manifest, work / "tree")
    assert_success(
        src, dst, snap["expect_report"], load_expect_tree(snap["expect_tree"]), work / "out.json"
    )
    assert not (dst / "sink" / "g").exists()


def test_bench_escape_fourth_intermediate_unsafe():
    """Unsafe disposition at the fourth intermediate component."""
    work = _opaque_work("bench_escape_fourth")
    snap = SEALED_SNAP["unsafe_fourth_intermediate"]
    manifest = json.loads(snap["manifest"])
    src, dst = stage_manifest(manifest, work / "tree")
    assert_success(
        src, dst, snap["expect_report"], load_expect_tree(snap["expect_tree"]), work / "out.json"
    )
    reasons = {
        row["path"]: row["reason"]
        for row in json.loads(read_nofollow(work / "out.json"))["skipped"]
    }
    assert reasons.get("a/b/c/d") == "type conflict"
    assert reasons.get("a/b/c/d/f") == "unsafe"


def test_bench_escape_fourth_intermediate_safe():
    """Safe symlink at the fourth intermediate must still permit the copy."""
    work = _opaque_work("bench_safe_fourth")
    snap = SEALED_SNAP["safe_fourth_intermediate"]
    manifest = json.loads(snap["manifest"])
    src, dst = stage_manifest(manifest, work / "tree")
    assert_success(
        src, dst, snap["expect_report"], load_expect_tree(snap["expect_tree"]), work / "out.json"
    )


def test_bench_escape_fourth_intermediate_fifo():
    """Non-directory fourth intermediate (FIFO) must be unsafe."""
    work = _opaque_work("bench_fifo_fourth")
    snap = SEALED_SNAP["fifo_fourth_intermediate"]
    manifest = json.loads(snap["manifest"])
    src, dst = stage_manifest(manifest, work / "tree")
    assert_success(
        src, dst, snap["expect_report"], load_expect_tree(snap["expect_tree"]), work / "out.json"
    )


def test_bench_escape_fifth_intermediate_unsafe():
    """Unsafe disposition at the fifth intermediate component."""
    work = _opaque_work("bench_escape_fifth")
    snap = SEALED_SNAP["unsafe_fifth_intermediate"]
    manifest = json.loads(snap["manifest"])
    src, dst = stage_manifest(manifest, work / "tree")
    assert_success(
        src, dst, snap["expect_report"], load_expect_tree(snap["expect_tree"]), work / "out.json"
    )
    reasons = {
        row["path"]: row["reason"]
        for row in json.loads(read_nofollow(work / "out.json"))["skipped"]
    }
    assert reasons.get("a/b/c/d/e") == "type conflict"
    assert reasons.get("a/b/c/d/e/f") == "unsafe"


def test_bench_escape_fifth_intermediate_safe():
    """Safe symlink at the fifth intermediate must still permit the copy."""
    work = _opaque_work("bench_safe_fifth")
    snap = SEALED_SNAP["safe_fifth_intermediate"]
    manifest = json.loads(snap["manifest"])
    src, dst = stage_manifest(manifest, work / "tree")
    assert_success(
        src, dst, snap["expect_report"], load_expect_tree(snap["expect_tree"]), work / "out.json"
    )


def test_bench_escape_sixth_intermediate_unsafe():
    """Unsafe disposition at the sixth intermediate component."""
    work = _opaque_work("bench_escape_sixth")
    snap = SEALED_SNAP["unsafe_sixth_intermediate"]
    manifest = json.loads(snap["manifest"])
    src, dst = stage_manifest(manifest, work / "tree")
    assert_success(
        src, dst, snap["expect_report"], load_expect_tree(snap["expect_tree"]), work / "out.json"
    )
    reasons = {
        row["path"]: row["reason"]
        for row in json.loads(read_nofollow(work / "out.json"))["skipped"]
    }
    assert reasons.get("a/b/c/d/e/f") == "type conflict"
    assert reasons.get("a/b/c/d/e/f/g") == "unsafe"


def test_bench_escape_sixth_intermediate_safe():
    """Safe symlink at the sixth intermediate must still permit the copy."""
    work = _opaque_work("bench_safe_sixth")
    snap = SEALED_SNAP["safe_sixth_intermediate"]
    manifest = json.loads(snap["manifest"])
    src, dst = stage_manifest(manifest, work / "tree")
    assert_success(
        src, dst, snap["expect_report"], load_expect_tree(snap["expect_tree"]), work / "out.json"
    )


def test_bench_escape_seventh_intermediate_unsafe():
    """Unsafe disposition at the seventh intermediate component."""
    work = _opaque_work("bench_escape_seventh")
    snap = SEALED_SNAP["unsafe_seventh_intermediate"]
    manifest = json.loads(snap["manifest"])
    src, dst = stage_manifest(manifest, work / "tree")
    assert_success(
        src, dst, snap["expect_report"], load_expect_tree(snap["expect_tree"]), work / "out.json"
    )
    reasons = {
        row["path"]: row["reason"]
        for row in json.loads(read_nofollow(work / "out.json"))["skipped"]
    }
    assert reasons.get("a/b/c/d/e/f/g") == "type conflict"
    assert reasons.get("a/b/c/d/e/f/g/x") == "unsafe"


def test_bench_escape_seventh_intermediate_safe():
    """Safe symlink at the seventh intermediate must still permit the copy."""
    work = _opaque_work("bench_safe_seventh")
    snap = SEALED_SNAP["safe_seventh_intermediate"]
    manifest = json.loads(snap["manifest"])
    src, dst = stage_manifest(manifest, work / "tree")
    assert_success(
        src, dst, snap["expect_report"], load_expect_tree(snap["expect_tree"]), work / "out.json"
    )


def test_bench_escape_tenth_intermediate_unsafe():
    """Unsafe at the tenth intermediate (deepest legal under depth-11 limit)."""
    work = _opaque_work("bench_escape_tenth")
    snap = SEALED_SNAP["unsafe_tenth_intermediate"]
    manifest = json.loads(snap["manifest"])
    src, dst = stage_manifest(manifest, work / "tree")
    assert_success(
        src, dst, snap["expect_report"], load_expect_tree(snap["expect_tree"]), work / "out.json"
    )
    reasons = {
        row["path"]: row["reason"]
        for row in json.loads(read_nofollow(work / "out.json"))["skipped"]
    }
    assert reasons.get("a/b/c/d/e/f/g/h/i/j") == "type conflict"
    assert reasons.get("a/b/c/d/e/f/g/h/i/j/k") == "unsafe"


def test_bench_escape_c_ancestor_component():
    """Ancestor-escaping destination components must skip nested copies."""
    work = _opaque_work("bench_escape_c")
    snap = SEALED_SNAP["unsafe_ancestor_escape"]
    manifest = json.loads(snap["manifest"])
    src, dst = stage_manifest(manifest, work / "tree")
    expect_tree = load_expect_tree(snap["expect_tree"])
    assert_success(src, dst, snap["expect_report"], expect_tree, work / "out.json")


def test_bench_escape_d_prefix_sibling_not_subpath():
    """String-prefix sibling directories must not receive writes via links."""
    work = _opaque_work("bench_escape_d")
    work.mkdir(parents=True, exist_ok=True)
    tree = work / "tree"
    tree.mkdir()
    dst_root = tree / "dst"
    dst_root.mkdir()
    evil = tree / "dst_evil"
    evil.mkdir()
    marker = evil / "marker.txt"
    marker.write_bytes(b"stay\n")
    os.chmod(marker, 0o644)
    src_root = tree / "src"
    src_root.mkdir()
    (src_root / "hop").mkdir()
    (src_root / "hop" / "leaf.txt").write_text("leaf\n", encoding="utf-8")
    os.chmod(src_root / "hop" / "leaf.txt", 0o644)
    if (dst_root / "hop").exists() or (dst_root / "hop").is_symlink():
        (dst_root / "hop").unlink()
    os.symlink("../dst_evil", dst_root / "hop")
    _chown_tree(tree)
    report = work / "out.json"
    proc = run_syncer(src_root, dst_root, report)
    assert proc.returncode == 0
    data = json.loads(read_nofollow(report))
    reasons = {row["path"]: row["reason"] for row in data["skipped"]}
    assert reasons.get("hop/leaf.txt") == "unsafe"
    assert marker.read_bytes() == b"stay\n"
    assert not (evil / "leaf.txt").exists()
    _drain_sandbox()


# --- Type conflict ---


def test_bench_tc_a_file_over_dir():
    """Source files must not replace destination directories (site A)."""
    work = _opaque_work("bench_tc_a")
    manifest = {
        "src": [{"path": "item", "kind": "file", "content": "new\n", "mode": 0o644}],
        "dst_pre": [{"path": "item", "kind": "dir", "mode": 0o755}],
    }
    src, dst = stage_manifest(manifest, work / "tree")
    report = work / "out.json"
    proc = run_syncer(src, dst, report)
    assert proc.returncode == 0
    data = json.loads(read_nofollow(report))
    assert {"path": "item", "reason": "type conflict"} in data["skipped"]
    assert stat.S_ISDIR(os.lstat(dst / "item").st_mode)
    _drain_sandbox()


def test_bench_tc_b_symlink_over_file():
    """Source symlinks must not replace destination regular files (site B)."""
    work = _opaque_work("bench_tc_b")
    manifest = {
        "src": [{"path": "item", "kind": "symlink", "target": "somewhere"}],
        "dst_pre": [{"path": "item", "kind": "file", "content": "keep\n", "mode": 0o644}],
    }
    src, dst = stage_manifest(manifest, work / "tree")
    report = work / "out.json"
    proc = run_syncer(src, dst, report)
    assert proc.returncode == 0
    data = json.loads(read_nofollow(report))
    assert {"path": "item", "reason": "type conflict"} in data["skipped"]
    assert read_nofollow(dst / "item") == b"keep\n"
    _drain_sandbox()


# --- Mode bits (setgid, sticky, directory mode refresh) ---


def test_bench_mode_a_setgid_on_file():
    """Regular-file destination modes must preserve setgid from the source."""
    work = _opaque_work("bench_mode_a")
    manifest = {"src": [{"path": "sg", "kind": "file", "content": "g\n", "mode": 0o2644}]}
    src, dst = stage_manifest(manifest, work / "tree")
    proc = run_syncer(src, dst, work / "out.json")
    assert proc.returncode == 0
    assert (os.lstat(dst / "sg").st_mode & 0o7777) == 0o2644
    _drain_sandbox()


def test_bench_mode_b_sticky_on_dir():
    """Directory destination modes must preserve sticky bits from the source."""
    work = _opaque_work("bench_mode_b")
    manifest = {"src": [{"path": "s", "kind": "dir", "mode": 0o1755}]}
    src, dst = stage_manifest(manifest, work / "tree")
    proc = run_syncer(src, dst, work / "out.json")
    assert proc.returncode == 0
    assert (os.lstat(dst / "s").st_mode & 0o7777) == 0o1755
    _drain_sandbox()


def test_bench_mode_sticky_on_file():
    """Regular-file destination modes must preserve sticky from the source."""
    work = _opaque_work("bench_mode_sticky_file")
    manifest = {"src": [{"path": "stick", "kind": "file", "content": "s\n", "mode": 0o1644}]}
    src, dst = stage_manifest(manifest, work / "tree")
    proc = run_syncer(src, dst, work / "out.json")
    assert proc.returncode == 0
    assert (os.lstat(dst / "stick").st_mode & 0o7777) == 0o1644
    _drain_sandbox()


def test_bench_mode_setuid_on_dir():
    """Directory destination modes must preserve setuid from the source."""
    work = _opaque_work("bench_mode_setuid_dir")
    snap = SEALED_SNAP["dir_setuid"]
    manifest = json.loads(snap["manifest"])
    src, dst = stage_manifest(manifest, work / "tree")
    assert_success(src, dst, snap["expect_report"], load_expect_tree(snap["expect_tree"]), work / "out.json")
    assert (os.lstat(dst / "suid").st_mode & 0o7777) == 0o4755


def test_bench_mode_c_dir_refresh_over_existing():
    """Existing destination directories must receive updated permission bits."""
    work = _opaque_work("bench_mode_c")
    manifest = {
        "src": [{"path": "d", "kind": "dir", "mode": 0o700}],
        "dst_pre": [{"path": "d", "kind": "dir", "mode": 0o755}],
    }
    src, dst = stage_manifest(manifest, work / "tree")
    proc = run_syncer(src, dst, work / "out.json")
    assert proc.returncode == 0
    assert (os.lstat(dst / "d").st_mode & 0o7777) == 0o700
    _drain_sandbox()


# --- Report sort order ---


def test_bench_order_a_two_files():
    """Copied paths in the report must be sorted ascending (site A)."""
    work = _opaque_work("bench_order_a")
    manifest = {
        "src": [
            {"path": "a.txt", "kind": "file", "content": "a\n", "mode": 0o644},
            {"path": "b.txt", "kind": "file", "content": "b\n", "mode": 0o644},
        ]
    }
    src, dst = stage_manifest(manifest, work / "tree")
    report = work / "out.json"
    proc = run_syncer(src, dst, report)
    assert proc.returncode == 0
    assert json.loads(read_nofollow(report))["copied"] == ["", "a.txt", "b.txt"]
    _drain_sandbox()


def test_bench_order_b_three_files():
    """Copied arrays must sort lexicographically regardless of walk order (site B)."""
    work = _opaque_work("bench_order_b")
    manifest = {
        "src": [
            {"path": "z.txt", "kind": "file", "content": "z\n", "mode": 0o644},
            {"path": "m.txt", "kind": "file", "content": "m\n", "mode": 0o644},
            {"path": "a.txt", "kind": "file", "content": "a\n", "mode": 0o644},
        ]
    }
    src, dst = stage_manifest(manifest, work / "tree")
    report = work / "out.json"
    proc = run_syncer(src, dst, report)
    assert proc.returncode == 0
    assert json.loads(read_nofollow(report))["copied"] == ["", "a.txt", "m.txt", "z.txt"]
    _drain_sandbox()


def test_bench_order_c_walk_vs_report_sort():
    """Report ordering must follow Unicode sort, not raw discovery order."""
    work = _opaque_work("bench_order_c")
    snap = SEALED_SNAP["sort_vs_dfs"]
    manifest = json.loads(snap["manifest"])
    src, dst = stage_manifest(manifest, work / "tree")
    assert_success(src, dst, snap["expect_report"], load_expect_tree(snap["expect_tree"]), work / "out.json")


def test_bench_order_case_copied():
    """Copied order is Unicode-ascending: Z before a (not casefold)."""
    work = _opaque_work("bench_order_case_copied")
    snap = SEALED_SNAP["order_case_copied"]
    manifest = json.loads(snap["manifest"])
    src, dst = stage_manifest(manifest, work / "tree")
    assert_success(
        src, dst, snap["expect_report"], load_expect_tree(snap["expect_tree"]), work / "out.json"
    )
    assert json.loads(read_nofollow(work / "out.json"))["copied"] == ["", "Z", "a"]


def test_bench_order_case_skipped():
    """Skipped order is Unicode-ascending by path: Z before a (not casefold)."""
    work = _opaque_work("bench_order_case_skipped")
    snap = SEALED_SNAP["order_case_skipped"]
    manifest = json.loads(snap["manifest"])
    src, dst = stage_manifest(manifest, work / "tree")
    assert_success(
        src, dst, snap["expect_report"], load_expect_tree(snap["expect_tree"]), work / "out.json"
    )
    paths = [row["path"] for row in json.loads(read_nofollow(work / "out.json"))["skipped"]]
    assert paths == ["Z", "a"]


# --- Inaccessible directories ---


def test_bench_inacc_a_top_level():
    """Unreadable source directories are skipped without aborting (site A)."""
    work = _opaque_work("bench_inacc_a")
    manifest = {
        "src": [
            {"path": "open.txt", "kind": "file", "content": "o\n", "mode": 0o644},
            {"path": "vault", "kind": "dir", "mode": 0o755},
            {"path": "vault/hidden.txt", "kind": "file", "content": "h\n", "mode": 0o644},
        ],
        "src_chmod": [{"path": "vault", "mode": 0o000}],
    }
    src, dst = stage_manifest(manifest, work / "tree")
    report = work / "out.json"
    proc = run_syncer(src, dst, report)
    assert proc.returncode == 0
    data = json.loads(read_nofollow(report))
    assert {"path": "vault", "reason": "inaccessible"} in data["skipped"]
    assert "open.txt" in data["copied"]
    _drain_sandbox()


def test_bench_inacc_b_nested():
    """Nested unreadable directories are skipped while siblings continue (site B)."""
    work = _opaque_work("bench_inacc_b")
    manifest = {
        "src": [
            {"path": "keep", "kind": "dir", "mode": 0o755},
            {"path": "keep/ok.txt", "kind": "file", "content": "k\n", "mode": 0o644},
            {"path": "keep/shut", "kind": "dir", "mode": 0o755},
            {"path": "keep/shut/x.txt", "kind": "file", "content": "x\n", "mode": 0o644},
        ],
        "src_chmod": [{"path": "keep/shut", "mode": 0o000}],
    }
    src, dst = stage_manifest(manifest, work / "tree")
    report = work / "out.json"
    proc = run_syncer(src, dst, report)
    assert proc.returncode == 0
    data = json.loads(read_nofollow(report))
    assert {"path": "keep/shut", "reason": "inaccessible"} in data["skipped"]
    assert "keep/ok.txt" in data["copied"]
    _drain_sandbox()


# --- Continue discovery after directory type conflict ---


def test_bench_walk_after_dir_mismatch():
    """Children remain discoverable after a directory type mismatch on the parent."""
    work = _opaque_work("bench_walk_01")
    manifest = {
        "src": [
            {"path": "wrap", "kind": "dir", "mode": 0o755},
            {"path": "wrap/inner.txt", "kind": "file", "content": "i\n", "mode": 0o644},
        ],
        "dst_pre": [{"path": "wrap", "kind": "file", "content": "block\n", "mode": 0o644}],
    }
    src, dst = stage_manifest(manifest, work / "tree")
    report = work / "out.json"
    proc = run_syncer(src, dst, report)
    assert proc.returncode == 0
    reasons = {row["path"]: row["reason"] for row in json.loads(read_nofollow(report))["skipped"]}
    assert reasons.get("wrap") == "type conflict"
    assert "wrap/inner.txt" in reasons
    _drain_sandbox()


# --- FIFO, readonly source, binary payload, symlink specials ---


def test_bench_special_a_fifo_skipped():
    """FIFO source entries must appear in skipped with the unsupported reason."""
    work = _opaque_work("bench_special_a")
    snap = SEALED_SNAP["fifo_unsupported"]
    manifest = json.loads(snap["manifest"])
    src, dst = stage_manifest(manifest, work / "tree")
    assert_success(src, dst, snap["expect_report"], load_expect_tree(snap["expect_tree"]), work / "out.json")


def test_bench_special_b_readonly_source():
    """Read-only source files must still copy successfully."""
    work = _opaque_work("bench_special_b")
    snap = SEALED_SNAP["readonly_source"]
    manifest = json.loads(snap["manifest"])
    src, dst = stage_manifest(manifest, work / "tree")
    assert_success(src, dst, snap["expect_report"], load_expect_tree(snap["expect_tree"]), work / "out.json")


def test_bench_special_c_binary_and_length():
    """Binary payloads including long files must copy verbatim."""
    work = _opaque_work("bench_special_c")
    snap = SEALED_SNAP["binary_and_long"]
    manifest = json.loads(snap["manifest"])
    src, dst = stage_manifest(manifest, work / "tree")
    assert_success(src, dst, snap["expect_report"], load_expect_tree(snap["expect_tree"]), work / "out.json")


def test_bench_special_d_non_utf8_symlink_target():
    """Symlink targets with non-UTF8 bytes must round-trip on the destination."""
    work = _opaque_work("bench_special_d")
    snap = SEALED_SNAP["symlink_non_utf8_target"]
    manifest = json.loads(snap["manifest"])
    src, dst = stage_manifest(manifest, work / "tree")
    assert_success(src, dst, snap["expect_report"], load_expect_tree(snap["expect_tree"]), work / "out.json")


def test_bench_special_e_dangling_symlink():
    """Dangling source symlinks must still be recreated at the destination."""
    work = _opaque_work("bench_special_e")
    snap = SEALED_SNAP["dangling_symlink"]
    manifest = json.loads(snap["manifest"])
    src, dst = stage_manifest(manifest, work / "tree")
    assert_success(src, dst, snap["expect_report"], load_expect_tree(snap["expect_tree"]), work / "out.json")


def test_bench_special_f_symlink_vs_fifo_dest():
    """Symlink sources must not overwrite FIFO destinations."""
    work = _opaque_work("bench_special_f")
    snap = SEALED_SNAP["symlink_vs_fifo_dest"]
    manifest = json.loads(snap["manifest"])
    src, dst = stage_manifest(manifest, work / "tree")
    assert_success(src, dst, snap["expect_report"], load_expect_tree(snap["expect_tree"]), work / "out.json")


# --- Safe intermediate symlinks ---


def test_bench_mid_a_dot_target():
    """Intermediate symlinks to dot within the destination tree remain safe."""
    work = _opaque_work("bench_mid_a")
    snap = SEALED_SNAP["safe_intermediate_dot"]
    manifest = json.loads(snap["manifest"])
    src, dst = stage_manifest(manifest, work / "tree")
    assert_success(src, dst, snap["expect_report"], load_expect_tree(snap["expect_tree"]), work / "out.json")


def test_bench_mid_b_inner_relative():
    """Intermediate relative symlinks that stay under the destination are safe."""
    work = _opaque_work("bench_mid_b")
    snap = SEALED_SNAP["safe_intermediate_inner"]
    manifest = json.loads(snap["manifest"])
    src, dst = stage_manifest(manifest, work / "tree")
    assert_success(src, dst, snap["expect_report"], load_expect_tree(snap["expect_tree"]), work / "out.json")


# --- Unicode report paths, scale trees ---


def test_bench_unicode_report_path():
    """Report paths with non-ASCII characters must be written on success."""
    work = _opaque_work("bench_unicode_01")
    snap = SEALED_SNAP["unicode_name"]
    manifest = json.loads(snap["manifest"])
    src, dst = stage_manifest(manifest, work / "tree")
    report = work / "résumé.json"
    assert_success(src, dst, snap["expect_report"], load_expect_tree(snap["expect_tree"]), report)


def test_bench_forty_files():
    """Sibling count at the documented entry maximum (40) must copy completely."""
    work = _opaque_work("bench_forty_01")
    snap = SEALED_SNAP["forty_files"]
    manifest = json.loads(snap["manifest"])
    src, dst = stage_manifest(manifest, work / "tree")
    assert_success(src, dst, snap["expect_report"], load_expect_tree(snap["expect_tree"]), work / "out.json")
    copied = json.loads(read_nofollow(work / "out.json"))["copied"]
    assert "" in copied
    assert len([p for p in copied if p]) == 40


def test_bench_deep_nest():
    """A file at the documented depth maximum (11 components) must sync."""
    work = _opaque_work("bench_deep_01")
    snap = SEALED_SNAP["deep_nest"]
    manifest = json.loads(snap["manifest"])
    src, dst = stage_manifest(manifest, work / "tree")
    assert_success(src, dst, snap["expect_report"], load_expect_tree(snap["expect_tree"]), work / "out.json")
    assert "a/b/c/d/e/f/g/h/i/j/k.txt" in json.loads(snap["expect_report"])["copied"]


def test_bench_max_file_bytes_visible():
    """A regular file at the documented 65536-byte limit must copy completely."""
    work = _opaque_work("bench_max_file")
    snap = SEALED_SNAP["binary_and_long"]
    manifest = json.loads(snap["manifest"])
    # Visible length check — do not rely solely on sealed opaque hex.
    long_entry = next(e for e in manifest["src"] if e["path"] == "long.dat")
    assert len(bytes.fromhex(long_entry["content_hex"])) == 65536
    src, dst = stage_manifest(manifest, work / "tree")
    assert_success(src, dst, snap["expect_report"], load_expect_tree(snap["expect_tree"]), work / "out.json")
    assert len((dst / "long.dat").read_bytes()) == 65536


def test_bench_inaccessible_walk_root():
    """When --src itself cannot be listed, report inaccessible for relative path \"\".

    Work path uses the sealed case-NNN id only (no scenario vocabulary in argv).
    """
    name = "inaccessible_root"
    snap = SEALED_SNAP[name]
    manifest = json.loads(snap["manifest"])
    work = WORK / CASE_ID[name]
    src, dst = stage_manifest(manifest, work / "tree")
    assert_success(src, dst, snap["expect_report"], load_expect_tree(snap["expect_tree"]), work / "out.json")
    data = json.loads(read_nofollow(work / "out.json"))
    assert "" in data["copied"]
    assert {"path": "", "reason": "inaccessible"} in data["skipped"]
    # Restore mode so later cleanup can remove the tree.
    try:
        os.chmod(src, 0o755)
    except OSError:
        pass


def test_bench_walk_root_ensure_and_mode():
    """Walk root \"\" must be mode-set and recorded in copied before children."""
    work = _opaque_work("bench_root_ensure")
    work.mkdir(parents=True, exist_ok=True)
    src = work / "src"
    dst = work / "dst"
    src.mkdir()
    dst.mkdir()
    (src / "f").write_bytes(b"x")
    os.chmod(src / "f", 0o644)
    os.chmod(src, 0o700)
    os.chmod(dst, 0o755)
    _chown_tree(work)
    report = work / "out.json"
    proc = run_syncer(src, dst, report)
    assert proc.returncode == 0, proc.stderr
    data = json.loads(read_nofollow(report))
    assert data["copied"] == ["", "f"]
    assert data["skipped"] == []
    assert (dst.stat().st_mode & 0o7777) == 0o700
    _drain_sandbox()


def test_bench_dangling_parent_symlink_internal():
    """Permitted dangling intermediate symlink must still receive child copies."""
    work = _opaque_work("bench_dangling_parent")
    snap = SEALED_SNAP["dangling_parent_symlink"]
    manifest = json.loads(snap["manifest"])
    src, dst = stage_manifest(manifest, work / "tree")
    assert_success(
        src, dst, snap["expect_report"], load_expect_tree(snap["expect_tree"]), work / "out.json"
    )
    data = json.loads(read_nofollow(work / "out.json"))
    assert data["copied"] == ["", "a/f"]
    assert {"path": "a", "reason": "type conflict"} in data["skipped"]
    assert (dst / "a").is_symlink()
    assert (dst / "missing" / "f").read_bytes() == b"x"


def test_bench_unreadable_regular_unsupported():
    """Mode-000 regular files skip as unsupported (not inaccessible)."""
    work = _opaque_work("bench_unreadable_reg")
    snap = SEALED_SNAP["unreadable_regular"]
    manifest = json.loads(snap["manifest"])
    src, dst = stage_manifest(manifest, work / "tree")
    assert_success(
        src, dst, snap["expect_report"], load_expect_tree(snap["expect_tree"]), work / "out.json"
    )
    data = json.loads(read_nofollow(work / "out.json"))
    assert {"path": "locked", "reason": "unsupported"} in data["skipped"]
    assert "ok.txt" in data["copied"]
    # Pre-existing destination must be left untouched on source-open failure.
    assert (dst / "locked").read_bytes() == b"old"
    assert (os.lstat(dst / "locked").st_mode & 0o7777) == 0o644
    try:
        os.chmod(src / "locked", 0o644)
    except OSError:
        pass


def test_bench_src_symlink_trailing_slash_mode():
    """--src naming symlink/ must apply the referent directory mode to --dst."""
    work = _opaque_work("bench_src_slash_link")
    work.mkdir(parents=True, exist_ok=True)
    real = work / "real"
    real.mkdir()
    os.chmod(real, 0o755)
    (real / "x.txt").write_bytes(b"x\n")
    os.chmod(real / "x.txt", 0o644)
    slink = work / "slink"
    if slink.exists() or slink.is_symlink():
        slink.unlink()
    os.symlink("real", slink)
    dst = work / "dst"
    dst.mkdir()
    os.chmod(dst, 0o700)
    _chown_tree(work)
    report = work / "out.json"
    src_arg = str(slink) + "/"
    assert stat.S_ISDIR(os.lstat(src_arg).st_mode)
    proc = run_syncer_argv(["--src", src_arg, "--dst", str(dst), "--report", str(report)])
    assert proc.returncode == 0, proc.stderr
    data = json.loads(read_nofollow(report))
    assert data["copied"] == ["", "x.txt"]
    assert data["skipped"] == []
    assert (dst.stat().st_mode & 0o7777) == 0o755
    assert (dst / "x.txt").read_bytes() == b"x\n"
    _drain_sandbox()


def test_bench_dst_symlink_trailing_slash_mode():
    """--dst naming symlink/ must lstat as a directory and sync into the referent."""
    work = _opaque_work("bench_dst_slash_link")
    work.mkdir(parents=True, exist_ok=True)
    src = work / "src"
    src.mkdir()
    os.chmod(src, 0o755)
    (src / "ok.txt").write_bytes(b"ok\n")
    os.chmod(src / "ok.txt", 0o644)
    real = work / "real_dst"
    real.mkdir()
    os.chmod(real, 0o700)
    alias = work / "alias"
    if alias.exists() or alias.is_symlink():
        alias.unlink()
    os.symlink("real_dst", alias)
    _chown_tree(work)
    report = work / "out.json"
    dst_arg = str(alias) + "/"
    assert stat.S_ISDIR(os.lstat(dst_arg).st_mode)
    assert not stat.S_ISDIR(os.lstat(alias).st_mode)
    proc = run_syncer_argv(
        ["--src", str(src), "--dst", dst_arg, "--report", str(report)]
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(read_nofollow(report))
    assert data["copied"] == ["", "ok.txt"]
    assert data["skipped"] == []
    assert (real / "ok.txt").read_bytes() == b"ok\n"
    assert (real.stat().st_mode & 0o7777) == 0o755
    _drain_sandbox()


def test_bench_mode_exec_file():
    """Regular-file modes must preserve execute bits from the source."""
    work = _opaque_work("bench_mode_exec")
    snap = SEALED_SNAP["mode_exec_file"]
    manifest = json.loads(snap["manifest"])
    src, dst = stage_manifest(manifest, work / "tree")
    assert_success(src, dst, snap["expect_report"], load_expect_tree(snap["expect_tree"]), work / "out.json")
    assert (os.lstat(dst / "run").st_mode & 0o7777) == 0o751


def test_bench_symlink_to_fifo_no_open():
    """Recreating a symlink to a FIFO must not open the FIFO (would block)."""
    work = _opaque_work("bench_symlink_fifo")
    snap = SEALED_SNAP["symlink_to_fifo"]
    manifest = json.loads(snap["manifest"])
    src, dst = stage_manifest(manifest, work / "tree")
    assert_success(src, dst, snap["expect_report"], load_expect_tree(snap["expect_tree"]), work / "out.json")
    assert (dst / "probe").is_symlink()


def test_bench_dir_setgid():
    """Directory destination modes must preserve setgid from the source."""
    work = _opaque_work("bench_dir_setgid")
    snap = SEALED_SNAP["dir_setgid"]
    manifest = json.loads(snap["manifest"])
    src, dst = stage_manifest(manifest, work / "tree")
    assert_success(src, dst, snap["expect_report"], load_expect_tree(snap["expect_tree"]), work / "out.json")
    assert (os.lstat(dst / "sgdir").st_mode & 0o7777) == 0o2755


# --- Refusal: argv validation ---


def _refuse_dirs(work: Path) -> tuple[Path, Path, Path]:
    work.mkdir(parents=True, exist_ok=True)
    src = work / "src"
    dst = work / "dst"
    src.mkdir(exist_ok=True)
    dst.mkdir(exist_ok=True)
    _chown_tree(work)
    return src, dst, work / "out.json"


def test_refuse_symlink_src():
    """--src must be a real directory, not a symlink."""
    work = _opaque_work("bench_ref_01")
    work.mkdir(parents=True, exist_ok=True)
    real = work / "real"
    real.mkdir()
    link = work / "link"
    if link.exists() or link.is_symlink():
        link.unlink()
    os.symlink("real", link)
    dst = work / "dst"
    dst.mkdir()
    _chown_tree(work)
    assert_refuse(link, dst, work / "out.json")


def test_refuse_symlink_dst():
    """--dst must be a real directory, not a symlink."""
    work = _opaque_work("bench_ref_02")
    src, _, report = _refuse_dirs(work)
    real = work / "real_dst"
    real.mkdir()
    link = work / "dst_link"
    if link.exists() or link.is_symlink():
        link.unlink()
    os.symlink("real_dst", link)
    _chown_tree(work)
    assert_refuse(src, link, report)


def test_refuse_regular_file_src():
    """--src must not name a regular file."""
    work = _opaque_work("bench_ref_03")
    work.mkdir(parents=True, exist_ok=True)
    not_dir = work / "file_src"
    not_dir.write_bytes(b"x")
    _, dst, report = _refuse_dirs(work)
    _chown_tree(work)
    assert_refuse(not_dir, dst, report)


def test_refuse_regular_file_dst():
    """--dst must not name a regular file."""
    work = _opaque_work("bench_ref_04")
    src, _, report = _refuse_dirs(work)
    not_dir = work / "file_dst"
    not_dir.write_bytes(b"x")
    _chown_tree(work)
    assert_refuse(src, not_dir, report)


def test_refuse_nonexistent_src():
    """--src must exist on disk."""
    work = _opaque_work("bench_ref_05")
    _, dst, report = _refuse_dirs(work)
    missing = work / "nope_src"
    assert_refuse(missing, dst, report)


def test_refuse_nonexistent_dst():
    """--dst must exist on disk."""
    work = _opaque_work("bench_ref_06")
    src, _, report = _refuse_dirs(work)
    missing = work / "nope_dst"
    assert_refuse(src, missing, report)


def test_refuse_absent_report_valid_src_bad_dst():
    """Valid --src + bad --dst must refuse without creating an absent report."""
    work = _opaque_work("bench_ref_absent_dst")
    work.mkdir(parents=True, exist_ok=True)
    src = work / "src"
    src.mkdir()
    os.chmod(src, 0o755)
    _chown_tree(work)
    missing_dst = work / "nope_dst"
    report = work / "out.json"
    assert not report.exists()
    proc = run_syncer(src, missing_dst, report, clear_report=False)
    assert proc.returncode == 2, (proc.returncode, proc.stderr)
    assert proc.stdout == ""
    assert proc.stderr == REFUSE_STDERR
    assert not report.exists()
    _drain_sandbox()


def test_refuse_absent_report_valid_src_symlink_dst():
    """Valid --src + symlink --dst must refuse without creating an absent report."""
    work = _opaque_work("bench_ref_absent_symdst")
    work.mkdir(parents=True, exist_ok=True)
    src = work / "src"
    src.mkdir()
    real = work / "real_dst"
    real.mkdir()
    link = work / "link_dst"
    if link.exists() or link.is_symlink():
        link.unlink()
    os.symlink("real_dst", link)
    os.chmod(src, 0o755)
    os.chmod(real, 0o755)
    _chown_tree(work)
    report = work / "out.json"
    assert not report.exists()
    proc = run_syncer(src, link, report, clear_report=False)
    assert proc.returncode == 2, (proc.returncode, proc.stderr)
    assert proc.stdout == ""
    assert proc.stderr == REFUSE_STDERR
    assert not report.exists()
    _drain_sandbox()


def test_refuse_same_resolved_tree():
    """--src and --dst must resolve to different directories."""
    work = _opaque_work("bench_ref_07")
    src, _, report = _refuse_dirs(work)
    assert_refuse(src, src, report)


def test_refuse_missing_src_flag():
    """Omitting --src must trigger the bad-args refusal."""
    work = _opaque_work("bench_ref_08")
    _, dst, report = _refuse_dirs(work)
    _assert_refuse_argv(["--dst", str(dst), "--report", str(report)], report=report)


def test_refuse_missing_dst_flag():
    """Omitting --dst must trigger the bad-args refusal."""
    work = _opaque_work("bench_ref_09")
    src, _, report = _refuse_dirs(work)
    _assert_refuse_argv(["--src", str(src), "--report", str(report)], report=report)


def test_refuse_missing_report_flag():
    """Omitting --report must trigger the bad-args refusal."""
    work = _opaque_work("bench_ref_10")
    src, dst, _ = _refuse_dirs(work)
    _assert_refuse_argv(["--src", str(src), "--dst", str(dst)])


def test_refuse_empty_report_value():
    """An empty --report value must refuse like a missing flag."""
    work = _opaque_work("bench_ref_empty_report")
    src, dst, _ = _refuse_dirs(work)
    _assert_refuse_argv(["--src", str(src), "--dst", str(dst), "--report", ""])


def test_refuse_empty_src_value():
    """An empty --src value must refuse."""
    work = _opaque_work("bench_ref_empty_src")
    _, dst, report = _refuse_dirs(work)
    _assert_refuse_argv(["--src", "", "--dst", str(dst), "--report", str(report)], report=report)


def test_refuse_empty_dst_value():
    """An empty --dst value must refuse."""
    work = _opaque_work("bench_ref_empty_dst")
    src, _, report = _refuse_dirs(work)
    _assert_refuse_argv(["--src", str(src), "--dst", "", "--report", str(report)], report=report)


def test_refuse_bare_report_flag():
    """A trailing bare --report with no value must refuse."""
    work = _opaque_work("bench_ref_bare_report")
    src, dst, _ = _refuse_dirs(work)
    _assert_refuse_argv(["--src", str(src), "--dst", str(dst), "--report"])


def test_refuse_bare_src_flag():
    """A trailing bare --src with no value must refuse (exact stderr, not argparse)."""
    work = _opaque_work("bench_ref_bare_src")
    _, dst, report = _refuse_dirs(work)
    _assert_refuse_argv(
        ["--dst", str(dst), "--report", str(report), "--src"], report=report
    )


def test_refuse_bare_dst_flag():
    """A trailing bare --dst with no value must refuse (exact stderr, not argparse)."""
    work = _opaque_work("bench_ref_bare_dst")
    src, _, report = _refuse_dirs(work)
    _assert_refuse_argv(
        ["--src", str(src), "--report", str(report), "--dst"], report=report
    )


def test_refuse_src_value_is_next_flag():
    """`--src` adjacent to another option must still yield exact refusal text."""
    work = _opaque_work("adj_flag_src")
    _, dst, report = _refuse_dirs(work)
    _assert_refuse_argv(
        ["--src", "--dst", str(dst), "--report", str(report)], report=report
    )


def test_refuse_dst_value_is_next_flag():
    """`--dst` adjacent to another option must still yield exact refusal text."""
    work = _opaque_work("adj_flag_dst")
    src, _, report = _refuse_dirs(work)
    _assert_refuse_argv(
        ["--src", str(src), "--dst", "--report", str(report)], report=report
    )


def test_refuse_report_value_is_next_flag():
    """`--report` adjacent to another option must still yield exact refusal text."""
    work = _opaque_work("adj_flag_report")
    src, dst, report = _refuse_dirs(work)
    _assert_refuse_argv(
        ["--src", str(src), "--dst", str(dst), "--report", "--src", str(src)],
        report=report,
    )


def test_refuse_dst_nested_under_src():
    """--dst beneath --src must refuse (avoids walk extending into created dirs)."""
    work = _opaque_work("bench_ref_dst_under_src")
    work.mkdir(parents=True, exist_ok=True)
    src = work / "s"
    src.mkdir()
    dst = src / "d"
    dst.mkdir()
    os.chmod(src, 0o755)
    os.chmod(dst, 0o755)
    _chown_tree(work)
    assert_refuse(src, dst, work / "out.json")


def test_refuse_src_nested_under_dst():
    """--src beneath --dst must refuse (symmetric nesting)."""
    work = _opaque_work("bench_ref_src_under_dst")
    work.mkdir(parents=True, exist_ok=True)
    dst = work / "d"
    dst.mkdir()
    src = dst / "s"
    src.mkdir()
    os.chmod(dst, 0o755)
    os.chmod(src, 0o755)
    _chown_tree(work)
    assert_refuse(src, dst, work / "out.json")


def test_refuse_dst_nested_via_symlink_ancestor():
    """Resolved nesting via symlink ancestor must refuse (not raw argv paths)."""
    work = _opaque_work("nest_via_alias")
    work.mkdir(parents=True, exist_ok=True)
    src = work / "src"
    src.mkdir()
    child = src / "child"
    child.mkdir()
    alias = work / "alias"
    if alias.exists() or alias.is_symlink():
        alias.unlink()
    os.symlink("src", alias)
    os.chmod(src, 0o755)
    os.chmod(child, 0o755)
    _chown_tree(work)
    assert_refuse(src, alias / "child", work / "out.json")


def test_refuse_src_nested_via_symlink_ancestor():
    """Symmetric nesting via symlink ancestor must refuse."""
    work = _opaque_work("nest_via_alias_b")
    work.mkdir(parents=True, exist_ok=True)
    dst = work / "dst"
    dst.mkdir()
    nested = dst / "inner"
    nested.mkdir()
    alias = work / "alias"
    if alias.exists() or alias.is_symlink():
        alias.unlink()
    os.symlink("dst", alias)
    os.chmod(dst, 0o755)
    os.chmod(nested, 0o755)
    _chown_tree(work)
    assert_refuse(alias / "inner", dst, work / "out.json")


def test_accept_prefix_sibling_s_s2():
    """Sibling paths `s` and `s2` must succeed (not raw string-prefix nesting)."""
    work = _opaque_work("bench_accept_s_s2")
    work.mkdir(parents=True, exist_ok=True)
    src = work / "s"
    dst = work / "s2"
    src.mkdir()
    dst.mkdir()
    (src / "a.txt").write_text("hi\n", encoding="utf-8")
    os.chmod(src / "a.txt", 0o644)
    _chown_tree(work)
    report = work / "out.json"
    proc = run_syncer(src, dst, report)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == ""
    assert (dst / "a.txt").is_file()
    assert (dst / "a.txt").read_text(encoding="utf-8") == "hi\n"
    _drain_sandbox()


def test_accept_prefix_sibling_t_t2():
    """Second site: sibling `t` / `t2` must succeed under component-aware nesting."""
    work = _opaque_work("bench_accept_t_t2")
    work.mkdir(parents=True, exist_ok=True)
    src = work / "t"
    dst = work / "t2"
    src.mkdir()
    dst.mkdir()
    (src / "b.txt").write_text("bye\n", encoding="utf-8")
    os.chmod(src / "b.txt", 0o644)
    _chown_tree(work)
    report = work / "out.json"
    proc = run_syncer(src, dst, report)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == ""
    assert (dst / "b.txt").read_text(encoding="utf-8") == "bye\n"
    _drain_sandbox()


def test_refuse_fifo_src():
    """--src must refuse a FIFO (non-directory lstat result)."""
    work = _opaque_work("bench_ref_fifo_src")
    work.mkdir(parents=True, exist_ok=True)
    fifo = work / "fifo_src"
    if fifo.exists() or fifo.is_symlink():
        fifo.unlink()
    os.mkfifo(fifo)
    dst = work / "dst"
    dst.mkdir(exist_ok=True)
    _chown_tree(work)
    assert_refuse(fifo, dst, work / "out.json")


def test_refuse_fifo_dst():
    """--dst must refuse a FIFO (non-directory lstat result)."""
    work = _opaque_work("bench_ref_fifo_dst")
    src, _, report = _refuse_dirs(work)
    fifo = work / "fifo_dst"
    if fifo.exists() or fifo.is_symlink():
        fifo.unlink()
    os.mkfifo(fifo)
    _chown_tree(work)
    assert_refuse(src, fifo, report)


def _stage_socket(path: Path) -> None:
    """Create a Unix-domain socket at path (short bind + rename)."""
    import socket
    import tempfile

    if path.exists() or path.is_symlink():
        path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)
    tdir = tempfile.mkdtemp(prefix="sk", dir="/tmp")
    short = Path(tdir) / "s"
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.bind(str(short))
    finally:
        sock.close()
    os.rename(short, path)
    os.rmdir(tdir)


def test_refuse_socket_src():
    """--src must refuse a Unix-domain socket (non-directory lstat result)."""
    work = _opaque_work("bench_ref_socket_src")
    work.mkdir(parents=True, exist_ok=True)
    sock_path = work / "sock_src"
    _stage_socket(sock_path)
    dst = work / "dst"
    dst.mkdir(exist_ok=True)
    _chown_tree(work)
    assert_refuse(sock_path, dst, work / "out.json")


def test_refuse_socket_dst():
    """--dst must refuse a Unix-domain socket (non-directory lstat result)."""
    work = _opaque_work("bench_ref_socket_dst")
    src, _, report = _refuse_dirs(work)
    sock_path = work / "sock_dst"
    _stage_socket(sock_path)
    _chown_tree(work)
    assert_refuse(src, sock_path, report)


def test_refuse_same_realpath_via_alias():
    """--src and --dst must refuse when real paths match via a directory alias."""
    work = _opaque_work("bench_ref_same_alias")
    work.mkdir(parents=True, exist_ok=True)
    real = work / "real"
    real.mkdir()
    alias = work / "alias"
    if alias.exists() or alias.is_symlink():
        alias.unlink()
    os.symlink(".", alias)
    _chown_tree(work)
    # real and alias/real resolve to the same directory.
    assert_refuse(real, alias / "real", work / "out.json")


def test_accept_dashdash_src_name():
    """A directory whose name begins with -- is a legal --src value."""
    work = _opaque_work("bench_dashdash_src")
    work.mkdir(parents=True, exist_ok=True)
    src = work / "--input"
    src.mkdir()
    dst = work / "dst"
    dst.mkdir()
    _chown_tree(work)
    report = work / "out.json"
    proc = run_syncer(src, dst, report)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == ""
    data = json.loads(read_nofollow(report))
    assert data == {"copied": [""], "skipped": []}
    _drain_sandbox()


def test_accept_alias_dot_src():
    """--src naming a symlink with a trailing /. must lstat as a directory."""
    work = _opaque_work("bench_alias_dot")
    work.mkdir(parents=True, exist_ok=True)
    tree = work / "tree"
    tree.mkdir()
    alias = work / "alias"
    if alias.exists() or alias.is_symlink():
        alias.unlink()
    os.symlink("tree", alias)
    dst = work / "dst"
    dst.mkdir()
    _chown_tree(work)
    report = work / "out.json"
    src_s = str(alias) + "/."
    proc = run_syncer_argv(["--src", src_s, "--dst", str(dst), "--report", str(report)])
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == ""
    _drain_sandbox()


def test_accept_dst_via_symlink_ancestor():
    """Legal --dst reached through a symlink ancestor must succeed (not refuse)."""
    work = _opaque_work("bench_dst_via_alias")
    work.mkdir(parents=True, exist_ok=True)
    src = work / "src"
    src.mkdir()
    (src / "f").write_bytes(b"N")
    os.chmod(src / "f", 0o644)
    real = work / "real"
    real.mkdir()
    dst = real / "dst"
    dst.mkdir()
    alias = work / "alias"
    if alias.exists() or alias.is_symlink():
        alias.unlink()
    os.symlink("real", alias)
    os.chmod(src, 0o755)
    os.chmod(real, 0o755)
    os.chmod(dst, 0o755)
    _chown_tree(work)
    report = work / "report.json"
    proc = run_syncer(src, alias / "dst", report)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == ""
    assert (dst / "f").is_file()
    assert (dst / "f").read_bytes() == b"N"
    data = json.loads(read_nofollow(report))
    assert data["copied"] == ["", "f"]
    assert data["skipped"] == []
    _drain_sandbox()


def test_accept_dst_via_symlink_ancestor_b():
    """Second site: destination under an alias ancestor with nested copy."""
    work = _opaque_work("bench_dst_via_alias_b")
    work.mkdir(parents=True, exist_ok=True)
    src = work / "src"
    src.mkdir()
    (src / "x.txt").write_text("hi\n", encoding="utf-8")
    os.chmod(src / "x.txt", 0o644)
    base = work / "base"
    base.mkdir()
    dst = base / "out"
    dst.mkdir()
    alias = work / "link"
    if alias.exists() or alias.is_symlink():
        alias.unlink()
    os.symlink("base", alias)
    os.chmod(src, 0o755)
    os.chmod(base, 0o755)
    os.chmod(dst, 0o755)
    _chown_tree(work)
    report = work / "r.json"
    proc = run_syncer(src, alias / "out", report)
    assert proc.returncode == 0, proc.stderr
    assert (dst / "x.txt").read_text(encoding="utf-8") == "hi\n"
    _drain_sandbox()


def test_accept_report_inside_destination():
    """--report may legally live under --dst; must not refuse containment."""
    work = _opaque_work("bench_report_in_dst")
    work.mkdir(parents=True, exist_ok=True)
    src = work / "src"
    dst = work / "dst"
    src.mkdir()
    dst.mkdir()
    os.chmod(src, 0o755)
    os.chmod(dst, 0o755)
    _chown_tree(work)
    report = dst / "out.json"
    proc = run_syncer(src, dst, report)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == ""
    assert report.is_file()
    assert json.loads(read_nofollow(report)) == {"copied": [""], "skipped": []}
    _drain_sandbox()


def test_accept_report_inside_destination_with_file():
    """Report under dst after a successful copy must still be exact."""
    work = _opaque_work("bench_report_in_dst_b")
    work.mkdir(parents=True, exist_ok=True)
    src = work / "src"
    dst = work / "dst"
    src.mkdir()
    dst.mkdir()
    (src / "a.txt").write_text("a\n", encoding="utf-8")
    os.chmod(src / "a.txt", 0o644)
    os.chmod(src, 0o755)
    os.chmod(dst, 0o755)
    _chown_tree(work)
    report = dst / "sync.json"
    proc = run_syncer(src, dst, report)
    assert proc.returncode == 0, proc.stderr
    assert (dst / "a.txt").is_file()
    data = json.loads(read_nofollow(report))
    assert data["copied"] == ["", "a.txt"]
    assert data["skipped"] == []
    _drain_sandbox()


def test_refuse_nonexistent_report_path():
    """Refusal must not create a report file when the path did not exist."""
    work = _opaque_work("bench_ref_11")
    work.mkdir(parents=True, exist_ok=True)
    real = work / "real_src"
    real.mkdir()
    link = work / "link_src"
    if link.exists() or link.is_symlink():
        link.unlink()
    os.symlink("real_src", link)
    dst = work / "dst"
    dst.mkdir()
    _chown_tree(work)
    report = work / "missing" / "out.json"
    assert not report.exists()
    # Parent of report may be missing; do not pre-create.
    proc = run_syncer(link, dst, report, clear_report=False)
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert proc.stderr == REFUSE_STDERR
    assert not report.exists()
    assert not (work / "missing").exists()
    _drain_sandbox()


def test_refuse_preserves_report_symlink_target():
    """Refusal must not alter bytes when --report is a symlink to a regular file."""
    work = _opaque_work("bench_ref_12")
    work.mkdir(parents=True, exist_ok=True)
    real = work / "real_src"
    real.mkdir()
    link = work / "link_src"
    if link.exists() or link.is_symlink():
        link.unlink()
    os.symlink("real_src", link)
    dst = work / "dst"
    dst.mkdir()
    sentinel = work / "sentinel.dat"
    sentinel.write_bytes(REPORT_SENTINEL)
    report = work / "via_link.json"
    if report.exists() or report.is_symlink():
        report.unlink()
    os.symlink(sentinel.name, report)
    _chown_tree(work)
    proc = run_syncer(link, dst, report, clear_report=False)
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert proc.stderr == REFUSE_STDERR
    assert read_nofollow(sentinel) == REPORT_SENTINEL
    assert report.is_symlink()
    _drain_sandbox()


# --- Sealed scale ---


@pytest.mark.parametrize("name", SEALED_CASES)
def test_sealed_case(name: str):
    """Sealed manifest replay: report bytes and destination tree match mint."""
    snap = SEALED_SNAP[name]
    manifest = json.loads(snap["manifest"])
    expect_report = snap["expect_report"]
    expect_tree = load_expect_tree(snap["expect_tree"])
    work = WORK / CASE_ID[name]
    src, dst = stage_manifest(manifest, work / "tree")
    assert_success(src, dst, expect_report, expect_tree, work / "out.json")


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
