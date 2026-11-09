#!/usr/bin/env python3
"""Independent executable model of indexing-notes.md (R1-R16)."""
import re
import sys

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# ---------- R5 calendar, done by hand ----------
def is_leap(y):
    return y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)

def month_len(y, m):
    if m == 2:
        return 29 if is_leap(y) else 28
    return 30 if m in (4, 6, 9, 11) else 31

def days_before_year(y):
    # days from 0001-01-01 to y-01-01 (proleptic Gregorian), counted by rule
    p = y - 1
    return 365 * p + p // 4 - p // 100 + p // 400

def to_seconds(y, mo, d, h, mi, s):
    days = days_before_year(y)
    for m in range(1, mo):
        days += month_len(y, m)
    days += d - 1
    return days * 86400 + h * 3600 + mi * 60 + s

def from_seconds(t):
    days, rem = divmod(t, 86400)
    h, rem = divmod(rem, 3600)
    mi, s = divmod(rem, 60)
    y = days // 366 + 1
    while days_before_year(y + 1) <= days:
        y += 1
    while days_before_year(y) > days:
        y -= 1
    days -= days_before_year(y)
    m = 1
    while days >= month_len(y, m):
        days -= month_len(y, m)
        m += 1
    return y, m, days + 1, h, mi, s

def fmt_instant(t):
    return "%04d-%02d-%02dT%02d:%02d:%02d" % from_seconds(t)

# ---------- parsing ----------
INSTANT_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})$")
WORD_RE = re.compile(r"^[a-z0-9]{1,32}$")
LOG_RE = re.compile(
    r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) "
    r"( [1-9]|[1-3][0-9]) "
    r"([0-9]{2}):([0-9]{2}):([0-9]{2}) "
    r"([a-z0-9.\-]{1,32}) "
    r"([A-Za-z0-9_/\-]{1,32})(\[[0-9]{1,7}\])?: "
    r"(.*)$", re.S)
RETAIN_RE = re.compile(r"^retain ([1-9][0-9]*)$")

def parse_instant(s):
    """Return (seconds, error-or-None)."""
    m = INSTANT_RE.match(s)
    if not m:
        return None, "malformed instant %r" % s
    y, mo, d, h, mi, se = map(int, m.groups())
    if not (2001 <= y <= 2097):
        return None, "year out of 2001..2097 in %r" % s
    if not (1 <= mo <= 12) or not (1 <= d <= month_len(y, mo)):
        return None, "not a real date %r" % s
    if h > 23 or mi > 59 or se > 59:
        return None, "time out of range %r" % s
    return to_seconds(y, mo, d, h, mi, se), None

def words_of(msg):
    return [w.lower() for w in re.findall(r"[A-Za-z0-9]+", msg)]

def parse_log(line):
    """Return (dict, errors)."""
    m = LOG_RE.match(line)
    if not m:
        return None, ["malformed log line %r" % line]
    mon, dd, hh, mm, ss, host, tag, pid, msg = m.groups()
    errs = []
    mo = MONTHS.index(mon) + 1
    d = int(dd)
    h, mi, s = int(hh), int(mm), int(ss)
    if h > 23 or mi > 59 or s > 59:
        errs.append("log time out of range %r" % line)
    # R7: a day some year has (leap year gives the maximum)
    if not (1 <= d <= month_len(2000, mo)):
        errs.append("log month/day does not exist %r" % line)
    if len(msg) > 200:
        errs.append("message longer than 200 chars %r" % line)
    if any(not (0x20 <= ord(c) <= 0x7E) for c in msg):
        errs.append("message has non-R1 characters %r" % line)
    return dict(mo=mo, d=d, h=h, mi=mi, s=s, msg=msg), errs

def classify(line):
    if line.startswith("batch "):
        return "batch"
    if line.startswith("count "):
        return "count"
    if line.startswith("lag "):
        return "lag"
    return "log"

def line_instant(stamp, batch_t):
    """R8: year putting the stamp nearest to batch_t; ties -> earlier year."""
    by = from_seconds(batch_t)[0]
    best = None
    for y in range(by - 12, by + 13):
        if stamp["mo"] == 2 and stamp["d"] == 29 and not is_leap(y):
            continue
        if stamp["d"] > month_len(y, stamp["mo"]):
            continue
        t = to_seconds(y, stamp["mo"], stamp["d"], stamp["h"], stamp["mi"], stamp["s"])
        key = (abs(t - batch_t), y)
        if best is None or key < best[0]:
            best = (key, t)
    return best[1]

