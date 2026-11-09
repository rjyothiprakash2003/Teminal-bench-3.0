"""Sandbox runners and sealed holdouts."""
from __future__ import annotations
import json, math, os, re, secrets, shutil, subprocess, tempfile, time
from http.client import HTTPConnection
from pathlib import Path

APP, BIN = Path("/app"), Path("/app/bin/ridgecast")
SCORES, LEDGER = APP / "out" / "week41-scores.jsonl", APP / "out" / "bench-ledger.json"
CI_GATE, SIDECAR, TOKEN_FILE = APP / "ops" / "ci-gate.sh", APP / "deploy" / "sidecar.kv", APP / "deploy" / "floor.token"
HOLDOUT, WORK, UID = Path("/tests/holdout"), Path("/work/out"), 12000
SCHEMA = ("tenure_months", "util_ratio", "delinq_24", "inq_6", "revol_bal", "open_trades")
LEDGER_KEYS = (
    "total_requests", "total_ticks", "throughput", "avg_latency_ticks",
    "p95_ticks", "p99_ticks", "cpu_units", "memory_units",
)
X_RIDGE = ("x-ridge-model", "x-ridge-token", "x-ridge-rev")
_HOLD = {p.name: p.read_bytes() for p in HOLDOUT.iterdir() if p.is_file()}
COEF_B, SCALE_B = _HOLD["ridge-v3.coef"], _HOLD["ridge-v3.scale"]
# Holdout expects only for sealed intakes the agent never sees (xi, nu).
# week41 / bench expects for agent-visible fixtures are minted below from the
# independent sealed weights + live fixture bytes — never shipped as goldens.
XI, NU = json.loads(_HOLD["xi-expected.json"]), json.loads(_HOLD["nu-expected.json"])
DIGESTS = json.loads(_HOLD["sealed-expected.json"])["digests"]
MODES = {k: int(v) for k, v in json.loads(_HOLD["sealed-expected.json"]).get("modes", {}).items()}
for _p in list(HOLDOUT.iterdir()):
    if _p.is_file():
        try: _p.unlink()
        except FileNotFoundError: pass

def _oracle_micros_row(row):
    coef, scale = {}, {}
    for line in COEF_B.decode().splitlines():
        p = line.split()
        if len(p) == 3 and p[0] == "w": coef[p[1]] = float(p[2])
        elif len(p) == 2 and p[0] == "bias": coef["bias"] = float(p[1])
    for line in SCALE_B.decode().splitlines():
        p = line.split()
        if len(p) == 3 and p[0] in ("mean", "std"): scale["%s:%s" % (p[0], p[1])] = float(p[2])
    logit = coef["bias"]
    for name in SCHEMA: logit += coef[name] * ((float(row[name]) - scale["mean:%s" % name]) / scale["std:%s" % name])
    return int(math.floor((1.0 / (1.0 + math.exp(-logit))) * 1_000_000 + 0.5))

def _mint_week41():
    """Mint from sealed holdout intake bytes — never from candidate-writable /app/fixtures."""
    import hashlib as _hl
    wb = _HOLD["week41-intake.jsonl"]
    assert _hl.sha256(wb).hexdigest() == DIGESTS["fixtures/week41-intake.jsonl"]
    rows = []
    for line in wb.decode().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        rows.append({"id": row["id"], "score_micros": _oracle_micros_row(row)})
    assert len(rows) == 6
    return rows

