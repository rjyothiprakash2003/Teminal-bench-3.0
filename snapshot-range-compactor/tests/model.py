"""Small-input specification model; deliberately uses literal reader traces.

This is independent of the Go sweep: enumerate every cell, sort full per-key
streams, and record the source identities reached by each individual read.
"""
from copy import deepcopy
import bisect


def _in_lower(lower, key):
    return any(span['start'] <= key < span['end'] for span in lower)


def _events_at(request, key, cut):
    points = {(p['key'], p['seq']): p for t in request['tables'] for p in t['points']}
    ranges = {(r['start'], r['end'], r['seq']) for t in request['tables'] for r in t['ranges']}
    events = [(p['seq'], 1, p) for (k, _), p in points.items() if k == key and p['seq'] <= cut]
    events += [(s, 0, None) for a, b, s in ranges if a <= key < b and s <= cut]
    events.sort(key=lambda item: (-item[0], item[1]))
    return events


def _read_terminates(request, key, cut):
    for _, _, point in _events_at(request, key, cut):
        if point is None or point['kind'] != 'merge':
            return True
    return False


def apply_cutoff(request):
    """Collapse the (F, cutoff] window; cutoff 0 or absent is a no-op."""
    cutoff = request.get('cutoff', 0) or 0
    if cutoff == 0:
        return request
    cuts = sorted(set(request['snapshots'] + [request['latest']]))
    floor = max((cut for cut in cuts if cut < cutoff), default=0)
    by_key = {}
    ranges = []
    for table in request['tables']:
        for point in table['points']:
            by_key.setdefault(point['key'], []).append(point)
        ranges.extend(deepcopy(table['ranges']))
    kept_points = []
    for key, points in by_key.items():
        window, outside = [], []
        for point in points:
            (window if floor < point['seq'] <= cutoff else outside).append(deepcopy(point))
        kept_points.extend(outside)
        if not window:
            continue
        determined = _read_terminates(request, key, cutoff) or not _in_lower(request['lower'], key)
        if not determined:
            kept_points.extend(window)
            continue
        present, value = read_at(request, key, cutoff, None)
        if present:
            kept_points.append({'key': key, 'seq': cutoff, 'kind': 'put', 'value': value})
        else:
            kept_points.append({'key': key, 'seq': cutoff, 'kind': 'delete'})
    reduced = deepcopy(request)
    reduced['tables'] = [dict(points=kept_points, ranges=ranges)]
    return reduced


def _overlaps_lower(lower, start, end):
    return any(start < span['end'] and span['start'] < end for span in lower)


def apply_release(request):
    """Drop out-of-lower ranges in (G, release]; release 0 or absent is a no-op."""
    release = request.get('release', 0) or 0
    if release == 0:
        return request
    cuts = sorted(set(request['snapshots'] + [request['latest']]))
    floor = max((cut for cut in cuts if cut < release), default=0)
    kept_ranges = []
    points = []
    for table in request['tables']:
        points.extend(deepcopy(table['points']))
        for span in table['ranges']:
            if floor < span['seq'] <= release and not _overlaps_lower(request['lower'], span['start'], span['end']):
                continue
            kept_ranges.append(deepcopy(span))
    reduced = deepcopy(request)
    reduced['tables'] = [dict(points=points, ranges=kept_ranges)]
    return reduced


def apply_lift(request):
    """Promote out-of-window ranges to lift unless a point win would invert."""
    lift = request.get('lift', 0) or 0
    if lift == 0:
        return request
    cuts = sorted(set(request['snapshots'] + [request['latest']]))
    floor = max((cut for cut in cuts if cut < lift), default=0)
    points = []
    for table in request['tables']:
        points.extend(deepcopy(table['points']))
    unique = {}
    for table in request['tables']:
        for span in table['ranges']:
            key = (span['start'], span['end'], span['seq'])
            if key not in unique:
                unique[key] = dict(start=span['start'], end=span['end'], seq=span['seq'])
    ranges = list(unique.values())
    blockers = [p for p in points if p['seq'] <= lift]
    blockers.sort(key=lambda p: p['key'])
    max_blocker = max((p['seq'] for p in blockers), default=0)
    kept = []
    seen = set()
    for span in ranges:
        seq = span['seq']
        if floor < seq <= lift:
            blocked = False
            if max_blocker > seq:
                keys = [p['key'] for p in blockers]
                lo = bisect.bisect_left(keys, span['start'])
                hi = bisect.bisect_left(keys, span['end'])
                for p in blockers[lo:hi]:
                    if seq < p['seq'] <= lift:
                        blocked = True
                        break
            if not blocked:
                seq = lift
        key = (span['start'], span['end'], seq)
        if key in seen:
            continue
        seen.add(key)
        kept.append(dict(start=span['start'], end=span['end'], seq=seq))
    reduced = deepcopy(request)
    reduced['tables'] = [dict(points=points, ranges=kept)]
    return reduced


