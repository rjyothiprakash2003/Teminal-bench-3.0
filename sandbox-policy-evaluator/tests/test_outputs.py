"""Verifier for the SPE-2026 cordon sandbox policy evaluator."""

from __future__ import annotations

import json
import hashlib
import os
import resource
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

APP = Path("/app")
TESTS = Path("/tests")
STAGE = Path("/stage")
SRC_DIR = STAGE / "src"
BIN = STAGE / "bin" / "evaluate"
SAND_UID = 3100
SAND_GID = 3100
VERDICT_ORDER = ["allow", "deny", "deny_default", "reject_malformed"]
ROW_KEYS = ["action", "matched_rule", "op_id", "scope", "target", "verdict"]
BIN_DIGEST: bytes | None = None
BIN_INODE: int | None = None


def _snapshot_tree(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _parse_jsonl(raw: bytes) -> list[dict]:
    rows = []
    for line in raw.decode("utf-8").splitlines():
        rows.append(json.loads(line))
    return rows


def _assert_native_ints(obj: object, path: str = "$") -> None:
    if isinstance(obj, bool) or isinstance(obj, float):
        raise AssertionError(f"{path}: expected int-compatible JSON number, got {obj!r}")
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in {"op_count", "allow_count"} or (
                path.endswith("by_verdict") and key in VERDICT_ORDER
            ):
                assert isinstance(value, int) and not isinstance(value, bool), (
                    f"{path}.{key}: expected int, got {type(value).__name__}"
                )
            else:
                _assert_native_ints(value, f"{path}.{key}")
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            _assert_native_ints(item, f"{path}[{i}]")


def _row_key_orders(raw: bytes) -> list[list[str]]:
    orders = []
    for line in raw.decode("utf-8").splitlines():
        pairs = json.loads(line, object_pairs_hook=list)
        orders.append([k for k, _ in pairs])
    return orders


def _assert_verdict_rows_schema(raw: bytes) -> None:
    """Every verdict row must declare all six SPE fields in sorted key order (incl. empty target)."""
    assert raw, "verdicts.jsonl must not be empty when schema is asserted"
    for keys in _row_key_orders(raw):
        assert keys == ROW_KEYS, keys
    for row in _parse_jsonl(raw):
        assert set(row) == set(ROW_KEYS), row
        for key in ROW_KEYS:
            assert isinstance(row[key], str), (key, row[key])


HOLDOUT_FILES = _snapshot_tree(TESTS / "holdout")
EXPECTED_PRIMARY_VERDICTS_RAW = (TESTS / "expected" / "verdicts.jsonl").read_bytes()
EXPECTED_PRIMARY_SUMMARY_RAW = (TESTS / "expected" / "summary.json").read_bytes()
EXPECTED_HOLDOUT_VERDICTS_RAW = (TESTS / "expected" / "holdout_verdicts.jsonl").read_bytes()
EXPECTED_HOLDOUT_SUMMARY_RAW = (TESTS / "expected" / "holdout_summary.json").read_bytes()
EXPECTED_PRIMARY_ROWS = _parse_jsonl(EXPECTED_PRIMARY_VERDICTS_RAW)
EXPECTED_PRIMARY_SUMMARY = json.loads(EXPECTED_PRIMARY_SUMMARY_RAW.decode())
EXPECTED_HOLDOUT_ROWS = _parse_jsonl(EXPECTED_HOLDOUT_VERDICTS_RAW)
EXPECTED_HOLDOUT_SUMMARY = json.loads(EXPECTED_HOLDOUT_SUMMARY_RAW.decode())

DELIVERED_OUTPUT_PATHS = sorted(APP.joinpath("out").iterdir(), key=lambda path: path.name)
assert [path.name for path in DELIVERED_OUTPUT_PATHS] == ["summary.json", "verdicts.jsonl"]
assert all(path.is_file() and not path.is_symlink() for path in DELIVERED_OUTPUT_PATHS)
DELIVERED_PRIMARY_VERDICTS_RAW = (APP / "out" / "verdicts.jsonl").read_bytes()
DELIVERED_PRIMARY_SUMMARY_RAW = (APP / "out" / "summary.json").read_bytes()
os.chmod(APP / "out", 0o700)

assert (APP / "evaluate.go").is_file() and not (APP / "evaluate.go").is_symlink()
assert (APP / "go.mod").is_file() and not (APP / "go.mod").is_symlink()


def _go_mod_module_path(text: str) -> str:
    """Return the semantic module path from go.mod (trailing // comments ignored)."""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        if "//" in line:
            line = line.split("//", 1)[0].rstrip()
        if not line.startswith("module"):
            continue
        rest = line[len("module") :].strip()
        if len(rest) >= 2 and rest[0] == '"' and rest[-1] == '"':
            rest = rest[1:-1]
        return rest
    return ""


def _seed_embed_pack_resources() -> None:
    """Ensure //go:embed of shipped pack paths builds under separate-mode verification.

    Separate environment_mode delivers only task.toml artifacts into /app, so
    cordon/policy/probes may be absent there even though the agent image had them.
    Prefer bytes already staged from /app; otherwise seed from verifier fixtures.
    """
    seeds: list[tuple[str, list[Path]]] = [
        (
            "cordon/SPE_LEDGER.md",
            [
                APP / "cordon" / "SPE_LEDGER.md",
                TESTS / "holdout" / "cordon" / "SPE_LEDGER.md",
                TESTS / "primary_pack" / "cordon" / "SPE_LEDGER.md",
            ],
        ),
        (
            "policy/rules.jsonl",
            [
                APP / "policy" / "rules.jsonl",
                TESTS / "primary_pack" / "policy" / "rules.jsonl",
            ],
        ),
        (
            "probes/ops.jsonl",
            [
                APP / "probes" / "ops.jsonl",
                TESTS / "primary_pack" / "probes" / "ops.jsonl",
            ],
        ),
    ]
    sample_dirs = [
        APP / "cordon" / "samples",
        TESTS / "holdout" / "cordon" / "samples",
        TESTS / "primary_pack" / "cordon" / "samples",
    ]
    sample_names: set[str] = set()
    for d in sample_dirs:
        if d.is_dir():
            sample_names.update(path.name for path in d.iterdir() if path.is_file())
    for name in sorted(sample_names):
        seeds.append(
            (
                f"cordon/samples/{name}",
                [d / name for d in sample_dirs],
            )
        )
    for rel, candidates in seeds:
        dest = SRC_DIR / rel
        if dest.is_file():
            continue
        for src in candidates:
            if src.is_file():
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(src.read_bytes())
                dest.chmod(0o644)
                os.chown(dest.parent, SAND_UID, SAND_GID, follow_symlinks=False)
                os.chown(dest, SAND_UID, SAND_GID, follow_symlinks=False)
                break
        assert dest.is_file(), f"missing embed seed for {rel}"


def _stage_sources() -> None:
    """Stage the Go module tree, including shipped pack resources for //go:embed."""
    SRC_DIR.mkdir(parents=True, exist_ok=True)
    BIN.parent.mkdir(parents=True, exist_ok=True)
    copied = False
    for path in sorted(APP.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        rel = path.relative_to(APP)
        if not rel.parts:
            continue
        root = rel.parts[0]
        if root == "out":
            continue
        if list(rel.parts) == ["evaluate"] and path.suffix == "":
            continue
        dest = SRC_DIR / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(path.read_bytes())
        dest.chmod(0o644)
        os.chown(dest.parent, SAND_UID, SAND_GID, follow_symlinks=False)
        os.chown(dest, SAND_UID, SAND_GID, follow_symlinks=False)
        copied = True
    assert copied
    assert (SRC_DIR / "evaluate.go").is_file()
    assert (SRC_DIR / "go.mod").is_file()
    _seed_embed_pack_resources()
    assert (SRC_DIR / "cordon" / "SPE_LEDGER.md").is_file()
    assert (SRC_DIR / "policy" / "rules.jsonl").is_file()
    assert (SRC_DIR / "probes" / "ops.jsonl").is_file()
    assert _go_mod_module_path((SRC_DIR / "go.mod").read_text()) == "cordon.evaluate"
    os.chown(SRC_DIR, SAND_UID, SAND_GID, follow_symlinks=False)
    os.chown(BIN.parent, SAND_UID, SAND_GID, follow_symlinks=False)


_stage_sources()
os.chown(STAGE, SAND_UID, SAND_GID, follow_symlinks=False)
os.chmod(APP, 0o700)

shutil.rmtree(TESTS / "expected")
shutil.rmtree(TESTS / "holdout")
os.chmod(TESTS, 0o700)


def _materialize(root: Path, files: dict[str, bytes]) -> None:
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def _chown_tree(root: Path, uid: int, gid: int) -> None:
    for path in [root, *root.rglob("*")]:
        os.chown(path, uid, gid, follow_symlinks=False)


def _is_regular_file(path: Path) -> bool:
    """True only when path is a non-symlink regular file (symlink targets do not count)."""
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return False
    return stat.S_ISREG(mode)


def _live_sand_pids() -> list[int]:
    alive = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != SAND_UID:
                continue
            with (entry / "stat").open("rb") as handle:
                state = handle.read().rsplit(b")", 1)[1].split()[0]
        except (OSError, IndexError):
            continue
        if state != b"Z":
            alive.append(int(entry.name))
    return alive


def _drain_sand(deadline: float = 15.0) -> None:
    waited = 0.0
    while waited < deadline:
        if not _live_sand_pids():
            return
        time.sleep(0.02)
        waited += 0.02
    for pid in _live_sand_pids():
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _apply_limits() -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (1800, 1800))
    resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024 * 1024, 32 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    if hasattr(resource, "RLIMIT_NPROC"):
        resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))


def _seal_rebuilt_binary() -> None:
    """Make /stage/bin/evaluate root-owned and immutable to sand across replay runs."""
    global BIN_DIGEST, BIN_INODE
    assert BIN.is_file() and not BIN.is_symlink()
    os.chown(BIN, 0, 0, follow_symlinks=False)
    BIN.chmod(0o755)
    os.chown(BIN.parent, 0, 0, follow_symlinks=False)
    BIN.parent.chmod(0o755)
    os.chown(STAGE, 0, 0, follow_symlinks=False)
    STAGE.chmod(0o755)
    BIN_DIGEST = hashlib.sha256(BIN.read_bytes()).digest()
    BIN_INODE = BIN.lstat().st_ino


def _assert_binary_sealed() -> None:
    """Reject a replaced or sand-writable evaluate before every sand execution."""
    assert BIN_DIGEST is not None and BIN_INODE is not None
    assert BIN.is_file() and not BIN.is_symlink()
    st = BIN.lstat()
    assert st.st_uid == 0 and st.st_gid == 0
    assert st.st_ino == BIN_INODE
    assert hashlib.sha256(BIN.read_bytes()).digest() == BIN_DIGEST
    dst = BIN.parent.lstat()
    assert dst.st_uid == 0 and dst.st_gid == 0
    assert (dst.st_mode & 0o022) == 0


