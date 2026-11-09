"""The over-strictness guard. The reference runs first as the control and must pass every
graded test; then every tolerated tree, each a program the notes permit that differs from
the reference only where no sentence speaks (independently written programs of the early
sitting, a variant that writes to standard error and prints line by line), must pass too.
A tolerated tree that fails names an assertion stricter than the notes."""
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TASK = HERE.parent
sys.path.insert(0, str(HERE))
from sweep import load_witnesses, failing  # noqa: E402


def main(scratch):
    scratch = Path(scratch) / "tolerated"
    if scratch.exists():
        shutil.rmtree(scratch)
    gomod = (TASK / "environment/app/logindex/go.mod").read_text()
    trees = {"reference-control": (TASK / "solution/main.go").read_text()}
    for d in sorted((HERE / "tolerated").iterdir()):
        trees[d.name] = (d / "main.go.txt").read_text()
    for name, src in trees.items():
        (scratch / "src" / name).mkdir(parents=True)
        (scratch / "src" / name / "main.go").write_text(src)
        (scratch / "src" / name / "go.mod").write_text(gomod)
    (scratch / "bin").mkdir()
    r = subprocess.run([str(HERE / "gobuild.sh"), str(scratch / "bin")] + [str(scratch / "src" / n) for n in trees],
                       capture_output=True, text=True)
    if r.returncode:
        raise SystemExit("build failed:\n" + r.stdout[-2000:] + r.stderr[-2000:])
    tests = load_witnesses()
    ok = True
    for name in trees:
        f = failing(scratch / "bin" / name, tests)
        print("%-28s %s" % (name, "PASS" if not f else "FAIL %s" % f))
        ok &= not f
        if name == "reference-control" and f:
            print("control failed: the guard cannot judge tolerated trees")
            sys.exit(1)
    print("over-strictness guard: %d tolerated trees, %s" % (len(trees) - 1, "all pass" if ok else "SOME FAIL"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main(sys.argv[1])
