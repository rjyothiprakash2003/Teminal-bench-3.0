"""Verifier for streaming-window-aggregator.

Harness follows the brinewire-session-decoder pattern: snapshot sealed expects
into memory, remove them from disk, and run the documented
`/app/src/aggregator.py` unprivileged with setpriv. Visible literals decide
contested rules; sealed streams scale the same rules on inputs the agent never
sees. Refusal checks plant a pre-existing `--out` sentinel so "write nothing"
means the bytes are unchanged, not merely that the harness cleared the path.
"""

from __future__ import annotations

import json
import os
import resource
import shutil
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

APP = Path("/app")
SRC = APP / "src" / "aggregator.py"
TESTS = Path("/tests")
SEALED = TESTS / "sealed"
WORK = Path("/work/out")
SANDBOX_ROOT = Path("/sandbox")

SANDBOX_UID = 2000
SANDBOX_GID = 2000
RUN_TIMEOUT = 20
# Pre-existing --out contents that a refusal must leave untouched (contract:
# write nothing to --out).
OUT_SENTINEL = b"sentinel\n"


def _snapshot_sealed() -> dict[str, dict[str, bytes]]:
    """Load every sealed case into memory before the tree is removed."""
    out: dict[str, dict[str, bytes]] = {}
    for case in sorted(p for p in SEALED.iterdir() if p.is_dir()):
        out[case.name] = {
            "config": (case / "config.json").read_bytes(),
            "events": (case / "events.jsonl").read_bytes(),
            "expect": (case / "expect.json").read_bytes(),
        }
    return out


SEALED_SNAP = _snapshot_sealed()
SEALED_CASES = sorted(SEALED_SNAP)


def _prepare_sandbox() -> None:
    """Sandbox home + execute bit on the documented aggregator path."""
    assert SRC.is_file() and not SRC.is_symlink()
    SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)
    os.chown(SANDBOX_ROOT, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    # setpriv must be able to traverse to and execute /app/src/aggregator.py.
    # 0711 on dirs: traverse without listing. SRC itself is world-readable/exec.
    try:
        os.chmod(APP, 0o711)
        os.chmod(SRC.parent, 0o711)
        os.chmod(SRC, 0o755)
    except OSError:
        # Local harness may mount /app read-only; farm trees are writable.
        pass


_prepare_sandbox()
# Remove sealed expects from disk so a sandbox symlink attack cannot target them.
shutil.rmtree(SEALED)
os.chmod(TESTS, 0o700)
WORK.mkdir(parents=True, exist_ok=True)
os.chown(WORK, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)


def _apply_resource_limits() -> None:
    """Cap each aggregator process near the published per-run wall budget."""
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
    """Remove candidate-writable files under /sandbox (HOME for graded runs)."""
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


def stage_stream(config: dict, events: str | bytes, dest: Path) -> Path:
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    (dest / "config.json").write_text(json.dumps(config) + "\n")
    if isinstance(events, bytes):
        (dest / "events.jsonl").write_bytes(events)
    else:
        (dest / "events.jsonl").write_text(events)
    for p in [dest, dest / "config.json", dest / "events.jsonl"]:
        os.chown(p, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    return dest


def materialize_sealed(name: str, dest: Path) -> Path:
    """Stage a sealed input stream without writing expect bytes to disk."""
    snap = SEALED_SNAP[name]
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    (dest / "config.json").write_bytes(snap["config"])
    (dest / "events.jsonl").write_bytes(snap["events"])
    for p in [dest, dest / "config.json", dest / "events.jsonl"]:
        os.chown(p, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    return dest


def read_out_bytes(out: Path) -> bytes:
    """Read output without following a sandbox-planted symlink (O_NOFOLLOW)."""
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


def run_agg(
    stream: Path, out: Path, *, clear_out: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run `/app/src/aggregator.py` under setpriv. Does not pre-create out.parent.

    When clear_out is True (success path), remove any prior --out so the run
    must create fresh bytes. Refusal checks pass clear_out=False after planting
    a sentinel the contract requires leave untouched.
    """
    if clear_out and (out.exists() or out.is_symlink()):
        out.unlink()
    # Ensure parent of nested outs is sandbox-writable when it already exists.
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
        sys.executable,
        str(SRC),
        "--stream",
        str(stream),
        "--out",
        str(out),
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


def assert_success_bytes(stream: Path, expected: str, out: Path) -> None:
    proc = run_agg(stream, out, clear_out=True)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == ""
    got = read_out_bytes(out)
    want = expected.encode("utf-8")
    if got != want:
        # Compare outside pytest's == so a wrong indent/shape does not dump two
        # multi-kilobyte JSON strings into the failure (that path has timed out
        # the 1800s verifier budget before reward.txt is written).
        n = min(len(got), len(want))
        idx = next((i for i in range(n) if got[i] != want[i]), n)
        raise AssertionError(
            f"--out mismatch: got {len(got)} bytes, expected {len(want)} bytes; "
            f"first difference at offset {idx}"
        )
    _drain_sandbox()


def assert_refuse(stream: Path, msg: str, out: Path) -> None:
    """Refuse must not create or alter --out (sentinel must survive)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    os.chown(out.parent, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    if out.exists() or out.is_symlink():
        out.unlink()
    out.write_bytes(OUT_SENTINEL)
    os.chown(out, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    os.chmod(out, 0o644)
    proc = run_agg(stream, out, clear_out=False)
    assert proc.returncode == 2, (proc.returncode, proc.stderr)
    assert proc.stdout == ""
    assert proc.stderr == msg + "\n"
    assert out.exists() and not out.is_symlink()
    assert read_out_bytes(out) == OUT_SENTINEL
    _drain_sandbox()


def test_aggregator_source_present():
    """Graded invocations use the documented /app/src/aggregator.py path."""
    assert SRC.is_file() and not SRC.is_symlink()
    assert SRC.resolve() == Path("/app/src/aggregator.py").resolve()


# --- Arrival / watermark ---


def test_visible_arrival_order_drops_late_event():
    expected = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 10,
              "end": 20,
              "totals": {
                "a": 1
              }
            },
            {
              "start": 15,
              "end": 25,
              "totals": {
                "a": 5
              }
            },
            {
              "start": 20,
              "end": 30,
              "totals": {
                "a": 4
              }
            }
          ]
        }
        """
    )
    s = stage_stream(
        {"size": 10, "slide": 5, "lateness": 2},
        '{"key":"a","ts":15,"val":1}\n'
        '{"key":"a","ts":8,"val":10}\n'
        '{"key":"a","ts":20,"val":4}\n',
        WORK / "vis_late_a",
    )
    assert_success_bytes(s, expected, WORK / "nest_late_a" / "out.json")


def test_visible_arrival_order_second_site():
    expected = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 16,
              "end": 24,
              "totals": {
                "k": 2
              }
            },
            {
              "start": 20,
              "end": 28,
              "totals": {
                "k": 5
              }
            },
            {
              "start": 24,
              "end": 32,
              "totals": {
                "k": 3
              }
            }
          ]
        }
        """
    )
    s = stage_stream(
        {"size": 8, "slide": 4, "lateness": 1},
        '{"key":"k","ts":20,"val":2}\n'
        '{"key":"k","ts":14,"val":9}\n'
        '{"key":"k","ts":24,"val":3}\n',
        WORK / "vis_late_b",
    )
    assert_success_bytes(s, expected, WORK / "nest_late_b" / "out.json")


def test_visible_lateness_subtracted_from_max_seen():
    """ts=9 is accepted when lateness=2 (watermark 8), not when ignored."""
    expected = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 0,
              "end": 10,
              "totals": {
                "a": 1
              }
            },
            {
              "start": 10,
              "end": 20,
              "totals": {
                "a": 1
              }
            }
          ]
        }
        """
    )
    s = stage_stream(
        {"size": 10, "slide": 10, "lateness": 2},
        '{"key":"a","ts":10,"val":1}\n'
        '{"key":"a","ts":9,"val":1}\n',
        WORK / "vis_lateness",
    )
    assert_success_bytes(s, expected, WORK / "vis_lateness.json")


def test_visible_event_on_watermark_accepted():
    """ts == watermark uses strict <, so the event is accepted."""
    expected = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 0,
              "end": 10,
              "totals": {
                "a": 1
              }
            },
            {
              "start": 10,
              "end": 20,
              "totals": {
                "a": 1
              }
            }
          ]
        }
        """
    )
    s = stage_stream(
        {"size": 10, "slide": 10, "lateness": 2},
        '{"key":"a","ts":10,"val":1}\n'
        '{"key":"a","ts":8,"val":1}\n',
        WORK / "vis_wm_exact",
    )
    assert_success_bytes(s, expected, WORK / "vis_wm_exact.json")


def test_visible_max_seen_preserved_after_accepted_descent():
    """Accepted downward ts must not reset max_seen for later lateness."""
    expected = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 0,
              "end": 10,
              "totals": {
                "a": 1
              }
            },
            {
              "start": 10,
              "end": 20,
              "totals": {
                "a": 1
              }
            }
          ]
        }
        """
    )
    s = stage_stream(
        {"size": 10, "slide": 10, "lateness": 2},
        '{"key":"a","ts":10,"val":1}\n'
        '{"key":"a","ts":9,"val":1}\n'
        '{"key":"a","ts":7,"val":1}\n',
        WORK / "vis_max_preserve",
    )
    assert_success_bytes(s, expected, WORK / "vis_max_preserve.json")


def test_visible_on_watermark_not_late_with_intervening_discard():
    """ts == watermark is accepted; a prior discarded lower ts must not change that."""
    expected = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 0,
              "end": 10,
              "totals": {
                "a": 3
              }
            },
            {
              "start": 10,
              "end": 20,
              "totals": {
                "a": 2
              }
            }
          ]
        }
        """
    )
    s = stage_stream(
        {"size": 10, "slide": 10, "lateness": 2},
        '{"key":"a","ts":10,"val":1}\n'
        '{"key":"a","ts":7,"val":9}\n'
        '{"key":"a","ts":8,"val":3}\n'
        '{"key":"a","ts":12,"val":1}\n',
        WORK / "vis_disc_adv",
    )
    assert_success_bytes(s, expected, WORK / "vis_disc_adv.json")


def test_visible_overlap_after_late_drop():
    """Overlapping assignment after an arrival-order late drop."""
    expected = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 15,
              "end": 25,
              "totals": {
                "x": 1
              }
            },
            {
              "start": 20,
              "end": 30,
              "totals": {
                "x": 1
              }
            }
          ]
        }
        """
    )
    s = stage_stream(
        {"size": 10, "slide": 5, "lateness": 2},
        '{"key":"x","ts":20,"val":1}\n'
        '{"key":"x","ts":15,"val":8}\n'
        '{"key":"x","ts":17,"val":2}\n',
        WORK / "vis_ov_late",
    )
    assert_success_bytes(s, expected, WORK / "vis_ov_late.json")


# --- Windows ---


def test_visible_overlapping_windows():
    expected = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 0,
              "end": 10,
              "totals": {
                "x": 2
              }
            },
            {
              "start": 5,
              "end": 15,
              "totals": {
                "x": 2
              }
            }
          ]
        }
        """
    )
    s = stage_stream(
        {"size": 10, "slide": 5, "lateness": 100},
        '{"key":"x","ts":7,"val":2}\n',
        WORK / "vis_ov_a",
    )
    assert_success_bytes(s, expected, WORK / "vis_ov_a.json")


