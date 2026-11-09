"""Graded cases aligned to instruction.md and serving-contract.txt."""
from __future__ import annotations
import hashlib, json, os, secrets, shutil, stat, subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from ridge_helpers import *

FX, LS = "/app/fixtures/week41-intake.jsonl", "/app/bench/loadspec.txt"
OUT_SCORES, OUT_LEDGER = "/app/out/week41-scores.jsonl", "/app/out/bench-ledger.json"

def _assert_gate_products(calls):
    assert calls, "no /app/bin/ridgecast execve records from root strace"
    assert gate_ran(calls, "selfcheck"), calls
    assert gate_ran(calls, "score", intake=FX, out=OUT_SCORES), calls
    assert gate_ran(calls, "bench", spec=LS, out=OUT_LEDGER), calls
    subs = gate_subs(calls)
    # Contract: rebuild then selfcheck first; score and bench both run after a
    # successful selfcheck. Relative order of score vs bench is not required.
    assert subs.index("selfcheck") < subs.index("score"), subs
    assert subs.index("selfcheck") < subs.index("bench"), subs

def test_ci_gate_rebuild_selfcheck_and_fail_fast():
    fx, ls = APP / "fixtures" / "week41-intake.jsonl", APP / "bench" / "loadspec.txt"
    fx_b, ls_b = fx.read_bytes(), ls.read_bytes()
    coef = APP / "weights" / "ridge-v3.coef"
    scale = APP / "weights" / "ridge-v3.scale"
    coef_bak, scale_bak = coef.read_bytes(), scale.read_bytes()
    broken = APP / "cmd" / "ridgecast" / "zbroken.go"
    rebuild_tok = "RidgeRebuild_%s" % secrets.token_hex(12)
    hold = APP / "cmd" / "ridgecast" / "rebuild_hold.go"
    # Unique const compiled into the binary proves the invoked ELF is this rebuild
    # (not a swapped prebuilt). No requirement that the process inherit verifier env.
    hold.write_text(
        'package main\n'
        'import "os"\n'
        'var ridgeRebuildToken = "%s"\n'
        'func init() {\n'
        '\t// Keep the stamp reachable so the linker retains it in the ELF.\n'
        '\tif ridgeRebuildToken == "" {\n'
        '\t\tpanic("rebuild stamp missing")\n'
        '\t}\n'
        '\t// Each gated exec must open this token-named path (strace-correlated).\n'
        '\t_ = os.MkdirAll("/app/out", 0o755)\n'
        '\tf, err := os.OpenFile("/app/out/.ridge-ran-"+ridgeRebuildToken, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)\n'
        '\tif err == nil {\n'
        '\t\t_, _ = f.WriteString(ridgeRebuildToken + "\\n")\n'
        '\t\t_ = f.Close()\n'
        '\t}\n'
        '}\n' % rebuild_tok
    )
    try:
        for p in (SCORES, LEDGER, BIN):
            if p.exists() or p.is_symlink(): p.unlink()
        code, out, err, calls, rebuilt, trace = run_ci_gate()
        assert code == 0, err + out
        assert rebuilt
        assert BIN.is_file() and not BIN.is_symlink() and BIN.read_bytes()[:4] == b"\x7fELF"
        assert ridgecast_buildinfo_ok(BIN), "gate must exec the Go binary built from /app/cmd/ridgecast"
        blob = BIN.read_bytes()
        tokb = rebuild_tok.encode()
        assert tokb in blob and b"cmd/ridgecast" in blob
        assert gate_exec_stamp_ok(trace, rebuild_tok), "gated ridgecast PIDs must open rebuild stamp"
        assert load_jsonl(SCORES) == WEEK41
        assert len(WEEK41) == 6
        assert ledger_obj(LEDGER) == BENCH
        _assert_gate_products(calls)

        row = mean({"id": "LIVE-FX", "tenure_months": 120})
        fx.write_text(json.dumps(row) + "\n")
        for p in (SCORES, LEDGER):
            if p.exists(): p.unlink()
        code, out, err, calls, rebuilt, trace = run_ci_gate()
        assert code == 0, err + out
        got = load_jsonl(SCORES)
        assert got[0]["score_micros"] == oracle_micros(row)
        assert gate_exec_stamp_ok(trace, rebuild_tok)
        _assert_gate_products(calls)
        fx.write_bytes(fx_b)

        sc = run(["selfcheck"])
        assert sc.returncode == 0
        assert sc.stdout.splitlines() == ["offmean_micros 364705", "selfcheck ok"]
        sc2 = run(["selfcheck"])
        assert sc2.returncode == 0 and sc2.stdout == sc.stdout
        lab = mean({"id": "OFFLAB", "tenure_months": 72, "util_ratio": 0.36})
        inn, outp = write_intake([lab]), work(".jsonl")
        assert score(inn, outp).returncode == 0
        assert load_jsonl(outp)[0]["score_micros"] == 364705
        bad = run(["selfcheck", "extra"])
        assert bad.returncode != 0 and "selfcheck ok" not in bad.stdout
        # #45/#62: selfcheck rejects flags; util_ratio std mutation fails the off-mean lab inside selfcheck.
        assert run(["selfcheck", "--config", "/app/deploy/sidecar.kv"]).returncode != 0
        assert run(["selfcheck", "--"]).returncode != 0
        scale.write_bytes(scale_bak.replace(b"std util_ratio 0.200000000", b"std util_ratio 0.400000000", 1))
        fail_u = run(["selfcheck"])
        assert fail_u.returncode != 0 and "selfcheck ok" not in fail_u.stdout
        assert "offmean_micros" not in fail_u.stdout
        scale.write_bytes(scale_bak)

        scale.write_bytes(scale_bak.replace(b"std tenure_months 24.000000000", b"std tenure_months 48.000000000", 1))
        fail_t = run(["selfcheck"])
        assert fail_t.returncode != 0 and "selfcheck ok" not in fail_t.stdout
        assert "offmean_micros" not in fail_t.stdout
        moved = mean({"id": "TS", "tenure_months": 72})
        inn, outp = write_intake([moved]), work(".jsonl")
        assert score(inn, outp).returncode == 0
        assert load_jsonl(outp)[0]["score_micros"] != oracle_micros(moved)
        scale.write_bytes(scale_bak)

        # Off-mean lab must read live util_ratio coefficient (not a frozen copy).
        coef.write_bytes(coef_bak.replace(b"w util_ratio -2.400000000", b"w util_ratio -1.200000000", 1))
        fail_c = run(["selfcheck"])
        assert fail_c.returncode != 0 and "selfcheck ok" not in fail_c.stdout
        assert "offmean_micros" not in fail_c.stdout
        coef.write_bytes(coef_bak)
        coef.write_bytes(coef_bak.replace(b"w util_ratio -2.400000000", b"w util_ratio 0.000000000", 1))
        fail_c0 = run(["selfcheck"])
        assert fail_c0.returncode != 0 and "selfcheck ok" not in fail_c0.stdout
        assert "offmean_micros" not in fail_c0.stdout
        coef.write_bytes(coef_bak)

        # Off-mean lab must read live tenure_months coefficient (not delivered 0.015).
        coef.write_bytes(coef_bak.replace(b"w tenure_months 0.015000000", b"w tenure_months 0.030000000", 1))
        fail_tn = run(["selfcheck"])
        assert fail_tn.returncode != 0 and "selfcheck ok" not in fail_tn.stdout
        assert "offmean_micros" not in fail_tn.stdout
        coef.write_bytes(coef_bak)

        # Live scale means: labs use current means, so a mean shift must still succeed.
        scale.write_bytes(scale_bak.replace(b"mean delinq_24 0.800000000", b"mean delinq_24 1.800000000", 1))
        ok_mean = run(["selfcheck"])
        assert ok_mean.returncode == 0
        assert ok_mean.stdout.splitlines() == ["offmean_micros 364705", "selfcheck ok"]
        scale.write_bytes(scale_bak)

        # Off-mean lab must use live mean inq_6 (not a delivered literal 1.2).
        scale.write_bytes(scale_bak.replace(b"mean inq_6 1.200000000", b"mean inq_6 2.600000000", 1))
        ok_inq = run(["selfcheck"])
        assert ok_inq.returncode == 0
        assert ok_inq.stdout.splitlines() == ["offmean_micros 364705", "selfcheck ok"]
        scale.write_bytes(scale_bak)

        # Live means for revol_bal / open_trades must feed both labs (not delivered 8500 / 7).
        for old, new in (
            (b"mean revol_bal 8500.000000000", b"mean revol_bal 9500.000000000"),
            (b"mean open_trades 7.000000000", b"mean open_trades 9.000000000"),
        ):
            scale.write_bytes(scale_bak.replace(old, new, 1))
            ok_m = run(["selfcheck"])
            assert ok_m.returncode == 0, (old, ok_m.stdout, ok_m.stderr)
            assert ok_m.stdout.splitlines() == ["offmean_micros 364705", "selfcheck ok"]
            scale.write_bytes(scale_bak)

        # Mean lab must read live tenure_months / util_ratio means (not delivered 48 / 0.35).
        # Tiny shifts keep off-mean micros at 364705 while breaking a frozen-mean logit.
        scale.write_bytes(scale_bak.replace(b"mean tenure_months 48.000000000", b"mean tenure_months 48.000000001", 1))
        ok_tm = run(["selfcheck"])
        assert ok_tm.returncode == 0
        assert ok_tm.stdout.splitlines() == ["offmean_micros 364705", "selfcheck ok"]
        scale.write_bytes(scale_bak)
        scale.write_bytes(scale_bak.replace(b"mean util_ratio 0.350000000", b"mean util_ratio 0.350000001", 1))
        ok_um = run(["selfcheck"])
        assert ok_um.returncode == 0
        assert ok_um.stdout.splitlines() == ["offmean_micros 364705", "selfcheck ok"]
        scale.write_bytes(scale_bak)

        # #61: bias shift that keeps mean micros 389361 but breaks exact logit == -0.45.
        coef.write_bytes(coef_bak.replace(b"bias -0.450000000", b"bias -0.450000001", 1))
        fail_b = run(["selfcheck"])
        assert fail_b.returncode != 0 and "selfcheck ok" not in fail_b.stdout
        assert "offmean_micros" not in fail_b.stdout
        inn, outp = write_intake([mean({"id": "TINYB"})]), work(".jsonl")
        assert score(inn, outp).returncode == 0
        assert load_jsonl(outp)[0]["score_micros"] == 389361
        coef.write_bytes(coef_bak)

        coef.write_bytes(coef_bak.replace(b"bias -0.450000000", b"bias 0.000000000", 1))
        want = run(["selfcheck"]).returncode
        assert want != 0
        sentinel = b"score-must-not-run\n"
        SCORES.write_bytes(sentinel); LEDGER.write_bytes(b"bench-must-not-run\n")
        code, out, err, calls, rebuilt, trace = run_ci_gate()
        # Contract: non-zero gate status and no score/bench after failing selfcheck.
        # Exact selfcheck exit-status propagation is not required.
        # Stale product files may be removed at gate startup — that is not a contract fault.
        assert code != 0
        assert_cli_clean(out, err)
        assert "score" not in gate_subs(calls) and "bench" not in gate_subs(calls)
        assert gate_exec_stamp_ok(trace, rebuild_tok)
        if SCORES.exists():
            assert SCORES.read_bytes() == sentinel
        if LEDGER.exists():
            assert LEDGER.read_bytes() == b"bench-must-not-run\n"
        coef.write_bytes(coef_bak)

        fx.write_text("{}\n")
        code, out, err, calls, rebuilt, trace = run_ci_gate()
        assert code != 0 and "selfcheck ok" in out
        assert gate_ran(calls, "selfcheck")
        assert gate_exec_stamp_ok(trace, rebuild_tok)
        # Score failure requires non-zero gate status; continuing to bench is permitted, not required.
        assert "score" in gate_subs(calls)
        fx.write_bytes(fx_b)

        # #53: empty loadspec fails bench; gate must be nonzero (score may still have run).
        ls.write_text("end\n")
        code, out, err, calls, rebuilt, trace = run_ci_gate()
        assert code != 0
        assert gate_ran(calls, "selfcheck")
        assert gate_ran(calls, "bench", spec=LS, out=OUT_LEDGER)
        assert gate_exec_stamp_ok(trace, rebuild_tok)
        ls.write_bytes(ls_b)

        # #54: gate ledger must follow a varied active bench fixture.
        ls.write_text("scenario solo repeat 1 batch 1\n")
        for p in (SCORES, LEDGER):
            if p.exists(): p.unlink()
        code, out, err, calls, rebuilt, trace = run_ci_gate()
        assert code == 0, err + out
        assert gate_exec_stamp_ok(trace, rebuild_tok)
        led = ledger_obj(LEDGER)
        assert led["total_requests"] == 1
        assert led["total_ticks"] == 15
        assert led["throughput"] == 66667
        assert led["avg_latency_ticks"] == 15
        assert led["p95_ticks"] == 15 and led["p99_ticks"] == 15
        assert led["memory_units"] == 68
        assert gate_ran(calls, "bench", spec=LS, out=OUT_LEDGER)
        ls.write_bytes(ls_b)

        # Gate score product must cover every active fixture row (not a fixed six-row cap).
        seven = [mean({"id": "G%d" % i, "tenure_months": 12 * (i + 1)}) for i in range(7)]
        fx.write_text("".join(json.dumps(r) + "\n" for r in seven))
        for p in (SCORES, LEDGER):
            if p.exists(): p.unlink()
        code, out, err, calls, rebuilt, trace = run_ci_gate()
        assert code == 0, err + out
        assert gate_exec_stamp_ok(trace, rebuild_tok)
        got7 = load_jsonl(SCORES)
        assert len(got7) == 7
        assert [r["score_micros"] for r in got7] == [oracle_micros(r) for r in seven]
        fx.write_bytes(fx_b)

        broken.write_text("package main\nfunc broken(\n")
        own(broken)
        code, out, err, calls, rebuilt, trace = run_ci_gate()
        assert code != 0
        assert token() not in (out + err)
    finally:
        fx.write_bytes(fx_b); ls.write_bytes(ls_b)
        coef.write_bytes(coef_bak); scale.write_bytes(scale_bak)
        if hold.exists(): hold.unlink()
        if broken.exists(): broken.unlink()

