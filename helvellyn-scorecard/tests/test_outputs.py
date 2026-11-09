"""Sealed verifier for the SMF-2026 scorecard folder.

Compares staged and holdout ledgers to expected goldens, including 6-tuple
policy recovery across independent --spec example overlays.
"""

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
PROGRAM = SANDBOX_ROOT / "bin" / "fold.py"
SANDBOX_UID = 2000
SANDBOX_GID = 2000
DISPOSITION_ORDER = ["ok", "reject_schema", "reject_class", "reject_fold"]
FOLD_ROW_KEYS = [
    "ap_micro_u",
    "bin_count",
    "card_id",
    "class_count",
    "disposition",
    "ece_micro_u",
    "row_count",
    "weight",
]
SUMMARY_KEYS = ["ap_pool_micro_u", "by_disposition", "card_count", "ece_pool_micro_u", "ok_count"]
INT_KEYS = {
    "ap_micro_u",
    "ap_pool_micro_u",
    "bin_count",
    "class_count",
    "ece_micro_u",
    "ece_pool_micro_u",
    "row_count",
    "card_count",
    "ok_count",
    "weight",
}


def _snapshot_tree(root: Path) -> dict[str, bytes]:
    snapshot = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        snapshot[str(path.relative_to(root))] = path.read_bytes()
    return snapshot


def _jsonl_records(raw: bytes) -> list[str]:
    text = raw.decode("utf-8")
    if not text:
        return []
    if text.endswith("\n"):
        text = text[:-1]
    records = text.split("\n")
    for index, record in enumerate(records):
        assert record.strip(), f"record {index}: blank JSONL record"
    return records


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict:
    keys = [key for key, _ in pairs]
    assert len(keys) == len(set(keys)), f"duplicate JSON keys: {keys!r}"
    return dict(pairs)


def _parse_jsonl(raw: bytes) -> list[dict]:
    rows = []
    for record in _jsonl_records(raw):
        value = json.loads(record, object_pairs_hook=_unique_pairs)
        assert isinstance(value, dict), f"expected JSON object, got {value!r}"
        assert list(value) == FOLD_ROW_KEYS, f"fold row keys {list(value)!r}"
        _assert_native_ints(value)
        rows.append(value)
    return rows


def _parse_summary(raw: bytes) -> dict:
    """Parse summary.json bytes; require SMF key order, unique keys, and native JSON integers."""
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs)
    assert isinstance(value, dict), f"expected JSON object, got {value!r}"
    assert list(value) == SUMMARY_KEYS, f"summary keys {list(value)!r}"
    assert isinstance(value["by_disposition"], dict)
    assert list(value["by_disposition"]) == DISPOSITION_ORDER
    _assert_native_ints(value)
    return value


def _canonicalize_folds(rows: list[dict]) -> list[dict]:
    """Normalize folds under the disclosed ordering: ascending card_id only.

    SMF requires rows sorted by card_id but does not tie-break equal card_ids.
    Within an equal-id run, compare by weight then by full-row JSON so either
    manifest-relative order or another equal-id permutation is accepted.
    """
    ids = [row["card_id"] for row in rows]
    assert ids == sorted(ids), f"card_id sequence not ascending: {ids!r}"
    out: list[dict] = []
    index = 0
    while index < len(rows):
        end = index + 1
        while end < len(rows) and rows[end]["card_id"] == rows[index]["card_id"]:
            end += 1
        group = rows[index:end]
        group = sorted(
            group,
            key=lambda row: (row.get("weight", 0), json.dumps(row, sort_keys=True)),
        )
        out.extend(group)
        index = end
    return out


def _assert_folds_match(got: list[dict], expected: list[dict]) -> None:
    assert _canonicalize_folds(got) == _canonicalize_folds(expected)


def _assert_native_ints(obj: object, path: str = "$") -> None:
    if isinstance(obj, bool) or isinstance(obj, float):
        raise AssertionError(f"{path}: expected int-compatible JSON number, got {obj!r}")
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in INT_KEYS:
                assert isinstance(value, int) and not isinstance(value, bool), (
                    f"{path}.{key}: expected int, got {type(value).__name__}"
                )
            else:
                _assert_native_ints(value, f"{path}.{key}")
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            _assert_native_ints(item, f"{path}[{i}]")


def _fold_key_orders(raw: bytes) -> list[list[str]]:
    orders = []
    for record in _jsonl_records(raw):
        pairs = json.loads(record, object_pairs_hook=list)
        orders.append([k for k, _ in pairs])
    return orders


def _summary_key_orders(raw: bytes) -> tuple[list[str], list[str]]:
    ordered = json.loads(raw.decode("utf-8"), object_pairs_hook=list)
    top = [k for k, _ in ordered]
    by_disposition = dict(ordered)["by_disposition"]
    nested = [k for k, _ in by_disposition]
    return top, nested


def _snapshot_links(root: Path) -> dict[str, str]:
    links = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            links[str(path.relative_to(root))] = os.readlink(path)
    return links


HOLDOUT_FILES = _snapshot_tree(TESTS / "holdout")
STAGED_CARD_FILES = _snapshot_tree(APP / "cards")
STAGED_CARD_LINKS = _snapshot_links(APP / "cards")
STAGED_SPEC_FILES = _snapshot_tree(APP / "scorecard")
EXPECTED_PRIMARY_FOLDS_RAW = (TESTS / "expected" / "folds.jsonl").read_bytes()
EXPECTED_PRIMARY_SUMMARY_RAW = (TESTS / "expected" / "summary.json").read_bytes()
EXPECTED_HOLDOUT_FOLDS_RAW = (TESTS / "expected" / "holdout_folds.jsonl").read_bytes()
EXPECTED_HOLDOUT_SUMMARY_RAW = (TESTS / "expected" / "holdout_summary.json").read_bytes()
EXPECTED_VOC101_FOLDS_RAW = (TESTS / "expected" / "voc101_folds.jsonl").read_bytes()
EXPECTED_VOC101_SUMMARY_RAW = (TESTS / "expected" / "voc101_summary.json").read_bytes()
EXPECTED_VOC11_FOLDS_RAW = (TESTS / "expected" / "voc11_folds.jsonl").read_bytes()
EXPECTED_VOC11_SUMMARY_RAW = (TESTS / "expected" / "voc11_summary.json").read_bytes()
EXPECTED_VOC101_ALT_FOLDS_RAW = (TESTS / "expected" / "voc101_alt_folds.jsonl").read_bytes()
EXPECTED_VOC101_ALT_SUMMARY_RAW = (TESTS / "expected" / "voc101_alt_summary.json").read_bytes()
EXPECTED_HOLDOUT_MAX_FOLDS_RAW = (TESTS / "expected" / "holdout_max_folds.jsonl").read_bytes()
EXPECTED_HOLDOUT_MAX_SUMMARY_RAW = (TESTS / "expected" / "holdout_max_summary.json").read_bytes()
EXPECTED_PRIMARY_ROWS = _parse_jsonl(EXPECTED_PRIMARY_FOLDS_RAW)
EXPECTED_PRIMARY_SUMMARY = _parse_summary(EXPECTED_PRIMARY_SUMMARY_RAW)
EXPECTED_HOLDOUT_ROWS = _parse_jsonl(EXPECTED_HOLDOUT_FOLDS_RAW)
EXPECTED_HOLDOUT_SUMMARY = _parse_summary(EXPECTED_HOLDOUT_SUMMARY_RAW)
EXPECTED_VOC101_ROWS = _parse_jsonl(EXPECTED_VOC101_FOLDS_RAW)
EXPECTED_VOC101_SUMMARY = _parse_summary(EXPECTED_VOC101_SUMMARY_RAW)
EXPECTED_VOC101_ALT_ROWS = _parse_jsonl(EXPECTED_VOC101_ALT_FOLDS_RAW)
EXPECTED_VOC101_ALT_SUMMARY = _parse_summary(EXPECTED_VOC101_ALT_SUMMARY_RAW)
EXPECTED_VOC11_ROWS = _parse_jsonl(EXPECTED_VOC11_FOLDS_RAW)
EXPECTED_VOC11_SUMMARY = _parse_summary(EXPECTED_VOC11_SUMMARY_RAW)
EXPECTED_HOLDOUT_MAX_ROWS = _parse_jsonl(EXPECTED_HOLDOUT_MAX_FOLDS_RAW)
EXPECTED_HOLDOUT_MAX_SUMMARY = _parse_summary(EXPECTED_HOLDOUT_MAX_SUMMARY_RAW)
PRIMARY_BY_ID = {row["card_id"]: row for row in EXPECTED_PRIMARY_ROWS}
HOLDOUT_BY_ID = {row["card_id"]: row for row in EXPECTED_HOLDOUT_ROWS}
VOC101_BY_ID = {row["card_id"]: row for row in EXPECTED_VOC101_ROWS}
VOC101_ALT_BY_ID = {row["card_id"]: row for row in EXPECTED_VOC101_ALT_ROWS}
VOC11_BY_ID = {row["card_id"]: row for row in EXPECTED_VOC11_ROWS}
HOLDOUT_MAX_BY_ID = {row["card_id"]: row for row in EXPECTED_HOLDOUT_MAX_ROWS}

DELIVERED_OUTPUT_PATHS = sorted(
    APP.joinpath("output").iterdir(), key=lambda path: path.name
)
assert [path.name for path in DELIVERED_OUTPUT_PATHS] == ["folds.jsonl", "summary.json"]
assert all(path.is_file() and not path.is_symlink() for path in DELIVERED_OUTPUT_PATHS)
DELIVERED_PRIMARY_FOLDS_RAW = (APP / "output" / "folds.jsonl").read_bytes()
DELIVERED_PRIMARY_SUMMARY_RAW = (APP / "output" / "summary.json").read_bytes()
os.chmod(APP / "output", 0o700)

assert (APP / "fold.py").is_file() and not (APP / "fold.py").is_symlink()
PROGRAM.parent.mkdir(parents=True, exist_ok=True)


def _stage_candidate_python() -> None:
    skip_roots = {"output", "cards"}
    for path in sorted(APP.rglob("*")):
        rel = path.relative_to(APP)
        if rel.parts and rel.parts[0] in skip_roots:
            continue
        dest = PROGRAM.parent / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            if dest.exists() or dest.is_symlink():
                dest.unlink()
            os.symlink(os.readlink(path), dest)
            os.chown(dest, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
            continue
        if path.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
            os.chown(dest, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
            continue
        if not path.is_file():
            continue
        dest.write_bytes(path.read_bytes())
        executable = rel.parts == ("fold.py",) or path.stat().st_mode & 0o111
        dest.chmod(0o755 if executable else 0o644)
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
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def _chown_tree(root: Path, uid: int, gid: int) -> None:
    for path in [root, *root.rglob("*")]:
        os.chown(path, uid, gid, follow_symlinks=False)


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


def _drain_sandbox(deadline: float = 15.0) -> None:
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
    resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024 * 1024, 32 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    if hasattr(resource, "RLIMIT_NPROC"):
        resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))


_HOLDOUT_RESULTS: dict[str, dict[str, object]] = {}


