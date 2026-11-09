"""Verifier for capability-aware secret audit.

Rebuilds the submitted Go auditor, materializes ownership-aware trees from
sealed manifests held only in verifier memory, and checks report bytes plus
refusal behavior against CAPABILITY.md.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from trees import grant_sandbox_search, materialize

APP = Path("/app")
PKG = APP / "cmd" / "casaudit"
GOMOD = APP / "go.mod"
AGENT_BIN = APP / "bin" / "casaudit"
# Root-owned seal dir: sandbox uid must not replace the rebuilt auditor.
SEAL_DIR = Path("/var/verifier-seal")
BIN = SEAL_DIR / "casaudit"
TESTS = Path("/tests")
SEALED = TESTS / "sealed"
WORK = Path("/work/out")
SANDBOX_UID = 2000
REFUSE_TEXT = b"refuse: bad args\n"
EMPTY_REPORT = b'{\n  "findings": [],\n  "skipped": []\n}\n'
RUN_TIMEOUT = 20
_BIN_FINGERPRINT: tuple[int, int, int, int] | None = None


def _canon_bytes(obj: object) -> bytes:
    """Exact CAPABILITY.md report bytes (indent=2, sort_keys, ensure_ascii=False, trailing LF)."""
    return (json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")



def _sandbox_argv(argv: list[str]) -> list[str]:
    return [
        "/usr/bin/setpriv",
        f"--reuid={SANDBOX_UID}",
        f"--regid={SANDBOX_UID}",
        "--clear-groups",
        "--no-new-privs",
        "--",
        *argv,
    ]


def _clean_child_env() -> dict[str, str]:
    """Strip pytest identity leaks so candidate code cannot key off the active test name."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST")}
    env.pop("PYTEST_CURRENT_TEST", None)
    return env


def _opaque_work(key: str) -> Path:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    path = WORK / f"w-{digest}"
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    os.chown(path, SANDBOX_UID, SANDBOX_UID)
    os.chmod(path, 0o755)
    return path


def _snapshot_sealed() -> dict[str, dict[str, bytes]]:
    out: dict[str, dict[str, bytes]] = {}
    if not SEALED.is_dir():
        return out
    for case in sorted(p.name for p in SEALED.iterdir() if p.is_dir()):
        cdir = SEALED / case
        out[case] = {
            "manifest.json": (cdir / "manifest.json").read_bytes(),
            "roster.json": (cdir / "roster.json").read_bytes(),
            "expect.json": (cdir / "expect.json").read_bytes(),
        }
    return out


SEALED_SNAP = _snapshot_sealed()
SEALED_CASES = sorted(SEALED_SNAP)
if SEALED.is_dir():
    shutil.rmtree(SEALED)
try:
    os.chmod(str(TESTS), 0o700)
except OSError:
    pass


def _pkg_go_files() -> list[Path]:
    if not PKG.is_dir():
        return []
    return sorted(p for p in PKG.glob("*.go") if p.is_file())


def _foreign_go_files() -> list[Path]:
    """Go sources outside /app/cmd/casaudit are not part of the rebuild package."""
    pkg_root = PKG.resolve()
    out: list[Path] = []
    for p in APP.rglob("*.go"):
        try:
            p.resolve().relative_to(pkg_root)
        except ValueError:
            out.append(p)
    return sorted(out)


def _bin_fingerprint(path: Path) -> tuple[int, int, int, int]:
    st = os.lstat(path)
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)


def _assert_bin_sealed() -> None:
    """Ensure the sealed rebuilt auditor was not replaced by the sandbox after rebuild."""
    global _BIN_FINGERPRINT
    # Parent must be searchable by sandbox uid (0755) but not writable (root-owned).
    dst = os.lstat(SEAL_DIR)
    assert dst.st_uid == 0 and dst.st_gid == 0, f"{SEAL_DIR} must stay root-owned"
    assert (dst.st_mode & 0o777) == 0o755, f"{SEAL_DIR} mode must remain 0755 (searchable)"
    assert BIN.is_file(), f"missing sealed rebuild {BIN}"
    assert not BIN.is_symlink(), f"sealed rebuild must not be a symlink: {BIN}"
    st = os.lstat(BIN)
    assert st.st_uid == 0 and st.st_gid == 0, f"{BIN} must stay root-owned"
    assert (st.st_mode & 0o777) == 0o555, f"{BIN} mode must remain 0555"
    assert _BIN_FINGERPRINT is not None
    assert _bin_fingerprint(BIN) == _BIN_FINGERPRINT, (
        f"{BIN} changed after rebuild (sandbox replacement?)"
    )


def _require_agent_bin() -> None:
    """Require the contract deliverable at /app/bin/casaudit (may need mode restore)."""
    assert AGENT_BIN.is_file(), f"missing contract binary {AGENT_BIN}"
    # Harbor artifact staging can drop +x; restore before invoking.
    os.chmod(AGENT_BIN, 0o755)
    assert os.access(AGENT_BIN, os.X_OK), f"{AGENT_BIN} is not executable"


def ensure_binary() -> Path:
    global _BIN_FINGERPRINT
    assert GOMOD.is_file(), f"missing {GOMOD}"
    # Contract path from instruction.md: a working auditor must exist here.
    _require_agent_bin()
    foreign = _foreign_go_files()
    assert not foreign, f"Go sources outside /app/cmd/casaudit: {foreign}"
    assert _pkg_go_files(), "no Go sources under /app/cmd/casaudit"
    if BIN.is_file() and _BIN_FINGERPRINT is not None:
        _assert_bin_sealed()
        return BIN
    SEAL_DIR.mkdir(parents=True, exist_ok=True)
    os.chown(SEAL_DIR, 0, 0)
    # 0755: sandbox uid can traverse+execute the sealed binary; cannot create/replace.
    os.chmod(SEAL_DIR, 0o755)
    staging = SEAL_DIR / "casaudit.building"
    if staging.exists():
        staging.unlink()
    build = subprocess.run(
        ["go", "build", "-o", str(staging), "/app/cmd/casaudit"],
        cwd=str(APP),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert build.returncode == 0, f"go build failed:\n{build.stderr}\n{build.stdout}"
    assert staging.is_file()
    os.chown(staging, 0, 0)
    os.chmod(staging, 0o555)
    os.replace(staging, BIN)
    os.chown(BIN, 0, 0)
    os.chmod(BIN, 0o555)
    _BIN_FINGERPRINT = _bin_fingerprint(BIN)
    _assert_bin_sealed()
    return BIN


def _run_exe(exe: Path, extra: list[str]) -> subprocess.CompletedProcess[bytes]:
    if exe.resolve() == BIN.resolve():
        _assert_bin_sealed()
    # Bytes mode: text=True would translate CRLF into LF and hide a contract violation.
    return subprocess.run(
        _sandbox_argv([str(exe), *extra]),
        capture_output=True,
        text=False,
        timeout=RUN_TIMEOUT,
        cwd="/sandbox",
        env=_clean_child_env(),
    )


def _report_body(report: Path) -> bytes:
    if report.is_symlink():
        target = Path(os.readlink(report))
        if not target.is_absolute():
            target = report.parent / target
        return target.read_bytes()
    return report.read_bytes()


def run_audit(root: Path, audience: Path, report: Path) -> subprocess.CompletedProcess[bytes]:
    """Grade both the delivered auditor and the sealed rebuilt package."""
    ensure_binary()
    bodies: list[bytes] = []
    procs: list[subprocess.CompletedProcess[bytes]] = []
    was_symlink = report.is_symlink()
    link_raw: str | None = None
    link_resolved: Path | None = None
    before_target: bytes | None = None
    if was_symlink:
        link_raw = os.readlink(report)
        link_resolved = Path(link_raw)
        if not link_resolved.is_absolute():
            link_resolved = report.parent / link_resolved
        before_target = link_resolved.read_bytes()
    for i, exe in enumerate((AGENT_BIN, BIN)):
        if was_symlink:
            # Restore original link spelling (relative or absolute) + prior target bytes.
            assert link_raw is not None and link_resolved is not None and before_target is not None
            if report.exists() or report.is_symlink():
                if not report.is_symlink():
                    report.unlink()
                    report.symlink_to(link_raw)
            else:
                report.symlink_to(link_raw)
            link_resolved.write_bytes(before_target)
        elif i > 0 and report.exists():
            report.unlink()
        proc = _run_exe(
            exe,
            ["--root", str(root), "--audience", str(audience), "--report", str(report)],
        )
        assert proc.returncode == 0, f"{exe} stderr={proc.stderr!r} stdout={proc.stdout!r}"
        assert proc.stdout == b""
        if was_symlink:
            assert report.is_symlink(), f"{exe} must write through --report symlink"
            assert os.readlink(report) == link_raw, f"{exe} must preserve symlink target spelling"
        bodies.append(_report_body(report))
        procs.append(proc)
    assert bodies[0] == bodies[1], "deliverable and rebuilt reports diverge"
    return procs[0]


def run_argv(extra: list[str]) -> subprocess.CompletedProcess[bytes]:
    """Grade both deliverable and rebuilt with raw argv (preserves // spelling)."""
    ensure_binary()
    report: Path | None = None
    if "--report" in extra:
        idx = extra.index("--report")
        if idx + 1 < len(extra):
            report = Path(extra[idx + 1])
    procs: list[subprocess.CompletedProcess[bytes]] = []
    bodies: list[bytes] = []
    for i, exe in enumerate((AGENT_BIN, BIN)):
        if i > 0 and report is not None and report.exists() and not report.is_symlink():
            report.unlink()
        proc = _run_exe(exe, extra)
        procs.append(proc)
        if report is not None and (report.exists() or report.is_symlink()):
            bodies.append(_report_body(report))
    assert procs[0].returncode == procs[1].returncode, (
        f"deliverable/rebuilt returncodes diverge: {procs[0].returncode} vs {procs[1].returncode}"
    )
    assert procs[0].stdout == procs[1].stdout
    assert procs[0].stderr == procs[1].stderr
    if len(bodies) == 2:
        assert bodies[0] == bodies[1], "deliverable and rebuilt reports diverge"
    return procs[0]


def _check_refuse_outcome(
    proc: subprocess.CompletedProcess[bytes],
    report: Path | None,
    before: bytes | None,
    was_symlink: bool,
    link_target: Path | None,
) -> None:
    assert proc.returncode == 2
    assert proc.stdout == b""
    assert proc.stderr == REFUSE_TEXT
    if report is not None and before is None:
        try:
            os.lstat(report)
            raise AssertionError("refusal must not create --report when it was absent")
        except FileNotFoundError:
            pass
    elif report is not None and before is not None:
        if was_symlink:
            assert report.is_symlink(), "refusal must not replace a report symlink with a regular file"
            target = Path(os.readlink(report))
            if not target.is_absolute():
                target = report.parent / target
            assert target.resolve() == link_target.resolve()
            assert target.read_bytes() == before
        else:
            assert report.is_file(), "refusal must not delete a preexisting regular report"
            assert report.read_bytes() == before


def assert_success(root: Path, audience: Path, expect_bytes: bytes, label: str = "ok") -> None:
    """Require identical success bytes from both the deliverable and the rebuilt package."""
    ensure_binary()
    for exe, tag in ((AGENT_BIN, "agent"), (BIN, "rebuilt")):
        report = writable_report(f"{label}-{tag}")
        proc = _run_exe(
            exe,
            ["--root", str(root), "--audience", str(audience), "--report", str(report)],
        )
        assert proc.returncode == 0, f"{tag} stderr={proc.stderr!r} stdout={proc.stdout!r}"
        assert proc.stdout == b""
        assert report.read_bytes() == expect_bytes, f"{tag} report mismatch"


def assert_refuse(extra: list[str], report: Path | None = None, before: bytes | None = None) -> None:
    """Require identical refusal disposition from both the deliverable and the rebuilt package."""
    ensure_binary()
    was_symlink = report is not None and report.is_symlink()
    link_target = None
    if was_symlink:
        link_target = Path(os.readlink(report))
        if not link_target.is_absolute():
            link_target = report.parent / link_target
    for exe in (AGENT_BIN, BIN):
        proc = _run_refusal_nofollow(exe, extra)
        _check_refuse_outcome(proc, report, before, was_symlink, link_target)


def _empty_tree_aud(work: Path) -> tuple[Path, Path]:
    tree = work / "tree"
    tree.mkdir()
    os.chmod(tree, 0o755)
    os.chown(tree, 0, 0)
    grant_sandbox_search(work)
    aud = work / "a.json"
    return tree, aud


def _write_aud(aud: Path, obj) -> None:
    if isinstance(obj, (bytes, bytearray)):
        aud.write_bytes(bytes(obj))
    else:
        aud.write_text(json.dumps(obj, ensure_ascii=False) + "\n", encoding="utf-8")
    os.chmod(aud, 0o644)


def stage_case(name: str) -> tuple[Path, Path, bytes]:
    snap = SEALED_SNAP[name]
    work = _opaque_work(f"case-{name}")
    manifest = json.loads(snap["manifest.json"])
    tree = materialize(work / "fs", manifest)
    roster = work / "roster.json"
    roster.write_bytes(snap["roster.json"])
    os.chmod(roster, 0o644)
    os.chown(roster, 0, 0)
    return tree, roster, snap["expect.json"]


def writable_report(label: str) -> Path:
    work = _opaque_work(f"report-{label}")
    return work / "report.json"


def _sandbox_owned_file(path: Path, content: bytes) -> None:
    """Write bytes owned by the sandbox uid so refusal cleanup that truncates euid files fails."""
    path.write_bytes(content)
    os.chown(path, SANDBOX_UID, SANDBOX_UID)
    os.chmod(path, 0o644)


def test_sources_present():
    """Submitted package sources under /app/cmd/casaudit are present for rebuild."""
    assert GOMOD.is_file()
    assert _pkg_go_files(), "expected Go sources under /app/cmd/casaudit"
    foreign = _foreign_go_files()
    assert foreign == [], f"Go sources outside /app/cmd/casaudit: {foreign}"


def test_contract_binary_present():
    """Delivered /app/bin/casaudit must correctly audit a distinguishing exposure."""
    _require_agent_bin()
    # Distinguishing case: world-readable secret, unintended subject must appear.
    # An empty-report stub at the contract path fails this check.
    work = _opaque_work("agent-bin-expose")
    tree = work / "tree"
    tree.mkdir()
    os.chmod(tree, 0o755)
    os.chown(tree, 0, 0)
    secret = tree / "secret"
    secret.write_text("x\n", encoding="utf-8")
    os.chown(secret, 1000, 1000)
    os.chmod(secret, 0o644)
    grant_sandbox_search(work)
    aud = work / "a.json"
    _write_aud(
        aud,
        {
            "entries": [{"path": "secret", "allow_uids": [], "allow_gids": []}],
            "subjects": [{"uid": 2000, "gids": [2000]}],
        },
    )
    report = work / "r.json"
    proc = _run_exe(
        AGENT_BIN,
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == b""
    body = json.loads(report.read_text(encoding="utf-8"))
    assert body["skipped"] == []
    assert len(body["findings"]) == 1
    finding = body["findings"][0]
    assert finding["path"] == "secret"
    assert finding["exposure"] == ["uid:2000"]
    assert finding["remediation"]["mode"] == "0640"


def test_build_succeeds():
    """Submitted /app/cmd/casaudit package rebuilds into a root-sealed auditor."""
    global _BIN_FINGERPRINT
    if BIN.exists():
        BIN.unlink()
    _BIN_FINGERPRINT = None
    ensure_binary()
    _assert_bin_sealed()


def test_rebuilt_empty_audience_not_world_stub():
    """Rebuilt sources must emit an empty report for an empty audience (not a world_readable stub)."""
    ensure_binary()
    work = _opaque_work("rebuilt-empty")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, {"entries": [], "subjects": []})
    report = work / "r.json"
    proc = _run_exe(
        BIN,
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
    )
    assert proc.returncode == 0, proc.stderr
    assert report.read_text(encoding="utf-8") == '{\n  "findings": [],\n  "skipped": []\n}\n'
    _assert_bin_sealed()

@pytest.mark.parametrize("name", SEALED_CASES)
def test_sealed_case(name: str):
    """Sealed ownership trees reproduce the minted report bytes."""
    tree, audience, expect = stage_case(name)
    assert_success(tree, audience, expect, label=f"sealed-{name}")


def test_bench_world_readable():
    """Other-readable secret records unintended uids and clears other-read."""
    tree, audience, expect = stage_case("world_readable")
    assert_success(tree, audience, expect, label="bench-world")
    data = json.loads(expect)
    assert data["findings"][0]["remediation"]["mode"] == "0640"


def test_bench_group_overshare():
    """Group-readable secret exposes subjects matching the file gid when disallowed."""
    tree, audience, expect = stage_case("group_overshare")
    assert_success(tree, audience, expect, label="bench-group")


def test_bench_parent_blocks_other():
    """Parent without other-execute blocks world exposure despite a world-readable leaf."""
    tree, audience, expect = stage_case("parent_blocks_other")
    assert_success(tree, audience, expect, label="bench-parent-block")
    assert json.loads(expect)["findings"] == []


def test_bench_deep_mid_noexec():
    """Execute denial at a deep ordinary directory blocks exposure (not only shallow parents)."""
    tree, audience, expect = stage_case("deep_mid_noexec")
    assert_success(tree, audience, expect, label="bench-deep-mid-noexec")
    assert json.loads(expect)["findings"] == []
    assert json.loads(expect)["skipped"] == []


def test_bench_escaped_final():
    """Final symlink leaving the root is skipped as unsupported (final-link rule)."""
    tree, audience, expect = stage_case("escaped_final")
    assert_success(tree, audience, expect, label="bench-escape")
    assert json.loads(expect)["skipped"][0]["reason"] == "unsupported"


def test_bench_cyclic_final_unsupported():
    """Cyclic final symlink fails resolution and is skipped as unsupported (not missing)."""
    tree, audience, expect = stage_case("cyclic_final_unsupported")
    assert_success(tree, audience, expect, label="bench-cyclic-final")
    skipped = json.loads(expect)["skipped"]
    assert len(skipped) == 1
    assert skipped[0]["path"] == "loop"
    assert skipped[0]["reason"] == "unsupported"


def test_bench_safe_symlink_inner():
    """In-root directory symlink still audits the resolved regular file."""
    tree, audience, expect = stage_case("safe_symlink_inner")
    assert_success(tree, audience, expect, label="bench-safe-link")
    assert json.loads(expect)["findings"][0]["path"] == "alias/token"


def test_bench_intermediate_file_symlink_unsupported():
    """In-root intermediate symlink to a regular file is skipped as unsupported."""
    tree, audience, expect = stage_case("intermediate_file_symlink_unsupported")
    assert_success(tree, audience, expect, label="bench-inter-file-link")
    skipped = json.loads(expect)["skipped"]
    assert len(skipped) == 1
    assert skipped[0]["reason"] == "unsupported"
    assert skipped[0]["path"] == "alias/child"


def test_bench_intermediate_fifo_symlink_unsupported():
    """In-root intermediate symlink to a FIFO is unsupported, not missing."""
    tree, audience, expect = stage_case("intermediate_fifo_symlink_unsupported")
    assert_success(tree, audience, expect, label="bench-inter-fifo-link")
    assert json.loads(expect)["skipped"] == [
        {"path": "alias/child", "reason": "unsupported"}
    ]


def test_bench_intermediate_device_symlink_unsupported():
    """In-root intermediate symlink to a character device is unsupported, not missing."""
    tree, audience, expect = stage_case("intermediate_device_symlink_unsupported")
    assert_success(tree, audience, expect, label="bench-inter-dev-link")
    assert json.loads(expect)["skipped"] == [
        {"path": "alias/child", "reason": "unsupported"}
    ]


def test_bench_intermediate_socket_symlink_unsupported():
    """In-root intermediate symlink to a socket is unsupported, not missing."""
    tree, audience, expect = stage_case("intermediate_socket_symlink_unsupported")
    assert_success(tree, audience, expect, label="bench-inter-sock-link")
    assert json.loads(expect)["skipped"] == [
        {"path": "alias/child", "reason": "unsupported"}
    ]


def test_bench_file_intermediate_unsupported():
    """Ordinary regular file as an intermediate path component is unsupported."""
    tree, audience, expect = stage_case("file_intermediate_unsupported")
    assert_success(tree, audience, expect, label="bench-file-inter")
    skipped = json.loads(expect)["skipped"]
    assert len(skipped) == 1
    assert skipped[0]["path"] == "a/child"
    assert skipped[0]["reason"] == "unsupported"


def test_bench_intermediate_link_resolved_dir_denies():
    """Execute denial on an intermediate link's resolved directory itself blocks exposure."""
    tree, audience, expect = stage_case("intermediate_link_resolved_dir_denies")
    assert_success(tree, audience, expect, label="bench-link-resolved-deny")
    assert json.loads(expect)["findings"] == []
    assert json.loads(expect)["skipped"] == []


def test_bench_other_exec_no_read_dir():
    """Other-execute without other-read on a directory still allows other-class traversal."""
    tree, audience, expect = stage_case("other_exec_no_read_dir")
    assert_success(tree, audience, expect, label="bench-other-x-dir")
    findings = json.loads(expect)["findings"]
    assert len(findings) == 1
    assert findings[0]["path"] == "d/s"
    assert findings[0]["exposure"] == ["uid:2000"]
    assert findings[0]["remediation"]["mode"] == "0640"


def test_bench_root_owner_exec_exposes():
    """Owner-execute on a 0700 --root is decisive (not other-execute alone)."""
    tree, audience, expect = stage_case("root_owner_exec_exposes")
    assert_success(tree, audience, expect, label="bench-root-owner-exec")
    findings = json.loads(expect)["findings"]
    assert len(findings) == 1
    assert findings[0]["exposure"] == ["uid:0"]
    assert findings[0]["remediation"]["mode"] == "0400"


def test_bench_root_group_exec_exposes():
    """Group-execute on a 0710 --root is decisive (not owner/other alone)."""
    tree, audience, expect = stage_case("root_group_exec_exposes")
    assert_success(tree, audience, expect, label="bench-root-group-exec")
    findings = json.loads(expect)["findings"]
    assert len(findings) == 1
    assert findings[0]["exposure"] == ["uid:2000"]
    assert findings[0]["remediation"]["mode"] == "0640"


def test_bench_setgid_preserved():
    """Remediation keeps setgid while clearing other-read."""
    tree, audience, expect = stage_case("setgid_preserved")
    assert_success(tree, audience, expect, label="bench-setgid")
    assert json.loads(expect)["findings"][0]["remediation"]["mode"] == "2640"


def test_bench_sticky_preserved():
    """Remediation keeps sticky while clearing other-read."""
    tree, audience, expect = stage_case("sticky_preserved")
    assert_success(tree, audience, expect, label="bench-sticky")
    assert json.loads(expect)["findings"][0]["remediation"]["mode"] == "1640"


def test_bench_uid0_not_magic():
    """Uid 0 does not bypass DAC class rules."""
    tree, audience, expect = stage_case("uid0_not_magic")
    assert_success(tree, audience, expect, label="bench-uid0")
    assert json.loads(expect)["findings"] == []


def test_bench_sort_findings():
    """Findings are sorted by path regardless of audience entry order."""
    tree, audience, expect = stage_case("sort_findings")
    assert_success(tree, audience, expect, label="bench-sort")
    paths = [f["path"] for f in json.loads(expect)["findings"]]
    assert paths == sorted(paths)


def _strace_stat_trace(exe: Path, root: Path, audience: Path, report: Path) -> str:
    """Run the auditor under strace; return raw trace text.

    strace itself runs as root and writes under /var/verifier-strace (root-owned,
    not sandbox-writable). Only the auditor drops to the sandbox uid via setpriv,
    so the candidate cannot truncate or forge the log.
    """
    trace_dir = Path("/var/verifier-strace")
    assert trace_dir.is_dir(), "verifier strace directory missing"
    st = os.stat(trace_dir)
    assert st.st_uid == 0 and (st.st_mode & 0o022) == 0, "strace dir must be root-owned and not group/other-writable"
    trace = trace_dir / f"strace-{hashlib.sha256(str(report).encode()).hexdigest()[:12]}.log"
    if trace.exists():
        trace.unlink()
    # Root-run strace; setpriv wraps only the auditor.
    argv = [
        "strace",
        "-f",
        "-e",
        "trace=newfstatat,lstat,stat",
        "-o",
        str(trace),
        *_sandbox_argv(
            [
                str(exe),
                "--root",
                str(root),
                "--audience",
                str(audience),
                "--report",
                str(report),
            ]
        ),
    ]
    proc = subprocess.run(
        argv,
        capture_output=True,
        text=False,
        timeout=RUN_TIMEOUT,
        cwd="/sandbox",
        env=_clean_child_env(),
    )
    assert proc.returncode == 0, f"strace/audit failed: {proc.stderr!r}"
    assert proc.stdout == b""
    assert trace.is_file(), "strace did not create root-owned trace log"
    tstat = os.stat(trace)
    assert tstat.st_uid == 0, "strace log must be root-owned"
    assert (tstat.st_mode & 0o022) == 0, "strace log must not be group/other-writable"
    # Sandbox uid must not be able to forge walk-order / lstat traces.
    forge = subprocess.run(
        _sandbox_argv(["/bin/sh", "-c", f"printf forged > {trace}"]),
        capture_output=True,
        text=False,
        timeout=5,
        cwd="/sandbox",
        env=_clean_child_env(),
    )
    assert forge.returncode != 0, "sandbox must not be able to rewrite strace log"
    text = trace.read_text(encoding="utf-8", errors="replace")
    assert b"forged" not in text.encode("utf-8", errors="replace")
    return text


_NOFOLLOW_ONLY = ("lstat", "lstat64", "readlink", "readlinkat")
_NOFOLLOW_FLAGGED = ("newfstatat", "fstatat64", "statx", "openat", "openat2", "open")


def _syscall_is_nofollow(line: str) -> bool:
    m = re.match(r"^([a-z0-9_]+)\(", line)
    if not m:
        return True
    name = m.group(1)
    if name in _NOFOLLOW_ONLY:
        return True
    if name in ("newfstatat", "fstatat64", "statx"):
        return "AT_SYMLINK_NOFOLLOW" in line
    if name in ("open", "openat", "openat2"):
        return "O_NOFOLLOW" in line
    return False


def _run_refusal_nofollow(exe: Path, extra: list[str]) -> subprocess.CompletedProcess[bytes]:
    """Run a refusal; when --report is a symlink, strace every path syscall and require no-follow access to link and target."""
    idx = extra.index("--report") + 1 if "--report" in extra else len(extra)
    report = Path(extra[idx]) if idx < len(extra) and extra[idx] else None
    if report is None or not report.is_symlink():
        return _run_exe(exe, extra)
    if exe.resolve() == BIN.resolve():
        _assert_bin_sealed()
    target = Path(os.readlink(report))
    if not target.is_absolute():
        target = report.parent / target
    trace_dir = Path("/var/verifier-strace")
    st = os.stat(trace_dir)
    assert st.st_uid == 0 and (st.st_mode & 0o022) == 0, "strace dir must be root-owned and not group/other-writable"
    prefix = trace_dir / f"refuse-{hashlib.sha256((str(report) + str(exe)).encode()).hexdigest()[:12]}"
    for old in trace_dir.glob(prefix.name + ".*"):
        old.unlink()
    proc = subprocess.run(
        ["strace", "-ff", "-qq", "-s", "4096", "-e", "trace=%file", "-o", str(prefix),
         *_sandbox_argv([str(exe), *extra])],
        capture_output=True,
        text=False,
        timeout=RUN_TIMEOUT,
        cwd="/sandbox",
        env=_clean_child_env(),
    )
    logs = sorted(trace_dir.glob(prefix.name + ".*"))
    assert logs and all(os.stat(p).st_uid == 0 for p in logs), "strace did not create root-owned trace logs"
    text = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in logs)
    assert "execve(" in text, "strace trace is empty"
    watched = {str(report), report.name, str(target), str(target.resolve()), target.name}
    bad: list[str] = []
    for line in text.splitlines():
        names = re.findall(r'"((?:[^"\\]|\\.)*)"', line)
        hit = any(n in watched or n.endswith("/" + report.name) or n.endswith("/" + target.name) for n in names)
        if hit and not line.startswith("execve(") and not _syscall_is_nofollow(line):
            bad.append(line.strip())
    assert not bad, f"refusal followed the --report symlink: {bad[:5]}"
    return proc