def test_scoring_isolation_and_legality():
    rebuild()
    out = work(".jsonl")
    assert score("/app/fixtures/week41-intake.jsonl", out).returncode == 0
    assert load_jsonl(out) == WEEK41
    out2 = work(".jsonl")
    r_sc1 = score("/app/fixtures/week41-intake.jsonl", out2)
    assert r_sc1.returncode == 0
    assert out2.read_bytes() == out.read_bytes()
    r_sc2 = score("/app/fixtures/week41-intake.jsonl", out2)
    assert r_sc2.returncode == 0 and r_sc2.stdout == r_sc1.stdout

    # Empty intake is legal for score: success with a complete empty product.
    empty_in = work(".jsonl")
    empty_in.write_bytes(b"")
    empty_out = work(".jsonl")
    assert score(empty_in, empty_out).returncode == 0
    assert empty_out.is_file() and empty_out.read_bytes() == b""

    # Failure diagnostics must also be deterministic across identical illegal intakes.
    bad_in = write_intake([mean({"id": ""})])
    pout_a, pout_b = stale(), stale()
    r_bad1 = score(bad_in, pout_a)
    r_bad2 = score(bad_in, pout_b)
    assert r_bad1.returncode != 0 and r_bad2.returncode != 0
    assert not pout_a.exists() and not pout_b.exists()
    assert r_bad1.stdout == r_bad2.stdout and r_bad1.stderr == r_bad2.stderr
    assert_cli_clean(r_bad1.stdout, r_bad1.stderr)

    # Multi-fault intakes must also yield deterministic diagnostics (not map-iteration order).
    multi = mean({"id": "MULTI", "tenure_months": -1, "util_ratio": -0.01})
    multi_in = write_intake([multi])
    pout_a, pout_b = stale(), stale()
    r_m1 = score(multi_in, pout_a)
    r_m2 = score(multi_in, pout_b)
    assert r_m1.returncode != 0 and r_m2.returncode != 0
    assert not pout_a.exists() and not pout_b.exists()
    assert r_m1.stdout == r_m2.stdout and r_m1.stderr == r_m2.stderr
    assert_cli_clean(r_m1.stdout, r_m1.stderr)

    # Two distinct unknown keys: diagnostics must be stable across identical intakes.
    dual_unk = {**mean({"id": "DUALUNK"}), "extra_a": 1, "extra_b": 1}
    du_in = write_intake([dual_unk])
    pout_a, pout_b = stale(), stale()
    r_d1 = score(du_in, pout_a)
    r_d2 = score(du_in, pout_b)
    assert r_d1.returncode != 0 and r_d2.returncode != 0
    assert not pout_a.exists() and not pout_b.exists()
    assert r_d1.stdout == r_d2.stdout and r_d1.stderr == r_d2.stderr
    assert_cli_clean(r_d1.stdout, r_d1.stderr)

    for name, want in (("xi", XI), ("nu", NU)):
        inn, outp = stage("%s-intake.jsonl" % name), work(".jsonl")
        assert score(inn, outp).returncode == 0
        assert load_jsonl(outp) == want

    rows = [
        mean({"id": "B1", "tenure_months": 48}),
        mean({"id": "B2", "tenure_months": 12}),
        mean({"id": "B3", "tenure_months": 72}),
        mean({"id": "B4", "tenure_months": 96}),
        mean({"id": "B5", "tenure_months": 36}),
        mean({"id": "B6", "tenure_months": 60}),
    ]
    inn, outp = write_intake(rows), work(".jsonl")
    assert score(inn, outp, cfg=write_sidecar(3)).returncode == 0
    assert [r["score_micros"] for r in load_jsonl(outp)] == [oracle_micros(x) for x in rows]

    # #63: per-request isolation with batch_limit equal to the full group size (>5).
    inn, outp = write_intake(rows), work(".jsonl")
    assert score(inn, outp, cfg=write_sidecar(6)).returncode == 0
    assert [r["score_micros"] for r in load_jsonl(outp)] == [oracle_micros(x) for x in rows]

    # batch_limit must admit groups larger than six.
    rows7 = rows + [mean({"id": "B7", "tenure_months": 84})]
    inn, outp = write_intake(rows7), work(".jsonl")
    assert score(inn, outp, cfg=write_sidecar(7)).returncode == 0
    assert [r["score_micros"] for r in load_jsonl(outp)] == [oracle_micros(x) for x in rows7]

    # Nine-member batch must isolate every request (no silent 8-slot scratch reuse).
    rows9 = rows7 + [
        mean({"id": "B8", "tenure_months": 108}),
        mean({"id": "B9", "tenure_months": 24}),
    ]
    inn, outp = write_intake(rows9), work(".jsonl")
    assert score(inn, outp, cfg=write_sidecar(9)).returncode == 0
    assert [r["score_micros"] for r in load_jsonl(outp)] == [oracle_micros(x) for x in rows9]

    # Residual chunk shorter than batch_limit must still be scored (4 rows, limit 3).
    rows4 = rows[:4]
    inn, outp = write_intake(rows4), work(".jsonl")
    assert score(inn, outp, cfg=write_sidecar(3)).returncode == 0
    assert [r["score_micros"] for r in load_jsonl(outp)] == [oracle_micros(x) for x in rows4]

    acc = mean({"id": "ORD", "tenure_months": 96, "open_trades": 23})
    inn, outp = write_intake([acc]), work(".jsonl")
    assert score(inn, outp).returncode == 0
    assert load_jsonl(outp)[0]["score_micros"] == oracle_micros(acc)

    huge = write_sidecar("9223372036854775808")
    inn, outp = write_intake(rows), work(".jsonl")
    assert score(inn, outp, cfg=huge).returncode == 0
    assert [r["score_micros"] for r in load_jsonl(outp)] == [oracle_micros(x) for x in rows]
    # bench and serve must also accept a positive batch_limit above signed int64.
    bout = work(".json")
    assert run([
        "bench", "--config", str(huge),
        "--spec", "/app/bench/loadspec.txt", "--out", str(bout),
    ]).returncode == 0
    assert ledger_obj(bout) == BENCH
    port = free_port()
    huge_serve = write_sidecar("9223372036854775808")
    hs_lines = ["listen 127.0.0.1:%d" % port if l.startswith("listen ") else l for l in huge_serve.read_text().splitlines()]
    huge_serve.write_text("\n".join(hs_lines) + "\n"); own(huge_serve)
    proc = subprocess.Popen(drop([str(BIN), "serve", "--config", str(huge_serve)]), cwd=str(APP), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env())
    assert wait_ready(proc, port)
    st, hdr, body = post(port, mean({"id": "HUGEBL"}), auth())
    assert st == 200 and body["score_micros"] == 389361
    stop(proc)

    mout = stale()
    gone(score(write_intake([mean({"id": "DEF"})]), mout, cfg=write_sidecar(omit=True)), mout)
    mout = stale()
    gone(score(write_intake([mean({"id": "DEF"})]), mout, cfg=write_sidecar(0)), mout)
    # #27/#28: non-integer and negative batch_limit rejected for score.
    mout = stale()
    gone(score(write_intake([mean({"id": "BL15"})]), mout, cfg=write_sidecar("1.5")), mout)
    mout = stale()
    gone(score(write_intake([mean({"id": "BLN"})]), mout, cfg=write_sidecar(-1)), mout)

    # #26/#11/#21: omitted / nonpositive batch_limit rejected for every sidecar loader,
    # including default-config score (no --config) and serve.
    side, side_b = SIDECAR, SIDECAR.read_bytes()
    try:
        lines = [l for l in side_b.decode().splitlines() if not l.startswith("batch_limit ")]
        side.write_text("\n".join(lines) + "\n"); own(side)
        assert run(["selfcheck"]).returncode != 0
        bout = work(".json")
        assert run(["bench", "--out", str(bout)]).returncode != 0
        mout = stale()
        gone(score(write_intake([mean({"id": "DEFSC"})]), mout), mout)
        port = free_port()
        bad = write_sidecar(omit=True)
        bad_lines = ["listen 127.0.0.1:%d" % port if l.startswith("listen ") else l for l in bad.read_text().splitlines()]
        bad.write_text("\n".join(bad_lines) + "\n"); own(bad)
        proc = subprocess.Popen(drop([str(BIN), "serve", "--config", str(bad)]), cwd=str(APP), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env())
        assert not wait_ready(proc, port, timeout=3.0)
        stop(proc)
    finally:
        side.write_bytes(side_b)

    # Nonpositive batch_limit also rejected for bench (explicit --config) and selfcheck (default sidecar).
    bout = work(".json")
    assert run(["bench", "--config", str(write_sidecar(0)), "--out", str(bout)]).returncode != 0
    bout = work(".json")
    assert run(["bench", "--config", str(write_sidecar(-1)), "--out", str(bout)]).returncode != 0
    try:
        side.write_text(side_b.decode().replace("batch_limit 1", "batch_limit 0", 1))
        own(side)
        assert run(["selfcheck"]).returncode != 0
        bout = work(".json")
        assert run(["bench", "--out", str(bout)]).returncode != 0
    finally:
        side.write_bytes(side_b)

    # Negative batch_limit is invalid for selfcheck (every sidecar loader).
    try:
        side.write_text(side_b.decode().replace("batch_limit 1", "batch_limit -1", 1))
        own(side)
        assert run(["selfcheck"]).returncode != 0
    finally:
        side.write_bytes(side_b)

    # #3: serve rejects invalid (nonpositive) batch_limit values, not only omission.
    for bl in (0, -1):
        port = free_port()
        bad = write_sidecar(bl)
        bad_lines = ["listen 127.0.0.1:%d" % port if l.startswith("listen ") else l for l in bad.read_text().splitlines()]
        bad.write_text("\n".join(bad_lines) + "\n"); own(bad)
        proc = subprocess.Popen(drop([str(BIN), "serve", "--config", str(bad)]), cwd=str(APP), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env())
        assert not wait_ready(proc, port, timeout=3.0)
        stop(proc)

    # Non-integer batch_limit rejected for bench, selfcheck, and serve (not only score).
    bout = work(".json")
    assert run(["bench", "--config", str(write_sidecar("1.5")), "--out", str(bout)]).returncode != 0
    try:
        side.write_text(side_b.decode().replace("batch_limit 1", "batch_limit 1.5", 1))
        own(side)
        assert run(["selfcheck"]).returncode != 0
    finally:
        side.write_bytes(side_b)
    port = free_port()
    bad = write_sidecar("1.5")
    bad_lines = ["listen 127.0.0.1:%d" % port if l.startswith("listen ") else l for l in bad.read_text().splitlines()]
    bad.write_text("\n".join(bad_lines) + "\n"); own(bad)
    proc = subprocess.Popen(drop([str(BIN), "serve", "--config", str(bad)]), cwd=str(APP), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env())
    assert not wait_ready(proc, port, timeout=3.0)
    stop(proc)

    for payload in illegal_rows():
        pout = stale()
        gone(score(write_intake([payload]), pout), pout)

    ok_rows = bound_rows()
    inn, outp = write_intake(ok_rows), work(".jsonl")
    assert score(inn, outp).returncode == 0
    got = load_jsonl(outp)
    assert got[0]["id"] == " "
    for i, row in enumerate(ok_rows):
        assert got[i]["score_micros"] == oracle_micros(row)

    # Live schema bounds must drive range checks (not hardcoded delivered maxima).
    schema = APP / "policy" / "intake-schema.json"
    schema_b = schema.read_bytes()
    try:
        schema.write_text(schema_b.decode().replace(
            '"name": "tenure_months", "min": 0, "max": 600',
            '"name": "tenure_months", "min": 0, "max": 601',
            1,
        ))
        own(schema)
        row601 = mean({"id": "S601", "tenure_months": 601})
        inn, outp = write_intake([row601]), work(".jsonl")
        assert score(inn, outp).returncode == 0
        assert load_jsonl(outp)[0]["score_micros"] == oracle_micros(row601)
        rebuild()
        proc, port = start_server()
        try:
            st, hdr, body = post(port, row601, auth())
            assert st == 200 and body == {"id": "S601", "score_micros": oracle_micros(row601)}
        finally:
            stop(proc)
        # util_ratio live max must also be honored (not only tenure_months).
        schema.write_text(schema_b.decode().replace(
            '"name": "util_ratio", "min": 0, "max": 2.5',
            '"name": "util_ratio", "min": 0, "max": 2.6',
            1,
        ))
        own(schema)
        row_u = mean({"id": "SU26", "util_ratio": 2.6})
        inn, outp = write_intake([row_u]), work(".jsonl")
        assert score(inn, outp).returncode == 0
        assert load_jsonl(outp)[0]["score_micros"] == oracle_micros(row_u)
        rebuild()
        proc, port = start_server()
        try:
            st, hdr, body = post(port, row_u, auth())
            assert st == 200 and body == {"id": "SU26", "score_micros": oracle_micros(row_u)}
        finally:
            stop(proc)
    finally:
        schema.write_bytes(schema_b)
        own(schema)

    pout = stale()
    gone(score(write_intake((json.dumps(mean({"id": "D"})) + "\n" + json.dumps(mean({"id": "D"})) + "\n").encode()), pout), pout)

    # #19: duplicate ids with different features.
    pout = stale()
    dup = [mean({"id": "D", "tenure_months": 48}), mean({"id": "D", "tenure_months": 72})]
    gone(score(write_intake(dup), pout), pout)

    # #18: blank / whitespace-only CLI line is illegal and removes --out.
    legal = json.dumps(mean({"id": "BLANK"}))
    pout = stale()
    gone(score(write_intake((legal + "\n\n").encode()), pout), pout)
    pout = stale()
    gone(score(write_intake((legal + "\n   \n").encode()), pout), pout)

    # #23: trailing tokens after a legal JSON object on the same line.
    pout = stale()
    gone(score(write_intake((json.dumps(mean({"id": "TRAIL"})) + " null\n").encode()), pout), pout)

    # Top-level JSON array (even a singleton wrapping a legal application) is illegal.
    arr_line = "[" + json.dumps(mean({"id": "ARRTOP"})) + "]"
    pout = stale()
    gone(score(write_intake((arr_line + "\n").encode()), pout), pout)

    # #20: CLI line-length boundary — exact 1048576 succeeds; 1048577 fails and removes --out.
    base = json.dumps(mean({"id": "PAD"}))
    pad_exact = base + (" " * (1048576 - len(base)))
    assert len(pad_exact) == 1048576
    inn, outp = write_intake((pad_exact + "\n").encode()), work(".jsonl")
    assert score(inn, outp).returncode == 0
    assert load_jsonl(outp)[0]["score_micros"] == 389361
    over = base + (" " * (1048577 - len(base)))
    assert len(over) == 1048577
    pout = stale()
    gone(score(write_intake((over + "\n").encode()), pout), pout)
    # CR immediately before LF counts toward the 1048576 limit (exclude only LF).
    cr_pad = base + (" " * (1048576 - len(base))) + "\r"
    assert len(cr_pad) == 1048577
    pout = stale()
    gone(score(write_intake((cr_pad + "\n").encode()), pout), pout)

    # #22: --out=PATH equals form still cleaned on failure.
    pout = stale()
    gone(run(["score", "--intake", str(write_intake([mean({"id": ""})])), "--out=%s" % pout]), pout)

    # #9: flag-parse failure after a named --out still removes the path.
    pout = stale()
    gone(run([
        "score", "--intake", "/app/fixtures/week41-intake.jsonl",
        "--out", str(pout), "--verbose",
    ]), pout)
    pout = stale()
    gone(run([
        "score", "--intake", "/app/fixtures/week41-intake.jsonl",
        "--out=%s" % pout, "--verbose",
    ]), pout)

    # Directory --out paths are left alone on failure (regular files/symlinks only).
    dout = stale_dir()
    r = score(write_intake([mean({"id": ""})]), dout)
    assert r.returncode != 0 and dout.is_dir() and (dout / "child").exists()
    assert_cli_clean(r.stdout, r.stderr)
    dout = stale_dir()
    r = run([
        "score", "--intake", str(write_intake([mean({"id": ""})])),
        "--out=%s" % dout,
    ])
    assert r.returncode != 0 and dout.is_dir() and (dout / "child").exists()
    assert_cli_clean(r.stdout, r.stderr)
    # Legal intake with --out naming a nonempty directory: write fails; directory kept.
    dout = stale_dir()
    r = score(write_intake([mean({"id": "WRITEDIR"})]), dout)
    assert r.returncode != 0 and dout.is_dir() and (dout / "child").exists()
    assert_cli_clean(r.stdout, r.stderr)

    # Dangling symlink --out must be removed on score failure (regular file or symlink).
    slink = stale_symlink()
    gone(score(write_intake([mean({"id": ""})]), slink), slink)
    slink = stale_symlink()
    gone(run([
        "score", "--intake", str(write_intake([mean({"id": ""})])),
        "--out=%s" % slink,
    ]), slink)

    # --out naming the process cwd is left in place on failure (directory rule).
    for out_flag in (["--out", "."], ["--out=."]):
        cwd_dir = work(".cwddir")
        if cwd_dir.exists():
            if cwd_dir.is_dir():
                shutil.rmtree(cwd_dir)
            else:
                cwd_dir.unlink()
        cwd_dir.mkdir(parents=True, exist_ok=True)
        own(cwd_dir)
        bad_in = cwd_dir / "bad.jsonl"
        bad_in.write_text(json.dumps(mean({"id": ""})) + "\n"); own(bad_in)
        r = run(["score", "--intake", str(bad_in), *out_flag], cwd=str(cwd_dir))
        assert r.returncode != 0
        assert_cli_clean(r.stdout, r.stderr)
        assert cwd_dir.is_dir(), out_flag
        assert bad_in.exists()

    miss = stale()
    gone(run(["score", "--intake", "/work/out/missing.jsonl", "--out", str(miss)]), miss)
    mout = stale()
    gone(run(["score", "--out", str(mout)]), mout)
    # #47: omitting --out entirely must fail.
    assert run(["score", "--intake", "/app/fixtures/week41-intake.jsonl"]).returncode != 0

    # Live scale/coef entries beyond tenure/util/bias must be read (not frozen delivered values).
    scale_mut = work(".scale")
    scale_mut.write_text(SCALE_B.decode().replace("std delinq_24 1.100000000", "std delinq_24 2.200000000", 1))
    own(scale_mut)
    row_d = mean({"id": "DLQ", "delinq_24": 4.0})
    want_d = oracle_micros_assets(row_d, COEF_B.decode(), scale_mut.read_text())
    assert want_d != oracle_micros(row_d)
    inn, outp = write_intake([row_d]), work(".jsonl")
    assert score(inn, outp, cfg=write_sidecar(scale=str(scale_mut))).returncode == 0
    assert load_jsonl(outp)[0]["score_micros"] == want_d

    coef_mut = work(".coef")
    coef_mut.write_text(COEF_B.decode().replace("w delinq_24 -0.350000000", "w delinq_24 0.000000000", 1))
    own(coef_mut)
    want_c = oracle_micros_assets(row_d, coef_mut.read_text(), SCALE_B.decode())
    assert want_c != oracle_micros(row_d)
    inn, outp = write_intake([row_d]), work(".jsonl")
    assert score(inn, outp, cfg=write_sidecar(coef=str(coef_mut))).returncode == 0
    assert load_jsonl(outp)[0]["score_micros"] == want_c

    # Live inq_6 coefficient and std must be read (not only tenure/util/delinq).
    coef_inq = work(".coef")
    coef_inq.write_text(COEF_B.decode().replace("w inq_6 -0.180000000", "w inq_6 0.000000000", 1))
    own(coef_inq)
    row_i = mean({"id": "INQ", "inq_6": 2.6})
    want_i = oracle_micros_assets(row_i, coef_inq.read_text(), SCALE_B.decode())
    assert want_i == 389361
    assert want_i != oracle_micros(row_i)
    inn, outp = write_intake([row_i]), work(".jsonl")
    assert score(inn, outp, cfg=write_sidecar(coef=str(coef_inq))).returncode == 0
    assert load_jsonl(outp)[0]["score_micros"] == want_i
    scale_inq = work(".scale")
    scale_inq.write_text(SCALE_B.decode().replace("std inq_6 1.400000000", "std inq_6 2.800000000", 1))
    own(scale_inq)
    want_is = oracle_micros_assets(row_i, COEF_B.decode(), scale_inq.read_text())
    assert want_is != oracle_micros(row_i)
    inn, outp = write_intake([row_i]), work(".jsonl")
    assert score(inn, outp, cfg=write_sidecar(scale=str(scale_inq))).returncode == 0
    assert load_jsonl(outp)[0]["score_micros"] == want_is

    # Scoring must read live util_ratio std and coefficient (not only selfcheck).
    row_off = mean({"id": "UOFF", "tenure_months": 72, "util_ratio": 0.36})
    scale_u = work(".scale")
    scale_u.write_text(SCALE_B.decode().replace("std util_ratio 0.200000000", "std util_ratio 0.400000000", 1))
    own(scale_u)
    want_u = oracle_micros_assets(row_off, COEF_B.decode(), scale_u.read_text())
    assert want_u != oracle_micros(row_off)
    inn, outp = write_intake([row_off]), work(".jsonl")
    assert score(inn, outp, cfg=write_sidecar(scale=str(scale_u))).returncode == 0
    assert load_jsonl(outp)[0]["score_micros"] == want_u
    coef_u = work(".coef")
    coef_u.write_text(COEF_B.decode().replace("w util_ratio -2.400000000", "w util_ratio -1.200000000", 1))
    own(coef_u)
    want_uc = oracle_micros_assets(row_off, coef_u.read_text(), SCALE_B.decode())
    assert want_uc != oracle_micros(row_off)
    inn, outp = write_intake([row_off]), work(".jsonl")
    assert score(inn, outp, cfg=write_sidecar(coef=str(coef_u))).returncode == 0
    assert load_jsonl(outp)[0]["score_micros"] == want_uc

    # Scoring must read live scale means (not only std/coef).
    scale_mean = work(".scale")
    scale_mean.write_text(SCALE_B.decode().replace("mean delinq_24 0.800000000", "mean delinq_24 1.800000000", 1))
    own(scale_mean)
    row_dm = mean({"id": "DMEAN", "delinq_24": 0.8})
    want_dm = oracle_micros_assets(row_dm, COEF_B.decode(), scale_mean.read_text())
    assert want_dm != oracle_micros(row_dm)
    inn, outp = write_intake([row_dm]), work(".jsonl")
    assert score(inn, outp, cfg=write_sidecar(scale=str(scale_mean))).returncode == 0
    assert load_jsonl(outp)[0]["score_micros"] == want_dm

    # Live mean tenure_months must drive scoring (not a delivered constant 48).
    scale_tm = work(".scale")
    scale_tm.write_text(SCALE_B.decode().replace("mean tenure_months 48.000000000", "mean tenure_months 49.000000000", 1))
    own(scale_tm)
    row_tm = mean({"id": "TMEAN", "tenure_months": 48})
    want_tm = oracle_micros_assets(row_tm, COEF_B.decode(), scale_tm.read_text())
    assert want_tm != oracle_micros(row_tm)
    inn, outp = write_intake([row_tm]), work(".jsonl")
    assert score(inn, outp, cfg=write_sidecar(scale=str(scale_tm))).returncode == 0
    assert load_jsonl(outp)[0]["score_micros"] == want_tm

    # Live mean util_ratio must drive scoring (not a delivered constant 0.35).
    scale_um = work(".scale")
    scale_um.write_text(SCALE_B.decode().replace("mean util_ratio 0.350000000", "mean util_ratio 0.360000000", 1))
    own(scale_um)
    row_um = mean({"id": "UMEAN", "util_ratio": 0.35})
    want_um = oracle_micros_assets(row_um, COEF_B.decode(), scale_um.read_text())
    assert want_um != oracle_micros(row_um)
    inn, outp = write_intake([row_um]), work(".jsonl")
    assert score(inn, outp, cfg=write_sidecar(scale=str(scale_um))).returncode == 0
    assert load_jsonl(outp)[0]["score_micros"] == want_um

    # Live means for inq_6 / revol_bal / open_trades must drive scoring (not delivered constants).
    for field, old, new, delivered_val in (
        ("inq_6", "mean inq_6 1.200000000", "mean inq_6 2.600000000", 1.2),
        ("revol_bal", "mean revol_bal 8500.000000000", "mean revol_bal 9500.000000000", 8500),
        ("open_trades", "mean open_trades 7.000000000", "mean open_trades 9.000000000", 7),
    ):
        scale_m = work(".scale")
        scale_m.write_text(SCALE_B.decode().replace(old, new, 1))
        own(scale_m)
        row_offm = mean({"id": "MEAN-%s" % field, field: delivered_val})
        want_offm = oracle_micros_assets(row_offm, COEF_B.decode(), scale_m.read_text())
        assert want_offm != oracle_micros(row_offm)
        inn, outp = write_intake([row_offm]), work(".jsonl")
        assert score(inn, outp, cfg=write_sidecar(scale=str(scale_m))).returncode == 0
        assert load_jsonl(outp)[0]["score_micros"] == want_offm
        # At all live means (including the shifted one) micros stay 389361.
        live_mean_val = float(new.split()[-1])
        row_at = mean({"id": "ATMEAN-%s" % field, field: live_mean_val})
        want_at = oracle_micros_assets(row_at, COEF_B.decode(), scale_m.read_text())
        assert want_at == 389361
        inn, outp = write_intake([row_at]), work(".jsonl")
        assert score(inn, outp, cfg=write_sidecar(scale=str(scale_m))).returncode == 0
        assert load_jsonl(outp)[0]["score_micros"] == want_at

    # Remaining field weights and stds must be live (tenure / revol_bal / open_trades).
    row_ot = mean({"id": "OTW", "open_trades": 23})
    coef_ot = work(".coef")
    coef_ot.write_text(COEF_B.decode().replace("w open_trades 0.060000000", "w open_trades 0.000000000", 1))
    own(coef_ot)
    want_ot = oracle_micros_assets(row_ot, coef_ot.read_text(), SCALE_B.decode())
    assert want_ot == 389361
    assert want_ot != oracle_micros(row_ot)
    inn, outp = write_intake([row_ot]), work(".jsonl")
    assert score(inn, outp, cfg=write_sidecar(coef=str(coef_ot))).returncode == 0
    assert load_jsonl(outp)[0]["score_micros"] == want_ot

    row_tn = mean({"id": "TNW", "tenure_months": 96})
    coef_tn = work(".coef")
    coef_tn.write_text(COEF_B.decode().replace("w tenure_months 0.015000000", "w tenure_months 0.000000000", 1))
    own(coef_tn)
    want_tn = oracle_micros_assets(row_tn, coef_tn.read_text(), SCALE_B.decode())
    assert want_tn != oracle_micros(row_tn)
    inn, outp = write_intake([row_tn]), work(".jsonl")
    assert score(inn, outp, cfg=write_sidecar(coef=str(coef_tn))).returncode == 0
    assert load_jsonl(outp)[0]["score_micros"] == want_tn

    row_rv = mean({"id": "RVW", "revol_bal": 20000})
    coef_rv = work(".coef")
    coef_rv.write_text(COEF_B.decode().replace("w revol_bal -0.000040000", "w revol_bal 0.000000000", 1))
    own(coef_rv)
    want_rv = oracle_micros_assets(row_rv, coef_rv.read_text(), SCALE_B.decode())
    assert want_rv != oracle_micros(row_rv)
    inn, outp = write_intake([row_rv]), work(".jsonl")
    assert score(inn, outp, cfg=write_sidecar(coef=str(coef_rv))).returncode == 0
    assert load_jsonl(outp)[0]["score_micros"] == want_rv

    scale_rv = work(".scale")
    scale_rv.write_text(SCALE_B.decode().replace("std revol_bal 6200.000000000", "std revol_bal 12400.000000000", 1))
    own(scale_rv)
    want_rvs = oracle_micros_assets(row_rv, COEF_B.decode(), scale_rv.read_text())
    assert want_rvs != oracle_micros(row_rv)
    inn, outp = write_intake([row_rv]), work(".jsonl")
    assert score(inn, outp, cfg=write_sidecar(scale=str(scale_rv))).returncode == 0
    assert load_jsonl(outp)[0]["score_micros"] == want_rvs

    scale_ot = work(".scale")
    scale_ot.write_text(SCALE_B.decode().replace("std open_trades 4.000000000", "std open_trades 8.000000000", 1))
    own(scale_ot)
    want_ots = oracle_micros_assets(row_ot, COEF_B.decode(), scale_ot.read_text())
    assert want_ots != oracle_micros(row_ot)
    inn, outp = write_intake([row_ot]), work(".jsonl")
    assert score(inn, outp, cfg=write_sidecar(scale=str(scale_ot))).returncode == 0
    assert load_jsonl(outp)[0]["score_micros"] == want_ots

    # Values outside inclusive range that float64-round onto an endpoint are illegal.
    over = (
        b'{"id":"EDGEU","tenure_months":48,"util_ratio":2.5000000000000001,'
        b'"delinq_24":0.8,"inq_6":1.2,"revol_bal":8500,"open_trades":7}\n'
    )
    pout = stale()
    gone(score(write_intake(over), pout), pout)
    # Excesses below a 256-bit ulp near 2.5 must still reject (exact decimal compare).
    over82 = (
        b'{"id":"EDGEU82","tenure_months":48,"util_ratio":2.5'
        + (b"0" * 80) + b"1,"
        b'"delinq_24":0.8,"inq_6":1.2,"revol_bal":8500,"open_trades":7}\n'
    )
    pout = stale()
    gone(score(write_intake(over82), pout), pout)
    over100 = (
        b'{"id":"EDGEU100","tenure_months":48,"util_ratio":2.5'
        + (b"0" * 100) + b"1,"
        b'"delinq_24":0.8,"inq_6":1.2,"revol_bal":8500,"open_trades":7}\n'
    )
    pout = stale()
    gone(score(write_intake(over100), pout), pout)
    # Excess below a 512-bit ulp near 2.5 must still reject (exact decimal, not fixed-prec Float).
    over200 = (
        b'{"id":"EDGEU200","tenure_months":48,"util_ratio":2.5'
        + (b"0" * 200) + b"1,"
        b'"delinq_24":0.8,"inq_6":1.2,"revol_bal":8500,"open_trades":7}\n'
    )
    pout = stale()
    gone(score(write_intake(over200), pout), pout)
    # Contract-sized excess (~1k zeros) must reject under exact compare (not 1024-bit Float).
    over1000 = (
        b'{"id":"EDGEU1000","tenure_months":48,"util_ratio":2.5'
        + (b"0" * 1000) + b"1,"
        b'"delinq_24":0.8,"inq_6":1.2,"revol_bal":8500,"open_trades":7}\n'
    )
    assert len(over1000) < 1048576
    pout = stale()
    gone(score(write_intake(over1000), pout), pout)
    under = (
        b'{"id":"EDGET","tenure_months":-1e-400,"util_ratio":0.35,'
        b'"delinq_24":0.8,"inq_6":1.2,"revol_bal":8500,"open_trades":7}\n'
    )
    pout = stale()
    gone(score(write_intake(under), pout), pout)
    # Scientific notation must not round onto an inclusive endpoint before the range check.
    sci = (
        b'{"id":"SCIE","tenure_months":48,"util_ratio":2.5000000000000000000001e0,'
        b'"delinq_24":0.8,"inq_6":1.2,"revol_bal":8500,"open_trades":7}\n'
    )
    pout = stale()
    gone(score(write_intake(sci), pout), pout)
    sci2 = (
        b'{"id":"SCIE2","tenure_months":48,"util_ratio":2.50000000000000000001e0,'
        b'"delinq_24":0.8,"inq_6":1.2,"revol_bal":8500,"open_trades":7}\n'
    )
    pout = stale()
    gone(score(write_intake(sci2), pout), pout)
    # Large exponents are legal when the exact value is in range (no undocumented exp cap).
    zero_hi = (
        b'{"id":"ZHI","tenure_months":0e100001,"util_ratio":0.35,'
        b'"delinq_24":0.8,"inq_6":1.2,"revol_bal":8500,"open_trades":7}\n'
    )
    inn, outp = write_intake(zero_hi), work(".jsonl")
    assert score(inn, outp).returncode == 0
    assert load_jsonl(outp)[0]["score_micros"] == oracle_micros(mean({"id": "ZHI", "tenure_months": 0}))
    tiny = (
        b'{"id":"TINY","tenure_months":48,"util_ratio":1e-100001,'
        b'"delinq_24":0.8,"inq_6":1.2,"revol_bal":8500,"open_trades":7}\n'
    )
    inn, outp = write_intake(tiny), work(".jsonl")
    assert score(inn, outp).returncode == 0
    assert load_jsonl(outp)[0]["id"] == "TINY"
    # Extreme exponents must remain processable (no unbounded 10^|exp| materialization).
    extreme = (
        b'{"id":"XEXT","tenure_months":1e-100000000000000000000,"util_ratio":0.35,'
        b'"delinq_24":0.8,"inq_6":1.2,"revol_bal":8500,"open_trades":7}\n'
    )
    inn, outp = write_intake(extreme), work(".jsonl")
    assert score(inn, outp).returncode == 0
    assert load_jsonl(outp)[0]["score_micros"] == oracle_micros(mean({"id": "XEXT", "tenure_months": 0}))
    # Mantissa magnitude must survive extreme-exponent cancel (1 + 1000001 zeros)e-1000001 = 1.
    big_mant = b"1" + (b"0" * 1000001)
    util_tok = big_mant + b"e-1000001"
    # Keep the JSON line under the 1048576 intake limit.
    rest = (
        b'{"id":"X","tenure_months":48,"util_ratio":'
        + util_tok
        + b',"delinq_24":0.8,"inq_6":1.2,"revol_bal":8500,"open_trades":7}\n'
    )
    assert len(rest) < 1048576
    inn, outp = write_intake(rest), work(".jsonl")
    assert score(inn, outp).returncode == 0
    assert load_jsonl(outp)[0]["score_micros"] == oracle_micros(mean({"id": "X", "util_ratio": 1}))

    # Panel reach: large mantissa with large negative exp must stay in-range when exact value is.
    # M = 1 + 100001 zeros + 1, e-100002 → 1 + 10^-100002 within util_ratio [0, 2.5].
    mant_ok = b"1" + (b"0" * 100001) + b"1"
    sci_ok = (
        b'{"id":"SCIOK","tenure_months":48,"util_ratio":'
        + mant_ok + b"e-100002,"
        b'"delinq_24":0.8,"inq_6":1.2,"revol_bal":8500,"open_trades":7}\n'
    )
    assert len(sci_ok) < 1048576
    inn, outp = write_intake(sci_ok), work(".jsonl")
    assert score(inn, outp).returncode == 0
    assert load_jsonl(outp)[0]["id"] == "SCIOK"
    # Cap-distorted positive exp must still reject true out-of-range values.
    # 0. + 100001 zeros + 1e100006 = 10000 > tenure max 600.
    tenure_hi = b"0." + (b"0" * 100001) + b"1e100006"
    sci_bad_t = (
        b'{"id":"SCIBADT","tenure_months":' + tenure_hi + b','
        b'"util_ratio":0.35,"delinq_24":0.8,"inq_6":1.2,"revol_bal":8500,"open_trades":7}\n'
    )
    assert len(sci_bad_t) < 1048576
    pout = stale()
    gone(score(write_intake(sci_bad_t), pout), pout)
    # 0. + 100001 zeros + 3e100002 = 3 > util_ratio max 2.5.
    util_hi = b"0." + (b"0" * 100001) + b"3e100002"
    sci_bad_u = (
        b'{"id":"SCIBADU","tenure_months":48,"util_ratio":' + util_hi + b','
        b'"delinq_24":0.8,"inq_6":1.2,"revol_bal":8500,"open_trades":7}\n'
    )
    assert len(sci_bad_u) < 1048576
    pout = stale()
    gone(score(write_intake(sci_bad_u), pout), pout)
    # M = 3 + 99999 zeros + 1, e-100001 ≈ 0.3 — must accept (no negative-exp substitute).
    mant_lo = b"3" + (b"0" * 99999) + b"1"
    sci_lo = (
        b'{"id":"SCILO","tenure_months":48,"util_ratio":'
        + mant_lo + b"e-100001,"
        b'"delinq_24":0.8,"inq_6":1.2,"revol_bal":8500,"open_trades":7}\n'
    )
    assert len(sci_lo) < 1048576
    inn, outp = write_intake(sci_lo), work(".jsonl")
    assert score(inn, outp).returncode == 0
    assert load_jsonl(outp)[0]["id"] == "SCILO"

    # #52/#9: relative sidecar assets resolve against process cwd; altered coef must change micros.
    rel_root = Path("/work/rel-assets")
    if rel_root.exists():
        shutil.rmtree(rel_root)
    for sub in ("weights", "policy", "deploy"):
        (rel_root / sub).mkdir(parents=True, exist_ok=True)
    shutil.copy(APP / "weights" / "ridge-v3.coef", rel_root / "weights" / "ridge-v3.coef")
    shutil.copy(APP / "weights" / "ridge-v3.scale", rel_root / "weights" / "ridge-v3.scale")
    shutil.copy(APP / "policy" / "intake-schema.json", rel_root / "policy" / "intake-schema.json")
    shutil.copy(APP / "deploy" / "floor.token", rel_root / "deploy" / "floor.token")
    # Distinguish cwd resolution from hardcoded /app: bias -0.35 → mean micros 413382.
    coef_rel = (rel_root / "weights" / "ridge-v3.coef").read_text().replace(
        "bias -0.450000000", "bias -0.350000000", 1
    )
    (rel_root / "weights" / "ridge-v3.coef").write_text(coef_rel)
    rel_kv = rel_root / "sidecar.kv"
    rel_lines = []
    for line in SIDECAR.read_text().splitlines():
        if line.startswith("coef "):
            rel_lines.append("coef weights/ridge-v3.coef")
        elif line.startswith("scale "):
            rel_lines.append("scale weights/ridge-v3.scale")
        elif line.startswith("schema "):
            rel_lines.append("schema policy/intake-schema.json")
        elif line.startswith("token_file "):
            rel_lines.append("token_file deploy/floor.token")
        else:
            rel_lines.append(line)
    rel_kv.write_text("\n".join(rel_lines) + "\n")
    for p in rel_root.rglob("*"):
        if p.is_file():
            own(p)
    own(rel_root)
    inn = rel_root / "in.jsonl"
    inn.write_text(json.dumps(mean({"id": "RELWD"})) + "\n"); own(inn)
    outp = work(".jsonl")
    assert score(inn, outp, cfg=rel_kv, cwd=rel_root).returncode == 0
    assert load_jsonl(outp)[0]["score_micros"] == 413382

    # Relative scale must resolve under cwd (not fall back to /app's std values).
    scale_txt = (rel_root / "weights" / "ridge-v3.scale").read_text().replace(
        "std tenure_months 24.000000000", "std tenure_months 48.000000000", 1
    )
    (rel_root / "weights" / "ridge-v3.scale").write_text(scale_txt)
    own(rel_root / "weights" / "ridge-v3.scale")
    row_sc = mean({"id": "RELSC", "tenure_months": 120})
    want_sc = oracle_micros_assets(row_sc, coef_rel, scale_txt)
    assert want_sc != oracle_micros(row_sc)
    inn = rel_root / "in-sc.jsonl"
    inn.write_text(json.dumps(row_sc) + "\n"); own(inn)
    outp = work(".jsonl")
    assert score(inn, outp, cfg=rel_kv, cwd=rel_root).returncode == 0
    assert load_jsonl(outp)[0]["score_micros"] == want_sc
    # Restore delivered-like scale for later relative serve checks that expect 413382.
    shutil.copy(APP / "weights" / "ridge-v3.scale", rel_root / "weights" / "ridge-v3.scale")
    own(rel_root / "weights" / "ridge-v3.scale")

    # Relative schema max must resolve under cwd.
    sch_rel = (rel_root / "policy" / "intake-schema.json")
    sch_rel.write_text(sch_rel.read_text().replace(
        '"name": "util_ratio", "min": 0, "max": 2.5',
        '"name": "util_ratio", "min": 0, "max": 2.6',
        1,
    ))
    own(sch_rel)
    row_su = mean({"id": "RELSCH", "util_ratio": 2.6})
    inn = rel_root / "in-sch.jsonl"
    inn.write_text(json.dumps(row_su) + "\n"); own(inn)
    outp = work(".jsonl")
    assert score(inn, outp, cfg=rel_kv, cwd=rel_root).returncode == 0
    assert load_jsonl(outp)[0]["score_micros"] == oracle_micros_assets(row_su, coef_rel, (rel_root / "weights" / "ridge-v3.scale").read_text())
    shutil.copy(APP / "policy" / "intake-schema.json", sch_rel)
    own(sch_rel)

    # Relative token_file must resolve under cwd (not /app's floor.token).
    alt_tok = "rel-cwd-token-%s" % secrets.token_hex(4)
    (rel_root / "deploy" / "floor.token").write_text(alt_tok + "\n")
    own(rel_root / "deploy" / "floor.token")
    dig = hashlib.sha256(alt_tok.encode()).hexdigest()
    tok_lines = []
    for line in rel_kv.read_text().splitlines():
        if line.startswith("token_sha256 "):
            tok_lines.append("token_sha256 %s" % dig)
        else:
            tok_lines.append(line)
    rel_kv.write_text("\n".join(tok_lines) + "\n"); own(rel_kv)
    port = free_port()
    serve_tok_lines = []
    for line in rel_kv.read_text().splitlines():
        if line.startswith("listen "):
            serve_tok_lines.append("listen 127.0.0.1:%d" % port)
        else:
            serve_tok_lines.append(line)
    serve_tok_kv = rel_root / "serve-tok.kv"
    serve_tok_kv.write_text("\n".join(serve_tok_lines) + "\n"); own(serve_tok_kv)
    proc = subprocess.Popen(
        drop([str(BIN), "serve", "--config", str(serve_tok_kv)]),
        cwd=str(rel_root), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env(),
    )
    try:
        assert wait_ready(proc, port)
        st, hdr, body = post(port, mean({"id": "RELTOK"}), auth(authorization="Bearer %s" % alt_tok))
        assert st == 200 and body["score_micros"] == 413382
        st, hdr, body = post(port, mean({"id": "RELTOK2"}), auth())  # /app floor token
        http_fail(st, hdr, body, "UNAUTHORIZED", 401)
    finally:
        stop(proc)
    # Restore starter token + sha for subsequent relative serve/bench that use auth() helpers.
    shutil.copy(APP / "deploy" / "floor.token", rel_root / "deploy" / "floor.token")
    own(rel_root / "deploy" / "floor.token")
    restored = []
    for line in SIDECAR.read_text().splitlines():
        if line.startswith("coef "):
            restored.append("coef weights/ridge-v3.coef")
        elif line.startswith("scale "):
            restored.append("scale weights/ridge-v3.scale")
        elif line.startswith("schema "):
            restored.append("schema policy/intake-schema.json")
        elif line.startswith("token_file "):
            restored.append("token_file deploy/floor.token")
        else:
            restored.append(line)
    # Keep relative coef bias -0.35.
    rel_kv.write_text("\n".join(restored) + "\n"); own(rel_kv)

    # Relative assets must also resolve for serve and bench under the same cwd.
    port = free_port()
    serve_lines = []
    for line in rel_kv.read_text().splitlines():
        if line.startswith("listen "):
            serve_lines.append("listen 127.0.0.1:%d" % port)
        else:
            serve_lines.append(line)
    serve_kv = rel_root / "serve.kv"
    serve_kv.write_text("\n".join(serve_lines) + "\n"); own(serve_kv)
    proc = subprocess.Popen(
        drop([str(BIN), "serve", "--config", str(serve_kv)]),
        cwd=str(rel_root), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env(),
    )
    try:
        assert wait_ready(proc, port)
        st, hdr, body = post(port, mean({"id": "RELSRV"}), auth())
        assert st == 200 and body["score_micros"] == 413382
    finally:
        stop(proc)
    bout = work(".json")
    assert run(
        ["bench", "--config", str(rel_kv), "--spec", "/app/bench/loadspec.txt", "--out", str(bout)],
        cwd=rel_root,
    ).returncode == 0
    assert ledger_obj(bout)["total_requests"] == BENCH["total_requests"]

    # Default relative --out (out/bench-ledger.json) must resolve against cwd, not /app.
    rel_out_dir = rel_root / "out"
    if rel_out_dir.exists():
        shutil.rmtree(rel_out_dir)
    rel_out_dir.mkdir(parents=True, exist_ok=True)
    own(rel_out_dir)
    assert run(
        ["bench", "--config", str(rel_kv), "--spec", "/app/bench/loadspec.txt"],
        cwd=rel_root,
    ).returncode == 0
    rel_ledger = rel_out_dir / "bench-ledger.json"
    assert rel_ledger.is_file()
    assert ledger_obj(rel_ledger)["total_requests"] == BENCH["total_requests"]

    # Bench cwd resolution must not hardcode /app: alternate.coef exists only under cwd.
    alt_name = "alternate.coef"
    assert not (APP / "weights" / alt_name).exists()
    shutil.copy(rel_root / "weights" / "ridge-v3.coef", rel_root / "weights" / alt_name)
    own(rel_root / "weights" / alt_name)
    alt_kv = rel_root / "alt.kv"
    alt_lines = []
    for line in rel_kv.read_text().splitlines():
        if line.startswith("coef "):
            alt_lines.append("coef weights/%s" % alt_name)
        else:
            alt_lines.append(line)
    alt_kv.write_text("\n".join(alt_lines) + "\n"); own(alt_kv)
    bout_alt = work(".json")
    assert run(
        ["bench", "--config", str(alt_kv), "--spec", "/app/bench/loadspec.txt", "--out", str(bout_alt)],
        cwd=rel_root,
    ).returncode == 0
    assert ledger_obj(bout_alt)["total_requests"] == BENCH["total_requests"]

    # Also keep /app-cwd relative resolution against delivered /app assets.
    rel_kv2 = work(".kv")
    rel_lines_app = []
    for line in SIDECAR.read_text().splitlines():
        if line.startswith("coef "):
            rel_lines_app.append("coef weights/ridge-v3.coef")
        elif line.startswith("scale "):
            rel_lines_app.append("scale weights/ridge-v3.scale")
        elif line.startswith("schema "):
            rel_lines_app.append("schema policy/intake-schema.json")
        elif line.startswith("token_file "):
            rel_lines_app.append("token_file deploy/floor.token")
        else:
            rel_lines_app.append(line)
    rel_kv2.write_text("\n".join(rel_lines_app) + "\n"); own(rel_kv2)
    inn, outp = write_intake([mean({"id": "REL"})]), work(".jsonl")
    assert score(inn, outp, cfg=rel_kv2).returncode == 0
    assert load_jsonl(outp)[0]["score_micros"] == 389361

