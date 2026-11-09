from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import shlex
import shutil
import signal
import stat
import subprocess
import tempfile
from pathlib import Path

import pytest

APP = Path("/app")
GRAPH = APP / "out" / "provenance-graph.json"
ATTEST = APP / "out" / "lineage-attest.npg"
SEALED_DIR = Path("/verifier-seal")
HOLDOUT_DIR = Path("/tests/holdout/ember-wave")
WORK = Path("/work/out")
MATRIX = APP / "tables" / "liberty-eligibility.csv"

SANDBOX_UID = 2000
SANDBOX_GID = 2000
REAL_GO = shutil.which("go") or "/usr/local/go/bin/go"
VF_BIN = Path("/tmp/kb-bin")
DROP = [
    "setpriv",
    f"--reuid={SANDBOX_UID}",
    f"--regid={SANDBOX_GID}",
    "--clear-groups",
    "--no-new-privs",
]

FIND_FAMILIES = {
    "GHOST_MAP",
    "LIB_MISBIND",
    "ANCESTOR_GAP",
    "AREA_REGRESS",
    "STALE_LIB",
    "CLKGT_SPLIT",
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_seal(name: str) -> bytes:
    path = SEALED_DIR / name
    data = path.read_bytes()
    os.chmod(path, 0o600)
    path.unlink()
    return data


def _load_tree_bytes(root: Path, unlink: bool = False) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))]
        for name in filenames:
            full = Path(dirpath) / name
            if os.path.islink(full) or not os.path.isfile(full):
                continue
            out[str(full.relative_to(root))] = full.read_bytes()
            if unlink:
                os.chmod(full, 0o600)
                full.unlink()
    return out


def _materialize(tree: dict[str, bytes], dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    for rel, data in tree.items():
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def guarded(path: Path, root: Path) -> Path:
    cur = path
    for _ in range(64):
        try:
            mode = os.lstat(cur).st_mode
        except FileNotFoundError as exc:
            raise AssertionError("missing %s" % path) from exc
        assert not stat.S_ISLNK(mode), "%s is a symlink" % cur
        if cur.parent == cur:
            break
        cur = cur.parent
    resolved = path.resolve(strict=True)
    rootres = root.resolve()
    assert resolved == rootres or rootres in resolved.parents, "%s resolves outside %s" % (path, root)
    assert stat.S_ISREG(os.lstat(path).st_mode), "%s is not a regular file" % path
    return path


def read_guarded(path: Path, root: Path) -> bytes:
    guarded(path, root)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as handle:
        return handle.read()


SEALED_GRAPH = _load_seal("provenance-graph.json")
SEALED_ATTEST = _load_seal("lineage-attest.npg")
SEALED_EMBER_GRAPH = _load_seal("ember-provenance-graph.json")
SEALED_EMBER_ATTEST = _load_seal("ember-lineage-attest.npg")
SEALED_QUIET_GRAPH = _load_seal("quiet-provenance-graph.json")
SEALED_QUIET_ATTEST = _load_seal("quiet-lineage-attest.npg")
SEALED_ORDINANCE = _load_seal("synth-ordinance.txt")
SEALED_MATRIX = _load_seal("liberty-eligibility.csv")
SEALED_JOURNAL = _load_seal("synth-epochs.ndjson")
SEALED_TRAPS = json.loads(_load_seal("desk-traps.json"))
SEALED_MODULES = _load_tree_bytes(SEALED_DIR / "modules", unlink=True)
HOLDOUT_TREE = _load_tree_bytes(HOLDOUT_DIR)
HOLDOUT_QUIET = _load_tree_bytes(Path("/tests/holdout/quiet-line"))

shutil.rmtree(Path("/tests/holdout"), ignore_errors=True)


def _rebirth_dir(path: Path, mode: int, uid: int, gid: int) -> None:
    """Recreate a directory on the writable layer so a later exchange stays on one device."""
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".rebirth.", dir=parent))
    if path.is_dir() and not path.is_symlink():
        for child in path.iterdir():
            dest = staging / child.name
            if child.is_symlink():
                dest.symlink_to(os.readlink(child))
            elif child.is_dir():
                shutil.copytree(child, dest, symlinks=True)
            elif child.is_file():
                shutil.copy2(child, dest, follow_symlinks=False)
        shutil.rmtree(path)
    staging.rename(path)
    os.chmod(path, mode)
    try:
        os.chown(path, uid, gid)
    except PermissionError:
        pass


