"""Grading for the Kilnbridge netlist reconstructor.

The ordinance at /app/canon/synth-ordinance.txt is the contract. These tests
rebuild /app/cmd/netloom and compare the graph and attest that contract names.
"""

from __future__ import annotations

import re

from desk_harness import *


def _output_identity(path: Path) -> dict:
    info = {"symlink": path.is_symlink()}
    if path.is_symlink():
        info["link"] = os.readlink(path)
        st = path.lstat()
        info["ino"] = (st.st_dev, st.st_ino)
    elif path.is_file():
        info["bytes"] = path.read_bytes()
        st = path.stat()
        info["ino"] = (st.st_dev, st.st_ino)
    elif path.exists():
        st = path.stat()
        info["special"] = (st.st_dev, st.st_ino, st.st_mode)
    return info


def _assert_output_identity(path: Path, info: dict) -> None:
    if info.get("symlink"):
        assert path.is_symlink(), path
        assert os.readlink(path) == info["link"], path
        st = path.lstat()
        assert (st.st_dev, st.st_ino) == info["ino"], path
    elif "bytes" in info:
        assert path.is_file() and not path.is_symlink(), path
        assert path.read_bytes() == info["bytes"], path
        st = path.stat()
        assert (st.st_dev, st.st_ino) == info["ino"], path
    elif "special" in info:
        st = path.stat()
        assert (st.st_dev, st.st_ino, st.st_mode) == info["special"], path
    else:
        assert not path.exists() and not path.is_symlink(), path


def test_shipped_desk_artifacts(shipped_artifacts):
    """Rebuilt reconstructor writes the sealed graph and attest for the shipped desk."""
    led = load_graph()
    assert set(led.keys()) == {"desk", "findings", "patches"}
    assert led["desk"] == "KilnbridgeNetloom"
    raw = read_guarded(GRAPH, APP)
    assert_graph_indent(raw)
    sealed = json.loads(SEALED_GRAPH)
    assert issue_identity(led) == issue_identity(sealed)
    assert repair_identity(led) == repair_identity(sealed)
    assert_module_derived_epochs(led, APP / "history" / "synth-epochs.ndjson")
    attest_raw = read_guarded(ATTEST, APP)
    assert sha256_bytes(attest_raw) == sha256_bytes(SEALED_ATTEST)
    lines = attest_raw.decode().splitlines()
    assert lines[0] == "NPG1"
    assert lines[-1].startswith("SEAL|")
    assert len(lines[-1].split("|", 1)[1]) == 64
    body, _, seal_line = attest_raw.decode().rpartition("SEAL|")
    assert body.endswith("\n")
    assert seal_line.strip() == sha256_bytes(body.encode())
    for line in lines:
        if line.startswith("NODE|"):
            parts = line.split("|")
            assert len(parts) == 5
            clk = parts[3]
            assert clk == clk.lower()
    assert_stream_safe(shipped_artifacts.stdout + "\n" + shipped_artifacts.stderr)
    assert_attest_safe(attest_raw.decode())
    codes = {item["code"] for item in led["findings"]}
    assert FIND_FAMILIES.issubset(codes), "missing %s" % (FIND_FAMILIES - codes)
    shipped_pairs = {(item["code"], item["part"]) for item in led["findings"]}
    assert ("AREA_REGRESS", "dbg-west") not in shipped_pairs
    keys = [(item["epoch"], item["code"], item["part"]) for item in led["findings"]]
    assert keys == sorted(keys)
    verbs = repair_verbs()
    assert len(led["patches"]) == len(led["findings"])
    for idx, (issue, repair) in enumerate(zip(led["findings"], led["patches"]), start=1):
        assert repair["rank"] == idx
        assert repair["part"] == issue["part"]
        assert repair["epoch"] == issue["epoch"]
        assert repair["verb"] == verbs[issue["code"]]
    report_keys = []
    for line in lines:
        if line.startswith("FIND|"):
            _, code, part, epoch = line.split("|", 3)
            report_keys.append((code, part, int(epoch)))
    assert report_keys == [(item["code"], item["part"], item["epoch"]) for item in led["findings"]]


def test_protected_inputs_unmodified():
    """Ordinance, matrix, journal, and shipped module files stay byte-identical."""
    assert_protected_inputs_intact()


def test_ember_holdout():
    """Held-out incremental wave rebuilds sealed identities and the hard recover/clock edges."""
    hold = WORK / "ember-src"
    _materialize(HOLDOUT_TREE, hold)
    out_graph = WORK / "ember-graph.json"
    out_attest = WORK / "ember-attest.npg"
    run_netloom(
        hold / "history" / "synth-epochs.ndjson",
        hold / "netlist" / "modules",
        APP / "canon" / "synth-ordinance.txt",
        MATRIX,
        out_graph,
        out_attest,
    )
    live = json.loads(read_guarded(out_graph, WORK))
    sealed = json.loads(SEALED_EMBER_GRAPH)
    assert issue_identity(live) == issue_identity(sealed)
    assert repair_identity(live) == repair_identity(sealed)
    assert_module_derived_epochs(live, hold / "history" / "synth-epochs.ndjson")
    pairs = {(item["code"], item["part"]) for item in live["findings"]}
    assert ("CLKGT_SPLIT", "ember-twin") in pairs
    assert ("GHOST_MAP", "ember-twin") not in pairs
    assert ("AREA_REGRESS", "ember-edge") in pairs
    assert ("AREA_REGRESS", "ember-up") not in pairs
    assert ("GHOST_MAP", "ember-clear") not in pairs
    assert ("ANCESTOR_GAP", "ember-larch") not in pairs
    raw = read_guarded(out_attest, WORK).decode()
    assert sha256_bytes(raw.encode()) == sha256_bytes(SEALED_EMBER_ATTEST)
    assert "NODE|ember-up|PAD_X1Y1|icg_x5y5|live" in raw.splitlines()


def test_repeated_runs_match():
    """Two independent rebuilds of the shipped desk produce identical graph and attest bytes."""
    first_graph, first_attest = WORK / "det-graph-1.json", WORK / "det-attest-1.isg"
    second_graph, second_attest = WORK / "det-graph-2.json", WORK / "det-attest-2.isg"
    logs = []
    for graph, attest in ((first_graph, first_attest), (second_graph, second_attest)):
        result = run_netloom(
            APP / "history" / "synth-epochs.ndjson",
            APP / "netlist" / "modules",
            APP / "canon" / "synth-ordinance.txt",
            MATRIX,
            graph,
            attest,
        )
        logs.append("%s\n%s" % (result.stdout, result.stderr))
    assert all("panic:" not in log.lower() for log in logs)
    assert sha256_bytes(read_guarded(first_graph, WORK)) == sha256_bytes(read_guarded(second_graph, WORK))
    assert sha256_bytes(read_guarded(first_attest, WORK)) == sha256_bytes(read_guarded(second_attest, WORK))
    live = json.loads(read_guarded(first_graph, WORK))
    sealed = json.loads(SEALED_GRAPH)
    assert issue_identity(live) == issue_identity(sealed)
    assert repair_identity(live) == repair_identity(sealed)
    assert sha256_bytes(read_guarded(first_attest, WORK)) == sha256_bytes(SEALED_ATTEST)