def test_visible_overlapping_windows_second_site():
    expected = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 0,
              "end": 12,
              "totals": {
                "y": 5
              }
            },
            {
              "start": 3,
              "end": 15,
              "totals": {
                "y": 5
              }
            },
            {
              "start": 6,
              "end": 18,
              "totals": {
                "y": 5
              }
            },
            {
              "start": 9,
              "end": 21,
              "totals": {
                "y": 5
              }
            }
          ]
        }
        """
    )
    s = stage_stream(
        {"size": 12, "slide": 3, "lateness": 100},
        '{"key":"y","ts":9,"val":5}\n',
        WORK / "vis_ov_b",
    )
    assert_success_bytes(s, expected, WORK / "vis_ov_b.json")


def test_visible_half_open_boundary():
    expected = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 5,
              "end": 15,
              "totals": {
                "x": 1
              }
            },
            {
              "start": 10,
              "end": 20,
              "totals": {
                "x": 1
              }
            }
          ]
        }
        """
    )
    s = stage_stream(
        {"size": 10, "slide": 5, "lateness": 100},
        '{"key":"x","ts":10,"val":1}\n',
        WORK / "vis_bound_a",
    )
    assert_success_bytes(s, expected, WORK / "vis_bound_a.json")


def test_visible_half_open_boundary_second_site():
    expected = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 3,
              "end": 9,
              "totals": {
                "z": 4
              }
            },
            {
              "start": 6,
              "end": 12,
              "totals": {
                "z": 4
              }
            }
          ]
        }
        """
    )
    s = stage_stream(
        {"size": 6, "slide": 3, "lateness": 100},
        '{"key":"z","ts":6,"val":4}\n',
        WORK / "vis_bound_b",
    )
    assert_success_bytes(s, expected, WORK / "vis_bound_b.json")


def test_visible_slide_greater_than_size_gap():
    expected = textwrap.dedent(
        """\
        {
          "windows": []
        }
        """
    )
    s = stage_stream(
        {"size": 2, "slide": 5, "lateness": 0},
        '{"key":"a","ts":3,"val":1}\n',
        WORK / "vis_gap",
    )
    assert_success_bytes(s, expected, WORK / "vis_gap.json")


def test_visible_slide_greater_than_size_hit():
    """slide > size still emits a window when ts lands on a slide-aligned start."""
    expected = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 2,
              "end": 3,
              "totals": {
                "a": 1
              }
            }
          ]
        }
        """
    )
    s = stage_stream(
        {"size": 1, "slide": 2, "lateness": 0},
        '{"key":"a","ts":2,"val":1}\n',
        _vs("sgt_hit"),
    )
    assert_success_bytes(s, expected, _vs("sgt_hit_out") / "out.json")


def test_visible_slide_greater_than_size_hit_second_site():
    expected = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 5,
              "end": 8,
              "totals": {
                "b": 2
              }
            }
          ]
        }
        """
    )
    s = stage_stream(
        {"size": 3, "slide": 5, "lateness": 0},
        '{"key":"b","ts":5,"val":2}\n',
        _vs("sgt_hit2"),
    )
    assert_success_bytes(s, expected, _vs("sgt_hit2_out") / "out.json")


def test_visible_negative_window_starts_filtered():
    expected = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 0,
              "end": 10,
              "totals": {
                "a": 1
              }
            }
          ]
        }
        """
    )
    s = stage_stream(
        {"size": 10, "slide": 5, "lateness": 0},
        '{"key":"a","ts":0,"val":1}\n',
        WORK / "vis_neg_start",
    )
    assert_success_bytes(s, expected, WORK / "vis_neg_start.json")


def test_visible_zero_val_creates_window():
    expected = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 0,
              "end": 10,
              "totals": {
                "z": 0
              }
            }
          ]
        }
        """
    )
    s = stage_stream(
        {"size": 10, "slide": 10, "lateness": 0},
        '{"key":"z","ts":1,"val":0}\n',
        WORK / "vis_zero",
    )
    assert_success_bytes(s, expected, WORK / "vis_zero.json")


def test_visible_bytewise_key_order():
    expected = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 0,
              "end": 10,
              "totals": {
                "B": 2,
                "a": 1
              }
            }
          ]
        }
        """
    )
    s = stage_stream(
        {"size": 10, "slide": 10, "lateness": 0},
        '{"key":"a","ts":1,"val":1}\n'
        '{"key":"B","ts":2,"val":2}\n',
        WORK / "vis_keys_case",
    )
    assert_success_bytes(s, expected, WORK / "vis_keys_case.json")


def test_visible_window_order_not_first_seen():
    expected = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 0,
              "end": 2,
              "totals": {
                "a": 1
              }
            },
            {
              "start": 4,
              "end": 6,
              "totals": {
                "a": 1
              }
            }
          ]
        }
        """
    )
    s = stage_stream(
        {"size": 2, "slide": 2, "lateness": 10},
        '{"key":"a","ts":4,"val":1}\n'
        '{"key":"a","ts":0,"val":1}\n',
        WORK / "vis_sort_ooo",
    )
    assert_success_bytes(s, expected, WORK / "vis_sort_ooo.json")


def test_visible_no_empty_windows_in_gaps():
    expected = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 0,
              "end": 2,
              "totals": {
                "a": 1
              }
            },
            {
              "start": 4,
              "end": 6,
              "totals": {
                "a": 1
              }
            }
          ]
        }
        """
    )
    s = stage_stream(
        {"size": 2, "slide": 2, "lateness": 0},
        '{"key":"a","ts":0,"val":1}\n'
        '{"key":"a","ts":4,"val":1}\n',
        WORK / "vis_empty_gap",
    )
    assert_success_bytes(s, expected, WORK / "vis_empty_gap.json")


def test_visible_large_ts_integer_window_start():
    expected = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 9007199254740990,
              "end": 9007199254740992,
              "totals": {
                "a": 1
              }
            }
          ]
        }
        """
    )
    s = stage_stream(
        {"size": 2, "slide": 2, "lateness": 0},
        '{"key":"a","ts":9007199254740991,"val":1}\n',
        WORK / "vis_large_ts",
    )
    assert_success_bytes(s, expected, WORK / "vis_large_ts.json")


def test_visible_large_integer_sum():
    expected = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 0,
              "end": 10,
              "totals": {
                "a": 18014398509481982
              }
            }
          ]
        }
        """
    )
    s = stage_stream(
        {"size": 10, "slide": 10, "lateness": 0},
        '{"key":"a","ts":1,"val":9007199254740991}\n'
        '{"key":"a","ts":1,"val":9007199254740991}\n',
        WORK / "vis_large_sum",
    )
    assert_success_bytes(s, expected, WORK / "vis_large_sum.json")


def test_visible_large_sum_not_binary64_rounded():
    """2^53−1 + 2 is exact as int; float64 accumulation yields 2^53."""
    expected = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 0,
              "end": 10,
              "totals": {
                "a": 9007199254740993
              }
            }
          ]
        }
        """
    )
    s = stage_stream(
        {"size": 10, "slide": 10, "lateness": 0},
        '{"key":"a","ts":1,"val":9007199254740991}\n'
        '{"key":"a","ts":2,"val":2}\n',
        _vs("sum_f64"),
    )
    assert_success_bytes(s, expected, _vs("sum_f64_out") / "out.json")


def test_visible_large_sum_not_binary64_rounded_second_site():
    expected = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 0,
              "end": 5,
              "totals": {
                "b": 9007199254740995
              }
            }
          ]
        }
        """
    )
    s = stage_stream(
        {"size": 5, "slide": 5, "lateness": 0},
        '{"key":"b","ts":1,"val":9007199254740991}\n'
        '{"key":"b","ts":3,"val":4}\n',
        _vs("sum_f64b"),
    )
    assert_success_bytes(s, expected, _vs("sum_f64b_out") / "out.json")


def test_visible_config_key_order_permuted_ok():
    expected = textwrap.dedent(
        """\
        {
          "windows": []
        }
        """
    )
    s = WORK / "vis_cfg_order"
    if s.exists():
        shutil.rmtree(s)
    s.mkdir(parents=True)
    (s / "config.json").write_text('{"slide":1,"lateness":0,"size":1}\n')
    (s / "events.jsonl").write_text("")
    assert_success_bytes(s, expected, WORK / "vis_cfg_order.json")


def test_visible_event_member_order_permuted_ok():
    """Event members may appear in any order — extract by name, not insertion."""
    expected = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 0,
              "end": 1,
              "totals": {
                "a": 1
              }
            }
          ]
        }
        """
    )
    s = stage_stream(
        {"size": 1, "slide": 1, "lateness": 0},
        '{"ts":0,"key":"a","val":1}\n',
        _vs("ev_ord"),
    )
    assert_success_bytes(s, expected, _vs("ev_ord_out") / "out.json")


def test_visible_event_member_order_permuted_ok_second_site():
    expected = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 0,
              "end": 1,
              "totals": {
                "b": 2
              }
            }
          ]
        }
        """
    )
    s = stage_stream(
        {"size": 1, "slide": 1, "lateness": 0},
        '{"val":2,"ts":0,"key":"b"}\n',
        _vs("ev_ord2"),
    )
    assert_success_bytes(s, expected, _vs("ev_ord2_out") / "out.json")


def test_visible_config_unicode_escaped_size_key_ok():
    """Decoded member names matter — \\u0073ize is size."""
    expected = textwrap.dedent(
        """\
        {
          "windows": []
        }
        """
    )
    s = _vs("cfg_esc")
    if s.exists():
        shutil.rmtree(s)
    s.mkdir(parents=True)
    (s / "config.json").write_text('{"\\u0073ize":1,"slide":1,"lateness":0}\n')
    (s / "events.jsonl").write_text("")
    assert_success_bytes(s, expected, _vs("cfg_esc_out"))


def test_visible_config_unicode_escaped_duplicate_size_refused():
    """Literal size and \\u0073ize decode to the same member name → bad config."""
    s = _vs("cfg_esc_dup")
    if s.exists():
        shutil.rmtree(s)
    s.mkdir(parents=True)
    (s / "config.json").write_text('{"size":1,"\\u0073ize":1,"slide":1,"lateness":0}\n')
    (s / "events.jsonl").write_text("")
    assert_refuse(s, "refuse: bad config", _vs("cfg_esc_dup_out"))


def test_visible_empty_events_yield_empty_windows():
    expected = textwrap.dedent(
        """\
        {
          "windows": []
        }
        """
    )
    s = stage_stream({"size": 10, "slide": 5, "lateness": 0}, "", WORK / "vis_empty")
    assert_success_bytes(s, expected, WORK / "vis_empty.json")


