"""Static checks in the spirit of preflight (KT-26 61.7, 60.2): counts, word limits, the
comment law, staging and resource rules, the page band, and the run-time measure."""
import ast
import re
import subprocess
import sys
import time
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
TASK = HERE.parent
sys.path.insert(0, str(HERE))
from sweep import load_witnesses  # noqa: E402

problems, notes = [], []


def need(cond, msg):
    (notes if cond else problems).append(msg)


def sentences(text):
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


tp = TASK / "tests/test_outputs.py"
src = tp.read_text()
tree = ast.parse(src)
tests = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")]
need(len(tests) <= 18, "domain tests: %d (at most 18)" % len(tests))
need(not any(l.lstrip().startswith("#") for l in src.splitlines()), "test_outputs.py comment lines: none")
need(ast.get_docstring(tree) is None, "test_outputs.py module docstring: none")
helpers = [n for n in tree.body if isinstance(n, ast.FunctionDef) and not n.name.startswith("test_")]
need(all(ast.get_docstring(h) is None for h in helpers), "helper docstrings: none")
need(all(ast.get_docstring(t) and "\n" not in ast.get_docstring(t) for t in tests), "every test has a one-line docstring")
need("copytree(" not in src or "symlinks=True" in src, "copytree passes symlinks=True")
need(not re.search(r"os\.(fchmod|fchown|mkfifo)", src), "no descriptor-level permission calls")

comment_count = 0
for p in list((TASK / "solution").glob("*")) + [TASK / "environment/Dockerfile", TASK / "tests/Dockerfile", TASK / "tests/test.sh"]:
    for i, line in enumerate(p.read_text().splitlines()):
        s = line.strip()
        if (s.startswith("#") and not (i == 0 and s.startswith("#!"))) or s.startswith("//"):
            comment_count += 1
need(comment_count <= 4, "comment lines in solution/, Dockerfiles and test.sh: %d (at most 4)" % comment_count)
everything = "".join(p.read_text(errors="replace") for p in TASK.rglob("*") if p.is_file() and "design" not in p.parts)
need("# Hint" not in everything, "no template hint comment")
need("--ctrf /logs/verifier/ctrf.json" in (TASK / "tests/test.sh").read_text(), "test.sh writes ctrf")
need(".gitignore" in (TASK / "environment/.dockerignore").read_text(), "environment/.dockerignore lists .gitignore")
need(sorted(p.name for p in TASK.iterdir() if p.name not in ("design", ".DS_Store"))
     == ["environment", "instruction.md", "solution", "task.toml", "tests"], "task root holds only the five shipped entries")

toml = tomllib.loads((TASK / "task.toml").read_text())
m = toml["metadata"]
for k in ("difficulty_explanation", "solution_explanation", "verification_explanation"):
    n = len(m[k].split())
    need(80 <= n <= 120, "%s: %d words (80 to 120)" % (k, n))
    longest = max(len(s.split()) for s in sentences(m[k]))
    need(longest <= 39, "%s: longest sentence %d words (at most 39)" % (k, longest))
need(toml["environment"]["cpus"] <= 2 and toml["environment"]["memory_mb"] <= 8192
     and toml["environment"]["storage_mb"] <= 10240, "resource limits inside the envelope")
need(toml["agent"]["timeout_sec"] <= 18000 and toml["verifier"]["timeout_sec"] <= 18000, "timeouts at most 18000")
ins = (TASK / "instruction.md").read_text()
need(len(ins.split()) <= 300, "instruction: %d words (at most 300)" % len(ins.split()))
need("/app/" in sentences(ins)[0], "instruction's first sentence names an absolute path")
need(max(len(s.split()) for s in sentences(ins)) <= 39, "instruction: no sentence over 39 words")
page = (TASK / "environment/app/docs/indexing-notes.md").read_text()
nl, nr = page.count("\n"), len(re.findall(r"^R\d+\.", page, re.M))
need(150 <= nl <= 260 and 10 <= nr <= 18, "page: %d lines, %d rules (150 to 260, 10 to 18)" % (nl, nr))
ne = len(list((TASK / "environment/app/examples").glob("*.journal")))
need(6 <= ne <= 10, "examples: %d sets (6 to 10)" % ne)

binary = sys.argv[1]
worst = 0.0
for ws in load_witnesses().values():
    for w in ws:
        t0 = time.perf_counter()
        subprocess.run([binary], input=w["journal"].encode(), capture_output=True, timeout=10)
        worst = max(worst, time.perf_counter() - t0)
need(worst <= 1.0, "slowest graded run of the reference: %.3f s (stop 10 s, at most a tenth)" % worst)

for n in notes:
    print("ok   " + n)
for p in problems:
    print("FAIL " + p)
print("CHECKS", "GREEN" if not problems else "RED")
sys.exit(1 if problems else 0)