def _strace_lstat_paths(exe: Path, root: Path, audience: Path, report: Path) -> list[str]:
    """Return filesystem paths passed to lstat/newfstatat/stat while auditing under strace."""
    text = _strace_stat_trace(exe, root, audience, report)
    # newfstatat(AT_FDCWD, "path", ...) or lstat("path", ...) or stat("path", ...)
    found: list[str] = []
    for m in re.finditer(r'(?:newfstatat\([^,]+,\s*|lstat\(|stat\()\s*"([^"]+)"', text):
        found.append(m.group(1))
    return found


def _path_basename_is(path: str, name: str) -> bool:
    base = path.rstrip("/").rsplit("/", 1)[-1]
    return base == name


def _assert_component_lstat_only(text: str, basename: str) -> None:
    """Require every metadata read of basename to be lstat / newfstatat+AT_SYMLINK_NOFOLLOW.

    Applies to ordinary path components and to canonical targets after EvalSymlinks.
    Distinguishes os.Stat (newfstatat flags=0) from os.Lstat (AT_SYMLINK_NOFOLLOW).
    """
    hits = 0
    for line in text.splitlines():
        m = re.search(r'newfstatat\([^,]+,\s*"([^"]+)"', line)
        if m and _path_basename_is(m.group(1), basename):
            hits += 1
            nofollow = "AT_SYMLINK_NOFOLLOW" in line or re.search(r",\s*0x100\s*\)", line)
            following = re.search(r",\s*0\s*\)\s*=", line) is not None and "AT_SYMLINK_NOFOLLOW" not in line
            assert nofollow and not following, (
                f"component {basename!r} metadata used following stat: {line}"
            )
            continue
        m = re.search(r'\blstat\("([^"]+)"', line)
        if m and _path_basename_is(m.group(1), basename):
            hits += 1
            continue
        m = re.search(r'\bstat\("([^"]+)"', line)
        if m and _path_basename_is(m.group(1), basename):
            raise AssertionError(f"component {basename!r} metadata used bare following stat: {line}")
    assert hits >= 1, f"expected lstat-family metadata reads of component {basename!r}"


# Backward-compatible name used by earlier SV11 sites.
_assert_resolved_target_lstat_only = _assert_component_lstat_only


def test_audience_walk_order_follows_entry_order_not_sorted_paths():
    """Entries are walked in audience order (z before a) even though findings sort a before z."""
    work = _opaque_work("walk-order-za")
    tree = work / "tree"
    tree.mkdir()
    os.chmod(tree, 0o755)
    os.chown(tree, 0, 0)
    for name in ("z", "a"):
        p = tree / name
        p.write_text("x\n", encoding="utf-8")
        os.chown(p, 1000, 1000)
        os.chmod(p, 0o644)
    grant_sandbox_search(work)
    aud = work / "a.json"
    _write_aud(
        aud,
        {
            "entries": [
                {"path": "z", "allow_uids": [], "allow_gids": []},
                {"path": "a", "allow_uids": [], "allow_gids": []},
            ],
            "subjects": [{"uid": 2000, "gids": []}],
        },
    )
    ensure_binary()
    for exe, tag in ((AGENT_BIN, "agent"), (BIN, "rebuilt")):
        report = writable_report(f"walk-order-{tag}")
        paths = _strace_lstat_paths(exe, tree, aud, report)
        # Last leaf appearance must also be audience-ordered: preliminary probes in
        # audience order followed by a sorted real walk would make first_z < first_a
        # while last_z > last_a.
        z_idxs = [i for i, p in enumerate(paths) if _path_basename_is(p, "z")]
        a_idxs = [i for i, p in enumerate(paths) if _path_basename_is(p, "a")]
        assert z_idxs and a_idxs, f"{tag} missing leaf lstats: {paths[:40]!r}"
        assert z_idxs[0] < a_idxs[0], (
            f"{tag} walked a before z (sorted-entry bug): z@{z_idxs[0]} a@{a_idxs[0]} in {paths[:40]!r}"
        )
        assert z_idxs[-1] < a_idxs[-1], (
            f"{tag} last walk of a preceded last walk of z "
            f"(probe-then-sort bug): last_z@{z_idxs[-1]} last_a@{a_idxs[-1]} in {paths!r}"
        )
        body = json.loads(report.read_bytes())
        assert [f["path"] for f in body["findings"]] == ["a", "z"]


def test_audience_walk_order_second_pair_y_before_b():
    """A second entry-order pair (y before b) is also walked before report-path sorting."""
    work = _opaque_work("walk-order-yb")
    tree = work / "tree"
    tree.mkdir()
    os.chmod(tree, 0o755)
    os.chown(tree, 0, 0)
    for name in ("y", "b"):
        p = tree / name
        p.write_text("x\n", encoding="utf-8")
        os.chown(p, 1000, 1000)
        os.chmod(p, 0o644)
    grant_sandbox_search(work)
    aud = work / "a.json"
    _write_aud(
        aud,
        {
            "entries": [
                {"path": "y", "allow_uids": [], "allow_gids": []},
                {"path": "b", "allow_uids": [], "allow_gids": []},
            ],
            "subjects": [{"uid": 2000, "gids": []}],
        },
    )
    ensure_binary()
    for exe, tag in ((AGENT_BIN, "agent"), (BIN, "rebuilt")):
        report = writable_report(f"walk-order-yb-{tag}")
        paths = _strace_lstat_paths(exe, tree, aud, report)
        y_idxs = [i for i, p in enumerate(paths) if _path_basename_is(p, "y")]
        b_idxs = [i for i, p in enumerate(paths) if _path_basename_is(p, "b")]
        assert y_idxs and b_idxs, f"{tag} missing leaf lstats: {paths[:40]!r}"
        assert y_idxs[0] < b_idxs[0], (
            f"{tag} walked b before y: y@{y_idxs[0]} b@{b_idxs[0]} in {paths[:40]!r}"
        )
        assert y_idxs[-1] < b_idxs[-1], (
            f"{tag} last walk of b preceded last walk of y "
            f"(probe-then-sort bug): last_y@{y_idxs[-1]} last_b@{b_idxs[-1]} in {paths!r}"
        )
        body = json.loads(report.read_bytes())
        assert [f["path"] for f in body["findings"]] == ["b", "y"]


def test_resolved_final_metadata_uses_lstat_nofollow():
    """Final symlink chain: ordinary intermediate conf and resolved token use lstat only."""
    tree, audience, expect = stage_case("final_symlink_chain")
    leaf = tree / "conf" / "token"
    assert leaf.is_file() and not leaf.is_symlink()
    ensure_binary()
    for exe, tag in ((AGENT_BIN, "agent"), (BIN, "rebuilt")):
        report = writable_report(f"lstat-final-{tag}")
        text = _strace_stat_trace(exe, tree, audience, report)
        assert report.read_bytes() == expect
        _assert_component_lstat_only(text, "conf")
        _assert_component_lstat_only(text, "token")


def test_resolved_dir_metadata_uses_lstat_nofollow():
    """After resolving an intermediate directory symlink, ancestry metadata uses lstat only."""
    work = _opaque_work("lstat-dir-link")
    tree = work / "tree"
    tree.mkdir()
    os.chmod(tree, 0o755)
    os.chown(tree, 0, 0)
    real = tree / "realdir"
    real.mkdir()
    os.chmod(real, 0o755)
    os.chown(real, 0, 0)
    secret = real / "secret"
    secret.write_text("x\n", encoding="utf-8")
    os.chown(secret, 1000, 1000)
    os.chmod(secret, 0o644)
    (tree / "alias").symlink_to("realdir")
    grant_sandbox_search(work)
    aud = work / "a.json"
    _write_aud(
        aud,
        {
            "entries": [{"path": "alias/secret", "allow_uids": [], "allow_gids": []}],
            "subjects": [{"uid": 2000, "gids": []}],
        },
    )
    ensure_binary()
    for exe, tag in ((AGENT_BIN, "agent"), (BIN, "rebuilt")):
        report = writable_report(f"lstat-dir-{tag}")
        text = _strace_stat_trace(exe, tree, aud, report)
        body = json.loads(report.read_bytes())
        assert [f["path"] for f in body["findings"]] == ["alias/secret"]
        # Resolved directory after the intermediate link must be read with lstat-only flags.
        _assert_component_lstat_only(text, "realdir")
        _assert_component_lstat_only(text, "secret")


def test_ordinary_intermediate_dir_metadata_uses_lstat_nofollow():
    """Ordinary non-final directory d in d/f must be metadata-read with lstat only."""
    work = _opaque_work("lstat-ord-dir")
    tree = work / "tree"
    tree.mkdir()
    os.chmod(tree, 0o755)
    os.chown(tree, 0, 0)
    d = tree / "d"
    d.mkdir()
    os.chmod(d, 0o755)
    os.chown(d, 0, 0)
    f = d / "f"
    f.write_text("x\n", encoding="utf-8")
    os.chown(f, 1000, 1000)
    os.chmod(f, 0o644)
    grant_sandbox_search(work)
    aud = work / "a.json"
    _write_aud(
        aud,
        {
            "entries": [{"path": "d/f", "allow_uids": [], "allow_gids": []}],
            "subjects": [{"uid": 2000, "gids": []}],
        },
    )
    ensure_binary()
    for exe, tag in ((AGENT_BIN, "agent"), (BIN, "rebuilt")):
        report = writable_report(f"lstat-ord-{tag}")
        text = _strace_stat_trace(exe, tree, aud, report)
        body = json.loads(report.read_bytes())
        assert [x["path"] for x in body["findings"]] == ["d/f"]
        _assert_component_lstat_only(text, "d")
        _assert_component_lstat_only(text, "f")