def _execute_fold(
    cards_files: dict[str, bytes],
    spec_files: dict[str, bytes],
    *,
    card_links: dict[str, str] | None = None,
    example_overlay: dict[str, bytes] | None = None,
    prepare_holdout: bool = False,
    live_cards: Path | None = None,
    live_spec: Path | None = None,
    live_out: Path | None = None,
) -> dict[str, object]:
    base = Path(tempfile.mkdtemp(prefix="bench_", dir="/tmp"))
    cards = base / "c"
    spec = base / "s"
    output = base / "o"
    home = base / "h"
    work = base / "w"
    for path in (cards, spec, output, home, work):
        path.mkdir()
    if live_out is not None:
        output = live_out
        output.mkdir(parents=True, exist_ok=True)
    cards_arg = live_cards if live_cards is not None else cards
    spec_arg = live_spec if live_spec is not None else spec
    if live_cards is None:
        _materialize(cards, cards_files)
        _materialize(spec, spec_files)
    if example_overlay:
        examples_dir = spec_arg / "worked_examples"
        examples_dir.mkdir(parents=True, exist_ok=True)
        for child in list(examples_dir.iterdir()):
            if child.is_file() and not child.is_symlink() and child.suffix == ".json":
                child.unlink()
        for name, content in example_overlay.items():
            (examples_dir / name).write_bytes(content)
    if card_links:
        for name, target in card_links.items():
            link = cards / name
            if link.exists() or link.is_symlink():
                link.unlink()
            os.symlink(target, link)
    if prepare_holdout:
        missing = cards / "H09.json"
        if missing.exists() or missing.is_symlink():
            missing.unlink()
        (cards / "H10.json").write_text('{"card_id": "H10", "classes": ["oak"], "bin_count": 4,\n')
        link = cards / "H11.json"
        if link.exists() or link.is_symlink():
            link.unlink()
        os.symlink("H11_real.json", link)
    (output / "stale.txt").write_text("leftover")
    (output / "old_dir").mkdir()
    (output / "old_dir" / "x.json").write_text("{}")
    os.mkfifo(output / "keep")
    os.symlink("stale.txt", output / "stale_link")
    os.symlink("old_dir", output / "dir_link")
    _chown_tree(base, SANDBOX_UID, SANDBOX_GID)
    if live_out is not None:
        _chown_tree(output, SANDBOX_UID, SANDBOX_GID)

    stdout_path = base / "stdout"
    stderr_path = base / "stderr"
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
        str(PROGRAM),
        "--cards",
        str(cards_arg),
        "--spec",
        str(spec_arg),
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

    leftover = sorted(path.name for path in output.iterdir())
    result = {
        "returncode": returncode,
        "stderr": stderr_path.read_text(errors="replace"),
        "folds_raw": (output / "folds.jsonl").read_bytes()
        if (output / "folds.jsonl").is_file()
        else b"",
        "summary_raw": (output / "summary.json").read_bytes()
        if (output / "summary.json").is_file()
        else b"",
        "entries": leftover,
        "cards_arg": str(cards_arg),
        "spec_arg": str(spec_arg),
        "out_arg": str(output),
    }
    shutil.rmtree(base)
    return result


def _run_fold(example_prefix: str = "replay_examples") -> dict[str, object]:
    if example_prefix not in _HOLDOUT_RESULTS:
        cards = {k[6:]: v for k, v in HOLDOUT_FILES.items() if k.startswith("cards/")}
        spec = {k[10:]: v for k, v in HOLDOUT_FILES.items() if k.startswith("scorecard/")}
        overlay = {
            Path(k).name: v
            for k, v in HOLDOUT_FILES.items()
            if k.startswith(example_prefix + "/")
        }
        assert overlay, example_prefix
        _HOLDOUT_RESULTS[example_prefix] = _execute_fold(
            cards,
            spec,
            example_overlay=overlay,
            prepare_holdout=True,
        )
    return _HOLDOUT_RESULTS[example_prefix]


def _run_staged() -> dict[str, object]:
    """Re-invoke fold.py with the required /app/cards and /app/scorecard paths."""
    if "staged" not in _HOLDOUT_RESULTS:
        for path in (APP, APP / "cards", APP / "scorecard", APP / "output"):
            os.chmod(path, 0o755)
            if path == APP:
                continue
            for child in path.rglob("*"):
                if child.is_symlink():
                    continue
                os.chmod(child, 0o755 if child.is_dir() else 0o644)
        _HOLDOUT_RESULTS["staged"] = _execute_fold(
            {},
            {},
            live_cards=Path("/app/cards"),
            live_spec=Path("/app/scorecard"),
            live_out=Path("/app/output"),
        )
    return _HOLDOUT_RESULTS["staged"]


def _staged_primary() -> dict[str, object]:
    """Execute fold.py on the staged pack; all primary-corpus checks use this result."""
    result = _run_staged()
    assert result["returncode"] == 0, result["stderr"]
    assert result["entries"] == ["folds.jsonl", "summary.json"]
    return result


def _staged_primary_rows() -> dict[str, dict]:
    return {row["card_id"]: row for row in _parse_jsonl(_staged_primary()["folds_raw"])}


def test_fold_script_exists():
    """Verification rebuild surface /app/fold.py must exist."""
    assert (APP / "fold.py").is_file()
    assert PROGRAM.is_file()
    assert os.access(PROGRAM, os.X_OK)
    assert (PROGRAM.parent / "scorecard" / "SMF_SPEC.md").is_file()


def test_primary_folds_match_expected():
    """Primary folds.jsonl must match the sealed SMF ledger, including a fresh staged run."""
    staged = _staged_primary()
    replayed = _parse_jsonl(staged["folds_raw"])
    for row in replayed:
        _assert_native_ints(row)
    _assert_folds_match(replayed, EXPECTED_PRIMARY_ROWS)
    delivered_ids = [row["card_id"] for row in replayed]
    assert delivered_ids == sorted(delivered_ids)
    assert delivered_ids[0] == "A"
    assert delivered_ids[-1] == "X"
    assert len(delivered_ids) == 86
    assert delivered_ids.count("C05") == 2
    c05_rows = [row for row in replayed if row["card_id"] == "C05"]
    assert sorted(row["weight"] for row in c05_rows) == [1, 2]
    assert c05_rows[0]["ap_micro_u"] == c05_rows[1]["ap_micro_u"]
    assert c05_rows[0]["ece_micro_u"] == c05_rows[1]["ece_micro_u"]
    assert "aaa_extra" not in delivered_ids
    assert "C18_real" not in delivered_ids
    assert replayed[0] == {
        "ap_micro_u": 1000000,
        "bin_count": 4,
        "card_id": "A",
        "class_count": 1,
        "disposition": "ok",
        "ece_micro_u": 0,
        "row_count": 1,
        "weight": 1,
    }
    assert len(replayed[0]["card_id"]) != 3
    assert PRIMARY_BY_ID["X"]["disposition"] == "reject_class"
    assert PRIMARY_BY_ID["X"] == {
        "ap_micro_u": 0,
        "bin_count": 0,
        "card_id": "X",
        "class_count": 0,
        "disposition": "reject_class",
        "ece_micro_u": 0,
        "row_count": 0,
        "weight": 0,
    }
    assert "bin_count" not in json.loads((APP / "cards" / "Q.json").read_text())
    assert PRIMARY_BY_ID["Q"]["disposition"] == "reject_schema"
    assert PRIMARY_BY_ID["Q"] == {
        "ap_micro_u": 0,
        "bin_count": 0,
        "card_id": "Q",
        "class_count": 0,
        "disposition": "reject_schema",
        "ece_micro_u": 0,
        "row_count": 0,
        "weight": 0,
    }
    assert "min_conf_bp" not in json.loads((APP / "cards" / "M.json").read_text())
    assert PRIMARY_BY_ID["M"]["disposition"] == "reject_schema"
    assert PRIMARY_BY_ID["M"] == {
        "ap_micro_u": 0,
        "bin_count": 0,
        "card_id": "M",
        "class_count": 0,
        "disposition": "reject_schema",
        "ece_micro_u": 0,
        "row_count": 0,
        "weight": 0,
    }
    assert staged["cards_arg"] == "/app/cards"
    assert staged["spec_arg"] == "/app/scorecard"
    assert staged["out_arg"] == "/app/output"
    assert _parse_summary(staged["summary_raw"]) == EXPECTED_PRIMARY_SUMMARY
    for keys in _fold_key_orders(staged["folds_raw"]):
        assert keys == FOLD_ROW_KEYS
    got = _parse_jsonl(DELIVERED_PRIMARY_FOLDS_RAW)
    for row in got:
        _assert_native_ints(row)
    _assert_folds_match(got, EXPECTED_PRIMARY_ROWS)
    _assert_folds_match(got, replayed)
    for keys in _fold_key_orders(DELIVERED_PRIMARY_FOLDS_RAW):
        assert keys == FOLD_ROW_KEYS


def test_primary_summary_matches_expected():
    """Primary summary.json must match sealed disposition totals."""
    staged = _staged_primary()
    got = _parse_summary(staged["summary_raw"])
    _assert_native_ints(got)
    assert got == EXPECTED_PRIMARY_SUMMARY
    delivered = _parse_summary(DELIVERED_PRIMARY_SUMMARY_RAW)
    _assert_native_ints(delivered)
    assert delivered == EXPECTED_PRIMARY_SUMMARY
    assert delivered == got
    top, nested = _summary_key_orders(DELIVERED_PRIMARY_SUMMARY_RAW)
    assert top == ["ap_pool_micro_u", "by_disposition", "card_count", "ece_pool_micro_u", "ok_count"]
    assert nested == DISPOSITION_ORDER


def test_summary_key_order():
    """summary.json must serialize top-level keys and by_disposition in SMF order."""
    staged = _staged_primary()
    top, nested = _summary_key_orders(staged["summary_raw"])
    assert top == ["ap_pool_micro_u", "by_disposition", "card_count", "ece_pool_micro_u", "ok_count"]
    assert nested == DISPOSITION_ORDER
    delivered_top, delivered_nested = _summary_key_orders(DELIVERED_PRIMARY_SUMMARY_RAW)
    assert delivered_top == ["ap_pool_micro_u", "by_disposition", "card_count", "ece_pool_micro_u", "ok_count"]
    assert delivered_nested == DISPOSITION_ORDER


def test_fold_row_keys_sorted():
    """Each folds.jsonl object must serialize keys in sorted order."""
    staged = _staged_primary()
    for keys in _fold_key_orders(staged["folds_raw"]):
        assert keys == FOLD_ROW_KEYS
    for keys in _fold_key_orders(DELIVERED_PRIMARY_FOLDS_RAW):
        assert keys == FOLD_ROW_KEYS


def test_output_dir_only_required_files():
    """/app/output must contain only folds.jsonl and summary.json after fold.py runs."""
    staged = _staged_primary()
    assert staged["entries"] == ["folds.jsonl", "summary.json"]
    assert [path.name for path in DELIVERED_OUTPUT_PATHS] == ["folds.jsonl", "summary.json"]


def test_interpolation_is_not_all_point():
    """Recovered interpolation on C02 must not equal the all_point millivalue."""
    rows = _staged_primary_rows()
    assert rows["C02"]["disposition"] == "ok"
    assert rows["C02"]["ap_micro_u"] == PRIMARY_BY_ID["C02"]["ap_micro_u"]
    assert rows["C02"]["ap_micro_u"] != 875000
    assert rows["C72"]["disposition"] == "ok"
    assert rows["C72"]["ap_micro_u"] == 500000
    assert rows["C72"]["ap_micro_u"] != 545454
    assert rows["C72"]["ap_micro_u"] != 458333
    assert rows["C72"]["ece_micro_u"] == 133333
    assert rows["C72"]["ece_micro_u"] != 333333


