"""Agreement of the reference binary and the independent model on random in-domain
journals drawn from two fixed seeds; prints the count and any disagreement."""
import random
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gen  # noqa: E402
import model  # noqa: E402


def main(binary, per_seed=400):
    bad = drawn = 0
    for seed in (101, 202):
        rng = random.Random(seed)
        n = 0
        while n < per_seed:
            j = gen.journal(rng, rng.choice([20, 40, 80, 400]))
            if model.violations(j):
                continue
            n += 1
            drawn += 1
            a = subprocess.run([binary], input=j.encode(), capture_output=True, timeout=10).stdout.decode()
            if a != model.answer(j):
                bad += 1
                print("DISAGREE seed %d case %d" % (seed, n))
    print("fuzz: %d journals over seeds 101 and 202, %d disagreements" % (drawn, bad))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main(sys.argv[1])