def test_visible_parent_mkdir_for_nested_out():
    """Program must create missing parent directories for --out."""
    expected = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 0,
              "end": 10,
              "totals": {
                "a": 1
              }
            }
          ]
        }
        """
    )
    s = stage_stream(
        {"size": 10, "slide": 10, "lateness": 0},
        '{"key":"a","ts":1,"val":1}\n',
        WORK / "vis_mkdir_src",
    )
    out = WORK / "deeply" / "nested" / "out.json"
    if out.parent.exists():
        shutil.rmtree(out.parent)
    assert_success_bytes(s, expected, out)


def test_visible_success_overwrites_existing_out():
    """Success must replace a pre-existing --out file (not require it absent)."""
    expected = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 0,
              "end": 10,
              "totals": {
                "a": 1
              }
            }
          ]
        }
        """
    )
    s = stage_stream(
        {"size": 10, "slide": 10, "lateness": 0},
        '{"key":"a","ts":1,"val":1}\n',
        _vs("exist_src"),
    )
    out = _vs("exist_out") / "out.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    os.chown(out.parent, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    out.write_bytes(b"stale-preexisting-bytes\n")
    os.chown(out, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    os.chmod(out, 0o644)
    proc = run_agg(s, out, clear_out=False)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == ""
    got = read_out_bytes(out)
    want = expected.encode("utf-8")
    if got != want:
        n = min(len(got), len(want))
        idx = next((i for i in range(n) if got[i] != want[i]), n)
        raise AssertionError(
            f"--out mismatch: got {len(got)} bytes, expected {len(want)} bytes; "
            f"first difference at offset {idx}"
        )
    _drain_sandbox()


def test_visible_event_line_leading_trailing_spaces_ok():
    """Spaces around a complete JSON object line are legal (json.loads)."""
    expected = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 0,
              "end": 10,
              "totals": {
                "a": 1
              }
            }
          ]
        }
        """
    )
    s = stage_stream(
        {"size": 10, "slide": 10, "lateness": 0},
        '  {"key":"a","ts":1,"val":1}  \n',
        _vs("sp_line"),
    )
    assert_success_bytes(s, expected, _vs("sp_line_out") / "out.json")


def test_visible_event_line_crlf_terminator_ok():
    """CRLF line endings: each record still ends with LF after the CR."""
    expected = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 0,
              "end": 10,
              "totals": {
                "a": 1
              }
            }
          ]
        }
        """
    )
    s = stage_stream(
        {"size": 10, "slide": 10, "lateness": 0},
        b'{"key":"a","ts":1,"val":1}\r\n',
        _vs("crlf_line"),
    )
    assert_success_bytes(s, expected, _vs("crlf_line_out") / "out.json")


