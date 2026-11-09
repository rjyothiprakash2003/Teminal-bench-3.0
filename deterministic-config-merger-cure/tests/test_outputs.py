from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

APP = Path("/app")
TESTS = Path("/tests")
SEALED = TESTS / "sealed"
SANDBOX_ROOT = Path("/sandbox")
WORK = SANDBOX_ROOT / "work"
PROGRAM = APP / "merge.py"

SANDBOX_UID = 2000
SANDBOX_GID = 2000
RUN_TIMEOUT = 4


def opaque(label: str) -> Path:
    return WORK / ("wx_" + hashlib.sha256(f"dcm:{label}".encode()).hexdigest()[:20])


def _snapshot_tree(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _materialize(root: Path, files: dict[str, bytes]) -> None:
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


SEALED_CASES: dict[str, dict[str, bytes]] = {}
SEALED_EXPECTS: dict[str, str] = {}
if SEALED.is_dir():
    for case in sorted(p for p in SEALED.iterdir() if p.is_dir()):
        expect = case / "expect.json"
        if not expect.is_file():
            continue
        SEALED_EXPECTS[case.name] = expect.read_text()
        files = _snapshot_tree(case)
        files.pop("expect.json", None)
        SEALED_CASES[case.name] = files

shutil.rmtree(SEALED, ignore_errors=True)
os.chmod(TESTS, 0o700)

assert (APP / "merge.py").is_file() and not (APP / "merge.py").is_symlink()
WORK.mkdir(parents=True, exist_ok=True)
os.chmod(APP, 0o711)
os.chmod(APP / "merge.py", 0o755)
os.chown(SANDBOX_ROOT, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
os.chown(WORK, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)

WITNESSES: dict[str, list[tuple[str, str, object]]] = {}


def witness(family: str, why: str):
    def register(fn):
        WITNESSES.setdefault(family, []).append((fn.__name__, why, fn))
        return fn

    return register


def run_family(family: str) -> None:
    failed = []
    for name, why, fn in WITNESSES[family]:
        try:
            fn()
        except Exception as exc:
            failed.append(f"{name} ({why}): {type(exc).__name__}: {str(exc)[:300]}")
    assert not failed, "\n".join(failed)


def _chown_tree(root: Path, uid: int, gid: int) -> None:
    for path in [root, *root.rglob("*")]:
        os.chown(path, uid, gid, follow_symlinks=False)


def write_root(dest: Path, manifest: dict, sources: dict[str, dict]) -> Path:
    if dest.exists():
        shutil.rmtree(dest)
    (dest / "sources").mkdir(parents=True)
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    for sid, body in sources.items():
        (dest / "sources" / f"{sid}.json").write_text(
            json.dumps(body, indent=2) + "\n"
        )
    _chown_tree(dest, SANDBOX_UID, SANDBOX_GID)
    return dest


def write_bytes_root(
    dest: Path, manifest_bytes: bytes, source_files: dict[str, bytes]
) -> Path:
    if dest.exists():
        shutil.rmtree(dest)
    (dest / "sources").mkdir(parents=True)
    (dest / "manifest.json").write_bytes(manifest_bytes)
    for name, body in source_files.items():
        (dest / "sources" / name).write_bytes(body)
    _chown_tree(dest, SANDBOX_UID, SANDBOX_GID)
    return dest


def stage_sealed_root(name: str) -> tuple[Path, str]:
    expected = SEALED_EXPECTS[name]
    token = hashlib.sha256(f"sealed:{name}".encode()).hexdigest()[:16]
    dest = opaque(f"wx_{token}")
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    _materialize(dest, SEALED_CASES[name])
    (dest / "sources").mkdir(parents=True, exist_ok=True)
    _chown_tree(dest, SANDBOX_UID, SANDBOX_GID)
    return dest, expected


def run_merge(
    root: Path,
    out: Path,
    *,
    program: Path | None = None,
    clear_out: bool = True,
    out_first: bool = False,
) -> subprocess.CompletedProcess[bytes]:
    os.chown(WORK, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    if clear_out and out.exists():
        out.unlink()
    env = {
        "HOME": str(SANDBOX_ROOT),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "PYTHONHASHSEED": "0",
        "PYTHONSAFEPATH": "0",
        "TZ": "UTC",
    }
    prog = program if program is not None else PROGRAM
    return subprocess.run(
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
            "-I",
            "-S",
            str(prog),
            *(["--out", str(out), "--root", str(root)] if out_first else ["--root", str(root), "--out", str(out)]),
        ],
        capture_output=True,
        timeout=RUN_TIMEOUT,
        cwd=str(SANDBOX_ROOT),
        env=env,
    )


def assert_success_bytes(root: Path, expected: str, out: Path, *, out_first: bool = False) -> None:
    proc = run_merge(root, out, out_first=out_first)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == b""
    assert out.read_bytes() == expected.encode("utf-8")


def assert_refuse(
    root: Path,
    msg: str,
    out: Path,
    *,
    preexisting: bytes | None = None,
    out_first: bool = False,
) -> None:
    if preexisting is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(preexisting)
        os.chown(out, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
        proc = run_merge(root, out, clear_out=False, out_first=out_first)
        assert proc.returncode == 2, (proc.returncode, proc.stderr)
        assert proc.stdout == b""
        assert proc.stderr == msg.encode("ascii") + b"\n", proc.stderr
        assert out.read_bytes() == preexisting
        return
    proc = run_merge(root, out, out_first=out_first)
    assert proc.returncode == 2, (proc.returncode, proc.stderr)
    assert proc.stdout == b""
    assert proc.stderr == msg.encode("ascii") + b"\n", proc.stderr
    assert not out.exists()


def refuse_pair(build, msg: str, tag: str) -> None:
    for suffix in ("p", "q"):
        name = f"wx_{tag}_{suffix}"
        root = build(opaque(name))
        assert_refuse(root, msg, opaque(f"{name}.out"), out_first=suffix == "q")


def dumps(value) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def one_source(dest: Path, body: dict) -> Path:
    return write_root(dest, {"sources": [{"id": "only", "priority": 1}]}, {"only": body})


@witness("layout", "manifest.json must be a regular file, not a symlink")
def w_refuse_manifest_symlink():
    def build(dest: Path) -> Path:
        if dest.exists():
            shutil.rmtree(dest)
        (dest / "sources").mkdir(parents=True)
        real = dest / "actual.json"
        real.write_text('{"sources":[]}\n')
        (dest / "manifest.json").symlink_to(real)
        _chown_tree(dest, SANDBOX_UID, SANDBOX_GID)
        return dest

    refuse_pair(build, "refuse: bad manifest", "18")


@witness("layout", "sources/ must be a real directory, not a symlink")
def w_refuse_sources_dir_symlink():
    def build(dest: Path) -> Path:
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        real = dest / "real_sources"
        real.mkdir()
        (dest / "manifest.json").write_text('{"sources":[]}\n')
        (dest / "sources").symlink_to(real)
        _chown_tree(dest, SANDBOX_UID, SANDBOX_GID)
        return dest

    refuse_pair(build, "refuse: bad manifest", "19")


@witness("layout", "Absent manifest.json is a bad manifest")
def w_refuse_missing_manifest():
    def build(dest: Path) -> Path:
        if dest.exists():
            shutil.rmtree(dest)
        (dest / "sources").mkdir(parents=True)
        _chown_tree(dest, SANDBOX_UID, SANDBOX_GID)
        return dest

    refuse_pair(build, "refuse: bad manifest", "20")


@witness("layout", "Absent sources/ directory is a bad manifest")
def w_refuse_missing_sources_directory():
    def build(dest: Path) -> Path:
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        (dest / "manifest.json").write_text('{"sources":[]}\n')
        _chown_tree(dest, SANDBOX_UID, SANDBOX_GID)
        return dest

    refuse_pair(build, "refuse: bad manifest", "21")


@witness("layout", "manifest.json must be a regular file, not a directory")
def w_refuse_manifest_is_directory():
    def build(dest: Path) -> Path:
        if dest.exists():
            shutil.rmtree(dest)
        (dest / "sources").mkdir(parents=True)
        (dest / "manifest.json").mkdir()
        _chown_tree(dest, SANDBOX_UID, SANDBOX_GID)
        return dest

    refuse_pair(build, "refuse: bad manifest", "70")


@witness("layout", "sources must be a directory, not a regular file")
def w_refuse_sources_is_regular_file():
    def build(dest: Path) -> Path:
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        (dest / "manifest.json").write_text('{"sources":[]}\n')
        (dest / "sources").write_text("notadir\n")
        _chown_tree(dest, SANDBOX_UID, SANDBOX_GID)
        return dest

    refuse_pair(build, "refuse: bad manifest", "71")


@witness("layout", "Nonexistent --root is a bad manifest")
def w_refuse_root_missing():
    def build(dest: Path) -> Path:
        if dest.exists():
            shutil.rmtree(dest)
        missing = dest / "r"
        return missing

    refuse_pair(build, "refuse: bad manifest", "72")


@witness("layout", "--root that is a regular file is a bad manifest")
def w_refuse_root_is_file():
    def build(dest: Path) -> Path:
        if dest.exists():
            if dest.is_dir():
                shutil.rmtree(dest)
            else:
                dest.unlink()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("not a directory\n")
        os.chown(dest, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
        return dest

    refuse_pair(build, "refuse: bad manifest", "73")


@witness("layout", "manifest.json must be a regular file, not a FIFO")
def w_refuse_manifest_is_fifo():
    def build(dest: Path) -> Path:
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        (dest / "sources").mkdir()
        fifo = dest / "manifest.json"
        if fifo.exists():
            fifo.unlink()
        os.mkfifo(fifo)
        os.chown(fifo, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
        os.chown(dest / "sources", SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
        os.chown(dest, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
        return dest

    refuse_pair(build, "refuse: bad manifest", "103")


@witness("layout", "sources/ must be a real directory, not a FIFO")
def w_refuse_sources_is_fifo():
    def build(dest: Path) -> Path:
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        (dest / "manifest.json").write_text('{"sources":[]}\n')
        fifo = dest / "sources"
        if fifo.exists():
            if fifo.is_dir():
                shutil.rmtree(fifo)
            else:
                fifo.unlink()
        os.mkfifo(fifo)
        os.chown(fifo, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
        os.chown(dest / "manifest.json", SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
        os.chown(dest, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
        return dest

    refuse_pair(build, "refuse: bad manifest", "104")


@witness("manifest_doc", "Manifest object must have exactly the sources key")
def w_refuse_extra_top_level_manifest_key():
    def build(dest: Path) -> Path:
        return write_bytes_root(
            dest,
            b'{"sources":[],"debug":true}\n',
            {},
        )

    refuse_pair(build, "refuse: bad manifest", "09")


@witness("manifest_doc", "Top-level manifest array is a bad manifest")
def w_refuse_non_object_manifest():
    def build(dest: Path) -> Path:
        return write_bytes_root(dest, b"[]\n", {})

    refuse_pair(build, "refuse: bad manifest", "10")


@witness("manifest_doc", "sources member must be a JSON array")
def w_refuse_sources_not_array():
    def build(dest: Path) -> Path:
        return write_bytes_root(dest, b'{"sources":{}}\n', {})

    refuse_pair(build, "refuse: bad manifest", "11")


@witness("manifest_doc", "Invalid UTF-8 in the manifest is a bad manifest")
def w_refuse_manifest_invalid_utf8():
    def build(dest: Path) -> Path:
        return write_bytes_root(dest, b'{"sources":[]}\xff', {})

    refuse_pair(build, "refuse: bad manifest", "12")


@witness("manifest_doc", "Second site: non-UTF-8 tail on an otherwise empty sources list")
def w_refuse_manifest_trailing_garbage_bytes():
    def build(dest: Path) -> Path:
        return write_bytes_root(dest, b'{"sources":[]}\x80', {})

    refuse_pair(build, "refuse: bad manifest", "13")


@witness("manifest_doc", "UTF-8 but truncated JSON syntax is a bad manifest")
def w_refuse_manifest_malformed_json_syntax():
    def build(dest: Path) -> Path:
        return write_bytes_root(dest, b'{"sources":[', {})

    refuse_pair(build, "refuse: bad manifest", "40")


@witness("manifest_doc", "Second site: trailing comma is not JSON")
def w_refuse_manifest_trailing_comma():
    def build(dest: Path) -> Path:
        return write_bytes_root(dest, b'{"sources":[],}\n', {})

    refuse_pair(build, "refuse: bad manifest", "41")


@witness("manifest_doc", "Empty object missing sources must refuse, not KeyError")
def w_refuse_manifest_missing_sources_key():
    def build(dest: Path) -> Path:
        return write_bytes_root(dest, b"{}\n", {})

    refuse_pair(build, "refuse: bad manifest", "42")


@witness("manifest_doc", "Top-level JSON null is not a manifest object")
def w_refuse_manifest_null_toplevel():
    def build(dest: Path) -> Path:
        return write_bytes_root(dest, b"null\n", {})

    refuse_pair(build, "refuse: bad manifest", "76")


@witness("manifest_doc", "sources must be an array, not null")
def w_refuse_sources_null_value():
    def build(dest: Path) -> Path:
        return write_bytes_root(dest, b'{"sources":null}\n', {})

    refuse_pair(build, "refuse: bad manifest", "77")


@witness("manifest_doc", "An empty sources array merges to {}")
def w_visible_empty_sources_array():
    root = write_root(opaque("vis_empty_src"), {"sources": []}, {})
    assert_success_bytes(root, "{}\n", opaque("vis_empty_src.json"))


@witness("manifest_doc", "Complete JSON object plus trailing value is a bad manifest")
def w_refuse_manifest_trailing_extra_json_value():
    def build(dest: Path) -> Path:
        return write_bytes_root(dest, b'{"sources":[]}0\n', {})

    refuse_pair(build, "refuse: bad manifest", "90")


@witness("manifest_doc", "Non-JSON NaN is refused even when a later duplicate priority would last-win")
def w_refuse_manifest_nan_hidden_by_duplicate_priority():
    def build(dest: Path) -> Path:
        return write_bytes_root(
            dest,
            b'{"sources":[{"id":"a","priority":NaN,"priority":0}]}\n',
            {"a.json": b"{}\n"},
        )

    refuse_pair(build, "refuse: bad manifest", "97")


@witness("manifest_doc", "manifest sources must be a JSON array, not a string")
def w_refuse_sources_value_empty_string():
    def build(dest: Path) -> Path:
        return write_bytes_root(dest, b'{"sources":""}\n', {})

    refuse_pair(build, "refuse: bad manifest", "105")


@witness("manifest_doc", "Infinity token is refused even if a later duplicate sources key last-wins")
def w_refuse_manifest_infinity_hidden_by_duplicate_sources():
    def build(dest: Path) -> Path:
        return write_bytes_root(
            dest,
            b'{"sources":Infinity,"sources":[]}\n',
            {},
        )

    refuse_pair(build, "refuse: bad manifest", "110")


@witness("manifest_doc", "-Infinity token is likewise refused under duplicate-key decoding")
def w_refuse_manifest_neg_infinity_hidden_by_duplicate_sources():
    def build(dest: Path) -> Path:
        return write_bytes_root(
            dest,
            b'{"sources":-Infinity,"sources":[]}\n',
            {},
        )

    refuse_pair(build, "refuse: bad manifest", "111")


@witness("entries", "Inclusive priority endpoints 0 and 1000000 succeed")
def w_visible_priority_endpoints():
    expected = textwrap.dedent(
        """\
        {
          "hi": 1,
          "lo": 1
        }
        """
    )
    root = write_root(
        opaque("vis_pri_ends"),
        {
            "sources": [
                {"id": "lo", "priority": 0},
                {"id": "hi", "priority": 1000000},
            ]
        },
        {"lo": {"lo": 1}, "hi": {"hi": 1}},
    )
    assert_success_bytes(root, expected, opaque("vis_pri_ends.json"))


@witness("entries", "Duplicate manifest ids are a bad manifest")
def w_refuse_duplicate_id():
    def build(dest: Path) -> Path:
        return write_root(
            dest,
            {
                "sources": [
                    {"id": "a", "priority": 1},
                    {"id": "a", "priority": 2},
                ]
            },
            {"a": {}},
        )

    refuse_pair(build, "refuse: bad manifest", "01")


@witness("entries", "Boolean priority is not a JSON integer")
def w_refuse_boolean_priority():
    def build(dest: Path) -> Path:
        return write_root(
            dest,
            {"sources": [{"id": "a", "priority": True}]},
            {"a": {}},
        )

    refuse_pair(build, "refuse: bad manifest", "04")


@witness("entries", "Floating-point priority is not a JSON integer")
def w_refuse_float_priority():
    def build(dest: Path) -> Path:
        return write_bytes_root(
            dest,
            b'{"sources":[{"id":"a","priority":1.5}]}\n',
            {"a.json": b"{}\n"},
        )

    refuse_pair(build, "refuse: bad manifest", "05")


@witness("entries", "Priority below 0 is a bad manifest")
def w_refuse_priority_out_of_range():
    def build(dest: Path) -> Path:
        return write_root(
            dest,
            {"sources": [{"id": "a", "priority": -1}]},
            {"a": {}},
        )

    refuse_pair(build, "refuse: bad manifest", "06")


@witness("entries", "Manifest entry with a third key is malformed")
def w_refuse_extra_entry_field():
    def build(dest: Path) -> Path:
        return write_bytes_root(
            dest,
            b'{"sources":[{"id":"a","priority":1,"extra":0}]}\n',
            {"a.json": b"{}\n"},
        )

    refuse_pair(build, "refuse: bad manifest", "08")


@witness("entries", "Non-object sources array element is a bad manifest")
def w_refuse_manifest_entry_null():
    def build(dest: Path) -> Path:
        return write_bytes_root(dest, b'{"sources":[null]}\n', {})

    refuse_pair(build, "refuse: bad manifest", "43")


@witness("entries", "Second site: numeric entry is not an object")
def w_refuse_manifest_entry_number():
    def build(dest: Path) -> Path:
        return write_bytes_root(dest, b'{"sources":[1]}\n', {})

    refuse_pair(build, "refuse: bad manifest", "44")


@witness("entries", "Entry missing priority must refuse")
def w_refuse_manifest_entry_missing_priority():
    def build(dest: Path) -> Path:
        return write_bytes_root(
            dest,
            b'{"sources":[{"id":"a"}]}\n',
            {"a.json": b"{}\n"},
        )

    refuse_pair(build, "refuse: bad manifest", "45")


@witness("entries", "Entry missing id must refuse")
def w_refuse_manifest_entry_missing_id():
    def build(dest: Path) -> Path:
        return write_bytes_root(
            dest,
            b'{"sources":[{"priority":1}]}\n',
            {},
        )

    refuse_pair(build, "refuse: bad manifest", "46")


@witness("entries", "JSON 1.0 is a float, not a JSON integer")
def w_refuse_priority_integer_valued_float():
    def build(dest: Path) -> Path:
        return write_bytes_root(
            dest,
            b'{"sources":[{"id":"a","priority":1.0}]}\n',
            {"a.json": b"{}\n"},
        )

    refuse_pair(build, "refuse: bad manifest", "47")


@witness("entries", "Priority 1000001 exceeds the inclusive upper bound")
def w_refuse_priority_above_upper_bound():
    def build(dest: Path) -> Path:
        return write_root(
            dest,
            {"sources": [{"id": "a", "priority": 1000001}]},
            {"a": {}},
        )

    refuse_pair(build, "refuse: bad manifest", "48")


@witness("entries", "String shorthand in sources array is forbidden")
def w_refuse_sources_string_element():
    def build(dest: Path) -> Path:
        return write_bytes_root(
            dest,
            b'{"sources":["a"]}\n',
            {"a.json": b"{}\n"},
        )

    refuse_pair(build, "refuse: bad manifest", "78")


@witness("entries", "String priority is not a JSON integer")
def w_refuse_priority_string():
    def build(dest: Path) -> Path:
        return write_bytes_root(
            dest,
            b'{"sources":[{"id":"a","priority":"1"}]}\n',
            {"a.json": b"{}\n"},
        )

    refuse_pair(build, "refuse: bad manifest", "79")


@witness("entries", "Null priority is not a JSON integer")
def w_refuse_priority_null():
    def build(dest: Path) -> Path:
        return write_bytes_root(
            dest,
            b'{"sources":[{"id":"a","priority":null}]}\n',
            {"a.json": b"{}\n"},
        )

    refuse_pair(build, "refuse: bad manifest", "80")


@witness("entries", "Every sources entry is validated, not only index 0")
def w_refuse_malformed_entry_not_first():
    def build(dest: Path) -> Path:
        return write_bytes_root(
            dest,
            b'{"sources":[{"id":"a","priority":0},null]}\n',
            {"a.json": b"{}\n"},
        )

    refuse_pair(build, "refuse: bad manifest", "112")


@witness("entries", "Manifest id uniqueness is global, not adjacent-only")
def w_refuse_duplicate_id_nonadjacent():
    def build(dest: Path) -> Path:
        return write_root(
            dest,
            {
                "sources": [
                    {"id": "a", "priority": 0},
                    {"id": "b", "priority": 1},
                    {"id": "a", "priority": 2},
                ]
            },
            {"a": {}, "b": {}},
        )

    refuse_pair(build, "refuse: bad manifest", "113")


@witness("ids", "Uppercase id fails the contract pattern")
def w_refuse_bad_id_grammar():
    def build(dest: Path) -> Path:
        root = write_root(
            dest,
            {"sources": [{"id": "Bad", "priority": 1}]},
            {},
        )
        (root / "sources" / "Bad.json").write_text("{}\n")
        _chown_tree(root, SANDBOX_UID, SANDBOX_GID)
        return root

    refuse_pair(build, "refuse: bad manifest", "07")


@witness("ids", "Id with trailing newline must fail fullmatch")
def w_refuse_id_trailing_newline():
    def build(dest: Path) -> Path:
        if dest.exists():
            shutil.rmtree(dest)
        (dest / "sources").mkdir(parents=True)
        (dest / "manifest.json").write_bytes(
            b'{"sources":[{"id":"a\\n","priority":1}]}\n'
        )
        (dest / "sources" / "a\n.json").write_text("{}\n")
        _chown_tree(dest, SANDBOX_UID, SANDBOX_GID)
        return dest

    refuse_pair(build, "refuse: bad manifest", "49")


@witness("ids", "33-character id exceeds the 32-char bound")
def w_refuse_id_too_long():
    long_id = "a" + ("b" * 32)
    assert len(long_id) == 33

    def build(dest: Path) -> Path:
        return write_root(
            dest,
            {"sources": [{"id": long_id, "priority": 1}]},
            {long_id: {}},
        )

    refuse_pair(build, "refuse: bad manifest", "50")


@witness("ids", "Id must start with a letter, not a digit")
def w_refuse_id_initial_digit():
    def build(dest: Path) -> Path:
        return write_root(
            dest,
            {"sources": [{"id": "1", "priority": 0}]},
            {"1": {}},
        )

    refuse_pair(build, "refuse: bad manifest", "60")


@witness("ids", "Second site: initial digit with following letters")
def w_refuse_id_initial_digit_then_letter():
    def build(dest: Path) -> Path:
        return write_root(
            dest,
            {"sources": [{"id": "1a", "priority": 0}]},
            {"1a": {}},
        )

    refuse_pair(build, "refuse: bad manifest", "61")


@witness("ids", "Id must not start with underscore")
def w_refuse_id_initial_underscore():
    def build(dest: Path) -> Path:
        return write_root(
            dest,
            {"sources": [{"id": "_a", "priority": 0}]},
            {"_a": {}},
        )

    refuse_pair(build, "refuse: bad manifest", "62")


@witness("ids", "Hyphen is not in the id alphabet")
def w_refuse_id_punctuation():
    def build(dest: Path) -> Path:
        return write_root(
            dest,
            {"sources": [{"id": "a-", "priority": 0}]},
            {"a-": {}},
        )

    refuse_pair(build, "refuse: bad manifest", "63")


@witness("ids", "Manifest id must be a JSON string")
def w_refuse_id_non_string():
    def build(dest: Path) -> Path:
        return write_bytes_root(
            dest,
            b'{"sources":[{"id":7,"priority":0}]}\n',
            {},
        )

    refuse_pair(build, "refuse: bad manifest", "74")


@witness("ids", "Empty id fails the required pattern")
def w_refuse_id_empty_string():
    def build(dest: Path) -> Path:
        if dest.exists():
            shutil.rmtree(dest)
        (dest / "sources").mkdir(parents=True)
        (dest / "manifest.json").write_bytes(
            b'{"sources":[{"id":"","priority":0}]}\n'
        )
        (dest / "sources" / ".json").write_text("{}\n")
        _chown_tree(dest, SANDBOX_UID, SANDBOX_GID)
        return dest

    refuse_pair(build, "refuse: bad manifest", "75")


@witness("ids", "Inclusive 32-character id bound succeeds")
def w_visible_id_max_length_32():
    sid = "a" + ("b" * 31)
    assert len(sid) == 32
    root = write_root(
        opaque("vis_id32"),
        {"sources": [{"id": sid, "priority": 1}]},
        {sid: {}},
    )
    assert_success_bytes(root, "{}\n", opaque("vis_id32.json"))


@witness("ids", "An uppercase letter in the id tail is refused")
def w_refuse_id_uppercase_in_tail():
    def build(dest: Path) -> Path:
        return write_root(
            dest,
            {"sources": [{"id": "aB", "priority": 0}]},
            {"aB": {}},
        )

    refuse_pair(build, "refuse: bad manifest", "91")


@witness("ids", "Id alphabet is ASCII [a-z0-9_]; Unicode letter in the tail is bad")
def w_refuse_id_non_ascii_tail():
    def build(dest: Path) -> Path:
        return write_root(
            dest,
            {"sources": [{"id": "aé", "priority": 0}]},
            {"aé": {}},
        )

    refuse_pair(build, "refuse: bad manifest", "100")


@witness("members", "Extra listed .json file is a bad manifest")
def w_refuse_extra_json_member():
    def build(dest: Path) -> Path:
        root = write_root(
            dest,
            {"sources": [{"id": "only", "priority": 1}]},
            {"only": {}},
        )
        (root / "sources" / "ghost.json").write_text("{}\n")
        _chown_tree(root, SANDBOX_UID, SANDBOX_GID)
        return root

    refuse_pair(build, "refuse: bad manifest", "02")


@witness("members", "Missing listed source file is a bad manifest")
def w_refuse_missing_source_file():
    def build(dest: Path) -> Path:
        return write_root(
            dest,
            {
                "sources": [
                    {"id": "a", "priority": 1},
                    {"id": "b", "priority": 2},
                ]
            },
            {"a": {}},
        )

    refuse_pair(build, "refuse: bad manifest", "03")


@witness("members", "A subdirectory under sources/ is a forbidden member")
def w_refuse_extra_subdirectory_member():
    def build(dest: Path) -> Path:
        root = write_root(
            dest,
            {"sources": [{"id": "only", "priority": 1}]},
            {"only": {"a": 1}},
        )
        (root / "sources" / "notes").mkdir()
        _chown_tree(root, SANDBOX_UID, SANDBOX_GID)
        return root

    refuse_pair(build, "refuse: bad manifest", "14")


@witness("members", "A non-.json file under sources/ is a forbidden member")
def w_refuse_extra_non_json_member():
    def build(dest: Path) -> Path:
        root = write_root(
            dest,
            {"sources": [{"id": "only", "priority": 1}]},
            {"only": {}},
        )
        (root / "sources" / "notes.txt").write_text("x\n")
        _chown_tree(root, SANDBOX_UID, SANDBOX_GID)
        return root

    refuse_pair(build, "refuse: bad manifest", "15")


@witness("members", "Listed path that is a directory is not a regular file")
def w_refuse_listed_source_is_directory():
    def build(dest: Path) -> Path:
        if dest.exists():
            shutil.rmtree(dest)
        (dest / "sources").mkdir(parents=True)
        (dest / "manifest.json").write_text(
            json.dumps({"sources": [{"id": "only", "priority": 1}]}, indent=2) + "\n"
        )
        (dest / "sources" / "only.json").mkdir()
        _chown_tree(dest, SANDBOX_UID, SANDBOX_GID)
        return dest

    refuse_pair(build, "refuse: bad manifest", "16")


@witness("members", "Listed source must be a regular file, not a symlink")
def w_refuse_listed_source_symlink():
    def build(dest: Path) -> Path:
        if dest.exists():
            shutil.rmtree(dest)
        (dest / "sources").mkdir(parents=True)
        (dest / "manifest.json").write_text(
            json.dumps({"sources": [{"id": "a", "priority": 0}]}, indent=2) + "\n"
        )
        target = dest / "real_payload.json"
        target.write_text("{}\n")
        (dest / "sources" / "a.json").symlink_to(target)
        _chown_tree(dest, SANDBOX_UID, SANDBOX_GID)
        return dest

    refuse_pair(build, "refuse: bad manifest", "17")


@witness("members", "Dangling symlink under sources/ is a forbidden member")
def w_refuse_extra_dangling_symlink_member():
    def build(dest: Path) -> Path:
        root = write_root(
            dest,
            {"sources": [{"id": "only", "priority": 1}]},
            {"only": {}},
        )
        (root / "sources" / "notes").symlink_to("/no/such/target/dcm")
        _chown_tree(root, SANDBOX_UID, SANDBOX_GID)
        return root

    refuse_pair(build, "refuse: bad manifest", "51")


@witness("members", "A FIFO under sources/ is a non-regular member → bad manifest")
def w_refuse_sources_fifo_member():
    def build(dest: Path) -> Path:
        root = write_root(
            dest,
            {"sources": [{"id": "only", "priority": 1}]},
            {"only": {"ok": 1}},
        )
        fifo = root / "sources" / "pipe"
        if fifo.exists():
            fifo.unlink()
        os.mkfifo(fifo)
        os.chown(fifo, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
        return root

    refuse_pair(build, "refuse: bad manifest", "94")


@witness("members", "Listed sources/<id>.json must be a regular file, not a FIFO")
def w_refuse_listed_source_is_fifo():
    def build(dest: Path) -> Path:
        if dest.exists():
            shutil.rmtree(dest)
        (dest / "sources").mkdir(parents=True)
        (dest / "manifest.json").write_text(
            json.dumps({"sources": [{"id": "a", "priority": 0}]}, indent=2) + "\n"
        )
        fifo = dest / "sources" / "a.json"
        if fifo.exists():
            fifo.unlink()
        os.mkfifo(fifo)
        _chown_tree(dest, SANDBOX_UID, SANDBOX_GID)
        return dest

    refuse_pair(build, "refuse: bad manifest", "108")


@witness("members", "A Unix-domain socket under sources/ is a forbidden member")
def w_refuse_sources_unix_socket_member():
    def build(dest: Path) -> Path:
        root = write_root(
            dest,
            {"sources": [{"id": "a", "priority": 0}]},
            {"a": {}},
        )
        sock_path = root / "sources" / "ghost.json"
        if sock_path.exists():
            sock_path.unlink()
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            srv.bind(str(sock_path))
        finally:
            srv.close()
        _chown_tree(root, SANDBOX_UID, SANDBOX_GID)
        return root

    refuse_pair(build, "refuse: bad manifest", "118")


@witness("order", "Later array position must not beat a lower priority")
def w_visible_priority_overrides_manifest_array_order():
    expected = textwrap.dedent(
        """\
        {
          "v": "from_high",
          "x": 1
        }
        """
    )
    root = write_root(
        opaque("vis_pri_a"),
        {
            "sources": [
                {"id": "high", "priority": 90},
                {"id": "low", "priority": 10},
            ]
        },
        {"high": {"v": "from_high"}, "low": {"v": "from_low", "x": 1}},
    )
    assert_success_bytes(root, expected, opaque("vis_pri_a.json"))


@witness("order", "Second ordering site: equal priority, later id wins")
def w_visible_equal_priority_ordered_by_id():
    expected = textwrap.dedent(
        """\
        {
          "slot": "b_src"
        }
        """
    )
    root = write_root(
        opaque("vis_pri_b"),
        {
            "sources": [
                {"id": "b_src", "priority": 5},
                {"id": "a_src", "priority": 5},
            ]
        },
        {"b_src": {"slot": "b_src"}, "a_src": {"slot": "a_src"}},
    )
    assert_success_bytes(root, expected, opaque("vis_pri_b.json"))


@witness("order", "Manifest entry key order is not significant")
def w_visible_reversed_manifest_entry_key_order():
    expected = "{}\n"
    root = write_root(
        opaque("vis_rev_keys"),
        {"sources": [{"priority": 1, "id": "a"}]},
        {"a": {}},
    )
    assert_success_bytes(root, expected, opaque("vis_rev_keys.json"))


@witness("order", "Priority 10 must sort after 2 numerically, not before lexically")
def w_visible_priority_numeric_not_lexical():
    expected = textwrap.dedent(
        """\
        {
          "winner": "high"
        }
        """
    )
    root = write_root(
        opaque("vis_pri_lex"),
        {
            "sources": [
                {"id": "a", "priority": 2},
                {"id": "z", "priority": 10},
            ]
        },
        {"a": {"winner": "low"}, "z": {"winner": "high"}},
    )
    assert_success_bytes(root, expected, opaque("vis_pri_lex.json"))


@witness("source_doc", "Top-level JSON array source is a bad source")
def w_refuse_source_non_object():
    def build(dest: Path) -> Path:
        return write_bytes_root(
            dest,
            b'{"sources":[{"id":"only","priority":1}]}\n',
            {"only.json": b"[]\n"},
        )

    refuse_pair(build, "refuse: bad source", "36")


@witness("source_doc", "Invalid UTF-8 in a source file is a bad source")
def w_refuse_source_invalid_utf8():
    def build(dest: Path) -> Path:
        return write_bytes_root(
            dest,
            b'{"sources":[{"id":"only","priority":1}]}\n',
            {"only.json": b'{"a":1}\xff'},
        )

    refuse_pair(build, "refuse: bad source", "37")


@witness("source_doc", "Trailing-comma JSON is not valid and is a bad source")
def w_refuse_source_malformed_json():
    def build(dest: Path) -> Path:
        return write_bytes_root(
            dest,
            b'{"sources":[{"id":"only","priority":1}]}\n',
            {"only.json": b'{"a":1,}\n'},
        )

    refuse_pair(build, "refuse: bad source", "38")


@witness("source_doc", "Python NaN token is not JSON and is a bad source")
def w_refuse_source_nan_constant():
    def build(dest: Path) -> Path:
        return write_bytes_root(
            dest,
            b'{"sources":[{"id":"a","priority":0}]}\n',
            {"a.json": b'{"x":NaN}\n'},
        )

    refuse_pair(build, "refuse: bad source", "39")


@witness("source_doc", "Infinity token is not JSON")
def w_refuse_source_infinity_constant():
    def build(dest: Path) -> Path:
        return write_bytes_root(
            dest,
            b'{"sources":[{"id":"a","priority":0}]}\n',
            {"a.json": b'{"x":Infinity}\n'},
        )

    refuse_pair(build, "refuse: bad source", "52")


@witness("source_doc", "-Infinity token is not JSON")
def w_refuse_source_neg_infinity_constant():
    def build(dest: Path) -> Path:
        return write_bytes_root(
            dest,
            b'{"sources":[{"id":"a","priority":0}]}\n',
            {"a.json": b'{"x":-Infinity}\n'},
        )

    refuse_pair(build, "refuse: bad source", "53")


@witness("source_doc", "Top-level JSON null is not an object")
def w_refuse_source_null_top_level():
    def build(dest: Path) -> Path:
        return write_bytes_root(
            dest,
            b'{"sources":[{"id":"only","priority":1}]}\n',
            {"only.json": b"null\n"},
        )

    refuse_pair(build, "refuse: bad source", "56")


@witness("source_doc", "Malformed JSON within the graded depth bound is a bad source")
def w_refuse_source_shallow_malformed_array():
    def build(dest: Path) -> Path:
        return write_bytes_root(
            dest,
            b'{"sources":[{"id":"a","priority":0}]}\n',
            {"a.json": b"[[[[0]]]\n"},
        )

    refuse_pair(build, "refuse: bad source", "57")


@witness("source_doc", "NaN nested under an object is still non-JSON")
def w_refuse_nested_nan_in_source():
    def build(dest: Path) -> Path:
        return write_bytes_root(
            dest,
            b'{"sources":[{"id":"only","priority":1}]}\n',
            {"only.json": b'{"n":{"x":NaN}}\n'},
        )

    refuse_pair(build, "refuse: bad source", "101")


@witness("source_doc", "A JSON boolean source is not an object")
def w_refuse_source_boolean_scalar():
    def build(dest: Path) -> Path:
        return write_bytes_root(
            dest,
            b'{"sources":[{"id":"a","priority":0}]}\n',
            {"a.json": b"true\n"},
        )

    refuse_pair(build, "refuse: bad source", "106")


@witness("source_doc", "A top-level JSON number is not an object")
def w_refuse_source_numeric_scalar():
    def build(dest: Path) -> Path:
        return write_bytes_root(
            dest,
            b'{"sources":[{"id":"a","priority":0}]}\n',
            {"a.json": b"1\n"},
        )

    refuse_pair(build, "refuse: bad source", "115")


@witness("source_doc", "A top-level JSON string is not an object")
def w_refuse_source_string_scalar():
    def build(dest: Path) -> Path:
        return write_bytes_root(
            dest,
            b'{"sources":[{"id":"a","priority":0}]}\n',
            {"a.json": b'"x"\n'},
        )

    refuse_pair(build, "refuse: bad source", "116")


@witness("source_doc", "NaN overwritten by a duplicate key must still refuse")
def w_refuse_source_nan_hidden_by_duplicate_key():
    def build(dest: Path) -> Path:
        return write_bytes_root(
            dest,
            b'{"sources":[{"id":"a","priority":0}]}\n',
            {"a.json": b'{"x":NaN,"x":0}\n'},
        )

    refuse_pair(build, "refuse: bad source", "119")


@witness("source_doc", "Infinity hidden by duplicate-key last-win must still refuse")
def w_refuse_source_infinity_hidden_by_duplicate_key():
    def build(dest: Path) -> Path:
        return write_bytes_root(
            dest,
            b'{"sources":[{"id":"a","priority":0}]}\n',
            {"a.json": b'{"x":Infinity,"x":1}\n'},
        )

    refuse_pair(build, "refuse: bad source", "120")


@witness("metadata", "$extends naming an unknown id is a bad source")
def w_refuse_unknown_extends():
    def build(dest: Path) -> Path:
        return write_root(
            dest,
            {"sources": [{"id": "only", "priority": 1}]},
            {"only": {"$extends": ["missing"]}},
        )

    refuse_pair(build, "refuse: bad source", "30")


@witness("metadata", "Disallowed top-level $ key is a bad source")
def w_refuse_disallowed_dollar_key():
    def build(dest: Path) -> Path:
        return write_root(
            dest,
            {"sources": [{"id": "only", "priority": 1}]},
            {"only": {"$nope": 1}},
        )

    refuse_pair(build, "refuse: bad source", "33")


@witness("metadata", "$extends must be an array, not a string")
def w_refuse_extends_string_not_array():
    def build(dest: Path) -> Path:
        return write_root(
            dest,
            {
                "sources": [
                    {"id": "parent", "priority": 1},
                    {"id": "child", "priority": 2},
                ]
            },
            {"parent": {"a": 1}, "child": {"$extends": "parent"}},
        )

    refuse_pair(build, "refuse: bad source", "34")


@witness("metadata", "$extends array members must be strings")
def w_refuse_extends_non_string_member():
    def build(dest: Path) -> Path:
        return write_root(
            dest,
            {"sources": [{"id": "only", "priority": 1}]},
            {"only": {"$extends": [1]}},
        )

    refuse_pair(build, "refuse: bad source", "35")


@witness("metadata", "Explicit empty $extends is valid")
def w_visible_empty_extends_array():
    expected = "{}\n"
    root = write_root(
        opaque("vis_ext_empty"),
        {"sources": [{"id": "only", "priority": 1}]},
        {"only": {"$extends": []}},
    )
    assert_success_bytes(root, expected, opaque("vis_ext_empty.json"))


@witness("metadata", "Dollar-prefix restriction is top-level only")
def w_visible_nested_dollar_key_allowed():
    expected = textwrap.dedent(
        """\
        {
          "n": {
            "$label": 1
          }
        }
        """
    )
    root = write_root(
        opaque("vis_nested_dollar"),
        {"sources": [{"id": "only", "priority": 1}]},
        {"only": {"n": {"$label": 1}}},
    )
    assert_success_bytes(root, expected, opaque("vis_nested_dollar.json"))


@witness("metadata", "Explicit null $extends is malformed metadata")
def w_refuse_extends_null_value():
    def build(dest: Path) -> Path:
        return write_root(
            dest,
            {"sources": [{"id": "only", "priority": 1}]},
            {"only": {"$extends": None}},
        )

    refuse_pair(build, "refuse: bad source", "65")


@witness("metadata", "Duplicate ids in $extends are a bad source")
def w_refuse_extends_duplicate_parent():
    def build(dest: Path) -> Path:
        return write_root(
            dest,
            {
                "sources": [
                    {"id": "a", "priority": 0},
                    {"id": "b", "priority": 1},
                ]
            },
            {"a": {"k": 1}, "b": {"$extends": ["a", "a"]}},
        )

    refuse_pair(build, "refuse: bad source", "92")


@witness("metadata", "Every $extends member is validated, not only the first")
def w_refuse_extends_second_member_non_string():
    def build(dest: Path) -> Path:
        return write_root(
            dest,
            {
                "sources": [
                    {"id": "p", "priority": 1},
                    {"id": "child", "priority": 2},
                ]
            },
            {"p": {}, "child": {"$extends": ["p", 1]}},
        )

    refuse_pair(build, "refuse: bad source", "95")


@witness("metadata", "Unknown id after a valid parent is still bad source")
def w_refuse_extends_second_member_unknown():
    def build(dest: Path) -> Path:
        return write_root(
            dest,
            {
                "sources": [
                    {"id": "p", "priority": 1},
                    {"id": "child", "priority": 2},
                ]
            },
            {"p": {}, "child": {"$extends": ["p", "missing"]}},
        )

    refuse_pair(build, "refuse: bad source", "96")


@witness("metadata", "$extends must be a JSON array, not an object")
def w_refuse_extends_object_not_array():
    def build(dest: Path) -> Path:
        return write_root(
            dest,
            {
                "sources": [
                    {"id": "parent", "priority": 1},
                    {"id": "child", "priority": 2},
                ]
            },
            {"parent": {"p": 1}, "child": {"$extends": {"parent": True}}},
        )

    refuse_pair(build, "refuse: bad source", "98")


@witness("metadata", "Non-string object members in $extends refuse (no crash)")
def w_refuse_extends_unhashable_member():
    def build(dest: Path) -> Path:
        return write_root(
            dest,
            {"sources": [{"id": "only", "priority": 1}]},
            {"only": {"$extends": [{}]}},
        )

    refuse_pair(build, "refuse: bad source", "99")


@witness("metadata", "$extends uniqueness is global, not only adjacent")
def w_refuse_extends_nonadjacent_duplicate():
    def build(dest: Path) -> Path:
        return write_root(
            dest,
            {
                "sources": [
                    {"id": "a", "priority": 0},
                    {"id": "b", "priority": 1},
                    {"id": "child", "priority": 2},
                ]
            },
            {"a": {}, "b": {}, "child": {"$extends": ["a", "b", "a"]}},
        )

    refuse_pair(build, "refuse: bad source", "102")


@witness("metadata", "Top-level id/priority in a source are ordinary data keys")
def w_visible_id_priority_are_data_keys():
    expected = json.dumps({"id": 1, "priority": 2}, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    root = write_root(
        opaque("vis_id_pri_data"),
        {"sources": [{"id": "a", "priority": 0}]},
        {"a": {"id": 1, "priority": 2}},
    )
    assert_success_bytes(root, expected, opaque("vis_id_pri_data.json"))


@witness("metadata", "Disallowed $ keys refuse even when $extends is also present")
def w_refuse_dollar_key_alongside_extends():
    def build(dest: Path) -> Path:
        return write_root(
            dest,
            {"sources": [{"id": "only", "priority": 1}]},
            {"only": {"$extends": [], "$evil": 1}},
        )

    refuse_pair(build, "refuse: bad source", "107")


@witness("metadata", "Falsy non-array $extends (JSON false) is bad source")
def w_refuse_extends_false():
    def build(dest: Path) -> Path:
        return write_bytes_root(
            dest,
            b'{"sources":[{"id":"only","priority":1}]}\n',
            {"only.json": b'{"$extends":false}\n'},
        )

    refuse_pair(build, "refuse: bad source", "114")


@witness("metadata", "Numeric $extends is not an array")
def w_refuse_extends_number():
    def build(dest: Path) -> Path:
        return write_bytes_root(
            dest,
            b'{"sources":[{"id":"a","priority":0}]}\n',
            {"a.json": b'{"$extends":1}\n'},
        )

    refuse_pair(build, "refuse: bad source", "117")


@witness("cycles", "Two-source $extends cycle is a bad source")
def w_refuse_extends_cycle_two():
    def build(dest: Path) -> Path:
        return write_root(
            dest,
            {
                "sources": [
                    {"id": "a", "priority": 1},
                    {"id": "b", "priority": 2},
                ]
            },
            {"a": {"$extends": ["b"]}, "b": {"$extends": ["a"]}},
        )

    refuse_pair(build, "refuse: bad source", "31")


@witness("cycles", "Three-source $extends cycle is a bad source")
def w_refuse_extends_cycle_three():
    def build(dest: Path) -> Path:
        return write_root(
            dest,
            {
                "sources": [
                    {"id": "a", "priority": 1},
                    {"id": "b", "priority": 2},
                    {"id": "c", "priority": 3},
                ]
            },
            {
                "a": {"$extends": ["b"]},
                "b": {"$extends": ["c"]},
                "c": {"$extends": ["a"]},
            },
        )

    refuse_pair(build, "refuse: bad source", "32")


@witness("cycles", "A source that extends itself is a cycle")
def w_refuse_extends_self_cycle():
    def build(dest: Path) -> Path:
        return write_root(
            dest,
            {"sources": [{"id": "a", "priority": 1}]},
            {"a": {"$extends": ["a"]}},
        )

    refuse_pair(build, "refuse: bad source", "64")


@witness("cycles", "A cycle refusal leaves an existing output file untouched")
def w_refuse_preserves_preexisting_out_on_extends_cycle():
    def build(dest: Path) -> Path:
        return write_root(
            dest,
            {
                "sources": [
                    {"id": "a", "priority": 1},
                    {"id": "b", "priority": 2},
                ]
            },
            {"a": {"$extends": ["b"]}, "b": {"$extends": ["a"]}},
        )

    for suffix in ("p", "q"):
        name = f"wx_109_{suffix}"
        root = build(opaque(name))
        out = opaque(f"{name}.out")
        assert_refuse(
            root,
            "refuse: bad source",
            out,
            preexisting=b"sentinel\n",
        )


@witness("apply", "Object overlay merges; siblings under the same parent survive")
def w_visible_deep_merge_preserves_sibling_keys():
    expected = textwrap.dedent(
        """\
        {
          "db": {
            "host": "h2",
            "port": 5432
          }
        }
        """
    )
    root = write_root(
        opaque("vis_deep_a"),
        {
            "sources": [
                {"id": "base", "priority": 1},
                {"id": "over", "priority": 2},
            ]
        },
        {
            "base": {"db": {"host": "h1", "port": 5432}},
            "over": {"db": {"host": "h2"}},
        },
    )
    assert_success_bytes(root, expected, opaque("vis_deep_a.json"))


@witness("apply", "Second deep-merge site: nested object retains untouched leaf")
def w_visible_deep_merge_nested_second_site():
    expected = textwrap.dedent(
        """\
        {
          "n": {
            "x": 1,
            "y": {
              "w": 3,
              "z": 2
            }
          }
        }
        """
    )
    root = write_root(
        opaque("vis_deep_b"),
        {
            "sources": [
                {"id": "b0", "priority": 1},
                {"id": "b1", "priority": 2},
            ]
        },
        {
            "b0": {"n": {"x": 1, "y": {"z": 1}}},
            "b1": {"n": {"y": {"z": 2, "w": 3}}},
        },
    )
    assert_success_bytes(root, expected, opaque("vis_deep_b.json"))


@witness("apply", "Arrays replace whole; they are not concatenated")
def w_visible_array_replaced_not_concatenated():
    expected = textwrap.dedent(
        """\
        {
          "k": 1,
          "list": [
            9
          ]
        }
        """
    )
    root = write_root(
        opaque("vis_arr"),
        {
            "sources": [
                {"id": "base", "priority": 1},
                {"id": "over", "priority": 2},
            ]
        },
        {"base": {"list": [1, 2, 3], "k": 1}, "over": {"list": [9]}},
    )
    assert_success_bytes(root, expected, opaque("vis_arr.json"))


@witness("apply", "Object overlay discards a non-object base and Applies against {}")
def w_visible_apply_object_over_scalar():
    expected = textwrap.dedent(
        """\
        {
          "n": {
            "y": 2
          }
        }
        """
    )
    root = write_root(
        opaque("vis_obj_over_scalar"),
        {
            "sources": [
                {"id": "base", "priority": 1},
                {"id": "over", "priority": 2},
            ]
        },
        {
            "base": {"n": 1},
            "over": {"n": {"x": "$delete", "y": 2}},
        },
    )
    assert_success_bytes(root, expected, opaque("vis_obj_over_scalar.json"))


@witness("apply", "Second site: object replaces scalar without nested delete")
def w_visible_apply_object_over_scalar_plain():
    expected = textwrap.dedent(
        """\
        {
          "k": {
            "x": 2
          }
        }
        """
    )
    root = write_root(
        opaque("vis_obj_over_scalar_b"),
        {
            "sources": [
                {"id": "a", "priority": 1},
                {"id": "b", "priority": 2},
            ]
        },
        {"a": {"k": 1}, "b": {"k": {"x": 2}}},
    )
    assert_success_bytes(root, expected, opaque("vis_obj_over_scalar_b.json"))


@witness("apply", "Scalar overlay replaces an object base")
def w_visible_apply_scalar_over_object():
    expected = textwrap.dedent(
        """\
        {
          "k": 7
        }
        """
    )
    root = write_root(
        opaque("vis_scalar_over_obj"),
        {
            "sources": [
                {"id": "base", "priority": 1},
                {"id": "over", "priority": 2},
            ]
        },
        {"base": {"k": {"x": 1}}, "over": {"k": 7}},
    )
    assert_success_bytes(root, expected, opaque("vis_scalar_over_obj.json"))


@witness("apply", "Applying merges past two object levels and keeps siblings at depth 3")
def w_visible_deep_merge_three_object_levels_keeps_sibling():
    expected = json.dumps(
        {"a": {"b": {"c": {"keep": 1, "old": 2}}}},
        indent=2,
        sort_keys=True,
        ensure_ascii=True,
    ) + "\n"
    root = write_root(
        opaque("vis_deep_abc"),
        {
            "sources": [
                {"id": "base", "priority": 1},
                {"id": "over", "priority": 2},
            ]
        },
        {
            "base": {"a": {"b": {"c": {"keep": 1, "old": 1}}}},
            "over": {"a": {"b": {"c": {"old": 2}}}},
        },
    )
    assert_success_bytes(root, expected, opaque("vis_deep_abc.json"))


@witness("apply", "Second site: three-level overlap retains an untouched leaf")
def w_visible_deep_merge_three_object_levels_second_site():
    expected = json.dumps(
        {"p": {"q": {"r": {"stay": True, "v": 9}}}},
        indent=2,
        sort_keys=True,
        ensure_ascii=True,
    ) + "\n"
    root = write_root(
        opaque("vis_deep_pqr"),
        {
            "sources": [
                {"id": "base", "priority": 0},
                {"id": "over", "priority": 1},
            ]
        },
        {
            "base": {"p": {"q": {"r": {"stay": True, "v": 0}}}},
            "over": {"p": {"q": {"r": {"v": 9}}}},
        },
    )
    assert_success_bytes(root, expected, opaque("vis_deep_pqr.json"))


@witness("apply", "Applying keeps siblings at the fourth overlapping object level")
def w_visible_deep_merge_four_object_levels_keeps_sibling():
    expected = json.dumps(
        {"a": {"b": {"c": {"d": {"keep": 1, "v": 2}}}}},
        indent=2,
        sort_keys=True,
        ensure_ascii=True,
    ) + "\n"
    root = write_root(
        opaque("vis_deep_abcd"),
        {
            "sources": [
                {"id": "base", "priority": 1},
                {"id": "over", "priority": 2},
            ]
        },
        {
            "base": {"a": {"b": {"c": {"d": {"keep": 1, "v": 1}}}}},
            "over": {"a": {"b": {"c": {"d": {"v": 2}}}}},
        },
    )
    assert_success_bytes(root, expected, opaque("vis_deep_abcd.json"))


@witness("apply", "Second four-level site: sibling retained under w.x.y.z")
def w_visible_deep_merge_four_object_levels_second_site():
    expected = json.dumps(
        {"w": {"x": {"y": {"z": {"keep": True, "n": 9}}}}},
        indent=2,
        sort_keys=True,
        ensure_ascii=True,
    ) + "\n"
    root = write_root(
        opaque("vis_deep_wxyz"),
        {
            "sources": [
                {"id": "base", "priority": 0},
                {"id": "over", "priority": 1},
            ]
        },
        {
            "base": {"w": {"x": {"y": {"z": {"keep": True, "n": 0}}}}},
            "over": {"w": {"x": {"y": {"z": {"n": 9}}}}},
        },
    )
    assert_success_bytes(root, expected, opaque("vis_deep_wxyz.json"))


@witness("apply", "Applying false replaces an existing value")
def w_visible_apply_false_replaces_existing():
    expected = json.dumps({"x": False}, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    root = write_root(
        opaque("vis_apply_false"),
        {
            "sources": [
                {"id": "base", "priority": 1},
                {"id": "over", "priority": 2},
            ]
        },
        {"base": {"x": 1}, "over": {"x": False}},
    )
    assert_success_bytes(root, expected, opaque("vis_apply_false.json"))


@witness("apply", "Applying keeps siblings at the fifth overlapping object level")
def w_visible_deep_merge_five_object_levels_keeps_sibling():
    expected = json.dumps(
        {"a": {"b": {"c": {"d": {"e": {"keep": 1, "v": 2}}}}}},
        indent=2,
        sort_keys=True,
        ensure_ascii=True,
    ) + "\n"
    root = write_root(
        opaque("vis_deep_abcde"),
        {
            "sources": [
                {"id": "base", "priority": 1},
                {"id": "over", "priority": 2},
            ]
        },
        {
            "base": {"a": {"b": {"c": {"d": {"e": {"keep": 1, "v": 0}}}}}},
            "over": {"a": {"b": {"c": {"d": {"e": {"v": 2}}}}}},
        },
    )
    assert_success_bytes(root, expected, opaque("vis_deep_abcde.json"))


@witness("markers", "JSON null stays; the $delete string removes the key")
def w_visible_null_is_value_delete_removes():
    expected = textwrap.dedent(
        """\
        {
          "a": null
        }
        """
    )
    root = write_root(
        opaque("vis_null_a"),
        {
            "sources": [
                {"id": "base", "priority": 1},
                {"id": "over", "priority": 2},
            ]
        },
        {"base": {"a": 1, "b": 2}, "over": {"a": None, "b": "$delete"}},
    )
    assert_success_bytes(root, expected, opaque("vis_null_a.json"))


@witness("markers", "Second null/$delete site with different key names")
def w_visible_null_and_delete_second_site():
    expected = textwrap.dedent(
        """\
        {
          "x": null
        }
        """
    )
    root = write_root(
        opaque("vis_null_b"),
        {
            "sources": [
                {"id": "p0", "priority": 1},
                {"id": "p1", "priority": 2},
            ]
        },
        {"p0": {"x": "was", "y": "was"}, "p1": {"x": None, "y": "$delete"}},
    )
    assert_success_bytes(root, expected, opaque("vis_null_b.json"))


@witness("markers", "Markers inside an object new at its key act, three levels down")
def w_visible_delete_inside_new_object():
    expected = textwrap.dedent(
        """\
        {
          "outer": {}
        }
        """
    )
    root = write_root(
        opaque("vis_del_new"),
        {"sources": [{"id": "only", "priority": 1}]},
        {"only": {"outer": {"inner": "$delete"}}},
    )
    assert_success_bytes(root, expected, opaque("vis_del_new.json"))


@witness("markers", "Second site: nested marker under absent key yields empty object")
def w_visible_delete_inside_new_object_second_site():
    expected = textwrap.dedent(
        """\
        {
          "nest": {}
        }
        """
    )
    root = write_root(
        opaque("vis_del_new_b"),
        {"sources": [{"id": "solo", "priority": 0}]},
        {"solo": {"nest": {"x": "$delete"}}},
    )
    assert_success_bytes(root, expected, opaque("vis_del_new_b.json"))


@witness("markers", "A later source may recreate a key an earlier source deleted")
def w_visible_recreate_after_delete():
    expected = textwrap.dedent(
        """\
        {
          "x": 7
        }
        """
    )
    root = write_root(
        opaque("vis_recreate"),
        {
            "sources": [
                {"id": "a", "priority": 1},
                {"id": "b", "priority": 2},
                {"id": "c", "priority": 3},
            ]
        },
        {"a": {"x": 1}, "b": {"x": "$delete"}, "c": {"x": 7}},
    )
    assert_success_bytes(root, expected, opaque("vis_recreate.json"))


@witness("markers", "Only the exact $delete string is a marker")
def w_visible_delete_prefix_not_marker():
    expected = textwrap.dedent(
        """\
        {
          "x": "$delete!"
        }
        """
    )
    root = write_root(
        opaque("vis_del_prefix"),
        {"sources": [{"id": "only", "priority": 1}]},
        {"only": {"x": "$delete!"}},
    )
    assert_success_bytes(root, expected, opaque("vis_del_prefix.json"))


@witness("markers", "Delete markers deeper than one nest must be Applied")
def w_visible_deep_delete_three_levels():
    expected = textwrap.dedent(
        """\
        {
          "a": {
            "b": {}
          }
        }
        """
    )
    root = write_root(
        opaque("vis_deep_del"),
        {"sources": [{"id": "only", "priority": 1}]},
        {"only": {"a": {"b": {"c": "$delete"}}}},
    )
    assert_success_bytes(root, expected, opaque("vis_deep_del.json"))


@witness("markers", "Second site: three-level delete under a newly introduced object")
def w_visible_deep_delete_new_object_three_levels():
    expected = textwrap.dedent(
        """\
        {
          "outer": {
            "middle": {}
          }
        }
        """
    )
    root = write_root(
        opaque("vis_deep_del_b"),
        {"sources": [{"id": "only", "priority": 1}]},
        {"only": {"outer": {"middle": {"inner": "$delete"}}}},
    )
    assert_success_bytes(root, expected, opaque("vis_deep_del_b.json"))


@witness("markers", "$delete applies through four nested object levels")
def w_visible_delete_four_levels():
    expected = textwrap.dedent(
        """\
        {
          "a": {
            "b": {
              "c": {}
            }
          }
        }
        """
    )
    root = write_root(
        opaque("vis_del4"),
        {"sources": [{"id": "only", "priority": 1}]},
        {"only": {"a": {"b": {"c": {"d": "$delete"}}}}},
    )
    assert_success_bytes(root, expected, opaque("vis_del4.json"))


@witness("markers", "$delete at the depth-6 bound is Applied")
def w_visible_deep_delete_within_depth_bound():
    inner: dict = {"leaf": "$delete"}
    for _ in range(5):
        inner = {"n": inner}
    expected_obj: dict = {}
    cur = expected_obj
    for _ in range(5):
        cur["n"] = {}
        cur = cur["n"]
    expected = json.dumps(expected_obj, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    root = write_root(
        opaque("vis_deep_del"),
        {"sources": [{"id": "only", "priority": 1}]},
        {"only": inner},
    )
    assert_success_bytes(root, expected, opaque("vis_deep_del.json"))


@witness("markers", "Array replace-whole keeps $delete as an ordinary array element")
def w_visible_delete_string_inside_array_is_ordinary():
    expected = json.dumps(
        {"list": ["$delete"]},
        indent=2,
        sort_keys=True,
        ensure_ascii=True,
    ) + "\n"
    root = write_root(
        opaque("vis_arr_delete_str"),
        {"sources": [{"id": "only", "priority": 1}]},
        {"only": {"list": ["$delete"]}},
    )
    assert_success_bytes(root, expected, opaque("vis_arr_delete_str.json"))


@witness("markers", "Objects inside arrays are not Applied; nested $delete stays")
def w_visible_delete_object_inside_array_unapplied():
    expected = json.dumps(
        {"list": [{"x": "$delete"}]},
        indent=2,
        sort_keys=True,
        ensure_ascii=True,
    ) + "\n"
    root = write_root(
        opaque("vis_arr_obj_delete"),
        {"sources": [{"id": "only", "priority": 1}]},
        {"only": {"list": [{"x": "$delete"}]}},
    )
    assert_success_bytes(root, expected, opaque("vis_arr_obj_delete.json"))


@witness("layers", "Own data is laid over the inherited configuration and wins")
def w_visible_extends_then_own_keys_win():
    expected = textwrap.dedent(
        """\
        {
          "a": 1,
          "b": 9,
          "c": 3
        }
        """
    )
    root = write_root(
        opaque("vis_ext_a"),
        {
            "sources": [
                {"id": "parent", "priority": 1},
                {"id": "child", "priority": 2},
            ]
        },
        {
            "parent": {"a": 1, "b": 2},
            "child": {"$extends": ["parent"], "b": 9, "c": 3},
        },
    )
    assert_success_bytes(root, expected, opaque("vis_ext_a.json"))


@witness("layers", "A child's own marker stays in its layer and removes the key from the merge")
def w_visible_extends_delete_marker_applied_on_final():
    expected = textwrap.dedent(
        """\
        {
          "a": 1,
          "c": 3
        }
        """
    )
    root = write_root(
        opaque("vis_ext_b"),
        {
            "sources": [
                {"id": "parent", "priority": 1},
                {"id": "child", "priority": 2},
            ]
        },
        {
            "parent": {"a": 1, "b": 2},
            "child": {"$extends": ["parent"], "b": "$delete", "c": 3},
        },
    )
    assert_success_bytes(root, expected, opaque("vis_ext_b.json"))


@witness("layers", "Own nested markers are laid member by member and act when the layer is applied")
def w_visible_nested_stamp_retains_delete_for_final_apply():
    expected = textwrap.dedent(
        """\
        {
          "n": {
            "y": 2
          }
        }
        """
    )
    root = write_root(
        opaque("vis_stamp_nest"),
        {
            "sources": [
                {"id": "parent", "priority": 1},
                {"id": "child", "priority": 2},
            ]
        },
        {
            "parent": {"n": {"x": 1, "y": 2}},
            "child": {"$extends": ["parent"], "n": {"x": "$delete"}},
        },
    )
    assert_success_bytes(root, expected, opaque("vis_stamp_nest.json"))


@witness("layers", "An own object laid over an inherited scalar replaces it")
def w_visible_stamp_object_over_scalar():
    expected = textwrap.dedent(
        """\
        {
          "k": {
            "x": 2
          }
        }
        """
    )
    root = write_root(
        opaque("vis_stamp_obj_scalar"),
        {
            "sources": [
                {"id": "parent", "priority": 1},
                {"id": "child", "priority": 2},
            ]
        },
        {
            "parent": {"k": 1},
            "child": {"$extends": ["parent"], "k": {"x": 2}},
        },
    )
    assert_success_bytes(root, expected, opaque("vis_stamp_obj_scalar.json"))


@witness("layers", "An own object laid over a scalar keeps its markers for the merge")
def w_visible_stamp_object_over_scalar_with_nested_delete():
    expected = textwrap.dedent(
        """\
        {
          "k": {}
        }
        """
    )
    root = write_root(
        opaque("vis_stamp_scalar_del"),
        {
            "sources": [
                {"id": "parent", "priority": 1},
                {"id": "middle", "priority": 2},
                {"id": "child", "priority": 3},
            ]
        },
        {
            "parent": {"k": 1},
            "middle": {"k": {"x": 2}},
            "child": {"$extends": ["parent"], "k": {"x": "$delete"}},
        },
    )
    assert_success_bytes(root, expected, opaque("vis_stamp_scalar_del.json"))


@witness("layers", "Child scalar data replaces an inherited object")
def w_visible_stamp_scalar_over_object():
    expected = textwrap.dedent(
        """\
        {
          "k": 0
        }
        """
    )
    root = write_root(
        opaque("vis_stamp_scalar_obj"),
        {
            "sources": [
                {"id": "parent", "priority": 1},
                {"id": "child", "priority": 2},
            ]
        },
        {
            "parent": {"k": {"x": 1}},
            "child": {"$extends": ["parent"], "k": 0},
        },
    )
    assert_success_bytes(root, expected, opaque("vis_stamp_scalar_obj.json"))


@witness("layers", "An own array laid over an inherited array replaces it whole")
def w_visible_stamp_array_replace_not_concat():
    expected = textwrap.dedent(
        """\
        {
          "x": [
            2
          ]
        }
        """
    )
    root = write_root(
        opaque("vis_stamp_arr"),
        {
            "sources": [
                {"id": "parent", "priority": 1},
                {"id": "child", "priority": 2},
            ]
        },
        {
            "parent": {"x": [1]},
            "child": {"$extends": ["parent"], "x": [2]},
        },
    )
    assert_success_bytes(root, expected, opaque("vis_stamp_arr.json"))


@witness("layers", "Own null laid over an inherited value replaces it")
def w_visible_stamp_null_replaces_inherited():
    expected = json.dumps({"x": None}, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    root = write_root(
        opaque("vis_stamp_null"),
        {
            "sources": [
                {"id": "parent", "priority": 1},
                {"id": "child", "priority": 2},
            ]
        },
        {
            "parent": {"x": 1},
            "child": {"$extends": ["parent"], "x": None},
        },
    )
    assert_success_bytes(root, expected, opaque("vis_stamp_null.json"))


@witness("layers", "Laying keeps inherited siblings at the second object level")
def w_visible_stamp_nested_inner_keeps_sibling():
    expected = json.dumps(
        {"n": {"inner": {"keep": 1, "new": 2}}},
        indent=2,
        sort_keys=True,
        ensure_ascii=True,
    ) + "\n"
    root = write_root(
        opaque("vis_stamp_inner"),
        {
            "sources": [
                {"id": "parent", "priority": 1},
                {"id": "middle", "priority": 2},
                {"id": "child", "priority": 3},
            ]
        },
        {
            "parent": {"n": {"inner": {"keep": 1}}},
            "middle": {"n": {"inner": {"keep": "$delete"}}},
            "child": {"$extends": ["parent"], "n": {"inner": {"new": 2}}},
        },
    )
    assert_success_bytes(root, expected, opaque("vis_stamp_inner.json"))


@witness("layers", "Own false laid over an inherited value replaces it")
def w_visible_stamp_false_replaces_inherited():
    expected = json.dumps({"x": False}, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    root = write_root(
        opaque("vis_stamp_false"),
        {
            "sources": [
                {"id": "parent", "priority": 1},
                {"id": "child", "priority": 2},
            ]
        },
        {
            "parent": {"x": 1},
            "child": {"$extends": ["parent"], "x": False},
        },
    )
    assert_success_bytes(root, expected, opaque("vis_stamp_false.json"))


@witness("layers", "Laying keeps inherited siblings through five object levels")
def w_visible_stamp_five_levels_keeps_inherited_sibling():
    def chain(leaf: dict) -> dict:
        return {"a": {"b": {"c": {"d": {"e": leaf}}}}}

    expected = json.dumps(
        chain({"keep": 1, "new": 2}),
        indent=2,
        sort_keys=True,
        ensure_ascii=True,
    ) + "\n"
    child_body = chain({"new": 2})
    child_body["$extends"] = ["parent"]
    root = write_root(
        opaque("vis_stamp_five"),
        {
            "sources": [
                {"id": "parent", "priority": 1},
                {"id": "middle", "priority": 2},
                {"id": "child", "priority": 3},
            ]
        },
        {
            "parent": chain({"keep": 1}),
            "middle": chain({"keep": "$delete"}),
            "child": child_body,
        },
    )
    assert_success_bytes(root, expected, opaque("vis_stamp_five.json"))


@witness("inherit", "Multi-parent $extends applies left to right; later parent wins on overlap")
def w_visible_extends_two_parents_left_to_right():
    expected = textwrap.dedent(
        """\
        {
          "a": 1,
          "b": 2,
          "k": "from_q"
        }
        """
    )
    root = write_root(
        opaque("vis_ext_multi"),
        {
            "sources": [
                {"id": "p", "priority": 1},
                {"id": "q", "priority": 1},
                {"id": "child", "priority": 2},
            ]
        },
        {
            "p": {"a": 1, "k": "from_p"},
            "q": {"b": 2, "k": "from_q"},
            "child": {"$extends": ["p", "q"]},
        },
    )
    assert_success_bytes(root, expected, opaque("vis_ext_multi.json"))


@witness("inherit", "Second multi-parent site: $extends order is as written, not sorted by id")
def w_visible_extends_two_parents_second_site():
    expected = textwrap.dedent(
        """\
        {
          "a": 1,
          "b": 2,
          "k": "from_p"
        }
        """
    )
    root = write_root(
        opaque("vis_ext_multi_b"),
        {
            "sources": [
                {"id": "p", "priority": 1},
                {"id": "q", "priority": 1},
                {"id": "child", "priority": 2},
            ]
        },
        {
            "p": {"a": 1, "k": "from_p"},
            "q": {"b": 2, "k": "from_q"},
            "child": {"$extends": ["q", "p"]},
        },
    )
    assert_success_bytes(root, expected, opaque("vis_ext_multi_b.json"))


@witness("inherit", "A parent's marker is spent in its configuration and not passed on")
def w_visible_concrete_of_extended_delete_source():
    expected = textwrap.dedent(
        """\
        {
          "x": 2
        }
        """
    )
    root = write_root(
        opaque("vis_conc"),
        {
            "sources": [
                {"id": "a", "priority": 1},
                {"id": "b", "priority": 2},
                {"id": "q", "priority": 3},
                {"id": "c", "priority": 4},
            ]
        },
        {
            "a": {"x": 1},
            "b": {"$extends": ["a"], "x": "$delete"},
            "q": {"x": 2},
            "c": {"$extends": ["b"]},
        },
    )
    assert_success_bytes(root, expected, opaque("vis_conc.json"))


@witness("inherit", "Each source contributes its layer, not its raw body")
def w_visible_resolve_not_raw_bodies():
    expected = textwrap.dedent(
        """\
        {
          "x": {
            "a": 1,
            "b": 2,
            "c": 3
          }
        }
        """
    )
    root = write_root(
        opaque("vis_resolve"),
        {
            "sources": [
                {"id": "a", "priority": 1},
                {"id": "mid", "priority": 2},
                {"id": "child", "priority": 3},
            ]
        },
        {
            "a": {"x": {"a": 1}},
            "mid": {"x": {"a": 9, "b": 2}},
            "child": {"$extends": ["a"], "x": {"c": 3}},
        },
    )
    assert_success_bytes(root, expected, opaque("vis_resolve.json"))


@witness("inherit", "A parent's nested markers are spent before a later source applies")
def w_visible_nested_concrete_intervening_apply():
    expected = textwrap.dedent(
        """\
        {
          "n": {
            "x": 2
          }
        }
        """
    )
    root = write_root(
        opaque("vis_nested_concrete"),
        {
            "sources": [
                {"id": "a", "priority": 1},
                {"id": "b", "priority": 2},
                {"id": "q", "priority": 3},
                {"id": "c", "priority": 4},
            ]
        },
        {
            "a": {"n": {"x": 1}},
            "b": {"$extends": ["a"], "n": {"x": "$delete"}},
            "q": {"n": {"x": 2}},
            "c": {"$extends": ["b"]},
        },
    )
    assert_success_bytes(root, expected, opaque("vis_nested_concrete.json"))


@witness("inherit", "Closed shared ancestors are not cycles")
def w_visible_shared_ancestor_diamond():
    expected = textwrap.dedent(
        """\
        {
          "x": 1
        }
        """
    )
    root = write_root(
        opaque("vis_diamond"),
        {
            "sources": [
                {"id": "common", "priority": 1},
                {"id": "p", "priority": 2},
                {"id": "q", "priority": 3},
                {"id": "child", "priority": 4},
            ]
        },
        {
            "common": {"x": 1},
            "p": {"$extends": ["common"]},
            "q": {"$extends": ["common"]},
            "child": {"$extends": ["p", "q"]},
        },
    )
    assert_success_bytes(root, expected, opaque("vis_diamond.json"))


@witness("inherit", "A parent shared by two children is not a cycle")
def w_visible_shared_parent_reused():
    expected = textwrap.dedent(
        """\
        {
          "c": 1,
          "p": 1,
          "q": 1
        }
        """
    )
    root = write_root(
        opaque("vis_shared_parent"),
        {
            "sources": [
                {"id": "p", "priority": 1},
                {"id": "q", "priority": 2},
                {"id": "child", "priority": 3},
            ]
        },
        {
            "p": {"p": 1},
            "q": {"$extends": ["p"], "q": 1},
            "child": {"$extends": ["p", "q"], "c": 1},
        },
    )
    assert_success_bytes(root, expected, opaque("vis_shared_parent.json"))


@witness("inherit", "A dense inheritance graph merges correctly")
def w_visible_dense_extends_dag_within_timeout():
    n = 12
    ids = [f"s{i}" for i in range(n)]
    man = {"sources": [{"id": sid, "priority": i} for i, sid in enumerate(ids)]}
    sources: dict[str, dict] = {}
    for i, sid in enumerate(ids):
        if i == 0:
            sources[sid] = {}
        else:
            sources[sid] = {"$extends": ids[:i]}
    root = write_root(opaque("vis_dense_dag"), man, sources)
    assert_success_bytes(root, "{}\n", opaque("vis_dense_dag.json"))


@witness("inherit", "Each parent's configuration is applied before the next parent")
def w_visible_multi_parent_concrete_before_next():
    expected = json.dumps({"x": 2}, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    root = write_root(
        opaque("vis_concrete_mp"),
        {
            "sources": [
                {"id": "q", "priority": 1},
                {"id": "b", "priority": 2},
                {"id": "c", "priority": 3},
            ]
        },
        {
            "q": {"x": 2},
            "b": {"x": "$delete"},
            "c": {"$extends": ["q", "b"]},
        },
    )
    assert_success_bytes(root, expected, opaque("vis_concrete_mp.json"))


@witness("inherit", "Nested parent markers are spent before later parents apply")
def w_visible_multi_parent_concrete_nested():
    expected = json.dumps({"n": {"x": 2}}, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    root = write_root(
        opaque("vis_concrete_mp_n"),
        {
            "sources": [
                {"id": "q", "priority": 1},
                {"id": "b", "priority": 2},
                {"id": "c", "priority": 3},
            ]
        },
        {
            "q": {"n": {"x": 2}},
            "b": {"n": {"x": "$delete"}},
            "c": {"$extends": ["q", "b"]},
        },
    )
    assert_success_bytes(root, expected, opaque("vis_concrete_mp_n.json"))


@witness("inherit", "$extends may name a listed id that applies later than the child")
def w_visible_extends_later_application_order_parent():
    expected = "{}\n"
    root = write_root(
        opaque("vis_ext_later"),
        {
            "sources": [
                {"id": "child", "priority": 0},
                {"id": "parent", "priority": 1},
            ]
        },
        {
            "child": {"$extends": ["parent"]},
            "parent": {},
        },
    )
    assert_success_bytes(root, expected, opaque("vis_ext_later.json"))


@witness("inherit", "Second later-parent site: child inherits data from a higher-priority parent")
def w_visible_extends_later_parent_with_payload():
    expected = json.dumps(
        {"from": "parent", "local": 1},
        indent=2,
        sort_keys=True,
        ensure_ascii=True,
    ) + "\n"
    root = write_root(
        opaque("vis_ext_later2"),
        {
            "sources": [
                {"id": "child", "priority": 0},
                {"id": "parent", "priority": 1},
            ]
        },
        {
            "child": {"$extends": ["parent"], "local": 1},
            "parent": {"from": "parent"},
        },
    )
    assert_success_bytes(root, expected, opaque("vis_ext_later2.json"))


@witness("output", "Object keys must be bytewise sorted at every depth")
def w_visible_output_keys_sorted():
    expected = textwrap.dedent(
        """\
        {
          "a": 1,
          "m": {
            "p": 1,
            "z": 2
          },
          "z": 0
        }
        """
    )
    root = write_root(
        opaque("vis_sort_a"),
        {"sources": [{"id": "only", "priority": 1}]},
        {"only": {"z": 0, "a": 1, "m": {"z": 2, "p": 1}}},
    )
    assert_success_bytes(root, expected, opaque("vis_sort_a.json"))


@witness("output", "Second sort site after a multi-source merge")
def w_visible_output_keys_sorted_second_site():
    expected = textwrap.dedent(
        """\
        {
          "alpha": 1,
          "beta": 2
        }
        """
    )
    root = write_root(
        opaque("vis_sort_b"),
        {
            "sources": [
                {"id": "a", "priority": 1},
                {"id": "b", "priority": 2},
            ]
        },
        {"a": {"beta": 0, "alpha": 1}, "b": {"beta": 2}},
    )
    assert_success_bytes(root, expected, opaque("vis_sort_b.json"))


@witness("output", "Objects nested inside arrays must also have sorted keys")
def w_visible_keys_sorted_inside_array_objects():
    expected = textwrap.dedent(
        """\
        {
          "items": [
            {
              "a": 1,
              "z": 0
            }
          ]
        }
        """
    )
    root = write_root(
        opaque("vis_sort_arr"),
        {"sources": [{"id": "only", "priority": 1}]},
        {"only": {"items": [{"z": 0, "a": 1}]}},
    )
    assert_success_bytes(root, expected, opaque("vis_sort_arr.json"))


@witness("output", "Merger must create parent directories for --out")
def w_visible_creates_missing_out_parent():
    expected = "{}\n"
    root = write_root(
        opaque("vis_mkdir_root"),
        {"sources": [{"id": "only", "priority": 1}]},
        {"only": {}},
    )
    out = opaque("nested_out_dir") / "result.json"
    if out.parent.exists():
        shutil.rmtree(out.parent)
    assert_success_bytes(root, expected, out)


@witness("output", "Non-$ top-level data keys need not look like ids")
def w_visible_empty_string_data_key():
    expected = textwrap.dedent(
        """\
        {
          "": 1
        }
        """
    )
    root = write_root(
        opaque("vis_empty_key"),
        {"sources": [{"id": "only", "priority": 1}]},
        {"only": {"": 1}},
    )
    assert_success_bytes(root, expected, opaque("vis_empty_key.json"))


@witness("output", "Finite JSON float 1.5 is accepted and serialized per contract dumps")
def w_visible_finite_float_literal():
    expected = json.dumps({"x": 1.5}, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    dest = opaque("vis_float")
    if dest.exists():
        shutil.rmtree(dest)
    (dest / "sources").mkdir(parents=True)
    (dest / "manifest.json").write_text(
        json.dumps({"sources": [{"id": "only", "priority": 1}]}, indent=2) + "\n"
    )
    (dest / "sources" / "only.json").write_text('{"x":1.5}\n')
    _chown_tree(dest, SANDBOX_UID, SANDBOX_GID)
    assert_success_bytes(dest, expected, opaque("vis_float.json"))


@witness("output", "Non-ASCII data keys serialize with ensure_ascii=True escapes")
def w_visible_unicode_key_ensure_ascii():
    expected = json.dumps({"é": 1}, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    root = write_root(
        opaque("vis_unicode_key"),
        {"sources": [{"id": "only", "priority": 1}]},
        {"only": {"é": 1}},
    )
    assert_success_bytes(root, expected, opaque("vis_unicode_key.json"))


@witness("output", "Nonempty data keys may contain punctuation")
def w_visible_punctuated_data_key():
    expected = textwrap.dedent(
        """\
        {
          "a-b": 1
        }
        """
    )
    root = write_root(
        opaque("vis_key_hyphen"),
        {"sources": [{"id": "only", "priority": 1}]},
        {"only": {"a-b": 1}},
    )
    assert_success_bytes(root, expected, opaque("vis_key_hyphen.json"))


@witness("output", "Non-$ top-level data keys may contain whitespace")
def w_visible_whitespace_data_key():
    expected = json.dumps({"a b": 1}, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    root = write_root(
        opaque("vis_key_ws"),
        {"sources": [{"id": "only", "priority": 1}]},
        {"only": {"a b": 1}},
    )
    assert_success_bytes(root, expected, opaque("vis_key_ws.json"))


@witness("output", "json.dumps keeps integer-valued floats as 1.0, not 1")
def w_visible_integral_float_literal_dumps():
    expected = json.dumps({"x": 1.0}, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    assert '"1.0"' in expected or "1.0" in expected
    dest = opaque("vis_float10")
    if dest.exists():
        shutil.rmtree(dest)
    (dest / "sources").mkdir(parents=True)
    (dest / "manifest.json").write_text(
        json.dumps({"sources": [{"id": "a", "priority": 0}]}, indent=2) + "\n"
    )
    (dest / "sources" / "a.json").write_text('{"x":1.0}\n')
    _chown_tree(dest, SANDBOX_UID, SANDBOX_GID)
    assert_success_bytes(dest, expected, opaque("vis_float10.json"))


@witness("output", "Negative zero retains Python json.dumps spelling")
def w_visible_negative_zero_float_dumps():
    expected = json.dumps({"x": -0.0}, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    dest = opaque("vis_neg0")
    if dest.exists():
        shutil.rmtree(dest)
    (dest / "sources").mkdir(parents=True)
    (dest / "manifest.json").write_text(
        json.dumps({"sources": [{"id": "only", "priority": 1}]}, indent=2) + "\n"
    )
    (dest / "sources" / "only.json").write_text('{"x":-0.0}\n')
    _chown_tree(dest, SANDBOX_UID, SANDBOX_GID)
    assert_success_bytes(dest, expected, opaque("vis_neg0.json"))


@witness("output", "Large finite float retains Python json.dumps spelling")
def w_visible_large_finite_float_dumps():
    expected = json.dumps({"x": 1e20}, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    dest = opaque("vis_1e20")
    if dest.exists():
        shutil.rmtree(dest)
    (dest / "sources").mkdir(parents=True)
    (dest / "manifest.json").write_text(
        json.dumps({"sources": [{"id": "only", "priority": 1}]}, indent=2) + "\n"
    )
    (dest / "sources" / "only.json").write_text('{"x":1e20}\n')
    _chown_tree(dest, SANDBOX_UID, SANDBOX_GID)
    assert_success_bytes(dest, expected, opaque("vis_1e20.json"))


@witness("bounds", "Five distinct sources stay inside the graded domain")
def w_visible_five_sources_within_domain():
    expected = textwrap.dedent(
        """\
        {
          "a": 1,
          "b": 2,
          "c": 3,
          "d": 4,
          "e": 5
        }
        """
    )
    root = write_root(
        opaque("vis_five"),
        {
            "sources": [
                {"id": "a", "priority": 1},
                {"id": "b", "priority": 2},
                {"id": "c", "priority": 3},
                {"id": "d", "priority": 4},
                {"id": "e", "priority": 5},
            ]
        },
        {
            "a": {"a": 1},
            "b": {"b": 2},
            "c": {"c": 3},
            "d": {"d": 4},
            "e": {"e": 5},
        },
    )
    assert_success_bytes(root, expected, opaque("vis_five.json"))


@witness("bounds", "A source larger than 1KiB but under 70000 bytes must succeed")
def w_visible_padded_source_within_size_domain():
    body = "{}" + (" " * 1023)
    assert len(body.encode()) == 1025
    dest = opaque("vis_pad")
    if dest.exists():
        shutil.rmtree(dest)
    (dest / "sources").mkdir(parents=True)
    (dest / "manifest.json").write_text(
        json.dumps({"sources": [{"id": "only", "priority": 1}]}, indent=2) + "\n"
    )
    (dest / "sources" / "only.json").write_text(body)
    _chown_tree(dest, SANDBOX_UID, SANDBOX_GID)
    assert_success_bytes(dest, "{}\n", opaque("vis_pad.json"))


@witness("bounds", "Nine sources stay inside the ≤64 domain")
def w_visible_nine_sources_within_domain():
    ids = [f"s{i}" for i in range(9)]
    sources = {sid: {} for sid in ids}
    man = {"sources": [{"id": sid, "priority": i} for i, sid in enumerate(ids)]}
    root = write_root(opaque("vis_nine"), man, sources)
    assert_success_bytes(root, "{}\n", opaque("vis_nine.json"))


@witness("bounds", "A >4KiB source under 70000 bytes must succeed")
def w_visible_padded_source_over_4kib():
    body = "{}" + (" " * 4095)
    assert len(body.encode()) == 4097
    dest = opaque("vis_pad4k")
    if dest.exists():
        shutil.rmtree(dest)
    (dest / "sources").mkdir(parents=True)
    (dest / "manifest.json").write_text(
        json.dumps({"sources": [{"id": "only", "priority": 1}]}, indent=2) + "\n"
    )
    (dest / "sources" / "only.json").write_text(body)
    _chown_tree(dest, SANDBOX_UID, SANDBOX_GID)
    assert_success_bytes(dest, "{}\n", opaque("vis_pad4k.json"))


@witness("bounds", "Seventeen sources stay inside the ≤64 domain")
def w_visible_seventeen_sources_within_domain():
    ids = [f"s{i}" for i in range(17)]
    man = {"sources": [{"id": sid, "priority": i} for i, sid in enumerate(ids)]}
    sources = {sid: {sid: 1} for sid in ids}
    expected_obj = {sid: 1 for sid in ids}
    expected = json.dumps(expected_obj, indent=2, sort_keys=True) + "\n"
    root = write_root(opaque("vis_seventeen"), man, sources)
    assert_success_bytes(root, expected, opaque("vis_seventeen.json"))


@witness("bounds", "Thirty-three sources distinguish a mistaken 32-source cap")
def w_visible_thirty_three_sources_within_domain():
    ids = [f"s{i}" for i in range(33)]
    man = {"sources": [{"id": sid, "priority": i} for i, sid in enumerate(ids)]}
    sources = {sid: {sid: 1} for sid in ids}
    expected = json.dumps({sid: 1 for sid in ids}, indent=2, sort_keys=True) + "\n"
    root = write_root(opaque("vis_thirty_three"), man, sources)
    assert_success_bytes(root, expected, opaque("vis_thirty_three.json"))


@witness("bounds", "Payload above an 8KiB read cap but under 70000 must succeed")
def w_visible_large_string_payload():
    payload = "x" * 8200
    body = json.dumps({"payload": payload}, indent=2, sort_keys=True) + "\n"
    assert len(body.encode()) > 8192
    dest = opaque("vis_payload8k")
    if dest.exists():
        shutil.rmtree(dest)
    (dest / "sources").mkdir(parents=True)
    (dest / "manifest.json").write_text(
        json.dumps({"sources": [{"id": "only", "priority": 1}]}, indent=2) + "\n"
    )
    (dest / "sources" / "only.json").write_text(body)
    _chown_tree(dest, SANDBOX_UID, SANDBOX_GID)
    assert_success_bytes(dest, body, opaque("vis_payload8k.json"))


@witness("bounds", "Payload above a 16KiB read cap stays under 70000")
def w_visible_payload_over_16kib():
    payload = "x" * 16385
    body = json.dumps({"payload": payload}, indent=2, sort_keys=True) + "\n"
    assert len(body.encode()) > 16384
    assert len(body.encode()) < 70_000
    dest = opaque("vis_payload16k")
    if dest.exists():
        shutil.rmtree(dest)
    (dest / "sources").mkdir(parents=True)
    (dest / "manifest.json").write_text(
        json.dumps({"sources": [{"id": "only", "priority": 1}]}, indent=2) + "\n"
    )
    (dest / "sources" / "only.json").write_text(body)
    _chown_tree(dest, SANDBOX_UID, SANDBOX_GID)
    assert_success_bytes(dest, body, opaque("vis_payload16k.json"))


@witness("bounds", "Six nested objects are the inclusive depth bound")
def w_visible_nesting_depth_six():
    inner: dict = {"value": 1}
    for _ in range(5):
        inner = {"n": inner}
    expected = json.dumps(inner, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    root = write_root(
        opaque("vis_depth6"),
        {"sources": [{"id": "only", "priority": 1}]},
        {"only": inner},
    )
    assert_success_bytes(root, expected, opaque("vis_depth6.json"))


@witness("bounds", "Four nested objects stay inside the depth-6 domain")
def w_visible_nesting_depth_four():
    inner: dict = {"value": 1}
    for _ in range(3):
        inner = {"n": inner}
    expected = json.dumps(inner, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    root = write_root(
        opaque("vis_depth4"),
        {"sources": [{"id": "only", "priority": 1}]},
        {"only": inner},
    )
    assert_success_bytes(root, expected, opaque("vis_depth4.json"))


@witness("bounds", "Exactly 64 sources; last body nonempty so a 63-source truncate fails")
def w_visible_sixty_four_sources_within_domain():
    ids = [f"s{i}" for i in range(64)]
    man = {"sources": [{"id": sid, "priority": i} for i, sid in enumerate(ids)]}
    sources = {sid: {} for sid in ids}
    sources[ids[-1]] = {"last": 1}
    expected = json.dumps({"last": 1}, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    root = write_root(opaque("vis_sixty_four"), man, sources)
    assert_success_bytes(root, expected, opaque("vis_sixty_four.json"))


@witness("bounds", "Fifty-one nonempty sources distinguish a 50-source cap")
def w_visible_fifty_one_sources_with_keys():
    ids = [f"s{i}" for i in range(51)]
    man = {"sources": [{"id": sid, "priority": i} for i, sid in enumerate(ids)]}
    sources = {sid: {sid: 1} for sid in ids}
    expected = json.dumps({sid: 1 for sid in ids}, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    root = write_root(opaque("vis_fifty_one"), man, sources)
    assert_success_bytes(root, expected, opaque("vis_fifty_one.json"))


@witness("bounds", "Source just over 64KiB stays under the 70_000-byte graded bound")
def w_visible_payload_over_64kib():
    n = 65000
    while True:
        body = '{"payload":"' + ("x" * n) + '"}\n'
        if len(body.encode()) > 65536:
            break
        n += 200
    assert len(body.encode()) < 70_000
    dest = opaque("vis_payload64k")
    if dest.exists():
        shutil.rmtree(dest)
    (dest / "sources").mkdir(parents=True)
    (dest / "manifest.json").write_text(
        json.dumps({"sources": [{"id": "only", "priority": 1}]}, indent=2) + "\n"
    )
    (dest / "sources" / "only.json").write_text(body)
    _chown_tree(dest, SANDBOX_UID, SANDBOX_GID)
    expected = json.dumps({"payload": "x" * n}, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    assert_success_bytes(dest, expected, opaque("vis_payload64k.json"))


@witness("bounds", "Five nested objects stay inside the depth-6 domain (second bound site)")
def w_visible_nesting_depth_five():
    inner: dict = {"value": 1}
    for _ in range(4):
        inner = {"n": inner}
    expected = json.dumps(inner, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    root = write_root(
        opaque("vis_depth5"),
        {"sources": [{"id": "only", "priority": 1}]},
        {"only": inner},
    )
    assert_success_bytes(root, expected, opaque("vis_depth5.json"))


@witness("bounds", "Four nested arrays under an object stay inside the depth-6 bound")
def w_visible_deep_array_nesting():
    inner: object = 1
    for _ in range(4):
        inner = [inner]
    body = {"x": inner}
    expected = json.dumps(body, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    root = write_root(
        opaque("vis_arr4"),
        {"sources": [{"id": "only", "priority": 1}]},
        {"only": body},
    )
    assert_success_bytes(root, expected, opaque("vis_arr4.json"))


@witness("bounds", "Inclusive 70_000-byte source file bound")
def w_visible_source_exactly_70000_bytes():
    prefix = b'{"payload":"'
    suffix = b'"}\n'
    n = 70_000 - len(prefix) - len(suffix)
    body = prefix + (b"x" * n) + suffix
    assert len(body) == 70_000
    dest = opaque("vis_exact_70k")
    if dest.exists():
        shutil.rmtree(dest)
    (dest / "sources").mkdir(parents=True)
    (dest / "manifest.json").write_text(
        json.dumps({"sources": [{"id": "only", "priority": 1}]}, indent=2) + "\n"
    )
    (dest / "sources" / "only.json").write_bytes(body)
    _chown_tree(dest, SANDBOX_UID, SANDBOX_GID)
    expected = json.dumps({"payload": "x" * n}, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    assert_success_bytes(dest, expected, opaque("vis_exact_70k.json"))


@witness("bounds", "Object + 5 nested arrays = depth 6 inclusive bound")
def w_visible_array_depth_bound():
    inner: object = 1
    for _ in range(5):
        inner = [inner]
    body = {"x": inner}
    expected = json.dumps(body, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    root = write_root(
        opaque("vis_arr6"),
        {"sources": [{"id": "only", "priority": 1}]},
        {"only": body},
    )
    assert_success_bytes(root, expected, opaque("vis_arr6.json"))


@witness("run", "Refusal must not destroy a pre-existing --out file")
def w_refuse_preserves_preexisting_out():
    def build(dest: Path) -> Path:
        return write_root(
            dest,
            {"sources": [{"id": "only", "priority": 1}]},
            {"only": {"$extends": ["missing"]}},
        )

    for suffix in ("p", "q"):
        name = f"wx_81_{suffix}"
        root = build(opaque(name))
        out = opaque(f"{name}.out")
        assert_refuse(
            root,
            "refuse: bad source",
            out,
            preexisting=b"sentinel\n",
        )


@witness("run", "Bad-manifest refusals also leave a pre-existing --out untouched")
def w_refuse_preserves_preexisting_out_on_bad_manifest():
    def build(dest: Path) -> Path:
        if dest.exists():
            shutil.rmtree(dest)
        return dest

    for suffix in ("p", "q"):
        name = f"wx_93_{suffix}"
        root = build(opaque(name))
        out = opaque(f"{name}.out")
        assert_refuse(
            root,
            "refuse: bad manifest",
            out,
            preexisting=b"sentinel\n",
        )


@witness("run", "The graded program is the regular file at the documented path")
def w_merger_source_present():
    assert (APP / "merge.py").is_file()


@witness("run", "The documented path runs as an unprivileged user and writes the configuration")
def w_drive_path_app_merge_py():
    root = one_source(opaque("vis_app_path"), {"ok": 1})
    out = opaque("vis_app_path.out")
    proc = run_merge(root, out, program=APP / "merge.py")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == b""
    assert out.read_bytes() == b'{\n  "ok": 1\n}\n'


@witness("ids", "A non-ASCII lowercase first character is not an ASCII letter a to z")
def w_refuse_id_non_ascii_initial():
    def build(dest: Path) -> Path:
        return write_bytes_root(
            dest,
            b'{"sources":[{"id":"\\u00e9","priority":0}]}\n',
            {"\u00e9.json": b"{}\n"},
        )

    refuse_pair(build, "refuse: bad manifest", "n1a")


@witness("ids", "A non-ASCII first character followed by an ASCII tail is still refused")
def w_refuse_id_non_ascii_initial_with_tail():
    def build(dest: Path) -> Path:
        return write_bytes_root(
            dest,
            b'{"sources":[{"id":"\\u00dfab","priority":0}]}\n',
            {"\u00dfab.json": b"{}\n"},
        )

    refuse_pair(build, "refuse: bad manifest", "n1b")


@witness("manifest_doc", "A manifest object that names sources twice is refused")
def w_refuse_manifest_duplicate_member():
    def build(dest: Path) -> Path:
        return write_bytes_root(
            dest,
            b'{"sources":[{"id":"a","priority":0}],"sources":[{"id":"a","priority":0}]}\n',
            {"a.json": b"{}\n"},
        )

    refuse_pair(build, "refuse: bad manifest", "n2a")


@witness("source_doc", "A complete object followed by a number is not one JSON document")
def w_refuse_source_trailing_number():
    def build(dest: Path) -> Path:
        return write_bytes_root(
            dest,
            b'{"sources":[{"id":"a","priority":0}]}\n',
            {"a.json": b'{"x":1}0\n'},
        )

    refuse_pair(build, "refuse: bad source", "n3a")


@witness("source_doc", "A second object after the first is not one JSON document")
def w_refuse_source_trailing_object():
    def build(dest: Path) -> Path:
        return write_bytes_root(
            dest,
            b'{"sources":[{"id":"a","priority":0}]}\n',
            {"a.json": b'{"x":1}\n{}\n'},
        )

    refuse_pair(build, "refuse: bad source", "n3b")


@witness("source_doc", "A source object that names a member twice is refused")
def w_refuse_source_duplicate_member():
    def build(dest: Path) -> Path:
        return write_bytes_root(
            dest,
            b'{"sources":[{"id":"a","priority":0}]}\n',
            {"a.json": b'{"x":1,"x":2}\n'},
        )

    refuse_pair(build, "refuse: bad source", "n3c")


@witness("apply", "An empty array replaces a non-empty one whole")
def w_visible_apply_empty_array_replaces():
    root = write_root(
        opaque("vis_empty_arr"),
        {"sources": [{"id": "base", "priority": 1}, {"id": "over", "priority": 2}]},
        {"base": {"x": [1], "y": [2, 3]}, "over": {"x": []}},
    )
    assert_success_bytes(root, '{\n  "x": [],\n  "y": [\n    2,\n    3\n  ]\n}\n', opaque("vis_empty_arr.json"))


@witness("layers", "An own empty array laid over an inherited array replaces it")
def w_visible_lay_empty_array_replaces():
    root = write_root(
        opaque("vis_lay_empty_arr"),
        {"sources": [{"id": "parent", "priority": 1}, {"id": "child", "priority": 2}]},
        {"parent": {"x": [1, 2]}, "child": {"$extends": ["parent"], "x": []}},
    )
    assert_success_bytes(root, '{\n  "x": []\n}\n', opaque("vis_lay_empty_arr.json"))


@witness("inherit", "A layer is built from {} and its parents only, not from the merge so far")
def w_visible_layer_ignores_merge_so_far():
    root = write_root(
        opaque("vis_layer_fresh"),
        {
            "sources": [
                {"id": "a", "priority": 1},
                {"id": "b", "priority": 2},
                {"id": "d", "priority": 3},
                {"id": "c", "priority": 4},
            ]
        },
        {"a": {"x": 1}, "b": {}, "d": {"x": "$delete"}, "c": {"$extends": ["b"]}},
    )
    assert_success_bytes(root, "{}\n", opaque("vis_layer_fresh.json"))


@witness("inherit", "A parent's configuration includes what it inherits from its own parents")
def w_visible_transitive_inheritance_restores():
    root = write_root(
        opaque("vis_transitive"),
        {
            "sources": [
                {"id": "a", "priority": 1},
                {"id": "b", "priority": 2},
                {"id": "mid", "priority": 3},
                {"id": "c", "priority": 4},
            ]
        },
        {"a": {"x": 1}, "b": {"$extends": ["a"]}, "mid": {"x": 9}, "c": {"$extends": ["b"]}},
    )
    assert_success_bytes(root, '{\n  "x": 1\n}\n', opaque("vis_transitive.json"))


@witness("inherit", "Transitive inheritance carries nested objects through two parents")
def w_visible_transitive_inheritance_nested():
    root = write_root(
        opaque("vis_transitive_nested"),
        {
            "sources": [
                {"id": "g", "priority": 1},
                {"id": "p", "priority": 2},
                {"id": "mid", "priority": 3},
                {"id": "c", "priority": 4},
            ]
        },
        {
            "g": {"db": {"host": "g", "port": 1}},
            "p": {"$extends": ["g"], "db": {"port": 2}},
            "mid": {"db": {"host": "mid", "port": 3}},
            "c": {"$extends": ["p"]},
        },
    )
    assert_success_bytes(root, '{\n  "db": {\n    "host": "g",\n    "port": 2\n  }\n}\n', opaque("vis_transitive_nested.json"))


@witness("output", "A small float is written as decoded, not rounded")
def w_visible_small_float_not_rounded():
    root = write_bytes_root(
        opaque("vis_small_float"),
        b'{"sources":[{"id":"only","priority":1}]}\n',
        {"only.json": b'{"x":0.0000001,"y":0.1234567,"z":123456.7890123}\n'},
    )
    assert_success_bytes(
        root,
        '{\n  "x": 1e-07,\n  "y": 0.1234567,\n  "z": 123456.7890123\n}\n',
        opaque("vis_small_float.json"),
    )


@witness("bounds", "The size bound is per file: two sources together may exceed 70,000 bytes")
def w_visible_aggregate_over_70000_bytes():
    a_body = b'{"a":"' + b"x" * 34991 + b'"}\n'
    b_body = b'{"b":"' + b"y" * 34992 + b'"}\n'
    assert len(a_body) == 35000 and len(b_body) == 35001
    root = write_bytes_root(
        opaque("vis_aggregate"),
        b'{"sources":[{"id":"a","priority":1},{"id":"b","priority":2}]}\n',
        {"a.json": a_body, "b.json": b_body},
    )
    assert_success_bytes(root, dumps({"a": "x" * 34991, "b": "y" * 34992}), opaque("vis_aggregate.json"))


@witness("bounds", "Three sources near the per-file bound merge together")
def w_visible_three_large_sources():
    bodies = {}
    for sid in ("a", "b", "c"):
        bodies[f"{sid}.json"] = b'{"' + sid.encode() + b'":"' + b"z" * 69000 + b'"}\n'
    root = write_bytes_root(
        opaque("vis_three_large"),
        b'{"sources":[{"id":"c","priority":3},{"id":"a","priority":1},{"id":"b","priority":2}]}\n',
        bodies,
    )
    assert_success_bytes(root, dumps({"a": "z" * 69000, "b": "z" * 69000, "c": "z" * 69000}), opaque("vis_three_large.json"))


@witness("entries", "An array or object priority is not a JSON integer and is refused, not crashed on")
def w_refuse_priority_array_or_object():
    for tag, value in (("g1a", b"[]"), ("g1b", b"{}"), ("g1c", b"[0]"), ("g1d", b'{"n":1}')):
        def build(dest: Path, value: bytes = value) -> Path:
            return write_bytes_root(
                dest,
                b'{"sources":[{"id":"a","priority":' + value + b'}]}\n',
                {"a.json": b"{}\n"},
            )

        refuse_pair(build, "refuse: bad manifest", tag)


@witness("members", "A member whose name starts with a dot is still a member and is refused")
def w_refuse_dot_prefixed_members():
    def regular(dest: Path) -> Path:
        root = write_bytes_root(dest, ONE_A, {"a.json": b"{}\n", ".notes": b"x\n"})
        return root

    def dot_json(dest: Path) -> Path:
        return write_bytes_root(dest, ONE_A, {"a.json": b"{}\n", ".json": b"{}\n"})

    def dot_dir(dest: Path) -> Path:
        root = write_bytes_root(dest, ONE_A, {"a.json": b"{}\n"})
        (root / "sources" / ".cache").mkdir()
        _chown_tree(root, SANDBOX_UID, SANDBOX_GID)
        return root

    def dot_link(dest: Path) -> Path:
        root = write_bytes_root(dest, ONE_A, {"a.json": b"{}\n"})
        (root / "sources" / ".a.json").symlink_to("a.json")
        _chown_tree(root, SANDBOX_UID, SANDBOX_GID)
        return root

    refuse_pair(regular, "refuse: bad manifest", "g2a")
    refuse_pair(dot_json, "refuse: bad manifest", "g2b")
    refuse_pair(dot_dir, "refuse: bad manifest", "g2c")
    refuse_pair(dot_link, "refuse: bad manifest", "g2d")


TWO_AB = b'{"sources":[{"id":"a","priority":0},{"id":"b","priority":1}]}\n'
THREE_ABC = b'{"sources":[{"id":"c","priority":2},{"id":"a","priority":0},{"id":"b","priority":1}]}\n'


@witness("source_doc", "Non-JSON constants are refused in every source, not only the first one applied")
def w_refuse_constants_in_later_sources():
    cases = [
        ("g3a", TWO_AB, {"a.json": b"{}\n", "b.json": b'{"x":NaN}\n'}),
        ("g3b", TWO_AB, {"a.json": b'{"k":1}\n', "b.json": b'{"n":{"x":-Infinity}}\n'}),
        ("g3c", THREE_ABC, {"a.json": b"{}\n", "b.json": b"{}\n", "c.json": b'{"x":[Infinity]}\n'}),
    ]
    for tag, manifest, files in cases:
        refuse_pair(lambda dest, m=manifest, f=files: write_bytes_root(dest, m, f), "refuse: bad source", tag)


@witness("source_doc", "Repeated member names are refused in every source, not only the first one applied")
def w_refuse_duplicates_in_later_sources():
    cases = [
        ("g4a", TWO_AB, {"a.json": b"{}\n", "b.json": b'{"x":1,"x":2}\n'}),
        ("g4b", TWO_AB, {"a.json": b'{"k":1}\n', "b.json": b'{"n":{"y":1,"y":1}}\n'}),
        ("g4c", THREE_ABC, {"a.json": b"{}\n", "b.json": b"{}\n", "c.json": b'{"v":[{"z":0,"z":0}]}\n'}),
    ]
    for tag, manifest, files in cases:
        refuse_pair(lambda dest, m=manifest, f=files: write_bytes_root(dest, m, f), "refuse: bad source", tag)


def _preexisting(tag: str, build, msg: str) -> None:
    root = build(opaque(f"wx_{tag}"))
    assert_refuse(root, msg, opaque(f"wx_{tag}_dir") / "out.json", preexisting=b"prior bytes\n")


def _manifest_root(manifest: bytes, files: dict[str, bytes]):
    return lambda dest: write_bytes_root(dest, manifest, files)


ONE_A = b'{"sources":[{"id":"a","priority":0}]}\n'


def _bind_socket(path: Path) -> None:
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        srv.bind(str(path))
    finally:
        srv.close()


def _socket_root(dest: Path, at: str) -> Path:
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    if at == "manifest.json":
        (dest / "sources").mkdir()
    else:
        (dest / "manifest.json").write_text('{"sources":[]}\n')
    _bind_socket(dest / at)
    _chown_tree(dest, SANDBOX_UID, SANDBOX_GID)
    return dest


@witness("layout", "A socket at manifest.json is not a regular file")
def w_refuse_manifest_is_socket():
    refuse_pair(lambda dest: _socket_root(dest, "manifest.json"), "refuse: bad manifest", "r2m1")


@witness("layout", "A socket at sources is not a directory")
def w_refuse_sources_is_socket():
    refuse_pair(lambda dest: _socket_root(dest, "sources"), "refuse: bad manifest", "r2m2")


@witness("manifest_doc", "Whitespace before and after the manifest value is allowed")
def w_visible_manifest_leading_whitespace():
    root = write_bytes_root(opaque("r2_man_ws"), b' \t\r\n{"sources":[]}\n\t ', {})
    assert_success_bytes(root, "{}\n", opaque("r2_man_ws.out"))


@witness("order", "A manifest with leading whitespace still orders its sources")
def w_visible_manifest_leading_whitespace_ordered():
    root = write_bytes_root(
        opaque("r2_man_ws_two"),
        b'\n\n  {"sources":[{"id":"b","priority":1},{"id":"a","priority":1}]}',
        {"a.json": b'{"v":"a"}\n', "b.json": b'{"v":"b"}\n'},
    )
    assert_success_bytes(root, '{\n  "v": "b"\n}\n', opaque("r2_man_ws_two.out"))


@witness("manifest_doc", "An entry that names priority twice is refused")
def w_refuse_entry_duplicate_priority():
    refuse_pair(
        _manifest_root(b'{"sources":[{"id":"a","priority":0,"priority":1}]}\n', {"a.json": b"{}\n"}),
        "refuse: bad manifest",
        "r2d1",
    )


@witness("entries", "An entry that names id twice is refused")
def w_refuse_entry_duplicate_id_member():
    refuse_pair(
        _manifest_root(b'{"sources":[{"id":"a","id":"b","priority":0}]}\n', {"a.json": b"{}\n", "b.json": b"{}\n"}),
        "refuse: bad manifest",
        "r2d2",
    )


@witness("entries", "A priority written with an exponent is not an integer")
def w_refuse_priority_exponent():
    refuse_pair(
        _manifest_root(b'{"sources":[{"id":"a","priority":1e2}]}\n', {"a.json": b"{}\n"}),
        "refuse: bad manifest",
        "r2p1",
    )


@witness("entries", "A priority of 0e0 is not an integer")
def w_refuse_priority_zero_exponent():
    refuse_pair(
        _manifest_root(b'{"sources":[{"id":"a","priority":0e0}]}\n', {"a.json": b"{}\n"}),
        "refuse: bad manifest",
        "r2p2",
    )


@witness("entries", "A priority of false is not an integer")
def w_refuse_priority_false():
    refuse_pair(
        _manifest_root(b'{"sources":[{"id":"a","priority":false}]}\n', {"a.json": b"{}\n"}),
        "refuse: bad manifest",
        "r2p3",
    )


@witness("source_doc", "Whitespace before a source's value is allowed")
def w_visible_source_leading_whitespace():
    root = write_bytes_root(opaque("r2_src_ws"), ONE_A, {"a.json": b' \t\r\n{"x":1}\n'})
    assert_success_bytes(root, '{\n  "x": 1\n}\n', opaque("r2_src_ws.out"))


@witness("output", "A source with leading whitespace merges like any other")
def w_visible_source_leading_whitespace_merged():
    root = write_bytes_root(
        opaque("r2_src_ws_two"),
        b'{"sources":[{"id":"a","priority":0},{"id":"b","priority":1}]}\n',
        {"a.json": b'{"x":1,"y":2}\n', "b.json": b'\r\n\t {"y":2.5}'},
    )
    assert_success_bytes(root, '{\n  "x": 1,\n  "y": 2.5\n}\n', opaque("r2_src_ws_two.out"))


@witness("source_doc", "A nested object that names a member twice is refused")
def w_refuse_source_nested_duplicate_member():
    refuse_pair(_manifest_root(ONE_A, {"a.json": b'{"n":{"x":1,"x":2}}\n'}), "refuse: bad source", "r2s1")


@witness("source_doc", "An object inside an array that names a member twice is refused")
def w_refuse_source_array_object_duplicate_member():
    refuse_pair(_manifest_root(ONE_A, {"a.json": b'{"l":[{"x":1,"x":2}]}\n'}), "refuse: bad source", "r2s2")


@witness("source_doc", "A source file of exactly two bytes is inside the bounds")
def w_visible_two_byte_source():
    root = write_bytes_root(opaque("r2_two_byte"), ONE_A, {"a.json": b"{}"})
    assert_success_bytes(root, "{}\n", opaque("r2_two_byte.out"))


@witness("bounds", "A two-byte source merges with another source")
def w_visible_two_byte_source_merged():
    root = write_bytes_root(
        opaque("r2_two_byte_two"),
        b'{"sources":[{"id":"a","priority":0},{"id":"b","priority":1}]}\n',
        {"a.json": b'{"k":1}', "b.json": b"{}"},
    )
    assert_success_bytes(root, '{\n  "k": 1\n}\n', opaque("r2_two_byte_two.out"))


BIG_INTS = {
    "a": 9007199254740993,
    "b": 9223372036854775808,
    "c": -9223372036854775809,
    "d": int("9" * 300),
    "e": -int("1" + "0" * 299),
}


@witness("output", "Integers keep every digit, beyond binary64 and 64-bit ranges")
def w_visible_large_integers_exact():
    body = b'{"a":9007199254740993,"b":9223372036854775808,"c":-9223372036854775809,"d":' + b"9" * 300 + b',"e":-1' + b"0" * 299 + b"}\n"
    root = write_bytes_root(opaque("r2_big_ints"), ONE_A, {"a.json": body})
    assert_success_bytes(root, dumps(BIG_INTS), opaque("r2_big_ints.out"))


@witness("output", "String values keep their surrounding whitespace")
def w_visible_string_whitespace_kept():
    root = write_bytes_root(
        opaque("r2_str_ws"),
        ONE_A,
        {"a.json": b'{"x":" a ","y":["\\tb\\n"," "],"z":{"w":"c "}}\n'},
    )
    assert_success_bytes(root, dumps({"x": " a ", "y": ["\tb\n", " "], "z": {"w": "c "}}), opaque("r2_str_ws.out"))


@witness("metadata", "$extends below the top level is ordinary data")
def w_visible_nested_extends_is_data():
    body = {"n": {"$extends": ["unlisted"]}, "l": [{"$extends": ["zz"]}], "$extends": []}
    root = one_source(opaque("r2_nested_ext"), body)
    assert_success_bytes(root, dumps({"n": {"$extends": ["unlisted"]}, "l": [{"$extends": ["zz"]}]}), opaque("r2_nested_ext.out"))


@witness("markers", "A marker differing only in letter case is an ordinary string")
def w_visible_marker_case_sensitive():
    root = write_root(
        opaque("r2_marker_case"),
        {"sources": [{"id": "base", "priority": 1}, {"id": "over", "priority": 2}]},
        {"base": {"x": 1, "y": 2, "n": {"z": 3}}, "over": {"x": "$DELETE", "y": "$Delete", "n": {"z": "$delete "}}},
    )
    assert_success_bytes(root, dumps({"x": "$DELETE", "y": "$Delete", "n": {"z": "$delete "}}), opaque("r2_marker_case.out"))


@witness("apply", "An array replaces a scalar or an object below it")
def w_visible_array_over_non_array():
    root = write_root(
        opaque("r2_arr_over"),
        {"sources": [{"id": "base", "priority": 1}, {"id": "over", "priority": 2}]},
        {"base": {"x": 1, "y": {"a": 1}, "z": None}, "over": {"x": [9], "y": [], "z": [None]}},
    )
    assert_success_bytes(root, dumps({"x": [9], "y": [], "z": [None]}), opaque("r2_arr_over.out"))


@witness("layers", "An own array is laid over an inherited scalar or object")
def w_visible_lay_array_over_non_array():
    root = write_root(
        opaque("r2_lay_arr"),
        {"sources": [{"id": "parent", "priority": 1}, {"id": "mid", "priority": 2}, {"id": "child", "priority": 3}]},
        {
            "parent": {"x": 1, "y": {"a": 1}},
            "mid": {"x": "$delete", "y": "$delete"},
            "child": {"$extends": ["parent"], "x": [9], "y": [8]},
        },
    )
    assert_success_bytes(root, dumps({"x": [9], "y": [8]}), opaque("r2_lay_arr.out"))


@witness("inherit", "A source used as a parent is still applied in its own place")
def w_visible_parent_layer_also_applied():
    root = write_root(
        opaque("r2_parent_applied"),
        {"sources": [{"id": "a", "priority": 0}, {"id": "p", "priority": 1}, {"id": "c", "priority": 2}]},
        {"a": {"x": 1}, "p": {"x": "$delete"}, "c": {"$extends": ["p"]}},
    )
    assert_success_bytes(root, "{}\n", opaque("r2_parent_applied.out"))


@witness("inherit", "One child's own data does not change what its parent gives a sibling")
def w_visible_sibling_does_not_alter_parent():
    root = write_root(
        opaque("r2_sibling"),
        {"sources": [{"id": "p", "priority": 0}, {"id": "a", "priority": 1}, {"id": "b", "priority": 2}]},
        {"p": {"x": 1, "db": {"h": 1, "port": 1}}, "a": {"$extends": ["p"], "x": 2, "db": {"port": 2}}, "b": {"$extends": ["p"]}},
    )
    assert_success_bytes(root, dumps({"x": 1, "db": {"h": 1, "port": 1}}), opaque("r2_sibling.out"))


SCALAR_MANIFESTS = [b"true\n", b"false\n", b"7\n", b"-0.5\n", b'"sources"\n']


@witness("manifest_doc", "A manifest that is a scalar is refused, not a crash")
def w_refuse_scalar_manifests():
    for i, body in enumerate(SCALAR_MANIFESTS):
        refuse_pair(_manifest_root(body, {}), "refuse: bad manifest", f"r3m{i}")


@witness("manifest_doc", "A scalar sources value or scalar entry is refused, not a crash")
def w_refuse_scalar_sources_and_entries():
    bodies = [b'{"sources":true}\n', b'{"sources":3}\n', b'{"sources":"a"}\n', b'{"sources":[true]}\n', b'{"sources":[false]}\n']
    for i, body in enumerate(bodies):
        refuse_pair(_manifest_root(body, {}), "refuse: bad manifest", f"r3e{i}")


@witness("order", "Equal priorities order ids byte by byte, not by the numbers inside them")
def w_visible_equal_priority_bytewise_not_natural():
    root = write_bytes_root(
        opaque("r3_natural"),
        b'{"sources":[{"id":"a2","priority":0},{"id":"a10","priority":0}]}\n',
        {"a2.json": b'{"winner":2}\n', "a10.json": b'{"winner":10}\n'},
    )
    assert_success_bytes(root, '{\n  "winner": 2\n}\n', opaque("r3_natural.out"))


@witness("order", "Equal priorities order digits, then underscore, then letters")
def w_visible_equal_priority_byte_classes():
    root = write_bytes_root(
        opaque("r3_byte_classes"),
        b'{"sources":[{"id":"ba","priority":5},{"id":"b_x","priority":5},{"id":"b9","priority":5},{"id":"b10","priority":5}]}\n',
        {
            "ba.json": b'{"last":"ba"}\n',
            "b_x.json": b'{"last":"b_x","u":1}\n',
            "b9.json": b'{"last":"b9","nine":1,"u":0}\n',
            "b10.json": b'{"last":"b10","nine":0,"ten":1}\n',
        },
    )
    assert_success_bytes(root, dumps({"last": "ba", "nine": 1, "ten": 1, "u": 1}), opaque("r3_byte_classes.out"))


@witness("inherit", "Parents are inherited in the order $extends writes them, not by priority")
def w_visible_parents_in_written_order():
    root = write_root(
        opaque("r3_parent_order"),
        {"sources": [{"id": "q", "priority": 0}, {"id": "p", "priority": 1}, {"id": "mid", "priority": 2}, {"id": "child", "priority": 3}]},
        {"q": {"x": "q"}, "p": {"x": "p"}, "mid": {"x": "mid"}, "child": {"$extends": ["p", "q"]}},
    )
    assert_success_bytes(root, '{\n  "x": "q"\n}\n', opaque("r3_parent_order.out"))


@witness("inherit", "Three parents written against priority order are inherited as written")
def w_visible_three_parents_in_written_order():
    root = write_root(
        opaque("r3_parent_order3"),
        {"sources": [{"id": "z", "priority": 0}, {"id": "y", "priority": 1}, {"id": "w", "priority": 2}, {"id": "kid", "priority": 3}]},
        {
            "z": {"a": "z", "b": "z", "c": "z"},
            "y": {"a": "y", "b": "y"},
            "w": {"a": "w", "d": {"k": "w"}},
            "kid": {"$extends": ["w", "z", "y"]},
        },
    )
    assert_success_bytes(root, dumps({"a": "y", "b": "y", "c": "z", "d": {"k": "w"}}), opaque("r3_parent_order3.out"))


KINDS = [True, False, 0, 7, -1.5, "s", "", None, [1], {"o": 1}]


def _kind_matrix():
    below, above, expected = {}, {}, {}
    for i, b in enumerate(KINDS):
        for j, a in enumerate(KINDS):
            if i == j:
                continue
            key = f"k{i}_{j}"
            below[key] = b
            above[key] = a
            expected[key] = a
    return below, above, expected


@witness("apply", "Every kind of value replaces every other kind below it, true included")
def w_visible_every_kind_replaces_every_other():
    below, above, expected = _kind_matrix()
    root = write_root(
        opaque("r4_kinds_apply"),
        {"sources": [{"id": "a", "priority": 0}, {"id": "b", "priority": 1}]},
        {"a": below, "b": above},
    )
    assert_success_bytes(root, dumps(expected), opaque("r4_kinds_apply.out"))


@witness("apply", "true over false and false over true each replace the value below")
def w_visible_true_replaces_false():
    root = write_root(
        opaque("r4_true_false"),
        {"sources": [{"id": "a", "priority": 0}, {"id": "b", "priority": 1}]},
        {"a": {"x": False, "y": True, "n": {"z": False}}, "b": {"x": True, "y": False, "n": {"z": True}}},
    )
    assert_success_bytes(root, '{\n  "n": {\n    "z": true\n  },\n  "x": true,\n  "y": false\n}\n', opaque("r4_true_false.out"))


@witness("layers", "Every kind of own value is laid over every other inherited kind")
def w_visible_every_kind_laid_over_every_other():
    below, above, expected = _kind_matrix()
    gone = {k: "$delete" for k in below}
    root = write_root(
        opaque("r4_kinds_lay"),
        {"sources": [{"id": "p", "priority": 0}, {"id": "m", "priority": 1}, {"id": "c", "priority": 2}]},
        {"p": below, "m": gone, "c": dict(above, **{"$extends": ["p"]})},
    )
    assert_success_bytes(root, dumps(expected), opaque("r4_kinds_lay.out"))


@witness("inherit", "A parent applied after its child still gives the child its configuration")
def w_visible_later_parent_decides():
    root = write_root(
        opaque("r4_later_parent"),
        {
            "sources": [
                {"id": "child", "priority": 0},
                {"id": "parent", "priority": 1},
                {"id": "mid", "priority": 2},
                {"id": "final", "priority": 3},
            ]
        },
        {"child": {"$extends": ["parent"]}, "parent": {"x": 1}, "mid": {"x": 9}, "final": {"$extends": ["child"]}},
    )
    assert_success_bytes(root, '{\n  "x": 1\n}\n', opaque("r4_later_parent.out"))


@witness("inherit", "A later parent's nested configuration reaches a child applied before it")
def w_visible_later_parent_nested_decides():
    root = write_root(
        opaque("r4_later_parent_n"),
        {
            "sources": [
                {"id": "kid", "priority": 0},
                {"id": "base", "priority": 1},
                {"id": "over", "priority": 2},
                {"id": "top", "priority": 3},
            ]
        },
        {
            "kid": {"$extends": ["base"], "db": {"user": "kid"}},
            "base": {"db": {"host": "base", "port": 1}},
            "over": {"db": {"host": "over", "port": 2}},
            "top": {"$extends": ["kid"]},
        },
    )
    assert_success_bytes(root, dumps({"db": {"host": "base", "port": 1, "user": "kid"}}), opaque("r4_later_parent_n.out"))


@witness("output", "Non-ASCII string values are escaped, in objects and in arrays")
def w_visible_non_ascii_values_escaped():
    root = write_bytes_root(
        opaque("r4_values_ascii"),
        ONE_A,
        {"a.json": '{"x":"é","l":["ü","😀"],"n":{"k":"Straße ☃"}}\n'.encode("utf-8")},
    )
    expected = '{\n  "l": [\n    "\\u00fc",\n    "\\ud83d\\ude00"\n  ],\n  "n": {\n    "k": "Stra\\u00dfe \\u2603"\n  },\n  "x": "\\u00e9"\n}\n'
    assert_success_bytes(root, expected, opaque("r4_values_ascii.out"))


def assert_success_replaces(root: Path, expected: str, out: Path, sentinel: bytes, *, out_first: bool = False) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(sentinel)
    os.chown(out, SANDBOX_UID, SANDBOX_GID, follow_symlinks=False)
    proc = run_merge(root, out, clear_out=False, out_first=out_first)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == b""
    assert out.read_bytes() == expected.encode("utf-8")


@witness("run", "Success replaces an output file that already exists")
def w_success_replaces_existing_out():
    root = one_source(opaque("r4_replace"), {"ok": 1})
    assert_success_replaces(root, '{\n  "ok": 1\n}\n', opaque("r4_replace.out"), b"sentinel bytes that are longer than the new output\n" * 4)


@witness("output", "A longer existing output is replaced whole, not overwritten in place")
def w_success_replaces_longer_out():
    root = one_source(opaque("r4_replace_long"), {"a": [1, 2]})
    assert_success_replaces(root, '{\n  "a": [\n    1,\n    2\n  ]\n}\n', opaque("r4_replace_long.out"), b"{" + b"x" * 300 + b"}\n")


JSON_KINDS = [b"true", b"false", b"null", b"0", b"7", b"-1", b"1.5", b'"s"', b'""', b"[]", b"[1]", b"{}", b'{"a":1}']


def refuse_one(build, msg: str, label: str) -> None:
    assert_refuse(build(opaque(label)), msg, opaque(f"{label}.out"))


def _kind_cases(template: bytes, kinds, files, msg: str, label: str) -> None:
    failed = []
    for i, kind in enumerate(kinds):
        try:
            refuse_one(_manifest_root(template.replace(b"@", kind), files), msg, f"{label}_{i}")
        except AssertionError as exc:
            failed.append(f"{kind!r}: {exc}")
    assert not failed, failed


@witness("manifest_doc", "sources holding any JSON kind other than an array is refused")
def w_refuse_sources_every_non_array_kind():
    kinds = [k for k in JSON_KINDS if not k.startswith(b"[")]
    _kind_cases(b'{"sources":@}\n', kinds, {}, "refuse: bad manifest", "r5_sources_kind")


@witness("entries", "An entry of any JSON kind other than a well-formed object is refused")
def w_refuse_entry_every_kind():
    _kind_cases(b'{"sources":[@]}\n', JSON_KINDS, {}, "refuse: bad manifest", "r5_entry_kind")


@witness("ids", "An id of any JSON kind other than a string is refused")
def w_refuse_id_every_non_string_kind():
    kinds = [k for k in JSON_KINDS if not k.startswith(b'"')]
    _kind_cases(b'{"sources":[{"id":@,"priority":0}]}\n', kinds, {"a.json": b"{}\n"}, "refuse: bad manifest", "r5_id_kind")


@witness("entries", "A priority of any JSON kind other than an integer in range is refused")
def w_refuse_priority_every_non_integer_kind():
    kinds = [k for k in JSON_KINDS if k not in (b"0", b"7")] + [b'"1"', b"[0]", b'{"p":0}']
    _kind_cases(b'{"sources":[{"id":"a","priority":@}]}\n', kinds, {"a.json": b"{}\n"}, "refuse: bad manifest", "r5_pri_kind")


@witness("entries", "A malformed priority in a later entry is refused")
def w_refuse_priority_kind_in_later_entry():
    for i, kind in enumerate([b"[]", b"{}", b"null"]):
        manifest = b'{"sources":[{"id":"a","priority":0},{"id":"b","priority":1},{"id":"c","priority":' + kind + b"}]}\n"
        refuse_one(_manifest_root(manifest, {"a.json": b"{}\n", "b.json": b"{}\n", "c.json": b"{}\n"}), "refuse: bad manifest", f"r5_pri_later_{i}")


@witness("metadata", "$extends of any JSON kind other than an array is refused")
def w_refuse_extends_every_non_array_kind():
    kinds = [k for k in JSON_KINDS if not k.startswith(b"[")]
    failed = []
    for i, kind in enumerate(kinds):
        try:
            refuse_one(_manifest_root(ONE_A, {"a.json": b'{"$extends":' + kind + b"}\n"}), "refuse: bad source", f"r5_ext_kind_{i}")
        except AssertionError as exc:
            failed.append(f"{kind!r}: {exc}")
    assert not failed, failed


@witness("metadata", "A parent named by any JSON kind other than a listed id string is refused")
def w_refuse_extends_member_every_kind():
    kinds = [k for k in JSON_KINDS if k != b'"s"'] + [b'"A"']
    failed = []
    for i, kind in enumerate(kinds):
        try:
            refuse_one(
                _manifest_root(TWO_AB, {"a.json": b"{}\n", "b.json": b'{"$extends":["a",' + kind + b"]}\n"}),
                "refuse: bad source",
                f"r5_ext_member_{i}",
            )
        except AssertionError as exc:
            failed.append(f"{kind!r}: {exc}")
    assert not failed, failed


@witness("source_doc", "A source of any JSON kind other than an object is refused")
def w_refuse_source_every_non_object_kind():
    kinds = [k for k in JSON_KINDS if not k.startswith(b"{")]
    failed = []
    for i, kind in enumerate(kinds):
        try:
            refuse_one(_manifest_root(TWO_AB, {"a.json": b'{"x":1}\n', "b.json": kind + b"\n"}), "refuse: bad source", f"r5_src_kind_{i}")
        except AssertionError as exc:
            failed.append(f"{kind!r}: {exc}")
    assert not failed, failed


THREE_LISTED_OUT_OF_ORDER = b'{"sources":[{"id":"c","priority":2},{"id":"a","priority":0},{"id":"b","priority":1}]}\n'

LATER_SOURCE_FAULTS = [
    b'{"x":NaN}\n',
    b'{"x":Infinity}\n',
    b'{"x":[-Infinity]}\n',
    b'{"x":1,"x":2}\n',
    b'{"n":{"x":1,"x":2}}\n',
    b'{"x":"\xff"}\n',
    b'{"x":1} {}\n',
    b'{"x":1,}\n',
    b"[]\n",
]


@witness("source_doc", "Every strict-JSON rule holds for the second and the last source too")
def w_refuse_faults_in_later_sources():
    failed = []
    for i, fault in enumerate(LATER_SOURCE_FAULTS):
        for where in ("b", "c"):
            files = {"a.json": b'{"x":0}\n', "b.json": b'{"y":1}\n', "c.json": b'{"z":2}\n'}
            files[f"{where}.json"] = fault
            try:
                refuse_one(_manifest_root(THREE_LISTED_OUT_OF_ORDER, files), "refuse: bad source", f"r5_later_{where}_{i}")
            except AssertionError as exc:
                failed.append(f"{where} {fault!r}: {exc}")
    assert not failed, failed


@witness("metadata", "Metadata rules hold for the last source too")
def w_refuse_metadata_faults_in_last_source():
    for i, fault in enumerate([b'{"$x":1}\n', b'{"$extends":["a","a"]}\n', b'{"$extends":"a"}\n', b'{"$extends":["zz"]}\n']):
        files = {"a.json": b'{"x":0}\n', "b.json": b'{"y":1}\n', "c.json": fault}
        refuse_one(_manifest_root(THREE_LISTED_OUT_OF_ORDER, files), "refuse: bad source", f"r5_meta_last_{i}")


def _member_root(extra) -> object:
    def build(dest: Path) -> Path:
        root = write_bytes_root(dest, ONE_A, {"a.json": b"{}\n"})
        extra(root / "sources")
        _chown_tree(root, SANDBOX_UID, SANDBOX_GID)
        return root

    return build


MEMBER_EXTRAS = [
    ("hidden_file", lambda s: (s / ".notes").write_bytes(b"x\n")),
    ("hidden_json", lambda s: (s / ".a.json").write_bytes(b"{}\n")),
    ("bare_json", lambda s: (s / ".json").write_bytes(b"{}\n")),
    ("hidden_dir", lambda s: (s / ".git").mkdir()),
    ("hidden_link", lambda s: (s / ".link").symlink_to("a.json")),
    ("upper_ext", lambda s: (s / "b.JSON").write_bytes(b"{}\n")),
    ("backup", lambda s: (s / "a.json.bak").write_bytes(b"{}\n")),
    ("tilde", lambda s: (s / "a.json~").write_bytes(b"{}\n")),
]


@witness("members", "Hidden and oddly named entries in sources are members too")
def w_refuse_hidden_and_odd_members():
    failed = []
    for name, extra in MEMBER_EXTRAS:
        try:
            refuse_one(_member_root(extra), "refuse: bad manifest", f"r5_member_{name}")
        except AssertionError as exc:
            failed.append(f"{name}: {exc}")
    assert not failed, failed


@witness("output", "Floats up to the largest finite binary64 value are written, not refused")
def w_visible_largest_finite_floats():
    root = write_bytes_root(
        opaque("r6_float_top"),
        ONE_A,
        {"a.json": b'{"a":1.1e308,"b":1.7976931348623157e308,"c":-1.7976931348623157e308,"d":1.7976931348623158e308,"e":1e308,"f":-1.1E+308}\n'},
    )
    expected = '{\n  "a": 1.1e+308,\n  "b": 1.7976931348623157e+308,\n  "c": -1.7976931348623157e+308,\n  "d": 1.7976931348623157e+308,\n  "e": 1e+308,\n  "f": -1.1e+308\n}\n'
    assert_success_bytes(root, expected, opaque("r6_float_top.out"))


@witness("bounds", "Floats down to the smallest subnormal, and literals that decode to zero, are written as decoded")
def w_visible_smallest_floats():
    root = write_bytes_root(
        opaque("r6_float_bottom"),
        ONE_A,
        {"a.json": b'{"a":2.2250738585072014e-308,"b":5e-324,"c":4.9e-324,"d":-5e-324,"e":1e-400,"f":-1e-400,"g":0.1000000000000000055511151231257827,"h":2.225073858507201e-308}\n'},
    )
    expected = '{\n  "a": 2.2250738585072014e-308,\n  "b": 5e-324,\n  "c": 5e-324,\n  "d": -5e-324,\n  "e": 0.0,\n  "f": -0.0,\n  "g": 0.1,\n  "h": 2.225073858507201e-308\n}\n'
    assert_success_bytes(root, expected, opaque("r6_float_bottom.out"))


@witness("run", "--out may come before --root on success")
def w_success_with_out_before_root():
    root = one_source(opaque("r7_out_first"), {"ok": 1, "n": {"m": [2]}})
    assert_success_bytes(root, dumps({"ok": 1, "n": {"m": [2]}}), opaque("r7_out_first.out"), out_first=True)


@witness("run", "--out may come before --root on an empty root and on a refusal")
def w_out_before_root_empty_and_refusal():
    empty = write_bytes_root(opaque("r7_out_first_empty"), b'{"sources":[]}\n', {})
    assert_success_bytes(empty, "{}\n", opaque("r7_out_first_empty.out"), out_first=True)
    bad = write_bytes_root(opaque("r7_out_first_bad"), ONE_A, {"a.json": b'{"x":NaN}\n'})
    assert_refuse(bad, "refuse: bad source", opaque("r7_out_first_bad.out"), out_first=True)
    assert_refuse(bad, "refuse: bad source", opaque("r7_out_first_keep") / "o.json", preexisting=b"keep\n", out_first=True)


WIDE_ENCODINGS = ("utf-16-le", "utf-16-be", "utf-32-le", "utf-32-be")


@witness("source_doc", "A source in UTF-16 or UTF-32 without a byte-order mark is not UTF-8 and is refused")
def w_refuse_source_wide_encodings():
    failed = []
    for enc in WIDE_ENCODINGS:
        for where in ("a", "b"):
            files = {"a.json": b'{"k":0}\n', "b.json": b'{"k":1}\n'}
            files[f"{where}.json"] = '{"x":1}\n'.encode(enc)
            try:
                refuse_one(_manifest_root(TWO_AB, files), "refuse: bad source", f"r8_src_{enc}_{where}")
            except AssertionError as exc:
                failed.append(f"{enc} in {where}: {exc}")
    assert not failed, failed


@witness("source_doc", "A source holding UTF-8-encoded surrogate bytes is not valid UTF-8")
def w_refuse_source_encoded_surrogate():
    refuse_one(_manifest_root(ONE_A, {"a.json": b'{"x":"\xed\xa0\x80"}\n'}), "refuse: bad source", "r8_src_surrogate")


@witness("manifest_doc", "A manifest in UTF-16 or UTF-32 without a byte-order mark is not UTF-8 and is refused")
def w_refuse_manifest_wide_encodings():
    failed = []
    for enc in WIDE_ENCODINGS:
        try:
            refuse_one(_manifest_root('{"sources":[{"id":"a","priority":0}]}\n'.encode(enc), {"a.json": b"{}\n"}), "refuse: bad manifest", f"r8_man_{enc}")
        except AssertionError as exc:
            failed.append(f"{enc}: {exc}")
    assert not failed, failed


@witness("output", "Escaped lone surrogates are decoded as Python decodes them and written back escaped")
def w_visible_escaped_lone_surrogates():
    root = write_bytes_root(
        opaque("r9_surrogates"),
        ONE_A,
        {"a.json": b'{"x":"\\ud800","l":["\\udfff"],"\\udc00k":1,"p":"\\ud83d\\ude00"}\n'},
    )
    expected = '{\n  "l": [\n    "\\udfff"\n  ],\n  "p": "\\ud83d\\ude00",\n  "x": "\\ud800",\n  "\\udc00k": 1\n}\n'
    assert_success_bytes(root, expected, opaque("r9_surrogates.out"))


@witness("run", "Every refusal kind leaves an existing output file untouched")
def w_refuse_kinds_keep_preexisting_out():
    cases = [
        ("k1", _manifest_root(ONE_A, {"a.json": b'{"x":NaN}\n'}), "refuse: bad source"),
        ("k2", _manifest_root(ONE_A, {"a.json": b'{"x":1}0\n'}), "refuse: bad source"),
        ("k3", _manifest_root(b'{"sources":[{"id":"Ab","priority":0}]}\n', {"Ab.json": b"{}\n"}), "refuse: bad manifest"),
        ("k4", _manifest_root(b'{"sources":[{"id":"\\u00e9","priority":0}]}\n', {"\u00e9.json": b"{}\n"}), "refuse: bad manifest"),
        ("k5", _manifest_root(b'{"sources":[{"id":"a\\n","priority":0}]}\n', {"a\n.json": b"{}\n"}), "refuse: bad manifest"),
        ("k6", _manifest_root(ONE_A, {"a.json": b'{"$label":"x"}\n'}), "refuse: bad source"),
        ("k7", _manifest_root(b'{"sources":[{"id":"a","priority":true}]}\n', {"a.json": b"{}\n"}), "refuse: bad manifest"),
        ("k8", _manifest_root(b'{"sources":[{"id":"a","priority":1000001}]}\n', {"a.json": b"{}\n"}), "refuse: bad manifest"),
        ("k9", _manifest_root(b'{"sources":[{"id":"a","priority":1},{"id":"a","priority":2}]}\n', {"a.json": b"{}\n"}), "refuse: bad manifest"),
        ("k10", _manifest_root(b'{"sources":[{"id":"a","priority":1},{"id":"b","priority":2}]}\n', {"a.json": b"{}\n", "b.json": b'{"$extends":["a","a"]}\n'}), "refuse: bad source"),
        ("k11", _manifest_root(b'{"sources":[{"id":"a","priority":false}]}\n', {"a.json": b"{}\n"}), "refuse: bad manifest"),
        ("k12", _manifest_root(b'{"sources":[{"id":"a","priority":1e2}]}\n', {"a.json": b"{}\n"}), "refuse: bad manifest"),
        ("k13", _manifest_root(b'{"sources":[{"id":"a","priority":0,"priority":1}]}\n', {"a.json": b"{}\n"}), "refuse: bad manifest"),
        ("k14", _manifest_root(ONE_A, {"a.json": b'{"n":{"x":1,"x":2}}\n'}), "refuse: bad source"),
        ("k15", lambda dest: _socket_root(dest, "manifest.json"), "refuse: bad manifest"),
        ("k16", lambda dest: _socket_root(dest, "sources"), "refuse: bad manifest"),
        ("k17", _manifest_root(b"true\n", {}), "refuse: bad manifest"),
        ("k18", _manifest_root(b"7\n", {}), "refuse: bad manifest"),
        ("k19", _manifest_root(b'{"sources":[true]}\n', {}), "refuse: bad manifest"),
        ("k20", _manifest_root(b'{"sources":false}\n', {}), "refuse: bad manifest"),
        ("k21", _manifest_root(b'{"sources":[{"id":"a","priority":[]}]}\n', {"a.json": b"{}\n"}), "refuse: bad manifest"),
        ("k22", _manifest_root(b'{"sources":[{"id":"a","priority":{}}]}\n', {"a.json": b"{}\n"}), "refuse: bad manifest"),
        ("k23", _manifest_root(ONE_A, {"a.json": b"{}\n", ".notes": b"x\n"}), "refuse: bad manifest"),
        ("k24", _manifest_root(TWO_AB, {"a.json": b"{}\n", "b.json": b'{"x":NaN}\n'}), "refuse: bad source"),
        ("k25", _manifest_root(TWO_AB, {"a.json": b"{}\n", "b.json": b'{"x":1,"x":2}\n'}), "refuse: bad source"),
        ("k26", _manifest_root(b'{"sources":[{"id":"a","priority":[]}]}\n', {"a.json": b"{}\n"}), "refuse: bad manifest"),
        ("k27", _manifest_root(b'{"sources":[{"id":"a","priority":{}}]}\n', {"a.json": b"{}\n"}), "refuse: bad manifest"),
        ("k28", _member_root(lambda s: (s / ".notes").write_bytes(b"x\n")), "refuse: bad manifest"),
        ("k29", _member_root(lambda s: (s / ".git").mkdir()), "refuse: bad manifest"),
        ("k30", _manifest_root(TWO_AB, {"a.json": b"{}\n", "b.json": b'{"x":NaN}\n'}), "refuse: bad source"),
        ("k31", _manifest_root(TWO_AB, {"a.json": b"{}\n", "b.json": b'{"x":1,"x":2}\n'}), "refuse: bad source"),
        ("k32", _manifest_root(TWO_AB, {"a.json": b"{}\n", "b.json": b'{"x":"\xff"}\n'}), "refuse: bad source"),
        ("k33", _manifest_root(TWO_AB, {"a.json": b"{}\n", "b.json": b'{"x":1} 0\n'}), "refuse: bad source"),
        ("k34", _manifest_root(b'{"sources":[{"id":{},"priority":0}]}\n', {"a.json": b"{}\n"}), "refuse: bad manifest"),
        ("k35", _manifest_root(TWO_AB, {"a.json": b"{}\n", "b.json": b'{"$extends":[null]}\n'}), "refuse: bad source"),
        ("k36", _manifest_root(TWO_AB, {"a.json": b"{}\n", "b.json": b'{"$extends":["a",{}]}\n'}), "refuse: bad source"),
        ("k37", _manifest_root(TWO_AB, {"a.json": b"{}\n", "b.json": b"[1]\n"}), "refuse: bad source"),
        ("k38", _manifest_root(ONE_A, {"a.json": '{"x":1}'.encode("utf-16-le")}), "refuse: bad source"),
        ("k39", _manifest_root(TWO_AB, {"a.json": b"{}\n", "b.json": '{"x":1}'.encode("utf-32-be")}), "refuse: bad source"),
        ("k40", _manifest_root('{"sources":[]}'.encode("utf-16-be"), {}), "refuse: bad manifest"),
        ("k41", _manifest_root('{"sources":[]}'.encode("utf-32-le"), {}), "refuse: bad manifest"),
        ("k42", _manifest_root(ONE_A, {"a.json": b'{"x":"\xed\xa0\x80"}\n'}), "refuse: bad source"),
        ("k43", _manifest_root(b'{"sources":[{"id":"a","priority":1},{"id":"b","priority":2}]}\n', {"a.json": b'{"$extends":["b"]}\n', "b.json": b'{"$extends":["a"]}\n'}), "refuse: bad source"),
    ]
    for tag, build, msg in cases:
        _preexisting(tag, build, msg)

    def with_dir_member(dest: Path) -> Path:
        root = write_bytes_root(dest, ONE_A, {"a.json": b"{}\n"})
        (root / "sources" / "extra").mkdir()
        _chown_tree(root, SANDBOX_UID, SANDBOX_GID)
        return root

    _preexisting("k_dir_member", with_dir_member, "refuse: bad manifest")


@witness("run", "Success creates every missing parent directory of the output path")
def w_creates_deep_out_parents():
    root = one_source(opaque("vis_deep_out"), {"ok": True})
    out = opaque("deep_out_a") / "b" / "c" / "out.json"
    if (opaque("deep_out_a")).exists():
        shutil.rmtree(opaque("deep_out_a"))
    assert_success_bytes(root, '{\n  "ok": true\n}\n', out)


def test_run_outcomes():
    """The program runs from /app/merge.py, creates output parents, and every refusal leaves an existing output untouched (R1, R2)."""
    run_family("run")


def test_root_layout():
    """A root needs a regular manifest.json and a real sources directory, neither a symlink (R3)."""
    run_family("layout")


def test_manifest_document():
    """The manifest is one strict JSON object with exactly the member sources holding an array (R4)."""
    run_family("manifest_doc")


def test_manifest_entries():
    """Entries hold exactly id and priority, priorities are integers 0 to 1000000, and ids are unique (R5)."""
    run_family("entries")


def test_id_grammar():
    """An id is 1 to 32 ASCII characters, a lowercase letter first, then lowercase letters, digits or underscores (R5)."""
    run_family("ids")


def test_sources_members():
    """Every member of sources is a regular <id>.json file for a listed id, and every listed id has one (R6)."""
    run_family("members")


def test_application_order():
    """Sources apply by ascending priority then bytewise id, whatever the listing order (R7)."""
    run_family("order")


def test_source_document():
    """Each source file is one strict UTF-8 JSON object with nothing after it (R8)."""
    run_family("source_doc")


def test_metadata():
    """Only $extends is allowed as top-level metadata and it lists distinct listed ids (R9)."""
    run_family("metadata")


def test_parent_cycles():
    """Parents that lead back to a source already on the way are refused (R9)."""
    run_family("cycles")


def test_deletion_markers():
    """A $delete member value reached through objects removes its key; inside arrays it is an ordinary string (R10, R11)."""
    run_family("markers")


def test_applying():
    """Applying merges objects key by key and replaces every other value whole (R11)."""
    run_family("apply")


def test_layers():
    """A source's own data is laid over its inheritance with its markers kept for the merge (R12)."""
    run_family("layers")


def test_inheritance():
    """A parent passes its configuration, with its own markers spent, built only from the root's files (R12, R13, R15)."""
    run_family("inherit")


def test_output_bytes():
    """Output is json.dumps with indent 2, sorted keys and ASCII escapes, plus one LF (R8, R16)."""
    run_family("output")


def test_domain_bounds():
    """Roots at the edges of the graded domain merge correctly (R18)."""
    run_family("bounds")


def test_sealed_roots():
    """Held-out roots merge to the exact configuration bytes (R7, R10 to R16, R18)."""
    failed = []
    for index, name in enumerate(sorted(SEALED_EXPECTS)):
        root, expected = stage_sealed_root(name)
        token = hashlib.sha256(f"sealedout:{name}".encode()).hexdigest()[:16]
        try:
            assert_success_replaces(root, expected, opaque(f"wx_out_{token}.json"), b"{\"stale\": " + b"0" * 400 + b"}\n", out_first=index % 2 == 1)
        except Exception as exc:
            failed.append(f"{name}: {type(exc).__name__}")
    assert len(SEALED_EXPECTS) >= 41
    assert not failed, failed