def test_joint_slice_ranking_not_mean():
    """C04 must fold one joint ranking, not the mean of per-slice average precision."""
    rows = _staged_primary_rows()
    assert rows["C04"]["disposition"] == "ok"
    assert rows["C04"]["row_count"] == 9
    assert rows["C04"]["ap_micro_u"] == PRIMARY_BY_ID["C04"]["ap_micro_u"]
    assert rows["C04"]["ap_micro_u"] != 750000


def test_prediction_tie_uses_class_name():
    """Staged prediction-tie member is earliest: C06 and C75 must match sealed ECE."""
    rows = _staged_primary_rows()
    assert rows["C06"]["disposition"] == "ok"
    assert rows["C06"]["ece_micro_u"] == PRIMARY_BY_ID["C06"]["ece_micro_u"]
    assert rows["C06"]["ece_micro_u"] != 275000
    assert rows["C75"]["disposition"] == "ok"
    assert rows["C75"]["ece_micro_u"] == 466667
    assert rows["C75"]["ece_micro_u"] != 333333


def test_confidence_floor_drops_rows():
    """min_conf_bp on C07 must drop rows and keep the inclusive floor boundary."""
    rows = _staged_primary_rows()
    assert rows["C07"]["disposition"] == "ok"
    assert rows["C07"]["row_count"] == 3
    assert rows["C07"]["row_count"] != 2
    assert rows["C07"]["row_count"] != 6
    assert json.loads((APP / "cards" / "C65.json").read_text())["min_conf_bp"] == 10000
    assert rows["C65"] == {
        "ap_micro_u": 1000000,
        "bin_count": 4,
        "card_id": "C65",
        "class_count": 1,
        "disposition": "ok",
        "ece_micro_u": 0,
        "row_count": 1,
        "weight": 1,
    }
    assert rows["C74"]["disposition"] == "ok"
    assert rows["C74"]["row_count"] == 3
    assert rows["C74"]["row_count"] != 2
    assert json.loads((APP / "cards" / "C74.json").read_text())["mode"] == "live"
    assert rows["C74"]["ece_micro_u"] == 50000
    assert rows["C74"]["ece_micro_u"] != 150000
    assert rows["C76"]["disposition"] == "ok"
    assert rows["C76"]["row_count"] == 1
    assert rows["C76"]["ece_micro_u"] == 100000
    assert json.loads((APP / "cards" / "C77.json").read_text())["mode"] == "frozen"
    assert rows["C77"]["disposition"] == "ok"
    assert rows["C77"]["row_count"] == 3
    assert rows["C77"]["weight"] == 3
    assert rows["C77"]["ece_micro_u"] == 150000
    assert json.loads((APP / "cards" / "C02.json").read_text())["mode"] == "frozen"
    assert rows["C02"]["disposition"] == "ok"
    assert rows["C02"]["ap_micro_u"] == 878788
    assert rows["C02"]["ap_micro_u"] != 1000000
    assert rows["C02"]["ece_micro_u"] == 300020
    assert json.loads((APP / "cards" / "C80.json").read_text())["mode"] == "frozen"
    assert rows["C80"]["disposition"] == "ok"
    assert rows["C80"]["ece_micro_u"] == 600000
    assert rows["C80"]["ece_micro_u"] != 400000
    assert json.loads((APP / "cards" / "C81.json").read_text())["mode"] == "frozen"
    assert rows["C81"]["disposition"] == "ok"
    assert rows["C81"]["ap_micro_u"] == 583333
    assert rows["C81"]["ap_micro_u"] != 590909
    assert json.loads((APP / "cards" / "C78.json").read_text())["classes"] == ["pine", "oak"]
    assert rows["C78"]["disposition"] == "ok"
    assert rows["C78"]["ap_micro_u"] == 1000000
    assert rows["C79"]["disposition"] == "reject_schema"
    assert json.loads((APP / "cards" / "C79.json").read_text())["mode"] == "thawed"
    staged_summary = _parse_summary(_staged_primary()["summary_raw"])
    assert staged_summary["ap_pool_micro_u"] == 885241
    assert staged_summary["ece_pool_micro_u"] == 181088
    assert staged_summary["ok_count"] == 27


def test_empty_positive_class_omitted():
    """C13 must omit elm from the macro mean because it has no positives."""
    rows = _staged_primary_rows()
    assert rows["C13"]["disposition"] == "ok"
    assert rows["C13"]["class_count"] == 3
    assert rows["C13"]["ap_micro_u"] == PRIMARY_BY_ID["C13"]["ap_micro_u"]
    assert rows["C13"]["ap_micro_u"] != 522727


def test_schema_class_fold_dispositions():
    """Schema, class, fold, missing, symlink, and non-JSON cards must reject as specified.

    C34 refuses a classes array whose element is an empty string (reject_schema).
    C35 refuses label-before-bin precedence (label fir and bin_count 3 is reject_class).
    C36 refuses schema-before-fold (empty slices and bin_count 3 is reject_schema).
    X refuses duplicate classes immediately even when slices is an empty array (reject_class).
    C37 refuses schema-before-label (missing p_bp and label pine is reject_schema).
    C38 refuses label-before-min_conf (label fir and min_conf_bp 10001 is reject_class).
    C39 refuses a mass outside 0..10000 even when the row sums to 10000.
    C40 refuses an empty class name before the duplicate-name exception.
    C41 refuses a non-string classes element. C42 refuses a non-string label.
    C43 refuses a non-integer bin_count. C44 refuses a boolean mass.
    C45 refuses a missing slice_id. C46 refuses a non-string example_id.
    C48 refuses an empty example_id. C60 refuses an integer slice_id.
    C66 refuses a non-object slice. C67 refuses a non-object row.
    C68 refuses a present non-object p_bp. C69 refuses a missing label.
    C70 refuses a missing document card_id.
    C71 refuses in-range masses that sum above 10000.
    C73 refuses the non-JSON token -Infinity.
    Q refuses a missing bin_count field (reject_schema), not a silent default of 4.
    M refuses a missing min_conf_bp field (reject_schema), not a silent default of 0.
    """
    rows = _staged_primary_rows()
    staged_rows = rows
    schema_ids = (
        "C08",
        "C11",
        "C12",
        "C16",
        "C17",
        "C18",
        "C19",
        "C20",
        "C21",
        "C24",
        "C25",
        "C26",
        "C28",
        "C29",
        "C30",
        "C32",
        "C33",
        "C34",
        "C36",
        "C37",
        "C39",
        "C40",
        "C41",
        "C42",
        "C43",
        "C44",
        "C45",
        "C46",
        "C48",
        "C50",
        "C52",
        "C54",
        "C55",
        "C56",
        "C57",
        "C59",
        "C60",
        "C63",
        "C64",
        "C66",
        "C67",
        "C68",
        "C69",
        "C70",
        "C71",
        "C73",
        "C79",
        "Q",
        "M",
    )
    class_ids = ("C09", "C22", "C23", "C35", "C38", "X")
    fold_ids = ("C10", "C14", "C27", "C49")
    for card_id in schema_ids:
        assert rows[card_id]["disposition"] == "reject_schema", card_id
    for card_id in class_ids:
        assert rows[card_id]["disposition"] == "reject_class", card_id
    for card_id in fold_ids:
        assert rows[card_id]["disposition"] == "reject_fold", card_id
    for card_id in (*schema_ids, *class_ids, *fold_ids):
        assert rows[card_id]["ap_micro_u"] == 0
        assert rows[card_id]["ece_micro_u"] == 0
        assert rows[card_id]["row_count"] == 0
        assert rows[card_id]["class_count"] == 0
    assert json.loads((APP / "cards" / "X.json").read_text())["classes"] == ["oak", "oak"]
    assert json.loads((APP / "cards" / "X.json").read_text())["slices"] == []
    assert rows["X"]["disposition"] == "reject_class"
    c34 = json.loads((APP / "cards" / "C34.json").read_text())
    assert c34["classes"] == ["", "oak"]
    assert c34["bin_count"] == 4
    assert c34["slices"][0]["rows"][0]["label"] == "oak"
    assert c34["slices"][0]["rows"][0]["p_bp"] == {"": 2000, "oak": 8000}
    c35 = json.loads((APP / "cards" / "C35.json").read_text())
    assert c35["classes"] == ["oak", "pine"]
    assert c35["bin_count"] == 3
    assert c35["slices"][0]["rows"][0]["label"] == "fir"
    assert c35["slices"][0]["rows"][0]["label"] not in c35["classes"]
    c36 = json.loads((APP / "cards" / "C36.json").read_text())
    assert c36["slices"] == []
    assert c36["bin_count"] == 3
    assert c36["classes"] == ["oak", "pine"]
    c37 = json.loads((APP / "cards" / "C37.json").read_text())
    assert "p_bp" not in c37["slices"][0]["rows"][0]
    assert c37["slices"][0]["rows"][0]["label"] == "pine"
    assert c37["slices"][0]["rows"][0]["label"] not in c37["classes"]
    assert c37["bin_count"] == 4
    c39 = json.loads((APP / "cards" / "C39.json").read_text())
    assert c39["slices"][0]["rows"][0]["p_bp"] == {"oak": 10001, "pine": -1}
    assert c39["bin_count"] == 4
    c49 = json.loads((APP / "cards" / "C49.json").read_text())
    assert c49["min_conf_bp"] == -1
    assert c49["bin_count"] == 4
    assert c49["slices"][0]["rows"][0]["label"] == "oak"
    c50 = json.loads((APP / "cards" / "C50.json").read_text())
    assert c50["min_conf_bp"] is True
    assert c50["slices"][0]["rows"][0]["label"] == "oak"
    assert staged_rows["C34"]["disposition"] == "reject_schema"
    assert staged_rows["C35"]["disposition"] == "reject_class"
    assert staged_rows["C36"]["disposition"] == "reject_schema"
    assert staged_rows["C37"]["disposition"] == "reject_schema"
    assert staged_rows["C38"]["disposition"] == "reject_class"
    assert staged_rows["X"]["disposition"] == "reject_class"
    assert staged_rows["Q"]["disposition"] == "reject_schema"
    assert "bin_count" not in json.loads((APP / "cards" / "Q.json").read_text())
    assert staged_rows["M"]["disposition"] == "reject_schema"
    assert "min_conf_bp" not in json.loads((APP / "cards" / "M.json").read_text())
    assert staged_rows["C40"]["disposition"] == "reject_schema"
    assert staged_rows["C43"]["disposition"] == "reject_schema"
    assert staged_rows["C47"]["disposition"] == "ok"
    assert staged_rows["C47"]["ap_micro_u"] == PRIMARY_BY_ID["C47"]["ap_micro_u"]
    assert staged_rows["C49"]["disposition"] == "reject_fold"
    assert staged_rows["C50"]["disposition"] == "reject_schema"
    c51 = json.loads((APP / "cards" / "C51.json").read_text())
    assert c51["classes"] == ["a", "b", "c", "d", "oak"]
    assert c51["bin_count"] == 4
    assert rows["C51"]["disposition"] == "ok"
    assert rows["C51"]["class_count"] == 5
    assert rows["C51"]["row_count"] == 1
    assert rows["C51"]["ap_micro_u"] == 1000000
    assert rows["C51"]["ece_micro_u"] == 400000
    assert json.loads((APP / "cards" / "C52.json").read_text()) == []
    assert rows["C52"]["disposition"] == "reject_schema"
    c53 = json.loads((APP / "cards" / "C53.json").read_text())
    assert [sl["slice_id"] for sl in c53["slices"]] == ["s0", "s0"]
    assert rows["C53"]["disposition"] == "ok"
    assert rows["C53"]["row_count"] == 2
    assert rows["C53"]["class_count"] == 1
    assert rows["C53"]["ap_micro_u"] == 1000000
    assert rows["C53"]["ece_micro_u"] == 0
    assert b"NaN" in (APP / "cards" / "C54.json").read_bytes()
    assert rows["C54"]["disposition"] == "reject_schema"
    c55 = json.loads((APP / "cards" / "C55.json").read_text())
    assert c55["min_conf_bp"] == 8000
    assert sum(c55["slices"][0]["rows"][1]["p_bp"].values()) == 9999
    assert rows["C55"]["disposition"] == "reject_schema"
    c56 = json.loads((APP / "cards" / "C56.json").read_text())
    assert c56["classes"] == ["oak"]
    assert c56["slices"][0]["rows"][0]["p_bp"] == {"oak": 10000, "pine": 0}
    assert rows["C56"]["disposition"] == "reject_schema"
    c57 = json.loads((APP / "cards" / "C57.json").read_text())
    assert c57["min_conf_bp"] == 8000
    assert [row["example_id"] for row in c57["slices"][0]["rows"]] == ["e", "e"]
    assert rows["C57"]["disposition"] == "reject_schema"
    c59 = json.loads((APP / "cards" / "C59.json").read_text())
    assert len(c59["classes"]) == 8
    assert rows["C59"]["disposition"] == "reject_schema"
    assert rows["C59"]["weight"] == 0
    assert rows["C59"]["ap_micro_u"] == 0
    c60 = json.loads((APP / "cards" / "C60.json").read_text())
    assert c60["slices"][0]["slice_id"] == 7
    assert rows["C60"]["disposition"] == "reject_schema"
    assert staged_rows["C51"]["class_count"] == 5
    assert staged_rows["C59"]["disposition"] == "reject_schema"
    assert staged_rows["C60"]["disposition"] == "reject_schema"
    c61 = json.loads((APP / "cards" / "C61.json").read_text())
    assert len(c61["classes"]) == 64
    assert rows["C61"]["disposition"] == "ok"
    assert rows["C61"]["class_count"] == 64
    assert rows["C61"]["row_count"] == 1
    assert rows["C61"]["ap_micro_u"] == 1000000
    assert rows["C61"]["ece_micro_u"] == 0
    c62 = json.loads((APP / "cards" / "C62.json").read_text())
    assert len(c62["slices"][0]["rows"]) == 256
    assert rows["C62"]["disposition"] == "ok"
    assert rows["C62"]["row_count"] == 256
    assert rows["C62"] == PRIMARY_BY_ID["C62"]
    assert staged_rows["C61"]["class_count"] == 64
    assert staged_rows["C62"]["row_count"] == 256
    c63 = json.loads((APP / "cards" / "C63.json").read_text())
    assert type(c63["slices"][0]["rows"][0]["p_bp"]["oak"]) is float
    assert rows["C63"]["disposition"] == "reject_schema"
    assert json.loads((APP / "cards" / "C64.json").read_text())["bin_count"] is True
    assert rows["C64"]["disposition"] == "reject_schema"
    assert staged_rows["C63"]["disposition"] == "reject_schema"
    assert staged_rows["C64"]["disposition"] == "reject_schema"
    assert json.loads((APP / "cards" / "C66.json").read_text())["slices"] == [None]
    assert rows["C66"]["disposition"] == "reject_schema"
    assert staged_rows["C66"]["disposition"] == "reject_schema"
    assert json.loads((APP / "cards" / "C67.json").read_text())["slices"][0]["rows"] == [None]
    assert rows["C67"]["disposition"] == "reject_schema"
    assert staged_rows["C67"]["disposition"] == "reject_schema"
    assert json.loads((APP / "cards" / "C68.json").read_text())["slices"][0]["rows"][0]["p_bp"] is None
    assert rows["C68"]["disposition"] == "reject_schema"
    assert staged_rows["C68"]["disposition"] == "reject_schema"
    assert "label" not in json.loads((APP / "cards" / "C69.json").read_text())["slices"][0]["rows"][0]
    assert rows["C69"]["disposition"] == "reject_schema"
    assert staged_rows["C69"]["disposition"] == "reject_schema"
    assert "card_id" not in json.loads((APP / "cards" / "C70.json").read_text())
    assert rows["C70"]["disposition"] == "reject_schema"
    assert staged_rows["C70"]["disposition"] == "reject_schema"
    c71 = json.loads((APP / "cards" / "C71.json").read_text())
    assert c71["slices"][0]["rows"][0]["p_bp"] == {"oak": 5001, "pine": 5000}
    assert sum(c71["slices"][0]["rows"][0]["p_bp"].values()) == 10001
    assert rows["C71"]["disposition"] == "reject_schema"
    assert staged_rows["C71"]["disposition"] == "reject_schema"
    assert b"-Infinity" in (APP / "cards" / "C73.json").read_bytes()
    assert rows["C73"]["disposition"] == "reject_schema"
    assert staged_rows["C73"]["disposition"] == "reject_schema"
    assert staged_rows["C52"]["disposition"] == "reject_schema"
    assert staged_rows["C53"]["row_count"] == 2
    assert staged_rows["C54"]["disposition"] == "reject_schema"
    assert staged_rows["C55"]["disposition"] == "reject_schema"
    assert staged_rows["C56"]["disposition"] == "reject_schema"
    assert staged_rows["C57"]["disposition"] == "reject_schema"


