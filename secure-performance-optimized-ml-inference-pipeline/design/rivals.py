#!/usr/bin/env python3
"""Executable rival sweep: patch reference, rebuild+grade inside Linux container.

Host macOS cannot exec the Linux binary; build and surface run in one docker
invocation. A patch that no longer applies STOPS the sweep.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOL = ROOT / "solution"
ENV = ROOT / "environment" / "kelso"
IMAGE = (
    "public.ecr.aws/docker/library/golang:1.24-bookworm@"
    "sha256:1a6d4452c65dea36aac2e2d606b01b4a029ec90cc1ae53890540ce6173ea77ac"
)

RIVALS: list[tuple[str, str, str, str, str]] = [
    (
        "auth_equal_fold_bearer",
        "S12",
        "internal/scorekit/scorekit.go",
        'func (k *Kit) AuthOK(header string) bool {\n'
        '\tconst p = "Bearer "\n'
        "\tif !strings.HasPrefix(header, p) {\n"
        "\t\treturn false\n"
        "\t}\n"
        "\tgot := header[len(p):]\n"
        "\t// Exact token match: rejects suffixes, tabs, and extra spaces after Bearer.\n"
        "\tif got != k.Token {\n"
        "\t\treturn false\n"
        "\t}\n"
        "\tsum := sha256.Sum256([]byte(got))\n"
        "\treturn hex.EncodeToString(sum[:]) == k.TokenH\n"
        "}\n",
        'func (k *Kit) AuthOK(header string) bool {\n'
        '\tparts := strings.SplitN(strings.TrimSpace(header), " ", 2)\n'
        '\tif len(parts) != 2 || !strings.EqualFold(parts[0], "Bearer") {\n'
        "\t\treturn false\n"
        "\t}\n"
        "\tgot := strings.TrimSpace(parts[1])\n"
        "\tif got != k.Token {\n"
        "\t\treturn false\n"
        "\t}\n"
        "\tsum := sha256.Sum256([]byte(got))\n"
        "\treturn hex.EncodeToString(sum[:]) == k.TokenH\n"
        "}\n",
    ),
    (
        "logit_div_std_sq",
        "S6",
        "internal/scorekit/scorekit.go",
        "\t\tz := (x[i] - k.W.Mean[i]) / k.W.Std[i]\n",
        "\t\tz := (x[i] - k.W.Mean[i]) / (k.W.Std[i] * k.W.Std[i])\n",
    ),
    (
        "score_chunk_last_wins",
        "S11",
        "internal/scorekit/scorekit.go",
        "func (k *Kit) ScoreChunk(apps []Application) []int64 {\n"
        "\tout := make([]int64, len(apps))\n"
        "\tfor i, app := range apps {\n"
        "\t\tlocal := Application{ID: app.ID, X: append([]float64(nil), app.X...)}\n"
        "\t\tout[i] = k.ScoreOne(local)\n"
        "\t}\n"
        "\treturn out\n"
        "}\n",
        "func (k *Kit) ScoreChunk(apps []Application) []int64 {\n"
        "\tout := make([]int64, len(apps))\n"
        "\tif len(apps) == 0 {\n\t\treturn out\n\t}\n"
        "\tlast := apps[len(apps)-1]\n"
        "\ts := k.ScoreOne(Application{ID: last.ID, X: append([]float64(nil), last.X...)})\n"
        "\tfor i := range apps {\n\t\tout[i] = s\n\t}\n"
        "\treturn out\n"
        "}\n",
    ),
    (
        "cache_key_by_id",
        "S10",
        "internal/scorekit/scorekit.go",
        "func (k *Kit) ScoreOne(app Application) int64 {\n"
        "\tkey := k.cacheKey(app.X)\n",
        "func (k *Kit) ScoreOne(app Application) int64 {\n"
        '\tkey := "id=" + app.ID\n',
    ),
    (
        "parse_allows_extra",
        "S1",
        "internal/scorekit/scorekit.go",
        '\t\tif _, ok := k.Schema.FieldIndex[name]; !ok {\n'
        '\t\t\treturn Application{}, fmt.Errorf("unknown")\n'
        "\t\t}\n",
        "\t\t_ = name\n",
    ),
    (
        "content_type_equal_fold",
        "S13",
        "internal/httpapi/httpapi.go",
        '\tif strings.Trim(parts[0], " \\t") != "application/json" {\n'
        "\t\treturn false\n"
        "\t}\n",
        '\tif !strings.EqualFold(strings.Trim(parts[0], " \\t"), "application/json") {\n'
        "\t\treturn false\n"
        "\t}\n",
    ),
    (
        "gate_ignores_selfcheck_rc",
        "S18",
        "ops/ci-gate.sh",
        '"$ROOT/bin/ridgecast" selfcheck\n'
        "sc=$?\n"
        "if [ \"$sc\" -ne 0 ]; then\n"
        "\texit \"$sc\"\n"
        "fi\n",
        '"$ROOT/bin/ridgecast" selfcheck\n'
        "sc=$?\n"
        "# rival: continue after failing selfcheck\n",
    ),
]

SURFACE = textwrap.dedent(
    r"""
    #!/usr/bin/env python3
    import json, os, socket, subprocess, time
    from http.client import HTTPConnection
    from pathlib import Path
    tree = Path('/app')
    bin_path = tree / 'bin' / 'ridgecast'
    results = {}

    def run(args, timeout=60):
        return subprocess.run([str(bin_path), *args], cwd=str(tree), capture_output=True, text=True, timeout=timeout)

    sc = run(['selfcheck'])
    results['selfcheck_ok'] = sc.returncode == 0 and 'selfcheck ok' in sc.stdout
    mean_row = {"id":"M","tenure_months":48,"util_ratio":0.35,"delinq_24":0.8,"inq_6":1.2,"revol_bal":8500,"open_trades":7}
    intake = tree / '_in.jsonl'; intake.write_text(json.dumps(mean_row)+'\n')
    out = tree / '_out.jsonl'
    r = run(['score','--intake',str(intake),'--out',str(out)])
    ok = False
    if r.returncode == 0 and out.exists():
        ok = json.loads(out.read_text().splitlines()[0]).get('score_micros') == 389361
    results['mean_score'] = ok
    bad = tree / '_bad.jsonl'; bad.write_text(json.dumps({**mean_row,'id':'X','extra':1})+'\n')
    br = run(['score','--intake',str(bad),'--out',str(tree/'_bout.jsonl')])
    results['rejects_extra'] = br.returncode != 0
    batch = tree / '_batch.jsonl'
    batch.write_text('\n'.join(json.dumps({**mean_row,'id':f'B{i}','tenure_months':t}) for i,t in enumerate((48,12,72)))+'\n')
    kv_lines = []
    for line in (tree/'deploy'/'sidecar.kv').read_text().splitlines():
        if line.startswith('batch_limit '): kv_lines.append('batch_limit 3')
        elif line.startswith('listen '): continue
        else: kv_lines.append(line)
    sock = socket.socket(); sock.bind(('127.0.0.1',0)); port = sock.getsockname()[1]; sock.close()
    kv_lines.append(f'listen 127.0.0.1:{port}')
    kv = tree / '_kv'; kv.write_text('\n'.join(kv_lines)+'\n')
    br2 = run(['score','--config',str(kv),'--intake',str(batch),'--out',str(tree/'_bout2.jsonl')])
    isol = False
    if br2.returncode == 0:
        got = [json.loads(l)['score_micros'] for l in (tree/'_bout2.jsonl').read_text().splitlines()]
        isol = len(set(got)) == 3
    results['batch_isolated'] = isol
    token = (tree/'deploy'/'floor.token').read_text().strip()
    proc = subprocess.Popen([str(bin_path),'serve','--config',str(kv)], cwd=str(tree), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    ready = False
    for _ in range(80):
        if proc.poll() is not None: break
        try:
            c = HTTPConnection('127.0.0.1', port, timeout=0.2); c.request('GET','/v1/health'); ready = c.getresponse().status == 200; c.close()
            if ready: break
        except Exception: pass
        time.sleep(0.05)
    bearer_exact = ct_exact = cache_feat = False
    if ready:
        def post(body, authorization, content_type='application/json'):
            c = HTTPConnection('127.0.0.1', port, timeout=5)
            c.request('POST','/v1/score', body=json.dumps(body).encode(), headers={'Authorization':authorization,'Content-Type':content_type})
            resp = c.getresponse(); data = resp.read(); c.close()
            try: parsed = json.loads(data) if data else {}
            except Exception: parsed = {}
            return resp.status, parsed
        st,_ = post(mean_row, f'bearer {token}'); bearer_exact = st == 401
        st,_ = post({**mean_row,'id':'CT'}, f'Bearer {token}', 'Application/JSON'); ct_exact = st == 400
        st1,b1 = post({**mean_row,'id':'CACHE'}, f'Bearer {token}')
        st2,b2 = post({**mean_row,'id':'CACHE','tenure_months':12}, f'Bearer {token}')
        cache_feat = st1==200 and st2==200 and b1.get('score_micros') != b2.get('score_micros')
    if proc.poll() is None:
        proc.kill(); proc.wait(timeout=5)
    results['bearer_exact'] = bearer_exact
    results['content_type_exact'] = ct_exact
    results['cache_not_id'] = cache_feat
    coef = tree/'weights'/'ridge-v3.coef'; bak = coef.read_bytes()
    coef.write_bytes(bak.replace(b'bias -0.450000000', b'bias 0.000000000', 1))
    gate = subprocess.run(['bash', str(tree/'ops'/'ci-gate.sh')], cwd=str(tree), capture_output=True, text=True, timeout=180)
    results['gate_fail_fast'] = gate.returncode != 0 and 'selfcheck ok' not in gate.stdout
    coef.write_bytes(bak)
    print(json.dumps(results))
    """
).strip()


def _build_tree(dest: Path, patch: tuple[str, str, str, str, str] | None) -> None:
    shutil.copytree(ENV, dest, dirs_exist_ok=True)
    (dest / "bin").mkdir(parents=True, exist_ok=True)
    (dest / "out").mkdir(parents=True, exist_ok=True)
    for rel in (
        "internal/scorekit/scorekit.go",
        "internal/httpapi/httpapi.go",
        "internal/tickbench/tickbench.go",
        "internal/ridgeio/ridgeio.go",
        "cmd/ridgecast/ridgecast.go",
        "ops/ci-gate.sh",
    ):
        (dest / rel).write_bytes((SOL / rel).read_bytes())
    (dest / "_surface.py").write_text(SURFACE + "\n")
    if patch is None:
        return
    _, _, rel, old, new = patch
    path = dest / rel
    text = path.read_text()
    if text.count(old) != 1:
        raise RuntimeError(f"patch {patch[0]} apply count={text.count(old)} (must be 1)")
    path.write_text(text.replace(old, new, 1))


def _docker_grade(tree: Path) -> dict[str, bool]:
    r = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{tree}:/app",
            "-w",
            "/app",
            IMAGE,
            "bash",
            "-c",
            "set -euo pipefail; "
            "export PATH=/usr/local/go/bin:/go/bin:$PATH; "
            "go build -o /app/bin/ridgecast ./cmd/ridgecast; "
            "python3 /app/_surface.py",
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if r.returncode != 0:
        raise RuntimeError(f"docker grade failed:\n{r.stdout}\n{r.stderr}")
    for line in reversed(r.stdout.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            return {k: bool(v) for k, v in json.loads(line).items()}
    raise RuntimeError(f"no JSON results in:\n{r.stdout}")


def main() -> None:
    if shutil.which("docker") is None:
        raise SystemExit("docker required for rival sweep on this host")

    with tempfile.TemporaryDirectory(prefix="kelso-ref-") as td:
        ref_tree = Path(td) / "ref"
        _build_tree(ref_tree, None)
        control = _docker_grade(ref_tree)
        if not all(control.values()):
            print("CONTROL FAIL (reference must pass surface):", control)
            raise SystemExit(1)
        print("control PASS", control)

    survivors: list[str] = []
    for rival in RIVALS:
        name = rival[0]
        with tempfile.TemporaryDirectory(prefix=f"kelso-{name}-") as td:
            tree = Path(td) / "t"
            try:
                _build_tree(tree, rival)
            except RuntimeError as e:
                print(f"STOP: {e}")
                raise SystemExit(1) from e
            got = _docker_grade(tree)
            failed = [k for k, ok in got.items() if not ok]
            print(f"{name} ({rival[1]}): failed_checks={failed or 'NONE'} full={got}")
            if not failed:
                survivors.append(name)

    print(f"rivals {len(RIVALS)}; survivors {len(survivors)}")
    if survivors:
        print("SURVIVORS (unenforced clauses):", survivors)
        raise SystemExit(1)
    print("rival sweep: 0 survivors")


if __name__ == "__main__":
    main()