def test_ordinary_nested_components_use_lstat_nofollow():
    """A second ordinary path a/b/c requires lstat-only metadata on each component."""
    work = _opaque_work("lstat-ord-nested")
    tree = work / "tree"
    tree.mkdir()
    os.chmod(tree, 0o755)
    os.chown(tree, 0, 0)
    a = tree / "a"
    a.mkdir()
    os.chmod(a, 0o755)
    os.chown(a, 0, 0)
    b = a / "b"
    b.mkdir()
    os.chmod(b, 0o755)
    os.chown(b, 0, 0)
    c = b / "c"
    c.write_text("x\n", encoding="utf-8")
    os.chown(c, 1000, 1000)
    os.chmod(c, 0o644)
    grant_sandbox_search(work)
    aud = work / "a.json"
    _write_aud(
        aud,
        {
            "entries": [{"path": "a/b/c", "allow_uids": [], "allow_gids": []}],
            "subjects": [{"uid": 2000, "gids": []}],
        },
    )
    ensure_binary()
    for exe, tag in ((AGENT_BIN, "agent"), (BIN, "rebuilt")):
        report = writable_report(f"lstat-nest-{tag}")
        text = _strace_stat_trace(exe, tree, aud, report)
        body = json.loads(report.read_bytes())
        assert [x["path"] for x in body["findings"]] == ["a/b/c"]
        for name in ("a", "b", "c"):
            _assert_component_lstat_only(text, name)


def test_bench_multi_clear_independent():
    """Two findings clear different class bits: 0640 other-only vs 0604 group-only."""
    tree, audience, expect = stage_case("multi_clear_independent")
    assert_success(tree, audience, expect, label="bench-multi-clear")
    findings = {f["path"]: f for f in json.loads(expect)["findings"]}
    assert findings["d/a"]["remediation"]["mode"] == "0640"
    assert findings["d/b"]["remediation"]["mode"] == "0604"


def test_bench_multi_mode_independent():
    """Two findings retain independent remediations: 0640 vs unchanged 0400."""
    tree, audience, expect = stage_case("multi_mode_independent")
    assert_success(tree, audience, expect, label="bench-multi-mode")
    findings = {f["path"]: f for f in json.loads(expect)["findings"]}
    assert findings["d/a"]["remediation"]["mode"] == "0640"
    assert findings["d/b"]["remediation"]["mode"] == "0400"
    assert findings["d/b"]["exposure"] == ["uid:1001"]


def test_bench_mantissa_uid_exposure():
    """Subject uid 2^53+1 must appear exactly (not float64-rounded) in exposure."""
    tree, audience, expect = stage_case("mantissa_uid_exposure")
    assert_success(tree, audience, expect, label="bench-mantissa-uid")
    findings = json.loads(expect)["findings"]
    assert findings[0]["exposure"] == ["uid:9007199254740993"]


def test_bench_mantissa_gid_unequal():
    """allow_gids 2^53 and subject gid 2^53+1 stay unequal under exact uint64."""
    tree, audience, expect = stage_case("mantissa_gid_unequal")
    assert_success(tree, audience, expect, label="bench-mantissa-gid")
    findings = json.loads(expect)["findings"]
    assert findings[0]["exposure"] == ["uid:2000"]


def test_bench_escaped_intermediate_fifo():
    """Escaping intermediate link to an outside FIFO is skipped without opening it."""
    tree, audience, expect = stage_case("escaped_intermediate")
    assert_success(tree, audience, expect, label="bench-esc-fifo")
    assert json.loads(expect)["skipped"][0]["reason"] == "escaped"


def test_bench_large_file_near_bound():
    """Regular files at the inclusive 65536-byte graded bound are audited."""
    tree, audience, expect = stage_case("large_file_bound")
    assert_success(tree, audience, expect, label="bench-large")
    assert json.loads(expect)["findings"][0]["path"] == "conf/big.bin"


def test_bench_other_exec_preserved():
    """Other-class remediation clears only other-read, keeping other-execute (0645->0641)."""
    tree, audience, expect = stage_case("other_exec_preserved")
    assert_success(tree, audience, expect, label="bench-other-exec")
    finding = json.loads(expect)["findings"][0]
    assert finding["remediation"]["mode"] == "0641"


def test_bench_group_exec_preserved():
    """Group-class remediation clears only group-read, keeping group-execute (0750->0710)."""
    tree, audience, expect = stage_case("group_exec_preserved")
    assert_success(tree, audience, expect, label="bench-group-exec")
    finding = json.loads(expect)["findings"][0]
    assert finding["remediation"]["mode"] == "0710"
    assert finding["exposure"] == ["uid:2000"]


def test_bench_group_write_preserved():
    """Group-class remediation clears only group-read, keeping group-write (0670->0630)."""
    tree, audience, expect = stage_case("group_write_preserved")
    assert_success(tree, audience, expect, label="bench-group-write")
    finding = json.loads(expect)["findings"][0]
    assert finding["remediation"]["mode"] == "0630"
    assert finding["exposure"] == ["uid:2000"]


def test_bench_uint64_high_uid_exposure():
    """Subject uid at 2^63 appears as an unsigned exposure string, not a signed int64."""
    tree, audience, expect = stage_case("uint64_high_uid_exposure")
    assert_success(tree, audience, expect, label="bench-high-uid")
    finding = json.loads(expect)["findings"][0]
    assert finding["exposure"] == ["uid:9223372036854775808"]


def _inotify_watch_opens(path: Path):
    """Return (fd, wd) watching IN_OPEN on path via libc inotify."""
    libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    IN_CLOEXEC = 0x80000
    IN_NONBLOCK = 0x800
    IN_OPEN = 0x20
    fd = libc.inotify_init1(IN_CLOEXEC | IN_NONBLOCK)
    if fd < 0:
        raise OSError(ctypes.get_errno(), "inotify_init1")
    wd = libc.inotify_add_watch(fd, str(path).encode("utf-8"), IN_OPEN)
    if wd < 0:
        os.close(fd)
        raise OSError(ctypes.get_errno(), "inotify_add_watch")
    return fd, wd


def test_bench_escaped_intermediate_regular_not_opened():
    """Escaping intermediate regular-file target is not opened before skipped escaped."""
    tree, audience, expect = stage_case("escaped_intermediate_regular")
    # Target lives beside the tree under fs/extra/secret.dat
    target = tree.parent / "extra" / "secret.dat"
    assert target.is_file()
    fd, _wd = _inotify_watch_opens(target)
    try:
        assert_success(tree, audience, expect, label="bench-esc-reg")
        assert json.loads(expect)["skipped"][0]["reason"] == "escaped"
        try:
            data = os.read(fd, 4096)
        except BlockingIOError:
            data = b""
        assert data == b"", f"escaping regular target was opened: {data!r}"
    finally:
        os.close(fd)


def test_bench_escape_reenter_outside_dir_not_opened():
    """Escaping intermediate directory symlink target must not be opened (IN_OPEN)."""
    tree, audience, expect = stage_case("escape_reenter")
    outside = tree.parent / "extra"
    assert outside.is_dir()
    fd, _wd = _inotify_watch_opens(outside)
    try:
        assert_success(tree, audience, expect, label="bench-esc-dir")
        assert json.loads(expect)["skipped"][0]["reason"] == "escaped"
        try:
            data = os.read(fd, 4096)
        except BlockingIOError:
            data = b""
        assert data == b"", f"escaping outside directory was opened: {data!r}"
    finally:
        os.close(fd)


def test_root_resolves_symlink_then_dotdot():
    """--root link/../tree follows the symlink before '..', not a lexical Clean."""
    work = _opaque_work("root-dotdot-link")
    t = work / "T"
    (t / "inner" / "a").mkdir(parents=True)
    (t / "inner" / "tree").mkdir(parents=True)
    (t / "tree").mkdir(parents=True)
    for d in [t, t / "inner", t / "inner" / "a", t / "inner" / "tree", t / "tree"]:
        os.chown(d, 0, 0)
        os.chmod(d, 0o755)
    (t / "link").symlink_to(t / "inner" / "a")
    inner_f = t / "inner" / "tree" / "f"
    outer_f = t / "tree" / "f"
    inner_f.write_text("secret\n", encoding="utf-8")
    outer_f.write_text("decoy\n", encoding="utf-8")
    os.chown(inner_f, 0, 0)
    os.chown(outer_f, 0, 0)
    os.chmod(inner_f, 0o600)
    os.chmod(outer_f, 0o644)
    grant_sandbox_search(work)
    aud = work / "a.json"
    _write_aud(
        aud,
        {
            "entries": [{"path": "f", "allow_uids": [], "allow_gids": []}],
            "subjects": [{"uid": 1000, "gids": []}],
        },
    )
    root = t / "link" / ".." / "tree"
    report = writable_report("root-dotdot-link")
    proc = run_audit(root, aud, report)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == b""
    body = json.loads(report.read_text(encoding="utf-8"))
    assert body == {"findings": [], "skipped": []}


def test_root_resolves_relative_symlink_target_with_dotdot():
    """Relative symlink target pivot/../real resolves pivot before '..'."""
    work = _opaque_work("root-nested-dotdot")
    t = work / "t"
    (t / "nested" / "deep").mkdir(parents=True)
    (t / "nested" / "real" / "tree").mkdir(parents=True)
    (t / "real" / "tree").mkdir(parents=True)
    for d in [
        t,
        t / "nested",
        t / "nested" / "deep",
        t / "nested" / "real",
        t / "nested" / "real" / "tree",
        t / "real",
        t / "real" / "tree",
    ]:
        os.chown(d, 0, 0)
        os.chmod(d, 0o755)
    (t / "pivot").symlink_to("nested/deep")
    (t / "gate").symlink_to("pivot/../real")
    nested_secret = t / "nested" / "real" / "tree" / "secret"
    decoy_secret = t / "real" / "tree" / "secret"
    nested_secret.write_text("n\n", encoding="utf-8")
    decoy_secret.write_text("d\n", encoding="utf-8")
    os.chown(nested_secret, 0, 0)
    os.chown(decoy_secret, 0, 0)
    os.chmod(nested_secret, 0o600)
    os.chmod(decoy_secret, 0o644)
    grant_sandbox_search(work)
    aud = work / "a.json"
    _write_aud(
        aud,
        {
            "entries": [{"path": "secret", "allow_uids": [], "allow_gids": []}],
            "subjects": [{"uid": 1000, "gids": [1000]}],
        },
    )
    root = t / "gate" / "tree"
    report = writable_report("root-nested-dotdot")
    proc = run_audit(root, aud, report)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == b""
    body = json.loads(report.read_text(encoding="utf-8"))
    assert body == {"findings": [], "skipped": []}


def test_root_slash_contains_absolute_resolved_links():
    """With --root /, an intermediate symlink under /tmp stays contained (not escaped)."""
    tag = hashlib.sha256(b"root-slash-contain").hexdigest()[:12]
    inside = Path(f"/tmp/casaudit-{tag}-inside")
    alias = Path(f"/tmp/casaudit-{tag}-alias")
    token = inside / "token"
    try:
        if alias.exists() or alias.is_symlink():
            alias.unlink()
        if inside.exists():
            shutil.rmtree(inside)
        inside.mkdir(parents=True)
        os.chmod(inside, 0o755)
        os.chown(inside, 0, 0)
        token.write_text("t\n", encoding="utf-8")
        os.chown(token, 0, 0)
        os.chmod(token, 0o644)
        alias.symlink_to(inside)
        work = _opaque_work("root-slash")
        aud = work / "a.json"
        rel = f"tmp/casaudit-{tag}-alias/token"
        _write_aud(
            aud,
            {
                "entries": [{"path": rel, "allow_uids": [], "allow_gids": []}],
                "subjects": [{"uid": 2000, "gids": [2000]}],
            },
        )
        report = writable_report("root-slash")
        proc = run_audit(Path("/"), aud, report)
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout == b""
        assert report.read_bytes() == _canon_bytes(
            {
                "findings": [
                    {
                        "exposure": ["uid:2000"],
                        "path": rel,
                        "remediation": {"mode": "0640"},
                    }
                ],
                "skipped": [],
            }
        )
    finally:
        if alias.exists() or alias.is_symlink():
            alias.unlink()
        if inside.exists():
            shutil.rmtree(inside)