def test_last_bin_and_bin_twenty():
    """Confidence 10000 lands in the last bin, and C01 must use bin_count 20."""
    rows = _staged_primary_rows()
    assert rows["C05"]["disposition"] == "ok"
    assert rows["C05"]["bin_count"] == 5
    assert rows["C05"]["ece_micro_u"] == PRIMARY_BY_ID["C05"]["ece_micro_u"]
    assert rows["C01"]["disposition"] == "ok"
    assert rows["C01"]["bin_count"] == 20
    assert rows["C01"]["ece_micro_u"] == PRIMARY_BY_ID["C01"]["ece_micro_u"]
    assert rows["C31"]["disposition"] == "ok"
    assert rows["C31"]["ece_micro_u"] == 200000
    assert rows["C31"]["ece_micro_u"] != 199900
    c58 = json.loads((APP / "cards" / "C58.json").read_text())
    c58_rows = c58["slices"][0]["rows"]
    assert len(c58_rows) == 32
    assert sum(1 for row in c58_rows if row["label"] == "oak") == 1
    assert rows["C58"]["disposition"] == "ok"
    assert rows["C58"]["row_count"] == 32
    assert rows["C58"]["ece_micro_u"] == 187600
    assert rows["C58"]["ece_micro_u"] != 768800
    assert rows["C58"]["ece_micro_u"] != 187500


def test_holdout_replay_folds():
    """Re-running fold.py on sealed holdout cards must match holdout folds."""
    result = _run_fold()
    assert result["returncode"] == 0, result["stderr"]
    assert result["entries"] == ["folds.jsonl", "summary.json"]
    got = _parse_jsonl(result["folds_raw"])
    for row in got:
        _assert_native_ints(row)
    _assert_folds_match(got, EXPECTED_HOLDOUT_ROWS)
    for keys in _fold_key_orders(result["folds_raw"]):
        assert keys == FOLD_ROW_KEYS
    delivered_ids = [row["card_id"] for row in got]
    assert delivered_ids == sorted(delivered_ids)
    assert delivered_ids[0] == "AB"
    assert delivered_ids[-1] == "Y"
    assert len(delivered_ids) == 101
    assert delivered_ids.count("H03") == 2
    assert delivered_ids.count("H41") == 3
    h03_rows = [row for row in got if row["card_id"] == "H03"]
    assert sorted(row["weight"] for row in h03_rows) == [1, 2]
    assert h03_rows[0]["ap_micro_u"] == h03_rows[1]["ap_micro_u"]
    manifest_records = [line for line in HOLDOUT_FILES["cards/manifest.jsonl"].split(b"\n") if line]
    assert len(manifest_records) == 101
    assert "aaa_extra" not in delivered_ids
    assert "H11_real" not in delivered_ids
    assert got[0] == {
        "ap_micro_u": 1000000,
        "bin_count": 4,
        "card_id": "AB",
        "class_count": 1,
        "disposition": "ok",
        "ece_micro_u": 0,
        "row_count": 1,
        "weight": 1,
    }
    assert len(got[0]["card_id"]) != 3
    assert HOLDOUT_BY_ID["Y"]["disposition"] == "reject_class"
    assert HOLDOUT_BY_ID["Y"] == {
        "ap_micro_u": 0,
        "bin_count": 0,
        "card_id": "Y",
        "class_count": 0,
        "disposition": "reject_class",
        "ece_micro_u": 0,
        "row_count": 0,
        "weight": 0,
    }
    assert "bin_count" not in json.loads(HOLDOUT_FILES["cards/R.json"])
    assert HOLDOUT_BY_ID["R"]["disposition"] == "reject_schema"
    assert HOLDOUT_BY_ID["R"] == {
        "ap_micro_u": 0,
        "bin_count": 0,
        "card_id": "R",
        "class_count": 0,
        "disposition": "reject_schema",
        "ece_micro_u": 0,
        "row_count": 0,
        "weight": 0,
    }
    assert "min_conf_bp" not in json.loads(HOLDOUT_FILES["cards/N.json"])
    assert HOLDOUT_BY_ID["N"]["disposition"] == "reject_schema"
    assert HOLDOUT_BY_ID["N"] == {
        "ap_micro_u": 0,
        "bin_count": 0,
        "card_id": "N",
        "class_count": 0,
        "disposition": "reject_schema",
        "ece_micro_u": 0,
        "row_count": 0,
        "weight": 0,
    }


def test_holdout_replay_summary():
    """Re-running fold.py on sealed holdout cards must match holdout summary."""
    result = _run_fold()
    assert result["returncode"] == 0, result["stderr"]
    got = _parse_summary(result["summary_raw"])
    assert got == EXPECTED_HOLDOUT_SUMMARY
    top, nested = _summary_key_orders(result["summary_raw"])
    assert top == ["ap_pool_micro_u", "by_disposition", "card_count", "ece_pool_micro_u", "ok_count"]
    assert nested == DISPOSITION_ORDER


