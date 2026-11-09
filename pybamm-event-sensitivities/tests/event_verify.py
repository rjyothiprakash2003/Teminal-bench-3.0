"""Isolated numerical probes. Goldens stay in this image; candidate code never sees them."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
SUBMISSION = Path("/app/submission")
SANDBOX_UID = 65534
PROBE_SECONDS = 300

_CACHE: dict[str, dict] = {}


def read_json(path: Path) -> dict:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 32_000_000:
            raise AssertionError("expected a bounded regular JSON file")
        return json.loads(stream.read(32_000_001))


def compare(actual, expected, absolute, relative=0):
    a = np.asarray(actual)
    b = np.asarray(expected)
    if (
        a.shape != b.shape
        or a.dtype.kind not in "fi"
        or b.dtype.kind not in "fi"
        or not np.all(np.isfinite(a))
        or not np.all(np.isfinite(b))
    ):
        raise AssertionError("invalid numerical shape, type, or non-finite value")
    delta = np.abs(a - b)
    if np.any(delta > absolute + relative * np.abs(b)):
        raise AssertionError(
            f"numerical error {float(delta.max())} exceeds {absolute} + {relative}*abs(ref)"
        )
    return float(delta.max()) if delta.size else 0.0


def _chmod_tree(root: Path) -> None:
    if not root.exists():
        return
    for dirpath, dirnames, filenames in os.walk(root):
        os.chmod(dirpath, 0o755)
        for name in dirnames + filenames:
            path = Path(dirpath) / name
            if path.is_symlink():
                continue
            os.chmod(path, 0o755 if path.is_dir() else 0o644)


def _kill_sandbox() -> None:
    for _ in range(10):
        found = False
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                line = next(
                    s
                    for s in (entry / "status").read_text().splitlines()
                    if s.startswith("Uid:")
                )
                if SANDBOX_UID in map(int, line.split()[1:]):
                    os.kill(int(entry.name), signal.SIGKILL)
                    found = True
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                pass
        if not found:
            return
        time.sleep(0.01)


def _assert_copied_file(path: Path, root: Path, label: str) -> None:
    """A parent symlink still looks like a real file; walk every ancestor."""
    node = path
    while True:
        assert not node.is_symlink(), "submission path is a symlink " + label
        if node == root:
            break
        node = node.parent
    assert path.is_file(), "missing upstream file " + label


def submitted_package_present() -> None:
    root = SUBMISSION / "pybamm"
    init = root / "__init__.py"
    _assert_copied_file(init, root, "__init__.py")
    version = root / "_version.py"
    _assert_copied_file(version, root, "_version.py")
    manifest = read_json(HERE / "package_manifest.json")
    for relative, digest in manifest["files"].items():
        path = root / relative
        _assert_copied_file(path, root, relative)
        if digest is None:
            assert path.read_bytes().strip(), "empty upstream module " + relative
            continue
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, (
            "bundled file changed " + relative
        )


def invoke(name: str, mode: str | None = None, config: dict | None = None) -> dict:
    key = name if mode is None else f"{name}:{mode}"
    if key in _CACHE:
        return _CACHE[key]
    submitted_package_present()
    _chmod_tree(SUBMISSION)
    if config is None:
        fixture = read_json(HERE / "fixtures" / f"{name}.json")
        config = dict(fixture["input"])
        expected = fixture["expected"]
    else:
        config = dict(config)
        expected = None
    if mode is not None:
        config["probe_mode"] = mode
    base = Path(tempfile.mkdtemp(prefix="ev-"))
    os.chmod(base, 0o755)
    probe = base / "probe.py"
    shutil.copyfile(HERE / "probe.py", probe)
    os.chmod(probe, 0o644)
    source = base / "input.json"
    source.write_text(json.dumps(config))
    os.chmod(source, 0o644)
    # HOME is read-only after setup so import-time pip --target=$HOME/... or a
    # generator writing under HOME cannot land new dependencies (delivery rule).
    home = base / "home"
    home.mkdir(mode=0o755)
    mpl = home / "mpl"
    mpl.mkdir(mode=0o700)
    os.chown(home, SANDBOX_UID, SANDBOX_UID)
    os.chown(mpl, SANDBOX_UID, SANDBOX_UID)
    os.chmod(home, 0o555)
    isolated = base / "import"
    isolated.mkdir(mode=0o755)
    os.symlink(SUBMISSION / "pybamm", isolated / "pybamm")
    output = base / "result.json"
    output.write_bytes(b"")
    os.chown(output, SANDBOX_UID, SANDBOX_UID)
    os.chmod(output, 0o600)
    environment = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(home),
        "PYTHONPATH": str(isolated),
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYBAMM_DISABLE_TELEMETRY": "true",
        "OPENBLAS_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
        "MPLCONFIGDIR": str(mpl),
        "MPLBACKEND": "Agg",
    }
    command = [
        "setpriv",
        f"--reuid={SANDBOX_UID}",
        f"--regid={SANDBOX_UID}",
        "--clear-groups",
        "--no-new-privs",
        sys.executable,
        str(probe),
        "--input",
        str(source),
        "--output",
        str(output),
    ]
    with (base / "probe.log").open("wb") as log:
        process = subprocess.Popen(
            command,
            cwd=str(home),
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            code = process.wait(timeout=PROBE_SECONDS)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise AssertionError(f"{name}: 300-second probe limit")
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            _kill_sandbox()
    assert code == 0, f"{name}: probe exited {code}"
    actual = read_json(output)
    _CACHE[key] = {"config": config, "expected": expected, "actual": actual}
    return _CACHE[key]


def check_terminating_expression(algebraic: bool, capacity: float, relaxation: float, end_time: float, output: float, phase) -> None:
    """The event endpoint keeps the terminating step's output expression."""
    config = {
        "model": "analytic",
        "parameters": {"capacity": capacity, "relaxation": relaxation},
        "steps": ["Discharge at 1 A until 3.5 V", "Rest for 2 seconds"],
        "period": "0.1 seconds",
        "variables": ["product"],
        "product": True,
        "probe_mode": "terminating",
    }
    if algebraic:
        config["algebraic"] = True
    actual = invoke("terminating-dae" if algebraic else "terminating-ode", config=config)["actual"]
    assert actual["names"] == ["capacity", "relaxation"], "alphabetical parameter columns"
    compare(actual["time"], end_time, 1e-3)
    compare(actual["output"], output, 5e-6)
    compare(actual["phase"], phase, 3e-5, 3e-5)
    compare(actual["rest_output"], 0.0, 5e-6)