def test_http_auth_and_concurrency():
    """HTTP auth, intake limits, routing, and identical-POST repeatability."""
    rebuild()
    proc, port = start_server()
    try:
        st, hdr, body = http_req(port, "GET", "/v1/health")
        assert st == 200 and body == {"status": "ok"}
        assert hdr.get("content-type") == "application/json"
        no_x_ridge(hdr)

        st, hdr, body = post(port, mean({"id": "OK"}), auth())
        assert st == 200 and body == {"id": "OK", "score_micros": 389361}
        assert type(body["score_micros"]) is int
        assert hdr.get("content-type") == "application/json"
        no_x_ridge(hdr)

        st, hdr, body = post(port, mean({"id": "CT"}), auth(ct="application/json; charset=utf-8"))
        assert st == 200 and body["score_micros"] == 389361 and type(body["score_micros"]) is int
        st, hdr, body = post(port, mean({"id": "CTNW"}), auth(ct="application/json;charset=utf-8"))
        assert st == 200 and body["score_micros"] == 389361
        # Optional whitespace after the semicolon is not limited to a single space.
        st, hdr, body = post(port, mean({"id": "CT2S"}), auth(ct="application/json;  charset=utf-8"))
        assert st == 200 and body["score_micros"] == 389361
        # Optional whitespace after the semicolon includes tab, not only spaces.
        st, hdr, body = post(port, mean({"id": "CTT"}), auth(ct="application/json;\tcharset=utf-8"))
        assert st == 200 and body["score_micros"] == 389361
        # Whitespace around the charset= assignment is illegal (only OWS after ';').
        st, hdr, body = post(port, mean({"id": "CTEQ"}), auth(ct="application/json;charset =utf-8"))
        http_fail(st, hdr, body, "INVALID_INTAKE", 400)
        st, hdr, body = post(port, mean({"id": "CTEQ2"}), auth(ct="application/json;charset= utf-8"))
        http_fail(st, hdr, body, "INVALID_INTAKE", 400)
        # Whitespace before the semicolon is not allowed.
        st, hdr, body = post(port, mean({"id": "CTBS"}), auth(ct="application/json ;charset=utf-8"))
        http_fail(st, hdr, body, "INVALID_INTAKE", 400)
        # Exact media type only — prefix match and non-utf-8 charset are illegal.
        st, hdr, body = post(port, mean({"id": "CTX"}), auth(ct="application/jsonx"))
        http_fail(st, hdr, body, "INVALID_INTAKE", 400)
        st, hdr, body = post(port, mean({"id": "CTL"}), auth(ct="application/json; charset=latin1"))
        http_fail(st, hdr, body, "INVALID_INTAKE", 400)
        st, hdr, body = post(port, mean({"id": "CTI"}), auth(ct="application/json; charset=iso-8859-1"))
        http_fail(st, hdr, body, "INVALID_INTAKE", 400)
        st, hdr, body = post(port, mean({"id": "CTF"}), auth(ct="application/json; foo=bar"))
        http_fail(st, hdr, body, "INVALID_INTAKE", 400)
        st, hdr, body = post(port, mean({"id": "CT2"}), auth(ct="application/json; charset=utf-8; foo=bar"))
        http_fail(st, hdr, body, "INVALID_INTAKE", 400)
        # charset spelling is exact lowercase utf-8 (not UTF-8).
        st, hdr, body = post(port, mean({"id": "CTU"}), auth(ct="application/json; charset=UTF-8"))
        http_fail(st, hdr, body, "INVALID_INTAKE", 400)
        # Parameter name must be exact lowercase charset (not Charset).
        st, hdr, body = post(port, mean({"id": "CTC"}), auth(ct="application/json; Charset=utf-8"))
        http_fail(st, hdr, body, "INVALID_INTAKE", 400)
        # Quoted charset values are not the contract's literal charset=utf-8 parameter.
        st, hdr, body = post(port, mean({"id": "CTQ"}), auth(ct='application/json; charset="utf-8"'))
        http_fail(st, hdr, body, "INVALID_INTAKE", 400)
        st, hdr, body = post(port, mean({"id": "CTQ2"}), auth(ct="application/json;charset=\"utf-8\""))
        http_fail(st, hdr, body, "INVALID_INTAKE", 400)

        tok = token()
        st, hdr, body = post(port, mean({"id": "A"}), auth(authorization="bearer %s" % tok))
        http_fail(st, hdr, body, "UNAUTHORIZED", 401)
        # #35: two spaces after Bearer is not the required single ASCII space.
        st, hdr, body = post(port, mean({"id": "SP"}), auth(authorization="Bearer  %s" % tok))
        http_fail(st, hdr, body, "UNAUTHORIZED", 401)
        # Separator after Bearer must be ASCII space, not a tab.
        st, hdr, body = post(port, mean({"id": "TAB"}), auth(authorization="Bearer\t%s" % tok))
        http_fail(st, hdr, body, "UNAUTHORIZED", 401)
        # Note: trailing OWS after the token is stripped by net/http before AuthOK sees
        # the header, so it cannot be graded on the HTTP path (panel-16r2 #8 disagreement).
        st, hdr, body = post(port, mean({"id": "NOAUTH"}), {"Content-Type": "application/json"})
        http_fail(st, hdr, body, "UNAUTHORIZED", 401)
        st, hdr, body = post(port, mean({"id": "C"}), auth(ct="text/plain"))
        http_fail(st, hdr, body, "INVALID_INTAKE", 400)
        st, hdr, body = post(port, mean({"id": "CTB"}), auth(ct="Application/JSON"))
        http_fail(st, hdr, body, "INVALID_INTAKE", 400)
        # #37: missing Content-Type with valid auth -> INVALID_INTAKE 400.
        st, hdr, body = post(port, mean({"id": "NOCT"}), auth(ct=None))
        http_fail(st, hdr, body, "INVALID_INTAKE", 400)
        st, hdr, body = post(port, mean({"id": "BOTH"}), auth(authorization="bearer %s" % tok, ct="text/plain"))
        http_fail(st, hdr, body, "UNAUTHORIZED", 401)

        for payload in illegal_rows():
            st, hdr, body = post(port, payload, auth())
            http_fail(st, hdr, body, "INVALID_INTAKE", 400)
        for row in bound_rows():
            st, hdr, body = post(port, row, auth())
            assert st == 200
            assert body == {"id": row["id"], "score_micros": oracle_micros(row)}
            assert type(body["score_micros"]) is int

        # HTTP must apply every field contribution (two non-mean features → offmean pin).
        off = mean({"id": "HTTPOFF", "tenure_months": 72, "util_ratio": 0.36})
        st, hdr, body = post(port, off, auth())
        assert st == 200 and body == {"id": "HTTPOFF", "score_micros": 364705}

        # Key order must not affect HTTP legality (shared order-independent intake).
        reordered = (
            b'{"open_trades":7,"revol_bal":8500,"inq_6":1.2,"delinq_24":0.8,'
            b'"util_ratio":0.35,"tenure_months":48,"id":"ORDHTTP"}'
        )
        st, hdr, body = http_req(port, "POST", "/v1/score", body=reordered, headers=auth())
        assert st == 200 and body == {"id": "ORDHTTP", "score_micros": 389361}

        # Exact-decimal range must reject on HTTP as well as CLI.
        edge_http = (
            b'{"id":"EDGEUH","tenure_months":48,"util_ratio":2.5000000000000001,'
            b'"delinq_24":0.8,"inq_6":1.2,"revol_bal":8500,"open_trades":7}'
        )
        st, hdr, body = http_req(port, "POST", "/v1/score", body=edge_http, headers=auth())
        http_fail(st, hdr, body, "INVALID_INTAKE", 400)
        sci_http = (
            b'{"id":"SCIEH","tenure_months":48,"util_ratio":2.5000000000000000000001e0,'
            b'"delinq_24":0.8,"inq_6":1.2,"revol_bal":8500,"open_trades":7}'
        )
        st, hdr, body = http_req(port, "POST", "/v1/score", body=sci_http, headers=auth())
        http_fail(st, hdr, body, "INVALID_INTAKE", 400)
        under_http = (
            b'{"id":"EDGETH","tenure_months":-1e-400,"util_ratio":0.35,'
            b'"delinq_24":0.8,"inq_6":1.2,"revol_bal":8500,"open_trades":7}'
        )
        st, hdr, body = http_req(port, "POST", "/v1/score", body=under_http, headers=auth())
        http_fail(st, hdr, body, "INVALID_INTAKE", 400)
        over200_http = (
            b'{"id":"EDGEU200H","tenure_months":48,"util_ratio":2.5'
            + (b"0" * 200) + b"1,"
            b'"delinq_24":0.8,"inq_6":1.2,"revol_bal":8500,"open_trades":7}'
        )
        st, hdr, body = http_req(port, "POST", "/v1/score", body=over200_http, headers=auth())
        http_fail(st, hdr, body, "INVALID_INTAKE", 400)
        over1000_http = (
            b'{"id":"EDGEU1000H","tenure_months":48,"util_ratio":2.5'
            + (b"0" * 1000) + b"1,"
            b'"delinq_24":0.8,"inq_6":1.2,"revol_bal":8500,"open_trades":7}'
        )
        assert len(over1000_http) < 1048576
        st, hdr, body = http_req(port, "POST", "/v1/score", body=over1000_http, headers=auth())
        http_fail(st, hdr, body, "INVALID_INTAKE", 400)
        # Large-exponent legal values must also be accepted on HTTP (parity with CLI ZHI).
        zhi_http = (
            b'{"id":"ZHIH","tenure_months":0e100001,"util_ratio":0.35,'
            b'"delinq_24":0.8,"inq_6":1.2,"revol_bal":8500,"open_trades":7}'
        )
        st, hdr, body = http_req(port, "POST", "/v1/score", body=zhi_http, headers=auth())
        assert st == 200
        assert body == {"id": "ZHIH", "score_micros": oracle_micros(mean({"id": "ZHIH", "tenure_months": 0}))}

        # Long legal JSON number tokens must be accepted on HTTP (parity with CLI; no digit-cap).
        long_util = b"1" + (b"0" * 4096) + b"e-4096"
        long_http = (
            b'{"id":"LONGH","tenure_months":48,"util_ratio":' + long_util + b','
            b'"delinq_24":0.8,"inq_6":1.2,"revol_bal":8500,"open_trades":7}'
        )
        assert len(long_http) < 1048576
        st, hdr, body = http_req(port, "POST", "/v1/score", body=long_http, headers=auth())
        assert st == 200 and body == {"id": "LONGH", "score_micros": oracle_micros(mean({"id": "LONGH", "util_ratio": 1}))}

        # #38: legal object + whitespace + second object.
        dual = (json.dumps(mean({"id": "DUAL"})) + " " + json.dumps(mean({"id": "DUAL2"}))).encode()
        st, hdr, body = http_req(port, "POST", "/v1/score", body=dual, headers=auth())
        http_fail(st, hdr, body, "INVALID_INTAKE", 400)
        # Malformed trailing data (not a second JSON value) must also be INVALID_INTAKE.
        junk = (json.dumps(mean({"id": "JUNK"})) + " garbage").encode()
        st, hdr, body = http_req(port, "POST", "/v1/score", body=junk, headers=auth())
        http_fail(st, hdr, body, "INVALID_INTAKE", 400)
        # Top-level JSON array body is illegal (even a singleton wrapping a legal object).
        arr_body = ("[" + json.dumps(mean({"id": "ARRAY"})) + "]").encode()
        st, hdr, body = http_req(port, "POST", "/v1/score", body=arr_body, headers=auth())
        http_fail(st, hdr, body, "INVALID_INTAKE", 400)
        # Top-level JSON null is INVALID_INTAKE (not a server error).
        st, hdr, body = http_req(port, "POST", "/v1/score", body=b"null", headers=auth())
        http_fail(st, hdr, body, "INVALID_INTAKE", 400)

        # #21: POST body exact 1048576 succeeds; 1048577 -> INVALID_INTAKE.
        base = json.dumps(mean({"id": "HBIG"}))
        exact = (base[:-1] + (" " * (1048576 - len(base))) + "}").encode()
        assert len(exact) == 1048576
        st, hdr, body = http_req(port, "POST", "/v1/score", body=exact, headers=auth())
        assert st == 200 and body["score_micros"] == 389361
        big = (base[:-1] + (" " * (1048577 - len(base))) + "}").encode()
        assert len(big) == 1048577
        st, hdr, body = http_req(port, "POST", "/v1/score", body=big, headers=auth())
        http_fail(st, hdr, body, "INVALID_INTAKE", 400)

        # Every wrong method on known paths → METHOD 405; unknown paths → NOT_FOUND for any method.
        # HEAD may yield an empty body under net/http; status + Content-Type still decide the case.
        def _method_or_missing(st, hdr, body, code, want_status):
            assert st == want_status
            assert hdr.get("content-type") == "application/json"
            no_x_ridge(hdr)
            if body:
                assert_err(body, code)
            else:
                assert body == {}

        for method in ("GET", "PUT", "DELETE", "OPTIONS", "PATCH", "HEAD", "TRACE"):
            st, hdr, body = http_req(port, method, "/v1/score")
            if method == "HEAD":
                _method_or_missing(st, hdr, body, "METHOD", 405)
            else:
                http_fail(st, hdr, body, "METHOD", 405)
        for method in ("POST", "PUT", "DELETE", "OPTIONS", "PATCH", "HEAD", "TRACE"):
            st, hdr, body = http_req(port, method, "/v1/health", body=mean({"id": "HM"}) if method not in ("GET", "HEAD") else None, headers=auth() if method == "POST" else None)
            if method == "HEAD":
                _method_or_missing(st, hdr, body, "METHOD", 405)
            else:
                http_fail(st, hdr, body, "METHOD", 405)
        for method in ("GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH", "HEAD", "TRACE"):
            st, hdr, body = http_req(port, method, "/nope", body=mean({"id": "NOP"}) if method in ("POST", "PUT", "PATCH") else None, headers=auth() if method in ("POST", "PUT", "PATCH") else None)
            if method == "HEAD":
                _method_or_missing(st, hdr, body, "NOT_FOUND", 404)
            else:
                http_fail(st, hdr, body, "NOT_FOUND", 404)
        # Prefix of a known route is still an unknown path.
        st, hdr, body = http_req(port, "POST", "/v1/score/extra", body=mean({"id": "PX"}), headers=auth())
        http_fail(st, hdr, body, "NOT_FOUND", 404)
        # Doubled slash is not a named contract path — must be NOT_FOUND, not a CleanPath redirect.
        st, hdr, body = http_req(port, "GET", "/v1//score")
        http_fail(st, hdr, body, "NOT_FOUND", 404)

        st, hdr, body = post(port, mean({"id": "CACHE"}), auth())
        assert st == 200 and body == {"id": "CACHE", "score_micros": 389361}
        st, hdr, body = post(port, mean({"id": "CACHE", "tenure_months": 12}), auth())
        assert st == 200 and body == {"id": "CACHE", "score_micros": 384025}

        # Shared id + near-collision util_ratio must not merge cache entries.
        hi = mean({"id": "NEAR", "util_ratio": 0.3500000932494865})
        lo = mean({"id": "NEAR", "util_ratio": 0.3500000932494864})
        st, hdr, body = post(port, hi, auth())
        assert st == 200 and body == {"id": "NEAR", "score_micros": 389360}
        st, hdr, body = post(port, lo, auth())
        assert st == 200 and body == {"id": "NEAR", "score_micros": 389361}

        # Cache key must include features beyond tenure/util (delinq_24 differs).
        d0 = mean({"id": "DLQID", "delinq_24": 0.8})
        d1 = mean({"id": "DLQID", "delinq_24": 4.0})
        st, hdr, body = post(port, d0, auth())
        assert st == 200 and body["score_micros"] == oracle_micros(d0)
        st, hdr, body = post(port, d1, auth())
        assert st == 200 and body["score_micros"] == oracle_micros(d1)
        assert body["score_micros"] != oracle_micros(d0)

        # Cache key must include inq_6 / revol_bal / open_trades as well.
        i0 = mean({"id": "INQID", "inq_6": 1.2})
        i1 = mean({"id": "INQID", "inq_6": 15})
        st, hdr, body = post(port, i0, auth())
        assert st == 200 and body["score_micros"] == oracle_micros(i0)
        st, hdr, body = post(port, i1, auth())
        assert st == 200 and body["score_micros"] == oracle_micros(i1)
        assert body["score_micros"] != oracle_micros(i0)
        r0 = mean({"id": "REVID", "revol_bal": 8500})
        r1 = mean({"id": "REVID", "revol_bal": 200000})
        st, _, b0 = post(port, r0, auth())
        st, _, b1 = post(port, r1, auth())
        assert st == 200 and b0["score_micros"] != b1["score_micros"]
        o0 = mean({"id": "OTID", "open_trades": 7})
        o1 = mean({"id": "OTID", "open_trades": 40})
        st, _, b0 = post(port, o0, auth())
        st, _, b1 = post(port, o1, auth())
        assert st == 200 and b0["score_micros"] != b1["score_micros"]

        # #29: identical legal POST twice in the same process — both 200, same micros.
        same = mean({"id": "SAME"})
        st1, _, b1 = post(port, same, auth())
        st2, _, b2 = post(port, same, auth())
        assert st1 == 200 and st2 == 200
        assert b1 == b2 == {"id": "SAME", "score_micros": 389361}

        # Concurrent POSTs under a race-instrumented binary (cache is optional).
        # Behavioral only: identical concurrent scores must agree; GORACE must stay clean.
        # Do not require cache/mutex source needles — a cache-free ScoreOne is contract-legal.
        ovlp = mean({"id": "OVLP"})
        stop(proc)
        rebuild_race()
        ge = env()
        # Log races without halt_on_error: an intermittent detector trip must not
        # flake the suite via process death; we assert the log stays clean.
        ge["GORACE"] = "atexit_sleep_ms=0"
        port = free_port()
        kv = write_sidecar(batch_limit=1)
        lines = ["listen 127.0.0.1:%d" % port if l.startswith("listen ") else l for l in kv.read_text().splitlines()]
        kv.write_text("\n".join(lines) + "\n"); own(kv)
        race_err_path = work(".race-stderr")
        race_err_f = open(race_err_path, "w")
        try:
            proc = subprocess.Popen(
                drop([str(BIN), "serve", "--config", str(kv)]),
                cwd=str(APP), stdout=subprocess.PIPE, stderr=race_err_f, text=True, env=ge,
            )
            assert wait_ready(proc, port, timeout=30.0), "race-instrumented serve failed"
            with ThreadPoolExecutor(max_workers=4) as pool:
                futs = [pool.submit(post, port, ovlp, auth(), 30) for _ in range(16)]
                for fut in as_completed(futs):
                    st, hdr, body = fut.result()
                    assert st == 200 and body == {"id": "OVLP", "score_micros": 389361}
            assert proc.poll() is None, "race-instrumented server exited early"
            stop(proc)
        finally:
            try:
                race_err_f.flush()
            except Exception:
                pass
            race_err_f.close()
        joined = race_err_path.read_text(errors="replace")
        assert "DATA RACE" not in joined, joined[-2000:]
        rebuild()
        proc, port = start_server()

        payloads = [mean({"id": "C%d" % i, "tenure_months": 12 + i * 3}) for i in range(8)]
        want = {p["id"]: oracle_micros(p) for p in payloads}
        with ThreadPoolExecutor(max_workers=4) as pool:
            futs = {pool.submit(post, port, p, auth()): p for p in payloads}
            for fut in as_completed(futs):
                orig = futs[fut]
                st, hdr, body = fut.result()
                assert st == 200 and body == {"id": orig["id"], "score_micros": want[orig["id"]]}
    finally:
        stop(proc)

    proc, port = start_server(kv=write_sidecar(token_sha="0" * 64))
    try:
        st, hdr, body = post(port, mean({"id": "H"}), auth())
        http_fail(st, hdr, body, "UNAUTHORIZED", 401)
    finally:
        stop(proc)

    # #36: alternate token_file + matching sha256 succeeds.
    alt_tok = "kelso-alt-token-%s" % secrets.token_hex(8)
    tok_path = work(".tok")
    tok_path.write_text(alt_tok + "\n"); own(tok_path)
    digest = hashlib.sha256(alt_tok.encode()).hexdigest()
    proc, port = start_server(kv=write_sidecar(token_sha=digest, token_file=str(tok_path)))
    try:
        st, hdr, body = post(port, mean({"id": "ALT"}), auth(authorization="Bearer %s" % alt_tok))
        assert st == 200 and body == {"id": "ALT", "score_micros": 389361}
        assert hdr.get("content-type") == "application/json"
        no_x_ridge(hdr)
    finally:
        stop(proc)

    # Authorization must match token_file contents, not only token_sha256 of the presented value.
    tok_a = work(".tok")
    tok_a.write_text("floor-token-A\n"); own(tok_a)
    tok_b = "floor-token-B"
    dig_b = hashlib.sha256(tok_b.encode()).hexdigest()
    proc, port = start_server(kv=write_sidecar(token_sha=dig_b, token_file=str(tok_a)))
    try:
        st, hdr, body = post(port, mean({"id": "MISMATCH"}), auth(authorization="Bearer %s" % tok_b))
        http_fail(st, hdr, body, "UNAUTHORIZED", 401)
    finally:
        stop(proc)