def test_visible_wide_event_line_under_byte_cap():
    """A single in-bounds event line longer than 64KiB must still aggregate."""
    # Pad inside the JSON object with spaces so the physical line exceeds 65536 bytes.
    pad = " " * 65600
    line = '{"key":"a","ts":1,' + pad + '"val":1}\n'
    assert len(line.encode()) > 65536
    expected = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 0,
              "end": 10,
              "totals": {
                "a": 1
              }
            }
          ]
        }
        """
    )
    s = stage_stream(
        {"size": 10, "slide": 10, "lateness": 0},
        line,
        WORK / "vis_wide",
    )
    assert_success_bytes(s, expected, WORK / "vis_wide.json")


# --- Truncation / malformed lines ---


def test_visible_truncated_events_refused():
    s = stage_stream(
        {"size": 10, "slide": 10, "lateness": 0},
        b'{"key":"t","ts":1,"val":1}',
        WORK / "vs_c15d2d10d9",
    )
    assert_refuse(s, "refuse: truncated events", WORK / "vs_76fef22d2f")


def test_visible_truncated_after_good_line_refused():
    s = stage_stream(
        {"size": 10, "slide": 10, "lateness": 0},
        b'{"key":"t","ts":1,"val":1}\n{"key":"u","ts":2,"val":2}',
        WORK / "vs_d21c5af50b",
    )
    assert_refuse(s, "refuse: truncated events", WORK / "vs_9684329179")


def test_visible_invalid_utf8_line_refused():
    s = stage_stream(
        {"size": 10, "slide": 10, "lateness": 0},
        b"\xff\n",
        WORK / "vs_ab640579fd",
    )
    assert_refuse(s, "refuse: truncated events", WORK / "vs_723b4cb159")


def test_visible_malformed_json_line_refused():
    s = stage_stream(
        {"size": 10, "slide": 10, "lateness": 0},
        b"not-json\n",
        WORK / "vs_68562b24c7",
    )
    assert_refuse(s, "refuse: truncated events", WORK / "vs_2a1fdc93ff")


def test_visible_non_object_json_line_refused():
    s = stage_stream(
        {"size": 10, "slide": 10, "lateness": 0},
        b"42\n",
        WORK / "vs_60778421f5",
    )
    assert_refuse(s, "refuse: truncated events", WORK / "vs_cf382bcd17")


def test_visible_nan_line_refused_as_truncated():
    """Object line with NaN value → truncated events (nonfinite constants)."""
    s = stage_stream(
        {"size": 10, "slide": 10, "lateness": 0},
        b'{"key":"a","ts":0,"val":NaN}\n',
        WORK / "vs_60f9eb50aa",
    )
    assert_refuse(s, "refuse: truncated events", WORK / "vs_3d2645f907")


def test_visible_nan_ts_member_refused_as_truncated():
    """NaN in ts must be truncated events, not semantic bad event."""
    s = stage_stream(
        {"size": 1, "slide": 1, "lateness": 0},
        b'{"key":"a","ts":NaN,"val":1}\n',
        _vs("nan_ts"),
    )
    assert_refuse(s, "refuse: truncated events", _vs("nan_ts_out"))


def test_visible_nan_ts_member_refused_as_truncated_second_site():
    s = stage_stream(
        {"size": 1, "slide": 1, "lateness": 0},
        b'{"ts":NaN,"key":"b","val":0}\n',
        _vs("nan_ts2"),
    )
    assert_refuse(s, "refuse: truncated events", _vs("nan_ts2_out"))


def test_visible_infinity_line_refused_as_truncated():
    s = stage_stream(
        {"size": 10, "slide": 10, "lateness": 0},
        b"Infinity\n",
        _vs("inf_line"),
    )
    assert_refuse(s, "refuse: truncated events", _vs("inf_line_out"))


def test_visible_infinity_member_refused_as_truncated():
    """Object member Infinity must be truncated events, not bad event."""
    s = stage_stream(
        {"size": 1, "slide": 1, "lateness": 0},
        b'{"key":"a","ts":0,"val":Infinity}\n',
        _vs("inf_val"),
    )
    assert_refuse(s, "refuse: truncated events", _vs("inf_val_out"))


def test_visible_infinity_member_refused_as_truncated_second_site():
    s = stage_stream(
        {"size": 1, "slide": 1, "lateness": 0},
        b'{"key":"a","ts":Infinity,"val":1}\n',
        _vs("inf_ts"),
    )
    assert_refuse(s, "refuse: truncated events", _vs("inf_ts_out"))


def test_visible_neg_infinity_member_refused_as_truncated():
    """Object member -Infinity must be truncated events, not bad event."""
    s = stage_stream(
        {"size": 1, "slide": 1, "lateness": 0},
        b'{"key":"a","ts":0,"val":-Infinity}\n',
        _vs("neginf_val"),
    )
    assert_refuse(s, "refuse: truncated events", _vs("neginf_val_out"))


def test_visible_neg_infinity_member_refused_as_truncated_second_site():
    s = stage_stream(
        {"size": 1, "slide": 1, "lateness": 0},
        b'{"key":"a","ts":-Infinity,"val":1}\n',
        _vs("neginf_ts"),
    )
    assert_refuse(s, "refuse: truncated events", _vs("neginf_ts_out"))


# --- Bad config ---


def test_visible_bad_config_missing_key():
    s = WORK / "vs_f6fcacb7c0"
    if s.exists():
        shutil.rmtree(s)
    s.mkdir(parents=True)
    (s / "config.json").write_text('{"size":10,"slide":10}\n')
    (s / "events.jsonl").write_text("")
    assert_refuse(s, "refuse: bad config", WORK / "vs_f20e263663")


def test_visible_bad_config_missing_size():
    s = _vs("miss_sz")
    if s.exists():
        shutil.rmtree(s)
    s.mkdir(parents=True)
    (s / "config.json").write_text('{"slide":1,"lateness":0}\n')
    (s / "events.jsonl").write_text("")
    assert_refuse(s, "refuse: bad config", _vs("miss_sz_out"))


def test_visible_bad_config_missing_slide():
    s = _vs("miss_sl")
    if s.exists():
        shutil.rmtree(s)
    s.mkdir(parents=True)
    (s / "config.json").write_text('{"size":1,"lateness":0}\n')
    (s / "events.jsonl").write_text("")
    assert_refuse(s, "refuse: bad config", _vs("miss_sl_out"))


def test_visible_bad_config_zero_size():
    s = stage_stream({"size": 0, "slide": 10, "lateness": 0}, "", WORK / "vs_ec8da9da56")
    assert_refuse(s, "refuse: bad config", WORK / "vs_977378451f")


def test_visible_bad_config_zero_slide():
    s = stage_stream({"size": 10, "slide": 0, "lateness": 0}, "", WORK / "vs_d687ceb3af")
    assert_refuse(s, "refuse: bad config", WORK / "vs_a7ea2892a2")


def test_visible_bad_config_array_shape():
    s = WORK / "vs_0ee2fad32d"
    if s.exists():
        shutil.rmtree(s)
    s.mkdir(parents=True)
    (s / "config.json").write_text('["size","slide","lateness"]\n')
    (s / "events.jsonl").write_text("")
    assert_refuse(s, "refuse: bad config", WORK / "vs_87a62fc6f2")


def test_visible_bad_config_size_upper_bound():
    s = stage_stream(
        {"size": 2**53, "slide": 10, "lateness": 0}, "", WORK / "vs_c23ce92abf"
    )
    assert_refuse(s, "refuse: bad config", WORK / "vs_43cf4bfe51")


def test_visible_bad_config_negative_lateness():
    s = stage_stream(
        {"size": 10, "slide": 10, "lateness": -1}, "", WORK / "vs_c01b118391"
    )
    assert_refuse(s, "refuse: bad config", WORK / "vs_b784e358f2")


def test_visible_bad_config_float_size():
    s = WORK / "vs_7933b890d3"
    if s.exists():
        shutil.rmtree(s)
    s.mkdir(parents=True)
    (s / "config.json").write_text('{"size":1.5,"slide":1,"lateness":0}\n')
    (s / "events.jsonl").write_text("")
    assert_refuse(s, "refuse: bad config", WORK / "vs_5c568a3f46")


def test_visible_bad_config_invalid_utf8():
    s = WORK / "vs_9d17b0cf1f"
    if s.exists():
        shutil.rmtree(s)
    s.mkdir(parents=True)
    (s / "config.json").write_bytes(b"\xff\n")
    (s / "events.jsonl").write_text("")
    assert_refuse(s, "refuse: bad config", WORK / "vs_4affdf4232")


# --- Bad event ---


def test_visible_bad_event_extra_field():
    s = stage_stream(
        {"size": 10, "slide": 10, "lateness": 0},
        '{"key":"a","ts":1,"val":1,"extra":0}\n',
        WORK / "vs_a8439d625e",
    )
    assert_refuse(s, "refuse: bad event", WORK / "vs_fe7a65545c")


def test_visible_bad_event_bool_val():
    s = stage_stream(
        {"size": 10, "slide": 10, "lateness": 0},
        '{"key":"a","ts":1,"val":true}\n',
        WORK / "vs_89f2b2e82b",
    )
    assert_refuse(s, "refuse: bad event", WORK / "vs_887dccf302")


def test_visible_bad_event_missing_field():
    s = stage_stream(
        {"size": 10, "slide": 10, "lateness": 0},
        '{"key":"a","ts":1}\n',
        WORK / "vs_a8444c2535",
    )
    assert_refuse(s, "refuse: bad event", WORK / "vs_557fafdf83")


def test_visible_bad_event_bool_ts():
    s = stage_stream(
        {"size": 10, "slide": 10, "lateness": 0},
        '{"key":"a","ts":true,"val":1}\n',
        WORK / "vs_a19e41c733",
    )
    assert_refuse(s, "refuse: bad event", WORK / "vs_68e618349d")


def test_visible_bad_event_empty_key():
    s = stage_stream(
        {"size": 10, "slide": 10, "lateness": 0},
        '{"key":"","ts":1,"val":1}\n',
        WORK / "vs_26a51e2705",
    )
    assert_refuse(s, "refuse: bad event", WORK / "vs_c6be17ae1d")


def test_visible_bad_event_key_with_newline():
    s = stage_stream(
        {"size": 10, "slide": 10, "lateness": 0},
        '{"key":"a\\n","ts":0,"val":1}\n',
        WORK / "vs_e4ddb79836",
    )
    assert_refuse(s, "refuse: bad event", WORK / "vs_9dd449a7ce")


def test_visible_bad_event_overlong_key():
    s = stage_stream(
        {"size": 10, "slide": 10, "lateness": 0},
        json.dumps({"key": "a" * 65, "ts": 1, "val": 1}) + "\n",
        WORK / "vs_ac1f5388a9",
    )
    assert_refuse(s, "refuse: bad event", WORK / "vs_ca5921f9b3")


def test_visible_bad_event_ts_upper_bound():
    s = stage_stream(
        {"size": 10, "slide": 10, "lateness": 0},
        '{"key":"a","ts":9007199254740992,"val":1}\n',
        WORK / "vs_530ca9d80b",
    )
    assert_refuse(s, "refuse: bad event", WORK / "vs_b10383c099")


def test_visible_bad_event_val_upper_bound():
    s = stage_stream(
        {"size": 10, "slide": 10, "lateness": 0},
        '{"key":"a","ts":1,"val":9007199254740992}\n',
        WORK / "vs_7df42cc4fa",
    )
    assert_refuse(s, "refuse: bad event", WORK / "vs_9f439f2d9b")


def test_visible_bad_event_negative_ts():
    s = stage_stream(
        {"size": 10, "slide": 10, "lateness": 0},
        '{"key":"a","ts":-1,"val":1}\n',
        WORK / "vs_67c4ffde4b",
    )
    assert_refuse(s, "refuse: bad event", WORK / "vs_e850e2d566")


def test_visible_bad_event_nonascii_key():
    s = stage_stream(
        {"size": 10, "slide": 10, "lateness": 0},
        '{"key":"é","ts":1,"val":1}\n',
        WORK / "vs_5f01872709",
    )
    assert_refuse(s, "refuse: bad event", WORK / "vs_82ba915e03")


# --- Bad stream ---


def test_visible_bad_stream_extra_file():
    s = stage_stream({"size": 10, "slide": 10, "lateness": 0}, "", WORK / "vs_cc603f71ad")
    (s / "notes.txt").write_text("nope\n")
    assert_refuse(s, "refuse: bad stream", WORK / "vs_6356f55291")


def test_visible_bad_stream_missing_events():
    s = WORK / "vs_55348c694e"
    if s.exists():
        shutil.rmtree(s)
    s.mkdir(parents=True)
    (s / "config.json").write_text('{"size":10,"slide":10,"lateness":0}\n')
    assert_refuse(s, "refuse: bad stream", WORK / "vs_6e8a7e40d1")


def test_visible_bad_stream_missing_config():
    s = WORK / "vs_dc5b5c8dbc"
    if s.exists():
        shutil.rmtree(s)
    s.mkdir(parents=True)
    (s / "events.jsonl").write_text("")
    assert_refuse(s, "refuse: bad stream", WORK / "vs_2eb6cd8ae9")


def test_visible_bad_stream_nondirectory():
    s = WORK / "vs_9088471948"
    if s.exists():
        if s.is_dir():
            shutil.rmtree(s)
        else:
            s.unlink()
    s.write_text("not a dir\n")
    assert_refuse(s, "refuse: bad stream", WORK / "vs_b1b042fb65")


def test_visible_bad_stream_config_is_directory():
    s = WORK / "vs_e65e1a3602"
    if s.exists():
        shutil.rmtree(s)
    s.mkdir(parents=True)
    (s / "config.json").mkdir()
    (s / "events.jsonl").write_text("")
    assert_refuse(s, "refuse: bad stream", WORK / "vs_7035a79b74")


def test_visible_bad_stream_events_is_directory():
    s = WORK / "vs_1919d1a59d"
    if s.exists():
        shutil.rmtree(s)
    s.mkdir(parents=True)
    (s / "config.json").write_text('{"size":10,"slide":10,"lateness":0}\n')
    (s / "events.jsonl").mkdir()
    assert_refuse(s, "refuse: bad stream", WORK / "vs_352dff6c05")


def test_visible_bad_stream_config_symlink():
    """Required member must be a regular file, not a symlink (contract stream rule)."""
    root = WORK / "vs_3d19068847"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    target = WORK / "vs_1117d2faa5"
    target.write_text('{"size":1,"slide":1,"lateness":0}\n')
    (root / "config.json").symlink_to(target)
    (root / "events.jsonl").write_text("")
    assert_refuse(root, "refuse: bad stream", WORK / "vs_27534fe329")


def test_visible_bad_stream_events_symlink():
    root = WORK / "vs_c2c6b38516"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    (root / "config.json").write_text('{"size":1,"slide":1,"lateness":0}\n')
    target = WORK / "vs_d6af41ec2b"
    target.write_text("")
    (root / "events.jsonl").symlink_to(target)
    assert_refuse(root, "refuse: bad stream", WORK / "vs_0382dd96ea")


def test_visible_bad_stream_extra_subdirectory():
    s = stage_stream({"size": 10, "slide": 10, "lateness": 0}, "", WORK / "vs_7f64a34e13")
    (s / "scratch").mkdir()
    assert_refuse(s, "refuse: bad stream", WORK / "vs_e0054198ec")


def test_visible_bad_stream_hidden_extra_member():
    """Dotfile members count — glob('*') must not accept the stream."""
    s = stage_stream({"size": 1, "slide": 1, "lateness": 0}, "", _vs("hid"))
    (s / ".hidden").write_text("")
    assert_refuse(s, "refuse: bad stream", _vs("hid_out"))


def test_visible_bad_stream_hidden_extra_member_second_site():
    s = stage_stream({"size": 1, "slide": 1, "lateness": 0}, "", _vs("hid2"))
    (s / ".DS_Store").write_text("x")
    assert_refuse(s, "refuse: bad stream", _vs("hid2_out"))


def test_visible_bad_config_malformed_json():
    s = WORK / "vs_c3b46ecdc7"
    if s.exists():
        shutil.rmtree(s)
    s.mkdir(parents=True)
    (s / "config.json").write_text('{"size":1,"slide":1,"lateness":0\n')
    (s / "events.jsonl").write_text("")
    assert_refuse(s, "refuse: bad config", WORK / "vs_c661d8831d")


def test_visible_bad_config_extra_key():
    s = WORK / "vs_3abb15b533"
    if s.exists():
        shutil.rmtree(s)
    s.mkdir(parents=True)
    (s / "config.json").write_text('{"size":1,"slide":1,"lateness":0,"extra":0}\n')
    (s / "events.jsonl").write_text("")
    assert_refuse(s, "refuse: bad config", WORK / "vs_5b3370fe91")


def test_visible_bad_config_bool_size():
    s = WORK / "vs_c9b74e56ba"
    if s.exists():
        shutil.rmtree(s)
    s.mkdir(parents=True)
    (s / "config.json").write_text('{"size":true,"slide":1,"lateness":0}\n')
    (s / "events.jsonl").write_text("")
    assert_refuse(s, "refuse: bad config", WORK / "vs_43d9c47c1a")


def test_visible_bad_config_float_slide():
    s = WORK / "vs_6561b265d6"
    if s.exists():
        shutil.rmtree(s)
    s.mkdir(parents=True)
    (s / "config.json").write_text('{"size":1,"slide":1.5,"lateness":0}\n')
    (s / "events.jsonl").write_text("")
    assert_refuse(s, "refuse: bad config", WORK / "vs_bfc25e97a2")


def test_visible_bad_config_float_lateness():
    s = WORK / "vs_0b64ad07f4"
    if s.exists():
        shutil.rmtree(s)
    s.mkdir(parents=True)
    (s / "config.json").write_text('{"size":1,"slide":1,"lateness":0.5}\n')
    (s / "events.jsonl").write_text("")
    assert_refuse(s, "refuse: bad config", WORK / "vs_3ab4d9b3f7")


def test_visible_bad_config_slide_upper_bound():
    s = stage_stream(
        {"size": 1, "slide": 2**53, "lateness": 0}, "", WORK / "vs_347630f7e6"
    )
    assert_refuse(s, "refuse: bad config", WORK / "vs_88cd48dc10")


def test_visible_bad_config_lateness_upper_bound():
    s = stage_stream(
        {"size": 1, "slide": 1, "lateness": 2**53}, "", WORK / "vs_f936594f81"
    )
    assert_refuse(s, "refuse: bad config", WORK / "vs_1d2996cea4")


def test_visible_invalid_utf8_inside_json_object_refused():
    s = stage_stream(
        {"size": 10, "slide": 10, "lateness": 0},
        b'{"key":"\xff","ts":1,"val":1}\n',
        WORK / "vs_e53165a6e0",
    )
    assert_refuse(s, "refuse: truncated events", WORK / "vs_efadf5dc76")


def test_visible_unicode_whitespace_line_ignored():
    """U+2003 (em space) only line is whitespace and must be ignored."""
    events = "\u2003\n" + '{"key":"w","ts":1,"val":1}\n'
    s = stage_stream({"size": 10, "slide": 10, "lateness": 0}, events, WORK / "vis_ws_em")
    expected = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 0,
              "end": 10,
              "totals": {
                "w": 1
              }
            }
          ]
        }
        """
    )
    assert_success_bytes(s, expected, WORK / "vis_ws_em.json")


