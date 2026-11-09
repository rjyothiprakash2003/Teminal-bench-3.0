"""Mints tests/sealed: held-out journals drawn with a fixed seed, each inside the domain
(model.violations empty), answered identically by the reference and the independent model,
cell free (both cell victims print the reference's answer, so the held-out test carries no
cell), and distinct from every shipped example. Some are drawn at the size limits."""
import hashlib
import subprocess
import sys
from pathlib import Path
import random

HERE = Path(__file__).resolve().parent
TASK = HERE.parent
sys.path.insert(0, str(HERE))
import gen  # noqa: E402
import model  # noqa: E402


def out(binary, j):
    return subprocess.run([str(binary)], input=j.encode(), capture_output=True, timeout=10).stdout.decode()


def main(bindir, count=30):
    bindir = Path(bindir)
    ref, victims = bindir / "reference", [bindir / "victim_stop_token_fuzzy_match", bindir / "victim_restricted_edit_distance"]
    shipped = {hashlib.sha256(p.read_bytes()).hexdigest() for p in (TASK / "environment" / "app").rglob("*") if p.is_file()}
    sealed = TASK / "tests" / "sealed"
    for p in sealed.glob("*"):
        p.unlink()
    rng = random.Random(20261013)
    made, tried = 0, 0
    while made < count:
        tried += 1
        size = 400 if made % 6 == 5 else rng.randint(12, 60)
        j = gen.journal(rng, size)
        if model.violations(j) or hashlib.sha256(j.encode()).hexdigest() in shipped:
            continue
        a = out(ref, j)
        if a != model.answer(j) or any(out(v, j) != a for v in victims):
            continue
        if a.count("@") < 2:
            continue
        made += 1
        (sealed / ("%03d.journal" % made)).write_text(j)
        (sealed / ("%03d.answers" % made)).write_text(a)
    print("sealed %d held-out journals (%d drawn)" % (made, tried))


if __name__ == "__main__":
    main(sys.argv[1])