def _column(values, index):
    array = np.asarray(values)
    return array[:, index : index + 1]


def _check_named(named, stacked, names, absolute, relative) -> None:
    stacked = np.asarray(stacked)
    for index, name in enumerate(names):
        column = np.asarray(named[name])
        assert column.shape == (stacked.shape[0], 1), "scalar parameter columns have one column"
        compare(column, stacked[:, index : index + 1], absolute, relative)


def _check_cycles(series, config, names, tolerance) -> None:
    cycles = series["cycles"]
    assert cycles, "each cycle must expose aligned derivatives"
    if config.get("cycles"):
        assert len(cycles) == len(config["cycles"]), "each experiment cycle stays separate"
    for cycle in cycles:
        assert cycle["plain_dict"] is True
        assert set(cycle["time_keys"]) == set(names) | {"all"}
        for variable in config["variables"]:
            assert set(cycle["phase_keys"][variable]) == set(names) | {"all"}
            _check_named(cycle["phase_named"][variable], cycle["phase_all"][variable], names, tolerance, tolerance)
        assert isinstance(cycle["termination"], str) and cycle["termination"]
        assert len(cycle["t"]) == len(cycle["time_all"])
        _check_named(cycle["time_named"], cycle["time_all"], names, 0.05, 2e-4)
        for variable in config["variables"]:
            assert len(cycle["forward"][variable]) == len(cycle["t"])
            assert np.asarray(cycle["fixed_all"][variable]).shape[0] == len(cycle["t"])
            assert np.asarray(cycle["phase_all"][variable]).shape == np.asarray(cycle["fixed_all"][variable]).shape
            assert np.asarray(cycle["fixed_all"][variable]).shape[1] == len(names)
            _check_named(cycle["fixed_named"][variable], cycle["fixed_all"][variable], names, tolerance, tolerance)
    compare(np.concatenate([cycle["t"] for cycle in cycles]), series["times"], 1e-3)
    compare(np.concatenate([cycle["time_all"] for cycle in cycles]), series["time_all"], 0.05, 2e-4)
    for variable in config["variables"]:
        compare(
            np.concatenate([cycle["forward"][variable] for cycle in cycles]),
            series["forward"][variable],
            5e-6,
        )
        compare(
            np.concatenate([cycle["phase_all"][variable] for cycle in cycles]),
            series["phase_all"][variable],
            tolerance,
            tolerance,
        )
        compare(
            np.concatenate([cycle["fixed_all"][variable] for cycle in cycles]),
            series["fixed_all"][variable],
            tolerance,
            tolerance,
        )
        compare(
            np.concatenate([step["phase_all"][variable] for step in series["steps"]]),
            series["phase_all"][variable],
            tolerance,
            tolerance,
        )
        compare(
            np.concatenate([step["fixed_all"][variable] for step in series["steps"]]),
            series["fixed_all"][variable],
            tolerance,
            tolerance,
        )
        compare(
            np.concatenate([step["forward"][variable] for step in series["steps"]]),
            series["forward"][variable],
            5e-6,
        )
    compare(
        np.concatenate([np.asarray(step["time_all"]) for step in series["steps"]]),
        series["time_all"],
        0.05,
        2e-4,
    )
    allowed = set(names) | {"all"}
    for step in series["steps"]:
        assert step["plain_time"] is True
        assert set(step["time_keys"]) == allowed
        samples = np.asarray(step["phase_all"][config["variables"][0]]).shape[0]
        assert np.asarray(step["time_all"]).shape == (samples, len(names))
        for name in names:
            assert tuple(step["time_named_shape"][name]) == (samples, 1)
        _check_named(step["time_named"], step["time_all"], names, 0.05, 2e-4)
        for variable in config["variables"]:
            assert step["plain_phase"][variable] is True
            assert set(step["phase_keys"][variable]) == allowed
            assert np.asarray(step["phase_all"][variable]).shape[0] == samples
            for name in names:
                assert tuple(step["phase_named_shape"][variable][name]) == (samples, 1)
            _check_named(
                step["phase_named"][variable],
                step["phase_all"][variable],
                names,
                tolerance,
                tolerance,
            )
            for name in names:
                assert tuple(step["fixed_named_shape"][variable][name]) == (samples, 1)
            _check_named(
                step["fixed_named"][variable],
                step["fixed_all"][variable],
                names,
                tolerance,
                tolerance,
            )