def test_holdout_geometry_and_floor():
    """Holdout geometry, filesystem rejects, and --spec interpolant recovery must fold."""
    result = _run_fold()
    assert result["returncode"] == 0, result["stderr"]
    rows = {r["card_id"]: r for r in _parse_jsonl(result["folds_raw"])}
    assert rows["H01"]["disposition"] == "ok"
    assert rows["H01"]["class_count"] == 4
    assert rows["H01"]["ap_micro_u"] == HOLDOUT_BY_ID["H01"]["ap_micro_u"]
    assert rows["H01"]["ece_micro_u"] == 262500
    assert rows["H01"]["ece_micro_u"] != 387500
    assert rows["H01"]["weight"] == 2
    assert rows["H01"]["ap_micro_u"] == 966667
    assert rows["AB"]["disposition"] == "ok"
    assert rows["AB"]["ap_micro_u"] == 1000000
    assert rows["AB"]["ece_micro_u"] == 0
    assert rows["AB"]["row_count"] == 1
    assert len(rows["AB"]["card_id"]) != 3
    assert rows["Y"]["disposition"] == "reject_class"
    assert rows["Y"]["ap_micro_u"] == 0
    assert json.loads(HOLDOUT_FILES["cards/Y.json"])["classes"] == ["pine", "pine"]
    assert json.loads(HOLDOUT_FILES["cards/Y.json"])["slices"] == []
    assert rows["R"]["disposition"] == "reject_schema"
    assert "bin_count" not in json.loads(HOLDOUT_FILES["cards/R.json"])
    assert rows["N"]["disposition"] == "reject_schema"
    assert "min_conf_bp" not in json.loads(HOLDOUT_FILES["cards/N.json"])
    assert rows["H03"]["bin_count"] == 16
    assert rows["H03"]["ece_micro_u"] == HOLDOUT_BY_ID["H03"]["ece_micro_u"]
    assert rows["H04"]["disposition"] == "reject_fold"
    assert rows["H06"]["row_count"] == 1
    assert rows["H08"]["disposition"] == "reject_schema"
    assert rows["H09"]["disposition"] == "reject_schema"
    assert rows["H10"]["disposition"] == "reject_schema"
    assert HOLDOUT_FILES["cards/H10.json"].startswith(b'{"card_id": "H10"')
    assert HOLDOUT_FILES["cards/H10.json"].strip() != STAGED_CARD_FILES["C17.json"].strip()
    assert rows["H11"]["disposition"] == "reject_schema"
    assert rows["H12"]["disposition"] == "ok"
    assert rows["H12"]["row_count"] == 7
    assert rows["H12"]["bin_count"] == 8
    assert rows["H12"]["ap_micro_u"] == HOLDOUT_BY_ID["H12"]["ap_micro_u"]
    assert rows["H12"]["ap_micro_u"] != PRIMARY_BY_ID["C02"]["ap_micro_u"]
    assert rows["H12"]["ap_micro_u"] != 875000
    assert rows["H12"]["ap_micro_u"] != 878788
    assert rows["H12"]["ece_micro_u"] == 540000
    assert rows["H12"]["ece_micro_u"] != 248571
    holdout_manifest = HOLDOUT_FILES["cards/manifest.jsonl"].split(b"\n", 1)[0]
    assert json.loads(holdout_manifest)["card_id"] == "H12"
    h13 = json.loads(HOLDOUT_FILES["cards/H13.json"])
    assert h13["classes"] == ["", "oak"]
    assert h13["bin_count"] == 4
    assert h13["slices"][0]["rows"][0]["p_bp"] == {"": 0, "oak": 10000}
    assert rows["H13"]["disposition"] == "reject_schema"
    h14 = json.loads(HOLDOUT_FILES["cards/H14.json"])
    assert h14["bin_count"] == 1
    assert h14["slices"][0]["rows"][0]["label"] == "fir"
    assert h14["slices"][0]["rows"][0]["label"] not in h14["classes"]
    assert rows["H14"]["disposition"] == "reject_class"
    h15 = json.loads(HOLDOUT_FILES["cards/H15.json"])
    assert h15["slices"] == []
    assert h15["bin_count"] == 9
    assert rows["H15"]["disposition"] == "reject_schema"
    h16 = json.loads(HOLDOUT_FILES["cards/H16.json"])
    assert "p_bp" not in h16["slices"][0]["rows"][0]
    assert h16["slices"][0]["rows"][0]["label"] == "fir"
    assert rows["H16"]["disposition"] == "reject_schema"
    assert json.loads(HOLDOUT_FILES["cards/H17.json"])["min_conf_bp"] == -1
    assert rows["H17"]["disposition"] == "reject_class"
    assert json.loads(HOLDOUT_FILES["cards/H19.json"])["classes"] == ["oak", "", "oak"]
    assert rows["H19"]["disposition"] == "reject_schema"
    assert json.loads(HOLDOUT_FILES["cards/H22.json"])["min_conf_bp"] == 0.0
    assert rows["H22"]["disposition"] == "reject_schema"
    assert "rows" not in json.loads(HOLDOUT_FILES["cards/H24.json"])["slices"][0]
    assert json.loads(HOLDOUT_FILES["cards/H24.json"])["bin_count"] == 3
    assert rows["H24"]["disposition"] == "reject_schema"
    assert rows["H24"]["disposition"] != "reject_fold"
    assert rows["H24"] == {
        "ap_micro_u": 0,
        "bin_count": 0,
        "card_id": "H24",
        "class_count": 0,
        "disposition": "reject_schema",
        "ece_micro_u": 0,
        "row_count": 0,
        "weight": 0,
    }
    assert json.loads(HOLDOUT_FILES["cards/H26.json"])["slices"][0]["rows"][0]["example_id"] == ""
    assert json.loads(HOLDOUT_FILES["cards/H26.json"])["classes"] == ["fir"]
    assert rows["H26"]["disposition"] == "reject_schema"
    assert json.loads(HOLDOUT_FILES["cards/H27.json"])["min_conf_bp"] == -1
    assert rows["H27"]["disposition"] == "reject_fold"
    assert json.loads(HOLDOUT_FILES["cards/H28.json"])["min_conf_bp"] is True
    assert json.loads(HOLDOUT_FILES["cards/H28.json"])["classes"] == ["pine"]
    assert rows["H28"]["disposition"] == "reject_schema"
    assert json.loads(HOLDOUT_FILES["cards/H29.json"])["classes"] == ["ash", "birch", "elm", "oak", "pine"]
    assert rows["H29"]["disposition"] == "reject_schema"
    assert rows["H29"]["weight"] == 0
    assert rows["H29"]["ap_micro_u"] == 0
    assert json.loads(HOLDOUT_FILES["cards/H30.json"]) is None
    assert rows["H30"]["disposition"] == "reject_schema"
    h31 = json.loads(HOLDOUT_FILES["cards/H31.json"])
    assert [sl["slice_id"] for sl in h31["slices"]] == ["s0", "s0"]
    assert rows["H31"]["disposition"] == "ok"
    assert rows["H31"]["row_count"] == 2
    assert rows["H31"]["class_count"] == 2
    assert rows["H31"]["ece_micro_u"] == 225000
    assert b"Infinity" in HOLDOUT_FILES["cards/H32.json"]
    assert rows["H32"]["disposition"] == "reject_schema"
    h33 = json.loads(HOLDOUT_FILES["cards/H33.json"])
    assert h33["min_conf_bp"] == 8500
    assert sum(h33["slices"][0]["rows"][1]["p_bp"].values()) == 9999
    assert rows["H33"]["disposition"] == "reject_schema"
    h34 = json.loads(HOLDOUT_FILES["cards/H34.json"])
    assert h34["classes"] == ["pine"]
    assert h34["slices"][0]["rows"][0]["p_bp"] == {"mist": 0, "pine": 10000}
    assert rows["H34"]["disposition"] == "reject_schema"
    h35 = json.loads(HOLDOUT_FILES["cards/H35.json"])
    assert h35["min_conf_bp"] == 6000
    assert [row["example_id"] for row in h35["slices"][0]["rows"]] == ["dup", "dup"]
    assert rows["H35"]["disposition"] == "reject_schema"
    h36_rows = [row for sl in json.loads(HOLDOUT_FILES["cards/H36.json"])["slices"] for row in sl["rows"]]
    assert len(h36_rows) == 32
    assert sum(1 for row in h36_rows if row["label"] == "fir") == 9
    assert rows["H36"]["disposition"] == "ok"
    assert rows["H36"]["ap_micro_u"] == 421387
    assert rows["H36"]["ece_micro_u"] == 543750
    assert rows["H36"]["ece_micro_u"] != 318800
    assert rows["H36"]["ece_micro_u"] != 257812
    assert rows["H36"]["ece_micro_u"] != 32900
    assert len(json.loads(HOLDOUT_FILES["cards/H37.json"])["classes"]) == 11
    assert rows["H37"]["disposition"] == "ok"
    assert rows["H37"]["class_count"] == 11
    assert json.loads(HOLDOUT_FILES["cards/H38.json"])["slices"][0]["slice_id"] == ["grove"]
    assert rows["H38"]["disposition"] == "reject_schema"
    assert len(json.loads(HOLDOUT_FILES["cards/H73.json"])["classes"]) == 200
    assert rows["H73"]["disposition"] == "ok"
    assert rows["H73"]["class_count"] == 200
    assert rows["H73"]["row_count"] == 3
    assert rows["H73"]["ece_micro_u"] == 300000
    assert rows["H73"]["ece_micro_u"] != 33333
    h74 = json.loads(HOLDOUT_FILES["cards/H74.json"])
    assert sum(len(sl["rows"]) for sl in h74["slices"]) == 600
    assert rows["H74"]["disposition"] == "ok"
    assert rows["H74"]["row_count"] == 282
    assert rows["H74"]["row_count"] != 600
    assert rows["H74"] == HOLDOUT_BY_ID["H74"]
    h75 = json.loads(HOLDOUT_FILES["cards/H75.json"])
    assert type(h75["slices"][0]["rows"][1]["p_bp"]["ash"]) is float
    assert rows["H75"]["disposition"] == "reject_schema"
    assert json.loads(HOLDOUT_FILES["cards/H76.json"])["bin_count"] is False
    assert rows["H76"]["disposition"] == "reject_schema"
    assert json.loads(HOLDOUT_FILES["cards/H77.json"])["min_conf_bp"] == 10000
    assert rows["H77"]["disposition"] == "ok"
    assert rows["H77"]["row_count"] == 1
    assert rows["H77"]["ece_micro_u"] == 0
    assert rows["H77"]["ece_micro_u"] != 500000
    assert json.loads(HOLDOUT_FILES["cards/H78.json"])["slices"] == [7]
    assert rows["H78"]["disposition"] == "reject_schema"
    assert json.loads(HOLDOUT_FILES["cards/H79.json"])["slices"][0]["rows"] == [7]
    assert rows["H79"]["disposition"] == "reject_schema"
    assert json.loads(HOLDOUT_FILES["cards/H80.json"])["slices"][0]["rows"][0]["p_bp"] == 7
    assert rows["H80"]["disposition"] == "reject_schema"
    assert "label" not in json.loads(HOLDOUT_FILES["cards/H81.json"])["slices"][0]["rows"][0]
    assert rows["H81"]["disposition"] == "reject_schema"
    assert "card_id" not in json.loads(HOLDOUT_FILES["cards/H82.json"])
    assert rows["H82"]["disposition"] == "reject_schema"
    h83 = json.loads(HOLDOUT_FILES["cards/H83.json"])
    assert h83["slices"][0]["rows"][0]["p_bp"] == {"ash": 4000, "elm": 3000, "fir": 3001}
    assert sum(h83["slices"][0]["rows"][0]["p_bp"].values()) == 10001
    assert rows["H83"]["disposition"] == "reject_schema"
    assert rows["H84"]["disposition"] == "ok"
    assert rows["H84"]["ece_micro_u"] == 475000
    assert rows["H84"]["ece_micro_u"] != 150050
    assert rows["H84"]["ece_micro_u"] != 350000
    assert b"-Infinity" in HOLDOUT_FILES["cards/H85.json"]
    assert rows["H85"]["disposition"] == "reject_schema"
    assert rows["H86"]["disposition"] == "ok"
    assert rows["H86"]["row_count"] == 2
    assert rows["H86"]["row_count"] != 3
    assert rows["H86"]["ece_micro_u"] == 175000
    assert rows["H86"]["ece_micro_u"] != 150000
    assert rows["H87"]["disposition"] == "ok"
    assert rows["H87"]["ece_micro_u"] == 333333
    assert rows["H87"]["ece_micro_u"] != 466667
    assert rows["H88"]["disposition"] == "reject_fold"
    assert rows["H88"] == {
        "ap_micro_u": 0,
        "bin_count": 0,
        "card_id": "H88",
        "class_count": 0,
        "disposition": "reject_fold",
        "ece_micro_u": 0,
        "row_count": 0,
        "weight": 0,
    }
    assert rows["H89"]["disposition"] == "ok"
    assert rows["H89"]["row_count"] == 3
    assert rows["H89"]["weight"] == 5
    assert rows["H89"]["ece_micro_u"] == 150000
    assert json.loads(HOLDOUT_FILES["cards/H89.json"])["mode"] == "frozen"
    assert json.loads(HOLDOUT_FILES["cards/H86.json"])["mode"] == "live"
    assert rows["H86"]["ece_micro_u"] == 175000
    assert json.loads(HOLDOUT_FILES["cards/H92.json"])["mode"] == "frozen"
    assert rows["H92"]["disposition"] == "ok"
    assert rows["H92"]["ece_micro_u"] == 600000
    assert rows["H92"]["ece_micro_u"] != 400000
    assert json.loads(HOLDOUT_FILES["cards/H93.json"])["mode"] == "frozen"
    assert rows["H93"]["disposition"] == "ok"
    assert rows["H93"]["ap_micro_u"] == 583333
    assert rows["H93"]["ap_micro_u"] != 541667
    assert json.loads(HOLDOUT_FILES["cards/H94.json"])["mode"] == "frozen"
    assert rows["H94"]["disposition"] == "ok"
    assert rows["H94"]["ap_micro_u"] == 875000
    assert rows["H94"]["ap_micro_u"] != 878788
    assert rows["H94"]["ap_micro_u"] != 1000000
    assert rows["H90"]["disposition"] == "ok"
    assert json.loads(HOLDOUT_FILES["cards/H90.json"])["classes"] == ["pine", "oak"]
    assert rows["H91"]["disposition"] == "reject_schema"
    assert json.loads(HOLDOUT_FILES["cards/H91.json"])["mode"] == "thawed"
    hold_summary = _parse_summary(result["summary_raw"])
    assert hold_summary["ap_pool_micro_u"] == 784935
    assert hold_summary["ece_pool_micro_u"] == 365568
    for card_id in (
        "H13",
        "H14",
        "H15",
        "H16",
        "H17",
        "H18",
        "H19",
        "H20",
        "H21",
        "H22",
        "H23",
        "H24",
        "H25",
        "H26",
        "H27",
        "H28",
        "H30",
        "H32",
        "H33",
        "H34",
        "H35",
        "H38",
        "H75",
        "H76",
        "H78",
        "H79",
        "H80",
        "H81",
        "H82",
        "H83",
        "H85",
    ):
        assert rows[card_id]["ap_micro_u"] == 0
        assert rows[card_id]["row_count"] == 0
        assert rows[card_id]["class_count"] == 0