def test_visible_nbsp_whitespace_line_ignored():
    """U+00A0 is Python str.strip() whitespace — not only U+2003."""
    events = "\u00a0\n" + '{"key":"a","ts":1,"val":1}\n'
    s = stage_stream({"size": 10, "slide": 10, "lateness": 0}, events, _vs("ws_nbsp"))
    expected = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 0,
              "end": 10,
              "totals": {
                "a": 1
              }
            }
          ]
        }
        """
    )
    assert_success_bytes(s, expected, _vs("ws_nbsp_out"))


def test_visible_ideographic_space_whitespace_line_ignored():
    """Second site: U+3000 ideographic space via str.strip()."""
    events = "\u3000\n" + '{"key":"b","ts":1,"val":2}\n'
    s = stage_stream({"size": 10, "slide": 10, "lateness": 0}, events, _vs("ws_ideo"))
    expected = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 0,
              "end": 10,
              "totals": {
                "b": 2
              }
            }
          ]
        }
        """
    )
    assert_success_bytes(s, expected, _vs("ws_ideo_out"))


def test_visible_ogham_space_whitespace_line_ignored():
    """U+1680 OGHAM SPACE MARK is str.strip() whitespace (beyond the shown set)."""
    events = "\u1680\n" + '{"key":"a","ts":1,"val":1}\n'
    s = stage_stream({"size": 10, "slide": 10, "lateness": 0}, events, _vs("ws_ogham"))
    expected = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 0,
              "end": 10,
              "totals": {
                "a": 1
              }
            }
          ]
        }
        """
    )
    assert_success_bytes(s, expected, _vs("ws_ogham_out"))


def test_visible_en_space_whitespace_line_ignored():
    """Second site: U+2002 EN SPACE via str.strip()."""
    events = "\u2002\n" + '{"key":"c","ts":1,"val":3}\n'
    s = stage_stream({"size": 10, "slide": 10, "lateness": 0}, events, _vs("ws_en"))
    expected = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 0,
              "end": 10,
              "totals": {
                "c": 3
              }
            }
          ]
        }
        """
    )
    assert_success_bytes(s, expected, _vs("ws_en_out"))


def test_visible_thin_space_whitespace_line_ignored():
    """U+2009 THIN SPACE is str.strip() whitespace beyond the previously sampled set."""
    events = "\u2009\n" + '{"key":"a","ts":1,"val":1}\n'
    s = stage_stream({"size": 10, "slide": 10, "lateness": 0}, events, _vs("ws_thin"))
    expected = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 0,
              "end": 10,
              "totals": {
                "a": 1
              }
            }
          ]
        }
        """
    )
    assert_success_bytes(s, expected, _vs("ws_thin_out"))


def test_visible_figure_space_whitespace_line_ignored():
    """U+2007 FIGURE SPACE via str.strip()."""
    events = "\u2007\n" + '{"key":"d","ts":1,"val":4}\n'
    s = stage_stream({"size": 10, "slide": 10, "lateness": 0}, events, _vs("ws_fig"))
    expected = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 0,
              "end": 10,
              "totals": {
                "d": 4
              }
            }
          ]
        }
        """
    )
    assert_success_bytes(s, expected, _vs("ws_fig_out"))


def test_visible_malformed_json_after_valid_line_refuses_with_no_out():
    s = stage_stream(
        {"size": 10, "slide": 10, "lateness": 0},
        b'{"key":"a","ts":1,"val":1}\nnot-json\n',
        WORK / "vs_beb278a2e8",
    )
    assert_refuse(s, "refuse: truncated events", WORK / "vs_d12cfa374e")


def test_visible_key_with_digit_accepted():
    s = stage_stream(
        {"size": 10, "slide": 10, "lateness": 0},
        '{"key":"a1","ts":1,"val":1}\n',
        WORK / "vis_key_digit",
    )
    expected = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 0,
              "end": 10,
              "totals": {
                "a1": 1
              }
            }
          ]
        }
        """
    )
    assert_success_bytes(s, expected, WORK / "vis_key_digit.json")


def test_visible_key_with_underscore_accepted():
    s = stage_stream(
        {"size": 10, "slide": 10, "lateness": 0},
        '{"key":"a_b","ts":1,"val":1}\n',
        WORK / "vis_key_us",
    )
    expected = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 0,
              "end": 10,
              "totals": {
                "a_b": 1
              }
            }
          ]
        }
        """
    )
    assert_success_bytes(s, expected, WORK / "vis_key_us.json")


def test_visible_key_leading_digit_accepted():
    """Keys may start with a digit — not identifier-style leading letter only."""
    s = stage_stream(
        {"size": 1, "slide": 1, "lateness": 0},
        '{"key":"1","ts":0,"val":1}\n',
        _vs("key_ldig"),
    )
    expected = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 0,
              "end": 1,
              "totals": {
                "1": 1
              }
            }
          ]
        }
        """
    )
    assert_success_bytes(s, expected, _vs("key_ldig_out") / "out.json")


def test_visible_key_leading_underscore_accepted():
    s = stage_stream(
        {"size": 1, "slide": 1, "lateness": 0},
        '{"key":"_a","ts":0,"val":2}\n',
        _vs("key_lus"),
    )
    expected = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 0,
              "end": 1,
              "totals": {
                "_a": 2
              }
            }
          ]
        }
        """
    )
    assert_success_bytes(s, expected, _vs("key_lus_out") / "out.json")


def test_visible_key_len_64_accepted():
    key = "a" * 64
    s = stage_stream(
        {"size": 10, "slide": 10, "lateness": 0},
        json.dumps({"key": key, "ts": 1, "val": 1}) + "\n",
        WORK / "vis_key_64",
    )
    expected = (
        "{\n"
        '  "windows": [\n'
        "    {\n"
        '      "start": 0,\n'
        '      "end": 10,\n'
        '      "totals": {\n'
        f'        "{key}": 1\n'
        "      }\n"
        "    }\n"
        "  ]\n"
        "}\n"
    )
    assert_success_bytes(s, expected, WORK / "vis_key_64.json")


def test_visible_bad_event_integral_float_ts():
    s = stage_stream(
        {"size": 10, "slide": 10, "lateness": 0},
        '{"key":"a","ts":1.0,"val":1}\n',
        WORK / "vs_bb67b57ecb",
    )
    assert_refuse(s, "refuse: bad event", WORK / "vs_d867958087")


def test_visible_bad_event_integral_float_val():
    s = stage_stream(
        {"size": 10, "slide": 10, "lateness": 0},
        '{"key":"a","ts":1,"val":1.0}\n',
        WORK / "vs_88f52fff2c",
    )
    assert_refuse(s, "refuse: bad event", WORK / "vs_b958058344")


def test_visible_bad_event_val_lower_bound():
    s = stage_stream(
        {"size": 10, "slide": 10, "lateness": 0},
        '{"key":"a","ts":1,"val":-9007199254740992}\n',
        WORK / "vs_5ffb710b81",
    )
    assert_refuse(s, "refuse: bad event", WORK / "vs_7f99e83df9")


def test_visible_array_json_line_refused_as_truncated():
    """Complete JSON array is not a JSON object → truncated (line rule), not bad event."""
    s = stage_stream(
        {"size": 1, "slide": 1, "lateness": 0},
        b"[]\n",
        WORK / "vs_826448e8e7",
    )
    assert_refuse(s, "refuse: truncated events", WORK / "vs_0ea19d7158")


def test_visible_array_with_nested_duplicate_still_truncated():
    """Top-level array → truncated even if a nested object has duplicate names."""
    s = stage_stream(
        {"size": 10, "slide": 5, "lateness": 2},
        '[{"x":1,"x":2}]\n',
        _vs("arr_nest_dup"),
    )
    assert_refuse(s, "refuse: truncated events", _vs("arr_nest_dup_out"))


def test_visible_string_json_line_refused_as_truncated():
    s = stage_stream(
        {"size": 1, "slide": 1, "lateness": 0},
        '"hello"\n',
        _vs("str_line"),
    )
    assert_refuse(s, "refuse: truncated events", _vs("str_line_out"))


def test_visible_boolean_json_line_refused_as_truncated():
    s = stage_stream(
        {"size": 1, "slide": 1, "lateness": 0},
        "true\n",
        _vs("bool_line"),
    )
    assert_refuse(s, "refuse: truncated events", _vs("bool_line_out"))


def test_visible_bad_event_missing_key_field():
    s = stage_stream(
        {"size": 10, "slide": 10, "lateness": 0},
        '{"ts":1,"val":1}\n',
        WORK / "vs_454a4126bf",
    )
    assert_refuse(s, "refuse: bad event", WORK / "vs_0b03d8253a")


def test_visible_bad_event_key_with_hyphen():
    s = stage_stream(
        {"size": 10, "slide": 10, "lateness": 0},
        '{"key":"a-b","ts":1,"val":1}\n',
        WORK / "vs_709742ad48",
    )
    assert_refuse(s, "refuse: bad event", WORK / "vs_ccb662e8dc")


def test_visible_bad_event_key_exclamation_refused():
    """Punctuation outside [A-Za-z0-9_] — not only hyphen/bracket samples."""
    s = stage_stream(
        {"size": 10, "slide": 10, "lateness": 0},
        '{"key":"!","ts":1,"val":1}\n',
        _vs("key_bang"),
    )
    assert_refuse(s, "refuse: bad event", _vs("key_bang_out"))


def test_visible_bad_event_key_at_sign_refused():
    s = stage_stream(
        {"size": 10, "slide": 10, "lateness": 0},
        '{"key":"@","ts":1,"val":1}\n',
        _vs("key_at"),
    )
    assert_refuse(s, "refuse: bad event", _vs("key_at_out"))


def test_visible_bad_event_key_period_refused():
    """Period is not in [A-Za-z0-9_] — common identifier regexes wrongly allow it."""
    s = stage_stream(
        {"size": 1, "slide": 1, "lateness": 0},
        '{"key":".","ts":0,"val":1}\n',
        _vs("key_dot"),
    )
    assert_refuse(s, "refuse: bad event", _vs("key_dot_out"))


def test_visible_bad_event_key_period_in_name_refused():
    s = stage_stream(
        {"size": 1, "slide": 1, "lateness": 0},
        '{"key":"a.b","ts":0,"val":1}\n',
        _vs("key_dot2"),
    )
    assert_refuse(s, "refuse: bad event", _vs("key_dot2_out"))


def test_visible_bad_event_float_ts_nonintegral():
    s = stage_stream(
        {"size": 10, "slide": 10, "lateness": 0},
        '{"key":"a","ts":1.5,"val":1}\n',
        WORK / "vs_07c8d569fb",
    )
    assert_refuse(s, "refuse: bad event", WORK / "vs_bac3f197c8")