def _check_series(actual, expected, config) -> None:
    series = actual["series"]
    reference = expected["series"]
    names = sorted(config["parameters"])
    assert series["plain_dict"] == {"phase": True, "time": True}
    allowed = set(names) | {"all"}
    assert set(series["time_keys"]) == allowed
    assert set(series["phase_keys"]) == set(config["variables"])
    for variable, keys in series["phase_keys"].items():
        assert set(keys) == allowed
    assert series["terminations"] == reference["terminations"]
    assert series["solution_termination"] == reference["terminations"][-1]
    tolerance = 3e-5 if config["model"] == "analytic" else 3e-4
    compare(series["times"], reference["times"], 1e-3)
    assert len(series["step_times"]) == len(reference["step_times"])
    for actual_step, expected_step in zip(series["step_times"], reference["step_times"], strict=True):
        compare(actual_step, expected_step, 1e-3)
    compare(series["time_all"], reference["time_all"], 0.05, 2e-4)
    _check_named(series["time_named"], series["time_all"], names, 0.05, 2e-4)
    for variable in config["variables"]:
        compare(series["forward"][variable], reference["forward"][variable], 5e-6)
        compare(series["phase_all"][variable], reference["phase_all"][variable], tolerance, tolerance)
        compare(series["fixed_all"][variable], reference["fixed_all"][variable], tolerance, tolerance)
        _check_named(series["phase_named"][variable], series["phase_all"][variable], names, tolerance, tolerance)
        _check_named(series["fixed_named"][variable], series["fixed_all"][variable], names, tolerance, tolerance)
        assert np.asarray(series["fixed_all"][variable]).shape[1] == len(names)
        assert np.asarray(series["phase_all"][variable]).shape == np.asarray(series["fixed_all"][variable]).shape
        assert len(series["forward"][variable]) == len(series["times"])
    _check_cycles(series, config, names, tolerance)


