"""Random journals inside the domain of the notes (validated by model.violations by the
callers). Small vocabularies so that queries hit, near misses and repeats are common."""
import random

WORDS = ["disk", "dusk", "desk", "full", "fill", "node", "nodes", "code", "cache", "catch", "miss",
         "retry", "error", "eror", "timeout", "time", "out", "link", "down", "up", "cpu", "cup",
         "auth", "uath", "pool", "poll", "host", "hosts", "ack", "akc", "ca", "abc", "api", "aip"]
STOPS = ["the", "on", "at", "of", "a", "to", "is", "an"]
PUNCT = [" ", " ", " ", "  ", ", ", "-", "/", ":", "_", "(", ")", "=", "."]


def text(rng, n, stops, cap=200):
    parts = []
    for i in range(n):
        w = rng.choice(WORDS + stops * 2) if stops else rng.choice(WORDS)
        if rng.random() < 0.15:
            w = w.upper() if rng.random() < 0.5 else w.capitalize()
        if rng.random() < 0.05:
            w += str(rng.randint(0, 99))
        parts.append(w)
        if i < n - 1:
            parts.append(rng.choice(PUNCT))
    t = "".join(parts)
    if rng.random() < 0.1:
        t = " " + t
    return t[:cap] or "x"


def journal(rng, size=40):
    stops = rng.sample(STOPS, rng.randint(0, len(STOPS)))
    lines = ["stopwords" + "".join(" " + s for s in stops)]
    live, ids = set(), ["a", "b", "c", "d-1", "d10", "d2", "e-x", "f"]
    lengths = {}
    while len(lines) < size:
        if rng.random() < 0.55:
            lines.append("begin")
            work, wl = set(live), dict(lengths)
            for _ in range(rng.randint(0, 5)):
                r = rng.random()
                i = rng.choice(ids)
                if r < 0.45:
                    n = rng.randint(0, 9)
                    lines.append("add %s %s" % (i, text(rng, n, stops) if n else rng.choice(["--", "...", "x"])))
                    work.add(i)
                    wl[i] = n
                elif r < 0.7 and work:
                    i = rng.choice(sorted(work))
                    n = rng.randint(1, 6)
                    if wl.get(i, 0) + n <= 290:
                        lines.append("append %s %s" % (i, text(rng, n, stops)))
                        wl[i] = wl.get(i, 0) + n
                else:
                    lines.append("delete %s" % i)
                    work.discard(i)
            if rng.random() < 0.75:
                lines.append("commit")
                live, lengths = work, wl
            else:
                lines.append("rollback")
        else:
            k = rng.randint(1, 4)
            words = [rng.choice(WORDS) for _ in range(k)]
            if rng.random() < 0.3:
                words.append(rng.choice(words))
            rng.shuffle(words)
            if stops and rng.random() < 0.4:
                words.insert(rng.randint(0, len(words)), rng.choice(stops))
            lines.append("query %d %d %s" % (rng.randint(0, 9), rng.choice([0, 0, 1, 1, 2]), " ".join(words)))
    return "\n".join(lines[:400]) + "\n"
