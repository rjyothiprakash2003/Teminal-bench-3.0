"""Builds every mutant, victim and the lazy tree from solution/main.go, runs each over the
whole graded surface (the witnesses read from tests/test_outputs.py itself, plus the
sealed journals) and prints which tests each one fails. Verdicts:
  mutant  fails at least one witness of its owner test
  victim  prints every shipped example exactly, fails witnesses in both owner tests and
          in no other test (held-out included)
  lazy    fails every owner test of every cell
  cells   no test carries witnesses of more than two cells"""
import ast
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TASK = HERE.parent
sys.path.insert(0, str(HERE))
import mutants as M  # noqa: E402


def load_witnesses():
    tree = ast.parse((TASK / "tests" / "test_outputs.py").read_text())
    consts, tests = {}, {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
            consts[node.targets[0].id] = ast.literal_eval(node.value)
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            call = node.body[-1].value
            names = [a.id for a in call.args[1:] if isinstance(a, ast.Name)]
            tests[node.name] = [consts[n] for n in names]
    sealed = []
    for j in sorted((TASK / "tests" / "sealed").glob("*.journal")):
        sealed.append({"name": "held-out " + j.stem, "journal": j.read_text(),
                       "answers": j.with_suffix(".answers").read_text()})
    tests["test_held_out_journals"] = sealed
    return tests


def examples():
    ex = []
    for j in sorted((TASK / "environment" / "app" / "examples").glob("*.journal")):
        ex.append({"name": "example " + j.stem, "journal": j.read_text(),
                   "answers": j.with_suffix(".answers").read_text()})
    return ex


def run(binary, journal):
    try:
        r = subprocess.run([str(binary)], input=journal.encode(), capture_output=True, timeout=10)
    except subprocess.TimeoutExpired:
        return None
    return r.stdout.decode() if r.returncode == 0 else None


def failing(binary, tests):
    out = {}
    for t, ws in tests.items():
        bad = [w["name"] for w in ws if run(binary, w["journal"]) != w["answers"]]
        if bad:
            out[t] = bad
    return out


def build(work, bindir):
    ref = (TASK / "solution" / "main.go").read_text()
    gomod = (TASK / "environment" / "app" / "logindex" / "go.mod").read_text()
    trees = {"reference": ref}
    for name, (_, patches) in M.MUTANTS.items():
        trees[name] = M.apply(ref, patches)
    for name, (_, patches) in M.VICTIMS.items():
        trees[name] = M.apply(ref, patches)
    trees["lazy"] = M.apply(ref, M.LAZY)
    if work.exists():
        shutil.rmtree(work)
    for name, src in trees.items():
        (work / name).mkdir(parents=True)
        (work / name / "main.go").write_text(src)
        (work / name / "go.mod").write_text(gomod)
    bindir.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([str(HERE / "gobuild.sh"), str(bindir)] + [str(work / n) for n in trees], capture_output=True, text=True)
    if r.returncode:
        raise SystemExit("build failed:\n" + r.stdout[-3000:] + r.stderr[-3000:])
    return trees


def main(scratch):
    scratch = Path(scratch)
    work, bindir = scratch / "trees", scratch / "treebin"
    build(work, bindir)
    tests = load_witnesses()
    ex = {"examples": examples()}
    ok = True
    ref_fail = failing(bindir / "reference", tests)
    print("reference control:", "PASS" if not ref_fail else "FAIL %s" % ref_fail)
    ok &= not ref_fail
    survivors = 0
    for name, (owner, _) in M.MUTANTS.items():
        f = failing(bindir / name, tests)
        own = f.get(owner, [])
        sites = len(f)
        status = "ok" if own else "SURVIVES ITS OWNER"
        if not own:
            survivors += 1
        print("mutant %-40s owner %-55s own %d, tests failed %d %s" % (name, owner, len(own), sites, status))
    ok &= survivors == 0
    for name, (owners, _) in M.VICTIMS.items():
        f = failing(bindir / name, tests)
        exf = failing(bindir / name, ex)
        good = set(f) == set(owners) and not exf
        ok &= good
        print("victim %s fails %s examples %s -> %s" % (name, {k: len(v) for k, v in f.items()},
                                                       "all exact" if not exf else exf, "ok" if good else "BAD"))
    lf = failing(bindir / "lazy", tests)
    owners = {o for os_, _ in M.VICTIMS.values() for o in os_}
    good = owners <= set(lf)
    ok &= good
    print("lazy fails %s -> %s" % (sorted(lf), "ok" if good else "BAD"))
    carried = {}
    for name, (os_, _) in M.VICTIMS.items():
        for o in os_:
            carried.setdefault(o, []).append(name)
    worst = max(len(v) for v in carried.values())
    ok &= worst <= 2
    print("cells per test, most:", worst)
    print("mutants %d, survivors %d" % (len(M.MUTANTS), survivors))
    print("SWEEP", "GREEN" if ok else "RED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main(sys.argv[1])