def check_forward_and_derivatives(name: str) -> None:
    payload = invoke(name)
    config = payload["config"]
    expected = payload["expected"]
    actual = payload["actual"]
    if config.get("compatibility") or config.get("legacy_event"):
        for key in ["end_times", "end_values", "phase_values", "fixed_values"]:
            compare(actual[key], expected[key], 1e-3 if key == "end_times" else 5e-6)
        for variable, reference in expected["fixed_gradients"].items():
            compare(actual["fixed_gradients"][variable], reference, 3e-5, 3e-5)
        for variable, reference in expected["fixed_all"].items():
            compare(actual["fixed_all"][variable], reference, 3e-5, 3e-5)
        assert actual["produced"] == {"time": False, "phase": False}
        if config.get("legacy_event"):
            assert actual["event_aligned"] is False
            assert actual["terminations"] == expected["terminations"]
        return
    _check_series(actual, expected, config)
    meta = actual["metadata"]
    assert "error" not in meta, "metadata access failed: " + meta.get("error", "")
    for key in expected["values"]:
        compare(
            actual["values"][key],
            expected["values"][key],
            1e-3 if key == "end_times" else 5e-6,
        )
    tolerance = 3e-5 if config["model"] == "analytic" else 3e-4
    for key in expected["derivatives"]:
        compare(
            actual["derivatives"][key],
            expected["derivatives"][key],
            0.05 if key == "end_times" else tolerance,
            2e-4 if key == "end_times" else tolerance,
        )


def check_plain(name: str) -> None:
    payload = invoke(name, mode="plain")
    expected = payload["expected"]
    actual = payload["actual"]
    compare(actual["end_times"], expected["values"]["end_times"], 1e-3)
    compare(actual["end_values"], expected["values"]["end_values"], 5e-6)
    compare(actual["times"], expected["plain"]["times"], 1e-3)
    for variable, samples in expected["plain"]["forward"].items():
        compare(actual["forward"][variable], samples, 5e-6)
    assert actual["terminations"] == expected["series"]["terminations"]
    assert actual["produced"] == {"time": False, "phase": False}


def check_event_only(name: str) -> None:
    payload = invoke(name, mode="event_only")
    assert payload["actual"]["produced"] == {"time": False, "phase": False}


def check_flag_off(name: str) -> None:
    """calculate_sensitivities without the event flag keeps native fixed-time values."""
    payload = invoke(name, mode="legacy")
    config = payload["config"]
    expected = payload["expected"]["legacy"]
    actual = payload["actual"]
    tolerance = 3e-5 if config["model"] == "analytic" else 3e-4
    compare(actual["end_times"], expected["end_times"], 1e-3)
    compare(actual["end_values"], expected["end_values"], 5e-6)
    assert actual["terminations"] == expected["terminations"]
    assert actual["produced"] == {"time": False, "phase": False}
    assert actual["event_aligned"] is False
    for variable, reference in expected["fixed_gradients"].items():
        compare(actual["fixed_gradients"][variable], reference, tolerance, tolerance)
    for variable, reference in expected["fixed_all"].items():
        compare(actual["fixed_all"][variable], reference, tolerance, tolerance)