def test_visible_wide_events_file_under_megabyte():
    """events.jsonl near half-megabyte stays inside the 1_048_576-byte domain."""
    prefix = b'{"key":"a","ts":1,"val":1'
    suffix = b"}\n"
    pad = 524_289 - len(prefix) - len(suffix)
    assert pad > 0
    events = prefix + (b" " * pad) + suffix
    assert len(events) == 524_289
    s = stage_stream({"size": 10, "slide": 10, "lateness": 0}, events, WORK / "vis_wide_file")
    expected = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 0,
              "end": 10,
              "totals": {
                "a": 1
              }
            }
          ]
        }
        """
    )
    assert_success_bytes(s, expected, WORK / "vis_wide_file.json")


def test_visible_many_distinct_windows():
    """1_025 distinct tumbling windows — not a single-bucket accumulation."""
    events = "".join(f'{{"key":"a","ts":{i},"val":1}}\n' for i in range(1025))
    s = stage_stream({"size": 1, "slide": 1, "lateness": 0}, events, WORK / "vis_many_win")
    windows = [
        {"start": i, "end": i + 1, "totals": {"a": 1}} for i in range(1025)
    ]
    expected = json.dumps({"windows": windows}, indent=2, ensure_ascii=False) + "\n"
    assert_success_bytes(s, expected, WORK / "vis_many_win.json")


def _vs(tag: str) -> Path:
    """Opaque stream basename — no refusal-category token in argv."""
    import hashlib

    return WORK / ("vs_" + hashlib.sha256(tag.encode()).hexdigest()[:10])


def test_visible_negative_slide_refused():
    s = stage_stream({"size": 1, "slide": -1, "lateness": 0}, "", _vs("neg_slide"))
    assert_refuse(s, "refuse: bad config", _vs("neg_slide_out"))


def test_visible_negative_size_refused():
    s = stage_stream({"size": -1, "slide": 1, "lateness": 0}, "", _vs("neg_size"))
    assert_refuse(s, "refuse: bad config", _vs("neg_size_out"))


def test_visible_bool_slide_refused():
    s = _vs("bool_slide")
    if s.exists():
        shutil.rmtree(s)
    s.mkdir(parents=True)
    (s / "config.json").write_text('{"size":1,"slide":true,"lateness":0}\n')
    (s / "events.jsonl").write_text("")
    assert_refuse(s, "refuse: bad config", _vs("bool_slide_out"))


def test_visible_bool_lateness_refused():
    s = _vs("bool_late")
    if s.exists():
        shutil.rmtree(s)
    s.mkdir(parents=True)
    (s / "config.json").write_text('{"size":1,"slide":1,"lateness":true}\n')
    (s / "events.jsonl").write_text("")
    assert_refuse(s, "refuse: bad config", _vs("bool_late_out"))


def test_visible_lateness_above_100_kept():
    """lateness=101 must not be silently capped at 100."""
    events = '{"key":"a","ts":101,"val":1}\n{"key":"a","ts":0,"val":1}\n'
    s = stage_stream({"size": 1, "slide": 1, "lateness": 101}, events, _vs("late101"))
    expected = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 0,
              "end": 1,
              "totals": {
                "a": 1
              }
            },
            {
              "start": 101,
              "end": 102,
              "totals": {
                "a": 1
              }
            }
          ]
        }
        """
    )
    assert_success_bytes(s, expected, _vs("late101_out"))


def test_visible_unterminated_whitespace_tail_refused():
    s = stage_stream(
        {"size": 10, "slide": 10, "lateness": 0},
        b'{"key":"a","ts":1,"val":1}\n ',
        _vs("ws_tail"),
    )
    assert_refuse(s, "refuse: truncated events", _vs("ws_tail_out"))


def test_visible_missing_ts_refused():
    s = stage_stream(
        {"size": 10, "slide": 10, "lateness": 0},
        '{"key":"a","val":1}\n',
        _vs("miss_ts"),
    )
    assert_refuse(s, "refuse: bad event", _vs("miss_ts_out"))


def test_visible_nonstring_key_refused():
    s = stage_stream(
        {"size": 10, "slide": 10, "lateness": 0},
        '{"key":7,"ts":1,"val":1}\n',
        _vs("key_int"),
    )
    assert_refuse(s, "refuse: bad event", _vs("key_int_out"))


def test_visible_val_lower_adjacent_accepted():
    s = stage_stream(
        {"size": 10, "slide": 10, "lateness": 0},
        '{"key":"a","ts":1,"val":-9007199254740991}\n',
        _vs("val_lo_ok"),
    )
    expected = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 0,
              "end": 10,
              "totals": {
                "a": -9007199254740991
              }
            }
          ]
        }
        """
    )
    assert_success_bytes(s, expected, _vs("val_lo_ok_out"))


def test_visible_bool_false_val_refused():
    s = stage_stream(
        {"size": 10, "slide": 10, "lateness": 0},
        '{"key":"a","ts":1,"val":false}\n',
        _vs("val_false"),
    )
    assert_refuse(s, "refuse: bad event", _vs("val_false_out"))


def test_visible_trailing_garbage_after_object_refused():
    s = stage_stream(
        {"size": 10, "slide": 10, "lateness": 0},
        b'{"key":"a","ts":1,"val":1} garbage\n',
        _vs("trail_junk"),
    )
    assert_refuse(s, "refuse: truncated events", _vs("trail_junk_out"))


def test_visible_unicode_line_separator_inside_lf_line_truncated():
    """U+2028 inside an LF-delimited physical line is not a line break."""
    events = b'{"key":"a","ts":1,"val":1}\xe2\x80\xa8\n'  # U+2028 UTF-8
    s = stage_stream({"size": 10, "slide": 10, "lateness": 0}, events, _vs("u2028"))
    assert_refuse(s, "refuse: truncated events", _vs("u2028_out"))


def test_visible_unicode_paragraph_separator_inside_lf_line_truncated():
    """Second site: U+2029 must stay inside the LF-delimited line."""
    events = b'{"key":"a","ts":1,"val":1}\xe2\x80\xa9\n'  # U+2029 UTF-8
    s = stage_stream({"size": 10, "slide": 10, "lateness": 0}, events, _vs("u2029"))
    assert_refuse(s, "refuse: truncated events", _vs("u2029_out"))


def test_visible_ten_thousand_windows():
    """Contract permits up to 10_000 emitted windows — grade the full capacity."""
    events = "".join(f'{{"key":"a","ts":{i},"val":1}}\n' for i in range(10_000))
    s = stage_stream({"size": 1, "slide": 1, "lateness": 0}, events, _vs("cap10k"))
    windows = [{"start": i, "end": i + 1, "totals": {"a": 1}} for i in range(10_000)]
    expected = json.dumps({"windows": windows}, indent=2, ensure_ascii=False) + "\n"
    assert_success_bytes(s, expected, _vs("cap10k_out"))


def test_visible_lateness_above_i32_kept():
    """lateness=2^31 must not be capped at 2^31-1."""
    events = '{"key":"a","ts":2147483648,"val":1}\n{"key":"a","ts":0,"val":1}\n'
    s = stage_stream(
        {"size": 1, "slide": 1, "lateness": 2147483648}, events, _vs("late_i32")
    )
    expected = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 0,
              "end": 1,
              "totals": {
                "a": 1
              }
            },
            {
              "start": 2147483648,
              "end": 2147483649,
              "totals": {
                "a": 1
              }
            }
          ]
        }
        """
    )
    assert_success_bytes(s, expected, _vs("late_i32_out"))


def test_visible_lateness_above_u32_descent_kept():
    """lateness=2^32 must not saturate watermark math at 2^32-1."""
    events = (
        '{"key":"a","ts":4294967296,"val":1}\n'
        '{"key":"a","ts":0,"val":1}\n'
    )
    s = stage_stream(
        {"size": 1, "slide": 1, "lateness": 4294967296}, events, _vs("late_u32")
    )
    expected = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 0,
              "end": 1,
              "totals": {
                "a": 1
              }
            },
            {
              "start": 4294967296,
              "end": 4294967297,
              "totals": {
                "a": 1
              }
            }
          ]
        }
        """
    )
    assert_success_bytes(s, expected, _vs("late_u32_out"))


def test_visible_lateness_above_u32_descent_kept_second_site():
    events = (
        '{"key":"b","ts":8589934592,"val":1}\n'
        '{"key":"b","ts":1,"val":1}\n'
    )
    s = stage_stream(
        {"size": 1, "slide": 1, "lateness": 8589934592}, events, _vs("late_u32b")
    )
    expected = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 1,
              "end": 2,
              "totals": {
                "b": 1
              }
            },
            {
              "start": 8589934592,
              "end": 8589934593,
              "totals": {
                "b": 1
              }
            }
          ]
        }
        """
    )
    assert_success_bytes(s, expected, _vs("late_u32b_out"))


def test_visible_lateness_max_bound_accepted():
    """lateness = 2^53 - 1 is in-domain (half-open upper bound)."""
    s = stage_stream(
        {"size": 1, "slide": 1, "lateness": 9007199254740991},
        '{"key":"a","ts":0,"val":1}\n',
        _vs("late_max"),
    )
    expected = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 0,
              "end": 1,
              "totals": {
                "a": 1
              }
            }
          ]
        }
        """
    )
    assert_success_bytes(s, expected, _vs("late_max_out"))


def test_visible_lateness_max_bound_accepted_second_site():
    s = stage_stream(
        {"size": 2, "slide": 2, "lateness": 9007199254740991},
        '{"key":"z","ts":1,"val":3}\n',
        _vs("late_max_b"),
    )
    expected = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 0,
              "end": 2,
              "totals": {
                "z": 3
              }
            }
          ]
        }
        """
    )
    assert_success_bytes(s, expected, _vs("late_max_b_out"))


def test_visible_large_ts_and_lateness_exact_watermark():
    """Near-limit ts and lateness must use exact int watermark subtraction."""
    events = (
        '{"key":"a","ts":9007199254740991,"val":1}\n'
        '{"key":"a","ts":1,"val":1}\n'
    )
    s = stage_stream(
        {"size": 1, "slide": 1, "lateness": 9007199254740990},
        events,
        _vs("wm_f64"),
    )
    expected = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 1,
              "end": 2,
              "totals": {
                "a": 1
              }
            },
            {
              "start": 9007199254740991,
              "end": 9007199254740992,
              "totals": {
                "a": 1
              }
            }
          ]
        }
        """
    )
    assert_success_bytes(s, expected, _vs("wm_f64_out") / "out.json")


def test_visible_large_ts_and_lateness_exact_watermark_second_site():
    events = (
        '{"key":"b","ts":9007199254740989,"val":1}\n'
        '{"key":"b","ts":3,"val":1}\n'
    )
    s = stage_stream(
        {"size": 1, "slide": 1, "lateness": 9007199254740986},
        events,
        _vs("wm_f64b"),
    )
    expected = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 3,
              "end": 4,
              "totals": {
                "b": 1
              }
            },
            {
              "start": 9007199254740989,
              "end": 9007199254740990,
              "totals": {
                "b": 1
              }
            }
          ]
        }
        """
    )
    assert_success_bytes(s, expected, _vs("wm_f64b_out") / "out.json")


def test_visible_string_size_refused():
    s = _vs("str_size")
    if s.exists():
        shutil.rmtree(s)
    s.mkdir(parents=True)
    (s / "config.json").write_text('{"size":"1","slide":1,"lateness":0}\n')
    (s / "events.jsonl").write_text("")
    assert_refuse(s, "refuse: bad config", _vs("str_size_out"))