def test_sandbox_cannot_read_verifier_assets():
    """The unprivileged rebuild uid cannot read verifier tests or the seal directory."""
    probes = [
        "test -r /tests/test_outputs.py",
        "test -r /verifier-seal",
    ]
    for probe in probes:
        result = subprocess.run(
            DROP + ["bash", "-c", probe],
            cwd="/work",
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode != 0, "sandbox could still access via %r stdout=%s stderr=%s" % (
            probe,
            result.stdout,
            result.stderr,
        )


def test_quiet_holdout_empty_findings():
    """A quiet flatten partition with ignored extras writes empty findings and sealed attest."""
    hold = WORK / "batch-k3"
    _materialize(HOLDOUT_QUIET, hold)
    out_graph = WORK / "batch-k3-graph.json"
    out_attest = WORK / "batch-k3-attest.npg"
    run_netloom(
        hold / "history" / "synth-epochs.ndjson",
        hold / "netlist" / "modules",
        APP / "canon" / "synth-ordinance.txt",
        MATRIX,
        out_graph,
        out_attest,
    )
    live = json.loads(read_guarded(out_graph, WORK))
    sealed = json.loads(SEALED_QUIET_GRAPH)
    assert set(live.keys()) == {"desk", "findings", "patches"}
    assert live["desk"] == "KilnbridgeNetloom"
    assert live["findings"] == []
    assert live["patches"] == []
    assert issue_identity(live) == issue_identity(sealed)
    assert sha256_bytes(read_guarded(out_attest, WORK)) == sha256_bytes(SEALED_QUIET_ATTEST)


def test_matrix_lib_schedule_governs():
    """Governing liberty revision comes from LIB rows of --matrix, not a hardcoded calendar."""
    matrix = BASE_MATRIX.replace("LIB,4,2024-03-01", "LIB,4,2024-04-01")
    journal = [
        '{"epoch": 1, "part": "p", "kind": "map", "site": "CELL_X0Y0", "gen": 1, "clk": "ICG_X0Y0", "role": "core", "on": "2024-01-01", "freeze_on": null, "ckpt": "", "lib": 2, "cells": 100}\n',
        '{"epoch": 2, "part": "p", "kind": "techmap", "site": "CELL_X0Y0", "gen": 1, "clk": "ICG_X0Y0", "role": "core", "on": "2024-03-10", "freeze_on": null, "ckpt": "", "lib": 2, "cells": 100}\n',
    ]
    drops = [["ok.nlg", "MODULE ok\nPART p\nLANE incremental\nICG icg_x0y0\nPARENT -\nWHEN 2024-03-20\n"]]
    live = _tiny_audit("lib-sched", journal, drops, matrix)
    ident = issue_identity(live)
    assert ident == []
    assert ("STALE_LIB", "p", 2) not in ident


def test_matrix_lib_revision_tokens_govern():
    """Revision numbers on LIB rows are consumed from --matrix, not assumed to stay 2 then 4."""
    matrix = BASE_MATRIX.replace("LIB,4,2024-03-01", "LIB,5,2024-03-01")
    journal = [
        '{"epoch": 1, "part": "p", "kind": "map", "site": "CELL_X0Y0", "gen": 1, "clk": "ICG_X0Y0", "role": "core", "on": "2024-01-01", "freeze_on": null, "ckpt": "", "lib": 2, "cells": 100}\n',
        '{"epoch": 2, "part": "p", "kind": "techmap", "site": "CELL_X0Y0", "gen": 1, "clk": "ICG_X0Y0", "role": "core", "on": "2024-03-10", "freeze_on": null, "ckpt": "", "lib": 4, "cells": 100}\n',
    ]
    drops = [["ok5.nlg", "MODULE ok5\nPART p\nLANE incremental\nICG icg_x0y0\nPARENT -\nWHEN 2024-03-20\n"]]
    live = _tiny_audit("lib-rev", journal, drops, matrix)
    assert ("STALE_LIB", "p", 2) in issue_identity(live)


def test_matrix_lib_highest_eligible_wins():
    """The highest eligible LIB revision governs even when a later eligible row is smaller."""
    matrix = BASE_MATRIX.replace("LIB,2,2024-01-01\nLIB,4,2024-03-01", "LIB,4,2024-01-01\nLIB,2,2024-02-01")
    journal = [
        '{"epoch": 1, "part": "p", "kind": "map", "site": "CELL_X0Y0", "gen": 1, "clk": "ICG_X0Y0", "role": "core", "on": "2024-01-01", "freeze_on": null, "ckpt": "", "lib": 2, "cells": 100}\n',
        '{"epoch": 2, "part": "p", "kind": "techmap", "site": "CELL_X0Y0", "gen": 1, "clk": "ICG_X0Y0", "role": "core", "on": "2024-03-10", "freeze_on": null, "ckpt": "", "lib": 2, "cells": 100}\n',
    ]
    drops = [["hi.nlg", "MODULE hi\nPART p\nLANE incremental\nICG icg_x0y0\nPARENT -\nWHEN 2024-03-20\n"]]
    live = _tiny_audit("lib-hi", journal, drops, matrix)
    assert ("STALE_LIB", "p", 2) in issue_identity(live)


def test_matrix_novel_role_eligibility():
    """ROLE rows for names outside the shipped core/io/debug/scan set still grant incremental eligibility."""
    matrix = BASE_MATRIX + "ROLE,accelerator,incremental\n"
    journal = [
        '{"epoch": 1, "part": "p", "kind": "map", "site": "CELL_X0Y0", "gen": 1, "clk": "ICG_X0Y0", "role": "accelerator", "on": "2024-01-01", "freeze_on": null, "ckpt": "", "lib": 2, "cells": 100}\n',
    ]
    drops = [["acc.nlg", "MODULE acc\nPART p\nLANE incremental\nICG icg_x0y0\nPARENT -\nWHEN 2024-02-01\n"]]
    live = _tiny_audit("role-acc", journal, drops, matrix)
    assert issue_identity(live) == []
    assert ("LIB_MISBIND", "p", 1) not in issue_identity(live)


def test_area_regress_requires_unchanged_liberty():
    """Cell growth is AREA_REGRESS only when liberty is unchanged, not when liberty falls."""
    journal = [
        '{"epoch": 1, "part": "p", "kind": "map", "site": "CELL_X0Y0", "gen": 1, "clk": "ICG_X0Y0", "role": "core", "on": "2024-01-01", "freeze_on": null, "ckpt": "", "lib": 2, "cells": 100}\n',
        '{"epoch": 2, "part": "p", "kind": "opt", "site": "CELL_X0Y0", "gen": 1, "clk": "ICG_X0Y0", "role": "core", "on": "2024-03-10", "freeze_on": null, "ckpt": "", "lib": 4, "cells": 100}\n',
        '{"epoch": 3, "part": "p", "kind": "opt", "site": "CELL_X0Y0", "gen": 1, "clk": "ICG_X0Y0", "role": "core", "on": "2024-03-11", "freeze_on": null, "ckpt": "", "lib": 2, "cells": 101}\n',
    ]
    drops = [["decr.nlg", "MODULE decr\nPART p\nLANE incremental\nICG icg_x0y0\nPARENT -\nWHEN 2024-03-20\n"]]
    live = _tiny_audit("area-decr", journal, drops, BASE_MATRIX)
    ident = issue_identity(live)
    assert ident == []
    assert ("AREA_REGRESS", "p", 3) not in ident


def test_area_regress_compares_previous_opt_only():
    """AREA_REGRESS compares consecutive opt cell counts, not the last event of any kind."""
    journal = [
        '{"epoch": 1, "part": "p", "kind": "map", "site": "CELL_X0Y0", "gen": 1, "clk": "ICG_X0Y0", "role": "core", "on": "2024-01-01", "freeze_on": null, "ckpt": "", "lib": 2, "cells": 80}\n',
        '{"epoch": 2, "part": "p", "kind": "opt", "site": "CELL_X0Y0", "gen": 1, "clk": "ICG_X0Y0", "role": "core", "on": "2024-01-02", "freeze_on": null, "ckpt": "", "lib": 2, "cells": 100}\n',
        '{"epoch": 3, "part": "p", "kind": "map", "site": "CELL_X0Y0", "gen": 2, "clk": "ICG_X0Y0", "role": "core", "on": "2024-01-03", "freeze_on": null, "ckpt": "", "lib": 2, "cells": 300}\n',
        '{"epoch": 4, "part": "p", "kind": "opt", "site": "CELL_X0Y0", "gen": 2, "clk": "ICG_X0Y0", "role": "core", "on": "2024-01-04", "freeze_on": null, "ckpt": "", "lib": 2, "cells": 150}\n',
    ]
    drops = [["gapopt.nlg", "MODULE gapopt\nPART p\nLANE incremental\nICG icg_x0y0\nPARENT -\nWHEN 2024-01-20\n"]]
    live = _tiny_audit("area-prev-opt", journal, drops, BASE_MATRIX)
    ident = issue_identity(live)
    assert ("AREA_REGRESS", "p", 4) in ident
    assert ident == [("AREA_REGRESS", "p", 4)]


def test_flatten_module_skips_ancestor_gap():
    """A flatten-lane module with a wrong PARENT on a settled freeze does not emit ANCESTOR_GAP."""
    journal = [
        '{"epoch": 1, "part": "fz", "kind": "map", "site": "CELL_X0Y0", "gen": 1, "clk": "ICG_X0Y0", "role": "core", "on": "2024-01-01", "freeze_on": null, "ckpt": "", "lib": 2, "cells": 100}\n',
        '{"epoch": 2, "part": "fz", "kind": "freeze", "site": "CELL_X0Y0", "gen": 1, "clk": "ICG_X0Y0", "role": "core", "on": "2024-01-01", "freeze_on": "2024-01-01", "ckpt": "ckpt-fz-1", "lib": 2, "cells": 100}\n',
        '{"epoch": 3, "part": "inc", "kind": "map", "site": "CELL_X1Y1", "gen": 1, "clk": "ICG_X1Y1", "role": "core", "on": "2024-01-01", "freeze_on": null, "ckpt": "", "lib": 2, "cells": 100}\n',
        '{"epoch": 4, "part": "inc", "kind": "freeze", "site": "CELL_X1Y1", "gen": 1, "clk": "ICG_X1Y1", "role": "core", "on": "2024-01-01", "freeze_on": "2024-01-01", "ckpt": "ckpt-inc-1", "lib": 2, "cells": 100}\n',
    ]
    drops = [
        ["flat-wrong.nlg", "MODULE flat-wrong\nPART fz\nLANE flatten\nICG icg_x9y9\nPARENT ckpt-bogus\nWHEN 2024-02-01\n"],
        ["inc-wrong.nlg", "MODULE inc-wrong\nPART inc\nLANE incremental\nICG icg_x1y1\nPARENT ckpt-bogus\nWHEN 2024-02-01\n"],
    ]
    live = _tiny_audit("flat-gap", journal, drops, BASE_MATRIX)
    ident = issue_identity(live)
    assert ("ANCESTOR_GAP", "fz", 2) not in ident
    assert ("CLKGT_SPLIT", "fz", 2) not in ident
    assert ("ANCESTOR_GAP", "inc", 4) in ident


def test_equal_freeze_date_picks_alphabetical_checkpoint():
    """When two freezes share a freeze_on day, the alphabetically first checkpoint is the ancestor."""
    journal = [
        '{"epoch": 1, "part": "tie", "kind": "map", "site": "CELL_X0Y0", "gen": 1, "clk": "ICG_X0Y0", "role": "core", "on": "2024-01-01", "freeze_on": null, "ckpt": "", "lib": 2, "cells": 100}\n',
        '{"epoch": 2, "part": "tie", "kind": "freeze", "site": "CELL_X0Y0", "gen": 1, "clk": "ICG_X0Y0", "role": "core", "on": "2024-01-01", "freeze_on": "2024-01-01", "ckpt": "z", "lib": 2, "cells": 100}\n',
        '{"epoch": 3, "part": "tie", "kind": "freeze", "site": "CELL_X0Y0", "gen": 1, "clk": "ICG_X0Y0", "role": "core", "on": "2024-01-01", "freeze_on": "2024-01-01", "ckpt": "a", "lib": 2, "cells": 100}\n',
    ]
    later = [["tie-z.nlg", "MODULE tie-z\nPART tie\nLANE incremental\nICG icg_x0y0\nPARENT z\nWHEN 2024-02-01\n"]]
    first = [["tie-a.nlg", "MODULE tie-a\nPART tie\nLANE incremental\nICG icg_x0y0\nPARENT a\nWHEN 2024-02-01\n"]]
    live_later = _tiny_audit("tie-z", journal, later, BASE_MATRIX)
    live_first = _tiny_audit("tie-a", journal, first, BASE_MATRIX)
    assert ("ANCESTOR_GAP", "tie", 3) in issue_identity(live_later)
    assert ("ANCESTOR_GAP", "tie", 3) not in issue_identity(live_first)
    assert issue_identity(live_first) == []


def test_settle_window_includes_the_offset_day_only():
    """A freeze is an eligible ancestor on freeze_on plus the settle count, and not on the day before."""
    journal = [
        '{"epoch": 1, "part": "p", "kind": "map", "site": "CELL_X0Y0", "gen": 1, "clk": "ICG_X0Y0", "role": "core", "on": "2024-01-01", "freeze_on": null, "ckpt": "", "lib": 2, "cells": 100}\n',
        '{"epoch": 2, "part": "p", "kind": "freeze", "site": "CELL_X0Y0", "gen": 1, "clk": "ICG_X0Y0", "role": "core", "on": "2024-01-01", "freeze_on": "2024-01-01", "ckpt": "ckpt-p", "lib": 2, "cells": 100}\n',
    ]
    early = [["early.nlg", "MODULE early\nPART p\nLANE incremental\nICG icg_x0y0\nPARENT ckpt-p\nWHEN 2024-01-14\n"]]
    ready = [["ready.nlg", "MODULE ready\nPART p\nLANE incremental\nICG icg_x0y0\nPARENT ckpt-p\nWHEN 2024-01-15\n"]]
    live_early = _tiny_audit("settle-early", journal, early, BASE_MATRIX)
    live_ready = _tiny_audit("settle-ready", journal, ready, BASE_MATRIX)
    assert ("ANCESTOR_GAP", "p", 2) in issue_identity(live_early)
    assert issue_identity(live_ready) == []


def test_node_clk_empty_without_map_or_recover():
    """A partition with no map or recover writes an empty NODE clock, not a placeholder."""
    journal = [
        '{"epoch": 1, "part": "p", "kind": "techmap", "site": "CELL_X0Y0", "gen": 1, "clk": "ICG_X0Y0", "role": "core", "on": "2024-03-10", "freeze_on": null, "ckpt": "", "lib": 2, "cells": 100}\n',
    ]
    drops = [["flatclk.nlg", "MODULE flatclk\nPART p\nLANE flatten\nICG icg_x0y0\nPARENT -\nWHEN 2024-03-20\n"]]
    live = _tiny_audit("empty-clk", journal, drops, BASE_MATRIX)
    attest = read_guarded(WORK / (_desk_token("empty-clk") + "-attest.npg"), WORK).decode()
    lines = attest.splitlines()
    assert "NODE|p|CELL_X0Y0||live" in lines
    assert "NODE|p|CELL_X0Y0|-|live" not in lines
    assert ("STALE_LIB", "p", 1) in issue_identity(live)


def test_node_clk_empty_when_only_event_is_freeze():
    """A partition whose only event is freeze still writes an empty NODE clock."""
    journal = [
        '{"epoch": 1, "part": "p", "kind": "freeze", "site": "CELL_X0Y0", "gen": 1, "clk": "ICG_X9Y9", "role": "core", "on": "2024-01-01", "freeze_on": "2024-01-01", "ckpt": "ckpt-p", "lib": 2, "cells": 100}\n',
    ]
    drops = [["flatfr.nlg", "MODULE flatfr\nPART p\nLANE flatten\nICG icg_x9y9\nPARENT -\nWHEN 2024-02-01\n"]]
    _tiny_audit("freeze-only", journal, drops, BASE_MATRIX)
    attest = read_guarded(WORK / (_desk_token("freeze-only") + "-attest.npg"), WORK).decode()
    lines = attest.splitlines()
    assert "NODE|p|CELL_X0Y0||frozen" in lines
    assert "NODE|p|CELL_X0Y0|icg_x9y9|frozen" not in lines
    assert "NODE|p|CELL_X0Y0|-|frozen" not in lines


def test_negative_lib_revision_is_not_stale_at_equality():
    """An effective negative liberty revision is the governing value, not a stale one."""
    matrix = (
        "kind,token,payload\n"
        "SETTLE,days,14\n"
        "ROLE,core,incremental\n"
        "LIB,-5,2024-01-01\n"
        "LIB,-1,2024-01-01\n"
        "REPAIR,STALE_LIB,promote-lib\n"
    )
    equal = [
        '{"epoch": 1, "part": "p", "kind": "techmap", "site": "CELL_X0Y0", "gen": 1, "clk": "ICG_X0Y0", "role": "core", "on": "2024-01-01", "freeze_on": null, "ckpt": "", "lib": -1, "cells": 100}\n',
    ]
    lower = [
        '{"epoch": 1, "part": "p", "kind": "techmap", "site": "CELL_X0Y0", "gen": 1, "clk": "ICG_X0Y0", "role": "core", "on": "2024-01-01", "freeze_on": null, "ckpt": "", "lib": -5, "cells": 100}\n',
    ]
    assert issue_identity(_tiny_audit("lib-neg-eq", equal, [], matrix)) == []
    assert ("STALE_LIB", "p", 1) in issue_identity(_tiny_audit("lib-neg-low", lower, [], matrix))


def test_clock_gate_match_ignores_case():
    """An incremental ICG matches the live clock-gate after case folding."""
    journal = [
        '{"epoch": 1, "part": "p", "kind": "map", "site": "CELL_X0Y0", "gen": 1, "clk": "ICG_X1Y1", "role": "core", "on": "2024-01-01", "freeze_on": null, "ckpt": "", "lib": 2, "cells": 100}\n',
    ]
    same = [["same.nlg", "MODULE same\nPART p\nLANE incremental\nICG icg_x1y1\nPARENT -\nWHEN 2024-02-01\n"]]
    other = [["other.nlg", "MODULE other\nPART p\nLANE incremental\nICG icg_x9y9\nPARENT -\nWHEN 2024-02-01\n"]]
    assert issue_identity(_tiny_audit("clk-fold", journal, same, BASE_MATRIX)) == []
    assert ("CLKGT_SPLIT", "p", 1) in issue_identity(_tiny_audit("clk-miss", journal, other, BASE_MATRIX))


def test_recover_does_not_emit_stale_lib():
    """STALE_LIB is a techmap code; recover may carry an old lib on a late date without firing it."""
    journal = [
        '{"epoch": 1, "part": "p", "kind": "map", "site": "CELL_X0Y0", "gen": 1, "clk": "ICG_X0Y0", "role": "core", "on": "2024-01-01", "freeze_on": null, "ckpt": "", "lib": 2, "cells": 100}\n',
        '{"epoch": 2, "part": "p", "kind": "recover", "site": "CELL_X0Y0", "gen": 1, "clk": "ICG_X0Y0", "role": "core", "on": "2024-03-10", "freeze_on": null, "ckpt": "", "lib": 2, "cells": 100}\n',
        '{"epoch": 3, "part": "q", "kind": "map", "site": "CELL_X1Y1", "gen": 1, "clk": "ICG_X1Y1", "role": "core", "on": "2024-01-01", "freeze_on": null, "ckpt": "", "lib": 2, "cells": 100}\n',
        '{"epoch": 4, "part": "q", "kind": "techmap", "site": "CELL_X1Y1", "gen": 1, "clk": "ICG_X1Y1", "role": "core", "on": "2024-03-10", "freeze_on": null, "ckpt": "", "lib": 2, "cells": 100}\n',
    ]
    drops = [
        ["prec.nlg", "MODULE prec\nPART p\nLANE incremental\nICG icg_x0y0\nPARENT -\nWHEN 2024-03-20\n"],
        ["qtm.nlg", "MODULE qtm\nPART q\nLANE incremental\nICG icg_x1y1\nPARENT -\nWHEN 2024-03-20\n"],
    ]
    live = _tiny_audit("rec-lib", journal, drops, BASE_MATRIX)
    ident = issue_identity(live)
    assert ("STALE_LIB", "p", 2) not in ident
    assert ("STALE_LIB", "q", 4) in ident
    assert ident == [("STALE_LIB", "q", 4)]


def test_matrix_reads_every_lib_row():
    """Every LIB row in --matrix participates in the governing revision, not only the first two."""
    matrix = BASE_MATRIX + "LIB,6,2024-05-01\n"
    journal = [
        '{"epoch": 1, "part": "p", "kind": "map", "site": "CELL_X0Y0", "gen": 1, "clk": "ICG_X0Y0", "role": "core", "on": "2024-01-01", "freeze_on": null, "ckpt": "", "lib": 2, "cells": 100}\n',
        '{"epoch": 2, "part": "p", "kind": "techmap", "site": "CELL_X0Y0", "gen": 1, "clk": "ICG_X0Y0", "role": "core", "on": "2024-05-02", "freeze_on": null, "ckpt": "", "lib": 4, "cells": 100}\n',
    ]
    drops = [["lib3.nlg", "MODULE lib3\nPART p\nLANE incremental\nICG icg_x0y0\nPARENT -\nWHEN 2024-05-10\n"]]
    live = _tiny_audit("lib-third", journal, drops, matrix)
    assert ("STALE_LIB", "p", 2) in issue_identity(live)


def test_matrix_lib_ignores_rows_not_yet_effective():
    """A LIB row does not govern a techmap before its effective date, including when it is the only row."""
    matrix = BASE_MATRIX.replace("LIB,2,2024-01-01\nLIB,4,2024-03-01", "LIB,4,2024-03-01")
    drops = [["prelib.nlg", "MODULE prelib\nPART p\nLANE incremental\nICG icg_x0y0\nPARENT -\nWHEN 2024-02-20\n"]]
    before = [
        '{"epoch": 1, "part": "p", "kind": "map", "site": "CELL_X0Y0", "gen": 1, "clk": "ICG_X0Y0", "role": "core", "on": "2024-01-01", "freeze_on": null, "ckpt": "", "lib": 2, "cells": 100}\n',
        '{"epoch": 2, "part": "p", "kind": "techmap", "site": "CELL_X0Y0", "gen": 1, "clk": "ICG_X0Y0", "role": "core", "on": "2024-02-01", "freeze_on": null, "ckpt": "", "lib": 2, "cells": 100}\n',
    ]
    live_before = _tiny_audit("lib-pre", before, drops, matrix)
    assert issue_identity(live_before) == []
    assert ("STALE_LIB", "p", 2) not in issue_identity(live_before)
    drops_on = [["onlib.nlg", "MODULE onlib\nPART p\nLANE incremental\nICG icg_x0y0\nPARENT -\nWHEN 2024-03-20\n"]]
    on_date = [
        '{"epoch": 1, "part": "p", "kind": "map", "site": "CELL_X0Y0", "gen": 1, "clk": "ICG_X0Y0", "role": "core", "on": "2024-01-01", "freeze_on": null, "ckpt": "", "lib": 2, "cells": 100}\n',
        '{"epoch": 2, "part": "p", "kind": "techmap", "site": "CELL_X0Y0", "gen": 1, "clk": "ICG_X0Y0", "role": "core", "on": "2024-03-01", "freeze_on": null, "ckpt": "", "lib": 2, "cells": 100}\n',
    ]
    live_on = _tiny_audit("lib-ondate", on_date, drops_on, matrix)
    assert ("STALE_LIB", "p", 2) in issue_identity(live_on)


def test_note_caps_long_ordinary_partition():
    """A long partition of ordinary characters still keeps every finding note within 160 characters."""
    part = "-" * 161
    journal = [
        '{"epoch": 1, "part": "%s", "kind": "map", "site": "CELL_X0Y0", "gen": 1, "clk": "ICG_X0Y0", "role": "scan", "on": "2024-01-01", "freeze_on": null, "ckpt": "", "lib": 2, "cells": 100}\n'
        % part
    ]
    drops = [["longp.nlg", "MODULE longp\nPART %s\nLANE incremental\nICG icg_x0y0\nPARENT -\nWHEN 2024-02-01\n" % part]]
    live = _tiny_audit("long-part", journal, drops, BASE_MATRIX)
    assert ("LIB_MISBIND", part, 1) in issue_identity(live)
    notes = [item["note"] for item in live["findings"]]
    assert notes
    assert all(len(item) <= 160 for item in notes)
    assert all(part not in item for item in notes)


def test_note_redacts_nonhex_base64_run():
    """A 32-character base64-alphabet role that is not hexadecimal must be redacted in the note."""
    token = "Z" * 32
    journal = [
        '{"epoch": 1, "part": "p", "kind": "map", "site": "CELL_X0Y0", "gen": 1, "clk": "ICG_X0Y0", "role": "%s", "on": "2024-01-01", "freeze_on": null, "ckpt": "", "lib": 2, "cells": 100}\n'
        % token
    ]
    drops = [["b64z.nlg", "MODULE b64z\nPART p\nLANE incremental\nICG icg_x0y0\nPARENT -\nWHEN 2024-02-01\n"]]
    live = _tiny_audit("b64-role", journal, drops, BASE_MATRIX)
    ident = issue_identity(live)
    assert ("LIB_MISBIND", "p", 1) in ident
    notes = [item["note"] for item in live["findings"]]
    assert notes
    assert all(token not in item for item in notes)
    assert any("redacted" in item for item in notes)
    assert all(not re.search(r"[A-Za-z0-9+/]{32,}={0,2}", item) for item in notes)


def test_note_redacts_forbidden_part_token():
    """A 32-character partition name must not appear inside a finding note."""
    token = "Z" * 32
    journal = [
        '{"epoch": 1, "part": "%s", "kind": "map", "site": "CELL_X0Y0", "gen": 1, "clk": "ICG_X0Y0", "role": "scan", "on": "2024-01-01", "freeze_on": null, "ckpt": "", "lib": 2, "cells": 100}\n'
        % token
    ]
    drops = [
        [
            "b64p.nlg",
            "MODULE b64p\nPART %s\nLANE incremental\nICG icg_x0y0\nPARENT -\nWHEN 2024-02-01\n" % token,
        ]
    ]
    live = _tiny_audit("b64-part", journal, drops, BASE_MATRIX)
    ident = issue_identity(live)
    assert ("LIB_MISBIND", token, 1) in ident
    notes = [item["note"] for item in live["findings"]]
    assert notes
    assert all(token not in item for item in notes)
    assert all(not re.search(r"[A-Za-z0-9+/]{32,}={0,2}", item) for item in notes)


def test_matrix_repair_verbs_govern():
    """Patch verbs follow the REPAIR rows of the matrix passed as --matrix, not the shipped map."""
    matrix_path = WORK / "verb-matrix.csv"
    body = SEALED_MATRIX
    body = body.replace(b"GHOST_MAP,drop-checkpoint", b"GHOST_MAP,relink-ancestor")
    body = body.replace(b"LIB_MISBIND,rebind-lib", b"LIB_MISBIND,drop-checkpoint")
    body = body.replace(b"ANCESTOR_GAP,relink-ancestor", b"ANCESTOR_GAP,rebind-lib")
    body = body.replace(b"AREA_REGRESS,remap-area", b"AREA_REGRESS,rebind-icg")
    body = body.replace(b"STALE_LIB,promote-lib", b"STALE_LIB,remap-area")
    body = body.replace(b"CLKGT_SPLIT,rebind-icg", b"CLKGT_SPLIT,relink-ancestor")
    matrix_path.write_bytes(body)
    out_graph = WORK / "verb-graph.json"
    out_attest = WORK / "verb-attest.npg"
    run_netloom(
        APP / "history" / "synth-epochs.ndjson",
        APP / "netlist" / "modules",
        APP / "canon" / "synth-ordinance.txt",
        matrix_path,
        out_graph,
        out_attest,
    )
    live = json.loads(read_guarded(out_graph, WORK))
    expected = {
        "GHOST_MAP": "relink-ancestor",
        "LIB_MISBIND": "drop-checkpoint",
        "ANCESTOR_GAP": "rebind-lib",
        "AREA_REGRESS": "rebind-icg",
        "STALE_LIB": "remap-area",
        "CLKGT_SPLIT": "relink-ancestor",
    }
    seen = set()
    for issue, repair in zip(live["findings"], live["patches"]):
        assert repair["verb"] == expected[issue["code"]]
        seen.add(issue["code"])
    assert seen == set(expected)


def test_first_hold_row_governs():
    """The first SETTLE row sets the parent window; later SETTLE rows and non-.nlg extras are ignored."""
    src = WORK / "hold-src"
    if src.exists():
        shutil.rmtree(src)
    hist = src / "history"
    regions = src / "netlist" / "modules"
    hist.mkdir(parents=True)
    regions.mkdir(parents=True)
    (hist / "synth-epochs.ndjson").write_text(
        '{"epoch":1,"part":"hold-a","kind":"map","site":"CELL_X0Y0","gen":1,"clk":"ICG_X0Y0","lib":2,"cells":100,"role":"core","on":"2024-01-01","freeze_on":null,"ckpt":""}\n'
        '{"epoch":2,"part":"hold-a","kind":"freeze","site":"CELL_X0Y0","gen":1,"clk":"ICG_X0Y0","lib":2,"cells":100,"role":"core","on":"2024-01-01","freeze_on":"2024-01-01","ckpt":"ckpt-hold-1"}\n'
        '{"epoch":3,"part":"hold-a","kind":"recover","site":"CELL_X0Y0","gen":1,"clk":"ICG_X0Y0","lib":2,"cells":100,"role":"core","on":"2024-01-05","freeze_on":null,"ckpt":""}\n'
    )
    (regions / "hold-1.nlg").write_text("MODULE hold-1\nPART hold-a\nLANE incremental\nICG icg_x0y0\nPARENT ckpt-hold-1\nWHEN 2024-01-10\n")
    poison = "MODULE poison\nPART hold-a\nLANE incremental\nICG icg_x9y9\nPARENT ckpt-fake\nWHEN 2024-01-10\n"
    (regions / "poison.cfg").write_text(poison)
    (regions / "poison.json").write_text(poison)
    (regions / "poison.nlg.bak").write_text(poison)
    (regions / "poison.NLG").write_text(poison)
    (regions / "junk.cfg").write_text("not a module record\n")
    matrix_path = WORK / "hold-matrix.csv"
    matrix_path.write_text(
        "kind,token,payload\n"
        "SETTLE,days,7\n"
        "SETTLE,days,21\n"
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
    out_graph = WORK / "hold-graph.json"
    out_attest = WORK / "hold-attest.npg"
    run_netloom(
        hist / "synth-epochs.ndjson",
        regions,
        APP / "canon" / "synth-ordinance.txt",
        matrix_path,
        out_graph,
        out_attest,
    )
    live = json.loads(read_guarded(out_graph, WORK))
    assert ("ANCESTOR_GAP", "hold-a", 3) not in issue_identity(live)
    assert ("GHOST_MAP", "hold-a", 3) not in issue_identity(live)
    assert ("CLKGT_SPLIT", "hold-a", 3) not in issue_identity(live)


def test_matrix_incremental_roles_govern():
    """Marking scan incremental in --matrix clears the shipped LIB_MISBIND on scan-tap."""
    matrix_path = WORK / "role-matrix.csv"
    matrix_path.write_bytes(SEALED_MATRIX.replace(b"ROLE,scan,flatten", b"ROLE,scan,incremental"))
    out_graph = WORK / "role-graph.json"
    out_attest = WORK / "role-attest.npg"
    run_netloom(
        APP / "history" / "synth-epochs.ndjson",
        APP / "netlist" / "modules",
        APP / "canon" / "synth-ordinance.txt",
        matrix_path,
        out_graph,
        out_attest,
    )
    live = json.loads(read_guarded(out_graph, WORK))
    assert ("LIB_MISBIND", "scan-tap", 12) not in issue_identity(live)


def test_ordinance_contract():
    """Missing, unreadable, mistitled, or code-stripped ordinances exit 2 and leave outputs unwritten."""

    def canon_path(label: str) -> Path:
        return WORK / (_desk_token("canon-" + label) + ".txt")

    def canon_outs(label: str) -> tuple[Path, Path]:
        token = _desk_token("canon-" + label)
        return WORK / (token + "-graph.json"), WORK / (token + "-attest.npg")

    def fail_and_keep(label: str, body: str | bytes) -> None:
        fake = canon_path(label)
        if isinstance(body, bytes):
            fake.write_bytes(body)
        else:
            fake.write_text(body)
        _sandbox_own(fake)
        graph, attest = canon_outs(label)
        marker_graph = b'{"desk":"stale"}\n'
        marker_attest = b"NPG1\nstale\n"
        graph.write_bytes(marker_graph)
        attest.write_bytes(marker_attest)
        _sandbox_own(graph)
        _sandbox_own(attest)
        before = {graph: _output_identity(graph), attest: _output_identity(attest)}
        result = invoke_netloom(
            APP / "history" / "synth-epochs.ndjson",
            APP / "netlist" / "modules",
            fake,
            MATRIX,
            graph,
            attest,
            unlink_outputs=False,
        )
        assert result.returncode == 2
        assert auditor_stderr(result.stderr) != ""
        assert_stream_safe(result.stdout + "\n" + result.stderr)
        for path, info in before.items():
            _assert_output_identity(path, info)

    cases = [
        ("body", "not the Kilnbridge desk contract\n"),
        ("title", "Kilnbridge Netlist Provenance Ordinance\nDesk copy without unsafe headings\n"),
        ("loose", "prefix Kilnbridge Netlist Provenance Ordinance suffix\n%s\n" % "\n".join(sorted(FIND_FAMILIES))),
    ]
    for label, body in cases:
        fake = canon_path(label)
        fake.write_text(body)
        _sandbox_own(fake)
        graph, attest = canon_outs(label)
        result = invoke_netloom(
            APP / "history" / "synth-epochs.ndjson",
            APP / "netlist" / "modules",
            fake,
            MATRIX,
            graph,
            attest,
        )
        assert_ordinance_failure(result, graph, attest)
    fail_and_keep("stale-body", "not the Kilnbridge desk contract\n")
    fail_and_keep("stale-title", "Kilnbridge Netlist Provenance Ordinance\nDesk copy without unsafe headings\n")
    fail_and_keep(
        "stale-loose",
        "prefix Kilnbridge Netlist Provenance Ordinance suffix\n%s\n" % "\n".join(sorted(FIND_FAMILIES)),
    )
    absent_graph, absent_attest = canon_outs("absent")
    marker_graph = b'{"desk":"stale"}\n'
    marker_attest = b"NPG1\nstale\n"
    absent_graph.write_bytes(marker_graph)
    absent_attest.write_bytes(marker_attest)
    _sandbox_own(absent_graph)
    _sandbox_own(absent_attest)
    absent_before = {absent_graph: _output_identity(absent_graph), absent_attest: _output_identity(absent_attest)}
    result = invoke_netloom(
        APP / "history" / "synth-epochs.ndjson",
        APP / "netlist" / "modules",
        Path("/tmp/" + _desk_token("canon-absent") + ".txt"),
        MATRIX,
        absent_graph,
        absent_attest,
        unlink_outputs=False,
    )
    assert result.returncode == 2
    assert auditor_stderr(result.stderr) != ""
    assert_stream_safe(result.stdout + "\n" + result.stderr)
    for path, info in absent_before.items():
        _assert_output_identity(path, info)
    link_dir = WORK / _desk_token("canon-links")
    link_dir.mkdir(parents=True, exist_ok=True)
    link_graph_target = link_dir / "g-body"
    link_attest_target = link_dir / "a-body"
    link_graph_target.write_bytes(marker_graph)
    link_attest_target.write_bytes(marker_attest)
    link_graph = link_dir / "g-link"
    link_attest = link_dir / "a-link"
    for path in (link_graph, link_attest):
        if path.is_symlink() or path.exists():
            path.unlink()
    os.symlink(link_graph_target.name, link_graph)
    os.symlink(link_attest_target.name, link_attest)
    link_before = {
        link_graph: _output_identity(link_graph),
        link_attest: _output_identity(link_attest),
        link_graph_target: _output_identity(link_graph_target),
        link_attest_target: _output_identity(link_attest_target),
    }
    fake = canon_path("links")
    fake.write_text("not the Kilnbridge desk contract\n")
    _sandbox_own(fake)
    result = invoke_netloom(
        APP / "history" / "synth-epochs.ndjson",
        APP / "netlist" / "modules",
        fake,
        MATRIX,
        link_graph,
        link_attest,
        unlink_outputs=False,
    )
    assert result.returncode == 2
    assert auditor_stderr(result.stderr) != ""
    for path, info in link_before.items():
        _assert_output_identity(path, info)
    for missing_code in sorted(FIND_FAMILIES):
        lines = [ln for ln in SEALED_ORDINANCE.decode().splitlines() if missing_code not in ln]
        fail_and_keep("omit-" + missing_code, "\n".join(lines) + "\n")
    fake = canon_path("unreadable")
    fake.write_bytes(SEALED_ORDINANCE)
    unread_graph, unread_attest = canon_outs("unreadable")
    unread_graph.write_bytes(b'{"desk":"stale"}\n')
    unread_attest.write_bytes(b"NPG1\nstale\n")
    _sandbox_own(unread_graph)
    _sandbox_own(unread_attest)
    unread_before = {
        unread_graph: _output_identity(unread_graph),
        unread_attest: _output_identity(unread_attest),
    }
    os.chmod(fake, 0o000)
    try:
        result = invoke_netloom(
            APP / "history" / "synth-epochs.ndjson",
            APP / "netlist" / "modules",
            fake,
            MATRIX,
            unread_graph,
            unread_attest,
            unlink_outputs=False,
        )
        assert result.returncode == 2
        assert auditor_stderr(result.stderr) != ""
        for path, info in unread_before.items():
            _assert_output_identity(path, info)
    finally:
        os.chmod(fake, 0o600)
    alt = canon_path("reprint")
    alt.write_bytes(b"Desk reprint kept beside the shipped copy.\n" + SEALED_ORDINANCE)
    _sandbox_own(alt)
    alt_graph, alt_attest = canon_outs("reprint")
    run_netloom(
        APP / "history" / "synth-epochs.ndjson",
        APP / "netlist" / "modules",
        alt,
        MATRIX,
        alt_graph,
        alt_attest,
    )
    live = json.loads(read_guarded(alt_graph, WORK))
    assert issue_identity(live) == issue_identity(json.loads(SEALED_GRAPH))
    assert repair_identity(live) == repair_identity(json.loads(SEALED_GRAPH))
    embed = ["Kilnbridge Netlist Provenance Ordinance"]
    for code in sorted(FIND_FAMILIES):
        embed.append("desk rule names %s among other text" % code)
    assert all(line not in FIND_FAMILIES for line in embed)
    desk = canon_path("substring")
    desk.write_text("\n".join(embed) + "\n")
    _sandbox_own(desk)
    desk_graph, desk_attest = canon_outs("substring")
    run_netloom(
        APP / "history" / "synth-epochs.ndjson",
        APP / "netlist" / "modules",
        desk,
        MATRIX,
        desk_graph,
        desk_attest,
    )
    live = json.loads(read_guarded(desk_graph, WORK))
    assert set(live.keys()) == {"desk", "findings", "patches"}
    assert live["desk"] == "KilnbridgeNetloom"
    assert isinstance(live["findings"], list)
    assert isinstance(live["patches"], list)
    assert_graph_indent(read_guarded(desk_graph, WORK))
    assert_attest_matches(
        read_guarded(desk_attest, WORK).decode(),
        live,
        (APP / "history" / "synth-epochs.ndjson").read_text().splitlines(keepends=True),
    )


def test_output_alias_does_not_clobber_inputs():
    """Outputs may not alias the journal, matrix, ordinance, or shipped /app/netlist/modules files."""
    src = WORK / _desk_token("src-neutral")
    src_name = src.name
    if src.exists():
        shutil.rmtree(src)
    hist = src / "history"
    regions = src / "netlist" / "modules"
    hist.mkdir(parents=True)
    regions.mkdir(parents=True)
    journal = hist / "synth-epochs.ndjson"
    journal.write_text(
        '{"epoch":1,"part":"p","kind":"map","site":"CELL_X0Y0","gen":1,"clk":"ICG_X0Y0","lib":2,"cells":100,"role":"core","on":"2024-01-01","freeze_on":null,"ckpt":""}\n'
    )
    (regions / "ok.nlg").write_text("MODULE ok\nPART p\nLANE incremental\nICG icg_x0y0\nPARENT -\nWHEN 2024-02-01\n")
    matrix_path = src / "rows.csv"
    matrix_path.write_text(BASE_MATRIX)
    ordinance = src / "synth-ordinance.txt"
    ordinance.write_bytes(SEALED_ORDINANCE)
    marker = b"NPG1\nstale\n"
    module_root = APP / "netlist" / "modules"
    shipped_files = sorted(path for path in module_root.iterdir() if path.is_file())
    assert shipped_files, "shipped module directory is empty"
    assert any(path.name == "birch-550.nlg" for path in shipped_files)
    assert any(path.suffix != ".nlg" for path in shipped_files)
    shipped_before = {path: path.read_bytes() for path in shipped_files}
    protected = [journal, matrix_path, ordinance] + shipped_files
    spare_graph = WORK / _desk_token("out-a")
    spare_attest = WORK / _desk_token("out-b")
    for path in protected:
        _sandbox_own(path)

    def refuse(graph: Path, attest: Path) -> None:
        watched = {path: path.read_bytes() for path in protected}
        extras = {}
        for path in (graph, attest):
            if path in {spare_graph, spare_attest}:
                if path.exists() or path.is_symlink():
                    path.unlink()
                path.write_bytes(marker)
                _sandbox_own(path)
                extras[path] = path.read_bytes()
        for path in protected:
            _sandbox_own(path)
        alias_paths = {}
        for path in (graph, attest):
            info = {"symlink": path.is_symlink()}
            if path.is_symlink():
                info["link"] = os.readlink(path)
                st = path.lstat()
                info["ino"] = (st.st_dev, st.st_ino)
            elif path.is_file():
                info["bytes"] = path.read_bytes()
                st = path.stat()
                info["ino"] = (st.st_dev, st.st_ino)
            alias_paths[path] = info
        result = invoke_netloom(
            journal,
            regions,
            ordinance,
            matrix_path,
            graph,
            attest,
            unlink_outputs=False,
        )
        assert result.returncode == 2
        assert auditor_stderr(result.stderr) != ""
        assert_stream_safe(result.stdout + "\n" + result.stderr)
        for path, before in watched.items():
            assert path.read_bytes() == before, path
        for path, before in extras.items():
            assert path.read_bytes() == before, path
        for path, info in alias_paths.items():
            if info.get("symlink"):
                assert path.is_symlink(), "alias pathname %s was detached or replaced" % path
                assert os.readlink(path) == info["link"], path
                st = path.lstat()
                assert (st.st_dev, st.st_ino) == info["ino"], path
            elif "bytes" in info:
                assert path.is_file() and not path.is_symlink(), path
                assert path.read_bytes() == info["bytes"], path
                st = path.stat()
                assert (st.st_dev, st.st_ino) == info["ino"], path
            else:
                assert not path.exists() and not path.is_symlink(), path

    for path in protected:
        refuse(path, spare_attest)
        refuse(spare_graph, path)

    hl_journal = WORK / _desk_token("n1")
    hl_ordinance = WORK / _desk_token("n2")
    hl_matrix = WORK / _desk_token("n3")
    sl_journal = WORK / _desk_token("n4")
    sl_ordinance = WORK / _desk_token("n5")
    sl_matrix = WORK / _desk_token("n6")
    sl_attest_journal = WORK / _desk_token("n7")
    shipped_hls = []
    shipped_sls = []
    bootstrap = [
        hl_journal,
        hl_ordinance,
        hl_matrix,
        sl_journal,
        sl_ordinance,
        sl_matrix,
        sl_attest_journal,
    ]
    for path in shipped_files:
        hl = WORK / _desk_token("h-" + path.name)
        sl = WORK / _desk_token("s-" + path.name)
        shipped_hls.append(hl)
        shipped_sls.append(sl)
        bootstrap.extend([hl, sl])
    for path in bootstrap:
        if path.exists() or path.is_symlink():
            path.unlink()
    os.link(journal, hl_journal)
    os.link(ordinance, hl_ordinance)
    os.link(matrix_path, hl_matrix)
    os.symlink(journal.resolve(), sl_journal)
    os.symlink(ordinance.resolve(), sl_ordinance)
    os.symlink(matrix_path.resolve(), sl_matrix)
    os.symlink(journal.resolve(), sl_attest_journal)
    keep_hls = []
    for shipped, hl, sl in zip(shipped_files, shipped_hls, shipped_sls):
        os.symlink(shipped.resolve(), sl)
        try:
            os.link(shipped, hl)
            keep_hls.append(hl)
        except OSError:
            pass
    for path in (hl_journal, hl_ordinance, hl_matrix) + tuple(keep_hls):
        _sandbox_own(path)
    refuse(hl_journal, spare_attest)
    refuse(spare_graph, hl_journal)
    refuse(hl_ordinance, spare_attest)
    refuse(spare_graph, hl_ordinance)
    refuse(hl_matrix, spare_attest)
    refuse(spare_graph, hl_matrix)
    for hl in keep_hls:
        refuse(hl, spare_attest)
        refuse(spare_graph, hl)
    refuse(sl_journal, spare_attest)
    refuse(spare_graph, sl_attest_journal)
    refuse(sl_ordinance, spare_attest)
    refuse(spare_graph, sl_ordinance)
    refuse(sl_matrix, spare_attest)
    refuse(spare_graph, sl_matrix)
    for sl in shipped_sls:
        refuse(sl, spare_attest)
        refuse(spare_graph, sl)
    rel_graph = WORK / _desk_token("n8")
    rel_attest = WORK / _desk_token("n9")
    nested_dir = WORK / _desk_token("n10")
    nested_dir.mkdir(parents=True, exist_ok=True)
    nested_graph = nested_dir / _desk_token("n11")
    nested_attest = nested_dir / _desk_token("n12")
    module_link_dir = WORK / _desk_token("n13")
    module_link_dir.mkdir(parents=True, exist_ok=True)
    module_link = module_link_dir / _desk_token("n14")
    for path in (rel_graph, rel_attest, nested_graph, nested_attest, module_link):
        if path.exists() or path.is_symlink():
            path.unlink()
    os.symlink(src_name + "/synth-ordinance.txt", rel_graph)
    os.symlink(src_name + "/synth-ordinance.txt", rel_attest)
    os.symlink("../" + src_name + "/rows.csv", nested_graph)
    os.symlink("../" + src_name + "/history/synth-epochs.ndjson", nested_attest)
    os.symlink("../../../app/netlist/modules/birch-550.nlg", module_link)
    assert not os.path.isabs(os.readlink(rel_graph))
    assert not os.path.isabs(os.readlink(nested_graph))
    assert not os.path.isabs(os.readlink(module_link))
    assert os.path.samefile(module_link, APP / "netlist" / "modules" / "birch-550.nlg")
    absent_attest = WORK / _desk_token("n15")
    absent_graph = WORK / _desk_token("n16")
    for path in (absent_attest, absent_graph):
        if path.exists() or path.is_symlink():
            path.unlink()
    refuse(rel_graph, spare_attest)
    refuse(spare_graph, rel_attest)
    refuse(nested_graph, spare_attest)
    refuse(spare_graph, nested_attest)
    refuse(module_link, spare_attest)
    refuse(spare_graph, module_link)
    refuse(hl_journal, absent_attest)
    refuse(absent_graph, hl_journal)
    for path, before in shipped_before.items():
        assert path.read_bytes() == before


def _run_publish_failure(graph: Path, attest: Path) -> None:
    before = {graph: _output_identity(graph), attest: _output_identity(attest)}
    result = invoke_netloom(
        APP / "history" / "synth-epochs.ndjson",
        APP / "netlist" / "modules",
        APP / "canon" / "synth-ordinance.txt",
        MATRIX,
        graph,
        attest,
        unlink_outputs=False,
    )
    assert result.returncode != 0
    assert auditor_stderr(result.stderr) != ""
    assert_stream_safe(result.stdout + "\n" + result.stderr)
    for path, info in before.items():
        _assert_output_identity(path, info)


def test_attest_write_failure_keeps_prior_graph():
    """A failed attest write after valid inputs must not replace the graph."""
    hold = WORK / _desk_token("pub-a")
    hold.mkdir(parents=True, exist_ok=True)
    graph = hold / _desk_token("pub-g")
    marker = b'{"desk":"prior-generation"}\n'
    graph.write_bytes(marker)
    locked = WORK / _desk_token("pub-b")
    locked.mkdir(parents=True, exist_ok=True)
    attest = locked / _desk_token("pub-t")
    if attest.exists() or attest.is_symlink():
        attest.unlink()
    os.chmod(locked, 0o555)
    try:
        _run_publish_failure(graph, attest)
    finally:
        os.chmod(locked, 0o755)


def test_graph_write_failure_keeps_prior_attest():
    """A failed graph write after valid inputs must not replace the attest."""
    locked = WORK / _desk_token("pub-c")
    locked.mkdir(parents=True, exist_ok=True)
    graph = locked / _desk_token("pub-h")
    if graph.exists() or graph.is_symlink():
        graph.unlink()
    hold = WORK / _desk_token("pub-d")
    hold.mkdir(parents=True, exist_ok=True)
    attest = hold / _desk_token("pub-u")
    attest.write_bytes(b"NPG1\nprior\n")
    os.chmod(locked, 0o555)
    try:
        _run_publish_failure(graph, attest)
    finally:
        os.chmod(locked, 0o755)


def test_late_attest_write_does_not_replace_graph():
    """An attest path that accepts open but rejects the payload must not replace the graph."""
    full = Path("/dev/full")
    assert full.is_char_device()
    hold = WORK / _desk_token("pub-e")
    hold.mkdir(parents=True, exist_ok=True)
    graph = hold / _desk_token("pub-i")
    graph.write_bytes(b'{"desk":"prior-generation"}\n')
    _run_publish_failure(graph, full)


def test_second_replace_restores_existing_graph():
    """After both temps exist, a failed attest replacement restores the prior graph bytes."""
    parent = WORK / _desk_token("pub-f")
    parent.mkdir(parents=True, exist_ok=True)
    graph = parent / _desk_token("pub-j")
    prior = b"prior-graph"
    graph.write_bytes(prior)
    attest = parent / _desk_token("pub-dir")
    if attest.exists() or attest.is_symlink():
        if attest.is_dir() and not attest.is_symlink():
            shutil.rmtree(attest)
        else:
            attest.unlink()
    attest.mkdir()
    (attest / "keep").write_text("x\n")
    before = _output_identity(graph)
    result = invoke_netloom(
        APP / "history" / "synth-epochs.ndjson",
        APP / "netlist" / "modules",
        APP / "canon" / "synth-ordinance.txt",
        MATRIX,
        graph,
        attest,
        unlink_outputs=False,
    )
    assert result.returncode != 0
    assert auditor_stderr(result.stderr) != ""
    _assert_output_identity(graph, before)
    assert graph.read_bytes() == prior
    assert attest.is_dir() and not attest.is_symlink()
    assert (attest / "keep").read_text() == "x\n"


def test_second_replace_removes_new_graph():
    """A failed attest replacement removes a graph that did not exist beforehand."""
    parent = WORK / _desk_token("pub-k")
    parent.mkdir(parents=True, exist_ok=True)
    graph = parent / _desk_token("pub-m")
    if graph.exists() or graph.is_symlink():
        graph.unlink()
    attest = parent / _desk_token("pub-n")
    if attest.exists() or attest.is_symlink():
        if attest.is_dir() and not attest.is_symlink():
            shutil.rmtree(attest)
        else:
            attest.unlink()
    attest.mkdir()
    (attest / "keep").write_text("y\n")
    result = invoke_netloom(
        APP / "history" / "synth-epochs.ndjson",
        APP / "netlist" / "modules",
        APP / "canon" / "synth-ordinance.txt",
        MATRIX,
        graph,
        attest,
        unlink_outputs=False,
    )
    assert result.returncode != 0
    assert not graph.exists() and not graph.is_symlink()
    assert attest.is_dir()


def test_direct_write_cannot_bypass_locked_graph_directory():
    """A writable graph file in a non-writable directory stays untouched when its temp cannot be created."""
    locked = WORK / _desk_token("pub-p")
    locked.mkdir(parents=True, exist_ok=True)
    graph = locked / _desk_token("pub-q")
    graph.write_bytes(b"prior-graph")
    hold = WORK / _desk_token("pub-r")
    hold.mkdir(parents=True, exist_ok=True)
    attest = hold / _desk_token("pub-s")
    attest.write_bytes(b"NPG1\nprior\n")
    os.chmod(locked, 0o555)
    try:
        _run_publish_failure(graph, attest)
    finally:
        os.chmod(locked, 0o755)


def test_graph_temp_failure_leaves_absent_attest():
    """A graph temporary failure must not leave an attest that did not exist."""
    locked = WORK / _desk_token("pub-t2")
    locked.mkdir(parents=True, exist_ok=True)
    graph = locked / _desk_token("pub-u2")
    graph.write_bytes(b"prior-graph")
    hold = WORK / _desk_token("pub-v")
    hold.mkdir(parents=True, exist_ok=True)
    attest = hold / _desk_token("pub-w")
    if attest.exists() or attest.is_symlink():
        attest.unlink()
    os.chmod(locked, 0o555)
    try:
        _run_publish_failure(graph, attest)
    finally:
        os.chmod(locked, 0o755)


def test_attest_temp_failure_keeps_existing_attest_file():
    """A preexisting attest in a non-writable directory keeps its bytes and inode."""
    hold = WORK / _desk_token("pub-ab")
    hold.mkdir(parents=True, exist_ok=True)
    graph = hold / _desk_token("pub-ac")
    graph.write_bytes(b'{"desk":"prior-generation"}\n')
    locked = WORK / _desk_token("pub-ad")
    locked.mkdir(parents=True, exist_ok=True)
    attest = locked / _desk_token("pub-ae")
    attest.write_bytes(b"NPG1\nprior\n")
    os.chmod(locked, 0o555)
    try:
        _run_publish_failure(graph, attest)
    finally:
        os.chmod(locked, 0o755)


def test_attest_temp_failure_leaves_absent_graph():
    """An attest temporary failure must not leave a graph that did not exist."""
    hold = WORK / _desk_token("pub-x")
    hold.mkdir(parents=True, exist_ok=True)
    graph = hold / _desk_token("pub-y")
    if graph.exists() or graph.is_symlink():
        graph.unlink()
    locked = WORK / _desk_token("pub-z")
    locked.mkdir(parents=True, exist_ok=True)
    attest = locked / _desk_token("pub-aa")
    if attest.exists() or attest.is_symlink():
        attest.unlink()
    os.chmod(locked, 0o555)
    try:
        _run_publish_failure(graph, attest)
    finally:
        os.chmod(locked, 0o755)


def test_graph_and_attest_must_not_alias():
    """The two output paths may not be the same file."""
    path = WORK / _desk_token("pub-same")
    if path.exists() or path.is_symlink():
        path.unlink()
    result = invoke_netloom(
        APP / "history" / "synth-epochs.ndjson",
        APP / "netlist" / "modules",
        APP / "canon" / "synth-ordinance.txt",
        MATRIX,
        path,
        path,
        unlink_outputs=False,
    )
    assert result.returncode == 2
    assert auditor_stderr(result.stderr) != ""
    assert not path.exists() and not path.is_symlink()
    path.write_bytes(b"prior\n")
    before = _output_identity(path)
    result = invoke_netloom(
        APP / "history" / "synth-epochs.ndjson",
        APP / "netlist" / "modules",
        APP / "canon" / "synth-ordinance.txt",
        MATRIX,
        path,
        path,
        unlink_outputs=False,
    )
    assert result.returncode == 2
    _assert_output_identity(path, before)


def _refuse_output_alias(graph: Path, attest: Path, watched: dict) -> None:
    result = invoke_netloom(
        APP / "history" / "synth-epochs.ndjson",
        APP / "netlist" / "modules",
        APP / "canon" / "synth-ordinance.txt",
        MATRIX,
        graph,
        attest,
        unlink_outputs=False,
    )
    assert result.returncode == 2
    assert auditor_stderr(result.stderr) != ""
    assert_stream_safe(result.stdout + "\n" + result.stderr)
    for item, info in watched.items():
        _assert_output_identity(item, info)


def test_graph_attest_hard_link_is_refused():
    """Distinct pathnames that share an inode are one output alias."""
    parent = WORK / _desk_token("pub-hl")
    parent.mkdir(parents=True, exist_ok=True)
    shared = parent / _desk_token("pub-hl-body")
    shared.write_bytes(b"prior\n")
    graph = parent / _desk_token("pub-hl-g")
    attest = parent / _desk_token("pub-hl-a")
    os.link(shared, graph)
    os.link(shared, attest)
    watched = {path: _output_identity(path) for path in (shared, graph, attest)}
    _refuse_output_alias(graph, attest, watched)


def test_graph_attest_symlink_is_refused():
    """A symbolic link from one output path to the other is refused, including a dangling link."""
    parent = WORK / _desk_token("pub-sl")
    parent.mkdir(parents=True, exist_ok=True)
    attest = parent / _desk_token("pub-sl-a")
    attest.write_bytes(b"prior\n")
    graph = parent / _desk_token("pub-sl-g")
    os.symlink(attest.name, graph)
    watched = {path: _output_identity(path) for path in (graph, attest)}
    _refuse_output_alias(graph, attest, watched)

    dangling = WORK / _desk_token("pub-dang")
    dangling.mkdir(parents=True, exist_ok=True)
    missing = dangling / "attest"
    link = dangling / "graph"
    if link.exists() or link.is_symlink():
        link.unlink()
    os.symlink("attest", link)
    watched = {link: _output_identity(link), missing: _output_identity(missing)}
    _refuse_output_alias(link, missing, watched)

    pair = WORK / _desk_token("pub-pair")
    pair.mkdir(parents=True, exist_ok=True)
    left = pair / _desk_token("pub-pair-g")
    right = pair / _desk_token("pub-pair-a")
    os.symlink("missing", left)
    os.symlink("missing", right)
    watched = {left: _output_identity(left), right: _output_identity(right)}
    _refuse_output_alias(left, right, watched)


def test_ancestor_symlink_aliases_absent_outputs():
    """Absent leaves under a symlinked directory and its real directory are one path."""
    root = WORK / _desk_token("pub-anc")
    real = root / "real"
    real.mkdir(parents=True)
    link = root / "link"
    os.symlink("real", link)
    graph = link / "out"
    attest = real / "out"
    result = invoke_netloom(
        APP / "history" / "synth-epochs.ndjson",
        APP / "netlist" / "modules",
        APP / "canon" / "synth-ordinance.txt",
        MATRIX,
        graph,
        attest,
        unlink_outputs=False,
    )
    assert result.returncode == 2
    assert auditor_stderr(result.stderr) != ""
    assert not graph.exists() and not graph.is_symlink()
    assert not attest.exists() and not attest.is_symlink()


def test_graph_symlink_survives_attest_replacement_failure():
    """A failed attest replacement leaves an existing graph symbolic link untouched."""
    parent = WORK / _desk_token("pub-gsl")
    parent.mkdir(parents=True)
    target = WORK / _desk_token("pub-gsl-body")
    prior = b"prior-graph"
    target.write_bytes(prior)
    graph = parent / _desk_token("pub-gsl-g")
    os.symlink(os.path.relpath(target, parent), graph)
    attest = parent / _desk_token("pub-gsl-a")
    attest.mkdir()
    (attest / "keep").write_text("z\n")
    before_graph = _output_identity(graph)
    before_target = _output_identity(target)
    result = invoke_netloom(
        APP / "history" / "synth-epochs.ndjson",
        APP / "netlist" / "modules",
        APP / "canon" / "synth-ordinance.txt",
        MATRIX,
        graph,
        attest,
        unlink_outputs=False,
    )
    assert result.returncode != 0
    assert auditor_stderr(result.stderr) != ""
    _assert_output_identity(graph, before_graph)
    _assert_output_identity(target, before_target)


def test_graph_symlink_target_survives_attest_write_failure():
    """An attest write failure must not change a graph symlink or the file it names."""
    parent = WORK / _desk_token("pub-gfull")
    parent.mkdir(parents=True)
    target = parent / _desk_token("pub-gfull-body")
    prior = b"prior-graph"
    target.write_bytes(prior)
    graph = parent / _desk_token("pub-gfull-g")
    os.symlink(target.name, graph)
    before_graph = _output_identity(graph)
    before_target = _output_identity(target)
    result = invoke_netloom(
        APP / "history" / "synth-epochs.ndjson",
        APP / "netlist" / "modules",
        APP / "canon" / "synth-ordinance.txt",
        MATRIX,
        graph,
        Path("/dev/full"),
        unlink_outputs=False,
    )
    assert result.returncode != 0
    _assert_output_identity(graph, before_graph)
    _assert_output_identity(target, before_target)


def test_absent_outputs_stay_absent_when_inputs_fail():
    """A modules or journal error does not create output paths that were absent."""
    parent = WORK / _desk_token("pub-miss")
    parent.mkdir(parents=True)
    graph = parent / _desk_token("pub-miss-g")
    attest = parent / _desk_token("pub-miss-a")
    missing = parent / _desk_token("pub-miss-mod")
    result = invoke_netloom(
        APP / "history" / "synth-epochs.ndjson",
        missing,
        APP / "canon" / "synth-ordinance.txt",
        MATRIX,
        graph,
        attest,
        unlink_outputs=False,
    )
    assert result.returncode != 0
    assert auditor_stderr(result.stderr) != ""
    assert not graph.exists() and not graph.is_symlink()
    assert not attest.exists() and not attest.is_symlink()
    journal = parent / _desk_token("pub-miss-j")
    journal.write_text("{not-a-journal\n")
    result = invoke_netloom(
        journal,
        APP / "netlist" / "modules",
        APP / "canon" / "synth-ordinance.txt",
        MATRIX,
        graph,
        attest,
        unlink_outputs=False,
    )
    assert result.returncode != 0
    assert not graph.exists() and not graph.is_symlink()
    assert not attest.exists() and not attest.is_symlink()


def test_publication_does_not_expose_a_mixed_generation():
    """A finished publish replaces both outputs and leaves a sibling file unchanged."""
    parent = WORK / _desk_token("pub-atom")
    parent.mkdir(parents=True)
    graph = parent / "graph"
    attest = parent / "attest"
    keep = parent / "keep"
    graph.write_bytes(b"OLD-GRAPH\n")
    attest.write_bytes(b"OLD-ATTEST\n")
    keep.write_bytes(b"keep\n")
    result = invoke_netloom(
        APP / "history" / "synth-epochs.ndjson",
        APP / "netlist" / "modules",
        APP / "canon" / "synth-ordinance.txt",
        MATRIX,
        graph,
        attest,
        unlink_outputs=False,
    )
    assert result.returncode == 0, result.stderr
    assert graph.read_bytes().startswith(b"{")
    assert attest.read_bytes().startswith(b"NPG1\n")
    assert keep.read_bytes() == b"keep\n"


def _trap_codes(rec: dict) -> set[str]:
    codes = set()
    for row in rec.get("must") or []:
        codes.add(row[0])
    for row in rec.get("must_not") or []:
        codes.add(row[0])
    checks = rec.get("checks") or {}
    if checks.get("no_code"):
        codes.add(checks["no_code"])
    for code in checks.get("order_codes") or []:
        codes.add(code)
    for row in checks.get("count") or []:
        if isinstance(row, list) and row:
            codes.add(row[0])
    return codes


def _assert_named_trap(rec: dict) -> None:
    """Grade one named trap. The name, not the list position, is the identity."""
    grouped_re = re.compile(r"[0-9a-fA-F](?:[^A-Za-z0-9]*[0-9a-fA-F]){63}")
    key = rec["name"]
    live = _tiny_audit(key, rec["chronicle"], rec["drops"], rec["matrix"])
    ident = issue_identity(live)
    for row in rec["must"]:
        assert tuple(row) in ident, rec["name"]
    for row in rec["must_not"]:
        assert tuple(row) not in ident, rec["name"]
    checks = rec.get("checks") or {}
    if checks.get("exact") is True:
        assert ident == [tuple(row) for row in rec["must"]], rec["name"]
    if checks.get("exact") == []:
        assert ident == [], rec["name"]
    if "order_codes" in checks:
        code = checks["order_codes"][0]
        rows = [row for row in ident if row[0] == code]
        assert rows == sorted(rows, key=lambda item: item[1]), rec["name"]
    if "count" in checks:
        row, n = checks["count"][0], checks["count"][1]
        assert ident.count(tuple(row)) == n, rec["name"]
    if checks.get("note_cap"):
        notes = [item["note"] for item in live["findings"]]
        assert notes and all(len(item) <= 160 for item in notes), rec["name"]
    if "redact" in checks:
        token = checks["redact"]
        notes = [item["note"] for item in live["findings"]]
        assert notes and all(token not in item for item in notes), rec["name"]
        assert any("redacted" in item for item in notes), rec["name"]
        if checks.get("grouped"):
            assert notes and all(not grouped_re.search(item) for item in notes), rec["name"]
    if "no_code" in checks:
        assert all(row[0] != checks["no_code"] for row in ident), rec["name"]
    attest = read_guarded(WORK / (_desk_token(key) + "-attest.npg"), WORK).decode()
    for line in checks.get("slot_has") or []:
        assert line in attest.splitlines(), rec["name"]
    for line in checks.get("slot_missing") or []:
        assert line not in attest.splitlines(), rec["name"]


def _traps_named(code: str) -> list[dict]:
    rows = [rec for rec in SEALED_TRAPS if code in _trap_codes(rec)]
    assert rows, code
    return sorted(rows, key=lambda item: item["name"])


def test_ghost_map_named_traps():
    """Each named ghost-map trap fails when a recover is treated as replay or as a new generation."""
    for rec in _traps_named("GHOST_MAP"):
        _assert_named_trap(rec)


def test_area_regress_named_traps():
    """Each named area trap fails when cell growth is tied to the wrong liberty revision."""
    for rec in _traps_named("AREA_REGRESS"):
        _assert_named_trap(rec)


def test_stale_lib_named_traps():
    """Each named liberty trap fails when the governing revision is not the highest eligible row."""
    for rec in _traps_named("STALE_LIB"):
        _assert_named_trap(rec)


def test_ancestor_gap_named_traps():
    """Each named ancestor trap fails when the eligible freeze is not the latest settle-ready checkpoint."""
    for rec in _traps_named("ANCESTOR_GAP"):
        _assert_named_trap(rec)


def test_lib_misbind_named_traps():
    """Each named role trap fails when incremental eligibility is not read from the matrix."""
    for rec in _traps_named("LIB_MISBIND"):
        _assert_named_trap(rec)


def test_clock_gate_named_traps():
    """Each named clock-gate trap fails when case folding or an empty clock is ignored."""
    for rec in _traps_named("CLKGT_SPLIT"):
        _assert_named_trap(rec)


def test_named_traps_outside_the_six_codes():
    """Named traps with no finding code still grade empty results, notes, and NODE lines."""
    covered = set()
    for rec in SEALED_TRAPS:
        if _trap_codes(rec):
            covered.add(rec["name"])
    rest = [rec for rec in SEALED_TRAPS if rec["name"] not in covered]
    assert rest
    for rec in sorted(rest, key=lambda item: item["name"]):
        _assert_named_trap(rec)