def check_explicit_time(name: str) -> None:
    payload = invoke(name)
    config = payload["config"]
    index = config["variables"].index("clock-weighted z")
    expected = payload["expected"]["derivatives"]
    actual = payload["actual"]["derivatives"]
    tolerance = 3e-5 if config["model"] == "analytic" else 3e-4
    for key in ("end_values", "phase_values", "fixed_values"):
        compare(
            np.asarray(actual[key])[:, index, :],
            np.asarray(expected[key])[:, index, :],
            tolerance,
            tolerance,
        )


def _same_snapshot(actual, before, end=None) -> None:
    if end == "first":
        window = slice(0, 1)
    elif end == "last":
        window = slice(-1, None)
    else:
        window = None
    if window is not None:
        compare(np.asarray(actual["t"]).reshape(-1), np.asarray(before["t"]).reshape(-1)[window], 1e-8)
        compare(actual["time"]["all"], np.asarray(before["time"]["all"])[window], 1e-8)
        names = sorted(key for key in before["time"] if key != "all")
        for name in names:
            compare(actual["time"][name], np.asarray(before["time"][name])[window], 1e-8)
        for variable, samples in before["forward"].items():
            compare(actual["forward"][variable], np.asarray(samples)[window], 1e-8)
            compare(actual["phase"][variable]["all"], np.asarray(before["phase"][variable]["all"])[window], 1e-8)
            compare(actual["fixed"][variable]["all"], np.asarray(before["fixed"][variable]["all"])[window], 1e-8)
            for name in names:
                compare(actual["phase"][variable][name], np.asarray(before["phase"][variable][name])[window], 1e-8)
                compare(actual["fixed"][variable][name], np.asarray(before["fixed"][variable][name])[window], 1e-8)
        return
    compare(actual["t"], before["t"], 1e-8)
    assert actual["termination"] == before["termination"]
    compare(actual["time"]["all"], before["time"]["all"], 1e-8)
    names = sorted(key for key in actual["time"] if key != "all")
    compare(actual["time"]["all"], np.hstack([np.asarray(actual["time"][name]) for name in names]), 1e-8)
    for variable in before["forward"]:
        compare(actual["forward"][variable], before["forward"][variable], 1e-8)
        compare(actual["phase"][variable]["all"], before["phase"][variable]["all"], 1e-8)
        compare(actual["fixed"][variable]["all"], before["fixed"][variable]["all"], 1e-8)
        for name in names:
            compare(actual["phase"][variable][name], before["phase"][variable][name], 1e-8)
            compare(actual["fixed"][variable][name], before["fixed"][variable][name], 1e-8)


def _same_columns(actual, before) -> None:
    assert set(actual) == set(before)
    for key in before:
        compare(actual[key], before[key], 1e-8)


def _same_steps(after, before) -> None:
    assert len(after) == len(before)
    for left, right in zip(after, before, strict=True):
        compare(left["t"], right["t"], 1e-8)
        assert left["termination"] == right["termination"]
        _same_columns(left["time"], right["time"])
        for variable in right["phase"]:
            _same_columns(left["phase"][variable], right["phase"][variable])
            _same_columns(left["fixed"][variable], right["fixed"][variable])
            compare(left["forward"][variable], right["forward"][variable], 1e-8)


