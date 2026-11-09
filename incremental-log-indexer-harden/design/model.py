#!/usr/bin/env python3
"""Independent executable model of indexing-notes.md (R1-R17)."""
import sys

LOWER = set("abcdefghijklmnopqrstuvwxyz")
DIGITS = set("0123456789")
ID_CHARS = LOWER | DIGITS | {"-"}
ALNUM = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")


# ---------------- R6 / R7 analysis ----------------
def tokens(text):
    """Maximal runs of ASCII letters/digits, lowercased (R6)."""
    out, cur = [], []
    for ch in text:
        if ch in ALNUM:
            cur.append(ch.lower() if "A" <= ch <= "Z" else ch)
        else:
            if cur:
                out.append("".join(cur))
                cur = []
    if cur:
        out.append("".join(cur))
    return out


# ---------------- R13 edit distance ----------------
def edit_distance(a, b):
    """True (unrestricted) Damerau-Levenshtein distance: the least number of
    insert/delete/replace/adjacent-swap edits (Lowrance-Wagner algorithm)."""
    INF = len(a) + len(b)
    da = {}
    d = [[0] * (len(b) + 2) for _ in range(len(a) + 2)]
    d[0][0] = INF
    for i in range(len(a) + 1):
        d[i + 1][0] = INF
        d[i + 1][1] = i
    for j in range(len(b) + 1):
        d[0][j + 1] = INF
        d[1][j + 1] = j
    for i in range(1, len(a) + 1):
        db = 0
        for j in range(1, len(b) + 1):
            i1 = da.get(b[j - 1], 0)
            j1 = db
            cost = 1
            if a[i - 1] == b[j - 1]:
                cost = 0
                db = j
            d[i + 1][j + 1] = min(
                d[i][j] + cost,
                d[i + 1][j] + 1,
                d[i][j + 1] + 1,
                d[i1][j1] + (i - i1 - 1) + 1 + (j - j1 - 1),
            )
        da[a[i - 1]] = i
    return d[len(a) + 1][len(b) + 1]


# ---------------- R14 / R15 distance ----------------
def _has_matching(words, allowed):
    """allowed[w] = set of token positions word w may take; injective?"""
    owner = {}

    def try_word(w, seen):
        for p in allowed[w]:
            if p in seen:
                continue
            seen.add(p)
            if p not in owner or try_word(owner[p], seen):
                owner[p] = w
                return True
        return False

    for w in range(len(words)):
        if not try_word(w, set()):
            return False
    return True


def doc_distance(qwords, doc_tokens, stops, fuzz, _cache):
    """Least spread over all placements, or None if no placement.
    qwords: list of (word, query position)."""
    # candidate (word index, token position, offset)
    cands = []
    for wi, (w, qp) in enumerate(qwords):
        for p, t in enumerate(doc_tokens):
            if t in stops:
                continue  # R7: stop words are never matched
            key = (w, t)
            dist = _cache.get(key)
            if dist is None:
                dist = edit_distance(w, t)
                _cache[key] = dist
            if dist <= fuzz:
                cands.append((wi, p, p - qp))
    n = len(qwords)
    offs = sorted(set(c[2] for c in cands))

    def feasible(lo, hi):
        allowed = [[] for _ in range(n)]
        for wi, p, o in cands:
            if lo <= o <= hi:
                allowed[wi].append(p)
        if any(not a for a in allowed):
            return False
        return _has_matching(qwords, allowed)

    if not offs or not feasible(offs[0], offs[-1]):
        return None
    best = None
    # Two pointers: minimal feasible hi is nondecreasing in lo.
    j = 0
    for i, lo in enumerate(offs):
        if j < i:
            j = i
        while j < len(offs) and not feasible(lo, offs[j]):
            j += 1
        if j == len(offs):
            break
        s = offs[j] - lo
        if best is None or s < best:
            best = s
    return best


# ---------------- parsing ----------------
def split_lines(journal):
    if journal.endswith("\n"):
        return journal[:-1].split("\n"), True
    return journal.split("\n"), False


def parse_line(line):
    """Return (kind, fields) structurally, or (None, reason)."""
    if line in ("begin", "commit", "rollback"):
        return line, {}
    for kw in ("add", "append"):
        if line.startswith(kw + " "):
            rest = line[len(kw) + 1:]
            if " " not in rest:
                return None, "%s line without text" % kw
            ident, text = rest.split(" ", 1)
            return kw, {"id": ident, "text": text}
    if line.startswith("delete "):
        return "delete", {"id": line[len("delete "):]}
    if line.startswith("query "):
        rest = line[len("query "):]
        parts = rest.split(" ", 2)
        if len(parts) < 3:
            return None, "query line lacks slop, fuzz or text"
        return "query", {"slop": parts[0], "fuzz": parts[1], "text": parts[2]}
    return None, "not one of the seven line forms"