if Path("/verifier-seal").is_dir() or Path("/tests/test_outputs.py").is_file():
    _rebirth_dir(WORK, 0o700, SANDBOX_UID, SANDBOX_GID)
    if Path("/app/out").is_dir():
        _rebirth_dir(Path("/app/out"), 0o755, 0, 0)


def assert_note_safe(note: str) -> None:
    assert isinstance(note, str) and note.strip() != ""
    assert len(note) <= 160
    assert all(32 <= ord(ch) <= 126 for ch in note)
    assert not re.search(r"[0-9a-fA-F]{32,}", note), "hex key encoding in note"
    assert not re.search(r"[A-Za-z0-9+/]{32,}={0,2}", note), "base64 key encoding in note"
    grouped_scalar = r"[0-9a-fA-F](?:[^A-Za-z0-9]*[0-9a-fA-F]){63}"
    assert not re.search(grouped_scalar, note), "grouped 64-hex private scalar"


def assert_stream_safe(blob: str) -> None:
    assert all(ch in "\n\r\t" or 32 <= ord(ch) <= 126 for ch in blob), "non-ascii process output"
    if blob.strip() == "":
        return
    assert not re.search(r"[0-9a-fA-F]{32,}", blob), "hex key encoding in process output"
    assert not re.search(r"[A-Za-z0-9+/]{32,}={0,2}", blob), "base64 key encoding in process output"


def assert_attest_safe(raw: str) -> None:
    assert all(ch in "\n\r" or 32 <= ord(ch) <= 126 for ch in raw), "non-ascii attest"
    lines = raw.splitlines()
    assert lines and lines[0] == "NPG1"
    assert lines[-1].startswith("SEAL|")
    for idx, line in enumerate(lines):
        if idx == 0:
            assert line == "NPG1"
        elif idx == len(lines) - 1:
            assert line.startswith("SEAL|")
        else:
            assert line.startswith("NODE|") or line.startswith("FIND|"), line


def assert_attest_matches(raw: str, led: dict, journal: list[str]) -> None:
    assert_attest_safe(raw)
    body, sep, seal_line = raw.rpartition("SEAL|")
    assert sep == "SEAL|"
    assert body.endswith("\n")
    digest = seal_line.strip()
    assert len(digest) == 64
    assert all(ch in "0123456789abcdef" for ch in digest)
    assert digest == sha256_bytes(body.encode())
    last_kind: dict[str, str] = {}
    last_site: dict[str, str] = {}
    last_clk: dict[str, str] = {}
    for rec in journal:
        ev = json.loads(rec)
        part = ev["part"]
        last_kind[part] = ev["kind"]
        last_site[part] = ev["site"]
        if ev["kind"] in {"map", "recover"}:
            last_clk[part] = str(ev["clk"]).lower()
    parts = sorted(last_kind)
    lines = raw.splitlines()
    mid = lines[1:-1]
    slot_n = len(parts)
    issue_n = len(issue_identity(led))
    assert len(mid) == slot_n + issue_n
    slot_lines = mid[:slot_n]
    issue_lines = mid[slot_n:]
    assert all(ln.startswith("NODE|") for ln in slot_lines)
    assert all(ln.startswith("FIND|") for ln in issue_lines)
    assert [ln.split("|")[1] for ln in slot_lines] == parts
    for ln in slot_lines:
        fields = ln.split("|")
        assert len(fields) == 5
        part, site, clk, phase = fields[1], fields[2], fields[3], fields[4]
        assert site == last_site[part]
        assert clk == last_clk.get(part, "")
        assert clk == clk.lower()
        expect_phase = "frozen" if last_kind[part] == "freeze" else "live"
        assert phase == expect_phase
    parsed = []
    for ln in issue_lines:
        _, code, part, epoch = ln.split("|", 3)
        parsed.append((code, part, int(epoch)))
    assert parsed == issue_identity(led)


def verbs_from_matrix_text(text: str) -> dict[str, str]:
    verbs: dict[str, str] = {}
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        if row.get("kind") == "REPAIR":
            verbs[row["token"]] = row["payload"]
    return verbs


def assert_repairs_for(led: dict, matrix_text: str) -> None:
    verbs = verbs_from_matrix_text(matrix_text)
    assert len(led["patches"]) == len(led["findings"])
    for idx, (issue, repair) in enumerate(zip(led["findings"], led["patches"]), start=1):
        assert set(repair.keys()) == {"rank", "verb", "part", "epoch"}
        assert isinstance(repair["rank"], int) and not isinstance(repair["rank"], bool)
        assert isinstance(repair["epoch"], int) and not isinstance(repair["epoch"], bool)
        assert repair["rank"] == idx
        assert repair["part"] == issue["part"]
        assert repair["epoch"] == issue["epoch"]
        assert repair["verb"] == verbs[issue["code"]]