def test_replay_recovers_interpolant_from_spec():
    """Replay must recover all_point, voc11, and voc101 from independent planted --spec example sets."""
    assert "worked_examples/case.json" not in STAGED_SPEC_FILES
    assert "worked_examples/ledger.json" in STAGED_SPEC_FILES
    assert "replay_examples/case.json" not in HOLDOUT_FILES
    assert "voc101_examples/case.json" not in HOLDOUT_FILES
    assert json.loads(HOLDOUT_FILES["replay_examples/batch.json"])["card"]["card_id"] == "ECEX"
    assert json.loads(HOLDOUT_FILES["voc101_examples/notes.json"])["card"]["card_id"] == "ECEX"
    replay_card = json.loads(HOLDOUT_FILES["replay_examples/batch.json"])
    voc_card = json.loads(HOLDOUT_FILES["voc101_examples/notes.json"])
    assert replay_card["card"]["slices"][0]["rows"][0]["p_bp"] == {"oak": 1000, "pine": 9000}
    assert voc_card["card"]["slices"][0]["rows"][0]["p_bp"] == {"oak": 1000, "pine": 9000}
    assert replay_card["ap_micro_u"] == 666667
    assert replay_card["ece_micro_u"] == 487500
    assert voc_card["ap_micro_u"] == 709571
    assert voc_card["ece_micro_u"] == 187500
    all_point = _run_fold("replay_examples")
    voc101 = _run_fold("voc101_examples")
    assert all_point["returncode"] == 0, all_point["stderr"]
    assert voc101["returncode"] == 0, voc101["stderr"]
    assert all_point["entries"] == ["folds.jsonl", "summary.json"]
    assert voc101["entries"] == ["folds.jsonl", "summary.json"]
    _assert_folds_match(_parse_jsonl(all_point["folds_raw"]), EXPECTED_HOLDOUT_ROWS)
    _assert_folds_match(_parse_jsonl(voc101["folds_raw"]), EXPECTED_VOC101_ROWS)
    assert _parse_summary(voc101["summary_raw"]) == EXPECTED_VOC101_SUMMARY
    for row in _parse_jsonl(voc101["folds_raw"]):
        _assert_native_ints(row)
    for keys in _fold_key_orders(voc101["folds_raw"]):
        assert keys == FOLD_ROW_KEYS
    voc_top, voc_nested = _summary_key_orders(voc101["summary_raw"])
    assert voc_top == ["ap_pool_micro_u", "by_disposition", "card_count", "ece_pool_micro_u", "ok_count"]
    assert voc_nested == DISPOSITION_ORDER
    all_rows = {r["card_id"]: r for r in _parse_jsonl(all_point["folds_raw"])}
    voc_rows = {r["card_id"]: r for r in _parse_jsonl(voc101["folds_raw"])}
    assert all_rows["H12"]["ap_micro_u"] == HOLDOUT_BY_ID["H12"]["ap_micro_u"]
    assert voc_rows["H12"]["ap_micro_u"] == VOC101_BY_ID["H12"]["ap_micro_u"]
    assert all_rows["H12"]["ap_micro_u"] != voc_rows["H12"]["ap_micro_u"]
    assert voc_rows["H12"]["ap_micro_u"] != PRIMARY_BY_ID["C02"]["ap_micro_u"]
    assert voc_rows["H12"]["ap_micro_u"] != 875000
    assert voc_rows["H12"]["ap_micro_u"] != 878788
    assert all_rows["H92"]["ece_micro_u"] == 600000
    assert all_rows["H93"]["ap_micro_u"] == 583333
    assert all_rows["H94"]["ap_micro_u"] == 875000
    assert voc_rows["H92"]["ece_micro_u"] == 600000
    assert voc_rows["H94"]["ap_micro_u"] == 875413
    assert voc_rows["H94"]["ap_micro_u"] != all_rows["H94"]["ap_micro_u"]
    assert voc_rows["H94"]["ap_micro_u"] != 878788
    alt_all_card = json.loads(HOLDOUT_FILES["allpoint_alt_examples/sheet.json"])
    alt_voc_card = json.loads(HOLDOUT_FILES["voc101_alt_examples/digest.json"])
    assert alt_all_card["ap_micro_u"] == 666667
    assert alt_all_card["ece_micro_u"] == 312475
    assert alt_voc_card["ap_micro_u"] == 709571
    assert alt_voc_card["ece_micro_u"] == 137550
    assert alt_all_card["card"]["card_id"] == "ECEX"
    assert alt_voc_card["card"]["classes"] == ["oak", "pine"]
    alt_all = _run_fold("allpoint_alt_examples")
    alt_voc = _run_fold("voc101_alt_examples")
    assert alt_all["returncode"] == 0, alt_all["stderr"]
    assert alt_voc["returncode"] == 0, alt_voc["stderr"]
    assert alt_all["entries"] == ["folds.jsonl", "summary.json"]
    assert alt_voc["entries"] == ["folds.jsonl", "summary.json"]
    _assert_folds_match(_parse_jsonl(alt_all["folds_raw"]), EXPECTED_HOLDOUT_MAX_ROWS)
    _assert_folds_match(_parse_jsonl(alt_voc["folds_raw"]), EXPECTED_VOC101_ALT_ROWS)
    assert _parse_summary(alt_all["summary_raw"]) == EXPECTED_HOLDOUT_MAX_SUMMARY
    assert _parse_summary(alt_voc["summary_raw"]) == EXPECTED_VOC101_ALT_SUMMARY
    for row in _parse_jsonl(alt_all["folds_raw"]):
        _assert_native_ints(row)
    for row in _parse_jsonl(alt_voc["folds_raw"]):
        _assert_native_ints(row)
    alt_all_rows = {r["card_id"]: r for r in _parse_jsonl(alt_all["folds_raw"])}
    alt_voc_rows = {r["card_id"]: r for r in _parse_jsonl(alt_voc["folds_raw"])}
    assert alt_all_rows["H12"]["ap_micro_u"] == all_rows["H12"]["ap_micro_u"]
    assert alt_all_rows["H84"]["ece_micro_u"] == 150050
    assert type(alt_all_rows["H84"]["ece_micro_u"]) is int
    assert alt_all_rows["H84"]["ece_micro_u"] != all_rows["H84"]["ece_micro_u"]
    assert alt_voc_rows["H84"]["ece_micro_u"] == 275000
    assert type(alt_voc_rows["H84"]["ece_micro_u"]) is int
    assert alt_voc_rows["H84"]["ece_micro_u"] != voc_rows["H84"]["ece_micro_u"]
    assert alt_voc_rows["H12"]["ece_micro_u"] == 248571
    assert alt_voc_rows["H84"]["ece_micro_u"] != 350000
    assert all_rows["H86"]["row_count"] == 2
    assert all_rows["H88"]["disposition"] == "reject_fold"
    assert all_rows["H87"]["ece_micro_u"] == 333333
    assert voc_rows["H86"]["row_count"] == 3
    assert voc_rows["H88"]["disposition"] == "ok"
    assert voc_rows["H87"]["ece_micro_u"] == 466667
    assert alt_all_rows["H86"]["row_count"] == 3
    assert alt_all_rows["H88"]["disposition"] == "ok"
    assert alt_voc_rows["H86"]["row_count"] == 2
    assert alt_voc_rows["H88"]["disposition"] == "reject_fold"
    assert alt_voc_rows["H87"]["ece_micro_u"] == 333333
    for result in (alt_all, alt_voc):
        for keys in _fold_key_orders(result["folds_raw"]):
            assert keys == FOLD_ROW_KEYS
    voc11_card = json.loads(HOLDOUT_FILES["voc11_alt_examples/probe.json"])
    assert voc11_card["ap_micro_u"] == 719697
    assert voc11_card["ece_micro_u"] == 137550
    assert voc11_card["card"]["card_id"] == "ECEX"
    voc11 = _run_fold("voc11_alt_examples")
    assert voc11["returncode"] == 0, voc11["stderr"]
    assert voc11["entries"] == ["folds.jsonl", "summary.json"]
    _assert_folds_match(_parse_jsonl(voc11["folds_raw"]), EXPECTED_VOC11_ROWS)
    assert _parse_summary(voc11["summary_raw"]) == EXPECTED_VOC11_SUMMARY
    for row in _parse_jsonl(voc11["folds_raw"]):
        _assert_native_ints(row)
    for keys in _fold_key_orders(voc11["folds_raw"]):
        assert keys == FOLD_ROW_KEYS
    voc11_rows = {r["card_id"]: r for r in _parse_jsonl(voc11["folds_raw"])}
    assert voc11_rows["H12"]["ap_micro_u"] == VOC11_BY_ID["H12"]["ap_micro_u"]
    assert voc11_rows["H12"]["ap_micro_u"] != all_rows["H12"]["ap_micro_u"]
    assert voc11_rows["H12"]["ap_micro_u"] != voc_rows["H12"]["ap_micro_u"]
    assert voc11_rows["H84"]["ece_micro_u"] == 275000
    assert voc11_rows["H84"]["ece_micro_u"] != 150050
    assert voc11_rows["H84"]["ece_micro_u"] != 350000
    assert voc11_rows["H86"]["row_count"] == 3
    assert voc11_rows["H87"]["ece_micro_u"] == 333333
    assert voc11_rows["H88"]["disposition"] == "ok"
    assert voc11_rows["H92"]["ece_micro_u"] == 600000
    assert voc11_rows["H94"]["ap_micro_u"] == 878788
    assert alt_all_rows["H93"]["ap_micro_u"] == 541667
    assert alt_all_rows["H93"]["ap_micro_u"] != all_rows["H93"]["ap_micro_u"]
    assert alt_voc_rows["H93"]["ap_micro_u"] == 584158


