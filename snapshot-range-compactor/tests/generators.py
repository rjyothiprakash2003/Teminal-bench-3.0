"""Deterministic unseen compactions and an analytically derived scale case."""
import random
from copy import deepcopy
from cases import point, tomb, fragment, request


def randomized(seed):
    rng = random.Random(seed)
    keys = sorted(set(['00', '0000', '00ff', '10', '1000', 'fe', 'ff'] +
                      [rng.randbytes(rng.randint(1, 32)).hex() for _ in range(10)]))
    latest = (2**64 - 1) if seed % 5 == 0 else 101
    seqs = list(range(1, 41))
    if latest > 101:
        seqs += [2**53 + 1, 2**53 + 2, latest - 2, latest - 1, latest]
    ps, rs, lower = [], [], []
    for key in rng.sample(keys, rng.randint(0, len(keys))):
        for seq in rng.sample(seqs, rng.randint(1, 8)):
            ps.append(point(key, seq, rng.choice(['put', 'put', 'merge', 'merge', 'delete']), rng.randint(-500, 500)))
    for _ in range(rng.randint(0, 16)):
        a, b = sorted(rng.sample(keys, 2))
        rs.append(tomb(a, b, rng.choice(seqs)))
    for _ in range(rng.randint(0, 5)):
        lower.append(sorted(rng.sample(keys, 2)))
    cuts = rng.choices([0] + seqs, k=rng.randint(0, 8))
    result = request(cuts=cuts, lower=lower, latest=latest)
    if latest and rng.randrange(3) == 0:
        result['cutoff'] = rng.choice(seqs)
    if latest and rng.randrange(3) == 0:
        result['release'] = rng.choice(seqs)
    if latest and rng.randrange(3) == 0:
        result['lift'] = rng.choice(seqs)
    result['tables'] = [dict(points=[], ranges=[]) for _ in range(rng.randint(1, 5))]
    for row in ps:
        rng.choice(result['tables'])['points'].append(row)
        if rng.randrange(6) == 0:
            rng.choice(result['tables'])['points'].append(deepcopy(row))
    for row in rs:
        rng.choice(result['tables'])['ranges'].append(row)
        if rng.randrange(5) == 0:
            rng.choice(result['tables'])['ranges'].append(deepcopy(row))
    return result


def reshuffle(req, seed):
    rng = random.Random(seed)
    req = deepcopy(req)
    rng.shuffle(req['snapshots'])
    rng.shuffle(req['lower'])
    rng.shuffle(req['tables'])
    for table in req['tables']:
        rng.shuffle(table['points'])
        rng.shuffle(table['ranges'])
    return req


