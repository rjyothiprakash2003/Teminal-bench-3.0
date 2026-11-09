"""Verifier for the BW-2026 Brinewire session decoder."""

from __future__ import annotations

import json
import os
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

APP = Path("/app")
TESTS = Path("/tests")
SANDBOX_ROOT = Path("/sandbox")
PROGRAM = SANDBOX_ROOT / "bin" / "decode.py"
SANDBOX_UID = 2000
SANDBOX_GID = 2000
DISPOSITION_ORDER = ["ok", "reject_syntax", "reject_flag", "reject_nested_encap"]
SESSION_ROW_KEYS = ["bytes", "disposition", "flags", "frame_count", "session_id"]


def _snapshot_tree(root: Path) -> dict[str, bytes]:
    """Copy every file under root into a relative-path map before those trees are removed."""
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _parse_jsonl(raw: bytes) -> list[dict]:
    """Parse a JSONL byte string into session rows."""
    rows = []
    for line in raw.decode("utf-8").splitlines():
        rows.append(json.loads(line))
    return rows


def _assert_native_ints(obj: object, path: str = "$") -> None:
    """Require counts and payload bytes to be JSON integers, not bools or floats."""
    if isinstance(obj, bool) or isinstance(obj, float):
        raise AssertionError(f"{path}: expected int-compatible JSON number, got {obj!r}")
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in {"frame_count", "session_count", "ok_count"}:
                assert isinstance(value, int) and not isinstance(value, bool), (
                    f"{path}.{key}: expected int, got {type(value).__name__}"
                )
            elif key == "bytes":
                assert isinstance(value, list), f"{path}.bytes: expected list"
                for i, item in enumerate(value):
                    assert isinstance(item, int) and not isinstance(item, bool), (
                        f"{path}.bytes[{i}]: expected int"
                    )
            else:
                _assert_native_ints(value, f"{path}.{key}")
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            _assert_native_ints(item, f"{path}[{i}]")


def _row_key_orders(raw: bytes) -> list[list[str]]:
    """Return the key order of each sessions.jsonl object."""
    orders = []
    for line in raw.decode("utf-8").splitlines():
        pairs = json.loads(line, object_pairs_hook=list)
        orders.append([k for k, _ in pairs])
    return orders


HOLDOUT_FILES = _snapshot_tree(TESTS / "holdout")
EXPECTED_PRIMARY_SESSIONS_RAW = (TESTS / "expected" / "sessions.jsonl").read_bytes()
EXPECTED_PRIMARY_SUMMARY_RAW = (TESTS / "expected" / "summary.json").read_bytes()
EXPECTED_HOLDOUT_SESSIONS_RAW = (TESTS / "expected" / "holdout_sessions.jsonl").read_bytes()
EXPECTED_HOLDOUT_SUMMARY_RAW = (TESTS / "expected" / "holdout_summary.json").read_bytes()
EXPECTED_PRIMARY_ROWS = _parse_jsonl(EXPECTED_PRIMARY_SESSIONS_RAW)
EXPECTED_PRIMARY_SUMMARY = json.loads(EXPECTED_PRIMARY_SUMMARY_RAW.decode())
EXPECTED_HOLDOUT_ROWS = _parse_jsonl(EXPECTED_HOLDOUT_SESSIONS_RAW)
EXPECTED_HOLDOUT_SUMMARY = json.loads(EXPECTED_HOLDOUT_SUMMARY_RAW.decode())

DELIVERED_OUTPUT_PATHS = sorted(
    APP.joinpath("output").iterdir(), key=lambda path: path.name
)
assert [path.name for path in DELIVERED_OUTPUT_PATHS] == ["sessions.jsonl", "summary.json"]
assert all(path.is_file() and not path.is_symlink() for path in DELIVERED_OUTPUT_PATHS)
DELIVERED_PRIMARY_SESSIONS_RAW = (APP / "output" / "sessions.jsonl").read_bytes()
DELIVERED_PRIMARY_SUMMARY_RAW = (APP / "output" / "summary.json").read_bytes()
os.chmod(APP / "output", 0o700)