def test_visible_null_size_refused():
    s = _vs("null_size")
    if s.exists():
        shutil.rmtree(s)
    s.mkdir(parents=True)
    (s / "config.json").write_text('{"size":null,"slide":1,"lateness":0}\n')
    (s / "events.jsonl").write_text("")
    assert_refuse(s, "refuse: bad config", _vs("null_size_out"))


def test_visible_null_slide_refused():
    s = _vs("null_slide")
    if s.exists():
        shutil.rmtree(s)
    s.mkdir(parents=True)
    (s / "config.json").write_text('{"size":1,"slide":null,"lateness":0}\n')
    (s / "events.jsonl").write_text("")
    assert_refuse(s, "refuse: bad config", _vs("null_slide_out"))


def test_visible_string_slide_refused():
    s = _vs("str_slide")
    if s.exists():
        shutil.rmtree(s)
    s.mkdir(parents=True)
    (s / "config.json").write_text('{"size":1,"slide":"1","lateness":0}\n')
    (s / "events.jsonl").write_text("")
    assert_refuse(s, "refuse: bad config", _vs("str_slide_out"))


def test_visible_string_lateness_refused():
    s = _vs("str_late")
    if s.exists():
        shutil.rmtree(s)
    s.mkdir(parents=True)
    (s / "config.json").write_text('{"size":1,"slide":1,"lateness":"0"}\n')
    (s / "events.jsonl").write_text("")
    assert_refuse(s, "refuse: bad config", _vs("str_late_out"))


def test_visible_null_lateness_refused():
    s = _vs("null_late")
    if s.exists():
        shutil.rmtree(s)
    s.mkdir(parents=True)
    (s / "config.json").write_text('{"size":1,"slide":1,"lateness":null}\n')
    (s / "events.jsonl").write_text("")
    assert_refuse(s, "refuse: bad config", _vs("null_late_out"))


def test_visible_ten_window_overlap():
    """size/slide = 10 — full permitted per-event window fan-out."""
    s = stage_stream(
        {"size": 10, "slide": 1, "lateness": 0},
        '{"key":"a","ts":9,"val":1}\n',
        _vs("ov10"),
    )
    windows = [
        {"start": i, "end": i + 10, "totals": {"a": 1}} for i in range(10)
    ]
    expected = json.dumps({"windows": windows}, indent=2, ensure_ascii=False) + "\n"
    assert_success_bytes(s, expected, _vs("ov10_out"))


def test_visible_five_window_overlap():
    """Five assignments — catches a fixed four-start loop."""
    s = stage_stream(
        {"size": 5, "slide": 1, "lateness": 0},
        '{"key":"a","ts":4,"val":1}\n',
        _vs("ov5"),
    )
    windows = [{"start": i, "end": i + 5, "totals": {"a": 1}} for i in range(5)]
    expected = json.dumps({"windows": windows}, indent=2, ensure_ascii=False) + "\n"
    assert_success_bytes(s, expected, _vs("ov5_out"))


def test_visible_256_distinct_keys():
    """Full 256-key graded domain in one window."""
    events = "".join(f'{{"key":"k{i:03d}","ts":1,"val":1}}\n' for i in range(256))
    s = stage_stream({"size": 10, "slide": 10, "lateness": 0}, events, _vs("keys256"))
    totals = {f"k{i:03d}": 1 for i in range(256)}
    expected = (
        json.dumps(
            {"windows": [{"start": 0, "end": 10, "totals": totals}]},
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )
    assert_success_bytes(s, expected, _vs("keys256_out"))


def test_visible_megabyte_events_file():
    """events.jsonl near 1_000_001 bytes stays inside the 1_048_576-byte domain."""
    prefix = b'{"key":"a","ts":1,"val":1'
    suffix = b"}\n"
    pad = 1_000_001 - len(prefix) - len(suffix)
    events = prefix + (b" " * pad) + suffix
    assert len(events) == 1_000_001
    s = stage_stream({"size": 10, "slide": 10, "lateness": 0}, events, _vs("near_mb"))
    expected = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 0,
              "end": 10,
              "totals": {
                "a": 1
              }
            }
          ]
        }
        """
    )
    assert_success_bytes(s, expected, _vs("near_mb_out"))


def test_visible_null_config_refused():
    s = _vs("cfg_null")
    if s.exists():
        shutil.rmtree(s)
    s.mkdir(parents=True)
    (s / "config.json").write_text("null\n")
    (s / "events.jsonl").write_text("")
    assert_refuse(s, "refuse: bad config", _vs("cfg_null_out"))


def test_visible_null_event_line_refused():
    s = stage_stream(
        {"size": 1, "slide": 1, "lateness": 0},
        b"null\n",
        _vs("ev_null"),
    )
    assert_refuse(s, "refuse: truncated events", _vs("ev_null_out"))


def test_visible_integral_float_config_refused():
    s = _vs("cfg_fint")
    if s.exists():
        shutil.rmtree(s)
    s.mkdir(parents=True)
    (s / "config.json").write_text('{"size":1.0,"slide":1.0,"lateness":0.0}\n')
    (s / "events.jsonl").write_text("")
    assert_refuse(s, "refuse: bad config", _vs("cfg_fint_out"))


def test_visible_deep_nested_array_line_truncated():
    """Deep nesting that raises RecursionError must still be truncated events."""
    line = ("[" * 1100) + "0" + ("]" * 1100) + "\n"
    s = stage_stream(
        {"size": 10, "slide": 5, "lateness": 2},
        line.encode("ascii"),
        _vs("deep_nest"),
    )
    assert_refuse(s, "refuse: truncated events", _vs("deep_nest_out"))


def test_visible_string_val_refused():
    s = stage_stream(
        {"size": 1, "slide": 1, "lateness": 0},
        '{"key":"a","ts":0,"val":"1"}\n',
        _vs("str_val"),
    )
    assert_refuse(s, "refuse: bad event", _vs("str_val_out"))


def test_visible_string_ts_refused():
    s = stage_stream(
        {"size": 1, "slide": 1, "lateness": 0},
        '{"key":"a","ts":"1","val":1}\n',
        _vs("str_ts"),
    )
    assert_refuse(s, "refuse: bad event", _vs("str_ts_out"))


def test_visible_null_ts_refused():
    s = stage_stream(
        {"size": 1, "slide": 1, "lateness": 0},
        '{"key":"a","ts":null,"val":1}\n',
        _vs("null_ts"),
    )
    assert_refuse(s, "refuse: bad event", _vs("null_ts_out"))


def test_visible_null_val_refused():
    s = stage_stream(
        {"size": 1, "slide": 1, "lateness": 0},
        '{"key":"a","ts":1,"val":null}\n',
        _vs("null_val"),
    )
    assert_refuse(s, "refuse: bad event", _vs("null_val_out"))


def test_visible_key_bracket_refused():
    """Catches [A-z0-9_] regex that wrongly allows '['."""
    s = stage_stream(
        {"size": 10, "slide": 10, "lateness": 0},
        '{"key":"[","ts":1,"val":1}\n',
        _vs("key_brack"),
    )
    assert_refuse(s, "refuse: bad event", _vs("key_brack_out"))


def test_visible_per_window_key_sets_differ():
    """Each window's totals contains only keys that contributed to that window."""
    events = '{"key":"a","ts":0,"val":1}\n{"key":"b","ts":1,"val":1}\n'
    s = stage_stream({"size": 1, "slide": 1, "lateness": 0}, events, _vs("keys_split"))
    expected = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 0,
              "end": 1,
              "totals": {
                "a": 1
              }
            },
            {
              "start": 1,
              "end": 2,
              "totals": {
                "b": 1
              }
            }
          ]
        }
        """
    )
    assert_success_bytes(s, expected, _vs("keys_split_out"))


def test_visible_dup_config_size_refused():
    s = _vs("dup_cfg")
    if s.exists():
        shutil.rmtree(s)
    s.mkdir(parents=True)
    (s / "config.json").write_text('{"size":0,"size":1,"slide":1,"lateness":0}\n')
    (s / "events.jsonl").write_text("")
    assert_refuse(s, "refuse: bad config", _vs("dup_cfg_out"))


def test_visible_dup_config_slide_refused():
    s = _vs("dup_cfg_sl")
    if s.exists():
        shutil.rmtree(s)
    s.mkdir(parents=True)
    (s / "config.json").write_text('{"size":1,"slide":0,"slide":1,"lateness":0}\n')
    (s / "events.jsonl").write_text("")
    assert_refuse(s, "refuse: bad config", _vs("dup_cfg_sl_out"))


def test_visible_dup_config_lateness_refused():
    s = _vs("dup_cfg_late")
    if s.exists():
        shutil.rmtree(s)
    s.mkdir(parents=True)
    (s / "config.json").write_text('{"size":1,"slide":1,"lateness":0,"lateness":1}\n')
    (s / "events.jsonl").write_text("")
    assert_refuse(s, "refuse: bad config", _vs("dup_cfg_late_out"))


def test_visible_dup_config_identical_size_refused():
    """Duplicate member names are refused even when values are identical."""
    s = _vs("dup_cfg_same")
    if s.exists():
        shutil.rmtree(s)
    s.mkdir(parents=True)
    (s / "config.json").write_text('{"size":1,"size":1,"slide":1,"lateness":0}\n')
    (s / "events.jsonl").write_text("")
    assert_refuse(s, "refuse: bad config", _vs("dup_cfg_same_out"))


def test_visible_dup_config_identical_slide_refused():
    s = _vs("dup_cfg_same_sl")
    if s.exists():
        shutil.rmtree(s)
    s.mkdir(parents=True)
    (s / "config.json").write_text('{"size":1,"slide":1,"slide":1,"lateness":0}\n')
    (s / "events.jsonl").write_text("")
    assert_refuse(s, "refuse: bad config", _vs("dup_cfg_same_sl_out"))


def test_visible_dup_event_val_refused():
    s = stage_stream(
        {"size": 1, "slide": 1, "lateness": 0},
        '{"key":"a","ts":0,"val":true,"val":7}\n',
        _vs("dup_val"),
    )
    assert_refuse(s, "refuse: bad event", _vs("dup_val_out"))


def test_visible_dup_event_identical_val_refused():
    """Duplicate member names refused even when values are identical."""
    s = stage_stream(
        {"size": 1, "slide": 1, "lateness": 0},
        '{"key":"a","ts":0,"val":1,"val":1}\n',
        _vs("dup_val_same"),
    )
    assert_refuse(s, "refuse: bad event", _vs("dup_val_same_out"))


def test_visible_dup_event_identical_key_refused():
    s = stage_stream(
        {"size": 1, "slide": 1, "lateness": 0},
        '{"key":"a","key":"a","ts":0,"val":1}\n',
        _vs("dup_key_same"),
    )
    assert_refuse(s, "refuse: bad event", _vs("dup_key_same_out"))


def test_visible_dup_event_key_refused():
    s = stage_stream(
        {"size": 1, "slide": 1, "lateness": 0},
        '{"key":"a","key":"b","ts":0,"val":1}\n',
        _vs("dup_key"),
    )
    assert_refuse(s, "refuse: bad event", _vs("dup_key_out"))


def test_visible_dup_event_ts_refused():
    s = stage_stream(
        {"size": 1, "slide": 1, "lateness": 0},
        '{"key":"a","ts":0,"ts":1,"val":1}\n',
        _vs("dup_ts"),
    )
    assert_refuse(s, "refuse: bad event", _vs("dup_ts_out"))


def test_visible_dup_event_ts_refused_second_site():
    s = stage_stream(
        {"size": 1, "slide": 1, "lateness": 0},
        '{"ts":9,"key":"a","ts":0,"val":1}\n',
        _vs("dup_ts2"),
    )
    assert_refuse(s, "refuse: bad event", _vs("dup_ts2_out"))


def test_visible_dup_event_unicode_escaped_val_refused():
    """Decoded member names matter — literal val and \\u0076al are the same name."""
    s = stage_stream(
        {"size": 1, "slide": 1, "lateness": 0},
        '{"key":"a","ts":0,"val":1,"\\u0076al":2}\n',
        _vs("dup_val_esc"),
    )
    assert_refuse(s, "refuse: bad event", _vs("dup_val_esc_out"))


def test_visible_dup_event_unicode_escaped_key_refused():
    s = stage_stream(
        {"size": 1, "slide": 1, "lateness": 0},
        '{"key":"a","\\u006bey":"b","ts":0,"val":1}\n',
        _vs("dup_key_esc"),
    )
    assert_refuse(s, "refuse: bad event", _vs("dup_key_esc_out"))


def test_visible_cancel_to_zero_keeps_key():
    """Successive vals that sum to zero still keep the key in totals."""
    events = '{"key":"a","ts":1,"val":1}\n{"key":"a","ts":2,"val":-1}\n'
    s = stage_stream({"size": 10, "slide": 10, "lateness": 0}, events, _vs("zero_sum"))
    expected = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 0,
              "end": 10,
              "totals": {
                "a": 0
              }
            }
          ]
        }
        """
    )
    assert_success_bytes(s, expected, _vs("zero_sum_out"))