def test_root_double_slash_accepted():
    """All-slash --root // normalizes to / and succeeds (not TrimRight to empty)."""
    work = _opaque_work("root-dslash")
    aud = work / "a.json"
    _write_aud(aud, {"entries": [], "subjects": []})
    report = writable_report("root-dslash")
    # Pass the raw spelling; pathlib collapses // to /.
    proc = run_argv(
        ["--root", "//", "--audience", str(aud), "--report", str(report)]
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == b""
    assert report.read_text(encoding="utf-8") == '{\n  "findings": [],\n  "skipped": []\n}\n'


def test_root_triple_slash_accepted():
    """All-slash --root /// normalizes to / and succeeds."""
    work = _opaque_work("root-tslash")
    aud = work / "a.json"
    _write_aud(aud, {"entries": [], "subjects": []})
    report = writable_report("root-tslash")
    proc = run_argv(
        ["--root", "///", "--audience", str(aud), "--report", str(report)]
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == b""
    assert report.read_text(encoding="utf-8") == '{\n  "findings": [],\n  "skipped": []\n}\n'


def test_root_ordinary_trailing_slash_accepted():
    """Non-symlink directory --root with a trailing / is normalized and accepted."""
    work = _opaque_work("root-trail-slash")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, {"entries": [], "subjects": []})
    report = writable_report("root-trail-slash")
    proc = run_argv(
        ["--root", str(tree) + "/", "--audience", str(aud), "--report", str(report)]
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == b""
    assert report.read_text(encoding="utf-8") == '{\n  "findings": [],\n  "skipped": []\n}\n'


def test_relative_root_resolved_against_cwd_not_app():
    """Relative --root is resolved against the invocation cwd (/sandbox), not /app."""
    tag = hashlib.sha256(b"rel-root-cwd").hexdigest()[:12]
    name = f"casaudit-relroot-{tag}"
    tree = Path("/sandbox") / name
    if tree.exists():
        shutil.rmtree(tree)
    tree.mkdir()
    os.chmod(tree, 0o755)
    os.chown(tree, SANDBOX_UID, SANDBOX_UID)
    try:
        work = _opaque_work("rel-root-cwd")
        aud = work / "a.json"
        _write_aud(aud, {"entries": [], "subjects": []})
        assert not (Path("/app") / name).exists()
        ensure_binary()
        for exe, tag_exe in ((AGENT_BIN, "agent"), (BIN, "rebuilt")):
            report = writable_report(f"rel-root-{tag_exe}")
            proc = _run_exe(
                exe,
                ["--root", name, "--audience", str(aud), "--report", str(report)],
            )
            assert proc.returncode == 0, (
                f"{tag_exe} relative root failed (likely resolved under /app): "
                f"stderr={proc.stderr!r}"
            )
            assert proc.stdout == b""
            assert report.read_bytes() == EMPTY_REPORT
    finally:
        if tree.exists():
            shutil.rmtree(tree)


def test_relative_root_nested_under_sandbox_cwd():
    """Nested relative --root dir/tree resolves from cwd, not as /app/dir/tree."""
    tag = hashlib.sha256(b"rel-root-nested").hexdigest()[:12]
    parent = Path("/sandbox") / f"casaudit-relnest-{tag}"
    tree = parent / "tree"
    if parent.exists():
        shutil.rmtree(parent)
    tree.mkdir(parents=True)
    for d in (parent, tree):
        os.chmod(d, 0o755)
        os.chown(d, SANDBOX_UID, SANDBOX_UID)
    try:
        work = _opaque_work("rel-root-nested")
        aud = work / "a.json"
        _write_aud(aud, {"entries": [], "subjects": []})
        rel = f"casaudit-relnest-{tag}/tree"
        assert not (Path("/app") / rel).exists()
        ensure_binary()
        for exe, tag_exe in ((AGENT_BIN, "agent"), (BIN, "rebuilt")):
            report = writable_report(f"rel-root-nest-{tag_exe}")
            proc = _run_exe(
                exe,
                ["--root", rel, "--audience", str(aud), "--report", str(report)],
            )
            assert proc.returncode == 0, proc.stderr
            assert proc.stdout == b""
            assert report.read_bytes() == EMPTY_REPORT
    finally:
        if parent.exists():
            shutil.rmtree(parent)


def test_bench_prefix_escape_final_not_opened():
    """Escaping final regular-file symlink target is not opened before unsupported."""
    tree, audience, expect = stage_case("prefix_escape")
    target = tree.parent / "tree_extra" / "token"
    assert target.is_file()
    fd, _wd = _inotify_watch_opens(target)
    try:
        assert_success(tree, audience, expect, label="bench-prefix-final")
        assert json.loads(expect)["skipped"][0]["reason"] == "unsupported"
        try:
            data = os.read(fd, 4096)
        except BlockingIOError:
            data = b""
        assert data == b"", f"escaping final regular target was opened: {data!r}"
    finally:
        os.close(fd)


def test_bench_multi_entry_mix():
    """Mixed findings and skips appear together with correct sorting."""
    tree, audience, expect = stage_case("multi_entry_mix")
    assert_success(tree, audience, expect, label="bench-multi")


def test_bench_fifo_unsupported():
    """FIFO finals are skipped as unsupported."""
    tree, audience, expect = stage_case("fifo_unsupported")
    assert_success(tree, audience, expect, label="bench-fifo")
    assert json.loads(expect)["skipped"][0]["reason"] == "unsupported"


def test_bench_missing_path():
    """Absent paths are skipped as missing."""
    tree, audience, expect = stage_case("missing_path")
    assert_success(tree, audience, expect, label="bench-missing")
    assert json.loads(expect)["skipped"][0]["reason"] == "missing"


def test_bench_remediation_group_clear():
    """Disallowed group and other exposure clears both class read bits."""
    tree, audience, expect = stage_case("remediation_group_clear")
    assert_success(tree, audience, expect, label="bench-remediation")
    assert json.loads(expect)["findings"][0]["remediation"]["mode"] == "0620"


def test_bench_scale_48():
    """Sealed scale_48_entries case reproduces minted report bytes."""
    tree, audience, expect = stage_case("scale_48_entries")
    assert_success(tree, audience, expect, label="bench-scale-48")


def test_bench_unicode_path():
    """Sealed unicode_path case reproduces minted report bytes."""
    tree, audience, expect = stage_case("unicode_path")
    assert_success(tree, audience, expect, label="bench-unicode")


def test_bench_emoji_path():
    """Supplementary-plane UTF-8 in a finding path stays literal, not \\ud83d\\ude00."""
    tree, audience, expect = stage_case("emoji_path")
    assert_success(tree, audience, expect, label="bench-emoji")
    text = expect.decode("utf-8") if isinstance(expect, (bytes, bytearray)) else expect
    assert "😀" in text
    assert "\\ud83d" not in text.lower()
    assert "\\ude00" not in text.lower()
    assert json.loads(text)["findings"][0]["path"] == "a😀b"


def test_bench_u2028_path():
    """U+2028 in a finding path appears literally, not as \\u2028."""
    tree, audience, expect = stage_case("u2028_path")
    assert_success(tree, audience, expect, label="bench-u2028")
    text = expect.decode("utf-8") if isinstance(expect, (bytes, bytearray)) else expect
    assert "\u2028" in text
    assert "\\u2028" not in text


def test_bench_u2029_path():
    """U+2029 in a finding path appears literally, not as \\u2029."""
    tree, audience, expect = stage_case("u2029_path")
    assert_success(tree, audience, expect, label="bench-u2029")
    text = expect.decode("utf-8") if isinstance(expect, (bytes, bytearray)) else expect
    assert "\u2029" in text
    assert "\\u2029" not in text


def test_bench_backspace_path():
    """U+0008 in a finding path is escaped as \\b (Python json.dumps), not \\u0008."""
    tree, audience, expect = stage_case("backspace_path")
    assert_success(tree, audience, expect, label="bench-backspace")
    text = expect.decode("utf-8") if isinstance(expect, (bytes, bytearray)) else expect
    assert "\\b" in text
    assert "\\u0008" not in text


def test_bench_bell_path():
    """U+0007 in a finding path is escaped as \\u0007 (JSON), not Go strconv.Quote \\a."""
    tree, audience, expect = stage_case("bell_path")
    assert_success(tree, audience, expect, label="bench-bell")
    text = expect.decode("utf-8") if isinstance(expect, (bytes, bytearray)) else expect
    assert "\\u0007" in text
    assert "\\a" not in text
    body = json.loads(text)
    assert body["findings"][0]["path"] == "a\x07b"
    assert body["findings"][0]["remediation"]["mode"] == "0640"


def test_bench_formfeed_path():
    """U+000C in a finding path is escaped as \\f (Python json.dumps), not \\u000c."""
    tree, audience, expect = stage_case("formfeed_path")
    assert_success(tree, audience, expect, label="bench-formfeed")
    text = expect.decode("utf-8") if isinstance(expect, (bytes, bytearray)) else expect
    assert "\\f" in text
    assert "\\u000c" not in text.lower()


def test_bench_owner_read_exposure():
    """Sealed owner_read_exposure case reproduces minted report bytes."""
    tree, audience, expect = stage_case("owner_read_exposure")
    assert_success(tree, audience, expect, label="bench-owner-read")


def test_bench_owner_exec_only_no_read():
    """Owner-class execute without read must not count as effective file read."""
    tree, audience, expect = stage_case("owner_exec_only_no_read")
    assert_success(tree, audience, expect, label="bench-owner-x-only")
    assert expect == EMPTY_REPORT


def test_bench_group_exec_only_no_read():
    """Group-class execute without read must not count as effective file read."""
    tree, audience, expect = stage_case("group_exec_only_no_read")
    assert_success(tree, audience, expect, label="bench-group-x-only")
    assert expect == EMPTY_REPORT


def test_bench_other_exec_only_no_read():
    """Other-class execute without read must not count as effective file read."""
    tree, audience, expect = stage_case("other_exec_only_no_read")
    assert_success(tree, audience, expect, label="bench-other-x-only")
    assert expect == EMPTY_REPORT


def test_bench_owner_denies_other_grants_read():
    """Owner class selected: other-read alone must not grant effective read."""
    tree, audience, expect = stage_case("owner_denies_other_grants_read")
    assert_success(tree, audience, expect, label="bench-owner-deny-other-r")
    assert expect == EMPTY_REPORT


def test_bench_other_read_no_exec_dir():
    """Other-class directory with read but no execute must block traversal."""
    tree, audience, expect = stage_case("other_read_no_exec_dir")
    assert_success(tree, audience, expect, label="bench-other-r-no-x")
    assert expect == EMPTY_REPORT


def test_bench_group_write_no_exec_dir():
    """Group-class directory write without execute must not count as traversal."""
    tree, audience, expect = stage_case("group_write_no_exec_dir")
    assert_success(tree, audience, expect, label="bench-group-w-no-x")
    assert expect == EMPTY_REPORT


def test_bench_other_write_no_exec_dir():
    """Other-class directory write without execute must not count as traversal."""
    tree, audience, expect = stage_case("other_write_no_exec_dir")
    assert_success(tree, audience, expect, label="bench-other-w-no-x")
    assert expect == EMPTY_REPORT


def test_bench_permitted_link_keeps_prior_dir():
    """Earlier permitted-link pivot directories remain in the execute check set."""
    tree, audience, expect = stage_case("permitted_link_keeps_prior_dir")
    assert_success(tree, audience, expect, label="bench-link-keep-prior")
    assert expect == EMPTY_REPORT


def test_bench_owner_dir_exec_only_exposes():
    """Owner directory execute without owner-read still grants traversal."""
    tree, audience, expect = stage_case("owner_dir_exec_only_exposes")
    assert_success(tree, audience, expect, label="bench-owner-dir-x-only")
    finding = json.loads(expect)["findings"][0]
    assert finding["exposure"] == ["uid:1000"]
    assert finding["remediation"]["mode"] == "0400"


def test_bench_owner_write_only_no_read():
    """Owner-class write without read must not count as effective file read."""
    tree, audience, expect = stage_case("owner_write_only_no_read")
    assert_success(tree, audience, expect, label="bench-owner-w-only")
    assert expect == EMPTY_REPORT


def test_bench_group_write_only_no_read():
    """Group-class write without read must not count as effective file read."""
    tree, audience, expect = stage_case("group_write_only_no_read")
    assert_success(tree, audience, expect, label="bench-group-w-only")
    assert expect == EMPTY_REPORT


def test_bench_other_write_only_no_read():
    """Other-class write without read must not count as effective file read."""
    tree, audience, expect = stage_case("other_write_only_no_read")
    assert_success(tree, audience, expect, label="bench-other-w-only")
    assert expect == EMPTY_REPORT


def test_bench_uid_uint32_narrow_not_owner():
    """Subject uid 2^32+1000 must not match owner 1000 via uint32 narrowing."""
    tree, audience, expect = stage_case("uid_uint32_narrow_not_owner")
    assert_success(tree, audience, expect, label="bench-uid-narrow")
    assert expect == EMPTY_REPORT


def test_bench_gid_uint32_narrow_not_group():
    """Subject gid 2^32+1000 must not match file gid 1000 via uint32 narrowing."""
    tree, audience, expect = stage_case("gid_uint32_narrow_not_group")
    assert_success(tree, audience, expect, label="bench-gid-narrow")
    assert expect == EMPTY_REPORT


def test_bench_gid_uint32_narrow_not_group_dir():
    """Narrowed subject gid must not unlock group-execute on a directory either."""
    tree, audience, expect = stage_case("gid_uint32_narrow_not_group_dir")
    assert_success(tree, audience, expect, label="bench-gid-narrow-dir")
    assert expect == EMPTY_REPORT


def test_bench_valid_final_regular_not_opened():
    """Successful walk of a valid regular final must not open the file (lstat only)."""
    tree, audience, expect = stage_case("world_readable")
    leaf = tree / "conf" / "token"
    assert leaf.is_file()
    fd, _wd = _inotify_watch_opens(leaf)
    try:
        assert_success(tree, audience, expect, label="bench-final-no-open")
        assert len(json.loads(expect)["findings"]) == 1
        try:
            data = os.read(fd, 4096)
        except BlockingIOError:
            data = b""
        assert data == b"", f"valid regular final was opened: {data!r}"
    finally:
        os.close(fd)


def test_bench_valid_final_empty_report_not_opened():
    """Even when the report is empty, a reachable regular final must not be opened."""
    tree, audience, expect = stage_case("owner_exec_only_no_read")
    leaf = tree / "f"
    assert leaf.is_file()
    fd, _wd = _inotify_watch_opens(leaf)
    try:
        assert_success(tree, audience, expect, label="bench-final-empty-no-open")
        assert expect == EMPTY_REPORT
        try:
            data = os.read(fd, 4096)
        except BlockingIOError:
            data = b""
        assert data == b"", f"valid regular final was opened: {data!r}"
    finally:
        os.close(fd)


def test_refuse_missing_root_flag():
    """Missing --root refuses with exact text and status 2."""
    work = _opaque_work("refuse-missing-root")
    aud = work / "a.json"
    aud.write_text('{"entries":[],"subjects":[]}\n', encoding="utf-8")
    report = work / "r.json"
    assert_refuse(
        ["--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_missing_audience_flag():
    """Missing --audience refuses with exact text and status 2."""
    work = _opaque_work("refuse-missing-aud")
    tree = work / "tree"
    tree.mkdir()
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_missing_report_flag():
    """Missing --report refuses with exact text and status 2."""
    work = _opaque_work("refuse-missing-rep")
    tree = work / "tree"
    tree.mkdir()
    aud = work / "a.json"
    aud.write_text('{"entries":[],"subjects":[]}\n', encoding="utf-8")
    assert_refuse(["--root", str(tree), "--audience", str(aud)])


def test_refuse_empty_root_value():
    """Empty --root value refuses."""
    work = _opaque_work("refuse-empty-root")
    aud = work / "a.json"
    aud.write_text('{"entries":[],"subjects":[]}\n', encoding="utf-8")
    report = work / "r.json"
    assert_refuse(
        ["--root", "", "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_empty_report_value():
    """Empty --report value refuses."""
    work = _opaque_work("refuse-empty-report")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, {"entries": [], "subjects": []})
    assert_refuse(["--root", str(tree), "--audience", str(aud), "--report", ""])


def test_refuse_empty_audience_value():
    """Empty --audience value refuses."""
    work = _opaque_work("refuse-empty-audience")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, {"entries": [], "subjects": []})
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", "", "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_missing_root_path():
    """Nonexistent --root path refuses."""
    work = _opaque_work("refuse-missing-root-path")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, {"entries": [], "subjects": []})
    report = work / "r.json"
    assert_refuse(
        ["--root", str(work / "no-such-root"), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_bad_root_preserves_existing_report():
    """Bad --root refuses without truncating a preexisting sandbox-owned --report."""
    work = _opaque_work("refuse-bad-root-keep-report")
    aud = work / "a.json"
    _write_aud(aud, {"entries": [], "subjects": []})
    report = work / "r.json"
    before = b"keep\n"
    _sandbox_owned_file(report, before)
    assert_refuse(
        ["--root", str(work / "no-such-root"), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=before,
    )


def test_refuse_bad_root_preserves_report_symlink_target():
    """Bad --root refuses without truncating a sandbox-owned --report symlink target."""
    work = _opaque_work("refuse-bad-root-keep-link")
    aud = work / "a.json"
    _write_aud(aud, {"entries": [], "subjects": []})
    target = work / "target.json"
    before = b"keep\n"
    _sandbox_owned_file(target, before)
    report = work / "report.link"
    report.symlink_to(target)
    ensure_binary()
    fd, _wd = _inotify_watch_opens(target)
    try:
        for exe in (AGENT_BIN, BIN):
            proc = _run_refusal_nofollow(
                exe,
                [
                    "--root",
                    str(work / "no-such-root"),
                    "--audience",
                    str(aud),
                    "--report",
                    str(report),
                ],
            )
            assert proc.returncode == 2
            assert proc.stdout == b""
            assert proc.stderr == REFUSE_TEXT
            assert report.is_symlink()
        try:
            data = os.read(fd, 4096)
        except BlockingIOError:
            data = b""
        assert data == b"", f"refusal followed --report symlink (IN_OPEN): {data!r}"
    finally:
        os.close(fd)
    assert target.read_bytes() == before


def test_refuse_root_is_regular_file():
    """--root that is a regular file refuses."""
    work = _opaque_work("refuse-root-file")
    tree, aud = _empty_tree_aud(work)
    root_file = work / "rootfile"
    root_file.write_text("not-a-dir\n", encoding="utf-8")
    os.chmod(root_file, 0o644)
    _write_aud(aud, {"entries": [], "subjects": []})
    report = work / "r.json"
    assert_refuse(
        ["--root", str(root_file), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_audience_missing():
    """Nonexistent audience path refuses."""
    work = _opaque_work("refuse-aud-missing")
    tree, aud = _empty_tree_aud(work)
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(work / "missing-aud.json"), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_audience_is_directory():
    """Audience path that is a directory refuses."""
    work = _opaque_work("refuse-aud-dir")
    tree, aud = _empty_tree_aud(work)
    aud.mkdir()
    os.chmod(aud, 0o755)
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_audience_is_fifo():
    """Audience FIFO is lstat-rejected as non-regular before any read."""
    work = _opaque_work("refuse-aud-fifo")
    tree, aud = _empty_tree_aud(work)
    os.mkfifo(aud, 0o644)
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_audience_is_char_device():
    """Audience character device (/dev/zero) refuses before any read of its bytes."""
    work = _opaque_work("refuse-aud-cdev")
    tree, _aud = _empty_tree_aud(work)
    report = work / "r.json"
    assert Path("/dev/zero").exists()
    assert_refuse(
        ["--root", str(tree), "--audience", "/dev/zero", "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_symlink_root():
    """Symlink --root refuses."""
    work = _opaque_work("refuse-symlink-root")
    real = work / "real"
    real.mkdir()
    link = work / "link"
    link.symlink_to(real)
    aud = work / "a.json"
    aud.write_text('{"entries":[],"subjects":[]}\n', encoding="utf-8")
    report = work / "r.json"
    assert_refuse(
        ["--root", str(link), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_symlink_root_trailing_slash():
    """Symlink --root with a trailing slash still refuses."""
    work = _opaque_work("refuse-symlink-root-slash")
    real = work / "real"
    real.mkdir()
    os.chmod(real, 0o755)
    link = work / "link"
    link.symlink_to(real)
    aud = work / "a.json"
    _write_aud(aud, {"entries": [], "subjects": []})
    report = work / "r.json"
    assert_refuse(
        ["--root", str(link) + "/", "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_symlink_root_double_slash():
    """Symlink --root with two trailing slashes still refuses (strip every /)."""
    work = _opaque_work("refuse-symlink-root-dslash")
    real = work / "real"
    real.mkdir()
    os.chmod(real, 0o755)
    link = work / "link"
    link.symlink_to(real)
    aud = work / "a.json"
    _write_aud(aud, {"entries": [], "subjects": []})
    report = work / "r.json"
    assert_refuse(
        ["--root", str(link) + "//", "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_symlink_audience():
    """Symlink --audience refuses."""
    work = _opaque_work("refuse-symlink-aud")
    tree = work / "tree"
    tree.mkdir()
    real = work / "real.json"
    real.write_text('{"entries":[],"subjects":[]}\n', encoding="utf-8")
    link = work / "aud.link"
    link.symlink_to(real)
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(link), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_bad_audience_json():
    """Malformed audience JSON refuses."""
    work = _opaque_work("refuse-bad-json")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, b"{not-json\n")
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_duplicate_paths():
    """Duplicate entry paths refuse."""
    work = _opaque_work("refuse-dup")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        {
            "entries": [
                {"path": "a", "allow_uids": [], "allow_gids": []},
                {"path": "a", "allow_uids": [], "allow_gids": []},
            ],
            "subjects": [],
        },
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_duplicate_paths_nonadjacent():
    """Duplicate entry paths refuse even when separated by another entry."""
    work = _opaque_work("refuse-dup-gap")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        {
            "entries": [
                {"path": "a", "allow_uids": [], "allow_gids": []},
                {"path": "b", "allow_uids": [], "allow_gids": []},
                {"path": "a", "allow_uids": [], "allow_gids": []},
            ],
            "subjects": [],
        },
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_dotdot_path():
    """Audience paths with .. refuse."""
    work = _opaque_work("refuse-dotdot")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        {
            "entries": [{"path": "a/../b", "allow_uids": [], "allow_gids": []}],
            "subjects": [],
        },
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_dotdot_path_preserves_existing_report():
    """Malformed .. path refuses without truncating a preexisting --report."""
    work = _opaque_work("refuse-dotdot-keep")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        {
            "entries": [{"path": "a/../b", "allow_uids": [], "allow_gids": []}],
            "subjects": [],
        },
    )
    report = work / "r.json"
    before = b"keep\n"
    _sandbox_owned_file(report, before)
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=before,
    )


def test_refuse_dot_component_preserves_existing_report():
    """Malformed . path component refuses without truncating preexisting --report."""
    work = _opaque_work("refuse-dot-keep")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        {
            "entries": [{"path": "a/./b", "allow_uids": [], "allow_gids": []}],
            "subjects": [],
        },
    )
    report = work / "r.json"
    before = b"keep\n"
    _sandbox_owned_file(report, before)
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=before,
    )


def test_refuse_dotdot_path_preserves_report_symlink_target():
    """Malformed .. path refuses without following or truncating a --report symlink."""
    work = _opaque_work("refuse-dotdot-keep-link")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        {
            "entries": [{"path": "a/../b", "allow_uids": [], "allow_gids": []}],
            "subjects": [],
        },
    )
    target = work / "target.json"
    before = b"keep\n"
    _sandbox_owned_file(target, before)
    report = work / "report.link"
    report.symlink_to(target)
    ensure_binary()
    fd, _wd = _inotify_watch_opens(target)
    try:
        for exe in (AGENT_BIN, BIN):
            proc = _run_refusal_nofollow(
                exe,
                ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
            )
            assert proc.returncode == 2
            assert proc.stdout == b""
            assert proc.stderr == REFUSE_TEXT
            assert report.is_symlink()
        try:
            data = os.read(fd, 4096)
        except BlockingIOError:
            data = b""
        assert data == b"", f"late path refusal followed --report symlink (IN_OPEN): {data!r}"
    finally:
        os.close(fd)
    assert target.read_bytes() == before


def test_refuse_invalid_utf8_audience():
    """Audience bytes with invalid UTF-8 in a path string refuse."""
    work = _opaque_work("refuse-bad-utf8")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        b'{"entries":[{"path":"a\xffb","allow_uids":[],"allow_gids":[]}],"subjects":[]}\n',
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_top_level_extra_key():
    """Top-level audience object with an extra key refuses."""
    work = _opaque_work("refuse-top-extra")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, {"entries": [], "subjects": [], "extra": True})
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_top_level_null():
    """Top-level JSON null is not an audience object and refuses."""
    work = _opaque_work("refuse-top-null")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, b"null\n")
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_top_level_array():
    """Top-level JSON array is not an audience object and refuses."""
    work = _opaque_work("refuse-top-array")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, b"[]\n")
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_top_level_number():
    """Top-level JSON number is not an audience object and refuses."""
    work = _opaque_work("refuse-top-num")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, b"1\n")
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_top_level_string():
    """Top-level JSON string is not an audience object and refuses."""
    work = _opaque_work("refuse-top-str")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, b'"nope"\n')
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_top_level_bool():
    """Top-level JSON boolean is not an audience object and refuses."""
    work = _opaque_work("refuse-top-bool")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, b"true\n")
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_entries_null():
    """Null entries field refuses."""
    work = _opaque_work("refuse-entries-null")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, {"entries": None, "subjects": []})
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_subjects_null():
    """Null subjects field refuses."""
    work = _opaque_work("refuse-subjects-null")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, {"entries": [], "subjects": None})
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_entries_not_array():
    """Non-array entries object refuses."""
    work = _opaque_work("refuse-entries-obj")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, {"entries": {}, "subjects": []})
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_entries_number():
    """Scalar number entries field refuses."""
    work = _opaque_work("refuse-entries-num")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, {"entries": 1, "subjects": []})
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_entries_string():
    """Scalar string entries field refuses."""
    work = _opaque_work("refuse-entries-str")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, {"entries": "[]", "subjects": []})
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_entries_bool():
    """Scalar boolean entries field refuses."""
    work = _opaque_work("refuse-entries-bool")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, {"entries": False, "subjects": []})
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_audience_trailing_vt():
    """Trailing vertical tab after a valid audience object is not JSON whitespace."""
    work = _opaque_work("refuse-aud-vt")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, b'{"entries":[],"subjects":[]}\x0b')
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_audience_trailing_nbsp():
    """Trailing U+00A0 after a valid audience object is not JSON whitespace."""
    work = _opaque_work("refuse-aud-nbsp")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, b'{"entries":[],"subjects":[]}\xc2\xa0')
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_audience_two_complete_objects():
    """Two successive complete audience objects (no junk token) refuse as trailing content."""
    work = _opaque_work("refuse-aud-twodoc")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, b'{"entries":[],"subjects":[]}{"entries":[],"subjects":[]}')
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_audience_two_complete_objects_with_space():
    """A second complete audience object after JSON whitespace also refuses."""
    work = _opaque_work("refuse-aud-twodoc-ws")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, b'{"entries":[],"subjects":[]} \n{"entries":[],"subjects":[]}')
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_subjects_not_array():
    """Non-array subjects object refuses."""
    work = _opaque_work("refuse-subjects-obj")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, {"entries": [], "subjects": {}})
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_subjects_number():
    """Scalar number subjects field refuses."""
    work = _opaque_work("refuse-subjects-num")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, {"entries": [], "subjects": 0})
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_subjects_string():
    """Scalar string subjects field refuses."""
    work = _opaque_work("refuse-subjects-str")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, {"entries": [], "subjects": "x"})
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_subjects_bool():
    """Scalar boolean subjects field refuses."""
    work = _opaque_work("refuse-subjects-bool")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, {"entries": [], "subjects": False})
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_entry_not_object():
    """Non-object entries element refuses."""
    work = _opaque_work("refuse-entry-num")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, {"entries": [7], "subjects": []})
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_entry_null_element():
    """Null entries array element is not an object and refuses."""
    work = _opaque_work("refuse-entry-null")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, b'{"entries":[null],"subjects":[]}\n')
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_subject_not_object():
    """Non-object subjects element refuses."""
    work = _opaque_work("refuse-subject-str")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, {"entries": [], "subjects": ["x"]})
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_subject_array_element():
    """Array subjects element is not an object and refuses."""
    work = _opaque_work("refuse-subject-arr")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, b'{"entries":[],"subjects":[[]]}\n')
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_subject_null_element():
    """Null subjects array element is not an object and refuses."""
    work = _opaque_work("refuse-subject-null")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, b'{"entries":[],"subjects":[null]}\n')
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_entry_extra_key():
    """Entry object with an extra key refuses."""
    work = _opaque_work("refuse-entry-extra")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        {
            "entries": [{"path": "a", "allow_uids": [], "allow_gids": [], "x": 1}],
            "subjects": [],
        },
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_entry_duplicate_path_key():
    """Duplicate decoded key inside an entry object refuses (exactly three keys)."""
    work = _opaque_work("refuse-entry-dup-path")
    tree, aud = _empty_tree_aud(work)
    secret = tree / "a"
    secret.write_text("x\n", encoding="utf-8")
    os.chown(secret, 1000, 1000)
    os.chmod(secret, 0o644)
    _write_aud(
        aud,
        b'{"entries":[{"path":"a","path":"a","allow_uids":[],"allow_gids":[]}],"subjects":[]}\n',
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_entry_duplicate_path_key_escaped():
    """Duplicate entry path key via JSON \\u escape of the same decoded name refuses."""
    work = _opaque_work("refuse-entry-dup-path-esc")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        b'{"entries":[{"path":"a","pa\\u0074h":"a","allow_uids":[],"allow_gids":[]}],"subjects":[]}\n',
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_entry_duplicate_allow_uids_key():
    """Duplicate allow_uids key inside an entry object refuses."""
    work = _opaque_work("refuse-entry-dup-uids")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        b'{"entries":[{"path":"a","allow_uids":[],"allow_uids":[],"allow_gids":[]}],"subjects":[]}\n',
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_entry_duplicate_allow_gids_key():
    """Duplicate allow_gids key inside an entry object refuses."""
    work = _opaque_work("refuse-entry-dup-gids")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        b'{"entries":[{"path":"a","allow_uids":[],"allow_gids":[],"allow_gids":[]}],"subjects":[]}\n',
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_entry_missing_allow_gids():
    """Entry missing allow_gids refuses."""
    work = _opaque_work("refuse-entry-no-gids")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        {"entries": [{"path": "a", "allow_uids": []}], "subjects": []},
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_allow_uids_negative():
    """Negative allow_uids element refuses."""
    work = _opaque_work("refuse-uids-neg")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        {
            "entries": [{"path": "a", "allow_uids": [-1], "allow_gids": []}],
            "subjects": [],
        },
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_allow_gids_negative():
    """Negative allow_gids element refuses."""
    work = _opaque_work("refuse-gids-neg")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        {
            "entries": [{"path": "a", "allow_uids": [], "allow_gids": [-1]}],
            "subjects": [],
        },
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_allow_uids_null_element():
    """Null element inside allow_uids refuses."""
    work = _opaque_work("refuse-uids-null")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        b'{"entries":[{"path":"a","allow_uids":[null],"allow_gids":[]}],"subjects":[]}\n',
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_subject_extra_key():
    """Subject object with an extra key refuses."""
    work = _opaque_work("refuse-subject-extra")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        {"entries": [], "subjects": [{"uid": 1, "gids": [], "name": "x"}]},
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_subject_duplicate_uid_key():
    """Duplicate decoded uid key inside a subject object refuses (exactly uid+gids)."""
    work = _opaque_work("refuse-subject-dup-uid")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        b'{"entries":[],"subjects":[{"uid":1,"uid":2,"gids":[]}]}\n',
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_subject_duplicate_uid_key_escaped():
    """Duplicate subject uid key via JSON \\u escape of the same decoded name refuses."""
    work = _opaque_work("refuse-subject-dup-uid-esc")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        b'{"entries":[],"subjects":[{"uid":1,"\\u0075id":2,"gids":[]}]}\n',
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_subject_uid_key_case_alias():
    """Subject member name UID (uppercase) is not the required uid key and refuses."""
    work = _opaque_work("refuse-subject-uid-case")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        b'{"entries":[],"subjects":[{"UID":1,"gids":[]}]}\n',
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_top_level_entries_key_case_alias():
    """Top-level Entries is not the required entries key and refuses."""
    work = _opaque_work("refuse-top-entries-case")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, b'{"Entries":[],"subjects":[]}\n')
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_top_level_subjects_key_case_alias():
    """Top-level Subjects is not the required subjects key and refuses."""
    work = _opaque_work("refuse-top-subjects-case")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, b'{"entries":[],"Subjects":[]}\n')
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_entry_path_key_case_alias():
    """Entry member Path is not the required path key and refuses."""
    work = _opaque_work("refuse-entry-path-case")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        b'{"entries":[{"Path":"missing","allow_uids":[],"allow_gids":[]}],"subjects":[]}\n',
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_entry_allow_uids_key_case_alias():
    """Entry member Allow_Uids is not the required allow_uids key and refuses."""
    work = _opaque_work("refuse-entry-allow-uids-case")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        b'{"entries":[{"path":"missing","Allow_Uids":[],"allow_gids":[]}],"subjects":[]}\n',
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_subject_gids_key_case_alias():
    """Subject member name GIDS is not the required gids key and refuses."""
    work = _opaque_work("refuse-subject-gids-case")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        b'{"entries":[],"subjects":[{"uid":1,"GIDS":[]}]}\n',
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_subject_duplicate_gids_key():
    """Duplicate decoded gids key inside a subject object refuses."""
    work = _opaque_work("refuse-subject-dup-gids")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        b'{"entries":[],"subjects":[{"uid":1,"gids":[],"gids":[]}]}\n',
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_subject_missing_uid():
    """Subject missing uid refuses."""
    work = _opaque_work("refuse-subject-no-uid")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, {"entries": [], "subjects": [{"gids": []}]})
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_subject_uid_negative():
    """Negative subject uid refuses."""
    work = _opaque_work("refuse-subject-uid-neg")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, {"entries": [], "subjects": [{"uid": -1, "gids": []}]})
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_subject_uid_negative_preserves_existing_report():
    """Negative uid refusal must not truncate a pre-existing sandbox-owned report."""
    work = _opaque_work("refuse-uid-neg-keep")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, {"entries": [], "subjects": [{"uid": -1, "gids": []}]})
    report = work / "r.json"
    before = b"keep\n"
    _sandbox_owned_file(report, before)
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=before,
    )