def _rebuild_binary() -> None:
    os.chown(BIN.parent, SAND_UID, SAND_GID, follow_symlinks=False)
    BIN.parent.chmod(0o755)
    if BIN.exists():
        os.chown(BIN, SAND_UID, SAND_GID, follow_symlinks=False)
        BIN.unlink()
    build = subprocess.run(
        [
            "/usr/bin/setpriv",
            "--reuid",
            str(SAND_UID),
            "--regid",
            str(SAND_GID),
            "--clear-groups",
            "--no-new-privs",
            "--",
            "go",
            "build",
            "-o",
            str(BIN),
            ".",
        ],
        cwd=str(SRC_DIR),
        capture_output=True,
        text=True,
        timeout=900,
        env={
            "HOME": str(STAGE / "home"),
            "GOPATH": str(STAGE / "gopath"),
            "GOCACHE": str(STAGE / "gocache"),
            "PATH": "/usr/local/go/bin:/usr/local/bin:/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TZ": "UTC",
        },
    )
    assert build.returncode == 0, build.stderr
    assert BIN.is_file() and not BIN.is_symlink()
    _seal_rebuilt_binary()
    _assert_binary_sealed()


(STAGE / "home").mkdir(parents=True, exist_ok=True)
(STAGE / "gopath").mkdir(parents=True, exist_ok=True)
(STAGE / "gocache").mkdir(parents=True, exist_ok=True)
_chown_tree(STAGE / "home", SAND_UID, SAND_GID)
_chown_tree(STAGE / "gopath", SAND_UID, SAND_GID)
_chown_tree(STAGE / "gocache", SAND_UID, SAND_GID)
_rebuild_binary()

DECOY_RULES = b'{"rule_id":"DECOY","ord":1,"effect":"allow","scope":"fs","mode":"exact","selector":"/decoy","action":"read"}\n'
DECOY_OPS = b'{"op_id":"DECOY","scope":"fs","action":"read","target":"/decoy"}\n'


def _plant_decoys(*directories: Path) -> None:
    for directory in directories:
        (directory / "rules.jsonl").write_bytes(DECOY_RULES)
        (directory / "ops.jsonl").write_bytes(DECOY_OPS)


def _plant_output_leftovers(output: Path) -> None:
    """Plant unrelated leftovers and result-name symlinks that must be cleared/replaced."""
    for child in list(output.iterdir()):
        if child.is_symlink() or child.is_file():
            child.unlink()
        elif child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    shadow_dir = output.parent / "_spe_shadow"
    if shadow_dir.exists():
        shutil.rmtree(shadow_dir)
    shadow_dir.mkdir()
    shadow_v = shadow_dir / "verdicts.jsonl"
    shadow_s = shadow_dir / "summary.json"
    shadow_v.write_bytes(b"STALE_VERDICTS\n")
    shadow_s.write_bytes(b'{"stale":true}\n')
    (output / "verdicts.jsonl").symlink_to(shadow_v.resolve())
    (output / "summary.json").symlink_to(shadow_s.resolve())
    (output / "prior.json").write_bytes(b"{}\n")
    leftover_dir = output / "old"
    leftover_dir.mkdir()
    (leftover_dir / "stale.txt").write_bytes(b"leftover\n")
    (output / "alias").symlink_to("prior.json")
    os.chown(shadow_dir, SAND_UID, SAND_GID, follow_symlinks=False)
    for path in shadow_dir.iterdir():
        os.chown(path, SAND_UID, SAND_GID, follow_symlinks=False)


def _execute_evaluate(
    policy: Path, book: Path, ops: Path, output: Path, home: Path, work: Path, log_dir: Path
) -> dict[str, object]:
    _assert_binary_sealed()
    stdout_path = log_dir / "stdout"
    stderr_path = log_dir / "stderr"
    env = {
        "HOME": str(home),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/local/go/bin:/usr/local/bin:/usr/bin:/bin",
        "TZ": "UTC",
    }
    command = [
        "/usr/bin/setpriv",
        "--reuid",
        str(SAND_UID),
        "--regid",
        str(SAND_GID),
        "--clear-groups",
        "--no-new-privs",
        "--",
        str(BIN),
        "--policy",
        str(policy),
        "--book",
        str(book),
        "--ops",
        str(ops),
        "--out",
        str(output),
    ]
    with stdout_path.open("wb") as stdout_file, stderr_path.open("wb") as stderr_file:
        process = subprocess.Popen(
            command,
            cwd=work,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=stdout_file,
            stderr=stderr_file,
            start_new_session=True,
            preexec_fn=_apply_limits,
        )
        try:
            returncode = process.wait(timeout=1800)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)
            returncode = -signal.SIGKILL
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            _drain_sand()

    shadow_v = output.parent / "_spe_shadow" / "verdicts.jsonl"
    shadow_s = output.parent / "_spe_shadow" / "summary.json"
    return {
        "returncode": returncode,
        "stderr": stderr_path.read_text(errors="replace"),
        "verdicts_raw": (output / "verdicts.jsonl").read_bytes()
        if (output / "verdicts.jsonl").is_file()
        else b"",
        "summary_raw": (output / "summary.json").read_bytes()
        if (output / "summary.json").is_file()
        else b"",
        "entries": sorted(path.name for path in output.iterdir()),
        "has_old": (output / "old").exists(),
        "has_prior": (output / "prior.json").exists(),
        "has_alias": (output / "alias").exists(),
        "verdicts_regular": _is_regular_file(output / "verdicts.jsonl"),
        "summary_regular": _is_regular_file(output / "summary.json"),
        "shadow_verdicts_stale": (
            shadow_v.read_bytes() == b"STALE_VERDICTS\n" if shadow_v.is_file() else True
        ),
        "shadow_summary_stale": (
            shadow_s.read_bytes() == b'{"stale":true}\n' if shadow_s.is_file() else True
        ),
    }


def _run_holdout() -> dict[str, object]:
    base = Path(tempfile.mkdtemp(prefix="spe_", dir="/tmp"))
    policy = base / "p"
    book = base / "b"
    ops = base / "o"
    output = base / "out"
    home = base / "h"
    work = base / "w"
    for path in (policy, book, ops, output, home, work):
        path.mkdir()
    _materialize(
        policy, {k[7:]: v for k, v in HOLDOUT_FILES.items() if k.startswith("policy/")}
    )
    _materialize(
        book, {k[7:]: v for k, v in HOLDOUT_FILES.items() if k.startswith("cordon/")}
    )
    _materialize(
        ops, {k[7:]: v for k, v in HOLDOUT_FILES.items() if k.startswith("probes/")}
    )
    _plant_decoys(book, work)
    _plant_output_leftovers(output)
    _chown_tree(base, SAND_UID, SAND_GID)
    try:
        return _execute_evaluate(policy, book, ops, output, home, work, base)
    finally:
        shutil.rmtree(base)


def _run_ops(rows: list[dict], rules: list[dict] | None = None) -> dict[str, object]:
    base = Path(tempfile.mkdtemp(prefix="spe_", dir="/tmp"))
    policy = base / "p"
    book = base / "b"
    ops = base / "o"
    output = base / "out"
    home = base / "h"
    work = base / "w"
    for path in (policy, book, ops, output, home, work):
        path.mkdir()
    if rules is None:
        _materialize(
            policy, {k[7:]: v for k, v in HOLDOUT_FILES.items() if k.startswith("policy/")}
        )
    else:
        text = "".join(
            json.dumps(row, separators=(",", ":"), ensure_ascii=False) + "\n" for row in rules
        )
        (policy / "rules.jsonl").write_text(text)
    manifest = "".join(
        json.dumps(row, separators=(",", ":"), ensure_ascii=False) + "\n" for row in rows
    )
    (ops / "ops.jsonl").write_text(manifest)
    _materialize(
        book, {k[7:]: v for k, v in HOLDOUT_FILES.items() if k.startswith("cordon/")}
    )
    _plant_decoys(book, work)
    _plant_output_leftovers(output)
    _chown_tree(base, SAND_UID, SAND_GID)
    try:
        return _execute_evaluate(policy, book, ops, output, home, work, base)
    finally:
        shutil.rmtree(base)


def _assert_summary_shape(summary_raw: bytes, expected: dict) -> None:
    got = json.loads(summary_raw.decode())
    _assert_native_ints(got)
    assert got == expected
    ordered = json.loads(summary_raw.decode(), object_pairs_hook=list)
    assert [k for k, _ in ordered] == ["op_count", "by_verdict", "allow_count"]
    by_verdict = dict(ordered)["by_verdict"]
    assert [k for k, _ in by_verdict] == VERDICT_ORDER
    for key, value in by_verdict:
        assert type(value) is int


def _provision_staged_pack() -> None:
    policy = APP / "policy"
    book = APP / "cordon"
    probes = APP / "probes"
    policy.mkdir(parents=True, exist_ok=True)
    book.mkdir(parents=True, exist_ok=True)
    probes.mkdir(parents=True, exist_ok=True)
    (policy / "rules.jsonl").write_bytes(
        (TESTS / "primary_pack" / "policy" / "rules.jsonl").read_bytes()
    )
    (probes / "ops.jsonl").write_bytes(
        (TESTS / "primary_pack" / "probes" / "ops.jsonl").read_bytes()
    )
    _materialize(
        book, {k[7:]: v for k, v in HOLDOUT_FILES.items() if k.startswith("cordon/")}
    )


def _run_staged_pack() -> dict[str, object]:
    output = APP / "out"
    base = Path(tempfile.mkdtemp(prefix="staged_", dir="/tmp"))
    home = base / "h"
    work = base / "w"
    home.mkdir()
    work.mkdir()
    _plant_decoys(work)
    _chown_tree(base, SAND_UID, SAND_GID)
    os.chmod(APP, 0o755)
    _provision_staged_pack()
    for root in (APP / "policy", APP / "cordon", APP / "probes"):
        os.chmod(root, 0o755)
        for path in root.rglob("*"):
            os.chmod(path, 0o755 if path.is_dir() else 0o644)
    output.mkdir(parents=True, exist_ok=True)
    os.chmod(output, 0o755)
    decoy = APP / "cordon" / "rules.jsonl"
    try:
        _plant_decoys(APP / "cordon")
        decoy.chmod(0o644)
        _plant_output_leftovers(output)
        _chown_tree(output, SAND_UID, SAND_GID)
        return _execute_evaluate(
            APP / "policy", APP / "cordon", APP / "probes", output, home, work, base
        )
    finally:
        if decoy.exists():
            decoy.unlink()
        ops_decoy = APP / "cordon" / "ops.jsonl"
        if ops_decoy.exists():
            ops_decoy.unlink()
        shutil.rmtree(base)
        os.chmod(APP, 0o700)
        if output.exists():
            _chown_tree(output, 0, 0)
            os.chmod(output, 0o700)


_STAGED_PRIMARY = _run_staged_pack()
GRADED_PRIMARY_VERDICTS_RAW = _STAGED_PRIMARY["verdicts_raw"]
GRADED_PRIMARY_SUMMARY_RAW = _STAGED_PRIMARY["summary_raw"]
GRADED_PRIMARY_ROWS = _parse_jsonl(GRADED_PRIMARY_VERDICTS_RAW)


def test_evaluate_sources_exist():
    """Verification rebuild surface /app/evaluate.go and /app/go.mod must exist."""
    assert (APP / "evaluate.go").is_file()
    assert (APP / "go.mod").is_file()


