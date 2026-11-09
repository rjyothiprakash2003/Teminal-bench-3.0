"""Grades programs against the tests of tests/test_outputs.py and the sealed journals:
prints, for each binary, the tests it fails and the witnesses within them."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from sweep import load_witnesses, failing
tests = load_witnesses()
for b in sys.argv[1:]:
    f = failing(Path(b), tests)
    print(Path(b).name, "PASS ALL" if not f else f)