def check_metadata(name: str) -> None:
    payload = invoke(name)
    config = payload["config"]
    expected = payload["expected"]
    meta = payload["actual"]["metadata"]
    assert "error" not in meta, "metadata access failed: " + meta.get("error", "")
    names = sorted(config["parameters"])
    for parameter in names + ["all"]:
        before = np.asarray(meta["before"][parameter])
        columns = len(names) if parameter == "all" else 1
        assert before.shape == (meta["samples"], columns)
        for version in ["copy", "combined", "after"]:
            compare(meta[version][parameter], before, 1e-8)
        compare(meta["first"][parameter], before[:1], 1e-8)
        compare(meta["last"][parameter], before[-1:], 1e-8)
    compare(
        meta["before"]["all"],
        np.hstack([meta["before"][parameter] for parameter in names]),
        1e-8,
    )
    for variable in config["variables"]:
        item = meta["variables"][variable]
        assert np.asarray(item["all"]).shape == (meta["samples"], len(names))
        compare(item["all"], item["named"], 1e-8)
        for parameter in names:
            compare(item["first"][parameter], item["full_first"][parameter], 1e-8)
            compare(item["last"][parameter], item["full_last"][parameter], 1e-8)
    assert len(meta["steps"]) == len(config["steps"])
    ending = np.asarray(expected["derivatives"]["end_times"])
    for index, step in enumerate(meta["steps"]):
        start = ending[index - 1] if index else np.zeros(len(names))
        wanted = np.broadcast_to(start, (step["samples"], len(names))).copy()
        wanted[-1] = ending[index]
        compare(step["time"]["all"], wanted, 0.05, 2e-4)
        for column, parameter in enumerate(names):
            compare(step["time"][parameter], wanted[:, column : column + 1], 0.05, 2e-4)
    ends = (
        [config["resume_after"] - 1, len(config["steps"]) - 1]
        if config.get("resume_after")
        else [len(config["steps"]) - 1]
    )
    assert len(meta["cycles"]) == len(ends)
    for parameter in names + ["all"]:
        compare(
            np.vstack([cycle["time"][parameter] for cycle in meta["cycles"]]),
            meta["before"][parameter],
            1e-8,
        )
    held = meta["held"]
    series = payload["actual"]["series"]
    compare(held["before"]["t"], series["times"], 1e-8)
    compare(held["before"]["time"]["all"], series["time_all"], 1e-8)
    for variable in config["variables"]:
        compare(held["before"]["forward"][variable], series["forward"][variable], 1e-8)
        compare(held["before"]["phase"][variable]["all"], series["phase_all"][variable], 1e-8)
        compare(held["before"]["fixed"][variable]["all"], series["fixed_all"][variable], 1e-8)
    _same_snapshot(held["after"], held["before"])
    for label in ("copy", "combined"):
        _same_snapshot(held[label], held["before"])
    _same_snapshot(held["first"], held["before"], end="first")
    _same_snapshot(held["last"], held["before"], end="last")
    assert meta["alias_isolated"] == {"time": True, "phase": True, "fixed": True, "forward": True}
    for label in ("source", "copy", "first", "last", "combined"):
        assert meta["plain_dict"][label] == {"time": True, "phase": True}
    for variable in config["variables"]:
        for parameter in names:
            column = np.asarray(held["before"]["fixed"][variable][parameter])
            assert column.shape == (meta["samples"], 1)
    _same_steps(held["steps_after"], held["steps_before"])
    _same_steps([held["left_after"]], [held["left_before"]])
    tolerance = 3e-5 if config["model"] == "analytic" else 3e-4
    for variable in config["variables"]:
        pieces = [np.asarray(cycle["fixed"][variable]["all"]) for cycle in meta["cycles"]]
        compare(np.concatenate(pieces), held["before"]["fixed"][variable]["all"], 1e-8)
        for cycle in meta["cycles"]:
            _check_named(
                cycle["fixed"][variable],
                cycle["fixed"][variable]["all"],
                names,
                tolerance,
                tolerance,
            )
    for cycle, endpoint in zip(meta["cycles"], ends, strict=True):
        compare(cycle["end_time"], expected["values"]["end_times"][endpoint], 1e-3)
        for vi, variable in enumerate(config["variables"]):
            for pi, parameter in enumerate(names):
                compare(
                    cycle["phase_end"][variable][parameter],
                    [[expected["derivatives"]["end_values"][endpoint][vi][pi]]],
                    tolerance,
                    tolerance,
                )