def test_cli_flags_and_bench_recovery():
    """CLI flag legality for all subcommands plus bench recovery isolating cases."""
    rebuild()
    x = stale()
    r = run(["score", "-out", str(x), "--intake", "/app/fixtures/week41-intake.jsonl"])
    assert r.returncode != 0; assert_cli_clean(r.stdout, r.stderr)
    # Illegal -out alone is not a contract --out PATH/--out=PATH cleanup target.
    # Nonzero + clean diagnostics are required; leaving the stale path is permitted.
    x = stale()
    r = run(["score", "-config", "/app/deploy/sidecar.kv", "--intake", "/app/fixtures/week41-intake.jsonl", "--out", str(x)])
    assert r.returncode != 0; assert_cli_clean(r.stdout, r.stderr)
    assert not x.exists()
    x = stale()
    r = run(["score", "-intake", "/app/fixtures/week41-intake.jsonl", "--out", str(x)])
    assert r.returncode != 0; assert_cli_clean(r.stdout, r.stderr)
    assert not x.exists()
    # --out naming an existing directory (including `.`) is left in place on failure.
    blank = write_intake(b"\n")
    r = run(["score", "--intake", str(blank), "--out", "."], cwd=APP)
    assert r.returncode != 0
    assert APP.is_dir()
    assert_cli_clean(r.stdout, r.stderr)
    # Unknown flag before a later --out=PATH still removes that named path.
    x = stale()
    gone(run([
        "score", "--intake", "/app/fixtures/week41-intake.jsonl",
        "--bogus", "--out=%s" % x,
    ]), x)
    # Unknown flag before a split-form --out PATH must also remove the path.
    x = stale()
    gone(run([
        "score", "--intake", "/app/fixtures/week41-intake.jsonl",
        "--bogus", "--out", str(x),
    ]), x)
    a = stale()
    gone(run(["score", "--intake", "/app/fixtures/week41-intake.jsonl", "--out", str(a), "--"]), a)
    assert run(["score", "--intake", "/app/fixtures/week41-intake.jsonl", "--out", str(work(".jsonl")), "leftover"]).returncode != 0
    # Leftover positionals must also remove the named --out path.
    leftover_out = stale()
    gone(run([
        "score", "--intake", "/app/fixtures/week41-intake.jsonl",
        "--out", str(leftover_out), "leftover",
    ]), leftover_out)
    # #46: score rejects flags outside its allowed set.
    assert run([
        "score", "--intake", "/app/fixtures/week41-intake.jsonl",
        "--out", str(work(".jsonl")), "--verbose",
    ]).returncode != 0
    # Score allowlist excludes bench's --spec (not only --verbose).
    spec_out = stale()
    gone(run([
        "score", "--intake", "/app/fixtures/week41-intake.jsonl",
        "--out", str(spec_out), "--spec", "/app/bench/loadspec.txt",
    ]), spec_out)
    # #47 (also in scoring): missing --out.
    assert run(["score", "--intake", "/app/fixtures/week41-intake.jsonl"]).returncode != 0
    # #45: selfcheck --config not allowed.
    assert run(["selfcheck", "--config", "/app/deploy/sidecar.kv"]).returncode != 0

    assert run(["serve", "--intake", "/app/fixtures/week41-intake.jsonl"]).returncode != 0
    # #44/#50/#66/#67: serve flag set — single-hyphen, --out, leftover, bare --, --spec.
    assert run(["serve", "-config", "/app/deploy/sidecar.kv"]).returncode != 0
    assert run(["serve", "--out", str(work(".json"))]).returncode != 0
    assert run(["serve", "--spec", "/app/bench/loadspec.txt"]).returncode != 0
    assert run(["serve", "leftover"]).returncode != 0
    assert run(["serve", "--"]).returncode != 0
    assert run(["serve", "--verbose"]).returncode != 0

    # #44/#48/#49/#51/#66: bench flag set.
    bout = work(".json")
    assert run(["bench", "-spec", "/app/bench/loadspec.txt", "--out", str(bout)]).returncode != 0
    assert run(["bench", "-config", "/app/deploy/sidecar.kv", "--out", str(bout)]).returncode != 0
    assert run(["bench", "--spec", "/app/bench/loadspec.txt", "-out", str(bout)]).returncode != 0
    assert run(["bench", "--"]).returncode != 0
    assert run(["bench", "--out", str(bout), "--verbose"]).returncode != 0
    assert run(["bench", "leftover"]).returncode != 0
    assert run(["bench", "--intake", "/app/fixtures/week41-intake.jsonl"]).returncode != 0
    # #16: --config must load the supplied path (not silently fall back to the default sidecar).
    assert run([
        "bench", "--config", "/work/out/nonexistent.kv",
        "--spec", "/app/bench/loadspec.txt", "--out", str(work(".json")),
    ]).returncode != 0
    assert run([
        "bench", "--config", str(write_sidecar(omit=True)),
        "--spec", "/app/bench/loadspec.txt", "--out", str(work(".json")),
    ]).returncode != 0
    # Trailing valueless --config is illegal (flag requires PATH).
    assert run([
        "score", "--intake", "/app/fixtures/week41-intake.jsonl",
        "--out", str(work(".jsonl")), "--config",
    ]).returncode != 0

    side, side_b = SIDECAR, SIDECAR.read_bytes()
    port = free_port()
    proc = None
    try:
        side.write_text(side_b.decode().replace("127.0.0.1:17441", "127.0.0.1:%d" % port, 1))
        own(side)
        proc = subprocess.Popen(drop([str(BIN), "serve"]), cwd=str(APP), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env())
        assert wait_ready(proc, port)
        st, hdr, body = post(port, mean({"id": "DEFSRV"}), auth())
        assert st == 200 and body["score_micros"] == 389361
    finally:
        if proc is not None: stop(proc)
        side.write_bytes(side_b)

    out = work(".json")
    r1 = run(["bench", "--out", str(out)])
    assert r1.returncode == 0
    assert ledger_obj(out) == BENCH
    bytes1 = out.read_bytes()
    # Same --out path: stdout and product bytes must be identical for identical inputs.
    r2 = run(["bench", "--out", str(out)])
    assert r2.returncode == 0 and ledger_obj(out) == BENCH
    assert r1.stdout == r2.stdout
    assert out.read_bytes() == bytes1
    if LEDGER.exists(): LEDGER.unlink()
    assert run(["bench"]).returncode == 0
    assert ledger_obj(LEDGER) == BENCH
    assert LEDGER.read_bytes() == bytes1

    spec, pout = work(".txt"), work(".json")
    spec.write_text("end\n")
    r = run(["bench", "--spec", str(spec), "--out", str(pout)])
    assert r.returncode != 0; assert_cli_clean(r.stdout, r.stderr)
    # Empty-loadspec failure diagnostics must be byte-identical across identical invocations.
    r_empty2 = run(["bench", "--spec", str(spec), "--out", str(work(".json"))])
    assert r_empty2.returncode != 0
    assert r.stdout == r_empty2.stdout and r.stderr == r_empty2.stderr
    delivered = (APP / "bench" / "loadspec.txt").read_text()
    spec.write_text(delivered.replace("tenure_months 48", "tenure_months 49", 1))
    assert run(["bench", "--spec", str(spec), "--out", str(pout)]).returncode == 0
    assert ledger_obj(pout) == BENCH
    # Costing depends only on scenario batch B — other seed fields must not change the ledger.
    spec.write_text(delivered.replace("util_ratio 0.35", "util_ratio 0.36", 1))
    assert run(["bench", "--spec", str(spec), "--out", str(pout)]).returncode == 0
    assert ledger_obj(pout) == BENCH
    spec.write_text(delivered.replace("seed_id LS-0", "seed_id LS-1", 1))
    assert run(["bench", "--spec", str(spec), "--out", str(pout)]).returncode == 0
    assert ledger_obj(pout) == BENCH
    for old, new in (
        ("delinq_24 0.8", "delinq_24 0.9"),
        ("inq_6 1.2", "inq_6 1.3"),
        ("revol_bal 8500", "revol_bal 8501"),
        ("open_trades 7", "open_trades 8"),
    ):
        spec.write_text(delivered.replace(old, new, 1))
        assert run(["bench", "--spec", str(spec), "--out", str(pout)]).returncode == 0
        assert ledger_obj(pout) == BENCH

    # Live S=15 / C=12 isolating specs (#31–#34, #64) before mutating recovery files.
    spec.write_text("scenario coal repeat 1 batch 3\nscenario solo repeat 1 batch 1\n")
    assert run(["bench", "--spec", str(spec), "--out", str(pout)]).returncode == 0
    led = ledger_obj(pout)
    assert led["avg_latency_ticks"] == 13
    assert led["throughput"] == 78431
    assert led["total_requests"] == 4 and led["total_ticks"] == 51

    # #23: half-up average — batch 5 + singleton = 75/6 = 12.5 → floor(x+1/2)=13 (not ties-to-even 12).
    spec.write_text("scenario coal repeat 1 batch 5\nscenario solo repeat 1 batch 1\n")
    assert run(["bench", "--spec", str(spec), "--out", str(pout)]).returncode == 0
    led = ledger_obj(pout)
    assert led["avg_latency_ticks"] == 13
    assert led["total_requests"] == 6 and led["total_ticks"] == 75

    # #4: repeat>1 with coalesced batch expands to repeat*batch members.
    spec.write_text("scenario coal repeat 2 batch 2\n")
    assert run(["bench", "--spec", str(spec), "--out", str(pout)]).returncode == 0
    led = ledger_obj(pout)
    assert led["total_requests"] == 4 and led["total_ticks"] == 48
    assert led["avg_latency_ticks"] == 12 and led["memory_units"] == 72

    # #5: memory_units uses the largest admitted batch, not the first scenario's batch.
    spec.write_text("scenario small repeat 1 batch 2\nscenario large repeat 1 batch 3\n")
    assert run(["bench", "--spec", str(spec), "--out", str(pout)]).returncode == 0
    led = ledger_obj(pout)
    assert led["memory_units"] == 76
    assert led["total_requests"] == 5 and led["total_ticks"] == 60

    # Repeat zero is not admitted; a sibling positive scenario still counts alone.
    spec.write_text("scenario bad repeat 0 batch 1\nscenario good repeat 1 batch 1\n")
    assert run(["bench", "--spec", str(spec), "--out", str(pout)]).returncode == 0
    led = ledger_obj(pout)
    assert led["total_requests"] == 1 and led["total_ticks"] == 15
    assert led["memory_units"] == 68
    # Non-decimal repeat tokens are also excluded when another scenario is admitted.
    spec.write_text("scenario bad repeat 1.5 batch 1\nscenario good repeat 1 batch 1\n")
    assert run(["bench", "--spec", str(spec), "--out", str(pout)]).returncode == 0
    led = ledger_obj(pout)
    assert led["total_requests"] == 1 and led["total_ticks"] == 15
    assert led["memory_units"] == 68
    # Zero-repeat with a large batch must not inflate memory_units via max-batch side effects.
    spec.write_text("scenario zero repeat 0 batch 100\nscenario solo repeat 1 batch 1\n")
    assert run(["bench", "--spec", str(spec), "--out", str(pout)]).returncode == 0
    led = ledger_obj(pout)
    assert led["total_requests"] == 1 and led["total_ticks"] == 15
    assert led["memory_units"] == 68
    # Negative repeats are not admitted (not merely "nonzero").
    spec.write_text("scenario bad repeat -1 batch 1\nscenario good repeat 2 batch 1\n")
    assert run(["bench", "--spec", str(spec), "--out", str(pout)]).returncode == 0
    led = ledger_obj(pout)
    assert led["total_requests"] == 2 and led["total_ticks"] == 30
    assert led["memory_units"] == 68
    # Nonpositive batch is not admitted (empty admitted set → bench failure).
    spec.write_text("scenario bad repeat 1 batch 0\n")
    r = run(["bench", "--spec", str(spec), "--out", str(pout)])
    assert r.returncode != 0
    assert_cli_clean(r.stdout, r.stderr)
    # Invalid batch excluded when a valid sibling is admitted; memory uses admitted max only.
    spec.write_text("scenario bad repeat 1 batch 0\nscenario good repeat 1 batch 1\n")
    assert run(["bench", "--spec", str(spec), "--out", str(pout)]).returncode == 0
    led = ledger_obj(pout)
    assert led["total_requests"] == 1 and led["total_ticks"] == 15
    assert led["memory_units"] == 68
    # Negative and non-integral batch tokens are excluded (not truncated / not !=0).
    spec.write_text("scenario bad repeat 1 batch -1\nscenario good repeat 1 batch 1\n")
    assert run(["bench", "--spec", str(spec), "--out", str(pout)]).returncode == 0
    led = ledger_obj(pout)
    assert led["total_requests"] == 1 and led["total_ticks"] == 15
    assert led["memory_units"] == 68
    spec.write_text("scenario bad repeat 1 batch 1.5\nscenario good repeat 1 batch 1\n")
    assert run(["bench", "--spec", str(spec), "--out", str(pout)]).returncode == 0
    led = ledger_obj(pout)
    assert led["total_requests"] == 1 and led["total_ticks"] == 15
    assert led["memory_units"] == 68
    # Exponent notation is not a decimal-integer token even when the value is integral.
    spec.write_text("scenario bad repeat 1e2 batch 1\nscenario good repeat 1 batch 1\n")
    assert run(["bench", "--spec", str(spec), "--out", str(pout)]).returncode == 0
    led = ledger_obj(pout)
    assert led["total_requests"] == 1 and led["total_ticks"] == 15
    assert led["memory_units"] == 68
    spec.write_text("scenario bad repeat 1 batch 1e2\nscenario good repeat 1 batch 1\n")
    assert run(["bench", "--spec", str(spec), "--out", str(pout)]).returncode == 0
    led = ledger_obj(pout)
    assert led["total_requests"] == 1 and led["total_ticks"] == 15
    assert led["memory_units"] == 68
    # Batch above signed int64 is not admitted (repeat may still be arbitrary-size).
    spec.write_text(
        "scenario bad repeat 1 batch 9223372036854775808\n"
        "scenario good repeat 1 batch 1\n"
    )
    assert run(["bench", "--spec", str(spec), "--out", str(pout)]).returncode == 0
    led = ledger_obj(pout)
    assert led["total_requests"] == 1 and led["total_ticks"] == 15
    assert led["memory_units"] == 68

    # Batch above 99 must be admitted (no silent batch-100 cap).
    spec.write_text("scenario tiny repeat 1 batch 1\nscenario edge repeat 1 batch 101\n")
    assert run(["bench", "--spec", str(spec), "--out", str(pout)]).returncode == 0
    led = ledger_obj(pout)
    assert led["total_requests"] == 102 and led["total_ticks"] == 1227
    assert led["memory_units"] == 468
    # Batch 129 is still below signed-int64 and must be admitted (no silent 128-cap).
    spec.write_text("scenario coal repeat 1 batch 129\n")
    assert run(["bench", "--spec", str(spec), "--out", str(pout)]).returncode == 0
    led = ledger_obj(pout)
    assert led["total_requests"] == 129 and led["total_ticks"] == 1548
    assert led["memory_units"] == 580
    # Legal signed-64 batch whose B*C exceeds MaxInt64 must still expand (big-int ticks).
    spec.write_text("scenario coal repeat 1 batch 768614336404564651\n")
    assert run(["bench", "--spec", str(spec), "--out", str(pout)]).returncode == 0
    led = ledger_obj(pout)
    assert led["total_requests"] == 768614336404564651
    assert led["total_ticks"] == 9223372036854775812
    assert led["memory_units"] == 64 + 4 * 768614336404564651
    # memory_units itself may exceed signed int64 (64+4B with large legal B).
    spec.write_text("scenario coal repeat 1 batch 2305843009213693936\n")
    assert run(["bench", "--spec", str(spec), "--out", str(pout)]).returncode == 0
    led = ledger_obj(pout)
    assert led["total_requests"] == 2305843009213693936
    assert led["memory_units"] == 9223372036854775808
    assert led["total_ticks"] == 2305843009213693936 * 12
    # Signed-int64 maximum batch must be admitted (B <= MaxInt64, not B < MaxInt64).
    spec.write_text("scenario edge repeat 1 batch 9223372036854775807\n")
    assert run(["bench", "--spec", str(spec), "--out", str(pout)]).returncode == 0
    led = ledger_obj(pout)
    assert led["total_requests"] == 9223372036854775807
    assert led["total_ticks"] == 9223372036854775807 * 12
    assert led["memory_units"] == 64 + 4 * 9223372036854775807
    assert led["cpu_units"] == led["total_ticks"]

    # #14: p99 index is ceil(0.99n)-1, not floor(0.99n); n=100 → index 98 → C.
    spec.write_text("scenario coal repeat 1 batch 99\nscenario solo repeat 1 batch 1\n")
    assert run(["bench", "--spec", str(spec), "--out", str(pout)]).returncode == 0
    led = ledger_obj(pout)
    assert led["p95_ticks"] == 12 and led["p99_ticks"] == 12
    assert led["total_requests"] == 100 and led["total_ticks"] == 1203

    spec.write_text("scenario coal repeat 1 batch 19\nscenario solo repeat 1 batch 1\n")
    assert run(["bench", "--spec", str(spec), "--out", str(pout)]).returncode == 0
    led = ledger_obj(pout)
    assert led["p95_ticks"] == 12 and led["p99_ticks"] == 15

    spec.write_text("scenario solo repeat 1 batch 1\n")
    assert run(["bench", "--spec", str(spec), "--out", str(pout)]).returncode == 0
    led = ledger_obj(pout)
    assert led["throughput"] == 66667
    assert led["total_requests"] == 1 and led["total_ticks"] == 15

    # Half-up throughput at an exact .5 tie: 27e6/384 = 70312.5 → 70313 (not banker's 70312).
    spec.write_text("scenario solo repeat 20 batch 1\nscenario coal repeat 1 batch 7\n")
    assert run(["bench", "--spec", str(spec), "--out", str(pout)]).returncode == 0
    led = ledger_obj(pout)
    assert led["total_requests"] == 27 and led["total_ticks"] == 384
    assert led["throughput"] == 70313
    # Percentiles require sorting: singleton-first expansion order would yield p95=p99=12.
    assert led["p95_ticks"] == 15 and led["p99_ticks"] == 15

    # Singleton-then-coalesce: sorted costs put S last → p95=p99=S (not C from expansion order).
    spec.write_text("scenario solo repeat 1 batch 1\nscenario coal repeat 1 batch 2\n")
    assert run(["bench", "--spec", str(spec), "--out", str(pout)]).returncode == 0
    led = ledger_obj(pout)
    assert led["total_requests"] == 3 and led["total_ticks"] == 39
    assert led["p95_ticks"] == 15 and led["p99_ticks"] == 15

    spec.write_text("scenario coal repeat 1 batch 2\n")
    assert run(["bench", "--spec", str(spec), "--out", str(pout)]).returncode == 0
    led = ledger_obj(pout)
    assert led["memory_units"] == 72

    recovery, anchor = APP / "policy" / "bench-recovery-loadspec.txt", APP / "policy" / "bench-anchor.json"
    rb, ab = recovery.read_bytes(), anchor.read_bytes()
    seed = "seed tenure_months 48 util_ratio 0.35 delinq_24 0.8 inq_6 1.2 revol_bal 8500 open_trades 7\n"
    seed_alt = "seed tenure_months 49 util_ratio 0.35 delinq_24 0.8 inq_6 1.2 revol_bal 8500 open_trades 7\n"
    try:
        recovery.write_text("seed_id LS\n" + seed + "scenario coal repeat 1 batch 2\nscenario solo repeat 1 batch 1\nend\n")
        anchor.write_text(json.dumps(dict(total_requests=3, total_ticks=512, avg_latency_ticks=171, p95_ticks=256, p99_ticks=256, memory_units=72)) + "\n")
        spec.write_text("scenario coal repeat 1 batch 2\n")
        assert run(["bench", "--spec", str(spec), "--out", str(pout)]).returncode == 0
        led = ledger_obj(pout)
        assert led["total_ticks"] == 256 and led["p95_ticks"] == 128
        assert led["memory_units"] == 72
        assert led["throughput"] == 7813

        # Recovery seed feature values are not frozen to the delivered constants.
        recovery.write_text("seed_id LS\n" + seed_alt + "scenario coal repeat 1 batch 2\nscenario solo repeat 1 batch 1\nend\n")
        assert run(["bench", "--spec", str(spec), "--out", str(pout)]).returncode == 0
        led = ledger_obj(pout)
        assert led["total_ticks"] == 256 and led["p95_ticks"] == 128
        assert led["throughput"] == 7813

        # Recovery with unequal anchor percentiles (p95=C, p99=S) uniquely fixes S=15,C=12.
        recovery.write_text("seed_id LS\n" + seed + "scenario coal repeat 1 batch 19\nscenario solo repeat 1 batch 1\nend\n")
        anchor.write_text(json.dumps(dict(
            total_requests=20, total_ticks=243, avg_latency_ticks=12,
            p95_ticks=12, p99_ticks=15, memory_units=68,
        )) + "\n")
        spec.write_text("scenario solo repeat 1 batch 1\n")
        assert run(["bench", "--spec", str(spec), "--out", str(pout)]).returncode == 0
        led = ledger_obj(pout)
        assert led["total_ticks"] == 15 and led["p95_ticks"] == 15 and led["p99_ticks"] == 15

        # #13: both anchor percentiles equal C (batch 99 + singleton uniquely fix S=15,C=12).
        recovery.write_text("seed_id LS\n" + seed + "scenario coal repeat 1 batch 99\nscenario solo repeat 1 batch 1\nend\n")
        anchor.write_text(json.dumps(dict(
            total_requests=100, total_ticks=1203, avg_latency_ticks=12,
            p95_ticks=12, p99_ticks=12, memory_units=68,
        )) + "\n")
        spec.write_text("scenario solo repeat 1 batch 1\n")
        assert run(["bench", "--spec", str(spec), "--out", str(pout)]).returncode == 0
        led = ledger_obj(pout)
        assert led["total_ticks"] == 15 and led["p95_ticks"] == 15 and led["p99_ticks"] == 15

        # Delivered-shape recovery whose avg cannot be reproduced by any S,C pair must fail.
        # Keep recovery loadspec + totals/percentiles that uniquely fix S=15,C=12, but set avg=13.
        recovery.write_bytes(rb)
        anchor.write_text(json.dumps(dict(
            total_requests=21, total_ticks=258, avg_latency_ticks=13,
            p95_ticks=15, p99_ticks=15, memory_units=128,
        )) + "\n")
        # Also keep the delivered recovery scenarios so totals/percentiles would recover S,C
        # if avg were ignored — contract requires all five metrics.
        r = run(["bench", "--spec", str(spec), "--out", str(pout)])
        assert r.returncode != 0
        assert_cli_clean(r.stdout, r.stderr)

        # total_requests alone disagreeing with the recovery expansion must fail recovery.
        recovery.write_bytes(rb)
        anchor.write_text(json.dumps(dict(
            total_requests=22, total_ticks=258, avg_latency_ticks=12,
            p95_ticks=15, p99_ticks=15, memory_units=128,
        )) + "\n")
        r = run(["bench", "--spec", str(spec), "--out", str(pout)])
        assert r.returncode != 0
        assert_cli_clean(r.stdout, r.stderr)

        # C<S must be decisive: S=2,C=3 matches ticks/percentiles/avg but violates C<S.
        recovery.write_text("seed_id LS\n" + seed + "scenario coal repeat 1 batch 2\nscenario solo repeat 1 batch 1\nend\n")
        anchor.write_text(json.dumps(dict(
            total_requests=3, total_ticks=8, avg_latency_ticks=3,
            p95_ticks=3, p99_ticks=3, memory_units=72,
        )) + "\n")
        r = run(["bench", "--spec", str(spec), "--out", str(pout)])
        assert r.returncode != 0
        assert_cli_clean(r.stdout, r.stderr)

        # Independently wrong p95 (delivered shape otherwise) must fail recovery.
        recovery.write_bytes(rb)
        anchor.write_text(json.dumps(dict(
            total_requests=21, total_ticks=258, avg_latency_ticks=12,
            p95_ticks=12, p99_ticks=15, memory_units=128,
        )) + "\n")
        r = run(["bench", "--spec", str(spec), "--out", str(pout)])
        assert r.returncode != 0
        assert_cli_clean(r.stdout, r.stderr)

        # Equal-C percentiles with a non-delivered S,C pair (S=16,C=12).
        recovery.write_text("seed_id LS\n" + seed + "scenario coal repeat 1 batch 99\nscenario solo repeat 1 batch 1\nend\n")
        anchor.write_text(json.dumps(dict(
            total_requests=100, total_ticks=1204, avg_latency_ticks=12,
            p95_ticks=12, p99_ticks=12, memory_units=68,
        )) + "\n")
        spec.write_text("scenario solo repeat 1 batch 1\n")
        assert run(["bench", "--spec", str(spec), "--out", str(pout)]).returncode == 0
        led = ledger_obj(pout)
        assert led["total_ticks"] == 16 and led["p95_ticks"] == 16 and led["p99_ticks"] == 16

        # #22: singleton-only big-integer recovery uniquely determines S=2,C=1.
        recovery.write_text("seed_id LS\n" + seed + "scenario solo repeat 5000000000000000000 batch 1\nend\n")
        anchor.write_text(json.dumps(dict(
            total_requests=5000000000000000000,
            total_ticks=10000000000000000000,
            avg_latency_ticks=2,
            p95_ticks=2,
            p99_ticks=2,
            memory_units=68,
        )) + "\n")
        spec.write_text("scenario check repeat 1 batch 1\n")
        assert run(["bench", "--spec", str(spec), "--out", str(pout)]).returncode == 0
        led = ledger_obj(pout)
        assert led == {
            "total_requests": 1, "total_ticks": 2, "throughput": 500000,
            "avg_latency_ticks": 2, "p95_ticks": 2, "p99_ticks": 2,
            "cpu_units": 2, "memory_units": 68,
        }

        # #30: S and C themselves exceed int64; percentiles equal S — also coalesce C.
        s_big = 9223372036854775810
        c_big = 9223372036854775809
        tot_big = 2 * c_big + s_big
        recovery.write_text("seed_id LS\n" + seed + "scenario coal repeat 1 batch 2\nscenario solo repeat 1 batch 1\nend\n")
        anchor.write_text(json.dumps(dict(
            total_requests=3,
            total_ticks=tot_big,
            avg_latency_ticks=(2 * tot_big + 3) // 6,
            p95_ticks=s_big,
            p99_ticks=s_big,
            memory_units=72,
        )) + "\n")
        spec.write_text("scenario s repeat 1 batch 1\n")
        assert run(["bench", "--spec", str(spec), "--out", str(pout)]).returncode == 0
        led = ledger_obj(pout)
        assert led["total_requests"] == 1
        assert led["total_ticks"] == s_big
        assert led["p95_ticks"] == s_big and led["p99_ticks"] == s_big
        assert led["cpu_units"] == s_big
        assert led["memory_units"] == 68
        assert led["avg_latency_ticks"] == s_big
        assert led["throughput"] == (1000000 * 1 + s_big // 2) // s_big
        # Coalesced expansion must retain C above int64 (not only singleton S).
        spec.write_text("scenario coal repeat 1 batch 2\n")
        assert run(["bench", "--spec", str(spec), "--out", str(pout)]).returncode == 0
        led = ledger_obj(pout)
        assert led["total_requests"] == 2
        assert led["total_ticks"] == 2 * c_big
        assert led["p95_ticks"] == c_big and led["p99_ticks"] == c_big
        assert led["cpu_units"] == 2 * c_big
        assert led["memory_units"] == 72

        # Large S/C with both anchor percentiles equal to C (not only equal to S).
        tot_eqc = 99 * c_big + s_big
        recovery.write_text("seed_id LS\n" + seed + "scenario coal repeat 1 batch 99\nscenario solo repeat 1 batch 1\nend\n")
        anchor.write_text(json.dumps(dict(
            total_requests=100,
            total_ticks=tot_eqc,
            avg_latency_ticks=(2 * tot_eqc + 100) // 200,
            p95_ticks=c_big,
            p99_ticks=c_big,
            memory_units=68,
        )) + "\n")
        spec.write_text("scenario solo repeat 1 batch 1\n")
        assert run(["bench", "--spec", str(spec), "--out", str(pout)]).returncode == 0
        led = ledger_obj(pout)
        assert led["total_ticks"] == s_big
        assert led["p95_ticks"] == s_big and led["p99_ticks"] == s_big

        # Repeat counts beyond signed int64 must still expand.
        recovery.write_bytes(rb); anchor.write_bytes(ab)
        spec.write_text("scenario large repeat 9223372036854775808 batch 1\n")
        assert run(["bench", "--spec", str(spec), "--out", str(pout)]).returncode == 0
        led = ledger_obj(pout)
        assert led["total_requests"] == 9223372036854775808
        assert led["total_ticks"] == 138350580552821637120
        assert led["avg_latency_ticks"] == 15 and led["p95_ticks"] == 15 and led["p99_ticks"] == 15
        assert led["throughput"] == 66667 and led["memory_units"] == 68
        assert led["cpu_units"] == led["total_ticks"]
        # Beyond-int64 repeat must also expand for coalesced scenarios (not only singletons).
        spec.write_text("scenario coal repeat 9223372036854775808 batch 2\n")
        assert run(["bench", "--spec", str(spec), "--out", str(pout)]).returncode == 0
        led = ledger_obj(pout)
        assert led["total_requests"] == 18446744073709551616
        assert led["total_ticks"] == 221360928884514619392
        assert led["memory_units"] == 72
        assert led["cpu_units"] == led["total_ticks"]
        # Repeat token itself may exceed unsigned 64-bit (not only signed); no uint64 ceiling.
        # Under delivered S=15: total_requests = 2^64, ticks = 15 * 2^64.
        spec.write_text("scenario solo repeat 18446744073709551616 batch 1\n")
        assert run(["bench", "--spec", str(spec), "--out", str(pout)]).returncode == 0
        led = ledger_obj(pout)
        assert led["total_requests"] == 18446744073709551616
        assert led["total_ticks"] == 276701161105643274240
        assert led["cpu_units"] == led["total_ticks"]
        assert led["throughput"] == 66667 and led["memory_units"] == 68
        assert led["avg_latency_ticks"] == 15 and led["p95_ticks"] == 15 and led["p99_ticks"] == 15
    finally:
        recovery.write_bytes(rb); anchor.write_bytes(ab)

    want, want_dirs = {}, {}
    for rel, digest in DIGESTS.items():
        p = APP / rel
        st = os.lstat(p)
        assert stat.S_ISREG(st.st_mode) and not p.is_symlink(), rel
        assert stat.S_IMODE(st.st_mode) == MODES[rel], (rel, oct(stat.S_IMODE(st.st_mode)), oct(MODES[rel]))
        fd = os.open(str(p), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            h = hashlib.sha256()
            while True:
                b = os.read(fd, 65536)
                if not b: break
                h.update(b)
        finally:
            os.close(fd)
        assert h.hexdigest() == digest, rel
        top, rest = rel.split("/", 1)
        want.setdefault(top, set()).add(rest)
        parts = rest.split("/")
        d = want_dirs.setdefault(top, set())
        for i in range(len(parts) - 1):
            d.add("/".join(parts[: i + 1]))
    for top, names in want.items():
        top_path = APP / top
        top_st = os.lstat(top_path)
        assert stat.S_ISDIR(top_st.st_mode) and not top_path.is_symlink(), top
        got, got_dirs = set(), set()
        for dirpath, dirnames, filenames in os.walk(APP / top, followlinks=False):
            rel_dir = os.path.relpath(dirpath, APP / top)
            if rel_dir != ".":
                assert not stat.S_ISLNK(os.lstat(dirpath).st_mode), dirpath
                got_dirs.add(rel_dir)
            for dname in dirnames:
                assert not (Path(dirpath) / dname).is_symlink()
            for name in filenames:
                p = Path(dirpath) / name
                assert not p.is_symlink(), p
                if p.is_file():
                    got.add(str(p.relative_to(APP / top)))
        assert got == names
        assert got_dirs == want_dirs.get(top, set())