def test_primary_verdicts_match_expected():
    """Primary verdicts.jsonl must match the sealed SPE ledger row-for-row."""
    got = GRADED_PRIMARY_ROWS
    for row in got:
        _assert_native_ints(row)
    assert got == EXPECTED_PRIMARY_ROWS
    delivered = _parse_jsonl(DELIVERED_PRIMARY_VERDICTS_RAW)
    for row in delivered:
        _assert_native_ints(row)
    assert delivered == EXPECTED_PRIMARY_ROWS
    for keys in _row_key_orders(DELIVERED_PRIMARY_VERDICTS_RAW):
        assert keys == ROW_KEYS


def test_primary_summary_matches_expected():
    """Primary summary.json must match sealed verdict totals."""
    got = json.loads(GRADED_PRIMARY_SUMMARY_RAW.decode())
    _assert_native_ints(got)
    assert got == EXPECTED_PRIMARY_SUMMARY
    delivered = json.loads(DELIVERED_PRIMARY_SUMMARY_RAW.decode())
    _assert_native_ints(delivered)
    assert delivered == EXPECTED_PRIMARY_SUMMARY
    ordered = json.loads(DELIVERED_PRIMARY_SUMMARY_RAW.decode(), object_pairs_hook=list)
    assert [k for k, _ in ordered] == ["op_count", "by_verdict", "allow_count"]
    by_verdict = dict(ordered)["by_verdict"]
    assert [k for k, _ in by_verdict] == VERDICT_ORDER


def test_summary_verdict_key_order():
    """summary.json top-level keys and by_verdict keys must use the SPE order."""
    for raw in (GRADED_PRIMARY_SUMMARY_RAW, DELIVERED_PRIMARY_SUMMARY_RAW):
        ordered = json.loads(raw.decode(), object_pairs_hook=list)
        keys = [k for k, _ in ordered]
        assert keys == ["op_count", "by_verdict", "allow_count"]
        by_verdict = dict(ordered)["by_verdict"]
        assert [k for k, _ in by_verdict] == VERDICT_ORDER


def test_verdict_row_keys_sorted():
    """Each verdicts.jsonl object must serialize keys in sorted order."""
    for raw in (GRADED_PRIMARY_VERDICTS_RAW, DELIVERED_PRIMARY_VERDICTS_RAW):
        for keys in _row_key_orders(raw):
            assert keys == ROW_KEYS


def test_output_dir_only_required_files():
    """/app/out must contain only summary.json and verdicts.jsonl."""
    assert [path.name for path in DELIVERED_OUTPUT_PATHS] == [
        "summary.json",
        "verdicts.jsonl",
    ]


def test_exact_allow_beats_prefix_deny():
    """Exact allow on a path must beat a broader prefix deny (O01)."""
    rows = {r["op_id"]: r for r in GRADED_PRIMARY_ROWS}
    assert rows["O01"]["verdict"] == "allow"
    assert rows["O01"]["matched_rule"] == "F02"
    assert rows["O02"]["verdict"] == "deny"
    assert rows["O02"]["matched_rule"] == "F01"


def test_equal_specificity_deny_beats_allow():
    """At equal specificity an explicit deny must beat an allow (O10)."""
    rows = {r["op_id"]: r for r in GRADED_PRIMARY_ROWS}
    assert rows["O10"]["verdict"] == "deny"
    assert rows["O10"]["matched_rule"] == "P02"


def test_segment_glob_and_starstar():
    """Single-segment glob and star-star globs must decide write and secret paths."""
    rows = {r["op_id"]: r for r in GRADED_PRIMARY_ROWS}
    assert rows["O03"]["verdict"] == "allow"
    assert rows["O03"]["matched_rule"] == "F03"
    assert rows["O05"]["verdict"] == "deny"
    assert rows["O05"]["matched_rule"] == "F04"
    assert rows["O22"]["verdict"] == "allow"
    assert rows["O22"]["matched_rule"] == "F09"
    assert rows["O45"]["verdict"] == "allow"
    assert rows["O45"]["matched_rule"] == "F09"


def test_dns_inheritance_and_exact_net_override():
    """DNS prefix deny must yield to an exact allow on the same host:port."""
    rows = {r["op_id"]: r for r in GRADED_PRIMARY_ROWS}
    assert rows["O15"]["verdict"] == "allow"
    assert rows["O15"]["matched_rule"] == "N02"
    assert rows["O16"]["verdict"] == "deny"
    assert rows["O16"]["matched_rule"] == "N01"
    assert rows["O34"]["verdict"] == "deny"
    assert rows["O40"]["verdict"] == "deny"


def test_action_wildcard_and_net_port_deny():
    """Action wildcards and equal-rank net glob denies must bind correctly."""
    rows = {r["op_id"]: r for r in GRADED_PRIMARY_ROWS}
    assert rows["O18"]["verdict"] == "deny"
    assert rows["O18"]["matched_rule"] == "N04"
    assert rows["O19"]["verdict"] == "deny"
    assert rows["O19"]["matched_rule"] == "N07"
    assert rows["O20"]["verdict"] == "deny"
    assert rows["O20"]["matched_rule"] == "N06"


def test_path_inheritance_and_default_deny():
    """Prefix inheritance, deny_default, and malformed targets are graded."""
    rows = {r["op_id"]: r for r in GRADED_PRIMARY_ROWS}
    assert rows["O07"]["verdict"] == "allow"
    assert rows["O08"]["verdict"] == "deny"
    assert rows["O24"]["verdict"] == "deny_default"
    assert rows["O24"]["matched_rule"] == ""
    assert rows["O25"]["verdict"] == "reject_malformed"
    assert rows["O26"]["verdict"] == "reject_malformed"
    assert rows["O27"]["verdict"] == "reject_malformed"
    assert rows["O28"]["verdict"] == "reject_malformed"
    assert rows["O31"]["verdict"] == "reject_malformed"


def test_ord_tiebreak_on_same_effect():
    """Equal exact allows must keep the lowest ord rule_id (O09 -> F08)."""
    rows = {r["op_id"]: r for r in GRADED_PRIMARY_ROWS}
    assert rows["O09"]["verdict"] == "allow"
    assert rows["O09"]["matched_rule"] == "F08"


def test_invalid_rules_are_skipped():
    """Invalid rule rows must be ignored so staged ops still evaluate."""
    rows = {r["op_id"]: r for r in GRADED_PRIMARY_ROWS}
    assert rows["O01"]["verdict"] == "allow"
    assert "BAD" not in rows["O01"]["matched_rule"]
    assert rows["O46"]["verdict"] == "deny_default"
    assert rows["O46"]["matched_rule"] == ""


def test_holdout_replay_verdicts():
    """Rebuilt binary on sealed holdout probes must match holdout verdicts."""
    result = _run_holdout()
    assert result["returncode"] == 0, result["stderr"]
    got = _parse_jsonl(result["verdicts_raw"])
    for row in got:
        _assert_native_ints(row)
    assert got == EXPECTED_HOLDOUT_ROWS
    for keys in _row_key_orders(result["verdicts_raw"]):
        assert keys == ROW_KEYS


def test_holdout_replay_summary():
    """Rebuilt binary on sealed holdout probes must match holdout summary."""
    result = _run_holdout()
    assert result["returncode"] == 0, result["stderr"]
    got = json.loads(result["summary_raw"].decode())
    _assert_native_ints(got)
    assert got == EXPECTED_HOLDOUT_SUMMARY
    ordered = json.loads(result["summary_raw"].decode(), object_pairs_hook=list)
    assert [k for k, _ in ordered] == ["op_count", "by_verdict", "allow_count"]


def test_holdout_equal_rank_and_glob_cases():
    """Holdout must exercise equal-rank deny, inbox glob allow, DNS prefix, and proc target validation."""
    result = _run_holdout()
    assert result["returncode"] == 0, result["stderr"]
    rows = {r["op_id"]: r for r in _parse_jsonl(result["verdicts_raw"])}
    assert rows["H06"]["verdict"] == "deny"
    assert rows["H06"]["matched_rule"] == "H06"
    assert rows["H03"]["verdict"] == "allow"
    assert rows["H03"]["matched_rule"] == "H03"
    assert rows["H07"]["verdict"] == "allow"
    assert rows["H07"]["matched_rule"] == "H09"
    assert rows["H08"]["verdict"] == "deny"
    assert rows["H12"]["verdict"] == "deny"
    assert rows["H12"]["matched_rule"] == "H14"
    assert rows["H11"]["verdict"] == "allow"
    assert rows["H11"]["matched_rule"] == "H12"
    for op_id in ("H51", "H52", "H53", "H54", "H56"):
        assert rows[op_id]["verdict"] == "reject_malformed", op_id
        assert rows[op_id]["matched_rule"] == "", op_id
    assert rows["H55"]["verdict"] == "deny_default"
    assert rows["H57"]["verdict"] == "deny_default"


def test_zero_verdict_counts_stay_in_summary():
    """by_verdict keeps every key when a count is zero, as a JSON number."""
    allow_only = _run_ops(
        [{"op_id": "Z01", "scope": "fs", "action": "read", "target": "/data/public/readme"}]
    )
    assert allow_only["returncode"] == 0, allow_only["stderr"]
    _assert_summary_shape(
        allow_only["summary_raw"],
        {
            "op_count": 1,
            "by_verdict": {
                "allow": 1,
                "deny": 0,
                "deny_default": 0,
                "reject_malformed": 0,
            },
            "allow_count": 1,
        },
    )
    malformed_only = _run_ops(
        [{"op_id": "Z02", "scope": "fs", "action": "read", "target": "relative"}]
    )
    assert malformed_only["returncode"] == 0, malformed_only["stderr"]
    _assert_summary_shape(
        malformed_only["summary_raw"],
        {
            "op_count": 1,
            "by_verdict": {
                "allow": 0,
                "deny": 0,
                "deny_default": 0,
                "reject_malformed": 1,
            },
            "allow_count": 0,
        },
    )


def test_fresh_pack_covers_sample_and_term_edges():
    """Fresh pack covering exact-over-prefix, equal-rank deny, and deny_default (no sample IDs)."""
    result = _run_ops(
        [
            {"op_id": "F01", "scope": "fs", "action": "read", "target": "/fresh/log/app.out"},
            {"op_id": "F02", "scope": "proc", "action": "spawn", "target": "/fresh/bin/tool"},
            {"op_id": "F03", "scope": "fs", "action": "read", "target": "/no/such"},
        ],
        rules=[
            {
                "rule_id": "Pdeny",
                "ord": 1,
                "effect": "deny",
                "scope": "fs",
                "mode": "prefix",
                "selector": "/fresh/log",
                "action": "*",
            },
            {
                "rule_id": "Pallow",
                "ord": 9,
                "effect": "allow",
                "scope": "fs",
                "mode": "exact",
                "selector": "/fresh/log/app.out",
                "action": "read",
            },
            {
                "rule_id": "Qallow",
                "ord": 2,
                "effect": "allow",
                "scope": "proc",
                "mode": "exact",
                "selector": "/fresh/bin/tool",
                "action": "spawn",
            },
            {
                "rule_id": "Qdeny",
                "ord": 8,
                "effect": "deny",
                "scope": "proc",
                "mode": "exact",
                "selector": "/fresh/bin/tool",
                "action": "*",
            },
        ],
    )
    assert result["returncode"] == 0, result["stderr"]
    got = _parse_jsonl(result["verdicts_raw"])
    assert [row["op_id"] for row in got] == ["F01", "F02", "F03"]
    rows = {r["op_id"]: r for r in got}
    assert rows["F01"]["verdict"] == "allow"
    assert rows["F01"]["matched_rule"] == "Pallow"
    assert rows["F02"]["verdict"] == "deny"
    assert rows["F02"]["matched_rule"] == "Qdeny"
    assert rows["F03"]["verdict"] == "deny_default"
    assert rows["F03"]["matched_rule"] == ""