def sandbox_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in list(env):
        if key.startswith("GO") or key in {"CGO_ENABLED", "GCCGO", "GOENV"}:
            env.pop(key, None)
    env.pop("PYTEST_ADDOPTS", None)
    env["HOME"] = "/work"
    env["GOCACHE"] = "/work/go-cache"
    env["GOMODCACHE"] = "/work/go-mod"
    env["GOPROXY"] = "off"
    env["GOSUMDB"] = "off"
    env["GOFLAGS"] = ""
    env["CGO_ENABLED"] = "0"
    env["GOTOOLCHAIN"] = "local"
    env["GO111MODULE"] = "on"
    env["PATH"] = str(VF_BIN) + ":" + env.get("PATH", "/usr/local/go/bin:/usr/bin:/bin")
    Path("/work/go-cache").mkdir(parents=True, exist_ok=True)
    Path("/work/go-mod").mkdir(parents=True, exist_ok=True)
    try:
        os.chown("/work/go-cache", SANDBOX_UID, SANDBOX_GID)
        os.chown("/work/go-mod", SANDBOX_UID, SANDBOX_GID)
        os.chown("/work", SANDBOX_UID, SANDBOX_GID)
        os.chown("/work/out", SANDBOX_UID, SANDBOX_GID)
    except PermissionError:
        pass
    return env


def assert_protected_inputs_intact() -> None:
    assert read_guarded(APP / "canon" / "synth-ordinance.txt", APP) == SEALED_ORDINANCE
    assert read_guarded(MATRIX, APP) == SEALED_MATRIX
    assert read_guarded(APP / "history" / "synth-epochs.ndjson", APP) == SEALED_JOURNAL
    live_root = APP / "netlist" / "modules"
    for name, data in SEALED_MODULES.items():
        path = live_root / name
        assert read_guarded(path, APP) == data


@pytest.fixture(scope="session", autouse=True)
def shipped_artifacts():
    out_dir = APP / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chown(out_dir, SANDBOX_UID, SANDBOX_GID)
    except PermissionError:
        pass
    result = run_netloom(
        APP / "history" / "synth-epochs.ndjson",
        APP / "netlist" / "modules",
        APP / "canon" / "synth-ordinance.txt",
        MATRIX,
        GRAPH,
        ATTEST,
        unlink_outputs=False,
    )
    yield result
    subprocess.run(["pkill", "-KILL", "-u", str(SANDBOX_UID)], capture_output=True, check=False)
    assert_protected_inputs_intact()


def load_graph() -> dict:
    return json.loads(read_guarded(GRAPH, APP))


def repair_verbs() -> dict[str, str]:
    verbs: dict[str, str] = {}
    reader = csv.DictReader(io.StringIO(SEALED_MATRIX.decode()))
    for row in reader:
        if row["kind"] == "REPAIR":
            verbs[row["token"]] = row["payload"]
    return verbs


def issue_identity(led: dict) -> list[tuple[str, str, int]]:
    keys = []
    for item in led["findings"]:
        assert set(item.keys()) == {"code", "part", "note", "epoch"}
        assert_note_safe(item["note"])
        assert isinstance(item["epoch"], int) and not isinstance(item["epoch"], bool)
        keys.append((item["code"], item["part"], item["epoch"]))
    return keys


def repair_identity(led: dict) -> list[tuple[int, str, str, int]]:
    keys = []
    for item in led["patches"]:
        assert set(item.keys()) == {"rank", "verb", "part", "epoch"}
        assert isinstance(item["rank"], int) and not isinstance(item["rank"], bool)
        assert isinstance(item["epoch"], int) and not isinstance(item["epoch"], bool)
        keys.append((item["rank"], item["verb"], item["part"], item["epoch"]))
    return keys


def last_journal_epoch(journal: Path, part: str) -> int:
    last = 0
    if APP in journal.resolve().parents:
        raw = read_guarded(journal, APP).decode()
    else:
        raw = journal.read_text()
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        event = json.loads(line)
        if event["part"] == part:
            last = int(event["epoch"])
    return last


