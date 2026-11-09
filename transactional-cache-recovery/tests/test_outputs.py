"""Verifier harness for transactional-cache-recovery.

At import time the harness loads every sealed scenario and expect artifact into
memory, deletes ``/tests/sealed`` from disk, and chmods ``/tests`` to ``0700``.
Each graded invocation runs ``/app/src/cachebox.py`` under ``setpriv`` as uid/gid
2000 with a 20s wall timeout. Sealed cases use neutral work directory names so
argv never carries fixture vocabulary.
"""

from __future__ import annotations

import hashlib
import json
import os
import resource
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest

APP = Path("/app")
SRC = APP / "src" / "cachebox.py"
TESTS = Path("/tests")
SEALED = TESTS / "sealed"
WORK = Path("/work/out")
SANDBOX_ROOT = Path("/sandbox")
SANDBOX_UID = 2000
SANDBOX_GID = 2000
RUN_TIMEOUT = 20
REPORT_SENTINEL = b"sentinel\n"
REFUSE_STDERR = "refuse: bad args\n"


def _opaque_work(key: str) -> Path:
    digest = hashlib.sha256(f"tcr:{key}".encode()).hexdigest()[:16]
    return WORK / f"w{digest}"


def _snapshot_sealed() -> dict[str, dict[str, bytes]]:
    out: dict[str, dict[str, bytes]] = {}
    for case in sorted(p for p in SEALED.iterdir() if p.is_dir()):
        out[case.name] = {
            "scenario": (case / "scenario.json").read_bytes(),
            "expect_report": (case / "expect_report.json").read_bytes(),
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
        time.sleep(0.05)
        waited += 0.05
    for pid in _live_sandbox_pids():
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def _run(
    args: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    base_env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(SANDBOX_ROOT),
        "PYTHONSAFEPATH": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "LANG": "C.UTF-8",
    }
    if env:
        base_env.update(env)
    cmd = [
        "setpriv",
        f"--reuid={SANDBOX_UID}",
        f"--regid={SANDBOX_GID}",
        "--clear-groups",
        "--inh-caps=-all",
        "--",
        "python3",
        str(SRC),
        *args,
    ]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            env=base_env,
            capture_output=True,
            timeout=RUN_TIMEOUT,
            preexec_fn=_apply_resource_limits,
        )
    except subprocess.TimeoutExpired as exc:
        _drain_sandbox()
        raise AssertionError(f"timeout after {RUN_TIMEOUT}s") from exc
    finally:
        _drain_sandbox()
    return proc


