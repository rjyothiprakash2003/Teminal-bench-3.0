"""The inputs gate: every graded journal (each witness in tests/test_outputs.py and every
sealed journal) and every shipped example meets the domain by model.violations; the
self-test plants out-of-domain journals and requires the gate to refuse each."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TASK = HERE.parent
sys.path.insert(0, str(HERE))
import model  # noqa: E402
from sweep import load_witnesses  # noqa: E402

PLANTED = {
    "no final LF": "stopwords\nquery 0 0 disk",
    "slop of two digits": "stopwords\nquery 10 0 disk\n",
    "fuzz of three": "stopwords\nquery 0 3 disk\n",
    "query of only stop words": "stopwords the\nquery 0 0 the\n",
    "seven query words": "stopwords\nquery 0 0 a b c d e f g\n",
    "append to an id not live": "stopwords\nbegin\nappend x disk\ncommit\n",
    "batch left open": "stopwords\nbegin\nadd x disk\n",
    "id with a capital": "stopwords\nbegin\nadd X disk\ncommit\n",
    "401 lines": "stopwords\n" + "query 0 0 disk\n" * 400,
    "301 tokens": "stopwords\nbegin\nadd x " + " ".join(["a"] * 100) + "\nappend x " + " ".join(["a"] * 100)
                  + "\nappend x " + " ".join(["a"] * 101) + "\ncommit\n",
}


def main():
    ok = True
    graded = [w for ws in load_witnesses().values() for w in ws]
    examples = [p.read_text() for p in sorted((TASK / "environment/app/examples").glob("*.journal"))]
    out = [w["name"] for w in graded if model.violations(w["journal"])]
    out += ["example %d" % i for i, j in enumerate(examples) if model.violations(j)]
    print("inputs gate: %d graded journals and %d examples checked, %d outside the domain %s"
          % (len(graded), len(examples), len(out), out or ""))
    ok &= not out
    disagree = [w["name"] for w in graded if model.answer(w["journal"]) != w["answers"]]
    print("model agreement: %d graded answers, %d differ from the independent model %s"
          % (len(graded), len(disagree), disagree or ""))
    ok &= not disagree
    missed = [k for k, j in PLANTED.items() if not model.violations(j)]
    print("inputs gate self-test: %d planted journals, %d let through %s" % (len(PLANTED), len(missed), missed or ""))
    ok &= not missed
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