def _mint_bench():
    """Recover S,C from sealed holdout bytes; never mint from candidate-writable /app."""
    import hashlib as _hl
    ab = _HOLD["bench-anchor.json"]
    rb = _HOLD["bench-recovery-loadspec.txt"]
    assert _hl.sha256(ab).hexdigest() == DIGESTS["policy/bench-anchor.json"]
    assert _hl.sha256(rb).hexdigest() == DIGESTS["policy/bench-recovery-loadspec.txt"]
    # Delivered loadspec is byte-identical to the sealed recovery spec.
    assert DIGESTS["bench/loadspec.txt"] == DIGESTS["policy/bench-recovery-loadspec.txt"]
    anchor = json.loads(ab.decode())
    recovery = rb.decode()
    specs = []
    for line in recovery.splitlines():
        line = line.strip()
        if not line.startswith("scenario "):
            continue
        parts = line.split()
        ri, bi = parts.index("repeat"), parts.index("batch")
        repeat, batch = int(parts[ri + 1]), int(parts[bi + 1])
        specs.append((batch, repeat * batch))
    n_solo = sum(c for b, c in specs if b == 1)
    n_coal = sum(c for b, c in specs if b > 1)
    total, n_req = int(anchor["total_ticks"]), int(anchor["total_requests"])
    p95, p99 = int(anchor["p95_ticks"]), int(anchor["p99_ticks"])
    avg = int(anchor["avg_latency_ticks"])
    sc = None
    for s in {p95, p99}:
        rem = total - n_solo * s
        if rem <= 0 or n_coal == 0 or rem % n_coal != 0:
            continue
        c = rem // n_coal
        if 1 <= c < s:
            sc = (s, c)
            break
    assert sc is not None, "S/C recovery failed"
    s, c = sc
    load = recovery
    costs, max_b = [], 1
    for line in load.splitlines():
        line = line.strip()
        if not line.startswith("scenario "):
            continue
        parts = line.split()
        ri, bi = parts.index("repeat"), parts.index("batch")
        repeat, batch = int(parts[ri + 1]), int(parts[bi + 1])
        cost = s if batch == 1 else c
        costs.extend([cost] * (repeat * batch))
        max_b = max(max_b, batch)
    n, tot = len(costs), sum(costs)
    ordered = sorted(costs)
    def pct(numer, denom):
        idx = (numer * n + denom - 1) // denom - 1
        return ordered[min(max(idx, 0), n - 1)]
    assert n == n_req and tot == total
    assert (tot + n // 2) // n == avg
    assert pct(19, 20) == p95 and pct(99, 100) == p99
    return {
        "total_requests": n,
        "total_ticks": tot,
        "throughput": (1000000 * n + tot // 2) // tot,
        "avg_latency_ticks": (tot + n // 2) // n,
        "p95_ticks": pct(19, 20),
        "p99_ticks": pct(99, 100),
        "cpu_units": tot,
        "memory_units": 64 + 4 * max_b,
    }

WEEK41, BENCH = _mint_week41(), _mint_bench()

def drop(cmd): return ["setpriv", f"--reuid={UID}", f"--regid={UID}", "--clear-groups", "--no-new-privs", *cmd]
def env():
    e = {**os.environ, "HOME": "/work", "GOCACHE": "/work/gocache", "GOMODCACHE": "/work/gomod", "GOPATH": "/work/gopath", "PATH": "/usr/local/go/bin:/go/bin:" + os.environ.get("PATH", "")}
    # Drop inherited GOFLAGS; pin buildvcs off so cold rebuilds under strace stay short.
    e.pop("GOFLAGS", None)
    e["GOFLAGS"] = "-buildvcs=false"
    return e
def own(p):
    try: os.chown(p, UID, UID)
    except OSError: pass
def prep():
    for d in (APP / "out", APP / "bin"):
        if d.is_symlink() or d.is_file(): d.unlink()
    for d in (APP, APP / "bin", APP / "out", WORK, Path("/work/gocache"), Path("/work/gomod"), Path("/work/gopath")):
        if d.is_symlink() or d.is_file(): d.unlink()
        d.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(d), os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
        try:
            os.fchmod(fd, 0o777)
            try: os.fchown(fd, UID, UID)
            except OSError: pass
        finally: os.close(fd)
def rebuild():
    prep()
    if BIN.exists() or BIN.is_symlink(): BIN.unlink()
    go = "go" if shutil.which("go") else "/usr/local/go/bin/go"
    r = subprocess.run(drop([go, "build", "-o", str(BIN), "./cmd/ridgecast"]), cwd=str(APP), capture_output=True, text=True, timeout=180, env=env())
    assert r.returncode == 0, r.stderr
    return BIN
def run(args, timeout=120, cwd=None):
    prep()
    return subprocess.run(drop([str(BIN), *args]), cwd=str(cwd or APP), capture_output=True, text=True, timeout=timeout, env=env())
def _norm_app_path(p):
    if not p:
        return ""
    p = os.path.normpath(p)
    if not os.path.isabs(p):
        p = os.path.normpath(os.path.join("/app", p))
    return p

def _flag_val(args, name):
    eq = name + "="
    for i, a in enumerate(args):
        if a == name and i + 1 < len(args):
            return args[i + 1]
        if a.startswith(eq):
            return a[len(eq):]
    return None

def _execve_events(text):
    events = []
    for line in text.splitlines():
        if "execve" not in line or re.search(r"=\s*-", line):
            continue
        pid_m = re.search(r"\[pid\s+(\d+)\]", line)
        pm = re.search(r'execve(?:at)?\((?:AT_FDCWD,\s*)?"([^"]*)"', line)
        m = re.search(r'\[((?:"(?:\\.|[^"\\])*"(?:,\s*)?)*)\]', line)
        if not m:
            continue
        args = []
        for a in re.findall(r'"((?:\\.|[^"\\])*)"', m.group(1)):
            try: args.append(bytes(a, "utf-8").decode("unicode_escape"))
            except Exception: args.append(a)
        events.append((pid_m.group(1) if pid_m else "0", _norm_app_path(pm.group(1)) if pm else "", args))
    return events

def parse_ridge_calls(text):
    """Credit /app/bin/ridgecast invocations from strace -f execve lines.

    Drop only an *immediate* same-PID re-exec into a different path (wrapper
    overlay). Do not scan all later same-PID events: after the process exits the
    kernel may reuse that PID for unrelated execves, which falsely dropped
    legitimate selfcheck/score/bench steps under load (oracle 2/3 flake).
    """
    events, calls = _execve_events(text), []
    for i, (pid, path, args) in enumerate(events):
        if path != "/app/bin/ridgecast" or not args:
            continue
        if i + 1 < len(events):
            npid, npath, _ = events[i + 1]
            if npid == pid and npath != "/app/bin/ridgecast":
                continue
        calls.append(args)
    return calls

def gate_ran(calls, sub, **want):
    defaults = {"spec": "bench/loadspec.txt", "out": "out/bench-ledger.json"} if sub == "bench" else {}
    for args in calls:
        if (args[1] if len(args) > 1 else "") != sub:
            continue
        if all(_norm_app_path(_flag_val(args, "--" + k) or defaults.get(k, "")) == _norm_app_path(p) for k, p in want.items()):
            return True
    return False

def gate_subs(calls):
    return [a[1] for a in calls if len(a) > 1]

def go_rebuilt_bin(text):
    """True when go built or installed /app/cmd/ridgecast (not a sibling .../cmd/ridgecast)."""
    def _is_app_ridgecast(pkg):
        p = pkg.rstrip("/")
        return p in ("./cmd/ridgecast", "cmd/ridgecast", "/app/cmd/ridgecast") or p.endswith("/app/cmd/ridgecast")

    for _, path, raw in _execve_events(text):
        if path != "/usr/local/go/bin/go" or not raw:
            continue
        action, pkgs, skip = None, [], False
        for a in raw[1:]:
            if skip:
                skip = False; continue
            if a in ("build", "install"):
                action = a; continue
            if a == "-o":
                skip = True; continue
            if a.startswith("-o=") and len(a) > 3:
                continue
            if a.startswith("-"):
                if a in ("-C", "-pkgdir", "-p", "-o"):
                    skip = True
                continue
            pkgs.append(a)
        if action not in ("build", "install"):
            continue
        if not any(_is_app_ridgecast(p) for p in pkgs):
            continue
        return True
    return False

def _open_paths(text):
    """Map PID -> set of opened paths from strace open/openat lines."""
    opened = {}
    for line in text.splitlines():
        if "openat(" not in line and "open(" not in line:
            continue
        if re.search(r"=\s*-", line):
            continue
        pid_m = re.search(r"\[pid\s+(\d+)\]", line)
        # openat(AT_FDCWD, "path", ...) or open("path", ...)
        pm = re.search(r'open(?:at)?\((?:AT_FDCWD,\s*)?"([^"]*)"', line)
        if not pm:
            continue
        pid = pid_m.group(1) if pid_m else "0"
        opened.setdefault(pid, set()).add(_norm_app_path(pm.group(1)))
    return opened

def gate_exec_stamp_ok(text, rebuild_tok):
    """Prove the live rebuild token ran during the gate (not a post-hoc ELF swap).

    Prefer the on-disk stamp file written by init() in the rebuilt binary. Strace
    open/openat PID attribution under -f is scheduling-sensitive on slow hosts;
    counting successful opens of the stamp path is only a secondary check.
    """
    stamp_path = APP / "out" / (".ridge-ran-" + rebuild_tok)
    if stamp_path.is_file() and rebuild_tok.encode() in stamp_path.read_bytes():
        return True
    # Fallback when the file was cleaned mid-gate: require stamp opens in the
    # strace log (path match), without per-PID attribution.
    stamp = _norm_app_path("/app/out/.ridge-ran-" + rebuild_tok)
    opens = 0
    for line in text.splitlines():
        if stamp not in line:
            continue
        if "openat(" not in line and "open(" not in line:
            continue
        if re.search(r"=\s*-", line):
            continue
        opens += 1
    ridge_n = sum(1 for _, path, args in _execve_events(text) if path == "/app/bin/ridgecast" and args)
    return ridge_n > 0 and opens >= ridge_n

def ridgecast_buildinfo_ok(path=BIN):
    """True when /app/bin/ridgecast is a Go main whose package path ends in cmd/ridgecast.

    Module path may be renamed from the starter; only the cmd/ridgecast main
    package suffix is required (gate provenance still checks the build argv).
    """
    go = "go" if shutil.which("go") else "/usr/local/go/bin/go"
    r = subprocess.run([go, "version", "-m", str(path)], capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        return False
    for line in r.stdout.splitlines():
        if not line.startswith("\tpath\t"):
            continue
        rest = line.split("\tpath\t", 1)[1]
        if rest == "cmd/ridgecast" or rest.endswith("/cmd/ridgecast"):
            return True
    return False

def rebuild_race():
    """Rebuild ridgecast with -race for verifier-controlled concurrency probes."""
    prep()
    if BIN.exists() or BIN.is_symlink(): BIN.unlink()
    go = "go" if shutil.which("go") else "/usr/local/go/bin/go"
    r = subprocess.run(
        drop([go, "build", "-race", "-o", str(BIN), "./cmd/ridgecast"]),
        cwd=str(APP), capture_output=True, text=True, timeout=180, env=env(),
    )
    assert r.returncode == 0, r.stderr + r.stdout
    own(BIN)
    return r

def run_ci_gate(extra_env=None):
    prep()
    try: os.chmod(CI_GATE, 0o755); os.chown(CI_GATE, UID, UID)
    except OSError: pass
    tdir = Path("/logs/verifier")
    tdir.mkdir(parents=True, exist_ok=True)
    tpath = tdir / ("gate-%s.strace" % secrets.token_hex(8))
    with tempfile.TemporaryDirectory(prefix="ci-") as td:
        o, e = Path(td) / "o", Path(td) / "e"
        ge = env()
        if extra_env:
            ge.update(extra_env)
        with open(o, "w") as of, open(e, "w") as ef:
            # 180s matches instruction.md ci-gate budget (includes rebuild under strace).
            # Trace execve only — open/openat volume on go build is large and
            # scheduling-sensitive; stamp provenance uses the on-disk stamp file.
            p = subprocess.run(
                ["strace", "-f", "-qq", "-s", "4096", "-e", "trace=execve,execveat", "-o", str(tpath), "--", *drop(["/bin/bash", str(CI_GATE)])],
                cwd=str(APP), stdout=of, stderr=ef, timeout=180, env=ge,
            )
        trace = tpath.read_text() if tpath.exists() else ""
        try: tpath.unlink()
        except FileNotFoundError: pass
        return p.returncode, o.read_text(), e.read_text(), parse_ridge_calls(trace), go_rebuilt_bin(trace), trace
def work(suf=""):
    prep(); p = WORK / (secrets.token_hex(8) + suf); own(WORK); return p
def stage(name):
    p = work(".bin"); p.write_bytes(_HOLD[name]); own(p); return p
def load_jsonl(path):
    rows = [json.loads(l) for l in path.read_text().splitlines()]
    for row in rows:
        assert set(row) == {"id", "score_micros"}
        assert type(row["id"]) is str and type(row["score_micros"]) is int
    return rows
def ledger_obj(path):
    """Parse ledger JSON; reject duplicate members (including unicode-escaped names)."""
    raw = path.read_bytes()
    text = raw.decode()
    seen = []
    def hook(pairs):
        seen.extend(pairs)
        return {k: v for k, v in pairs}
    obj = json.loads(text, object_pairs_hook=hook)
    assert len(seen) == 8, (len(seen), [k for k, _ in seen])
    assert sorted(k for k, _ in seen) == sorted(LEDGER_KEYS)
    assert set(obj) == set(LEDGER_KEYS), set(obj)
    for k in LEDGER_KEYS:
        assert k in obj and type(obj[k]) is int, k
    assert obj["cpu_units"] == obj["total_ticks"]
    return obj
def mean(extra=None):
    row = {"id": "M", "tenure_months": 48, "util_ratio": 0.35, "delinq_24": 0.8, "inq_6": 1.2, "revol_bal": 8500, "open_trades": 7}
    if extra: row.update(extra)
    return row
def write_intake(rows):
    p = work(".jsonl")
    if isinstance(rows, (bytes, bytearray)):
        p.write_bytes(rows)
    else:
        p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    own(p); return p

def stale(suf=".jsonl"):
    p = work(suf); p.write_text("stale\n"); return p

def stale_symlink(suf=".jsonl"):
    """Dangling symlink at a fresh --out path (lexists, not exists)."""
    p = work(suf)
    if p.exists() or p.is_symlink():
        p.unlink()
    target = work(suf + ".missing")
    if target.exists() or target.is_symlink():
        target.unlink()
    p.symlink_to(target)
    return p

def stale_dir(suf=".out"):
    """Nonempty directory path for --out cleanup coverage."""
    p = work(suf)
    if p.exists():
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()
    p.mkdir(parents=True, exist_ok=True)
    (p / "child").write_text("keep\n")
    own(p); own(p / "child")
    return p

def gone(r, *paths):
    assert r.returncode != 0
    for p in paths:
        assert not p.exists() and not p.is_symlink(), p
    assert_cli_clean(r.stdout, r.stderr)

def score(intake, out, cfg=None, timeout=120, cwd=None):
    cmd = ["score"]
    if cfg is not None:
        cmd += ["--config", str(cfg)]
    return run(cmd + ["--intake", str(intake), "--out", str(out)], timeout=timeout, cwd=cwd)

def illegal_rows():
    """Named illegal intakes covering every schema field and forbidden key."""
    rows = [
        {**mean({"id": "X"}), "extra": 1},
        {**mean({"id": "XS"}), "extra": "ignored"},
        {**mean({"id": "F"}), "internal_prior": 1},
        {**mean({"id": "FS"}), "internal_prior": "ignored"},
        {**mean({"id": "DB"}), "debug_bias": 1},
        {**mean({"id": "DBS"}), "debug_bias": "ignored"},
        {**mean({"id": "PR"}), "_probe": 1},
        {**mean({"id": "PRS"}), "_probe": "ignored"},
        {**mean({"id": "N"}), "tenure_months": -1},
        {**mean({"id": "TH"}), "tenure_months": 601},
        # Below-minimum for every numeric field (inclusive mins are zero except tenure).
        {**mean({"id": "U-"}), "util_ratio": -0.01},
        {**mean({"id": "D-"}), "delinq_24": -0.01},
        {**mean({"id": "I-"}), "inq_6": -0.01},
        {**mean({"id": "RB-"}), "revol_bal": -1},
        {**mean({"id": "OT-"}), "open_trades": -1},
        {**mean({"id": "U"}), "util_ratio": 9.0},
        {**mean({"id": "D21"}), "delinq_24": 21},
        # Fractional delinq above 20 must fail (not int-truncation then >20).
        {**mean({"id": "D205"}), "delinq_24": 20.5},
        {**mean({"id": "I16"}), "inq_6": 16},
        {**mean({"id": "RB"}), "revol_bal": 250001},
        {**mean({"id": "OT81"}), "open_trades": 81},
        {**mean({"id": "Z"}), "id": ""},
        {**mean({"id": "I7"}), "id": 7},
        {k: v for k, v in mean({"id": "NOID"}).items() if k != "id"},
    ]
    # Null / bool / numeric-string / array / object — every schema field, both interfaces.
    for field in SCHEMA:
        rows.append({**mean({"id": "NULL-%s" % field}), field: None})
        rows.append({**mean({"id": "BOOL-%s" % field}), field: True})
        rows.append({**mean({"id": "BOOLF-%s" % field}), field: False})
        rows.append({**mean({"id": "STR-%s" % field}), field: "0"})
        rows.append({**mean({"id": "ARR-%s" % field}), field: [0]})
        rows.append({**mean({"id": "OBJ-%s" % field}), field: {"x": 0}})
    base = mean({"id": "MISS"})
    for field in SCHEMA:
        rows.append({k: v for k, v in {**base, "id": "MISS-%s" % field}.items() if k != field})
    return rows

def bound_rows():
    """Inclusive schema minima and maxima for every numeric field."""
    return [
        mean({"id": " "}),
        mean({"id": "T0", "tenure_months": 0}),
        mean({"id": "TMAX", "tenure_months": 600}),
        mean({"id": "TF", "tenure_months": 48.5}),
        mean({"id": "U0", "util_ratio": 0}),
        mean({"id": "U25", "util_ratio": 2.5}),
        mean({"id": "D0", "delinq_24": 0}),
        mean({"id": "D20", "delinq_24": 20}),
        mean({"id": "I0", "inq_6": 0}),
        mean({"id": "I15", "inq_6": 15}),
        mean({"id": "R0", "revol_bal": 0}),
        mean({"id": "RMAX", "revol_bal": 250000}),
        mean({"id": "RF", "revol_bal": 8500.5}),
        mean({"id": "OT0", "open_trades": 0}),
        mean({"id": "OT80", "open_trades": 80}),
        mean({"id": "OTF", "open_trades": 7.5}),
    ]
def write_sidecar(batch_limit=1, omit=False, token_sha=None, coef=None, scale=None, token_file=None):
    p, lines = work(".kv"), []
    for line in SIDECAR.read_text().splitlines():
        if line.startswith("batch_limit "):
            if not omit: lines.append("batch_limit %s" % batch_limit)
        elif line.startswith("token_sha256 ") and token_sha is not None:
            lines.append("token_sha256 %s" % token_sha)
        elif line.startswith("coef ") and coef is not None:
            lines.append("coef %s" % coef)
        elif line.startswith("scale ") and scale is not None:
            lines.append("scale %s" % scale)
        elif line.startswith("token_file ") and token_file is not None:
            lines.append("token_file %s" % token_file)
        else: lines.append(line)
    p.write_text("\n".join(lines) + "\n"); own(p); return p
def token(): return TOKEN_FILE.read_text().strip()
def auth(ct="application/json", authorization=None):
    h = {"Authorization": authorization if authorization is not None else ("Bearer %s" % token())}
    if ct is not None: h["Content-Type"] = ct
    return h
def free_port():
    import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close(); return port
def stop(proc):
    if proc.poll() is None: proc.kill()
    try: proc.wait(timeout=5)
    except subprocess.TimeoutExpired: proc.kill(); proc.wait(timeout=5)
def wait_ready(proc, port, timeout=8.0):
    import select; deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None: return False
        try:
            c = HTTPConnection("127.0.0.1", port, timeout=0.25); c.request("GET", "/v1/health"); ok = c.getresponse().status == 200; c.close()
            if ok: return True
        except Exception: pass
        select.select([], [], [], 0.05)
    return False
def start_server(batch_limit=1, kv=None):
    prep(); port = free_port()
    if kv is None: kv = write_sidecar(batch_limit=batch_limit)
    lines = ["listen 127.0.0.1:%d" % port if l.startswith("listen ") else l for l in kv.read_text().splitlines()]
    kv.write_text("\n".join(lines) + "\n"); own(kv)
    proc = subprocess.Popen(drop([str(BIN), "serve", "--config", str(kv)]), cwd=str(APP), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env())
    if not wait_ready(proc, port): stop(proc); raise AssertionError("serve failed")
    return proc, port

def http_req(port, method, path, body=None, headers=None, timeout=10):
    raw = json.dumps(body).encode() if isinstance(body, dict) else body
    last = None
    for attempt in range(1, 6):
        c = None
        try:
            c = HTTPConnection("127.0.0.1", port, timeout=timeout)
            c.request(method, path, body=raw, headers=dict(headers or {}))
            resp = c.getresponse()
            data = resp.read()
            hdr = {k.lower(): v for k, v in resp.getheaders()}
            try:
                parsed = json.loads(data) if data else {}
            except json.JSONDecodeError:
                parsed = {"_raw": data.decode("utf-8", "replace")}
            return resp.status, hdr, parsed
        except (ConnectionError, OSError, TimeoutError) as e:
            last = e
            time.sleep(0.05 * attempt)
        finally:
            if c is not None:
                try:
                    c.close()
                except Exception:
                    pass
    raise last
def post(port, body, headers, timeout=10): return http_req(port, "POST", "/v1/score", body=body, headers=headers, timeout=timeout)

def no_x_ridge(hdr):
    for k in X_RIDGE:
        assert k not in hdr, k

def assert_err(body, code):
    assert set(body.keys()) == {"error", "message"} and body["error"] == code
    msg = body["message"]
    assert isinstance(msg, str)
    assert token() not in msg
    assert "ridge-v3.coef" not in msg and "ridge-v3.scale" not in msg

def assert_cli_clean(*parts):
    blob = "\n".join("" if p is None else str(p) for p in parts)
    assert token() not in blob
    assert "ridge-v3.coef" not in blob and "ridge-v3.scale" not in blob

def http_fail(status, hdr, body, code, want_status):
    assert status == want_status
    assert hdr.get("content-type") == "application/json"
    no_x_ridge(hdr)
    assert_err(body, code)

def oracle_micros(row):
    return _oracle_micros_row(row)

def oracle_micros_assets(row, coef_text, scale_text):
    """Mint micros from explicit coef/scale text (for live-asset mutation cases)."""
    coef, scale = {}, {}
    for line in coef_text.splitlines():
        p = line.split()
        if len(p) == 3 and p[0] == "w": coef[p[1]] = float(p[2])
        elif len(p) == 2 and p[0] == "bias": coef["bias"] = float(p[1])
    for line in scale_text.splitlines():
        p = line.split()
        if len(p) == 3 and p[0] in ("mean", "std"): scale["%s:%s" % (p[0], p[1])] = float(p[2])
    logit = coef["bias"]
    for name in SCHEMA:
        logit += coef[name] * ((float(row[name]) - scale["mean:%s" % name]) / scale["std:%s" % name])
    return int(math.floor((1.0 / (1.0 + math.exp(-logit))) * 1_000_000 + 0.5))