def test_zero_ok_pool_branch():
    """A one-card reject_fold pack must write zero pools (no division by zero)."""
    card = {
        "bin_count": 3,
        "card_id": "Z0",
        "classes": ["oak", "pine"],
        "min_conf_bp": 0,
        "mode": "frozen",
        "slices": [
            {
                "slice_id": "s0",
                "rows": [
                    {
                        "example_id": "a",
                        "label": "oak",
                        "p_bp": {"oak": 10000, "pine": 0},
                    }
                ],
            }
        ],
    }
    cards = {
        "manifest.jsonl": b'{"card_id":"Z0"}\n',
        "Z0.json": (json.dumps(card) + "\n").encode(),
    }
    result = _execute_fold(cards, STAGED_SPEC_FILES)
    assert result["returncode"] == 0, result["stderr"]
    assert result["entries"] == ["folds.jsonl", "summary.json"]
    rows = _parse_jsonl(result["folds_raw"])
    assert len(rows) == 1
    assert rows[0]["disposition"] == "reject_fold"
    assert rows[0]["weight"] == 0
    summary = _parse_summary(result["summary_raw"])
    _assert_native_ints(summary)
    assert summary["ap_pool_micro_u"] == 0
    assert summary["ece_pool_micro_u"] == 0
    assert type(summary["ap_pool_micro_u"]) is int
    assert type(summary["ece_pool_micro_u"]) is int
    assert b"false" not in result["summary_raw"]
    assert summary["ok_count"] == 0
    assert summary["card_count"] == 1
    assert summary["by_disposition"]["reject_fold"] == 1
    assert summary["by_disposition"]["ok"] == 0


def test_duplicate_card_distinct_weights():
    """Repeated card_id rows must keep per-occurrence weights (no card_id fold-row cache).

    SMF processes every manifest record and writes that record's weight onto a fold
    row. Order among equal card_ids is not disclosed; only the weight multiset and
    ascending card_id order are graded. A cache keyed only by card_id that reuses
    the first assembled row emits weight 1 twice below.
    """
    card_a = {
        "bin_count": 4,
        "card_id": "A",
        "classes": ["oak"],
        "min_conf_bp": 0,
        "slices": [
            {
                "slice_id": "s",
                "rows": [
                    {
                        "example_id": "e",
                        "label": "oak",
                        "p_bp": {"oak": 10000},
                    }
                ],
            }
        ],
    }
    cards = {
        "manifest.jsonl": (
            b'{"card_id":"A","weight":1}\n{"card_id":"A","weight":2}\n'
        ),
        "A.json": (json.dumps(card_a) + "\n").encode(),
    }
    result = _execute_fold(cards, STAGED_SPEC_FILES)
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["folds_raw"])
    assert len(rows) == 2
    assert [row["card_id"] for row in rows] == ["A", "A"]
    assert sorted(row["weight"] for row in rows) == [1, 2]
    by_weight = {row["weight"]: row for row in rows}
    assert by_weight[1]["disposition"] == "ok"
    assert by_weight[2]["disposition"] == "ok"
    assert by_weight[1]["ap_micro_u"] == 1000000
    assert by_weight[2]["ap_micro_u"] == 1000000
    assert by_weight[1]["ece_micro_u"] == 0
    assert by_weight[2]["ece_micro_u"] == 0
    summary = _parse_summary(result["summary_raw"])
    assert summary["ok_count"] == 2
    assert summary["card_count"] == 2
    assert summary["ap_pool_micro_u"] == 1000000
    assert summary["ece_pool_micro_u"] == 0
    cards2 = {
        "manifest.jsonl": (
            b'{"card_id":"A","weight":2}\n{"card_id":"A","weight":5}\n'
        ),
        "A.json": cards["A.json"],
    }
    result2 = _execute_fold(cards2, STAGED_SPEC_FILES)
    assert result2["returncode"] == 0, result2["stderr"]
    rows2 = _parse_jsonl(result2["folds_raw"])
    assert sorted(row["weight"] for row in rows2) == [2, 5]


def test_pool_half_even_large_weights():
    """Exact integer half-even pool must survive weights near Decimal default precision."""
    base_card = {
        "bin_count": 4,
        "classes": ["oak", "pine"],
        "min_conf_bp": 0,
        "mode": "frozen",
        "slices": [
            {
                "slice_id": "s0",
                "rows": [
                    {
                        "example_id": "a",
                        "label": "oak",
                        "p_bp": {"oak": 10000, "pine": 0},
                    }
                ],
            }
        ],
    }
    card_a = dict(base_card, card_id="A")
    card_b = dict(base_card, card_id="B")
    card_b["slices"] = [
        {
            "slice_id": "s0",
            "rows": [
                {
                    "example_id": "b",
                    "label": "oak",
                    "p_bp": {"oak": 9999, "pine": 1},
                }
            ],
        }
    ]
    weight_a = 199 * (10**30) - 1
    weight_b = 10**30
    cards = {
        "manifest.jsonl": (
            json.dumps({"card_id": "A", "weight": weight_a})
            + "\n"
            + json.dumps({"card_id": "B", "weight": weight_b})
            + "\n"
        ).encode(),
        "A.json": (json.dumps(card_a) + "\n").encode(),
        "B.json": (json.dumps(card_b) + "\n").encode(),
    }
    result = _execute_fold(cards, STAGED_SPEC_FILES)
    assert result["returncode"] == 0, result["stderr"]
    rows = {row["card_id"]: row for row in _parse_jsonl(result["folds_raw"])}
    assert rows["A"]["disposition"] == "ok"
    assert rows["B"]["disposition"] == "ok"
    assert rows["A"]["ece_micro_u"] == 0
    assert rows["B"]["ece_micro_u"] == 100
    assert rows["A"]["weight"] == weight_a
    assert rows["B"]["weight"] == weight_b
    summary = _parse_summary(result["summary_raw"])
    assert summary["ece_pool_micro_u"] == 1
    assert summary["ap_pool_micro_u"] == 1000000
    assert summary["ok_count"] == 2


def test_pool_half_even_exact_tie():
    """Pack ECE pool at exact half (100/200) must round half-even to 0, not half-up to 1."""
    base_card = {
        "bin_count": 4,
        "classes": ["oak", "pine"],
        "min_conf_bp": 0,
        "mode": "frozen",
        "slices": [
            {
                "slice_id": "s0",
                "rows": [
                    {
                        "example_id": "a",
                        "label": "oak",
                        "p_bp": {"oak": 10000, "pine": 0},
                    }
                ],
            }
        ],
    }
    card_a = dict(base_card, card_id="A")
    card_b = dict(base_card, card_id="B")
    card_b["slices"] = [
        {
            "slice_id": "s0",
            "rows": [
                {
                    "example_id": "b",
                    "label": "oak",
                    "p_bp": {"oak": 9999, "pine": 1},
                }
            ],
        }
    ]
    cards = {
        "manifest.jsonl": (
            '{"card_id":"A","weight":199}\n{"card_id":"B","weight":1}\n'
        ).encode(),
        "A.json": (json.dumps(card_a) + "\n").encode(),
        "B.json": (json.dumps(card_b) + "\n").encode(),
    }
    result = _execute_fold(cards, STAGED_SPEC_FILES)
    assert result["returncode"] == 0, result["stderr"]
    rows = {row["card_id"]: row for row in _parse_jsonl(result["folds_raw"])}
    assert rows["A"]["ece_micro_u"] == 0
    assert rows["B"]["ece_micro_u"] == 100
    summary = _parse_summary(result["summary_raw"])
    _assert_native_ints(summary)
    assert summary["ece_pool_micro_u"] == 0
    assert summary["ece_pool_micro_u"] != 1
    assert summary["ap_pool_micro_u"] == 1000000
    assert summary["ok_count"] == 2


def test_null_mode_rejected():
    """Explicit mode:null is reject_schema; absent mode still defaults to live.

    Isolates `mode = card.get("mode") or "live"`, which accepts null as live.
    """
    base = json.loads((Path("/app/cards/A.json")).read_text())
    null_card = dict(base, card_id="N0", mode=None)
    absent_card = dict(base, card_id="N1")
    absent_card.pop("mode", None)
    null_bytes = (json.dumps(null_card) + "\n").encode()
    assert b'"mode": null' in null_bytes or b'"mode":null' in null_bytes
    assert "mode" not in absent_card
    cards = {
        "manifest.jsonl": b'{"card_id":"N0"}\n{"card_id":"N1"}\n',
        "N0.json": null_bytes,
        "N1.json": (json.dumps(absent_card) + "\n").encode(),
    }
    result = _execute_fold(cards, STAGED_SPEC_FILES)
    assert result["returncode"] == 0, result["stderr"]
    rows = {row["card_id"]: row for row in _parse_jsonl(result["folds_raw"])}
    assert rows["N0"] == {
        "ap_micro_u": 0,
        "bin_count": 0,
        "card_id": "N0",
        "class_count": 0,
        "disposition": "reject_schema",
        "ece_micro_u": 0,
        "row_count": 0,
        "weight": 0,
    }
    assert rows["N1"]["disposition"] == "ok"
    assert rows["N1"]["ap_micro_u"] == 1000000
    assert rows["N1"]["ece_micro_u"] == 0
    assert rows["N1"]["weight"] == 1
    null2 = dict(base, card_id="N2", mode=None)
    cards2 = {
        "manifest.jsonl": b'{"card_id":"N2"}\n',
        "N2.json": (json.dumps(null2) + "\n").encode(),
    }
    result2 = _execute_fold(cards2, STAGED_SPEC_FILES)
    assert result2["returncode"] == 0, result2["stderr"]
    rows2 = _parse_jsonl(result2["folds_raw"])
    assert len(rows2) == 1
    assert rows2[0]["disposition"] == "reject_schema"
    assert rows2[0]["ap_micro_u"] == 0
    assert rows2[0]["weight"] == 0