def scale_case(n=6000, key_bytes=4):
    ps, rs, fragments = [], [], []
    for i in range(n):
        key, end = (2*i+1).to_bytes(key_bytes, 'big').hex(), (2*i+2).to_bytes(key_bytes, 'big').hex()
        # A pinned cut exists at every record's own sequence. Consequently every
        # point is live; all merge stripes differ; no positive delete is elidable.
        for seq in range(100, 0, -10):
            kind = ['put', 'merge', 'delete', 'merge'][seq//10 % 4]
            ps.append(point(key, seq, kind, i-seq))
        rs.extend([tomb(key, end, 25), tomb(key, end, 75)])
        fragments.append(fragment(key, end, 75, 25))
    req = request(ps, rs, cuts=list(range(127)), latest=127,
                  lower=[((4*i+1).to_bytes(key_bytes, 'big').hex(), (4*i+4).to_bytes(key_bytes, 'big').hex()) for i in range(min(4000, n))])
    # Partition into 128 tables and reverse each stream, without changing truth.
    req['tables'] = [dict(points=list(reversed(ps[i::128])), ranges=list(reversed(rs[i::128]))) for i in range(128)]
    return req, dict(points=ps, ranges=fragments)


def width_case(width):
    prefix = (bytes([0, 255, 128, 1]) * 8)[:width-1]
    start, middle, end = [(prefix + bytes([suffix])).hex() for suffix in (16, 32, 48)]
    ps = [point(start, 3, value=4), point(middle, 3, value=6), point(end, 3, value=8),
          point(middle, 9, 'merge', -6)]
    kept = [point(middle, 9, 'merge', -6), point(end, 3, value=8)]
    if prefix:
        ps.append(point(prefix.hex(), 2, value=12))
        kept.insert(0, point(prefix.hex(), 2, value=12))
    return (request(ps, [tomb(start, end, 7)], lower=[(middle, end)]),
            dict(points=kept, ranges=[fragment(middle, end, 7)]))


def nested_case(n=12000, distinct_sequences=False):
    """Analytic nested geometry: the active sequences are a prefix at every cell."""
    def key(i):
        return i.to_bytes(4, 'big').hex()
    latest = max(127, n) if distinct_sequences else 127
    cuts = sorted({0, latest, *(i * latest // 127 for i in range(1, 127))})
    spans = [tomb(key(i), key(2*n-i), i+1 if distinct_sequences else 1) for i in range(n)]
    req = request(ranges=spans, cuts=cuts[:-1], latest=latest)
    if not distinct_sequences:
        return req, dict(points=[], ranges=[fragment(key(0), key(2*n), 1)])
    # All active identities at a cell have consecutive sequences 1..depth.
    # That gives max-visible=min(cut,depth) directly, without replaying ranges.
    bounds = list(range(n)) + list(range(n+1, 2*n+1))
    fragments = []
    for start, end in zip(bounds, bounds[1:]):
        depth = min(start+1, 2*n-start)
        stack = sorted({min(cut, depth) for cut in cuts if cut}, reverse=True)
        if fragments and fragments[-1]['seqs'] == stack:
            fragments[-1]['end'] = key(end)
        else:
            fragments.append(fragment(key(start), key(end), *stack))
    return req, dict(points=[], ranges=fragments)


def combined_case(point_count=60000, range_count=12000):
    """Maximum points inside the innermost cell, with analytically known liveness."""
    req, expected = nested_case(range_count, distinct_sequences=True)
    # Raising only the latest cut by one changes no selected range sequence.
    # A point at the new latest is newer than every tombstone; a seq-1 point is
    # hidden by a covering range at every cut at which that point is visible.
    req['latest'] += 1
    ranges = req['tables'][0]['ranges']
    points, kept = [], []
    prefix = range_count.to_bytes(4, 'big')
    for i in range(point_count):
        key = (prefix + i.to_bytes(28, 'big')).hex()
        kind = ['put', 'merge', 'put', 'delete'][i % 4]
        record = point(key, 1 if i % 4 == 2 else req['latest'], kind, i - point_count)
        points.append(record)
        if i % 4 != 2:
            kept.append(record)
    req['lower'] = [dict(start=i.to_bytes(4, 'big').hex(),
                         end=(2*range_count-i).to_bytes(4, 'big').hex())
                    for i in range(min(4000, range_count))]
    req['tables'] = [dict(points=list(reversed(points[i::128])),
                          ranges=list(reversed(ranges[i::128]))) for i in range(128)]
    expected['points'] = kept
    return req, expected


def many_points_duplicate_snapshots(n=60000, duplicates=65536):
    """Many distinct keys plus a raw snapshot array that must be deduplicated first."""
    points = [point(i.to_bytes(4, 'big').hex(), 9, value=1) for i in range(n)]
    req = request(points, latest=20)
    req['snapshots'] = [0] * duplicates + [5]
    return req, dict(points=list(points), ranges=[])


def cutoff_point_envelope(n=60000):
    """Every point key sits in the collapse window at the documented point maximum."""
    points = [point(i.to_bytes(4, 'big').hex(), 1, value=1) for i in range(n)]
    return request(points, latest=1, cutoff=1), dict(points=list(points), ranges=[])


def cutoff_covering_range_envelope(n_points=60000, n_ranges=12000, n_lower=4000):
    """Every collapse-window key is covered by the maximum range count."""
    newest_range = n_ranges + 1
    latest = newest_range + 1
    points = [point(i.to_bytes(4, 'big').hex(), 1, value=0) for i in range(n_points)]
    ranges = [tomb('00000000', '00010000', seq) for seq in range(2, newest_range + 1)]
    lower = [('00000000', '00010000')]
    for i in range(1, n_lower):
        lower.append(((0x20000 + i).to_bytes(4, 'big').hex(), (0x20001 + i).to_bytes(4, 'big').hex()))
    req = request(points, ranges, lower=lower, latest=latest, cutoff=latest)
    expected = dict(
        points=[point(row['key'], latest, 'delete') for row in points],
        ranges=[fragment('00000000', '00010000', newest_range)],
    )
    return req, expected


def lift_point_range_envelope(n_points=60000, n_ranges=12000):
    """Positive lift over the maximum point and range counts must stay sub-quadratic."""
    end = '00020000'
    prefix = bytes([0, 1, 0, 0])
    points = [point((prefix + i.to_bytes(28, 'big')).hex(), 1, value=1) for i in range(n_points)]
    ranges = [tomb(i.to_bytes(4, 'big').hex(), end, 1) for i in range(n_ranges)]
    req = request(points, ranges, cuts=[0], latest=2, lift=2)
    # Every range promotes to 2; all share end and stack [2], so one coalesced fragment.
    expected = dict(points=[], ranges=[fragment('00000000', end, 2)])
    return req, expected


def lift_duplicate_range_envelope(n_points=60000, n_dupes=12000):
    """Duplicate promotable ranges must be deduped before any point-win scan."""
    a = '00' * 32
    b = '00' * 31 + 'fe'
    k = '00' * 31 + 'ff'
    points = [point(k, seq, value=0) for seq in range(2, n_points + 2)]
    ranges = [tomb(a, b, 1) for _ in range(n_dupes)]
    latest = n_points + 1
    req = request(points, ranges, latest=latest, lift=1)
    # Sole cut is latest; range seq 1 <= oldest cut and outside lower, so elided.
    expected = dict(points=[point(k, latest, value=0)], ranges=[])
    return req, expected
