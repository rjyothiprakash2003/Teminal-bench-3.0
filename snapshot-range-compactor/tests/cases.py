"""Named isolating examples with manually worked expected records."""
from copy import deepcopy


def point(key, seq, kind='put', value=1):
    result = dict(key=key, seq=seq, kind=kind)
    if kind != 'delete':
        result['value'] = value
    return result


def tomb(start, end, seq):
    return dict(start=start, end=end, seq=seq)


def fragment(start, end, *seqs):
    return dict(start=start, end=end, seqs=list(seqs))


def request(points=(), ranges=(), cuts=(), lower=(), latest=20, cutoff=0, emit_cutoff=False,
            release=0, emit_release=False, lift=0, emit_lift=False):
    result = dict(latest=latest, snapshots=list(cuts), tables=[dict(points=list(points), ranges=list(ranges))],
                  lower=[dict(start=a, end=b) for a, b in lower])
    if cutoff or emit_cutoff:
        result['cutoff'] = cutoff
    if release or emit_release:
        result['release'] = release
    if lift or emit_lift:
        result['lift'] = lift
    return result


def fixed_cases():
    cases = []

    def add(name, req, ps=(), rs=()):
        cases.append((name, req, dict(points=list(ps), ranges=list(rs))))

    add('empty', request())
    add('latest-zero-no-tables', dict(latest=0, snapshots=[], tables=[], lower=[]))
    add('latest-zero-empty-table', request(latest=0))
    add('latest-zero-duplicate-cuts', request(latest=0, cuts=[0] * 257))
    add('latest-zero-lower-coverage', request(latest=0, cuts=[0, 0],
                                           lower=[('00', '80'), ('61', 'ff')]))
    ps = [point('61', 9, value=90), point('61', 5, value=50), point('61', 2, value=20)]
    add('snapshot-version', request(ps, cuts=[5]), ps[:2])
    add('cut-inclusive', request(ps, cuts=[2, 5, 9]), ps)
    add('latest-implicit', request(ps, cuts=[2]), [ps[0], ps[2]])
    add('no-snapshots', request(ps), ps[:1])
    add('snapshot-zero', request(ps, cuts=[0]), ps[:1])
    add('cuts-set', request(ps, cuts=[5, 2, 5, 2, 20]), ps)
    m = [point('61', 9, 'merge', 6), point('61', 7, 'merge', -2), point('61', 3, value=10)]
    add('merge-fold', request(m), [point('61', 9, 'merge', 4), m[2]])
    add('merge-stripes', request(m, cuts=[7]), m)
    z = [point('61', 9, 'merge', 4), point('61', 7, 'merge', -4)]
    add('merge-zero', request(z), [point('61', 9, 'merge', 0)])
    add('opaque-base', request(m[:2], lower=[('60', '70')]), [point('61', 9, 'merge', 4)])
    d = [point('61', 9, 'merge', 6), point('61', 7, 'delete'), point('61', 3, value=99)]
    add('delete-stops-before-elision', request(d), [d[0]])
    add('delete-needed-by-snapshot', request(d, cuts=[3]), d)
    add('delete-needed-by-lower', request(d, lower=[('60', '70')]), d[:2])
    add('delete-floor-inclusive', request(d, cuts=[7]), d[:1])
    add('zero-put', request([point('61', 4, value=0)]), [point('61', 4, value=0)])
    r = tomb('61', '63', 7)
    add('range-start-inclusive', request([point('61', 3)], [r]))
    add('range-end-exclusive', request([point('63', 3)], [r]), [point('63', 3)])
    add('range-tie-wins', request([point('61', 7)], [r]))
    add('future-range-invisible', request(ps, [tomb('61', '62', 8)], cuts=[5]), ps[:2], [fragment('61', '62', 8)])
    add('range-terminates-merge', request(m, [tomb('61', '62', 5)]), [point('61', 9, 'merge', 4)])
    add('range-stripe-barrier', request(m, [tomb('61', '62', 8)], cuts=[7]), m,
        [fragment('61', '62', 8)])
    add('old-range-needed-by-lower', request(ranges=[r], lower=[('61', '63')]), rs=[fragment('61', '63', 7)])
    add('old-range-elided', request(ranges=[r]))
    add('range-floor-inclusive', request(ranges=[r], cuts=[7]))
    add('range-floor-newer', request(ranges=[r], cuts=[6]), rs=[fragment('61', '63', 7)])
    add('isolated-delete-above-floor', request([point('85', 9, 'delete')], cuts=[5]),
        [point('85', 9, 'delete')])
    add('isolated-delete-above-zero-cut', request([point('88', 7, 'delete')], cuts=[0]),
        [point('88', 7, 'delete')])
    add('lone-delete-no-snap', request([point('86', 7, 'delete')]))
    add('lone-delete-seq-one', request([point('87', 1, 'delete')]))
    add('lone-delete-seq-one-above-zero-cut',
        request([point('89', 1, 'delete')], cuts=[0], latest=1),
        [point('89', 1, 'delete')])
    add('two-above-floor-in-lower', request(
        ranges=[tomb('8500', '8502', 7), tomb('8500', '8502', 9)],
        cuts=[5], lower=[('8500', '8502')]),
        rs=[fragment('8500', '8502', 9)])
    add('cutoff-zero-is-noop', request([point('91', 8, value=40)], cuts=[5]),
        [point('91', 8, value=40)])
    add('cutoff-explicit-zero', request([point('91', 8, value=40)], cuts=[5], emit_cutoff=True),
        [point('91', 8, value=40)])
    add('cutoff-explicit-zero-no-snap', request([point('91', 8, value=40)], emit_cutoff=True),
        [point('91', 8, value=40)])
    add('cutoff-put-seq-rewritten', request([point('91', 8, value=40)], cuts=[5], cutoff=12),
        [point('91', 12, value=40)])
    add('cutoff-keeps-below-floor', request(
        [point('91', 8, value=40), point('91', 3, value=7)], cuts=[5], cutoff=12),
        [point('91', 12, value=40), point('91', 3, value=7)])
    add('cutoff-leaves-above-window', request(
        [point('91', 18, 'merge', 2), point('91', 8, value=40)], cuts=[5], cutoff=12),
        [point('91', 18, 'merge', 2), point('91', 12, value=40)])
    add('cutoff-not-a-read-cut', request(
        [point('61', 18, value=2), point('61', 8, value=1)], cuts=[5], cutoff=12),
        [point('61', 18, value=2)])
    add('cutoff-equals-snapshot', request([point('91', 8, value=40)], cuts=[12], cutoff=12),
        [point('91', 12, value=40)])
    add('cutoff-merge-becomes-put', request(
        [point('91', 10, 'merge', 6), point('91', 8, 'merge', 4)], cuts=[5], cutoff=12),
        [point('91', 12, value=10)])
    add('cutoff-merge-open-over-lower', request(
        [point('91', 10, 'merge', 6), point('91', 8, 'merge', 4)],
        cuts=[5], cutoff=12, lower=[('90', '92')]),
        [point('91', 10, 'merge', 10)])
    add('cutoff-merge-open-over-lower-single', request(
        [point('92', 9, 'merge', 3)], cuts=[5], cutoff=12, lower=[('90', '93')]),
        [point('92', 9, 'merge', 3)])
    add('cutoff-merge-terminates-over-lower', request(
        [point('91', 10, 'merge', 6), point('91', 8, value=1)],
        cuts=[5], cutoff=12, lower=[('90', '92')]),
        [point('91', 12, value=7)])
    add('cutoff-delete-window', request(
        [point('91', 8, 'delete'), point('91', 3, value=7)], cuts=[5], cutoff=12),
        [point('91', 12, 'delete'), point('91', 3, value=7)])
    add('cutoff-materialized-delete-elided', request([point('91', 8, 'delete')], cutoff=20))
    add('cutoff-put-still-beats-older-range', request(
        [point('91', 8, value=40)], [tomb('90', '93', 6)], cuts=[5], cutoff=12),
        [point('91', 12, value=40)], [fragment('90', '93', 6)])
    add('cutoff-range-seq-unchanged', request(
        ranges=[tomb('9100', '9102', 7)], cuts=[5], cutoff=12),
        rs=[fragment('9100', '9102', 7)])
    add('release-zero-is-noop', request(
        [point('61', 9, 'merge', 6), point('61', 7, 'merge', 4)], [tomb('61', '62', 8)],
        emit_release=True),
        [point('61', 9, 'merge', 6)])
    add('release-drops-barrier', request(
        [point('61', 9, 'merge', 6), point('61', 7, 'merge', 4)], [tomb('61', '62', 8)],
        release=10),
        [point('61', 9, 'merge', 10)])
    add('release-keeps-over-lower', request(
        [point('61', 9, 'merge', 6), point('61', 7, 'merge', 4)], [tomb('61', '62', 8)],
        release=10, lower=[('61', '62')]),
        [point('61', 9, 'merge', 6)], [fragment('61', '62', 8)])
    add('release-keeps-at-or-below-floor', request(
        ranges=[tomb('61', '62', 3)], cuts=[5], release=10, lower=[('61', '62')]),
        rs=[fragment('61', '62', 3)])
    # Isolating: range at/below G with no lower. Must remain (and hide the put);
    # a G=0 release wrongly drops it and emits the put. Not confounded by lower.
    add('release-keeps-below-floor-no-lower', request(
        [point('61', 2, value=1)], [tomb('61', '62', 3)], cuts=[5], release=10))
    # Second site: different G/release, still lower-free and at/below floor.
    add('release-keeps-below-floor-no-lower-alt', request(
        [point('71', 4, value=1)], [tomb('71', '72', 6)], cuts=[8], release=15))
    add('release-ignores-above-window', request(
        ranges=[tomb('61', '62', 15)], cuts=[10], release=10),
        rs=[fragment('61', '62', 15)])
    add('release-after-cutoff', request(
        [point('61', 8, value=1), point('61', 18, 'merge', 2)], [tomb('61', '62', 10)],
        cuts=[5], cutoff=12, release=11),
        [point('61', 18, 'merge', 2), point('61', 12, 'delete')])
    add('release-partial-overlap-kept', request(
        ranges=[tomb('60', '70', 8)], release=10, lower=[('65', '66')]),
        rs=[fragment('65', '66', 8)])
    # Isolating: lower touches only the exclusive end. Half-open overlap is false;
    # release must drop the range. Inclusive-contact overlap wrongly retains it.
    add('release-endpoint-contact-not-overlap', request(
        ranges=[tomb('61', '62', 8)], cuts=[5], release=10, lower=[('62', '63')]))
    # Second site: contact at the range start / lower end (still half-open miss).
    add('release-endpoint-contact-start', request(
        ranges=[tomb('62', '63', 8)], cuts=[5], release=10, lower=[('61', '62')]))
    add('release-drops-retained-range', request(
        ranges=[tomb('61', '62', 8)], cuts=[5], release=10))
    # Isolating: two retained ranges straddle release. Cuts are {5,20}; winner
    # stack is [11]. Treating release as a cut wrongly also selects 7 → [11,7].
    add('release-not-a-read-cut', request(
        ranges=[tomb('61', '62', 7), tomb('61', '62', 11)], cuts=[5], release=10,
        lower=[('61', '62')]),
        rs=[fragment('61', '62', 11)])
    # Second site: different sequences straddling release under retained lower.
    add('release-not-a-read-cut-alt', request(
        ranges=[tomb('81', '82', 6), tomb('81', '82', 14)], cuts=[3], release=10,
        lower=[('81', '82')]),
        rs=[fragment('81', '82', 14)])
    add('lift-zero-is-noop', request(
        ranges=[tomb('61', '62', 8)], cuts=[5], emit_lift=True),
        rs=[fragment('61', '62', 8)])
    # Isolating: free range in (L, lift] promotes to lift; stack shows lift.
    add('lift-promotes-free-range', request(
        ranges=[tomb('61', '62', 8)], cuts=[5], lift=12),
        rs=[fragment('61', '62', 12)])
    # Point with seq between range.seq and lift blocks promotion (would invert).
    add('lift-blocked-by-point-win', request(
        [point('61', 10, value=1)], [tomb('61', '62', 8)], cuts=[5], lift=12),
        [point('61', 10, value=1)], [fragment('61', '62', 8)])
    # Below floor L stays put (lower retains the stack so the sequence is visible).
    add('lift-keeps-at-or-below-floor', request(
        ranges=[tomb('61', '62', 3)], cuts=[5], lift=12, lower=[('61', '62')]),
        rs=[fragment('61', '62', 3)])
    add('lift-ignores-above-window', request(
        ranges=[tomb('61', '62', 15)], cuts=[5], lift=12),
        rs=[fragment('61', '62', 15)])
    # After release drops a barrier range, lift still promotes a kept sibling.
    add('lift-after-release', request(
        [point('61', 9, 'merge', 6)], [tomb('61', '62', 8), tomb('70', '71', 8)],
        cuts=[5], release=10, lift=12, lower=[('70', '71')]),
        [point('61', 9, 'merge', 6)], [fragment('70', '71', 12)])
    # Cutoff materializes a put that then blocks lift of an older covering range.
    add('lift-blocked-after-cutoff', request(
        [point('61', 8, value=1)], [tomb('61', '62', 6)], cuts=[5], cutoff=10, lift=12),
        [point('61', 10, value=1)], [fragment('61', '62', 6)])
    # Second promote site with a different L/lift window.
    add('lift-promotes-alt-window', request(
        ranges=[tomb('81', '82', 7)], cuts=[4], lift=11),
        rs=[fragment('81', '82', 11)])
    add('lower-clips-range', request(ranges=[tomb('60', '70', 4)], lower=[('62', '64')]),
        rs=[fragment('62', '64', 4)])
    add('lower-union', request(ranges=[tomb('60', '70', 4)], lower=[('61', '64'), ('63', '66'), ('62', '65')]),
        rs=[fragment('61', '66', 4)])
    add('lower-point-boundaries', request([point('61', 5, 'delete'), point('63', 5, 'delete')], lower=[('61', '63')]),
        [point('61', 5, 'delete')])
    rs = [tomb('61', '65', 4), tomb('62', '66', 9), tomb('63', '64', 7)]
    add('snapshot-range-stack', request(ranges=rs, cuts=[5, 8], lower=[('60', '70')]), rs=[
        fragment('61', '62', 4), fragment('62', '63', 9, 4), fragment('63', '64', 9, 7, 4), fragment('64', '65', 9, 4), fragment('65', '66', 9)])
    add('range-nonwinner-pruned', request(ranges=rs, lower=[('60', '70')]),
        rs=[fragment('61', '62', 4), fragment('62', '66', 9)])
    add('adjacent-coalesce', request(ranges=[tomb('61', '63', 9), tomb('63', '65', 9)], cuts=[0]), rs=[fragment('61', '65', 9)])
    add('empty-gap-not-coalesced', request(ranges=[tomb('61', '62', 9), tomb('63', '64', 9)], cuts=[0]),
        rs=[fragment('61', '62', 9), fragment('63', '64', 9)])
    add('lower-boundary-coalesces', request(ranges=[tomb('61', '65', 9)], cuts=[0], lower=[('62', '64')]),
        rs=[fragment('61', '65', 9)])
    add('same-sequence-multiplicity', request(ranges=[tomb('61', '64', 9), tomb('62', '65', 9)], cuts=[0]), rs=[fragment('61', '65', 9)])
    b = [point(k, 4, value=i) for i, k in enumerate(['ff', '00ff', '0001', '00', 'ff00'])]
    add('binary-prefix-key-order', request(b), sorted(b, key=lambda p: p['key']))
    large = 2**64 - 1
    wide = [point('61', large, 'merge', -(2**62)), point('61', large - 1, 'merge', 2**62 - 1), point('61', 2**53 + 1, value=2**62)]
    add('signed-wide-merge-fold', request(wide, latest=large), [point('61', large, 'merge', -1), wide[2]])
    add('uint64-and-int64', request(wide, cuts=[large - 1], latest=large), wide)
    add('uint64-range-tie', request([point('61', large - 1)], [tomb('60', '62', large - 1)], cuts=[large - 2], latest=large),
        rs=[fragment('60', '62', large - 1)])
    duplicate = request(m, [tomb('61', '62', 5)], cuts=[3], lower=[('61', '62')])
    duplicate['tables'].append(deepcopy(duplicate['tables'][0]))
    add('cross-table-dedup', duplicate, [point('61', 9, 'merge', 4), m[2]], [fragment('61', '62', 5)])

    for kind in ['put', 'merge']:
        for label, value in [('min', -(2**63)), ('max', 2**63-1)]:
            p = point('61', 1, kind, value)
            add(f'int64-{label}-{kind}', request([p], latest=1), [p])
    for label, left, right in [('min', -(2**62), -(2**62)), ('max', 2**62, 2**62-1)]:
        operands = [point('61', 3, 'merge', left), point('61', 2, 'merge', right)]
        add(f'int64-{label}-fold', request(operands), [point('61', 3, 'merge', left+right)])

    add('snapshot-129-duplicates', request(cuts=[0]*129, latest=1))
    ps = [point('61', 5, value=50), point('61', 2, value=20)]
    add('snapshot-late-distinct-cut', request(ps, cuts=[0]*1025+[2], latest=5), ps)

    ps = [point('70', 9, 'delete')]
    add('lower-4000-last-span', request(ps, [tomb('61', '62', 7)],
        lower=[('00', '01')]*3999+[('60', '80')]), ps, [fragment('61', '62', 7)])
    distinct_lower = [((2 * i).to_bytes(2, 'big').hex(), (2 * i + 1).to_bytes(2, 'big').hex())
                      for i in range(4000)]
    add('lower-4000-distinct-last-span',
        request(ranges=[tomb(*distinct_lower[-1], 7)], lower=distinct_lower),
        rs=[fragment(*distinct_lower[-1], 7)])
    early_lower = [((2 * i).to_bytes(2, 'big').hex(), (2 * i + 1).to_bytes(2, 'big').hex())
                   for i in range(1025)]
    add('lower-1025-distinct-last-span',
        request(ranges=[tomb('0800', '0801', 7)], lower=early_lower),
        rs=[fragment('0800', '0801', 7)])

    # A verifier-owned copy of the exact input named in instruction.md. Only
    # the input is public; its expected output remains in this protected file.
    ps = [point('6374722f61', 29, 'merge', 17), point('6374722f61', 23, 'merge', -4),
          point('6374722f61', 11, value=31)]
    add('documented-regression', request(ps, [tomb('6374722f61', '6374722f63', 26)], cuts=[23], latest=40),
        ps, [fragment('6374722f61', '6374722f63', 26)])
    add('latest-cut-range-floor', request(ranges=[tomb('61', '62', 1)], latest=1))
    add('retained-range-lowercase-members', request(ranges=[tomb('61', '62', 1)], cuts=[0], latest=1),
        rs=[fragment('61', '62', 1)])
    add('lower-retained-range-lowercase-members', request(ranges=[tomb('61', '62', 1)], lower=[('61', '62')], latest=1),
        rs=[fragment('61', '62', 1)])
    return cases