# ---------- replay ----------
def replay(journal, collect_errors=False):
    errors = []
    out = []

    # R1
    if journal == "":
        errors.append("R1: input holds no lines")
        lines = []
    else:
        if not journal.endswith("\n"):
            errors.append("R1: last line does not end with LF")
            lines = journal.split("\n")
        else:
            lines = journal[:-1].split("\n")
    if not (1 <= len(lines) <= 600):
        errors.append("R1: %d lines, not 1..600" % len(lines))
    for i, ln in enumerate(lines, 1):
        if ln == "":
            errors.append("R1: line %d is empty" % i)
        if any(not (0x20 <= ord(c) <= 0x7E) for c in ln):
            errors.append("R1: line %d has a character outside 0x20..0x7E" % i)

    if not lines:
        return out, errors

    # R2
    days = None
    m = RETAIN_RE.match(lines[0])
    if not m:
        errors.append("R2: first line is not 'retain <days>': %r" % lines[0])
    else:
        days = int(m.group(1))
        if not (1 <= days <= 3650):
            errors.append("R2: retain days %d not in 1..3650" % days)
            days = None

    index = []          # list of (instant, filing_seq, lag, words)
    seq = 0
    state = "start"     # start | batch | query
    batch_t = None
    last_batch_t = None
    nlog = 0
    nquery = 0
    for i, ln in enumerate(lines[1:], 2):
        kind = classify(ln)
        if kind == "batch":
            state = "batch"
            t, e = parse_instant(ln[len("batch "):])
            if e:
                errors.append("R4: line %d: %s" % (i, e))
                batch_t = None
                continue
            if last_batch_t is not None and t <= last_batch_t:
                errors.append("R4: line %d: batch instant does not strictly increase" % i)
            last_batch_t = t
            batch_t = t
            if days is not None:
                limit = days * 86400
                index = [x for x in index if not (batch_t - x[0] > limit)]
        elif kind == "log":
            nlog += 1
            if nlog == 501:
                errors.append("R15: more than 500 log lines (line %d)" % i)
            stamp, errs = parse_log(ln)
            for e in errs:
                errors.append("R6/R7: line %d: %s" % (i, e))
            if state == "start":
                errors.append("R3: line %d: log line before the first batch" % i)
                continue
            if state == "query":
                errors.append("R3: line %d: log line straight after a query line" % i)
                continue
            if stamp is None or errs or batch_t is None or days is None:
                continue
            t = line_instant(stamp, batch_t)
            if batch_t - t > days * 86400:
                continue
            index.append((t, seq, batch_t - t, set(words_of(stamp["msg"]))))
            seq += 1
            if len(index) == 501:
                errors.append("R15: index holds more than 500 lines (line %d)" % i)
        elif kind == "count":
            state = "query"
            nquery += 1
            if nquery == 41:
                errors.append("R15: more than 40 queries (line %d)" % i)
            parts = ln.split(" ")
            if len(parts) != 4:
                errors.append("R3: line %d: malformed count line %r" % (i, ln))
                continue
            f, e1 = parse_instant(parts[1])
            to, e2 = parse_instant(parts[2])
            w = parts[3]
            bad = False
            for e in (e1, e2):
                if e:
                    errors.append("R4: line %d: %s" % (i, e))
                    bad = True
            if not WORD_RE.match(w):
                errors.append("R11: line %d: word %r not 1..32 lowercase letters/digits" % (i, w))
            if not bad and f > to:
                errors.append("R11: line %d: <from> later than <to>" % i)
            if bad:
                continue
            n = sum(1 for x in index if f <= x[0] <= to and w in x[3])
            out.append("%d %d" % (nquery, n))
        else:  # lag
            state = "query"
            nquery += 1
            if nquery == 41:
                errors.append("R15: more than 40 queries (line %d)" % i)
            parts = ln.split(" ")
            if len(parts) != 3:
                errors.append("R3: line %d: malformed lag line %r" % (i, ln))
                continue
            q, e = parse_instant(parts[1])
            w = parts[2]
            if e:
                errors.append("R4: line %d: %s" % (i, e))
            if not WORD_RE.match(w):
                errors.append("R11/R13: line %d: word %r not 1..32 lowercase letters/digits" % (i, w))
            if e:
                continue
            hits = sorted((x for x in index if w in x[3]), key=lambda x: (x[0], x[1]))
            if not hits:
                out.append("%d -" % nquery)
            for x in hits:
                out.append("%d %s %d %d" % (nquery, fmt_instant(x[0]), x[2], q - x[0]))
    return out, errors

def answer(journal: str) -> str:
    out, _ = replay(journal)
    return "".join(l + "\n" for l in out)

def violations(journal: str) -> list:
    _, errs = replay(journal)
    return errs

if __name__ == "__main__":
    data = sys.stdin.buffer.read().decode("latin-1")
    if "--check" in sys.argv[1:]:
        for v in violations(data):
            print(v)
    else:
        sys.stdout.write(answer(data))