def test_refuse_subject_gids_negative():
    """Negative subject gids element refuses."""
    work = _opaque_work("refuse-subject-gids-neg")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, {"entries": [], "subjects": [{"uid": 1, "gids": [-2]}]})
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_subject_gids_negative_preserves_existing_report():
    """Negative gids refusal must not truncate a pre-existing sandbox-owned report."""
    work = _opaque_work("refuse-gids-neg-keep")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, {"entries": [], "subjects": [{"uid": 1, "gids": [-2]}]})
    report = work / "r.json"
    before = b"keep\n"
    _sandbox_owned_file(report, before)
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=before,
    )


def test_refuse_subject_uid_negative_preserves_report_symlink_target():
    """Negative uid refusal must not follow or truncate a report symlink target."""
    work = _opaque_work("refuse-uid-neg-link")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, {"entries": [], "subjects": [{"uid": -1, "gids": []}]})
    target = work / "target.json"
    before = b"keep\n"
    _sandbox_owned_file(target, before)
    report = work / "r.link"
    report.symlink_to(target)
    ensure_binary()
    fd, _wd = _inotify_watch_opens(target)
    try:
        for exe in (AGENT_BIN, BIN):
            proc = _run_refusal_nofollow(
                exe,
                ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
            )
            assert proc.returncode == 2
            assert proc.stdout == b""
            assert proc.stderr == REFUSE_TEXT
            assert report.is_symlink()
        try:
            data = os.read(fd, 4096)
        except BlockingIOError:
            data = b""
        assert data == b"", f"late numeric refusal followed --report symlink (IN_OPEN): {data!r}"
    finally:
        os.close(fd)
    assert target.read_bytes() == before


def test_refuse_subject_gids_negative_preserves_report_symlink_no_open():
    """Negative gids refusal must not follow a --report symlink (IN_OPEN)."""
    work = _opaque_work("refuse-gids-neg-link-open")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, {"entries": [], "subjects": [{"uid": 1, "gids": [-2]}]})
    target = work / "target.json"
    before = b"keep\n"
    _sandbox_owned_file(target, before)
    report = work / "r.link"
    report.symlink_to(target)
    ensure_binary()
    fd, _wd = _inotify_watch_opens(target)
    try:
        for exe in (AGENT_BIN, BIN):
            proc = _run_refusal_nofollow(
                exe,
                ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
            )
            assert proc.returncode == 2
            assert proc.stdout == b""
            assert proc.stderr == REFUSE_TEXT
            assert report.is_symlink()
        try:
            data = os.read(fd, 4096)
        except BlockingIOError:
            data = b""
        assert data == b"", f"late gids refusal followed --report symlink (IN_OPEN): {data!r}"
    finally:
        os.close(fd)
    assert target.read_bytes() == before


@pytest.mark.parametrize(
    "label,audience",
    [
        ("allow-uids-negative", b'{"entries":[{"path":"a","allow_uids":[-1],"allow_gids":[]}],"subjects":[]}\n'),
        ("allow-gids-fraction", b'{"entries":[{"path":"a","allow_uids":[],"allow_gids":[1.5]}],"subjects":[]}\n'),
        ("allow-uids-exp-overflow", b'{"entries":[{"path":"a","allow_uids":[18446744073709551615e1],"allow_gids":[]}],"subjects":[]}\n'),
        ("leading-slash-path", b'{"entries":[{"path":"/a","allow_uids":[],"allow_gids":[]}],"subjects":[]}\n'),
        ("empty-component-path", b'{"entries":[{"path":"a//b","allow_uids":[],"allow_gids":[]}],"subjects":[]}\n'),
        ("subject-uid-exp-overflow", b'{"entries":[],"subjects":[{"uid":18446744073709551615e1,"gids":[]}]}\n'),
    ],
)
def test_refuse_late_semantic_never_opens_report_symlink(label, audience):
    """Every late path/numeric refusal leaves a --report symlink unopened (IN_OPEN) and its target intact."""
    work = _opaque_work(f"refuse-late-nofollow-{label}")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, audience)
    target = work / "target.json"
    before = b"keep\n"
    _sandbox_owned_file(target, before)
    report = work / "r.link"
    report.symlink_to(target)
    ensure_binary()
    fd, _wd = _inotify_watch_opens(target)
    try:
        for exe in (AGENT_BIN, BIN):
            proc = _run_refusal_nofollow(
                exe,
                ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
            )
            assert proc.returncode == 2
            assert proc.stdout == b""
            assert proc.stderr == REFUSE_TEXT
            assert report.is_symlink()
        try:
            data = os.read(fd, 4096)
        except BlockingIOError:
            data = b""
        assert data == b"", f"{label} refusal followed --report symlink (IN_OPEN): {data!r}"
    finally:
        os.close(fd)
    assert target.read_bytes() == before


def test_refuse_subject_uid_null():
    """Null subject uid refuses."""
    work = _opaque_work("refuse-subject-uid-null")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, b'{"entries":[],"subjects":[{"uid":null,"gids":[]}]}\n')
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_duplicate_paths_differing_allows():
    """Duplicate paths with differing allow lists refuse."""
    work = _opaque_work("refuse-dup-diff")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        {
            "entries": [
                {"path": "a", "allow_uids": [1], "allow_gids": []},
                {"path": "a", "allow_uids": [2], "allow_gids": []},
            ],
            "subjects": [],
        },
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_absolute_path():
    """Absolute audience path refuses."""
    work = _opaque_work("refuse-abs-path")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        {"entries": [{"path": "/a", "allow_uids": [], "allow_gids": []}], "subjects": []},
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_empty_component():
    """Audience path with an empty component refuses."""
    work = _opaque_work("refuse-empty-comp")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        {"entries": [{"path": "a//b", "allow_uids": [], "allow_gids": []}], "subjects": []},
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_dot_component():
    """Audience path with a . component refuses."""
    work = _opaque_work("refuse-dot-comp")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        {"entries": [{"path": "a/./b", "allow_uids": [], "allow_gids": []}], "subjects": []},
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_standalone_dot_path():
    """Audience path that is solely '.' refuses (dot component, not a walkable final)."""
    work = _opaque_work("refuse-standalone-dot")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        {"entries": [{"path": ".", "allow_uids": [], "allow_gids": []}], "subjects": []},
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def _ok_entry(path: str = "a") -> dict:
    return {"path": path, "allow_uids": [], "allow_gids": []}


def test_refuse_second_entry_not_object():
    """Malformed second entries element refuses even when the first is valid."""
    work = _opaque_work("refuse-entry1-num")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, {"entries": [_ok_entry(), 7], "subjects": []})
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_second_entry_extra_key():
    """Extra key on the second entry refuses even when the first is valid."""
    work = _opaque_work("refuse-entry1-extra")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        {
            "entries": [
                _ok_entry(),
                {"path": "b", "allow_uids": [], "allow_gids": [], "x": 1},
            ],
            "subjects": [],
        },
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_second_entry_absolute_path():
    """Absolute path on the second entry refuses even when the first is valid."""
    work = _opaque_work("refuse-entry1-abs")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        {"entries": [_ok_entry(), {"path": "/b", "allow_uids": [], "allow_gids": []}], "subjects": []},
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_second_entry_empty_component():
    """Empty path component on the second entry refuses even when the first is valid."""
    work = _opaque_work("refuse-entry1-empty")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        {
            "entries": [_ok_entry(), {"path": "a//b", "allow_uids": [], "allow_gids": []}],
            "subjects": [],
        },
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_second_entry_dot_component():
    """Dot path component on the second entry refuses even when the first is valid."""
    work = _opaque_work("refuse-entry1-dot")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        {
            "entries": [_ok_entry(), {"path": "a/./b", "allow_uids": [], "allow_gids": []}],
            "subjects": [],
        },
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_allow_uids_second_element_string():
    """String in a later allow_uids element refuses (not only index zero)."""
    work = _opaque_work("refuse-uids-el1-str")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        b'{"entries":[{"path":"missing","allow_uids":[0,"1"],"allow_gids":[]}],"subjects":[]}\n',
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_allow_gids_second_element_string():
    """String in a later allow_gids element refuses (not only index zero)."""
    work = _opaque_work("refuse-gids-el1-str")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        b'{"entries":[{"path":"missing","allow_uids":[],"allow_gids":[0,"1"]}],"subjects":[]}\n',
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_subject_gids_second_element_null():
    """Null in a later subject gids element refuses (not only index zero)."""
    work = _opaque_work("refuse-subj-gids-el1-null")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        b'{"entries":[],"subjects":[{"uid":1,"gids":[3,null]}]}\n',
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_allow_uids_second_element_overflow():
    """Later allow_uids element above uint64 max refuses (not only index zero)."""
    work = _opaque_work("refuse-uids-el1-ovf")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        b'{"entries":[{"path":"missing","allow_uids":[0,18446744073709551616],"allow_gids":[]}],"subjects":[]}\n',
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_allow_gids_second_element_fractional():
    """Later allow_gids fractional element refuses (not only index zero)."""
    work = _opaque_work("refuse-gids-el1-frac")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        b'{"entries":[{"path":"missing","allow_uids":[],"allow_gids":[0,1.5]}],"subjects":[]}\n',
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_subject_gids_second_element_negative():
    """Later subject gids negative element refuses (not only index zero)."""
    work = _opaque_work("refuse-subj-gids-el1-neg")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        b'{"entries":[],"subjects":[{"uid":1,"gids":[0,-1]}]}\n',
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_second_subject_extra_key():
    """Extra key on the second subject refuses even when the first is valid."""
    work = _opaque_work("refuse-subj1-extra")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        {
            "entries": [],
            "subjects": [
                {"uid": 1, "gids": []},
                {"uid": 2, "gids": [], "extra": True},
            ],
        },
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_second_subject_null():
    """Null second subject element refuses even when the first is valid."""
    work = _opaque_work("refuse-subj1-null")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        b'{"entries":[],"subjects":[{"uid":1,"gids":[]},null]}\n',
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_second_subject_uid_string():
    """String uid on the second subject refuses even when the first is valid."""
    work = _opaque_work("refuse-subj1-uid-str")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        b'{"entries":[],"subjects":[{"uid":1,"gids":[]},{"uid":"1","gids":[]}]}\n',
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_second_entry_allow_uids_second_element_string():
    """Later entry with a later string allow_uids element refuses."""
    work = _opaque_work("refuse-entry1-uids-el1")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        b'{"entries":[{"path":"missing","allow_uids":[],"allow_gids":[]},'
        b'{"path":"also-missing","allow_uids":[1,"2"],"allow_gids":[]}],'
        b'"subjects":[{"uid":1,"gids":[]}]}\n',
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_nested_second_subject_and_gid_malformed():
    """Combined later-subject extra key and later gids null refuses."""
    work = _opaque_work("refuse-nested-later")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        b'{"entries":[{"path":"missing","allow_uids":[],"allow_gids":[]}],'
        b'"subjects":[{"uid":1,"gids":[]},{"uid":2,"gids":[3,null],"extra":true}]}\n',
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_leading_dotdot():
    """Audience path starting with .. refuses."""
    work = _opaque_work("refuse-lead-dotdot")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        {"entries": [{"path": "../a", "allow_uids": [], "allow_gids": []}], "subjects": []},
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_dup_preserves_absent_report():
    """Semantic duplicate-path refusal leaves an absent report absent."""
    work = _opaque_work("refuse-dup-absent")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        {
            "entries": [
                {"path": "a", "allow_uids": [], "allow_gids": []},
                {"path": "a", "allow_uids": [], "allow_gids": []},
            ],
            "subjects": [],
        },
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_dup_preserves_existing_report():
    """Semantic duplicate-path refusal leaves existing sandbox-owned report bytes unchanged."""
    work = _opaque_work("refuse-dup-keep")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        {
            "entries": [
                {"path": "a", "allow_uids": [], "allow_gids": []},
                {"path": "a", "allow_uids": [], "allow_gids": []},
            ],
            "subjects": [],
        },
    )
    report = work / "r.json"
    before = b'{"keep":true}\n'
    _sandbox_owned_file(report, before)
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=before,
    )


