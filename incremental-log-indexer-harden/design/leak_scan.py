"""Leak scan: hashes every file the agent can read (environment/) and every file the
verifier holds (tests/), and every graded journal and answer as bytes, and reports each
byte-identical pair. Also refuses a graded journal that equals a shipped example."""
import hashlib
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TASK = HERE.parent
sys.path.insert(0, str(HERE))
from sweep import load_witnesses  # noqa: E402


def h(b):
    return hashlib.sha256(b).hexdigest()


def main():
    agent = {h(p.read_bytes()): str(p.relative_to(TASK)) for p in (TASK / "environment").rglob("*") if p.is_file()}
    verifier = {h(p.read_bytes()): str(p.relative_to(TASK)) for p in (TASK / "tests").rglob("*") if p.is_file()}
    graded = {}
    for ws in load_witnesses().values():
        for w in ws:
            graded[h(w["journal"].encode())] = w["name"] + " journal"
            graded[h(w["answers"].encode())] = w["name"] + " answers"
    pairs = [(agent[k], verifier[k]) for k in agent if k in verifier]
    pairs += [(agent[k], graded[k]) for k in agent if k in graded and len(k) and agent[k].endswith((".journal", ".answers"))]
    declared = {("environment/.dockerignore", "tests/.dockerignore")}
    undeclared = [p for p in pairs if p not in declared]
    print("leak scan: %d agent files, %d verifier files, %d graded strings; identical pairs %d, undeclared %d %s"
          % (len(agent), len(verifier), len(graded), len(pairs), len(undeclared), undeclared or ""))
    sys.exit(1 if undeclared else 0)


if __name__ == "__main__":
    main()