assert (APP / "decode.py").is_file() and not (APP / "decode.py").is_symlink()
PROGRAM.parent.mkdir(parents=True, exist_ok=True)


def _stage_candidate_python() -> None:
    """Copy the candidate program into the sandbox, leaving captures and the spec behind."""
    skip_roots = {"output", "captures", "brinewire"}
    for path in sorted(APP.rglob("*")):
        if path.is_symlink():
            continue
        rel = path.relative_to(APP)
        if rel.parts and rel.parts[0] in skip_roots:
            continue
        dest = PROGRAM.parent / rel
        if path.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
            os.chown(dest, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
            continue
        if not path.is_file():
            continue
        assert not path.is_symlink()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(path.read_bytes())
        dest.chmod(0o755 if rel.parts == ("decode.py",) else 0o644)
        os.chown(dest.parent, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
        os.chown(dest, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)


_stage_candidate_python()
assert PROGRAM.is_file()
os.chown(SANDBOX_ROOT, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
os.chown(PROGRAM.parent, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
os.chmod(APP, 0o700)

shutil.rmtree(TESTS / "expected")
shutil.rmtree(TESTS / "holdout")
os.chmod(TESTS, 0o700)


def _materialize(root: Path, files: dict[str, bytes]) -> None:
    """Write a relative-path map back out as files under root."""
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def _chown_tree(root: Path, uid: int, gid: int) -> None:
    """Give the sandbox user ownership of root and everything under it."""
    for path in [root, *root.rglob("*")]:
        os.chown(path, uid, gid, follow_symlinks=False)


def _live_sandbox_pids() -> list[int]:
    """List non-zombie processes still running as the sandbox user."""
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


def _drain_sandbox(deadline: float = 15.0) -> None:
    """Wait for sandbox processes to exit, then kill any that remain."""
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


def _apply_resource_limits() -> None:
    """Cap each decoder process at the published 1800-second verifier timeout."""
    resource.setrlimit(resource.RLIMIT_CPU, (1800, 1800))
    resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024 * 1024, 32 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    if hasattr(resource, "RLIMIT_NPROC"):
        resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))


DECOY_MANIFEST = b'{"session_id":"DECOY","flags":"nope","frames":["zz"]}\n'


def _plant_decoys(*directories: Path) -> None:
    """Drop a decoy manifest so a decoder that reads the wrong file fails."""
    for directory in directories:
        (directory / "manifest.jsonl").write_bytes(DECOY_MANIFEST)


def _plant_output_leftovers(output: Path) -> None:
    """Leave a file, a directory, and a symlink in --out for the decoder to clear."""
    for child in list(output.iterdir()):
        if child.is_symlink() or child.is_file():
            child.unlink()
        elif child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    (output / "prior.json").write_bytes(b"{}\n")
    leftover_dir = output / "old"
    leftover_dir.mkdir()
    (leftover_dir / "stale.txt").write_bytes(b"leftover\n")
    (output / "alias").symlink_to("prior.json")


def _execute_decoder(
    captures: Path, spec: Path, output: Path, home: Path, work: Path, log_dir: Path
) -> dict[str, object]:
    """Run the sandboxed decoder and return its status, outputs, and leftover names."""
    stdout_path = log_dir / "stdout"
    stderr_path = log_dir / "stderr"
    env = {
        "HOME": str(home),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "PYTHONHASHSEED": "0",
        "PYTHONPATH": str(PROGRAM.parent),
        "PYTHONSAFEPATH": "0",
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
        sys.executable,
        str(PROGRAM),
        "--captures",
        str(captures),
        "--spec",
        str(spec),
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
            preexec_fn=_apply_resource_limits,
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
            _drain_sandbox()

    return {
        "returncode": returncode,
        "stderr": stderr_path.read_text(errors="replace"),
        "sessions_raw": (output / "sessions.jsonl").read_bytes()
        if (output / "sessions.jsonl").is_file()
        else b"",
        "summary_raw": (output / "summary.json").read_bytes()
        if (output / "summary.json").is_file()
        else b"",
        "entries": sorted(path.name for path in output.iterdir()),
        "has_old": (output / "old").exists(),
        "has_prior": (output / "prior.json").exists(),
        "has_alias": (output / "alias").exists(),
    }


def _run_decode() -> dict[str, object]:
    """Replay the sealed holdout pack through the sandboxed decoder."""
    base = Path(tempfile.mkdtemp(prefix="bench_", dir="/tmp"))
    captures = base / "c"
    spec = base / "s"
    output = base / "o"
    home = base / "h"
    work = base / "w"
    for path in (captures, spec, output, home, work):
        path.mkdir()
    _materialize(
        captures, {k[9:]: v for k, v in HOLDOUT_FILES.items() if k.startswith("captures/")}
    )
    _materialize(
        spec, {k[10:]: v for k, v in HOLDOUT_FILES.items() if k.startswith("brinewire/")}
    )
    _plant_decoys(spec, work)
    _plant_output_leftovers(output)
    _chown_tree(base, SANDBOX_UID, SANDBOX_GID)
    try:
        return _execute_decoder(captures, spec, output, home, work, base)
    finally:
        shutil.rmtree(base)


def _run_capture_rows(rows: list[dict]) -> dict[str, object]:
    """Decode a freshly built manifest against the sealed BW-2026 spec."""
    base = Path(tempfile.mkdtemp(prefix="bench_", dir="/tmp"))
    captures = base / "c"
    spec = base / "s"
    output = base / "o"
    home = base / "h"
    work = base / "w"
    for path in (captures, spec, output, home, work):
        path.mkdir()
    manifest = "".join(
        json.dumps(row, separators=(",", ":"), ensure_ascii=False) + "\n" for row in rows
    )
    (captures / "manifest.jsonl").write_text(manifest)
    _materialize(
        spec, {k[10:]: v for k, v in HOLDOUT_FILES.items() if k.startswith("brinewire/")}
    )
    _plant_decoys(spec, work)
    _chown_tree(base, SANDBOX_UID, SANDBOX_GID)
    try:
        return _execute_decoder(captures, spec, output, home, work, base)
    finally:
        shutil.rmtree(base)


def _assert_summary_shape(summary_raw: bytes, expected: dict) -> None:
    """Check summary totals, key order, and that every disposition count is an int."""
    got = json.loads(summary_raw.decode())
    _assert_native_ints(got)
    assert got == expected
    ordered = json.loads(summary_raw.decode(), object_pairs_hook=list)
    assert [k for k, _ in ordered] == ["session_count", "by_disposition", "ok_count"]
    by_disposition = dict(ordered)["by_disposition"]
    assert [k for k, _ in by_disposition] == DISPOSITION_ORDER
    for key, value in by_disposition:
        assert type(value) is int


def _provision_staged_pack() -> None:
    """The verifier image does not contain the agent pack. Recreate the staged paths."""
    captures = APP / "captures"
    spec = APP / "brinewire"
    captures.mkdir(parents=True, exist_ok=True)
    spec.mkdir(parents=True, exist_ok=True)
    (captures / "manifest.jsonl").write_bytes(
        (TESTS / "primary_pack" / "manifest.jsonl").read_bytes()
    )
    _materialize(
        spec, {k[10:]: v for k, v in HOLDOUT_FILES.items() if k.startswith("brinewire/")}
    )


def _run_staged_pack() -> dict[str, object]:
    """Invoke the staged program on /app/captures, /app/brinewire, and /app/output."""
    output = APP / "output"
    base = Path(tempfile.mkdtemp(prefix="staged_", dir="/tmp"))
    home = base / "h"
    work = base / "w"
    home.mkdir()
    work.mkdir()
    _plant_decoys(work)
    _chown_tree(base, SANDBOX_UID, SANDBOX_GID)
    os.chmod(APP, 0o755)
    _provision_staged_pack()
    for root in (APP / "captures", APP / "brinewire"):
        os.chmod(root, 0o755)
        for path in root.rglob("*"):
            os.chmod(path, 0o755 if path.is_dir() else 0o644)
    output.mkdir(parents=True, exist_ok=True)
    os.chmod(output, 0o755)
    decoy = APP / "brinewire" / "manifest.jsonl"
    try:
        _plant_decoys(APP / "brinewire")
        decoy.chmod(0o644)
        _plant_output_leftovers(output)
        _chown_tree(output, SANDBOX_UID, SANDBOX_GID)
        return _execute_decoder(
            APP / "captures", APP / "brinewire", output, home, work, base
        )
    finally:
        if decoy.exists():
            decoy.unlink()
        shutil.rmtree(base)
        os.chmod(APP, 0o700)
        if output.exists():
            _chown_tree(output, 0, 0)
            os.chmod(output, 0o700)


def test_decode_script_exists():
    """Verification rebuild surface /app/decode.py must exist."""
    assert (APP / "decode.py").is_file()


def test_primary_sessions_match_expected():
    """Primary sessions.jsonl must match the sealed BW ledger row-for-row."""
    got = _parse_jsonl(DELIVERED_PRIMARY_SESSIONS_RAW)
    for row in got:
        _assert_native_ints(row)
    assert got == EXPECTED_PRIMARY_ROWS


def test_primary_summary_matches_expected():
    """Primary summary.json must match sealed disposition totals."""
    got = json.loads(DELIVERED_PRIMARY_SUMMARY_RAW.decode())
    _assert_native_ints(got)
    assert got == EXPECTED_PRIMARY_SUMMARY


def test_summary_disposition_key_order():
    """summary.json top-level keys and by_disposition keys must use the BW order."""
    ordered = json.loads(
        DELIVERED_PRIMARY_SUMMARY_RAW.decode(), object_pairs_hook=list
    )
    keys = [k for k, _ in ordered]
    assert keys == ["session_count", "by_disposition", "ok_count"]
    by_disposition = dict(ordered)["by_disposition"]
    disp_keys = [k for k, _ in by_disposition]
    assert disp_keys == DISPOSITION_ORDER


def test_session_row_keys_sorted():
    """Each sessions.jsonl object must serialize keys in sorted order."""
    for keys in _row_key_orders(DELIVERED_PRIMARY_SESSIONS_RAW):
        assert keys == SESSION_ROW_KEYS


def test_output_dir_only_required_files():
    """/app/output must contain only sessions.jsonl and summary.json."""
    assert [path.name for path in DELIVERED_OUTPUT_PATHS] == [
        "sessions.jsonl",
        "summary.json",
    ]


def test_hex_prefix_requires_flag_i():
    """Flag i must accept a 0X prefix; the same prefix without i is reject_syntax."""
    rows = {r["session_id"]: r for r in _parse_jsonl(DELIVERED_PRIMARY_SESSIONS_RAW)}
    assert rows["S05"]["disposition"] == "ok"
    assert rows["S05"]["bytes"] == [81]
    assert rows["S26"]["disposition"] == "reject_syntax"
    assert rows["S26"]["bytes"] == []
    assert rows["S26"]["frame_count"] == 0


def test_extended_length_uses_two_bytes():
    """Flag e must parse a 16-bit length on staged session S04."""
    rows = {r["session_id"]: r for r in _parse_jsonl(DELIVERED_PRIMARY_SESSIONS_RAW)}
    assert rows["S04"]["disposition"] == "ok"
    assert rows["S04"]["bytes"] == [120, 121]


def test_single_encap_unwraps_inner_data():
    """A single ENCAP wrapping DATA must emit only the inner payload (S06)."""
    rows = {r["session_id"]: r for r in _parse_jsonl(DELIVERED_PRIMARY_SESSIONS_RAW)}
    assert rows["S06"]["disposition"] == "ok"
    assert rows["S06"]["bytes"] == [73, 78]


def test_encap_then_data_concatenates():
    """ENCAP data then a later DATA frame must concatenate in capture order (S20)."""
    rows = {r["session_id"]: r for r in _parse_jsonl(DELIVERED_PRIMARY_SESSIONS_RAW)}
    assert rows["S20"]["disposition"] == "ok"
    assert rows["S20"]["bytes"] == [97, 98, 99]


def test_nested_encap_rejects():
    """Nested ENCAP must disposition reject_nested_encap."""
    rows = {r["session_id"]: r for r in _parse_jsonl(DELIVERED_PRIMARY_SESSIONS_RAW)}
    for sid in ("S07", "S24"):
        assert rows[sid]["disposition"] == "reject_nested_encap"
        assert rows[sid]["bytes"] == []
        assert rows[sid]["frame_count"] == 0


def test_syntax_and_flag_rejects():
    """Bad magic, flags, hex, sequence, TERM, ACK, kind, and length must reject."""
    rows = {r["session_id"]: r for r in _parse_jsonl(DELIVERED_PRIMARY_SESSIONS_RAW)}
    reject_ids = (
        "S08",
        "S09",
        "S11",
        "S12",
        "S13",
        "S14",
        "S15",
        "S18",
        "S19",
        "S23",
        "S25",
        "S26",
        "S27",
        "S28",
        "S29",
        "S30",
        "S31",
        "S32",
        "S34",
    )
    for sid in reject_ids:
        if sid in ("S09", "S12"):
            assert rows[sid]["disposition"] == "reject_flag"
        else:
            assert rows[sid]["disposition"] == "reject_syntax"
        assert rows[sid]["bytes"] == []
        assert rows[sid]["frame_count"] == 0


def test_ack_echo_and_seq_wrap():
    """ACK must echo the last data seq, and sequence 255 must wrap to 0."""
    rows = {r["session_id"]: r for r in _parse_jsonl(DELIVERED_PRIMARY_SESSIONS_RAW)}
    assert rows["S10"]["disposition"] == "ok"
    assert rows["S10"]["bytes"] == [75]
    assert rows["S17"]["disposition"] == "ok"
    assert rows["S17"]["bytes"] == [87]


def test_empty_data_payload_is_ok():
    """DATA with zero payload bytes plus TERM must still be ok (S21)."""
    rows = {r["session_id"]: r for r in _parse_jsonl(DELIVERED_PRIMARY_SESSIONS_RAW)}
    assert rows["S21"]["disposition"] == "ok"
    assert rows["S21"]["bytes"] == []
    assert rows["S21"]["frame_count"] == 2


def test_extended_seal_pair():
    """Flags e and x together must accept a sealed extended DATA frame (S16)."""
    rows = {r["session_id"]: r for r in _parse_jsonl(DELIVERED_PRIMARY_SESSIONS_RAW)}
    assert rows["S16"]["disposition"] == "ok"
    assert rows["S16"]["bytes"] == [111, 107]
    assert rows["S16"]["flags"] == "ex"


def test_seal_unchecked_without_flag_x():
    """Without flag x a corrupted seal must still decode as ok (S33)."""
    rows = {r["session_id"]: r for r in _parse_jsonl(DELIVERED_PRIMARY_SESSIONS_RAW)}
    assert rows["S33"]["disposition"] == "ok"
    assert rows["S33"]["bytes"] == [72, 105]
    assert rows["S02"]["disposition"] == "ok"
    assert rows["S02"]["bytes"] == [72, 105]


def test_uppercase_prefix_permuted_flags_and_session_order():
    """0X prefixes, non-canonical flag order, and session_id sorting are graded."""
    got = _parse_jsonl(DELIVERED_PRIMARY_SESSIONS_RAW)
    rows = {r["session_id"]: r for r in got}
    assert [row["session_id"] for row in got] == sorted(row["session_id"] for row in got)
    assert rows["S05"]["disposition"] == "ok"
    assert rows["S05"]["bytes"] == [81]
    assert rows["S05"]["frame_count"] == 2
    assert rows["S22"]["flags"] == "ie"
    assert rows["S22"]["disposition"] == "ok"
    assert rows["S22"]["bytes"] == [77]
    assert rows["S35"]["disposition"] == "ok"
    assert rows["S35"]["flags"] == "e"
    assert rows["S35"]["bytes"] == [0] * 256
    assert rows["S35"]["frame_count"] == 2
    assert rows["S36"]["disposition"] == "ok"
    assert rows["S36"]["bytes"] == [73, 78]
    assert rows["S36"]["frame_count"] == 3
    assert rows["S37"]["disposition"] == "reject_syntax"
    assert rows["S37"]["bytes"] == []
    assert rows["S37"]["frame_count"] == 0
    assert rows["S38"]["disposition"] == "reject_syntax"
    assert rows["S38"]["flags"] == "x"
    assert rows["S38"]["bytes"] == []
    assert rows["S38"]["frame_count"] == 0
    assert rows["S39"]["disposition"] == "reject_flag"
    assert rows["S39"]["flags"] == "E"
    assert rows["S40"]["disposition"] == "reject_flag"
    assert rows["S40"]["flags"] == "ee"
    assert rows["S41"]["disposition"] == "reject_syntax"
    assert rows["S42"]["disposition"] == "ok"
    assert rows["S42"]["bytes"] == [171]
    assert rows["S42"]["frame_count"] == 2
    assert rows["S43"]["disposition"] == "reject_flag"
    assert rows["S43"]["flags"] == "ii"
    assert rows["S44"]["disposition"] == "reject_syntax"
    assert rows["S45"]["disposition"] == "reject_syntax"
    assert rows["S46"]["disposition"] == "ok"
    assert rows["S46"]["bytes"] == []
    assert rows["S46"]["frame_count"] == 1
    assert rows["S47"]["disposition"] == "reject_nested_encap"
    assert rows["S47"]["bytes"] == []
    assert rows["S47"]["frame_count"] == 0


def test_holdout_replay_sessions():
    """Re-running decode.py on sealed holdout captures must match holdout sessions."""
    result = _run_decode()
    assert result["returncode"] == 0, result["stderr"]
    got = _parse_jsonl(result["sessions_raw"])
    for row in got:
        _assert_native_ints(row)
    assert len(got) == len(EXPECTED_HOLDOUT_ROWS)
    assert [row["session_id"] for row in got] == sorted(row["session_id"] for row in got)
    rows = {r["session_id"]: r for r in got}
    assert rows["H02"]["flags"] == "xi"
    assert rows["H02"]["disposition"] == "ok"
    assert rows["H02"]["bytes"] == [103, 111]
    assert rows["H13"]["disposition"] == "ok"
    assert rows["H13"]["bytes"] == [90] * 258
    assert rows["H13"]["frame_count"] == 2
    assert rows["H14"]["disposition"] == "ok"
    assert rows["H14"]["bytes"] == [81, 90]
    assert rows["H14"]["frame_count"] == 3
    assert rows["H15"]["disposition"] == "reject_syntax"
    assert rows["H15"]["bytes"] == []
    assert rows["H15"]["frame_count"] == 0
    assert rows["H16"]["disposition"] == "reject_syntax"
    assert rows["H16"]["flags"] == "x"
    assert rows["H16"]["bytes"] == []
    assert rows["H16"]["frame_count"] == 0
    assert rows["H41"]["disposition"] == "reject_flag"
    assert rows["H41"]["flags"] == "E"
    assert rows["H17"]["disposition"] == "reject_syntax"
    assert rows["H18"]["disposition"] == "reject_syntax"
    assert rows["H19"]["disposition"] == "ok"
    assert rows["H19"]["bytes"] == []
    assert rows["H19"]["frame_count"] == 1
    assert rows["H20"]["disposition"] == "reject_nested_encap"
    assert rows["H20"]["bytes"] == []
    assert rows["H20"]["frame_count"] == 0
    assert rows["H42"]["disposition"] == "reject_flag"
    assert rows["H42"]["flags"] == "ii"
    assert rows["H43"]["disposition"] == "reject_syntax"
    assert rows["H44"]["disposition"] == "ok"
    assert rows["H44"]["bytes"] == [205, 239]
    assert rows["H44"]["frame_count"] == 2
    assert got == EXPECTED_HOLDOUT_ROWS
    for keys in _row_key_orders(result["sessions_raw"]):
        assert keys == SESSION_ROW_KEYS


def test_holdout_replay_summary():
    """Re-running decode.py on sealed holdout captures must match holdout summary."""
    result = _run_decode()
    assert result["returncode"] == 0, result["stderr"]
    got = json.loads(result["summary_raw"].decode())
    _assert_native_ints(got)
    assert got == EXPECTED_HOLDOUT_SUMMARY
    ordered = json.loads(result["summary_raw"].decode(), object_pairs_hook=list)
    assert [k for k, _ in ordered] == ["session_count", "by_disposition", "ok_count"]
    by_disposition = dict(ordered)["by_disposition"]
    assert [k for k, _ in by_disposition] == DISPOSITION_ORDER


def test_zero_disposition_counts_stay_in_summary():
    """by_disposition keeps every key when a count is zero, as a JSON number."""
    ok_only = _run_capture_rows(
        [
            {
                "session_id": "Z01",
                "flags": "",
                "frames": ["4201000361626386", "42040100e2"],
            }
        ]
    )
    assert ok_only["returncode"] == 0, ok_only["stderr"]
    _assert_summary_shape(
        ok_only["summary_raw"],
        {
            "session_count": 1,
            "by_disposition": {
                "ok": 1,
                "reject_syntax": 0,
                "reject_flag": 0,
                "reject_nested_encap": 0,
            },
            "ok_count": 1,
        },
    )
    syntax_only = _run_capture_rows(
        [{"session_id": "Z02", "flags": "", "frames": []}]
    )
    assert syntax_only["returncode"] == 0, syntax_only["stderr"]
    _assert_summary_shape(
        syntax_only["summary_raw"],
        {
            "session_count": 1,
            "by_disposition": {
                "ok": 0,
                "reject_syntax": 1,
                "reject_flag": 0,
                "reject_nested_encap": 0,
            },
            "ok_count": 0,
        },
    )


def test_final_executable_decodes_staged_paths():
    """The final decode.py must regenerate /app/output from the staged /app/captures pack."""
    result = _run_staged_pack()
    assert result["returncode"] == 0, result["stderr"]
    got_rows = _parse_jsonl(result["sessions_raw"])
    for row in got_rows:
        _assert_native_ints(row)
    assert got_rows == EXPECTED_PRIMARY_ROWS
    for keys in _row_key_orders(result["sessions_raw"]):
        assert keys == SESSION_ROW_KEYS
    got_summary = json.loads(result["summary_raw"].decode())
    _assert_native_ints(got_summary)
    assert got_summary == EXPECTED_PRIMARY_SUMMARY
    ordered = json.loads(result["summary_raw"].decode(), object_pairs_hook=list)
    assert [k for k, _ in ordered] == ["session_count", "by_disposition", "ok_count"]
    by_disposition = dict(ordered)["by_disposition"]
    assert [k for k, _ in by_disposition] == DISPOSITION_ORDER
    assert result["entries"] == ["sessions.jsonl", "summary.json"]
    assert result["has_old"] is False
    assert result["has_prior"] is False
    assert result["has_alias"] is False


def test_generated_rows_cover_flag_term_and_nested_ack():
    """A fresh pack must reject duplicate i, a blank frame, and a TERM sequence gap, and must accept TERM alone."""
    w02 = json.loads(HOLDOUT_FILES["brinewire/worked_examples/W02_encap_encap_ack.json"].decode())
    result = _run_capture_rows(
        [
            {"session_id": "G01", "flags": "ii", "frames": ["420100016187", "42040100e2"]},
            {"session_id": "G02", "flags": "", "frames": ["", "420100016187", "42040100e2"]},
            {"session_id": "G03", "flags": "", "frames": ["420100016187", "42040700e4"]},
            {"session_id": "G04", "flags": "", "frames": ["42040000e3"]},
            {"session_id": "G05", "flags": "", "frames": list(w02["frames"])},
        ]
    )
    assert result["returncode"] == 0, result["stderr"]
    rows = {r["session_id"]: r for r in _parse_jsonl(result["sessions_raw"])}
    assert rows["G01"]["disposition"] == "reject_flag"
    assert rows["G02"]["disposition"] == "reject_syntax"
    assert rows["G03"]["disposition"] == "reject_syntax"
    assert rows["G04"]["disposition"] == "ok"
    assert rows["G04"]["bytes"] == []
    assert rows["G04"]["frame_count"] == 1
    assert rows["G05"]["disposition"] == "reject_nested_encap"
    assert rows["G05"]["bytes"] == []
    assert rows["G05"]["frame_count"] == 0


def test_holdout_nested_extended_rejects():
    """Holdout nested ENCAP under flag e must reject_nested_encap."""
    result = _run_decode()
    assert result["returncode"] == 0, result["stderr"]
    rows = {r["session_id"]: r for r in _parse_jsonl(result["sessions_raw"])}
    assert rows["H03"]["disposition"] == "reject_nested_encap"
    assert rows["H03"]["flags"] == "e"
    assert rows["H03"]["bytes"] == []
    assert rows["H06"]["disposition"] == "reject_syntax"
    assert rows["H09"]["disposition"] == "reject_syntax"
    assert rows["H10"]["disposition"] == "reject_syntax"
    assert rows["H11"]["disposition"] == "ok"
    assert rows["H12"]["disposition"] == "reject_syntax"


def test_holdout_lookalike_payload_is_data():
    """Holdout DATA payload that resembles ENCAP bytes must stay ok (H07)."""
    result = _run_decode()
    assert result["returncode"] == 0, result["stderr"]
    rows = {r["session_id"]: r for r in _parse_jsonl(result["sessions_raw"])}
    row = rows["H07"]
    assert row["disposition"] == "ok"
    assert row["bytes"] == [66, 3, 0, 1, 97, 0]


def test_holdout_seq_wrap_across_255():
    """Holdout sequence 254, 255, then 0 must concatenate as one ok session."""
    result = _run_decode()
    assert result["returncode"] == 0, result["stderr"]
    rows = {r["session_id"]: r for r in _parse_jsonl(result["sessions_raw"])}
    row = rows["H04"]
    assert row["disposition"] == "ok"
    assert row["bytes"] == [88, 89]
    assert row["frame_count"] == 3


def test_holdout_out_clears_prior_entries():
    """A holdout --out directory that already has a file, directory, and symlink must keep only the two result files."""
    result = _run_decode()
    assert result["returncode"] == 0, result["stderr"]
    assert result["entries"] == ["sessions.jsonl", "summary.json"]
    assert result["has_old"] is False
    assert result["has_prior"] is False
    assert result["has_alias"] is False