def canonical(request):
    request = apply_lift(apply_release(apply_cutoff(request)))
    cuts = sorted(set(request['snapshots'] + [request['latest']]))
    points = {}
    ranges = set()
    for table in request['tables']:
        for point in table['points']:
            points[(point['key'], point['seq'])] = deepcopy(point)
        for span in table['ranges']:
            ranges.add((span['start'], span['end'], span['seq']))
    lower = request['lower']
    output = {'points': [], 'ranges': []}
    for key in sorted({key for key, _ in points}):
        events = [(p['seq'], 1, p) for (k, _), p in points.items() if k == key]
        events += [(seq, 0, None) for start, end, seq in ranges if start <= key < end]
        events.sort(key=lambda item: (-item[0], item[1]))
        live = set()
        for cut in cuts:
            for seq, _, point in events:
                if seq > cut:
                    continue
                if point is None:
                    break
                live.add(seq)
                if point['kind'] != 'merge':
                    break
        run = []
        stripe = None

        def flush():
            if run:
                result = deepcopy(run[0])
                result['value'] = sum(p['value'] for p in run)
                output['points'].append(result)
                run.clear()

        for seq, _, point in events:
            if point is None or seq not in live:
                flush()
                continue
            current = next(cut for cut in cuts if cut >= seq)
            if point['kind'] == 'merge':
                if current != stripe:
                    flush()
                run.append(point)
                stripe = current
            else:
                flush()
                if not (point['kind'] == 'delete' and seq <= cuts[0]
                        and not any(s['start'] <= key < s['end'] for s in lower)):
                    output['points'].append(deepcopy(point))
        flush()
    bounds = {x for a, b, _ in ranges for x in (a, b)}
    bounds.update(x for span in lower for x in (span['start'], span['end']))
    bounds = sorted(bounds)
    for start, end in zip(bounds, bounds[1:]):
        covering = [seq for a, b, seq in ranges if a <= start < b]
        selected = {max((seq for seq in covering if seq <= cut), default=0) for cut in cuts}
        outside = not any(span['start'] <= start < span['end'] for span in lower)
        stack = sorted((seq for seq in selected if seq and not (outside and seq <= cuts[0])), reverse=True)
        if not stack:
            continue
        if output['ranges'] and output['ranges'][-1]['end'] == start and output['ranges'][-1]['seqs'] == stack:
            output['ranges'][-1]['end'] = end
        else:
            output['ranges'].append({'start': start, 'end': end, 'seqs': stack})
    return output


def read_at(request_or_result, key, cut, base):
    """Return (present, value), so present zero cannot masquerade as absence."""
    if 'tables' in request_or_result:
        tables = request_or_result['tables']
    else:
        expanded = [dict(start=r['start'], end=r['end'], seq=s)
                    for r in request_or_result['ranges'] for s in r['seqs']]
        tables = [dict(points=request_or_result['points'], ranges=expanded)]
    points = {(p['key'], p['seq']): p for t in tables for p in t['points']}
    ranges = {(r['start'], r['end'], r['seq']) for t in tables for r in t['ranges']}
    events = [(p['seq'], 1, p) for (k, _), p in points.items() if k == key and p['seq'] <= cut]
    events += [(s, 0, None) for a, b, s in ranges if a <= key < b and s <= cut]
    total, merged = 0, False
    for _, _, point in sorted(events, key=lambda item: (-item[0], item[1])):
        if point is None or point['kind'] == 'delete':
            return (True, total) if merged else (False, None)
        if point['kind'] == 'put':
            return True, total + point['value']
        merged = True
        total += point['value']
    if base is not None:
        return True, total + base
    return (True, total) if merged else (False, None)