def test_refuse_dup_preserves_report_symlink_target():
    """Semantic duplicate-path refusal leaves sandbox-owned report symlink target unchanged."""
    work = _opaque_work("refuse-dup-link")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        {
            "entries": [
                {"path": "a", "allow_uids": [], "allow_gids": []},
                {"path": "a", "allow_uids": [], "allow_gids": []},
            ],
            "subjects": [],
        },
    )
    target = work / "target.json"
    before = b'{"keep":true}\n'
    _sandbox_owned_file(target, before)
    report = work / "r.link"
    report.symlink_to(target)
    ensure_binary()
    fd, _wd = _inotify_watch_opens(target)
    try:
        for exe in (AGENT_BIN, BIN):
            proc = _run_refusal_nofollow(
                exe,
                ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
            )
            assert proc.returncode == 2
            assert proc.stdout == b""
            assert proc.stderr == REFUSE_TEXT
            assert report.is_symlink()
        try:
            data = os.read(fd, 4096)
        except BlockingIOError:
            data = b""
        assert data == b"", f"refusal followed --report symlink (IN_OPEN): {data!r}"
    finally:
        os.close(fd)
    assert target.read_bytes() == before


def test_refuse_preserves_report_bytes():
    """Refusal leaves an existing sandbox-owned regular report file unchanged."""
    work = _opaque_work("refuse-preserve")
    tree = work / "tree"
    tree.mkdir()
    aud = work / "a.json"
    aud.write_text("{bad\n", encoding="utf-8")
    report = work / "r.json"
    before = b'{"keep":true}\n'
    _sandbox_owned_file(report, before)
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=before,
    )


def test_refuse_preserves_report_symlink_target():
    """Refusal does not alter a sandbox-owned report symlink target."""
    work = _opaque_work("refuse-preserve-link")
    tree = work / "tree"
    tree.mkdir()
    aud = work / "a.json"
    aud.write_text("{bad\n", encoding="utf-8")
    target = work / "target.json"
    before = b'{"keep":true}\n'
    _sandbox_owned_file(target, before)
    report = work / "r.link"
    report.symlink_to(target)
    ensure_binary()
    fd, _wd = _inotify_watch_opens(target)
    try:
        for exe in (AGENT_BIN, BIN):
            proc = _run_refusal_nofollow(
                exe,
                ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
            )
            assert proc.returncode == 2
            assert proc.stdout == b""
            assert proc.stderr == REFUSE_TEXT
            assert report.is_symlink()
        try:
            data = os.read(fd, 4096)
        except BlockingIOError:
            data = b""
        assert data == b"", f"refusal followed --report symlink (IN_OPEN): {data!r}"
    finally:
        os.close(fd)
    assert target.read_bytes() == before


def test_refuse_duplicate_top_level_subjects_key():
    """Duplicate top-level subjects key refuses."""
    work = _opaque_work("refuse-dup-subjects-key")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, b'{"entries":[],"subjects":[],"subjects":[]}')
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_duplicate_top_level_entries_key():
    """Duplicate top-level entries key refuses."""
    work = _opaque_work("refuse-dup-entries-key")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, b'{"entries":[],"entries":[],"subjects":[]}')
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_duplicate_top_level_entries_key_escaped():
    """Duplicate entries key via JSON \\u escape of the same decoded name refuses."""
    work = _opaque_work("refuse-dup-entries-esc")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, b'{"entries":[],"\\u0065ntries":[],"subjects":[]}')
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_duplicate_top_level_subjects_key_escaped():
    """Duplicate subjects key via JSON \\u escape of the same decoded name refuses."""
    work = _opaque_work("refuse-dup-subjects-esc")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, b'{"entries":[],"subjects":[],"\\u0073ubjects":[]}')
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_entry_missing_path():
    """Entry missing path refuses."""
    work = _opaque_work("refuse-entry-no-path")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        {"entries": [{"allow_uids": [], "allow_gids": []}], "subjects": []},
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_entry_missing_allow_uids():
    """Entry missing allow_uids refuses."""
    work = _opaque_work("refuse-entry-no-uids")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        {"entries": [{"path": "a", "allow_gids": []}], "subjects": []},
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_missing_top_level_subjects():
    """Audience object missing top-level subjects refuses."""
    work = _opaque_work("refuse-no-subjects")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, b'{"entries":[]}')
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_path_trailing_slash():
    """Audience path with a trailing slash refuses."""
    work = _opaque_work("refuse-trail-slash")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        {"entries": [{"path": "a/", "allow_uids": [], "allow_gids": []}], "subjects": []},
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_path_trailing_dot():
    """Audience path ending with /. refuses."""
    work = _opaque_work("refuse-trail-dot")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        {"entries": [{"path": "a/.", "allow_uids": [], "allow_gids": []}], "subjects": []},
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_path_trailing_dotdot():
    """Audience path ending with /.. refuses."""
    work = _opaque_work("refuse-trail-dotdot")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        {"entries": [{"path": "a/..", "allow_uids": [], "allow_gids": []}], "subjects": []},
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_empty_path():
    """Empty audience path refuses."""
    work = _opaque_work("refuse-empty-path")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        {"entries": [{"path": "", "allow_uids": [], "allow_gids": []}], "subjects": []},
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_allow_uids_null_array():
    """Null allow_uids array refuses."""
    work = _opaque_work("refuse-uids-null-arr")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        b'{"entries":[{"path":"a","allow_uids":null,"allow_gids":[]}],"subjects":[]}\n',
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_allow_gids_null_array():
    """Null allow_gids array refuses."""
    work = _opaque_work("refuse-gids-null-arr")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        b'{"entries":[{"path":"a","allow_uids":[],"allow_gids":null}],"subjects":[]}\n',
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_subject_gids_null_array():
    """Null subject gids array refuses."""
    work = _opaque_work("refuse-subj-gids-null-arr")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, b'{"entries":[],"subjects":[{"uid":1,"gids":null}]}\n')
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_allow_uids_fractional():
    """Fractional allow_uids element refuses."""
    work = _opaque_work("refuse-uids-frac")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        b'{"entries":[{"path":"a","allow_uids":[1.5],"allow_gids":[]}],"subjects":[]}\n',
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_allow_gids_fractional():
    """Fractional allow_gids element refuses."""
    work = _opaque_work("refuse-gids-frac")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        b'{"entries":[{"path":"a","allow_uids":[],"allow_gids":[1.5]}],"subjects":[]}\n',
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_subject_uid_fractional():
    """Fractional subject uid refuses."""
    work = _opaque_work("refuse-subj-uid-frac")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, b'{"entries":[],"subjects":[{"uid":1.5,"gids":[]}]}\n')
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_subject_uid_near_integer_fraction():
    """Subject uid fraction that 64-bit float precision would erase must still refuse."""
    work = _opaque_work("refuse-subj-uid-near-int")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        b'{"entries":[],"subjects":[{"uid":1.00000000000000000001,"gids":[]}]}\n',
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_allow_uids_sub128bit_fraction():
    """allow_uids 1+10^-40 refuses (128-bit float IsInt would round to 1)."""
    work = _opaque_work("refuse-uids-sub128")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        b'{"entries":[{"path":"missing","allow_uids":[1.0000000000000000000000000000000000000001],"allow_gids":[]}],"subjects":[]}\n',
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_allow_gids_sub128bit_fraction():
    """allow_gids 1+10^-40 refuses (128-bit float IsInt would round to 1)."""
    work = _opaque_work("refuse-gids-sub128")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        b'{"entries":[{"path":"missing","allow_uids":[],"allow_gids":[1.0000000000000000000000000000000000000001]}],"subjects":[]}\n',
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_subject_uid_sub128bit_fraction():
    """Subject uid 1+10^-50 refuses (128-bit float IsInt would round to 1)."""
    work = _opaque_work("refuse-subj-uid-sub128")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        b'{"entries":[],"subjects":[{"uid":1.00000000000000000000000000000000000000000000000001,"gids":[]}]}\n',
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_subject_gids_sub128bit_fraction():
    """Subject gids 1+10^-50 refuses (128-bit float IsInt would round to 1)."""
    work = _opaque_work("refuse-subj-gids-sub128")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        b'{"entries":[],"subjects":[{"uid":1,"gids":[1.00000000000000000000000000000000000000000000000001]}]}\n',
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_subject_gids_fractional():
    """Fractional subject gids element refuses."""
    work = _opaque_work("refuse-subj-gids-frac")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, b'{"entries":[],"subjects":[{"uid":1,"gids":[1.5]}]}\n')
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_subject_gids_near_integer_fraction():
    """Subject gids fraction that 64-bit float precision would erase must still refuse."""
    work = _opaque_work("refuse-subj-gids-near-int")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        b'{"entries":[],"subjects":[{"uid":1,"gids":[1.00000000000000000001]}]}\n',
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_subject_uid_nonintegral_exponent():
    """Nonintegral exponent form 1e-1 for subject uid refuses (not truncated to 0)."""
    work = _opaque_work("refuse-uid-1e-1")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, b'{"entries":[],"subjects":[{"uid":1e-1,"gids":[]}]}\n')
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_allow_uids_nonintegral_exponent():
    """Nonintegral exponent form 1e-1 in allow_uids refuses."""
    work = _opaque_work("refuse-uids-1e-1")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        b'{"entries":[{"path":"a","allow_uids":[1e-1],"allow_gids":[]}],"subjects":[]}\n',
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_allow_gids_nonintegral_exponent():
    """Nonintegral exponent form 1e-1 in allow_gids refuses."""
    work = _opaque_work("refuse-gids-1e-1")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        b'{"entries":[{"path":"a","allow_uids":[],"allow_gids":[1e-1]}],"subjects":[]}\n',
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_subject_gids_nonintegral_exponent():
    """Nonintegral exponent form 1e-1 in subject gids refuses."""
    work = _opaque_work("refuse-subj-gids-1e-1")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, b'{"entries":[],"subjects":[{"uid":1,"gids":[1e-1]}]}\n')
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_allow_gids_null_element():
    """Null element inside allow_gids refuses."""
    work = _opaque_work("refuse-gids-null-el")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        b'{"entries":[{"path":"a","allow_uids":[],"allow_gids":[null]}],"subjects":[]}\n',
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_subject_gids_null_element():
    """Null element inside subject gids refuses."""
    work = _opaque_work("refuse-subj-gids-null-el")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, b'{"entries":[],"subjects":[{"uid":1,"gids":[null]}]}\n')
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_subject_missing_gids():
    """Subject missing gids refuses."""
    work = _opaque_work("refuse-subject-no-gids")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, {"entries": [], "subjects": [{"uid": 1}]})
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_subject_uid_string():
    """String subject uid refuses."""
    work = _opaque_work("refuse-subject-uid-str")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, b'{"entries":[],"subjects":[{"uid":"1001","gids":[]}]}\n')
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_subject_gids_not_array():
    """Non-array subject gids refuses."""
    work = _opaque_work("refuse-subject-gids-obj")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, {"entries": [], "subjects": [{"uid": 1, "gids": {}}]})
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_subject_gids_number():
    """Scalar number subject gids refuses."""
    work = _opaque_work("refuse-subject-gids-num")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, {"entries": [], "subjects": [{"uid": 1, "gids": 0}]})
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_subject_gids_string():
    """Scalar string subject gids refuses."""
    work = _opaque_work("refuse-subject-gids-str")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, {"entries": [], "subjects": [{"uid": 1, "gids": "[]"}]})
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_subject_gids_bool():
    """Scalar boolean subject gids refuses."""
    work = _opaque_work("refuse-subject-gids-bool")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, {"entries": [], "subjects": [{"uid": 1, "gids": True}]})
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_trailing_json_junk():
    """Audience JSON with trailing junk after a complete object refuses."""
    work = _opaque_work("refuse-json-junk")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, b'{"entries":[],"subjects":[]}]')
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_flag_report_without_value():
    """--report with no following value refuses."""
    work = _opaque_work("refuse-report-noval")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, {"entries": [], "subjects": []})
    assert_refuse(["--root", str(tree), "--audience", str(aud), "--report"])


def test_refuse_flag_root_without_value():
    """--root with no following value refuses."""
    work = _opaque_work("refuse-root-noval")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, {"entries": [], "subjects": []})
    report = work / "r.json"
    assert_refuse(
        ["--audience", str(aud), "--report", str(report), "--root"],
        report=report,
        before=None,
    )


def test_refuse_missing_top_level_entries():
    """Audience object missing top-level entries refuses."""
    work = _opaque_work("refuse-no-entries")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, {"subjects": []})
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_allow_uids_object():
    """Non-array allow_uids object refuses."""
    work = _opaque_work("refuse-uids-obj")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        {"entries": [{"path": "a", "allow_uids": {}, "allow_gids": []}], "subjects": []},
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_allow_uids_number():
    """Scalar number allow_uids field refuses."""
    work = _opaque_work("refuse-uids-num")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        {"entries": [{"path": "missing", "allow_uids": 0, "allow_gids": []}], "subjects": []},
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_allow_uids_string():
    """Scalar string allow_uids field refuses."""
    work = _opaque_work("refuse-uids-str")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        {"entries": [{"path": "missing", "allow_uids": "[]", "allow_gids": []}], "subjects": []},
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_allow_uids_scalar_bool():
    """Scalar boolean allow_uids field refuses."""
    work = _opaque_work("refuse-uids-sbool")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        {"entries": [{"path": "missing", "allow_uids": True, "allow_gids": []}], "subjects": []},
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_allow_gids_object():
    """Non-array allow_gids object refuses."""
    work = _opaque_work("refuse-gids-obj")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        {"entries": [{"path": "a", "allow_uids": [], "allow_gids": {}}], "subjects": []},
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_allow_gids_number():
    """Scalar number allow_gids field refuses."""
    work = _opaque_work("refuse-gids-num")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        {"entries": [{"path": "missing", "allow_uids": [], "allow_gids": 0}], "subjects": []},
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_allow_gids_string():
    """Scalar string allow_gids field refuses."""
    work = _opaque_work("refuse-gids-str")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        {"entries": [{"path": "missing", "allow_uids": [], "allow_gids": "[]"}], "subjects": []},
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_allow_gids_scalar_bool():
    """Scalar boolean allow_gids field refuses."""
    work = _opaque_work("refuse-gids-sbool")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        {"entries": [{"path": "missing", "allow_uids": [], "allow_gids": True}], "subjects": []},
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_flag_audience_without_value():
    """--audience with no following value refuses."""
    work = _opaque_work("refuse-aud-noval")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, {"entries": [], "subjects": []})
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--report", str(report), "--audience"],
        report=report,
        before=None,
    )