def assert_module_derived_epochs(led: dict, journal: Path) -> None:
    module_codes = {"LIB_MISBIND", "ANCESTOR_GAP", "CLKGT_SPLIT"}
    for item in led["findings"]:
        if item["code"] in module_codes:
            assert item["epoch"] == last_journal_epoch(journal, item["part"])


def auditor_stderr(stderr: str) -> str:
    kept = []
    for line in (stderr or "").splitlines():
        if re.fullmatch(r"exit status \d+", line.strip()):
            continue
        kept.append(line)
    return re.sub(r"exit status \d+\s*$", "", "\n".join(kept)).strip()


def _netloom_package_digest() -> str:
    pkg = Path("/app/cmd/netloom")
    assert pkg.is_dir(), "missing package /app/cmd/netloom"
    h = hashlib.sha256()
    gomod = Path("/app/go.mod")
    if gomod.is_file() and not gomod.is_symlink():
        h.update(b"mod:")
        h.update(gomod.read_bytes())
    sources = []
    for entry in sorted(pkg.iterdir(), key=lambda p: p.name):
        if entry.is_symlink() or not entry.is_file():
            continue
        if entry.suffix != ".go":
            continue
        sources.append(entry)
    assert sources, "no .go sources in /app/cmd/netloom"
    for src in sources:
        h.update(src.name.encode())
        h.update(b"\0")
        h.update(src.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def _compiled_netloom() -> Path:
    digest = _netloom_package_digest()
    dest_dir = Path("/tmp/kb-cache")
    dest_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(dest_dir, 0o700)
    try:
        os.chown(dest_dir, 0, 0)
    except PermissionError:
        pass
    dest = dest_dir / "netloom"
    stamp = dest_dir / ".digest"
    if dest.is_symlink() or (dest.exists() and not dest.is_file()):
        dest.unlink()
    if dest.is_file() and dest.stat().st_size > 0 and stamp.exists() and stamp.read_text() == digest:
        st = dest.stat()
        if stat.S_ISREG(st.st_mode) and st.st_uid == 0:
            return dest
    env = sandbox_env()
    cache = Path("/tmp/go-build-cache")
    cache.mkdir(parents=True, exist_ok=True)
    os.chmod(cache, 0o700)
    env["GOCACHE"] = str(cache)
    env["GOMODCACHE"] = str(cache / "mod")
    env["HOME"] = str(cache)
    Path(env["GOMODCACHE"]).mkdir(parents=True, exist_ok=True)
    built = subprocess.run(
        [REAL_GO, "build", "-o", str(dest), "/app/cmd/netloom"],
        cwd=str(APP),
        env=env,
        capture_output=True,
        text=True,
        timeout=1800,
    )
    assert built.returncode == 0, "go build failed\n%s\n%s" % (built.stdout, built.stderr)
    os.chmod(dest, 0o755)
    try:
        os.chown(dest, 0, 0)
    except PermissionError:
        pass
    stamp.write_text(digest)
    try:
        os.chown(stamp, 0, 0)
        os.chmod(stamp, 0o600)
    except PermissionError:
        pass
    return dest


def _stage_go_run_binary() -> Path:
    built = _compiled_netloom()
    token = str(int.from_bytes(os.urandom(4), "big"))
    stage_dir = Path("/tmp/go-build%s/b001/exe" % token)
    stage_dir.mkdir(parents=True, exist_ok=True)
    for path in (stage_dir, stage_dir.parent, stage_dir.parent.parent):
        os.chmod(path, 0o755)
        try:
            os.chown(path, 0, 0)
        except PermissionError:
            pass
    dest = stage_dir / "netloom"
    shutil.copy2(built, dest)
    os.chmod(dest, 0o755)
    try:
        os.chown(dest, 0, 0)
    except PermissionError:
        pass
    return dest


def _install_go_shim() -> None:
    exe = _stage_go_run_binary()
    VF_BIN.mkdir(parents=True, exist_ok=True)
    os.chmod(VF_BIN, 0o755)
    try:
        os.chown(VF_BIN, 0, 0)
    except PermissionError:
        pass
    body = (
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        'if [ "${1-}" = run ] && [ "${2-}" = /app/cmd/netloom ]; then\n'
        "  shift 2\n"
        "  exec %s \"$@\"\n"
        "fi\n"
        "exec %s \"$@\"\n"
    ) % (shlex.quote(str(exe)), shlex.quote(REAL_GO))
    tmp = VF_BIN / ".go.tmp"
    tmp.write_text(body)
    os.chmod(tmp, 0o755)
    try:
        os.chown(tmp, 0, 0)
    except PermissionError:
        pass
    tmp.replace(VF_BIN / "go")


def _readable_bytes(path: Path) -> bytes | None:
    try:
        if path.is_file() and not path.is_symlink():
            return path.read_bytes()
    except OSError:
        return None
    return None


def _assert_input_unchanged(path: Path, before: bytes | None) -> None:
    if before is None:
        return
    after = _readable_bytes(path)
    assert after == before, "candidate mutated %s" % path


def _under_publish_root(directory: Path) -> bool:
    try:
        resolved = directory.resolve()
    except OSError:
        return False
    text = str(resolved)
    return text == "/app" or text.startswith("/app/") or text == "/work" or text.startswith("/work/")


def invoke_netloom(
    journal: Path,
    regions: Path,
    ordinance: Path,
    matrix: Path,
    graph: Path,
    attest: Path,
    unlink_outputs: bool = True,
) -> subprocess.CompletedProcess:
    for path in (graph, attest):
        if unlink_outputs:
            if path.exists() or path.is_symlink():
                path.unlink()
        elif path.is_symlink():
            pass
        elif path.is_file():
            os.chmod(path, 0o644)
            try:
                os.chown(path, SANDBOX_UID, SANDBOX_GID)
            except PermissionError:
                pass
    graph.parent.mkdir(parents=True, exist_ok=True)
    attest.parent.mkdir(parents=True, exist_ok=True)
    for directory in (graph.parent, attest.parent):
        if not _under_publish_root(directory):
            continue
        try:
            os.chown(directory, SANDBOX_UID, SANDBOX_GID)
        except PermissionError:
            pass
        grand = directory.parent
        if _under_publish_root(grand):
            try:
                os.chmod(grand, os.stat(grand).st_mode | 0o0777)
            except PermissionError:
                pass
    ord_before = _readable_bytes(ordinance)
    matrix_before = _readable_bytes(matrix)
    journal_before = _readable_bytes(journal)
    _install_go_shim()
    env = sandbox_env()
    cmd = DROP + [
        "go",
        "run",
        "/app/cmd/netloom",
        "--journal",
        str(journal),
        "--modules",
        str(regions),
        "--ordinance",
        str(ordinance),
        "--matrix",
        str(matrix),
        "--graph",
        str(graph),
        "--attest",
        str(attest),
    ]
    out_fd, out_name = tempfile.mkstemp(prefix="kb-out-", dir="/tmp")
    err_fd, err_name = tempfile.mkstemp(prefix="kb-err-", dir="/tmp")
    os.fchmod(out_fd, 0o600)
    os.fchmod(err_fd, 0o600)
    proc = None
    try:
        with os.fdopen(out_fd, "w") as out_fh, os.fdopen(err_fd, "w") as err_fh:
            proc = subprocess.Popen(
                cmd,
                cwd=str(APP),
                stdout=out_fh,
                stderr=err_fh,
                text=True,
                env=env,
                start_new_session=True,
            )
            try:
                proc.wait(timeout=1800)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                raise
    finally:
        if proc is not None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass
        subprocess.run(["pkill", "-KILL", "-u", str(SANDBOX_UID)], capture_output=True, check=False)
    stdout = Path(out_name).read_text()
    stderr = Path(err_name).read_text()
    for name in (out_name, err_name):
        try:
            os.unlink(name)
        except OSError:
            pass
    result = subprocess.CompletedProcess(args=cmd, returncode=proc.returncode if proc else -1, stdout=stdout, stderr=stderr)
    _assert_input_unchanged(ordinance, ord_before)
    _assert_input_unchanged(matrix, matrix_before)
    _assert_input_unchanged(journal, journal_before)
    return result


def run_netloom(
    journal: Path,
    regions: Path,
    ordinance: Path,
    matrix: Path,
    graph: Path,
    attest: Path,
    unlink_outputs: bool = True,
) -> subprocess.CompletedProcess:
    result = invoke_netloom(
        journal,
        regions,
        ordinance,
        matrix,
        graph,
        attest,
        unlink_outputs=unlink_outputs,
    )
    log = "netloom exited %d\nstdout:\n%s\nstderr:\n%s" % (result.returncode, result.stdout, result.stderr)
    assert result.returncode == 0, log
    assert "panic:" not in log.lower(), log
    assert "missing required flags" not in result.stderr, result.stderr
    assert_stream_safe(result.stdout + "\n" + result.stderr)
    return result


def assert_graph_indent(raw: bytes) -> None:
    assert raw.endswith(b"\n")
    assert b"\t" not in raw
    lines = raw.decode().splitlines()
    assert lines[0] == "{"
    assert lines[-1] == "}"
    closer = re.compile(r"[}\]],?$")
    empty_box = re.compile(r'"[^"]*"\s*:\s*(?:\{\}|\[\])\s*,?$')
    open_box = re.compile(r'"[^"]*"\s*:\s*[\[{]$')
    for line in lines:
        stripped = line.lstrip(" ")
        pad = len(line) - len(stripped)
        assert line == (" " * pad) + stripped
        assert pad in (0, 2, 4, 6), line
        if stripped in {"}", "},"}:
            assert pad in (0, 4), line
            continue
        if stripped in {"]", "],"}:
            assert pad == 2, line
            continue
        if stripped in {"{", "["}:
            assert pad in (0, 2, 4), line
            continue
        if re.match(r'"[^"]*"\s*:\s*"', stripped):
            assert pad in (2, 6), line
            continue
        if closer.search(stripped):
            assert empty_box.match(stripped), line
            assert pad == 2, line
            continue
        if stripped.endswith("{") or stripped.endswith("["):
            assert open_box.match(stripped), line
            assert pad == 2, line
            continue
        assert pad in (2, 6), line


BASE_MATRIX = (
    "kind,token,payload\n"
    "SETTLE,days,14\n"
    "ROLE,core,incremental\n"
    "ROLE,io,incremental\n"
    "ROLE,debug,flatten\n"
    "ROLE,scan,flatten\n"
    "LIB,2,2024-01-01\n"
    "LIB,4,2024-03-01\n"
    "REPAIR,GHOST_MAP,drop-checkpoint\n"
    "REPAIR,LIB_MISBIND,rebind-lib\n"
    "REPAIR,ANCESTOR_GAP,relink-ancestor\n"
    "REPAIR,AREA_REGRESS,remap-area\n"
    "REPAIR,STALE_LIB,promote-lib\n"
    "REPAIR,CLKGT_SPLIT,rebind-icg\n"
)


def _desk_token(name: str) -> str:
    return "desk-" + sha256_bytes(name.encode())[:12]


def _tiny_audit(name, journal, regions, matrix_text=None):
    token = _desk_token(name)
    src = WORK / token
    if src.exists():
        shutil.rmtree(src)
    hist = src / "history"
    module_dir = src / "netlist" / "modules"
    hist.mkdir(parents=True)
    module_dir.mkdir(parents=True)
    (hist / "synth-epochs.ndjson").write_text("".join(journal))
    for filename, body in regions:
        (module_dir / filename).write_text(body)
    (module_dir / "junk.cfg").write_text("not a module record\n")
    extra = module_dir / "poison.nlg.bak"
    if not extra.exists():
        extra.write_text("MODULE poison\nPART poison-ci\nLANE incremental\nICG icg_x9y9\nPARENT ckpt-fake\nWHEN 2024-01-02\n")
    matrix_path = WORK / (token + "-matrix.csv")
    matrix_path.write_text(matrix_text or BASE_MATRIX)
    out_graph = WORK / (token + "-graph.json")
    out_attest = WORK / (token + "-attest.npg")
    run_netloom(
        hist / "synth-epochs.ndjson",
        module_dir,
        APP / "canon" / "synth-ordinance.txt",
        matrix_path,
        out_graph,
        out_attest,
    )
    raw_graph = read_guarded(out_graph, WORK)
    assert_graph_indent(raw_graph)
    led = json.loads(raw_graph)
    assert set(led.keys()) == {"desk", "findings", "patches"}
    assert led["desk"] == "KilnbridgeNetloom"
    assert isinstance(led["findings"], list)
    assert isinstance(led["patches"], list)
    matrix_body = matrix_text or BASE_MATRIX
    assert_repairs_for(led, matrix_body)
    assert_attest_matches(read_guarded(out_attest, WORK).decode(), led, journal)
    return led


def assert_ordinance_failure(result, graph: Path, attest: Path) -> None:
    assert result.returncode == 2
    assert auditor_stderr(result.stderr) != ""
    assert_stream_safe(result.stdout + "\n" + result.stderr)
    assert not graph.exists()
    assert not attest.exists()


def _sandbox_own(path: Path) -> None:
    try:
        os.chown(path, SANDBOX_UID, SANDBOX_GID)
    except PermissionError:
        pass


__all__ = [name for name in globals() if not name.startswith("__")]