def parse_stopwords(line):
    """Return (set of stop words, list of problems)."""
    probs = []
    if line == "stopwords":
        return set(), probs
    if not line.startswith("stopwords "):
        return set(), ["R2: first line is not 'stopwords' followed by stop words"]
    words = line[len("stopwords "):].split(" ")
    if len(words) > 20:
        probs.append("R2: %d stop words (max 20)" % len(words))
    seen = set()
    for w in words:
        if not (1 <= len(w) <= 12 and all(c in LOWER for c in w)):
            probs.append("R2: bad stop word %r (1-12 letters a-z, one space each)" % w)
        elif w in seen:
            probs.append("R2: stop word %r listed twice" % w)
        seen.add(w)
    return {w for w in words if w}, probs


# ---------------- replay ----------------
def _replay(journal):
    """Replay the journal; return (output lines, violations)."""
    viol = []
    lines, ends_lf = split_lines(journal)
    if journal == "":
        lines = []
    if not ends_lf and journal != "":
        viol.append("R1: input does not end with LF")
    if not (1 <= len(lines) <= 400):
        viol.append("R1: input holds %d lines (1-400)" % len(lines))
    for n, line in enumerate(lines, 1):
        if line == "":
            viol.append("R1: line %d is empty" % n)
        bad = [c for c in line if not (" " <= c <= "~")]
        if bad:
            viol.append("R1: line %d holds non-printable character(s) %r" % (n, "".join(sorted(set(bad)))))

    out = []
    if not lines:
        return out, viol
    stops, p = parse_stopwords(lines[0])
    viol.extend("line 1: " + x for x in p)

    committed = {}  # id -> list of tokens
    work = None     # working copy while a batch is open
    for n, line in enumerate(lines[1:], 2):
        where = "line %d: " % n
        kind, f = parse_line(line)
        if kind is None:
            viol.append(where + "R3: " + f)
            continue
        if "id" in f:
            i = f["id"]
            if not (1 <= len(i) <= 16 and all(c in ID_CHARS for c in i)):
                viol.append(where + "R3: bad id %r" % i)
        if kind in ("add", "append") and not (1 <= len(f["text"]) <= 200):
            viol.append(where + "R3: %s text length %d (1-200)" % (kind, len(f["text"])))

        if kind == "begin":
            if work is not None:
                viol.append(where + "R4: begin inside an open batch")
                continue
            work = {k: list(v) for k, v in committed.items()}
        elif kind in ("commit", "rollback"):
            if work is None:
                viol.append(where + "R4: %s outside a batch" % kind)
                continue
            if kind == "commit":
                committed = work
            work = None
        elif kind in ("add", "append", "delete"):
            if work is None:
                viol.append(where + "R4: %s outside a batch" % kind)
                continue
            i = f["id"]
            if kind == "add":
                work[i] = tokens(f["text"])
            elif kind == "append":
                if i not in work:
                    viol.append(where + "domain: append names id %r that is not live" % i)
                    continue
                work[i] = work[i] + tokens(f["text"])
            else:
                work.pop(i, None)
            if kind != "delete" and len(work[i]) > 300:
                viol.append(where + "R11: document %r holds %d tokens (0-300)" % (i, len(work[i])))
            if len(work) > 100:
                viol.append(where + "R11: working copy holds %d live documents (max 100)" % len(work))
        elif kind == "query":
            if work is not None:
                viol.append(where + "R4: query inside an open batch")
            slop, fuzz, text = f["slop"], f["fuzz"], f["text"]
            ok = True
            if not (len(slop) == 1 and slop in DIGITS):
                viol.append(where + "R3: bad slop %r (one digit 0-9)" % slop)
                ok = False
            if not (len(fuzz) == 1 and fuzz in "012"):
                viol.append(where + "R3: bad fuzz %r (one digit 0-2)" % fuzz)
                ok = False
            if not (1 <= len(text) <= 120):
                viol.append(where + "R3: query text length %d (1-120)" % len(text))
            qwords = [(t, pos) for pos, t in enumerate(tokens(text)) if t not in stops]
            if not (1 <= len(qwords) <= 6):
                viol.append(where + "R12: query keeps %d words (1-6)" % len(qwords))
                ok = False
            if not ok:
                out.append("-")
                continue
            out.append(run_query(committed, stops, int(slop), int(fuzz), qwords))
    if work is not None:
        viol.append("R4: input ends while a batch is open")
    return out, viol


def run_query(index, stops, slop, fuzz, qwords):
    cache = {}
    hits = []
    for ident, toks in index.items():
        d = doc_distance(qwords, toks, stops, fuzz, cache)
        if d is not None and d <= slop:
            hits.append((d, ident.encode("latin-1"), ident))
    hits.sort()
    if not hits:
        return "-"
    return " ".join("%s@%d" % (h[2], h[0]) for h in hits)


def answer(journal: str) -> str:
    out, _ = _replay(journal)
    return "".join(line + "\n" for line in out)


def violations(journal: str) -> list:
    return _replay(journal)[1]


if __name__ == "__main__":
    data = sys.stdin.buffer.read().decode("latin-1")
    if "--check" in sys.argv[1:]:
        for v in violations(data):
            print(v)
    else:
        sys.stdout.write(answer(data))