def test_lexicographic_rule_id_tiebreak():
    """Equal specificity, effect, and ord must keep the lexicographically least rule_id."""
    result = _run_ops(
        [{"op_id": "T01", "scope": "fs", "action": "read", "target": "/x"}],
        rules=[
            {
                "rule_id": "B",
                "ord": 1,
                "effect": "allow",
                "scope": "fs",
                "mode": "exact",
                "selector": "/x",
                "action": "read",
            },
            {
                "rule_id": "A",
                "ord": 1,
                "effect": "allow",
                "scope": "fs",
                "mode": "exact",
                "selector": "/x",
                "action": "read",
            },
        ],
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["verdicts_raw"])
    assert len(rows) == 1
    assert rows[0]["verdict"] == "allow"
    assert rows[0]["matched_rule"] == "A"


def test_competing_denies_lower_ord_wins():
    """Two exact denies at equal specificity: lowest ord wins even when listed second."""
    result = _run_ops(
        [{"op_id": "O", "scope": "fs", "action": "read", "target": "/x"}],
        rules=[
            {
                "rule_id": "B",
                "ord": 2,
                "effect": "deny",
                "scope": "fs",
                "mode": "exact",
                "selector": "/x",
                "action": "read",
            },
            {
                "rule_id": "A",
                "ord": 1,
                "effect": "deny",
                "scope": "fs",
                "mode": "exact",
                "selector": "/x",
                "action": "read",
            },
        ],
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["verdicts_raw"])
    assert rows[0]["verdict"] == "deny"
    assert rows[0]["matched_rule"] == "A"


def test_competing_denies_lexicographic_rule_id_tiebreak():
    """Equal-specificity equal-ord denies must keep the lexicographically least rule_id."""
    result = _run_ops(
        [{"op_id": "O", "scope": "fs", "action": "read", "target": "/x"}],
        rules=[
            {
                "rule_id": "B",
                "ord": 1,
                "effect": "deny",
                "scope": "fs",
                "mode": "exact",
                "selector": "/x",
                "action": "read",
            },
            {
                "rule_id": "A",
                "ord": 1,
                "effect": "deny",
                "scope": "fs",
                "mode": "exact",
                "selector": "/x",
                "action": "read",
            },
        ],
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["verdicts_raw"])
    assert rows[0]["verdict"] == "deny"
    assert rows[0]["matched_rule"] == "A"


def test_invalid_rule_skipped_changes_verdict():
    """An empty rule_id allow must be skipped so a matching op falls to deny_default."""
    result = _run_ops(
        [{"op_id": "T02", "scope": "fs", "action": "read", "target": "/x"}],
        rules=[
            {
                "rule_id": "",
                "ord": 1,
                "effect": "allow",
                "scope": "fs",
                "mode": "exact",
                "selector": "/x",
                "action": "read",
            }
        ],
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["verdicts_raw"])
    assert rows[0]["verdict"] == "deny_default"
    assert rows[0]["matched_rule"] == ""


def test_whitespace_and_nul_targets_reject():
    """Whitespace, NUL, empty, and unknown-scope ops must reject_malformed."""
    result = _run_ops(
        [
            {"op_id": "W01", "scope": "fs", "action": "read", "target": "/a b"},
            {"op_id": "W02", "scope": "fs", "action": "read", "target": "/x\x00y"},
            {"op_id": "W03", "scope": "fs", "action": "read", "target": ""},
            {"op_id": "W04", "scope": "weird", "action": "read", "target": "/x"},
            {"op_id": "", "scope": "fs", "action": "read", "target": "/x"},
            {"op_id": "W05", "scope": "fs", "action": "read", "target": "/ok"},
        ],
        rules=[
            {
                "rule_id": "G",
                "ord": 1,
                "effect": "allow",
                "scope": "fs",
                "mode": "glob",
                "selector": "/*",
                "action": "read",
            }
        ],
    )
    assert result["returncode"] == 0, result["stderr"]
    _assert_verdict_rows_schema(result["verdicts_raw"])
    got = _parse_jsonl(result["verdicts_raw"])
    assert [row["op_id"] for row in got] == ["", "W01", "W02", "W03", "W04", "W05"]
    rows = {r["op_id"]: r for r in got}
    assert rows["W01"]["verdict"] == "reject_malformed"
    assert rows["W01"]["matched_rule"] == ""
    assert rows["W02"]["verdict"] == "reject_malformed"
    assert rows["W03"]["verdict"] == "reject_malformed"
    assert rows["W03"]["target"] == ""
    assert rows["W04"]["verdict"] == "reject_malformed"
    assert rows[""]["verdict"] == "reject_malformed"
    assert rows["W05"]["verdict"] == "allow"
    assert rows["W05"]["matched_rule"] == "G"


def test_nbsp_in_target_rejects():
    """Non-ASCII whitespace (U+00A0) in a target must reject_malformed, not deny_default."""
    result = _run_ops(
        [{"op_id": "U", "scope": "fs", "action": "read", "target": "/a\u00a0b"}],
        rules=[],
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["verdicts_raw"])
    assert len(rows) == 1
    assert rows[0]["op_id"] == "U"
    assert rows[0]["verdict"] == "reject_malformed"
    assert rows[0]["matched_rule"] == ""
    summary = json.loads(result["summary_raw"].decode())
    assert summary["by_verdict"]["reject_malformed"] == 1
    assert summary["by_verdict"]["deny_default"] == 0


def test_oversized_jsonl_line_still_evaluates():
    """A well-formed fs target longer than 1 MiB must still produce a verdict row."""
    target = "/" + ("a" * 1_100_000)
    result = _run_ops(
        [{"op_id": "L01", "scope": "fs", "action": "read", "target": target}],
        rules=[],
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["verdicts_raw"])
    assert len(rows) == 1
    assert rows[0]["op_id"] == "L01"
    assert rows[0]["verdict"] == "deny_default"
    assert rows[0]["matched_rule"] == ""
    assert rows[0]["target"] == target


def test_net_glob_leading_slash_is_invalid():
    """A net glob selector with a leading slash has an empty segment and must be skipped."""
    result = _run_ops(
        [{"op_id": "N80", "scope": "net", "action": "connect", "target": "host:80"}],
        rules=[
            {
                "rule_id": "R",
                "ord": 1,
                "effect": "allow",
                "scope": "net",
                "mode": "glob",
                "selector": "/host:80",
                "action": "connect",
            }
        ],
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["verdicts_raw"])
    assert rows[0]["verdict"] == "deny_default"
    assert rows[0]["matched_rule"] == ""


def test_slash_only_fs_glob_is_invalid():
    """A filesystem glob selector of '/' alone must be skipped, not authorize root."""
    result = _run_ops(
        [{"op_id": "A", "scope": "fs", "action": "read", "target": "/"}],
        rules=[
            {
                "rule_id": "R",
                "ord": 1,
                "effect": "allow",
                "scope": "fs",
                "mode": "glob",
                "selector": "/",
                "action": "read",
            }
        ],
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["verdicts_raw"])
    assert rows[0]["verdict"] == "deny_default"
    assert rows[0]["matched_rule"] == ""