def test_refuse_root_is_fifo():
    """--root that is a FIFO refuses."""
    work = _opaque_work("refuse-root-fifo")
    tree, aud = _empty_tree_aud(work)
    root_fifo = work / "rootfifo"
    os.mkfifo(root_fifo, 0o644)
    _write_aud(aud, {"entries": [], "subjects": []})
    report = work / "r.json"
    assert_refuse(
        ["--root", str(root_fifo), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_uid_overflow():
    """Subject uid above uint64 max refuses."""
    work = _opaque_work("refuse-uid-overflow")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        b'{"entries":[],"subjects":[{"uid":18446744073709551616,"gids":[]}]}\n',
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_allow_uids_overflow():
    """allow_uids element above uint64 max refuses."""
    work = _opaque_work("refuse-uids-overflow")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        b'{"entries":[{"path":"a","allow_uids":[18446744073709551616],"allow_gids":[]}],"subjects":[]}\n',
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_allow_gids_overflow():
    """allow_gids element above uint64 max refuses."""
    work = _opaque_work("refuse-gids-overflow")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        b'{"entries":[{"path":"a","allow_uids":[],"allow_gids":[18446744073709551616]}],"subjects":[]}\n',
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_gids_overflow():
    """Subject gids element above uint64 max refuses."""
    work = _opaque_work("refuse-subj-gids-overflow")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        b'{"entries":[],"subjects":[{"uid":1,"gids":[18446744073709551616]}]}\n',
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_uid_exponent_overflow():
    """Subject uid 18446744073709551615e1 (uint64 max * 10) refuses."""
    work = _opaque_work("refuse-uid-exp-overflow")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        b'{"entries":[],"subjects":[{"uid":18446744073709551615e1,"gids":[]}]}\n',
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_allow_uids_exponent_overflow():
    """allow_uids 18446744073709551615e1 refuses as above uint64 max."""
    work = _opaque_work("refuse-uids-exp-overflow")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        b'{"entries":[{"path":"a","allow_uids":[18446744073709551615e1],"allow_gids":[]}],"subjects":[]}\n',
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_allow_gids_exponent_overflow():
    """allow_gids 18446744073709551615e1 refuses as above uint64 max."""
    work = _opaque_work("refuse-gids-exp-overflow")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        b'{"entries":[{"path":"a","allow_uids":[],"allow_gids":[18446744073709551615e1]}],"subjects":[]}\n',
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_gids_exponent_overflow():
    """Subject gids 18446744073709551615e1 refuses as above uint64 max."""
    work = _opaque_work("refuse-subj-gids-exp-overflow")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        b'{"entries":[],"subjects":[{"uid":1,"gids":[18446744073709551615e1]}]}\n',
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


@pytest.mark.parametrize(
    "label,audience",
    [
        ("uid-2p64-e0", b'{"entries":[],"subjects":[{"uid":18446744073709551616e0,"gids":[]}]}\n'),
        ("uid-2p64-frac-e19", b'{"entries":[],"subjects":[{"uid":1.8446744073709551616e19,"gids":[]}]}\n'),
        ("gids-2p64-neg-exp", b'{"entries":[],"subjects":[{"uid":1,"gids":[184467440737095516160e-1]}]}\n'),
        ("allow-uids-2p64-e0", b'{"entries":[{"path":"a","allow_uids":[18446744073709551616e0],"allow_gids":[]}],"subjects":[]}\n'),
        ("allow-gids-2p64-frac-e19", b'{"entries":[{"path":"a","allow_uids":[],"allow_gids":[1.8446744073709551616e19]}],"subjects":[]}\n'),
        ("allow-uids-1e20", b'{"entries":[{"path":"a","allow_uids":[1e20],"allow_gids":[]}],"subjects":[]}\n'),
    ],
)
def test_refuse_exponent_form_one_above_uint64_max(label, audience):
    """Exponent spellings of 2^64 (one above the inclusive uint64 maximum) refuse in every numeric field."""
    work = _opaque_work(f"refuse-exp-2p64-{label}")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, audience)
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


@pytest.mark.parametrize(
    "spelling",
    [b"18446744073709551615e0", b"1.8446744073709551615e19", b"184467440737095516150e-1"],
)
def test_accept_exponent_form_exact_uint64_max_uid(spelling):
    """Exponent spellings of exactly 18446744073709551615 are accepted and reported as that uid."""
    work = _opaque_work(f"accept-exp-max-uid-{spelling.decode()}")
    tree = work / "tree"
    tree.mkdir()
    os.chmod(tree, 0o755)
    os.chown(tree, 0, 0)
    secret = tree / "secret"
    secret.write_bytes(b"x\n")
    os.chown(secret, 0, 0)
    os.chmod(secret, 0o644)
    grant_sandbox_search(work)
    aud = work / "a.json"
    _write_aud(
        aud,
        b'{"entries":[{"path":"secret","allow_uids":[],"allow_gids":[]}],'
        b'"subjects":[{"uid":' + spelling + b',"gids":[]}]}\n',
    )
    expect = (
        b'{\n  "findings": [\n    {\n      "exposure": [\n        "uid:18446744073709551615"\n      ],\n'
        b'      "path": "secret",\n      "remediation": {\n        "mode": "0640"\n      }\n    }\n  ],\n'
        b'  "skipped": []\n}\n'
    )
    assert_success(tree, aud, expect, label=f"accept-exp-max-uid-{spelling.decode()}")


@pytest.mark.parametrize(
    "field",
    ["allow_uids", "allow_gids"],
)
def test_accept_exponent_form_exact_uint64_max_allow(field):
    """allow_uids/allow_gids 1.8446744073709551615e19 equals the subject's uint64-max id, so nothing is exposed."""
    work = _opaque_work(f"accept-exp-max-allow-{field}")
    tree = work / "tree"
    tree.mkdir()
    os.chmod(tree, 0o755)
    os.chown(tree, 0, 0)
    secret = tree / "secret"
    secret.write_bytes(b"x\n")
    os.chown(secret, 0, 0)
    os.chmod(secret, 0o644)
    grant_sandbox_search(work)
    aud = work / "a.json"
    uids = b"[1.8446744073709551615e19]" if field == "allow_uids" else b"[]"
    gids = b"[1.8446744073709551615e19]" if field == "allow_gids" else b"[]"
    _write_aud(
        aud,
        b'{"entries":[{"path":"secret","allow_uids":' + uids + b',"allow_gids":' + gids + b'}],'
        b'"subjects":[{"uid":18446744073709551615,"gids":[18446744073709551615]}]}\n',
    )
    assert_success(tree, aud, EMPTY_REPORT, label=f"accept-exp-max-allow-{field}")


def test_refuse_allow_uids_string_element():
    """String element inside allow_uids refuses."""
    work = _opaque_work("refuse-uids-str-el")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        b'{"entries":[{"path":"a","allow_uids":["1"],"allow_gids":[]}],"subjects":[]}\n',
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_allow_gids_string_element():
    """String element inside allow_gids refuses."""
    work = _opaque_work("refuse-gids-str-el")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        b'{"entries":[{"path":"a","allow_uids":[],"allow_gids":["1"]}],"subjects":[]}\n',
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_subject_gids_string_element():
    """String element inside subject gids refuses."""
    work = _opaque_work("refuse-subj-gids-str-el")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, b'{"entries":[],"subjects":[{"uid":1,"gids":["1"]}]}\n')
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_path_not_string():
    """Non-string entry path refuses."""
    work = _opaque_work("refuse-path-num")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        b'{"entries":[{"path":7,"allow_uids":[],"allow_gids":[]}],"subjects":[]}\n',
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_path_null():
    """Null entry path is not a string and refuses."""
    work = _opaque_work("refuse-path-null")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        b'{"entries":[{"path":null,"allow_uids":[],"allow_gids":[]}],"subjects":[]}\n',
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_allow_uids_boolean():
    """Boolean true inside allow_uids refuses (not numeric zero)."""
    work = _opaque_work("refuse-uids-bool")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        b'{"entries":[{"path":"missing","allow_uids":[true],"allow_gids":[]}],"subjects":[]}\n',
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_allow_gids_boolean():
    """Boolean false inside allow_gids refuses."""
    work = _opaque_work("refuse-gids-bool")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        b'{"entries":[{"path":"missing","allow_uids":[],"allow_gids":[false]}],"subjects":[]}\n',
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_subject_uid_boolean():
    """Boolean subject uid refuses."""
    work = _opaque_work("refuse-uid-bool")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, b'{"entries":[],"subjects":[{"uid":true,"gids":[]}]}\n')
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_allow_uids_object_element():
    """Object inside allow_uids is not a JSON number and refuses."""
    work = _opaque_work("refuse-uids-obj-el")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        b'{"entries":[{"path":"x","allow_uids":[{}],"allow_gids":[]}],"subjects":[]}\n',
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_allow_uids_array_element():
    """Array inside allow_uids is not a JSON number and refuses."""
    work = _opaque_work("refuse-uids-arr-el")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        b'{"entries":[{"path":"x","allow_uids":[[]],"allow_gids":[]}],"subjects":[]}\n',
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_allow_gids_array_element():
    """Array inside allow_gids is not a JSON number and refuses."""
    work = _opaque_work("refuse-gids-arr-el")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        b'{"entries":[{"path":"missing","allow_uids":[],"allow_gids":[[]]}],"subjects":[]}\n',
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_allow_gids_object_element():
    """Object inside allow_gids is not a JSON number and refuses."""
    work = _opaque_work("refuse-gids-obj-el")
    tree, aud = _empty_tree_aud(work)
    _write_aud(
        aud,
        b'{"entries":[{"path":"x","allow_uids":[],"allow_gids":[{}]}],"subjects":[]}\n',
    )
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_subject_uid_object():
    """Object subject uid is not a JSON number and refuses."""
    work = _opaque_work("refuse-uid-obj")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, b'{"entries":[],"subjects":[{"uid":{},"gids":[]}]}\n')
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_subject_uid_array():
    """Array subject uid is not a JSON number and refuses."""
    work = _opaque_work("refuse-uid-arr")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, b'{"entries":[],"subjects":[{"uid":[],"gids":[]}]}\n')
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_subject_gids_object_element():
    """Object inside subject gids is not a JSON number and refuses."""
    work = _opaque_work("refuse-subj-gids-obj-el")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, b'{"entries":[],"subjects":[{"uid":1,"gids":[{}]}]}\n')
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_subject_gids_array_element():
    """Array inside subject gids is not a JSON number and refuses."""
    work = _opaque_work("refuse-subj-gids-arr-el")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, b'{"entries":[],"subjects":[{"uid":1,"gids":[[]]}]}\n')
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_refuse_subject_gids_boolean():
    """Boolean element inside subject gids refuses."""
    work = _opaque_work("refuse-subj-gids-bool")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, b'{"entries":[],"subjects":[{"uid":1,"gids":[true]}]}\n')
    report = work / "r.json"
    assert_refuse(
        ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        report=report,
        before=None,
    )


def test_accept_uint64_subject_uid():
    """Subject uid at 2^63 is accepted; empty entries yield an empty report."""
    work = _opaque_work("accept-uint64-uid")
    tree = work / "tree"
    tree.mkdir()
    os.chmod(tree, 0o755)
    os.chown(tree, 0, 0)
    grant_sandbox_search(work)
    aud = work / "a.json"
    _write_aud(
        aud,
        b'{"entries":[],"subjects":[{"uid":9223372036854775808,"gids":[]}]}\n',
    )
    report = writable_report("accept-uint64-uid")
    proc = run_audit(tree, aud, report)
    assert proc.returncode == 0
    assert proc.stdout == b""
    assert report.read_bytes() == EMPTY_REPORT


def test_accept_uint64_allow_uids():
    """allow_uids element at 2^63 is accepted; missing path yields skipped missing."""
    work = _opaque_work("accept-uint64-allow-uids")
    tree = work / "tree"
    tree.mkdir()
    os.chmod(tree, 0o755)
    os.chown(tree, 0, 0)
    grant_sandbox_search(work)
    aud = work / "a.json"
    _write_aud(
        aud,
        b'{"entries":[{"path":"a","allow_uids":[9223372036854775808],"allow_gids":[]}],"subjects":[]}\n',
    )
    report = writable_report("accept-uint64-allow-uids")
    proc = run_audit(tree, aud, report)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == b""
    assert report.read_bytes() == _canon_bytes(
        {"findings": [], "skipped": [{"path": "a", "reason": "missing"}]}
    )


def test_accept_uint64_allow_gids():
    """allow_gids element at 2^63 is accepted; missing path yields skipped missing."""
    work = _opaque_work("accept-uint64-allow-gids")
    tree = work / "tree"
    tree.mkdir()
    os.chmod(tree, 0o755)
    os.chown(tree, 0, 0)
    grant_sandbox_search(work)
    aud = work / "a.json"
    _write_aud(
        aud,
        b'{"entries":[{"path":"a","allow_uids":[],"allow_gids":[9223372036854775808]}],"subjects":[]}\n',
    )
    report = writable_report("accept-uint64-allow-gids")
    proc = run_audit(tree, aud, report)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == b""
    assert report.read_bytes() == _canon_bytes(
        {"findings": [], "skipped": [{"path": "a", "reason": "missing"}]}
    )


def test_accept_uint64_subject_gids():
    """Subject gids element at 2^63 is accepted with empty entries."""
    work = _opaque_work("accept-uint64-gids")
    tree = work / "tree"
    tree.mkdir()
    os.chmod(tree, 0o755)
    os.chown(tree, 0, 0)
    grant_sandbox_search(work)
    aud = work / "a.json"
    _write_aud(
        aud,
        b'{"entries":[],"subjects":[{"uid":1,"gids":[9223372036854775808]}]}\n',
    )
    report = writable_report("accept-uint64-gids")
    proc = run_audit(tree, aud, report)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == b""
    assert report.read_bytes() == EMPTY_REPORT


def test_accept_uint64_max_all_fields():
    """Inclusive uint64 maximum is accepted in every numeric audience field."""
    work = _opaque_work("accept-uint64-max")
    tree = work / "tree"
    tree.mkdir()
    os.chmod(tree, 0o755)
    os.chown(tree, 0, 0)
    grant_sandbox_search(work)
    aud = work / "a.json"
    _write_aud(
        aud,
        b'{"entries":[{"path":"missing","allow_uids":[18446744073709551615],"allow_gids":[18446744073709551615]}],"subjects":[{"uid":18446744073709551615,"gids":[18446744073709551615]}]}\n',
    )
    report = writable_report("accept-uint64-max")
    proc = run_audit(tree, aud, report)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == b""
    assert report.read_bytes() == _canon_bytes(
        {"findings": [], "skipped": [{"path": "missing", "reason": "missing"}]}
    )


def test_accept_exponent_integer_uid():
    """JSON exponent form 1e0 is accepted as the integer value 1."""
    work = _opaque_work("accept-exp-uid")
    tree = work / "tree"
    tree.mkdir()
    os.chmod(tree, 0o755)
    os.chown(tree, 0, 0)
    grant_sandbox_search(work)
    aud = work / "a.json"
    _write_aud(aud, b'{"entries":[],"subjects":[{"uid":1e0,"gids":[]}]}\n')
    report = writable_report("accept-exp-uid")
    proc = run_audit(tree, aud, report)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == b""
    assert report.read_bytes() == EMPTY_REPORT


def test_accept_exponent_integer_subject_gids():
    """Subject gids accept integral JSON exponent token 1e0."""
    work = _opaque_work("accept-exp-gids")
    tree = work / "tree"
    tree.mkdir()
    os.chmod(tree, 0o755)
    os.chown(tree, 0, 0)
    grant_sandbox_search(work)
    aud = work / "a.json"
    _write_aud(aud, b'{"entries":[],"subjects":[{"uid":1,"gids":[1e0]}]}\n')
    report = writable_report("accept-exp-gids")
    proc = run_audit(tree, aud, report)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == b""
    assert report.read_bytes() == EMPTY_REPORT


def test_accept_negative_exponent_integral():
    """Nonzero significand with negative exponent that yields an integer (10e-1 == 1) is accepted."""
    work = _opaque_work("accept-10e-1")
    tree = work / "tree"
    tree.mkdir()
    os.chmod(tree, 0o755)
    os.chown(tree, 0, 0)
    grant_sandbox_search(work)
    aud = work / "a.json"
    _write_aud(
        aud,
        b'{"entries":[{"path":"missing","allow_uids":[10e-1],"allow_gids":[10e-1]}],"subjects":[{"uid":10e-1,"gids":[]}]}\n',
    )
    report = writable_report("accept-10e-1")
    proc = run_audit(tree, aud, report)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == b""
    assert report.read_bytes() == _canon_bytes(
        {"findings": [], "skipped": [{"path": "missing", "reason": "missing"}]}
    )


def test_accept_fractional_significand_exponent_integer():
    """JSON 1.5e1 (fractional significand, integer value 15) is accepted in allow_uids."""
    work = _opaque_work("accept-1-5e1")
    tree = work / "tree"
    tree.mkdir()
    os.chmod(tree, 0o755)
    os.chown(tree, 0, 0)
    grant_sandbox_search(work)
    aud = work / "a.json"
    _write_aud(
        aud,
        b'{"entries":[{"path":"a","allow_uids":[1.5e1],"allow_gids":[]}],"subjects":[]}\n',
    )
    report = writable_report("accept-1-5e1")
    proc = run_audit(tree, aud, report)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == b""
    assert report.read_bytes() == _canon_bytes(
        {"findings": [], "skipped": [{"path": "a", "reason": "missing"}]}
    )


def test_accept_1_5e1_subject_uid_allows_exposure():
    """1.5e1 as subject uid must decode to 15 so an allow_uids:[15] entry yields no finding.

    Truncating the significand to 10 would wrongly expose uid:10.
    """
    work = _opaque_work("accept-1-5e1-conseq")
    tree = work / "tree"
    tree.mkdir()
    os.chmod(tree, 0o755)
    os.chown(tree, 0, 0)
    leaf = tree / "a"
    leaf.write_text("t\n", encoding="utf-8")
    os.chown(leaf, 1000, 1000)
    os.chmod(leaf, 0o644)
    grant_sandbox_search(work)
    aud = work / "a.json"
    _write_aud(
        aud,
        b'{"entries":[{"path":"a","allow_uids":[15],"allow_gids":[]}],'
        b'"subjects":[{"uid":1.5e1,"gids":[]}]}\n',
    )
    report = writable_report("accept-1-5e1-conseq")
    proc = run_audit(tree, aud, report)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == b""
    assert report.read_bytes() == EMPTY_REPORT


def test_accept_1_5e1_allow_uids_allows_subject():
    """1.5e1 in allow_uids must decode to 15 so subject uid 15 yields no finding."""
    work = _opaque_work("accept-1-5e1-allow-uids")
    tree = work / "tree"
    tree.mkdir()
    os.chmod(tree, 0o755)
    os.chown(tree, 0, 0)
    leaf = tree / "a"
    leaf.write_text("t\n", encoding="utf-8")
    os.chown(leaf, 1000, 1000)
    os.chmod(leaf, 0o644)
    grant_sandbox_search(work)
    aud = work / "a.json"
    _write_aud(
        aud,
        b'{"entries":[{"path":"a","allow_uids":[1.5e1],"allow_gids":[]}],'
        b'"subjects":[{"uid":15,"gids":[]}]}\n',
    )
    report = writable_report("accept-1-5e1-allow-uids")
    proc = run_audit(tree, aud, report)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == b""
    assert report.read_bytes() == EMPTY_REPORT


def test_accept_1_5e1_allow_gids_allows_subject():
    """1.5e1 in allow_gids must decode to 15 so a subject with gid 15 yields no finding."""
    work = _opaque_work("accept-1-5e1-allow-gids")
    tree = work / "tree"
    tree.mkdir()
    os.chmod(tree, 0o755)
    os.chown(tree, 0, 0)
    leaf = tree / "a"
    leaf.write_text("t\n", encoding="utf-8")
    os.chown(leaf, 1000, 1000)
    os.chmod(leaf, 0o644)
    grant_sandbox_search(work)
    aud = work / "a.json"
    _write_aud(
        aud,
        b'{"entries":[{"path":"a","allow_uids":[],"allow_gids":[1.5e1]}],'
        b'"subjects":[{"uid":2000,"gids":[15]}]}\n',
    )
    report = writable_report("accept-1-5e1-allow-gids")
    proc = run_audit(tree, aud, report)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == b""
    assert report.read_bytes() == EMPTY_REPORT


def test_accept_1_5e1_subject_gids_allows_exposure():
    """1.5e1 in subject gids must decode to 15 so allow_gids:[15] yields no finding."""
    work = _opaque_work("accept-1-5e1-subj-gids")
    tree = work / "tree"
    tree.mkdir()
    os.chmod(tree, 0o755)
    os.chown(tree, 0, 0)
    leaf = tree / "a"
    leaf.write_text("t\n", encoding="utf-8")
    os.chown(leaf, 1000, 1000)
    os.chmod(leaf, 0o644)
    grant_sandbox_search(work)
    aud = work / "a.json"
    _write_aud(
        aud,
        b'{"entries":[{"path":"a","allow_uids":[],"allow_gids":[15]}],'
        b'"subjects":[{"uid":2000,"gids":[1.5e1]}]}\n',
    )
    report = writable_report("accept-1-5e1-subj-gids")
    proc = run_audit(tree, aud, report)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == b""
    assert report.read_bytes() == EMPTY_REPORT


def test_accept_decimal_integer_allow_uids():
    """JSON decimal form 1.0 is accepted as the integer value 1 in allow_uids."""
    work = _opaque_work("accept-dec-uids")
    tree = work / "tree"
    tree.mkdir()
    os.chmod(tree, 0o755)
    os.chown(tree, 0, 0)
    grant_sandbox_search(work)
    aud = work / "a.json"
    _write_aud(
        aud,
        b'{"entries":[{"path":"a","allow_uids":[1.0],"allow_gids":[]}],"subjects":[]}\n',
    )
    report = writable_report("accept-dec-uids")
    proc = run_audit(tree, aud, report)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == b""
    assert report.read_bytes() == _canon_bytes(
        {"findings": [], "skipped": [{"path": "a", "reason": "missing"}]}
    )


def test_accept_decimal_integer_allow_gids():
    """JSON 1.0 is accepted as integer 1 in allow_gids."""
    work = _opaque_work("accept-dec-gids")
    tree = work / "tree"
    tree.mkdir()
    os.chmod(tree, 0o755)
    os.chown(tree, 0, 0)
    grant_sandbox_search(work)
    aud = work / "a.json"
    _write_aud(
        aud,
        b'{"entries":[{"path":"missing","allow_uids":[],"allow_gids":[1.0]}],"subjects":[]}\n',
    )
    report = writable_report("accept-dec-gids")
    proc = run_audit(tree, aud, report)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == b""
    assert report.read_bytes() == _canon_bytes(
        {"findings": [], "skipped": [{"path": "missing", "reason": "missing"}]}
    )


