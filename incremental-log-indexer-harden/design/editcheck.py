import random, sys
from collections import deque
def bfs(a, b, cap=4):
    alpha = sorted(set(a + b))
    seen = {a: 0}; q = deque([a])
    while q:
        s = q.popleft(); d = seen[s]
        if s == b: return d
        if d >= cap: continue
        nxt = set()
        for i in range(len(s) + 1):
            for c in alpha: nxt.add(s[:i] + c + s[i:])
        for i in range(len(s)):
            nxt.add(s[:i] + s[i+1:])
            for c in alpha: nxt.add(s[:i] + c + s[i+1:])
        for i in range(len(s) - 1):
            nxt.add(s[:i] + s[i+1] + s[i] + s[i+2:])
        for n in nxt:
            if n not in seen and len(n) <= max(len(a), len(b)) + 2:
                seen[n] = d + 1; q.append(n)
    return cap + 1
def dl(a, b):
    inf = len(a) + len(b); d = [[0]*(len(b)+2) for _ in range(len(a)+2)]
    d[0][0] = inf
    for i in range(len(a)+1): d[i+1][0] = inf; d[i+1][1] = i
    for j in range(len(b)+1): d[0][j+1] = inf; d[1][j+1] = j
    last = {}
    for i in range(1, len(a)+1):
        db = 0
        for j in range(1, len(b)+1):
            k, l = last.get(b[j-1], 0), db
            cost = 0 if a[i-1] == b[j-1] else 1
            if cost == 0: db = j
            d[i+1][j+1] = min(d[i][j]+cost, d[i+1][j]+1, d[i][j+1]+1, d[k][l]+(i-k-1)+1+(j-l-1))
        last[a[i-1]] = i
    return d[len(a)+1][len(b)+1]
def osa(a, b):
    d = [[0]*(len(b)+1) for _ in range(len(a)+1)]
    for i in range(len(a)+1): d[i][0] = i
    for j in range(len(b)+1): d[0][j] = j
    for i in range(1, len(a)+1):
        for j in range(1, len(b)+1):
            c = 0 if a[i-1] == b[j-1] else 1
            d[i][j] = min(d[i-1][j]+1, d[i][j-1]+1, d[i-1][j-1]+c)
            if i > 1 and j > 1 and a[i-1] == b[j-2] and a[i-2] == b[j-1]:
                d[i][j] = min(d[i][j], d[i-2][j-2]+1)
    return d[len(a)][len(b)]
if __name__ == "__main__":
    rng = random.Random(7); diff = 0
    for _ in range(3000):
        a = "".join(rng.choice("abc") for _ in range(rng.randint(0, 4)))
        b = "".join(rng.choice("abc") for _ in range(rng.randint(0, 4)))
        t = bfs(a, b)
        if min(t, 3) != min(dl(a, b), 3): print("DL MISMATCH", a, b, t, dl(a, b)); sys.exit(1)
        if min(osa(a, b), 3) != min(t, 3): diff += 1
    print("dl==bfs on 3000 pairs (capped at 3); osa differs within cap on", diff)