def test_hostname_charset_and_port_endpoints():
    """Invalid hostname characters and a leading-zero port reject; ports 1 and 65535 are legal endpoints."""
    result = _run_ops(
        [
            {"op_id": "HBAD", "scope": "net", "action": "connect", "target": "bad_host:80"},
            {"op_id": "P080", "scope": "net", "action": "connect", "target": "edge:080"},
            {"op_id": "P01", "scope": "net", "action": "connect", "target": "edge:1"},
            {"op_id": "P65", "scope": "net", "action": "connect", "target": "edge:65535"},
        ],
        rules=[
            {
                "rule_id": "R1",
                "ord": 1,
                "effect": "allow",
                "scope": "net",
                "mode": "exact",
                "selector": "bad_host:80",
                "action": "connect",
            },
            {
                "rule_id": "R0",
                "ord": 1,
                "effect": "allow",
                "scope": "net",
                "mode": "exact",
                "selector": "edge:080",
                "action": "connect",
            },
            {
                "rule_id": "R2",
                "ord": 1,
                "effect": "allow",
                "scope": "net",
                "mode": "exact",
                "selector": "edge:1",
                "action": "connect",
            },
            {
                "rule_id": "R3",
                "ord": 1,
                "effect": "allow",
                "scope": "net",
                "mode": "exact",
                "selector": "edge:65535",
                "action": "connect",
            },
        ],
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = {r["op_id"]: r for r in _parse_jsonl(result["verdicts_raw"])}
    assert rows["HBAD"]["verdict"] == "reject_malformed"
    assert rows["HBAD"]["matched_rule"] == ""
    assert rows["P080"]["verdict"] == "reject_malformed"
    assert rows["P080"]["matched_rule"] == ""
    assert rows["P01"]["verdict"] == "allow"
    assert rows["P01"]["matched_rule"] == "R2"
    assert rows["P65"]["verdict"] == "allow"
    assert rows["P65"]["matched_rule"] == "R3"


def test_unknown_mode_rule_is_skipped():
    """An unrecognized mode must not be treated as exact and must yield deny_default."""
    result = _run_ops(
        [{"op_id": "M01", "scope": "fs", "action": "read", "target": "/x"}],
        rules=[
            {
                "rule_id": "BADMODE",
                "ord": 1,
                "effect": "allow",
                "scope": "fs",
                "mode": "typo",
                "selector": "/x",
                "action": "read",
            }
        ],
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["verdicts_raw"])
    assert rows[0]["verdict"] == "deny_default"
    assert rows[0]["matched_rule"] == ""


def test_fs_prefix_requires_path_boundary():
    """Prefix /var/log must not deny /var/logger, and proc prefix /usr/bin must not allow /usr/binx."""
    result = _run_ops(
        [
            {"op_id": "PLOG", "scope": "fs", "action": "read", "target": "/var/log/app"},
            {"op_id": "PSIB", "scope": "fs", "action": "read", "target": "/var/logger"},
            {"op_id": "QBIN", "scope": "proc", "action": "spawn", "target": "/usr/bin/env"},
            {"op_id": "QSIB", "scope": "proc", "action": "spawn", "target": "/usr/binx"},
        ],
        rules=[
            {
                "rule_id": "F01",
                "ord": 1,
                "effect": "deny",
                "scope": "fs",
                "mode": "prefix",
                "selector": "/var/log",
                "action": "*",
            },
            {
                "rule_id": "Q01",
                "ord": 1,
                "effect": "allow",
                "scope": "proc",
                "mode": "prefix",
                "selector": "/usr/bin",
                "action": "spawn",
            },
        ],
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = {r["op_id"]: r for r in _parse_jsonl(result["verdicts_raw"])}
    assert rows["PLOG"]["verdict"] == "deny"
    assert rows["PLOG"]["matched_rule"] == "F01"
    assert rows["PSIB"]["verdict"] == "deny_default"
    assert rows["PSIB"]["matched_rule"] == ""
    assert rows["QBIN"]["verdict"] == "allow"
    assert rows["QBIN"]["matched_rule"] == "Q01"
    assert rows["QSIB"]["verdict"] == "deny_default"
    assert rows["QSIB"]["matched_rule"] == ""


def test_root_prefix_does_not_match_every_absolute_path():
    """Prefix selector '/' matches only '/' or paths under '//', not every absolute path."""
    result = _run_ops(
        [{"op_id": "O", "scope": "fs", "action": "read", "target": "/x"}],
        rules=[
            {
                "rule_id": "R",
                "ord": 1,
                "effect": "allow",
                "scope": "fs",
                "mode": "prefix",
                "selector": "/",
                "action": "read",
            }
        ],
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["verdicts_raw"])
    assert rows[0]["verdict"] == "deny_default"
    assert rows[0]["matched_rule"] == ""


def test_net_prefix_with_colon_is_exact_equality():
    """A net prefix selector that contains a colon must equal the full target."""
    result = _run_ops(
        [
            {"op_id": "E443", "scope": "net", "action": "connect", "target": "edge:443"},
            {"op_id": "E4439", "scope": "net", "action": "connect", "target": "edge:4439"},
        ],
        rules=[
            {
                "rule_id": "H17",
                "ord": 1,
                "effect": "allow",
                "scope": "net",
                "mode": "prefix",
                "selector": "edge:443",
                "action": "connect",
            }
        ],
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = {r["op_id"]: r for r in _parse_jsonl(result["verdicts_raw"])}
    assert rows["E443"]["verdict"] == "allow"
    assert rows["E443"]["matched_rule"] == "H17"
    assert rows["E4439"]["verdict"] == "deny_default"
    assert rows["E4439"]["matched_rule"] == ""


def test_dns_prefix_requires_dot_boundary():
    """Net prefix without a colon must use DNS inheritance, not raw hostname suffix."""
    result = _run_ops(
        [{"op_id": "O", "scope": "net", "action": "connect", "target": "notcorp.lan:80"}],
        rules=[
            {
                "rule_id": "R",
                "ord": 1,
                "effect": "allow",
                "scope": "net",
                "mode": "prefix",
                "selector": "corp.lan",
                "action": "connect",
            }
        ],
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["verdicts_raw"])
    assert rows[0]["verdict"] == "deny_default"
    assert rows[0]["matched_rule"] == ""


def test_more_than_fifty_ops_are_all_processed():
    """Every-row processing must cover more than 100 ops (fixed 100-cap readers fail)."""
    ops = [
        {"op_id": "R%03d" % i, "scope": "fs", "action": "read", "target": "/x"}
        for i in range(101)
    ]
    result = _run_ops(
        ops,
        rules=[
            {
                "rule_id": "ALLOWX",
                "ord": 1,
                "effect": "allow",
                "scope": "fs",
                "mode": "exact",
                "selector": "/x",
                "action": "read",
            }
        ],
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["verdicts_raw"])
    assert len(rows) == 101
    assert [row["op_id"] for row in rows] == sorted(row["op_id"] for row in rows)
    assert all(row["verdict"] == "allow" and row["matched_rule"] == "ALLOWX" for row in rows)
    summary = json.loads(result["summary_raw"].decode())
    assert summary["op_count"] == 101
    assert summary["allow_count"] == 101


def test_unknown_effect_that_would_match_is_skipped():
    """A rule with effect permit that would otherwise match must be skipped."""
    result = _run_ops(
        [{"op_id": "E", "scope": "fs", "action": "read", "target": "/x"}],
        rules=[
            {
                "rule_id": "R",
                "ord": 1,
                "effect": "permit",
                "scope": "fs",
                "mode": "exact",
                "selector": "/x",
                "action": "read",
            }
        ],
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["verdicts_raw"])
    assert rows[0]["verdict"] == "deny_default"
    assert rows[0]["matched_rule"] == ""


def test_empty_selector_prefix_is_skipped():
    """An empty prefix selector must not authorize paths via selector+'/' becoming '/'."""
    result = _run_ops(
        [{"op_id": "E", "scope": "fs", "action": "read", "target": "/x"}],
        rules=[
            {
                "rule_id": "R",
                "ord": 1,
                "effect": "allow",
                "scope": "fs",
                "mode": "prefix",
                "selector": "",
                "action": "read",
            }
        ],
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["verdicts_raw"])
    assert rows[0]["verdict"] == "deny_default"
    assert rows[0]["matched_rule"] == ""


def test_uppercase_hostname_is_allowed():
    """Hostname letters include uppercase; ABC:80 must not reject_malformed."""
    result = _run_ops(
        [{"op_id": "U", "scope": "net", "action": "connect", "target": "ABC:80"}],
        rules=[
            {
                "rule_id": "R",
                "ord": 1,
                "effect": "allow",
                "scope": "net",
                "mode": "exact",
                "selector": "ABC:80",
                "action": "connect",
            }
        ],
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["verdicts_raw"])
    assert rows[0]["verdict"] == "allow"
    assert rows[0]["matched_rule"] == "R"
    assert rows[0]["target"] == "ABC:80"


def test_empty_host_network_target_rejects():
    """A network target with empty host (:80) must reject_malformed before matching."""
    result = _run_ops(
        [{"op_id": "H", "scope": "net", "action": "connect", "target": ":80"}],
        rules=[
            {
                "rule_id": "R",
                "ord": 1,
                "effect": "allow",
                "scope": "net",
                "mode": "exact",
                "selector": ":80",
                "action": "connect",
            }
        ],
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["verdicts_raw"])
    assert rows[0]["verdict"] == "reject_malformed"
    assert rows[0]["matched_rule"] == ""
    assert rows[0]["target"] == ":80"


def test_case_variant_target_key_does_not_overwrite():
    """Extra Target must not overwrite the contract target field under case folding."""
    result = _run_ops(
        [
            {
                "op_id": "O",
                "scope": "fs",
                "action": "read",
                "target": "/x",
                "Target": "/y",
            }
        ],
        rules=[
            {
                "rule_id": "R",
                "ord": 1,
                "effect": "allow",
                "scope": "fs",
                "mode": "exact",
                "selector": "/x",
                "action": "read",
            }
        ],
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["verdicts_raw"])
    assert rows[0]["verdict"] == "allow"
    assert rows[0]["matched_rule"] == "R"
    assert rows[0]["target"] == "/x"


def test_case_variant_selector_key_does_not_overwrite():
    """Extra Selector on a rule must not overwrite the contract selector under case folding."""
    result = _run_ops(
        [{"op_id": "O", "scope": "fs", "action": "read", "target": "/x"}],
        rules=[
            {
                "rule_id": "R",
                "ord": 1,
                "effect": "allow",
                "scope": "fs",
                "mode": "exact",
                "selector": "/x",
                "Selector": "/y",
                "action": "read",
            }
        ],
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["verdicts_raw"])
    assert rows[0]["verdict"] == "allow"
    assert rows[0]["matched_rule"] == "R"
    assert rows[0]["target"] == "/x"


def test_missing_ord_rule_is_skipped():
    """A rule missing ord is schema-invalid and must not match."""
    result = _run_ops(
        [{"op_id": "O", "scope": "fs", "action": "read", "target": "/x"}],
        rules=[
            {
                "rule_id": "R",
                "effect": "allow",
                "scope": "fs",
                "mode": "exact",
                "selector": "/x",
                "action": "read",
            }
        ],
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["verdicts_raw"])
    assert rows[0]["verdict"] == "deny_default"
    assert rows[0]["matched_rule"] == ""


def test_large_integer_ord_is_accepted():
    """A beyond-int64 ordinal must lose to a smaller matching ordinal, not wrap below it."""
    result = _run_ops(
        [{"op_id": "O", "scope": "fs", "action": "read", "target": "/x"}],
        rules=[
            {
                "rule_id": "R",
                "ord": 9223372036854775808,
                "effect": "allow",
                "scope": "fs",
                "mode": "exact",
                "selector": "/x",
                "action": "read",
            },
            {
                "rule_id": "S",
                "ord": 1,
                "effect": "allow",
                "scope": "fs",
                "mode": "exact",
                "selector": "/x",
                "action": "read",
            },
        ],
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["verdicts_raw"])
    assert rows[0]["verdict"] == "allow"
    assert rows[0]["matched_rule"] == "S"


def test_sole_beyond_int64_ord_rule_matches():
    """A sole matching rule with ord beyond int64 must still be accepted and allow."""
    result = _run_ops(
        [{"op_id": "O", "scope": "fs", "action": "read", "target": "/x"}],
        rules=[
            {
                "rule_id": "R",
                "ord": 9223372036854775808,
                "effect": "allow",
                "scope": "fs",
                "mode": "exact",
                "selector": "/x",
                "action": "read",
            }
        ],
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["verdicts_raw"])
    assert rows[0]["verdict"] == "allow"
    assert rows[0]["matched_rule"] == "R"


def test_sole_beyond_uint64_ord_rule_matches():
    """A sole matching rule with ord beyond uint64 (2^64) must still be accepted and allow."""
    result = _run_ops(
        [{"op_id": "O", "scope": "fs", "action": "read", "target": "/x"}],
        rules=[
            {
                "rule_id": "R",
                "ord": 18446744073709551616,
                "effect": "allow",
                "scope": "fs",
                "mode": "exact",
                "selector": "/x",
                "action": "read",
            }
        ],
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["verdicts_raw"])
    assert rows[0]["verdict"] == "allow"
    assert rows[0]["matched_rule"] == "R"
    summary = json.loads(result["summary_raw"].decode())
    assert summary["allow_count"] == 1


def test_competing_beyond_uint64_ords_keep_order():
    """Competing ordinals past uint64 must not saturate; the lower magnitude wins."""
    result = _run_ops(
        [{"op_id": "O", "scope": "fs", "action": "read", "target": "/x"}],
        rules=[
            {
                "rule_id": "A",
                "ord": 18446744073709551617,
                "effect": "allow",
                "scope": "fs",
                "mode": "exact",
                "selector": "/x",
                "action": "read",
            },
            {
                "rule_id": "B",
                "ord": 18446744073709551616,
                "effect": "allow",
                "scope": "fs",
                "mode": "exact",
                "selector": "/x",
                "action": "read",
            },
        ],
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["verdicts_raw"])
    assert rows[0]["verdict"] == "allow"
    assert rows[0]["matched_rule"] == "B"


def test_null_ord_rule_is_skipped():
    """A present JSON null ordinal is invalid and must not match."""
    result = _run_ops(
        [{"op_id": "O", "scope": "fs", "action": "read", "target": "/x"}],
        rules=[
            {
                "rule_id": "R",
                "ord": None,
                "effect": "allow",
                "scope": "fs",
                "mode": "exact",
                "selector": "/x",
                "action": "read",
            }
        ],
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["verdicts_raw"])
    assert rows[0]["verdict"] == "deny_default"
    assert rows[0]["matched_rule"] == ""


def test_interior_char_wildcard_glob_matches():
    """A character * in the interior of a glob segment must match (fo*ar -> foobar)."""
    result = _run_ops(
        [{"op_id": "I", "scope": "fs", "action": "read", "target": "/foobar"}],
        rules=[
            {
                "rule_id": "R",
                "ord": 1,
                "effect": "allow",
                "scope": "fs",
                "mode": "glob",
                "selector": "/fo*ar",
                "action": "read",
            }
        ],
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["verdicts_raw"])
    assert rows[0]["verdict"] == "allow"
    assert rows[0]["matched_rule"] == "R"


def test_two_char_wildcards_in_one_glob_segment():
    """An ordinary glob segment may contain multiple *; /a*b* must match /ab."""
    result = _run_ops(
        [{"op_id": "M", "scope": "fs", "action": "read", "target": "/ab"}],
        rules=[
            {
                "rule_id": "R",
                "ord": 1,
                "effect": "allow",
                "scope": "fs",
                "mode": "glob",
                "selector": "/a*b*",
                "action": "read",
            }
        ],
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["verdicts_raw"])
    assert rows[0]["verdict"] == "allow"
    assert rows[0]["matched_rule"] == "R"


def test_hyphenated_hostname_is_allowed():
    """Hostname characters include hyphens; a-b:80 must not reject_malformed."""
    result = _run_ops(
        [{"op_id": "H", "scope": "net", "action": "connect", "target": "a-b:80"}],
        rules=[
            {
                "rule_id": "R",
                "ord": 1,
                "effect": "allow",
                "scope": "net",
                "mode": "exact",
                "selector": "a-b:80",
                "action": "connect",
            }
        ],
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["verdicts_raw"])
    assert rows[0]["verdict"] == "allow"
    assert rows[0]["matched_rule"] == "R"
    assert rows[0]["target"] == "a-b:80"


def test_leading_hyphen_hostname_is_allowed():
    """Ledger hostname grammar allows a leading hyphen; it is not conventional DNS-label rules."""
    result = _run_ops(
        [{"op_id": "H", "scope": "net", "action": "connect", "target": "-edge:80"}],
        rules=[
            {
                "rule_id": "R",
                "ord": 1,
                "effect": "allow",
                "scope": "net",
                "mode": "exact",
                "selector": "-edge:80",
                "action": "connect",
            }
        ],
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["verdicts_raw"])
    assert rows[0]["verdict"] == "allow"
    assert rows[0]["matched_rule"] == "R"
    assert rows[0]["target"] == "-edge:80"


def test_trailing_hyphen_hostname_is_allowed():
    """Ledger hostname grammar allows a trailing hyphen on a label."""
    result = _run_ops(
        [{"op_id": "H", "scope": "net", "action": "connect", "target": "edge-:80"}],
        rules=[
            {
                "rule_id": "R",
                "ord": 1,
                "effect": "allow",
                "scope": "net",
                "mode": "exact",
                "selector": "edge-:80",
                "action": "connect",
            }
        ],
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["verdicts_raw"])
    assert rows[0]["verdict"] == "allow"
    assert rows[0]["matched_rule"] == "R"


def test_consecutive_dots_hostname_is_allowed():
    """Ledger hostname charset allows consecutive dots; empty DNS labels are not rejected."""
    result = _run_ops(
        [{"op_id": "E", "scope": "net", "action": "connect", "target": "a..b:80"}],
        rules=[
            {
                "rule_id": "R",
                "ord": 1,
                "effect": "allow",
                "scope": "net",
                "mode": "exact",
                "selector": "a..b:80",
                "action": "connect",
            }
        ],
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["verdicts_raw"])
    assert rows[0]["verdict"] == "allow"
    assert rows[0]["matched_rule"] == "R"
    assert rows[0]["target"] == "a..b:80"


def test_negative_ord_wins_tiebreak():
    """Negative ordinals are valid integers; the lowest ordinal wins among equal matches."""
    result = _run_ops(
        [{"op_id": "T", "scope": "fs", "action": "read", "target": "/x"}],
        rules=[
            {
                "rule_id": "B",
                "ord": -1,
                "effect": "allow",
                "scope": "fs",
                "mode": "exact",
                "selector": "/x",
                "action": "read",
            },
            {
                "rule_id": "A",
                "ord": 0,
                "effect": "allow",
                "scope": "fs",
                "mode": "exact",
                "selector": "/x",
                "action": "read",
            },
        ],
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["verdicts_raw"])
    assert rows[0]["verdict"] == "allow"
    assert rows[0]["matched_rule"] == "B"


def test_beyond_int64_negative_ord_wins_tiebreak():
    """A matching rule with ord below int64 min must beat a smaller-magnitude negative ord."""
    result = _run_ops(
        [{"op_id": "O", "scope": "fs", "action": "read", "target": "/x"}],
        rules=[
            {
                "rule_id": "A",
                "ord": -9223372036854775809,
                "effect": "allow",
                "scope": "fs",
                "mode": "exact",
                "selector": "/x",
                "action": "read",
            },
            {
                "rule_id": "B",
                "ord": -1,
                "effect": "allow",
                "scope": "fs",
                "mode": "exact",
                "selector": "/x",
                "action": "read",
            },
        ],
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["verdicts_raw"])
    assert rows[0]["verdict"] == "allow"
    assert rows[0]["matched_rule"] == "A"


def test_float64_adjacent_ords_keep_integer_order():
    """Adjacent integers past float64 mantissa must not collapse; lower ord wins."""
    result = _run_ops(
        [{"op_id": "O", "scope": "fs", "action": "read", "target": "/x"}],
        rules=[
            {
                "rule_id": "B",
                "ord": 9007199254740992,
                "effect": "allow",
                "scope": "fs",
                "mode": "exact",
                "selector": "/x",
                "action": "read",
            },
            {
                "rule_id": "A",
                "ord": 9007199254740993,
                "effect": "allow",
                "scope": "fs",
                "mode": "exact",
                "selector": "/x",
                "action": "read",
            },
        ],
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["verdicts_raw"])
    assert rows[0]["verdict"] == "allow"
    assert rows[0]["matched_rule"] == "B"


def test_fractional_ord_rule_is_skipped():
    """A non-integer ordinal is schema-invalid and must not match."""
    result = _run_ops(
        [{"op_id": "O", "scope": "fs", "action": "read", "target": "/x"}],
        rules=[
            {
                "rule_id": "R",
                "ord": 1.5,
                "effect": "allow",
                "scope": "fs",
                "mode": "exact",
                "selector": "/x",
                "action": "read",
            }
        ],
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["verdicts_raw"])
    assert rows[0]["verdict"] == "deny_default"
    assert rows[0]["matched_rule"] == ""


def test_integral_looking_decimal_ord_is_skipped():
    """ord must be a JSON integer token; 1.0 is invalid even though it is mathematically integral."""
    result = _run_ops(
        [{"op_id": "O", "scope": "fs", "action": "read", "target": "/x"}],
        rules=[
            {
                "rule_id": "R",
                "ord": 1.0,
                "effect": "allow",
                "scope": "fs",
                "mode": "exact",
                "selector": "/x",
                "action": "read",
            }
        ],
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["verdicts_raw"])
    assert rows[0]["verdict"] == "deny_default"
    assert rows[0]["matched_rule"] == ""


def test_plus_prefixed_port_rejects():
    """Port must be a decimal digit string; host:+1 is reject_malformed."""
    result = _run_ops(
        [{"op_id": "P", "scope": "net", "action": "connect", "target": "host:+1"}],
        rules=[
            {
                "rule_id": "R",
                "ord": 1,
                "effect": "allow",
                "scope": "net",
                "mode": "exact",
                "selector": "host:+1",
                "action": "connect",
            }
        ],
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["verdicts_raw"])
    assert rows[0]["verdict"] == "reject_malformed"
    assert rows[0]["matched_rule"] == ""
    assert rows[0]["target"] == "host:+1"


def test_more_than_one_hundred_rules_are_all_loaded():
    """Every rule row must be loaded; a fixed 100-cap reader misses the final allow."""
    rules = [
        {
            "rule_id": "N%03d" % i,
            "ord": i + 1,
            "effect": "allow",
            "scope": "fs",
            "mode": "exact",
            "selector": "/nomatch/%d" % i,
            "action": "read",
        }
        for i in range(100)
    ]
    rules.append(
        {
            "rule_id": "WIN",
            "ord": 101,
            "effect": "allow",
            "scope": "fs",
            "mode": "exact",
            "selector": "/x",
            "action": "read",
        }
    )
    result = _run_ops(
        [{"op_id": "O", "scope": "fs", "action": "read", "target": "/x"}],
        rules=rules,
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["verdicts_raw"])
    assert rows[0]["verdict"] == "allow"
    assert rows[0]["matched_rule"] == "WIN"


def test_duplicate_op_id_rows_each_get_a_verdict():
    """Duplicate op_id values still require one verdict row per input op."""
    result = _run_ops(
        [
            {"op_id": "D", "scope": "fs", "action": "read", "target": "/x"},
            {"op_id": "D", "scope": "fs", "action": "write", "target": "/y"},
        ],
        rules=[],
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["verdicts_raw"])
    assert len(rows) == 2
    assert all(row["op_id"] == "D" for row in rows)
    assert {row["target"] for row in rows} == {"/x", "/y"}
    assert all(row["verdict"] == "deny_default" for row in rows)
    summary = json.loads(result["summary_raw"].decode())
    assert summary["op_count"] == 2
    assert summary["by_verdict"]["deny_default"] == 2


def test_malformed_actions_per_scope():
    """Actions illegal for proc and net must reject_malformed, not fall through."""
    result = _run_ops(
        [
            {"op_id": "AP", "scope": "proc", "action": "read", "target": "/bin/x"},
            {"op_id": "AN", "scope": "net", "action": "read", "target": "h:80"},
            {"op_id": "AF", "scope": "fs", "action": "spawn", "target": "/bin/x"},
        ],
        rules=[],
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = {r["op_id"]: r for r in _parse_jsonl(result["verdicts_raw"])}
    assert rows["AP"]["verdict"] == "reject_malformed"
    assert rows["AN"]["verdict"] == "reject_malformed"
    assert rows["AF"]["verdict"] == "reject_malformed"


def test_numeric_rule_id_is_skipped_not_fatal():
    """A non-string rule_id must be skipped so evaluation still yields deny_default."""
    result = _run_ops(
        [{"op_id": "O", "scope": "fs", "action": "read", "target": "/x"}],
        rules=[
            {
                "rule_id": 7,
                "ord": 1,
                "effect": "allow",
                "scope": "fs",
                "mode": "exact",
                "selector": "/x",
                "action": "read",
            }
        ],
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["verdicts_raw"])
    assert len(rows) == 1
    assert rows[0]["verdict"] == "deny_default"
    assert rows[0]["matched_rule"] == ""


def test_quoted_ord_string_is_skipped():
    """A quoted numeric ord is not an integer field and must not match."""
    result = _run_ops(
        [{"op_id": "O", "scope": "fs", "action": "read", "target": "/a"}],
        rules=[
            {
                "rule_id": "R",
                "ord": "0",
                "effect": "allow",
                "scope": "fs",
                "mode": "exact",
                "selector": "/a",
                "action": "read",
            }
        ],
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["verdicts_raw"])
    assert rows[0]["verdict"] == "deny_default"
    assert rows[0]["matched_rule"] == ""


def test_non_string_op_action_still_gets_verdict():
    """A JSON object op with a non-string action must still emit reject_malformed."""
    result = _run_ops(
        [{"op_id": "M1", "scope": "fs", "action": 7, "target": "/tmp/x"}],
        rules=[],
    )
    assert result["returncode"] == 0, result["stderr"]
    _assert_verdict_rows_schema(result["verdicts_raw"])
    rows = _parse_jsonl(result["verdicts_raw"])
    assert len(rows) == 1
    assert rows[0]["op_id"] == "M1"
    assert rows[0]["verdict"] == "reject_malformed"
    assert rows[0]["matched_rule"] == ""
    assert rows[0]["action"] == ""
    assert rows[0]["target"] == "/tmp/x"
    summary = json.loads(result["summary_raw"].decode())
    assert summary["op_count"] == 1
    assert summary["by_verdict"]["reject_malformed"] == 1
    assert result["entries"] == ["summary.json", "verdicts.jsonl"]
    assert result["has_old"] is False
    assert result["has_prior"] is False
    assert result["has_alias"] is False


def test_net_bind_exact_allow_matches():
    """A net bind op must be matchable; bind must not be hard-coded to deny_default."""
    result = _run_ops(
        [{"op_id": "B", "scope": "net", "action": "bind", "target": "h:80"}],
        rules=[
            {
                "rule_id": "R",
                "ord": 1,
                "effect": "allow",
                "scope": "net",
                "mode": "exact",
                "selector": "h:80",
                "action": "bind",
            }
        ],
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["verdicts_raw"])
    assert rows[0]["verdict"] == "allow"
    assert rows[0]["matched_rule"] == "R"


def test_non_string_rule_string_fields_are_skipped():
    """Non-string effect/scope/mode/selector/action must skip the rule, not abort."""
    base = {
        "rule_id": "R",
        "ord": 1,
        "effect": "allow",
        "scope": "fs",
        "mode": "exact",
        "selector": "/x",
        "action": "read",
    }
    bad_fields = {
        "effect": True,
        "scope": 1,
        "mode": False,
        "selector": 9,
        "action": ["read"],
    }
    for field, value in bad_fields.items():
        rule = dict(base)
        rule[field] = value
        result = _run_ops(
            [{"op_id": "X", "scope": "fs", "action": "read", "target": "/x"}],
            rules=[rule],
        )
        assert result["returncode"] == 0, (field, result["stderr"])
        rows = _parse_jsonl(result["verdicts_raw"])
        assert len(rows) == 1, field
        assert rows[0]["verdict"] == "deny_default", field
        assert rows[0]["matched_rule"] == "", field


def test_non_string_op_scope_target_opid_still_get_verdict():
    """Non-string scope, target, and op_id must each still yield reject_malformed."""
    result = _run_ops(
        [
            {"op_id": "X", "scope": 7, "action": "read", "target": "/x"},
            {"op_id": "Y", "scope": "fs", "action": "read", "target": 3},
            {"op_id": 5, "scope": "fs", "action": "read", "target": "/x"},
        ],
        rules=[],
    )
    assert result["returncode"] == 0, result["stderr"]
    _assert_verdict_rows_schema(result["verdicts_raw"])
    rows = _parse_jsonl(result["verdicts_raw"])
    assert len(rows) == 3
    assert all(row["verdict"] == "reject_malformed" for row in rows)
    assert all(row["matched_rule"] == "" for row in rows)
    by_id = {row["op_id"]: row for row in rows}
    assert by_id["X"]["scope"] == ""
    assert by_id["X"]["target"] == "/x"
    assert by_id["Y"]["target"] == ""
    assert by_id[""]["op_id"] == ""
    assert by_id[""]["target"] == "/x"
    summary = json.loads(result["summary_raw"].decode())
    assert summary["op_count"] == 3
    assert summary["by_verdict"]["reject_malformed"] == 3
    assert result["entries"] == ["summary.json", "verdicts.jsonl"]
    assert result["has_old"] is False
    assert result["has_prior"] is False
    assert result["has_alias"] is False


def test_proc_relative_target_rejects():
    """Proc targets must be absolute; relative must reject_malformed even if a rule matches."""
    result = _run_ops(
        [{"op_id": "P", "scope": "proc", "action": "spawn", "target": "relative"}],
        rules=[
            {
                "rule_id": "R",
                "ord": 1,
                "effect": "allow",
                "scope": "proc",
                "mode": "exact",
                "selector": "relative",
                "action": "spawn",
            }
        ],
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["verdicts_raw"])
    assert rows[0]["verdict"] == "reject_malformed"
    assert rows[0]["matched_rule"] == ""


def test_proc_trailing_slash_target_rejects():
    """Absolute proc targets ending with '/' must reject_malformed, not match as paths."""
    result = _run_ops(
        [{"op_id": "P", "scope": "proc", "action": "spawn", "target": "/bin/"}],
        rules=[
            {
                "rule_id": "R",
                "ord": 1,
                "effect": "allow",
                "scope": "proc",
                "mode": "exact",
                "selector": "/bin/",
                "action": "spawn",
            }
        ],
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["verdicts_raw"])
    assert rows[0]["verdict"] == "reject_malformed"
    assert rows[0]["matched_rule"] == ""


def test_proc_double_slash_target_rejects():
    """Proc targets with '//' must reject_malformed even when an exact allow rule matches."""
    result = _run_ops(
        [{"op_id": "P", "scope": "proc", "action": "spawn", "target": "/bin//x"}],
        rules=[
            {
                "rule_id": "R",
                "ord": 1,
                "effect": "allow",
                "scope": "proc",
                "mode": "exact",
                "selector": "/bin//x",
                "action": "spawn",
            }
        ],
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["verdicts_raw"])
    assert rows[0]["verdict"] == "reject_malformed"
    assert rows[0]["matched_rule"] == ""


def test_proc_whitespace_and_nul_targets_reject():
    """Proc targets with a space, NUL, or NBSP must reject_malformed even under a matching prefix allow."""
    result = _run_ops(
        [
            {"op_id": "PS", "scope": "proc", "action": "spawn", "target": "/bin/a b"},
            {"op_id": "PN", "scope": "proc", "action": "spawn", "target": "/bin/x\x00y"},
            {"op_id": "PU", "scope": "proc", "action": "spawn", "target": "/bin/a\u00a0b"},
            {"op_id": "PV", "scope": "proc", "action": "spawn", "target": "/bin/ok"},
        ],
        rules=[
            {
                "rule_id": "R",
                "ord": 1,
                "effect": "allow",
                "scope": "proc",
                "mode": "prefix",
                "selector": "/bin",
                "action": "spawn",
            }
        ],
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = {r["op_id"]: r for r in _parse_jsonl(result["verdicts_raw"])}
    for op_id in ("PS", "PN", "PU"):
        assert rows[op_id]["verdict"] == "reject_malformed", op_id
        assert rows[op_id]["matched_rule"] == "", op_id
    assert rows["PV"]["verdict"] == "allow"
    assert rows["PV"]["matched_rule"] == "R"


def test_proc_root_target_is_allowed():
    """Ledger allows proc target '/'; it must not be rejected as a trailing-slash path."""
    result = _run_ops(
        [{"op_id": "P0", "scope": "proc", "action": "spawn", "target": "/"}],
        rules=[
            {
                "rule_id": "R",
                "ord": 1,
                "effect": "allow",
                "scope": "proc",
                "mode": "exact",
                "selector": "/",
                "action": "spawn",
            }
        ],
    )
    assert result["returncode"] == 0, result["stderr"]
    _assert_verdict_rows_schema(result["verdicts_raw"])
    rows = _parse_jsonl(result["verdicts_raw"])
    assert rows[0]["verdict"] == "allow"
    assert rows[0]["matched_rule"] == "R"
    assert rows[0]["target"] == "/"
    assert rows[0]["scope"] == "proc"


def _run_ops_custom_book(
    rows: list[dict],
    rules: list[dict],
    book_files: dict[str, bytes],
) -> dict[str, object]:
    """Run evaluate with an explicitly supplied --book tree (may omit ledger/samples).

    Uses the same /tmp/spe_* layout as ordinary _run_ops so cwd parent names do not
    reveal incomplete-book rejection scenarios.
    """
    base = Path(tempfile.mkdtemp(prefix="spe_", dir="/tmp"))
    policy = base / "p"
    book = base / "b"
    ops = base / "o"
    output = base / "out"
    home = base / "h"
    work = base / "w"
    for path in (policy, book, ops, output, home, work):
        path.mkdir()
    (policy / "rules.jsonl").write_text(
        "".join(json.dumps(row, separators=(",", ":"), ensure_ascii=False) + "\n" for row in rules)
    )
    (ops / "ops.jsonl").write_text(
        "".join(json.dumps(row, separators=(",", ":"), ensure_ascii=False) + "\n" for row in rows)
    )
    _materialize(book, book_files)
    _plant_decoys(work)
    _plant_output_leftovers(output)
    _chown_tree(base, SAND_UID, SAND_GID)
    try:
        return _execute_evaluate(policy, book, ops, output, home, work, base)
    finally:
        shutil.rmtree(base)


def _assert_no_result_artifacts(result: dict[str, object]) -> None:
    """Incomplete-book failures must clear leftovers and leave --out without verdicts/summary."""
    assert result["entries"] == [], result["entries"]
    assert "verdicts.jsonl" not in result["entries"], result["entries"]
    assert "summary.json" not in result["entries"], result["entries"]
    assert result["verdicts_regular"] is False
    assert result["summary_regular"] is False
    assert result["verdicts_raw"] == b""
    assert result["summary_raw"] == b""
    assert result["has_old"] is False
    assert result["has_prior"] is False
    assert result["has_alias"] is False


def test_book_requires_ledger_and_samples():
    """Ledger requires --book SPE_LEDGER.md file and samples/ dir; incomplete books exit nonzero."""
    ops = [{"op_id": "O", "scope": "fs", "action": "read", "target": "/x"}]
    rules = [
        {
            "rule_id": "R",
            "ord": 1,
            "effect": "allow",
            "scope": "fs",
            "mode": "exact",
            "selector": "/x",
            "action": "read",
        }
    ]
    missing_ledger = _run_ops_custom_book(
        ops,
        rules,
        {"samples/NOTE.md": b"note\n"},
    )
    assert missing_ledger["returncode"] != 0, missing_ledger["stderr"]
    _assert_no_result_artifacts(missing_ledger)
    missing_samples = _run_ops_custom_book(
        ops,
        rules,
        {"SPE_LEDGER.md": b"ledger\n"},
    )
    assert missing_samples["returncode"] != 0, missing_samples["stderr"]
    _assert_no_result_artifacts(missing_samples)
    complete = _run_ops_custom_book(
        ops,
        rules,
        {"SPE_LEDGER.md": b"ledger\n", "samples/NOTE.md": b"note\n"},
    )
    assert complete["returncode"] == 0, complete["stderr"]
    rows = _parse_jsonl(complete["verdicts_raw"])
    assert rows[0]["verdict"] == "allow"
    assert rows[0]["matched_rule"] == "R"
    assert complete["entries"] == ["summary.json", "verdicts.jsonl"]
    assert complete["verdicts_regular"] is True
    assert complete["summary_regular"] is True
    assert complete["shadow_verdicts_stale"] is True
    assert complete["shadow_summary_stale"] is True
    assert complete["has_old"] is False
    assert complete["has_prior"] is False
    assert complete["has_alias"] is False


def test_book_rejects_symlink_ledger():
    """SPE_LEDGER.md must be a regular file; a symlink to a valid ledger is rejected with no outputs."""
    ops = [{"op_id": "O", "scope": "fs", "action": "read", "target": "/x"}]
    rules = [
        {
            "rule_id": "R",
            "ord": 1,
            "effect": "allow",
            "scope": "fs",
            "mode": "exact",
            "selector": "/x",
            "action": "read",
        }
    ]
    base = Path(tempfile.mkdtemp(prefix="spe_", dir="/tmp"))
    policy = base / "p"
    book = base / "b"
    ops_dir = base / "o"
    output = base / "out"
    home = base / "h"
    work = base / "w"
    for path in (policy, book, ops_dir, output, home, work):
        path.mkdir()
    (policy / "rules.jsonl").write_text(
        "".join(json.dumps(row, separators=(",", ":"), ensure_ascii=False) + "\n" for row in rules)
    )
    (ops_dir / "ops.jsonl").write_text(
        "".join(json.dumps(row, separators=(",", ":"), ensure_ascii=False) + "\n" for row in ops)
    )
    real_ledger = base / "real_SPE_LEDGER.md"
    real_ledger.write_bytes(b"ledger\n")
    (book / "SPE_LEDGER.md").symlink_to(real_ledger)
    (book / "samples").mkdir()
    (book / "samples" / "NOTE.md").write_bytes(b"note\n")
    _plant_decoys(work)
    _plant_output_leftovers(output)
    _chown_tree(base, SAND_UID, SAND_GID)
    try:
        result = _execute_evaluate(policy, book, ops_dir, output, home, work, base)
    finally:
        shutil.rmtree(base)
    assert result["returncode"] != 0, result["stderr"]
    _assert_no_result_artifacts(result)


def test_port_65536_rejects():
    """Port 65536 is the first invalid upper bound and must reject_malformed."""
    result = _run_ops(
        [{"op_id": "E", "scope": "net", "action": "connect", "target": "edge:65536"}],
        rules=[
            {
                "rule_id": "R",
                "ord": 1,
                "effect": "allow",
                "scope": "net",
                "mode": "exact",
                "selector": "edge:65536",
                "action": "connect",
            }
        ],
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["verdicts_raw"])
    assert rows[0]["verdict"] == "reject_malformed"
    assert rows[0]["matched_rule"] == ""


def test_extra_colon_after_port_rejects():
    """A network target with trailing colon components (host:1:2) must reject_malformed."""
    result = _run_ops(
        [{"op_id": "E", "scope": "net", "action": "connect", "target": "host:1:2"}],
        rules=[
            {
                "rule_id": "R",
                "ord": 1,
                "effect": "allow",
                "scope": "net",
                "mode": "exact",
                "selector": "host:1:2",
                "action": "connect",
            }
        ],
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["verdicts_raw"])
    assert rows[0]["verdict"] == "reject_malformed"
    assert rows[0]["matched_rule"] == ""


def test_empty_ops_pack_still_writes_outputs():
    """An empty ops.jsonl must still write empty verdicts.jsonl and a zeroed summary."""
    result = _run_ops([], rules=[])
    assert result["returncode"] == 0, result["stderr"]
    assert result["verdicts_raw"] == b""
    _assert_summary_shape(
        result["summary_raw"],
        {
            "op_count": 0,
            "by_verdict": {
                "allow": 0,
                "deny": 0,
                "deny_default": 0,
                "reject_malformed": 0,
            },
            "allow_count": 0,
        },
    )
    assert result["entries"] == ["summary.json", "verdicts.jsonl"]
    assert result["has_old"] is False
    assert result["has_prior"] is False
    assert result["has_alias"] is False
    assert result["verdicts_regular"] is True
    assert result["summary_regular"] is True
    assert result["shadow_verdicts_stale"] is True
    assert result["shadow_summary_stale"] is True


def test_starstar_glob_pathological_completes():
    """Many ** segments against a long non-matching path must finish with deny_default."""
    selector = "/" + "/".join(["**"] * 25) + "/z"
    target = "/" + "/".join(["a"] * 50)
    result = _run_ops(
        [{"op_id": "G", "scope": "fs", "action": "read", "target": target}],
        rules=[
            {
                "rule_id": "R",
                "ord": 1,
                "effect": "allow",
                "scope": "fs",
                "mode": "glob",
                "selector": selector,
                "action": "read",
            }
        ],
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["verdicts_raw"])
    assert len(rows) == 1
    assert rows[0]["verdict"] == "deny_default"
    assert rows[0]["matched_rule"] == ""


def test_cross_scope_exact_wildcard_does_not_match():
    """An otherwise matching fs rule must not apply to a proc op with the same target."""
    result = _run_ops(
        [{"op_id": "O", "scope": "proc", "action": "spawn", "target": "/x"}],
        rules=[
            {
                "rule_id": "R",
                "ord": 1,
                "effect": "allow",
                "scope": "fs",
                "mode": "exact",
                "selector": "/x",
                "action": "*",
            }
        ],
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["verdicts_raw"])
    assert rows[0]["verdict"] == "deny_default"
    assert rows[0]["matched_rule"] == ""


def test_relative_fs_glob_selector_matches():
    """A relative fs glob selector 'x' must match target '/x' after slash stripping."""
    result = _run_ops(
        [{"op_id": "O", "scope": "fs", "action": "read", "target": "/x"}],
        rules=[
            {
                "rule_id": "R",
                "ord": 1,
                "effect": "allow",
                "scope": "fs",
                "mode": "glob",
                "selector": "x",
                "action": "read",
            }
        ],
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["verdicts_raw"])
    assert rows[0]["verdict"] == "allow"
    assert rows[0]["matched_rule"] == "R"


def test_internal_empty_glob_segment_is_skipped():
    """Repeated slashes in an fs glob selector must invalidate the rule, not normalize away."""
    result = _run_ops(
        [{"op_id": "O", "scope": "fs", "action": "read", "target": "/x/y"}],
        rules=[
            {
                "rule_id": "R",
                "ord": 1,
                "effect": "allow",
                "scope": "fs",
                "mode": "glob",
                "selector": "/x//**",
                "action": "read",
            }
        ],
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["verdicts_raw"])
    assert rows[0]["verdict"] == "deny_default"
    assert rows[0]["matched_rule"] == ""


def test_question_mark_is_literal_in_glob():
    """SPE character wildcards are '*' only; '?' must not match a single character."""
    result = _run_ops(
        [{"op_id": "Q", "scope": "fs", "action": "read", "target": "/acb"}],
        rules=[
            {
                "rule_id": "R",
                "ord": 1,
                "effect": "allow",
                "scope": "fs",
                "mode": "glob",
                "selector": "/a?b",
                "action": "read",
            }
        ],
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["verdicts_raw"])
    assert rows[0]["verdict"] == "deny_default"
    assert rows[0]["matched_rule"] == ""


def test_trailing_slash_glob_selector_is_skipped():
    """A nonempty fs glob ending with '/' has an empty final segment and must be skipped."""
    result = _run_ops(
        [{"op_id": "O", "scope": "fs", "action": "read", "target": "/x"}],
        rules=[
            {
                "rule_id": "R",
                "ord": 1,
                "effect": "allow",
                "scope": "fs",
                "mode": "glob",
                "selector": "/x/",
                "action": "read",
            }
        ],
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["verdicts_raw"])
    assert rows[0]["verdict"] == "deny_default"
    assert rows[0]["matched_rule"] == ""


def test_go_mod_module_path_enforced():
    """Staged go.mod must declare module cordon.evaluate (trailing // comments allowed)."""
    assert _go_mod_module_path((APP / "go.mod").read_text()) == "cordon.evaluate"
    commented = "module cordon.evaluate // policy evaluator\n\ngo 1.24\n"
    assert _go_mod_module_path(commented) == "cordon.evaluate"


def test_final_binary_decodes_staged_paths():
    """The rebuilt binary must regenerate /app/out from the staged packs."""
    result = _run_staged_pack()
    assert result["returncode"] == 0, result["stderr"]
    got_rows = _parse_jsonl(result["verdicts_raw"])
    for row in got_rows:
        _assert_native_ints(row)
    assert got_rows == EXPECTED_PRIMARY_ROWS
    for keys in _row_key_orders(result["verdicts_raw"]):
        assert keys == ROW_KEYS
    got_summary = json.loads(result["summary_raw"].decode())
    _assert_native_ints(got_summary)
    assert got_summary == EXPECTED_PRIMARY_SUMMARY
    assert result["entries"] == ["summary.json", "verdicts.jsonl"]
    assert result["verdicts_regular"] is True
    assert result["summary_regular"] is True
    assert result["shadow_verdicts_stale"] is True
    assert result["shadow_summary_stale"] is True
    assert result["has_old"] is False
    assert result["has_prior"] is False
    assert result["has_alias"] is False


def test_holdout_out_clears_prior_entries():
    """A holdout --out directory with leftovers must keep only the two result files."""
    result = _run_holdout()
    assert result["returncode"] == 0, result["stderr"]
    assert result["entries"] == ["summary.json", "verdicts.jsonl"]
    assert result["verdicts_regular"] is True
    assert result["summary_regular"] is True
    assert result["shadow_verdicts_stale"] is True
    assert result["shadow_summary_stale"] is True
    assert result["has_old"] is False
    assert result["has_prior"] is False
    assert result["has_alias"] is False


def test_prefix_action_split_on_share_tree():
    """Read allow and write deny at equal prefix rank must split by action (O07/O08)."""
    rows = {r["op_id"]: r for r in GRADED_PRIMARY_ROWS}
    assert rows["O07"]["matched_rule"] == "F05"
    assert rows["O08"]["matched_rule"] == "F06"


def test_proc_prefix_spawn_nested():
    """Proc prefix inheritance must allow nested binaries under /usr/local/bin."""
    rows = {r["op_id"]: r for r in GRADED_PRIMARY_ROWS}
    assert rows["O11"]["verdict"] == "allow"
    assert rows["O11"]["matched_rule"] == "P03"
    assert rows["O32"]["verdict"] == "allow"
    assert rows["O32"]["matched_rule"] == "P03"