def test_boolean_manifest_weight_rejected():
    """JSON boolean weight must be reject_schema (type(x) is int), not accepted via isinstance."""
    card = json.loads((Path("/app/cards/A.json")).read_text())
    card["card_id"] = "B"
    cards = {
        "manifest.jsonl": b'{"card_id":"B","weight":true}\n',
        "B.json": (json.dumps(card) + "\n").encode(),
    }
    result = _execute_fold(cards, STAGED_SPEC_FILES)
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["folds_raw"])
    assert len(rows) == 1
    assert rows[0] == {
        "ap_micro_u": 0,
        "bin_count": 0,
        "card_id": "B",
        "class_count": 0,
        "disposition": "reject_schema",
        "ece_micro_u": 0,
        "row_count": 0,
        "weight": 0,
    }
    cards_f = {
        "manifest.jsonl": b'{"card_id":"B","weight":false}\n',
        "B.json": (json.dumps(card) + "\n").encode(),
    }
    result_f = _execute_fold(cards_f, STAGED_SPEC_FILES)
    assert result_f["returncode"] == 0, result_f["stderr"]
    rows_f = _parse_jsonl(result_f["folds_raw"])
    assert rows_f[0]["disposition"] == "reject_schema"
    assert rows_f[0]["weight"] == 0


def test_path_component_card_id_rejected():
    """Manifest card_id with path components must reject; document id must equal file stem.

    Escape target is materialized via ../ relative to the cards dir passed to fold.py,
    so a naive cards_dir/(card_id+'.json') join finds the file and can wrongly accept
    when comparing the document id to the manifest id instead of path.stem.
    """
    card = {
        "card_id": "../T",
        "classes": ["oak"],
        "bin_count": 4,
        "min_conf_bp": 0,
        "slices": [
            {
                "slice_id": "s",
                "rows": [
                    {
                        "example_id": "e",
                        "label": "oak",
                        "p_bp": {"oak": 10000},
                    }
                ],
            }
        ],
    }
    cards = {
        "manifest.jsonl": b'{"card_id":"../T"}\n',
        "../T.json": (json.dumps(card) + "\n").encode(),
    }
    result = _execute_fold(cards, STAGED_SPEC_FILES)
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["folds_raw"])
    assert len(rows) == 1
    assert rows[0]["disposition"] == "reject_schema"
    assert rows[0]["ap_micro_u"] == 0
    assert rows[0]["ece_micro_u"] == 0
    assert rows[0]["weight"] == 0
    assert rows[0]["bin_count"] == 0
    assert rows[0]["class_count"] == 0
    assert rows[0]["row_count"] == 0
    card2 = {
        "card_id": "../U",
        "classes": ["oak"],
        "bin_count": 4,
        "min_conf_bp": 0,
        "mode": "live",
        "slices": [
            {
                "slice_id": "s",
                "rows": [
                    {
                        "example_id": "e",
                        "label": "oak",
                        "p_bp": {"oak": 10000},
                    }
                ],
            }
        ],
    }
    cards2 = {
        "manifest.jsonl": b'{"card_id":"../U"}\n',
        "../U.json": (json.dumps(card2) + "\n").encode(),
    }
    result2 = _execute_fold(cards2, STAGED_SPEC_FILES)
    assert result2["returncode"] == 0, result2["stderr"]
    rows2 = _parse_jsonl(result2["folds_raw"])
    assert len(rows2) == 1
    assert rows2[0]["disposition"] == "reject_schema"
    assert rows2[0]["ap_micro_u"] == 0
    assert rows2[0]["weight"] == 0


def test_hyphenated_card_id_accepted():
    """Punctuation in card_id is legal when the file is beside the manifest and stem matches.

    Isolates an invented `card_id.isalnum()` restriction that rejects A-B.
    """
    base = json.loads((Path("/app/cards/A.json")).read_text())
    hyphen = dict(base, card_id="A-B")
    underscore = dict(base, card_id="A_B")
    cards = {
        "manifest.jsonl": b'{"card_id":"A-B"}\n{"card_id":"A_B"}\n',
        "A-B.json": (json.dumps(hyphen) + "\n").encode(),
        "A_B.json": (json.dumps(underscore) + "\n").encode(),
    }
    result = _execute_fold(cards, STAGED_SPEC_FILES)
    assert result["returncode"] == 0, result["stderr"]
    rows = {row["card_id"]: row for row in _parse_jsonl(result["folds_raw"])}
    assert rows["A-B"]["disposition"] == "ok"
    assert rows["A-B"]["ap_micro_u"] == 1000000
    assert rows["A-B"]["ece_micro_u"] == 0
    assert rows["A-B"]["weight"] == 1
    assert rows["A_B"]["disposition"] == "ok"
    assert rows["A_B"]["ap_micro_u"] == 1000000


def test_non_ascii_card_id_accepted():
    """Non-ASCII card_id is legal when the sibling file stem matches (no ASCII-only rule).

    Isolates `if not card_id.isascii(): reject_schema`.
    """
    base = json.loads((Path("/app/cards/A.json")).read_text())
    for cid in ("é", "α"):
        assert not cid.isascii()
        card = dict(base, card_id=cid)
        cards = {
            "manifest.jsonl": (json.dumps({"card_id": cid}, ensure_ascii=False) + "\n").encode(
                "utf-8"
            ),
            f"{cid}.json": (json.dumps(card, ensure_ascii=False) + "\n").encode("utf-8"),
        }
        result = _execute_fold(cards, STAGED_SPEC_FILES)
        assert result["returncode"] == 0, (cid, result["stderr"])
        rows = _parse_jsonl(result["folds_raw"])
        assert len(rows) == 1, cid
        assert rows[0]["card_id"] == cid
        assert rows[0]["disposition"] == "ok", cid
        assert rows[0]["ap_micro_u"] == 1000000
        assert rows[0]["ece_micro_u"] == 0
        assert rows[0]["bin_count"] == 4
        assert rows[0]["class_count"] == 1
        assert rows[0]["row_count"] == 1
        assert rows[0]["weight"] == 1


def test_backslash_card_id_accepted():
    """On Linux a backslash is an ordinary filename character, not a path separator."""
    base = json.loads((Path("/app/cards/A.json")).read_text())
    cid = "a\\b"
    card = dict(base, card_id=cid)
    cards = {
        "manifest.jsonl": (json.dumps({"card_id": cid}) + "\n").encode(),
        f"{cid}.json": (json.dumps(card) + "\n").encode(),
    }
    result = _execute_fold(cards, STAGED_SPEC_FILES)
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["folds_raw"])
    assert len(rows) == 1
    assert rows[0]["card_id"] == cid
    assert rows[0]["disposition"] == "ok"
    assert rows[0]["ap_micro_u"] == 1000000
    assert rows[0]["ece_micro_u"] == 0
    assert rows[0]["bin_count"] == 4
    assert rows[0]["class_count"] == 1
    assert rows[0]["row_count"] == 1
    assert rows[0]["weight"] == 1


def test_dot_card_id_accepted():
    """Dot-only stems (file ..json / ...json) are legal when beside the manifest.

    Isolates rejecting card_id in {'.', '..'} without reading a matching sibling file.
    """
    base = json.loads((Path("/app/cards/A.json")).read_text())
    for cid in (".", ".."):
        card = dict(base, card_id=cid)
        cards = {
            "manifest.jsonl": (json.dumps({"card_id": cid}) + "\n").encode(),
            f"{cid}.json": (json.dumps(card) + "\n").encode(),
        }
        result = _execute_fold(cards, STAGED_SPEC_FILES)
        assert result["returncode"] == 0, (cid, result["stderr"])
        rows = _parse_jsonl(result["folds_raw"])
        assert len(rows) == 1, cid
        assert rows[0]["card_id"] == cid
        assert rows[0]["disposition"] == "ok", cid
        assert rows[0]["ap_micro_u"] == 1000000
        assert rows[0]["ece_micro_u"] == 0
        assert rows[0]["bin_count"] == 4
        assert rows[0]["class_count"] == 1
        assert rows[0]["row_count"] == 1
        assert rows[0]["weight"] == 1


def test_fractional_manifest_weight_rejected():
    """Positive float weights (e.g. 1.5) are not integers and must reject_schema."""
    card = json.loads((Path("/app/cards/A.json")).read_text())
    cards = {
        "manifest.jsonl": b'{"card_id":"A","weight":1.5}\n',
        "A.json": (json.dumps(card) + "\n").encode(),
    }
    result = _execute_fold(cards, STAGED_SPEC_FILES)
    assert result["returncode"] == 0, result["stderr"]
    rows = _parse_jsonl(result["folds_raw"])
    assert len(rows) == 1
    assert rows[0] == {
        "ap_micro_u": 0,
        "bin_count": 0,
        "card_id": "A",
        "class_count": 0,
        "disposition": "reject_schema",
        "ece_micro_u": 0,
        "row_count": 0,
        "weight": 0,
    }
    cards2 = {
        "manifest.jsonl": b'{"card_id":"A","weight":2.0}\n',
        "A.json": (json.dumps(card) + "\n").encode(),
    }
    result2 = _execute_fold(cards2, STAGED_SPEC_FILES)
    assert result2["returncode"] == 0, result2["stderr"]
    rows2 = _parse_jsonl(result2["folds_raw"])
    assert rows2[0]["disposition"] == "reject_schema"
    assert rows2[0]["weight"] == 0


def test_negative_manifest_weight_rejected():
    """Negative integer weights violate weight >= 1 and must reject_schema.

    Isolates `type(weight) is int and weight != 0`, which wrongly accepts -1.
    """
    card = json.loads((Path("/app/cards/A.json")).read_text())
    for weight in (-1, -5):
        cards = {
            "manifest.jsonl": (
                json.dumps({"card_id": "A", "weight": weight}) + "\n"
            ).encode(),
            "A.json": (json.dumps(card) + "\n").encode(),
        }
        result = _execute_fold(cards, STAGED_SPEC_FILES)
        assert result["returncode"] == 0, (weight, result["stderr"])
        rows = _parse_jsonl(result["folds_raw"])
        assert len(rows) == 1, weight
        assert rows[0] == {
            "ap_micro_u": 0,
            "bin_count": 0,
            "card_id": "A",
            "class_count": 0,
            "disposition": "reject_schema",
            "ece_micro_u": 0,
            "row_count": 0,
            "weight": 0,
        }, weight


def test_sandbox_cannot_read_tests_tree():
    """Sandbox uid 2000 must not read sealed verifier files under /tests."""
    script = "from pathlib import Path\nprint(Path('/tests/test_outputs.py').read_bytes()[:8])\n"
    completed = subprocess.run(
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
            script,
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    combined = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert "Permission" in combined or "Errno 13" in combined or "PermissionError" in combined