def test_visible_huge_ts_digits_bad_event():
    """Decoder integer-conversion failure on a complete object → bad event."""
    ts = "1" * 4301
    s = stage_stream(
        {"size": 10, "slide": 10, "lateness": 0},
        f'{{"key":"a","ts":{ts},"val":0}}\n',
        _vs("huge_ts"),
    )
    assert_refuse(s, "refuse: bad event", _vs("huge_ts_out"))


def test_visible_huge_val_digits_bad_event():
    """Decoder integer-conversion failure on val → bad event (not truncated)."""
    val = "1" * 4301
    s = stage_stream(
        {"size": 10, "slide": 10, "lateness": 0},
        f'{{"key":"a","ts":0,"val":{val}}}\n',
        _vs("huge_val"),
    )
    assert_refuse(s, "refuse: bad event", _vs("huge_val_out"))


def test_visible_huge_val_digits_bad_event_second_site():
    val = "9" * 4301
    s = stage_stream(
        {"size": 1, "slide": 1, "lateness": 0},
        f'{{"key":"z","ts":1,"val":-{val}}}\n',
        _vs("huge_val2"),
    )
    assert_refuse(s, "refuse: bad event", _vs("huge_val2_out"))


def test_visible_huge_config_size_digits_bad_config():
    """Decoder integer-conversion failure on config size → bad config, not exit 1."""
    huge = "1" * 4301
    s = _vs("huge_cfg")
    if s.exists():
        shutil.rmtree(s)
    s.mkdir(parents=True)
    (s / "config.json").write_text(f'{{"size":{huge},"slide":1,"lateness":0}}\n')
    (s / "events.jsonl").write_text("")
    assert_refuse(s, "refuse: bad config", _vs("huge_cfg_out"))


def test_visible_huge_config_slide_digits_bad_config():
    huge = "1" * 4301
    s = _vs("huge_cfg_sl")
    if s.exists():
        shutil.rmtree(s)
    s.mkdir(parents=True)
    (s / "config.json").write_text(f'{{"size":1,"slide":{huge},"lateness":0}}\n')
    (s / "events.jsonl").write_text("")
    assert_refuse(s, "refuse: bad config", _vs("huge_cfg_sl_out"))


def test_visible_bad_event_after_valid_leaves_no_out():
    """Semantic bad-event after a accepted line must still leave --out absent."""
    events = '{"key":"a","ts":1,"val":1}\n{"key":"a","ts":2,"val":true}\n'
    s = stage_stream({"size": 10, "slide": 10, "lateness": 0}, events, _vs("be_after"))
    assert_refuse(s, "refuse: bad event", _vs("be_after_out"))


def test_visible_bad_event_after_valid_leaves_no_out_second_site():
    events = '{"key":"a","ts":1,"val":1}\n{"key":"","ts":2,"val":1}\n'
    s = stage_stream({"size": 10, "slide": 10, "lateness": 0}, events, _vs("be_after2"))
    assert_refuse(s, "refuse: bad event", _vs("be_after2_out"))


def test_visible_late_bad_event_still_refused():
    """Late discard must not skip semantic field validation (empty key)."""
    events = (
        '{"key":"a","ts":10,"val":1}\n'
        '{"key":"","ts":7,"val":1}\n'
    )
    s = stage_stream(
        {"size": 10, "slide": 10, "lateness": 2}, events, _vs("late_bad")
    )
    assert_refuse(s, "refuse: bad event", _vs("late_bad_out"))


def test_visible_late_bad_event_still_refused_second_site():
    """Late discard must not skip semantic field validation (bool val)."""
    events = (
        '{"key":"a","ts":10,"val":1}\n'
        '{"key":"a","ts":7,"val":true}\n'
    )
    s = stage_stream(
        {"size": 10, "slide": 10, "lateness": 2}, events, _vs("late_bad2")
    )
    assert_refuse(s, "refuse: bad event", _vs("late_bad2_out"))


def test_visible_events_file_exact_byte_limit():
    """Exactly 1_048_576 bytes is in-domain (inclusive upper bound)."""
    prefix = b'{"key":"a","ts":1,"val":1'
    suffix = b"}\n"
    pad = 1_048_576 - len(prefix) - len(suffix)
    events = prefix + (b" " * pad) + suffix
    assert len(events) == 1_048_576
    s = stage_stream({"size": 10, "slide": 10, "lateness": 0}, events, _vs("exact_mb"))
    expected = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 0,
              "end": 10,
              "totals": {
                "a": 1
              }
            }
          ]
        }
        """
    )
    assert_success_bytes(s, expected, _vs("exact_mb_out"))


def test_visible_events_file_exact_byte_limit_second_site():
    """Second site: exact 1_048_576-byte events with a different key/window."""
    prefix = b'{"key":"z","ts":5,"val":2'
    suffix = b"}\n"
    pad = 1_048_576 - len(prefix) - len(suffix)
    events = prefix + (b" " * pad) + suffix
    assert len(events) == 1_048_576
    s = stage_stream({"size": 5, "slide": 5, "lateness": 0}, events, _vs("exact_mb_b"))
    expected = textwrap.dedent(
        """\
        {
          "windows": [
            {
              "start": 5,
              "end": 10,
              "totals": {
                "z": 2
              }
            }
          ]
        }
        """
    )
    assert_success_bytes(s, expected, _vs("exact_mb_b_out"))


def test_visible_config_trailing_garbage_refused():
    """config.json must be one complete JSON object — no trailing content."""
    s = _vs("cfg_trail")
    if s.exists():
        shutil.rmtree(s)
    s.mkdir(parents=True)
    (s / "config.json").write_text('{"size":1,"slide":1,"lateness":0} trailing\n')
    (s / "events.jsonl").write_text("")
    assert_refuse(s, "refuse: bad config", _vs("cfg_trail_out"))


def test_visible_config_trailing_second_object_refused():
    """Second site: valid config object followed by a second JSON value."""
    s = _vs("cfg_trail2")
    if s.exists():
        shutil.rmtree(s)
    s.mkdir(parents=True)
    (s / "config.json").write_text('{"size":1,"slide":1,"lateness":0}\n0\n')
    (s / "events.jsonl").write_text("")
    assert_refuse(s, "refuse: bad config", _vs("cfg_trail2_out"))


# --- Sealed ---


@pytest.mark.parametrize("name", SEALED_CASES)
def test_sealed_stream_matches_expect(name: str):
    """Held-out stream reproduces the sealed expect bytes (expects never on disk)."""
    snap = SEALED_SNAP[name]
    dest = materialize_sealed(name, WORK / f"sealed_{name}")
    assert_success_bytes(dest, snap["expect"].decode("utf-8"), WORK / f"sealed_out_{name}" / "out.json")



def test_output_deterministic_on_late_case():
    """Two independent runs must each match the sealed expect (not merely each other)."""
    snap = SEALED_SNAP["arrival_late_drop"]
    expected = snap["expect"].decode("utf-8")
    stream_a = materialize_sealed("arrival_late_drop", _vs("det_a"))
    assert_success_bytes(stream_a, expected, _vs("det_oa") / "out.json")
    _scrub_sandbox_scratch()
    _drain_sandbox()
    stream_b = materialize_sealed("arrival_late_drop", _vs("det_b"))
    assert_success_bytes(stream_b, expected, _vs("det_ob") / "out.json")



def test_no_expect_bytes_under_app():
    assert not list(APP.rglob("expect.json"))
    assert not (APP / "tests").exists()


def test_sandbox_cannot_read_reward_or_sealed_expect():
    """Privilege probe: sandbox cannot read reward.txt; sealed expects are gone from disk."""
    reward = Path("/logs/verifier/reward.txt")
    reward.parent.mkdir(parents=True, exist_ok=True)
    if not reward.exists():
        reward.write_text("0\n")
        os.chmod(reward, 0o600)
        try:
            os.chown(reward, 0, 0)
        except PermissionError:
            pass
    # Expects were snapshotted and removed at import time.
    assert not SEALED.exists()
    probe_code = (
        "from pathlib import Path\n"
        "errs=[]\n"
        "for p in [Path('/logs/verifier/reward.txt'), Path('/tests/sealed')]:\n"
        "    try:\n"
        "        if p.is_dir():\n"
        "            list(p.iterdir())\n"
        "            errs.append(f'listable:{p}')\n"
        "        else:\n"
        "            p.read_bytes()\n"
        "            errs.append(f'readable:{p}')\n"
        "    except PermissionError:\n"
        "        pass\n"
        "    except FileNotFoundError:\n"
        "        pass\n"
        "raise SystemExit(0 if not errs else 1)\n"
    )
    probe = [
        "/usr/bin/setpriv",
        f"--reuid={SANDBOX_UID}",
        f"--regid={SANDBOX_GID}",
        "--clear-groups",
        "--no-new-privs",
        "--",
        "python3",
        "-c",
        probe_code,
    ]
    proc = subprocess.run(probe, capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0, proc.stderr + proc.stdout