def test_accept_fractional_significand_allow_gids():
    """JSON 1.5e1 is accepted as integer 15 in allow_gids."""
    work = _opaque_work("accept-1-5e1-gids")
    tree = work / "tree"
    tree.mkdir()
    os.chmod(tree, 0o755)
    os.chown(tree, 0, 0)
    grant_sandbox_search(work)
    aud = work / "a.json"
    _write_aud(
        aud,
        b'{"entries":[{"path":"missing","allow_uids":[],"allow_gids":[1.5e1]}],"subjects":[]}\n',
    )
    report = writable_report("accept-1-5e1-gids")
    proc = run_audit(tree, aud, report)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == b""
    assert report.read_bytes() == _canon_bytes(
        {"findings": [], "skipped": [{"path": "missing", "reason": "missing"}]}
    )


def test_accept_decimal_integer_subject_uid():
    """JSON 1.0 is accepted as integer 1 for subject uid."""
    work = _opaque_work("accept-dec-uid")
    tree = work / "tree"
    tree.mkdir()
    os.chmod(tree, 0o755)
    os.chown(tree, 0, 0)
    grant_sandbox_search(work)
    aud = work / "a.json"
    _write_aud(aud, b'{"entries":[],"subjects":[{"uid":1.0,"gids":[]}]}\n')
    report = writable_report("accept-dec-uid")
    proc = run_audit(tree, aud, report)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == b""
    assert report.read_bytes() == EMPTY_REPORT


def test_accept_fractional_significand_subject_uid():
    """JSON 1.5e1 is accepted as integer 15 for subject uid."""
    work = _opaque_work("accept-1-5e1-uid")
    tree = work / "tree"
    tree.mkdir()
    os.chmod(tree, 0o755)
    os.chown(tree, 0, 0)
    grant_sandbox_search(work)
    aud = work / "a.json"
    _write_aud(aud, b'{"entries":[],"subjects":[{"uid":1.5e1,"gids":[]}]}\n')
    report = writable_report("accept-1-5e1-uid")
    proc = run_audit(tree, aud, report)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == b""
    assert report.read_bytes() == EMPTY_REPORT


def test_accept_decimal_integer_subject_gids():
    """JSON 1.0 is accepted as integer 1 in subject gids."""
    work = _opaque_work("accept-dec-subj-gids")
    tree = work / "tree"
    tree.mkdir()
    os.chmod(tree, 0o755)
    os.chown(tree, 0, 0)
    grant_sandbox_search(work)
    aud = work / "a.json"
    _write_aud(aud, b'{"entries":[],"subjects":[{"uid":1,"gids":[1.0]}]}\n')
    report = writable_report("accept-dec-subj-gids")
    proc = run_audit(tree, aud, report)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == b""
    assert report.read_bytes() == EMPTY_REPORT


def test_accept_fractional_significand_subject_gids():
    """JSON 1.5e1 is accepted as integer 15 in subject gids."""
    work = _opaque_work("accept-1-5e1-subj-gids")
    tree = work / "tree"
    tree.mkdir()
    os.chmod(tree, 0o755)
    os.chown(tree, 0, 0)
    grant_sandbox_search(work)
    aud = work / "a.json"
    _write_aud(aud, b'{"entries":[],"subjects":[{"uid":1,"gids":[1.5e1]}]}\n')
    report = writable_report("accept-1-5e1-subj-gids")
    proc = run_audit(tree, aud, report)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == b""
    assert report.read_bytes() == EMPTY_REPORT


def test_accept_minus_zero_allow_uids():
    """JSON -0 is the integer value 0 and is accepted in allow_uids."""
    work = _opaque_work("accept-minus-zero")
    tree = work / "tree"
    tree.mkdir()
    os.chmod(tree, 0o755)
    os.chown(tree, 0, 0)
    grant_sandbox_search(work)
    aud = work / "a.json"
    _write_aud(
        aud,
        b'{"entries":[{"path":"missing","allow_uids":[-0],"allow_gids":[]}],"subjects":[]}\n',
    )
    report = writable_report("accept-minus-zero")
    proc = run_audit(tree, aud, report)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == b""
    assert report.read_bytes() == _canon_bytes(
        {"findings": [], "skipped": [{"path": "missing", "reason": "missing"}]}
    )


def test_accept_minus_zero_allow_gids():
    """JSON -0 is accepted as integer 0 in allow_gids."""
    work = _opaque_work("accept-minus-zero-gids")
    tree = work / "tree"
    tree.mkdir()
    os.chmod(tree, 0o755)
    os.chown(tree, 0, 0)
    grant_sandbox_search(work)
    aud = work / "a.json"
    _write_aud(
        aud,
        b'{"entries":[{"path":"missing","allow_uids":[],"allow_gids":[-0]}],"subjects":[]}\n',
    )
    report = writable_report("accept-minus-zero-gids")
    proc = run_audit(tree, aud, report)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == b""
    assert report.read_bytes() == _canon_bytes(
        {"findings": [], "skipped": [{"path": "missing", "reason": "missing"}]}
    )


def test_accept_minus_zero_subject_uid_and_gids():
    """JSON -0 is accepted as integer 0 for subject uid and gids."""
    work = _opaque_work("accept-minus-zero-subj")
    tree = work / "tree"
    tree.mkdir()
    os.chmod(tree, 0o755)
    os.chown(tree, 0, 0)
    grant_sandbox_search(work)
    aud = work / "a.json"
    _write_aud(aud, b'{"entries":[],"subjects":[{"uid":-0,"gids":[-0]}]}\n')
    report = writable_report("accept-minus-zero-subj")
    proc = run_audit(tree, aud, report)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == b""
    assert report.read_bytes() == EMPTY_REPORT


def test_accept_zero_extreme_exponent():
    """JSON 0e9223372036854775808 is exact zero and is accepted in allow_uids."""
    work = _opaque_work("accept-zero-huge-exp")
    tree = work / "tree"
    tree.mkdir()
    os.chmod(tree, 0o755)
    os.chown(tree, 0, 0)
    grant_sandbox_search(work)
    aud = work / "a.json"
    _write_aud(
        aud,
        b'{"entries":[{"path":"x","allow_uids":[0e9223372036854775808],"allow_gids":[]}],"subjects":[]}\n',
    )
    report = writable_report("accept-zero-huge-exp")
    proc = run_audit(tree, aud, report)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == b""
    assert report.read_bytes() == _canon_bytes(
        {"findings": [], "skipped": [{"path": "x", "reason": "missing"}]}
    )


def test_exact_exponent_allow_uids_unequal():
    """allow_uids 9007199254740993e0 must not float64-round to subject uid 2^53."""
    work = _opaque_work("exact-e0-allow-uids")
    tree = work / "tree"
    tree.mkdir()
    os.chmod(tree, 0o755)
    os.chown(tree, 0, 0)
    conf = tree / "a"
    conf.write_text("t\n", encoding="utf-8")
    os.chown(conf, 1000, 1000)
    os.chmod(conf, 0o644)
    grant_sandbox_search(work)
    aud = work / "a.json"
    _write_aud(
        aud,
        b'{"entries":[{"path":"a","allow_uids":[9007199254740993e0],"allow_gids":[]}],'
        b'"subjects":[{"uid":9007199254740992,"gids":[]}]}\n',
    )
    report = writable_report("exact-e0-allow-uids")
    proc = run_audit(tree, aud, report)
    assert proc.returncode == 0, proc.stderr
    assert report.read_bytes() == _canon_bytes(
        {
            "findings": [
                {
                    "exposure": ["uid:9007199254740992"],
                    "path": "a",
                    "remediation": {"mode": "0640"},
                }
            ],
            "skipped": [],
        }
    )


def test_exact_exponent_allow_gids_unequal():
    """allow_gids 9007199254740993e0 must not float64-round-match subject gid 2^53."""
    work = _opaque_work("exact-e0-allow-gids")
    tree = work / "tree"
    tree.mkdir()
    os.chmod(tree, 0o755)
    os.chown(tree, 0, 0)
    conf = tree / "a"
    conf.write_text("t\n", encoding="utf-8")
    os.chown(conf, 1000, 1000)
    os.chmod(conf, 0o644)
    grant_sandbox_search(work)
    aud = work / "a.json"
    _write_aud(
        aud,
        b'{"entries":[{"path":"a","allow_uids":[],"allow_gids":[9007199254740993e0]}],'
        b'"subjects":[{"uid":2000,"gids":[9007199254740992]}]}\n',
    )
    report = writable_report("exact-e0-allow-gids")
    proc = run_audit(tree, aud, report)
    assert proc.returncode == 0, proc.stderr
    assert report.read_bytes() == _canon_bytes(
        {
            "findings": [
                {
                    "exposure": ["uid:2000"],
                    "path": "a",
                    "remediation": {"mode": "0640"},
                }
            ],
            "skipped": [],
        }
    )


def test_exact_decimal_subject_uid_exposure():
    """Subject uid 9007199254740993.0 must appear exactly in the exposure string."""
    work = _opaque_work("exact-dec-uid-exp")
    tree = work / "tree"
    tree.mkdir()
    os.chmod(tree, 0o755)
    os.chown(tree, 0, 0)
    conf = tree / "a"
    conf.write_text("t\n", encoding="utf-8")
    os.chown(conf, 1000, 1000)
    os.chmod(conf, 0o644)
    grant_sandbox_search(work)
    aud = work / "a.json"
    _write_aud(
        aud,
        b'{"entries":[{"path":"a","allow_uids":[],"allow_gids":[]}],'
        b'"subjects":[{"uid":9007199254740993.0,"gids":[]}]}\n',
    )
    report = writable_report("exact-dec-uid-exp")
    proc = run_audit(tree, aud, report)
    assert proc.returncode == 0, proc.stderr
    assert report.read_bytes() == _canon_bytes(
        {
            "findings": [
                {
                    "exposure": ["uid:9007199254740993"],
                    "path": "a",
                    "remediation": {"mode": "0640"},
                }
            ],
            "skipped": [],
        }
    )


def test_report_creates_missing_parent():
    """Success writes --report under a nonexistent parent directory."""
    work = _opaque_work("report-mkdir")
    tree = work / "tree"
    tree.mkdir()
    os.chmod(tree, 0o755)
    os.chown(tree, 0, 0)
    grant_sandbox_search(work)
    aud = work / "a.json"
    _write_aud(aud, {"entries": [], "subjects": []})
    parent = work / "new-parent"
    report = parent / "report.json"
    assert not parent.exists()
    ensure_binary()
    for exe in (AGENT_BIN, BIN):
        if report.exists():
            report.unlink()
        if parent.exists():
            shutil.rmtree(parent)
        proc = _run_exe(
            exe,
            ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout == b""
        assert report.is_file()
        assert report.read_text(encoding="utf-8") == '{\n  "findings": [],\n  "skipped": []\n}\n'


def test_report_creates_nested_missing_parents():
    """Success creates multiple missing ancestor directories for --report (MkdirAll, not Mkdir)."""
    work = _opaque_work("report-mkdir-nested")
    tree = work / "tree"
    tree.mkdir()
    os.chmod(tree, 0o755)
    os.chown(tree, 0, 0)
    grant_sandbox_search(work)
    aud = work / "a.json"
    _write_aud(aud, {"entries": [], "subjects": []})
    ensure_binary()
    for exe, tag in ((AGENT_BIN, "agent"), (BIN, "rebuilt")):
        top = work / f"new-parent-{tag}"
        report = top / "deeper" / "report.json"
        assert not top.exists()
        proc = _run_exe(
            exe,
            ["--root", str(tree), "--audience", str(aud), "--report", str(report)],
        )
        assert proc.returncode == 0, (tag, proc.stderr)
        assert report.is_file()
        assert report.read_text(encoding="utf-8") == '{\n  "findings": [],\n  "skipped": []\n}\n'


def test_success_writes_through_report_symlink():
    """Successful run overwrites the target of a --report symlink and exits 0."""
    work = _opaque_work("success-report-symlink")
    tree = work / "tree"
    tree.mkdir()
    os.chmod(tree, 0o755)
    os.chown(tree, 0, 0)
    grant_sandbox_search(work)
    aud = work / "a.json"
    _write_aud(aud, {"entries": [], "subjects": []})
    target = work / "target-report.json"
    target.write_bytes(b'{"keep":false}\n')
    os.chown(target, SANDBOX_UID, SANDBOX_UID)
    os.chmod(target, 0o644)
    report = work / "report.link"
    report.symlink_to(target)
    proc = run_audit(tree, aud, report)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == b""
    assert report.is_symlink()
    assert target.read_text(encoding="utf-8") == '{\n  "findings": [],\n  "skipped": []\n}\n'


def test_success_writes_through_relative_report_symlink():
    """Successful run follows a relative --report symlink target (not cwd-relative Readlink)."""
    work = _opaque_work("success-report-rel-link")
    tree = work / "tree"
    tree.mkdir()
    os.chmod(tree, 0o755)
    os.chown(tree, 0, 0)
    grant_sandbox_search(work)
    aud = work / "a.json"
    _write_aud(aud, {"entries": [], "subjects": []})
    target = work / "target.json"
    _sandbox_owned_file(target, b"old\n")
    report = work / "report.link"
    report.symlink_to("target.json")
    proc = run_audit(tree, aud, report)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == b""
    assert report.is_symlink()
    assert os.readlink(report) == "target.json"
    assert target.read_text(encoding="utf-8") == '{\n  "findings": [],\n  "skipped": []\n}\n'


def test_accept_empty_audience():
    """Empty entries and subjects yield empty findings, skipped, and empty stdout."""
    work = _opaque_work("accept-empty")
    tree = work / "tree"
    tree.mkdir()
    os.chmod(tree, 0o755)
    os.chown(tree, 0, 0)
    grant_sandbox_search(work)
    aud = work / "a.json"
    aud.write_text('{"entries":[],"subjects":[]}\n', encoding="utf-8")
    os.chmod(aud, 0o644)
    report = writable_report("accept-empty")
    proc = run_audit(tree, aud, report)
    assert proc.returncode == 0
    assert proc.stdout == b""
    assert report.read_text(encoding="utf-8") == '{\n  "findings": [],\n  "skipped": []\n}\n'


def test_success_overwrites_existing_report():
    """Successful empty-audience run overwrites preexisting report bytes."""
    work = _opaque_work("success-overwrite")
    tree = work / "tree"
    tree.mkdir()
    os.chmod(tree, 0o755)
    os.chown(tree, 0, 0)
    grant_sandbox_search(work)
    aud = work / "a.json"
    aud.write_text('{"entries":[],"subjects":[]}\n', encoding="utf-8")
    os.chmod(aud, 0o644)
    report = writable_report("success-overwrite")
    report.write_bytes(b"old\n")
    os.chown(report, SANDBOX_UID, SANDBOX_UID)
    os.chmod(report, 0o644)
    proc = run_audit(tree, aud, report)
    assert proc.returncode == 0, proc.stderr
    assert report.read_text(encoding="utf-8") == '{\n  "findings": [],\n  "skipped": []\n}\n'


def test_bench_dangling_intermediate_escaped():
    """Dangling intermediate symlink is skipped as escaped."""
    tree, audience, expect = stage_case("dangling_intermediate_escaped")
    assert_success(tree, audience, expect, label="bench-dangling-inter")
    assert json.loads(expect)["skipped"][0]["reason"] == "escaped"


def test_bench_group_exposure_keeps_other():
    """Group exposure remediation clears group-read while keeping other-read."""
    tree, audience, expect = stage_case("group_exposure_keeps_other")
    assert_success(tree, audience, expect, label="bench-group-keeps-other")
    assert json.loads(expect)["findings"][0]["remediation"]["mode"] == "0604"


def test_bench_parent_above_root_ignored():
    """Hostile parent above root does not block in-root exposure detection."""
    tree, audience, expect = stage_case("parent_above_root_ignored")
    assert_success(tree, audience, expect, label="bench-hostile-parent")


def test_determinism_sealed_world():
    """Two runs over the same sealed tree produce identical report bytes."""
    tree, audience, expect = stage_case("world_readable")
    r1 = writable_report("det-a")
    r2 = writable_report("det-b")
    assert run_audit(tree, audience, r1).returncode == 0
    assert run_audit(tree, audience, r2).returncode == 0
    assert r1.read_bytes() == r2.read_bytes() == expect


def test_agent_binary_runs_unprivileged():
    """Graded invocations drop to the sandbox uid via setpriv --no-new-privs."""
    probe = subprocess.run(
        _sandbox_argv(
            [
                "python3",
                "-c",
                (
                    "import os,sys\n"
                    "print('uid=' + str(os.geteuid()), flush=True)\n"
                    "print('gid=' + str(os.getegid()), flush=True)\n"
                    "sys.exit(0 if os.geteuid()==2000 and os.getegid()==2000 else 1)\n"
                ),
            ]
        ),
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert probe.returncode == 0, probe.stderr
    assert "uid=2000" in probe.stdout
    assert "gid=2000" in probe.stdout
    # Smoke the rebuilt auditor under the same drop privileges used for grading.
    ensure_binary()
    work = _opaque_work("unpriv-bin-smoke")
    tree, aud = _empty_tree_aud(work)
    _write_aud(aud, {"entries": [], "subjects": []})
    report = work / "r.json"
    proc = run_audit(tree, aud, report)
    assert proc.returncode == 0, proc.stderr
    assert report.read_text(encoding="utf-8") == (
        '{\n  "findings": [],\n  "skipped": []\n}\n'
    )


def test_sandbox_cannot_read_reward_or_sealed():
    """Sandbox uid must not list /tests or resurrect sealed expects from disk.

    Re-locks /logs/verifier when possible. Some harbor /logs bind mounts ignore
    mode bits (stat may look locked while reads still succeed), so the hard
    assertion is the /tests tree the verifier controls.
    """
    reward = Path("/logs/verifier/reward.txt")
    reward_dir = reward.parent
    reward_dir.mkdir(parents=True, exist_ok=True)
    if not reward.exists():
        reward.write_text("0\n")
    try:
        os.chmod(reward_dir, 0o700)
        os.chmod(reward, 0o600)
        os.chown(reward_dir, 0, 0)
        os.chown(reward, 0, 0)
    except OSError:
        pass
    os.chmod(TESTS, 0o700)
    assert not SEALED.exists()
    probe = subprocess.run(
        _sandbox_argv(
            [
                "python3",
                "-c",
                (
                    "from pathlib import Path\n"
                    "paths = [Path('/tests'), Path('/tests/sealed')]\n"
                    "for p in paths:\n"
                    "    try:\n"
                    "        list(p.iterdir()) if (p.exists() and p.is_dir()) else p.read_bytes()\n"
                    "    except Exception:\n"
                    "        pass\n"
                    "    else:\n"
                    "        raise SystemExit('readable:' + str(p))\n"
                    "print('OK')\n"
                ),
            ]
        ),
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert probe.returncode == 0, (probe.stderr, probe.stdout)
    assert "OK" in probe.stdout