def _write_owned(path: Path, data: bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.chown(path, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    os.chmod(path, mode)


def _prepare_case(key: str, scenario: bytes) -> tuple[Path, Path, Path]:
    root = _opaque_work(key)
    if root.exists():
        shutil.rmtree(root)
    store = root / "store"
    scenario_path = root / "scenario.json"
    report = root / "report.json"
    store.mkdir(parents=True)
    os.chown(root, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    os.chown(store, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    os.chmod(root, 0o755)
    os.chmod(store, 0o755)
    _write_owned(scenario_path, scenario)
    return store, scenario_path, report


def _assert_report(actual: bytes, expected: bytes) -> None:
    if actual != expected:
        try:
            left = json.loads(actual)
            right = json.loads(expected)
        except json.JSONDecodeError:
            raise AssertionError(
                f"report mismatch len={len(actual)} vs {len(expected)}"
            ) from None
        raise AssertionError(f"report JSON mismatch: {left!r} != {right!r}")


@pytest.mark.parametrize("case_name", SEALED_CASES)
def test_sealed_scenario(case_name: str) -> None:
    snap = SEALED_SNAP[case_name]
    store, scenario_path, report = _prepare_case(CASE_ID[case_name], snap["scenario"])
    proc = _run(
        ["--store", str(store), "--scenario", str(scenario_path), "--report", str(report)],
        cwd=store.parent,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    assert proc.stdout == b""
    assert report.is_file()
    _assert_report(report.read_bytes(), snap["expect_report"])


def test_example_simple_no_stale_read() -> None:
    scenario = (APP / "examples" / "simple" / "scenario.json").read_bytes()
    store, scenario_path, report = _prepare_case("example-simple", scenario)
    proc = _run(
        ["--store", str(store), "--scenario", str(scenario_path), "--report", str(report)],
        cwd=store.parent,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    data = json.loads(report.read_text(encoding="utf-8"))
    # staged get must not see uncommitted value
    assert data["results"][2] == {"found": False}
    assert data["results"][3]["status"] == "committed"
    assert data["results"][4] == {"found": True, "value": "one", "version": 1}


def test_example_regression_conflict_and_idempotent() -> None:
    scenario = (APP / "examples" / "regression" / "scenario.json").read_bytes()
    store, scenario_path, report = _prepare_case("example-regression", scenario)
    proc = _run(
        ["--store", str(store), "--scenario", str(scenario_path), "--report", str(report)],
        cwd=store.parent,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    data = json.loads(report.read_text(encoding="utf-8"))
    statuses = [r.get("status") for r in data["results"] if isinstance(r, dict)]
    assert "conflict" in statuses
    idem = [r for r in data["results"] if r.get("idempotent") is True]
    assert idem and idem[0]["status"] == "committed"
    assert data["final"]["entries"]["user.1"]["value"] == {"n": 3}
    assert data["final"]["entries"]["user.1"]["version"] == 2


def test_refuse_missing_flags_preserves_report(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    report.write_bytes(REPORT_SENTINEL)
    os.chown(tmp_path, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    os.chown(report, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    proc = _run(["--store", str(tmp_path)], cwd=tmp_path)
    assert proc.returncode == 2
    assert proc.stderr.decode("utf-8") == REFUSE_STDERR
    assert proc.stdout == b""
    assert report.read_bytes() == REPORT_SENTINEL


def test_refuse_store_not_dir(tmp_path: Path) -> None:
    store = tmp_path / "storefile"
    store.write_text("x", encoding="utf-8")
    scenario = tmp_path / "scenario.json"
    scenario.write_text('{"steps":[]}\n', encoding="utf-8")
    report = tmp_path / "report.json"
    report.write_bytes(REPORT_SENTINEL)
    for path in (tmp_path, store, scenario, report):
        os.chown(path, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    proc = _run(
        ["--store", str(store), "--scenario", str(scenario), "--report", str(report)],
        cwd=tmp_path,
    )
    assert proc.returncode == 2
    assert proc.stderr.decode("utf-8") == REFUSE_STDERR
    assert report.read_bytes() == REPORT_SENTINEL


def test_refuse_scenario_symlink(tmp_path: Path) -> None:
    store = tmp_path / "store"
    store.mkdir()
    real = tmp_path / "real.json"
    real.write_text('{"steps":[]}\n', encoding="utf-8")
    scenario = tmp_path / "scenario.json"
    scenario.symlink_to(real)
    report = tmp_path / "report.json"
    report.write_bytes(REPORT_SENTINEL)
    for path in (tmp_path, store, real, report):
        os.chown(path, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    proc = _run(
        ["--store", str(store), "--scenario", str(scenario), "--report", str(report)],
        cwd=tmp_path,
    )
    assert proc.returncode == 2
    assert proc.stderr.decode("utf-8") == REFUSE_STDERR
    assert report.read_bytes() == REPORT_SENTINEL


def test_sandbox_cannot_read_reward_or_tests() -> None:
    probe = SANDBOX_ROOT / "probe.py"
    probe.write_text(
        "import os,sys\n"
        "for p in ('/logs/verifier/reward.txt','/tests/test_outputs.py'):\n"
        "  try:\n"
        "    open(p,'rb').read(1)\n"
        "    sys.exit(11)\n"
        "  except Exception:\n"
        "    pass\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    os.chown(probe, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    os.chmod(probe, 0o644)
    cmd = [
        "setpriv",
        f"--reuid={SANDBOX_UID}",
        f"--regid={SANDBOX_GID}",
        "--clear-groups",
        "--inh-caps=-all",
        "--",
        "python3",
        str(probe),
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=10)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")


def test_consistency_across_reopen_matches_snapshot() -> None:
    """Independent check: durable final equals reopen snapshot after commit."""
    scenario = {
        "steps": [
            {"op": "begin", "txn": "t1"},
            {"op": "put", "txn": "t1", "key": "q", "value": {"ok": True}, "expected": 0},
            {"op": "commit", "txn": "t1", "op_id": "q1"},
            {"op": "snapshot"},
            {"op": "open"},
            {"op": "snapshot"},
        ]
    }
    raw = (json.dumps(scenario, indent=2) + "\n").encode()
    store, scenario_path, report = _prepare_case("consistency-reopen", raw)
    proc = _run(
        ["--store", str(store), "--scenario", str(scenario_path), "--report", str(report)],
        cwd=store.parent,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    data = json.loads(report.read_text(encoding="utf-8"))
    first = data["results"][3]
    second = data["results"][5]
    assert first["entries"] == second["entries"]
    assert first["ops"] == second["ops"]
    committed = store / "committed.json"
    assert committed.is_file()
    disk = json.loads(committed.read_text(encoding="utf-8"))
    assert disk["entries"] == data["final"]["entries"]
    assert disk["ops"] == data["final"]["ops"]
