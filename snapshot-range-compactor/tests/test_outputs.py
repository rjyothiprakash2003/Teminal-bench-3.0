"""Compile the submitted Go source; grade canonical records and read semantics."""
import itertools
import json
import os
from pathlib import Path
import resource
import shutil
import signal
import subprocess
import time

import pytest

from cases import fixed_cases, point as make_point, request as make_request
from generators import (combined_case, cutoff_covering_range_envelope, cutoff_point_envelope,
                        lift_duplicate_range_envelope, lift_point_range_envelope,
                        many_points_duplicate_snapshots, nested_case, randomized, reshuffle,
                        scale_case, width_case)
from model import apply_cutoff, apply_lift, apply_release, canonical, read_at

DROP = ['/usr/bin/setpriv', '--reuid=2000', '--regid=2000', '--clear-groups', '--no-new-privs']
ENV = dict(PATH='/usr/local/go/bin:/usr/bin:/bin', HOME='/work', GOCACHE='/work/go-cache',
           GOPATH='/work/go', GOPROXY='off', GOSUMDB='off', GOTOOLCHAIN='local', CGO_ENABLED='0',
           GOMAXPROCS='2', GOMEMLIMIT='1800MiB', LANG='C.UTF-8')
FIXED = fixed_cases()
MEMORY_LIMIT_BYTES = 2 * 1024**3
RUNTIME = ['/usr/local/bin/run-compact']
RUNTIME_ENV = dict(PATH='/nonexistent', HOME='/tmp', TMPDIR='/tmp',
                   GOMAXPROCS='2', GOMEMLIMIT='1800MiB', LANG='C.UTF-8')


def limits():
    # Contract bounds wall time (20s on two cores) and RSS, not aggregate CPU.
    # A hard RLIMIT_CPU of 24s false-rejects two busy workers that finish under
    # the elapsed envelope while consuming more than 24 process-CPU seconds.
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def wait_measured(proc, started):
    """Reap this invocation with its kernel high-water RSS, including brief peaks."""
    timed_out = False
    try:
        while True:
            pid, status, usage = os.wait4(proc.pid, os.WNOHANG)
            if pid:
                break
            remaining = 20 - (time.monotonic() - started)
            if remaining <= 0:
                timed_out = True
                os.killpg(proc.pid, signal.SIGKILL)
                _, status, usage = os.wait4(proc.pid, 0)
                break
            time.sleep(min(0.002, remaining))
        # Popen must not reap the child again: wait4 supplies both its status and
        # its per-invocation resource usage, independent of the compiler/pytest.
        proc.returncode = os.waitstatus_to_exitcode(status)
        return time.monotonic() - started, int(usage.ru_maxrss) * 1024, timed_out
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        if proc.returncode is None:
            _, status, _ = os.wait4(proc.pid, 0)
            proc.returncode = os.waitstatus_to_exitcode(status)


def check_dependencies():
    """Inspect Go's resolved dependency graph before building candidate code."""
    listed = subprocess.run(DROP + ['/usr/local/go/bin/go', 'list', '-deps', '-json', '.'],
                            cwd='/app/src', env=ENV, capture_output=True, timeout=120)
    assert listed.returncode == 0, listed.stderr.decode(errors='replace')[-6000:]
    decoder = json.JSONDecoder()
    remaining = listed.stdout.decode()
    packages = []
    while remaining.strip():
        package, end = decoder.raw_decode(remaining.lstrip())
        packages.append(package)
        remaining = remaining.lstrip()[end:]
    assert packages, 'Go must resolve a nonempty dependency graph'
    root = Path('/app/src').resolve()
    for package in packages:
        if package.get('Standard'):
            continue
        module = package.get('Module') or {}
        assert module.get('Main') is True, 'external Go dependency: ' + package['ImportPath']
        assert Path(module.get('GoMod', '')).resolve() == root / 'go.mod', 'dependency belongs to another Go module'
        directory = Path(package['Dir']).resolve()
        assert directory.is_relative_to(root), 'package source is outside /app/src'
        assert 'vendor' not in directory.relative_to(root).parts, 'vendored dependency is not allowed'


@pytest.fixture(scope='session')
def executable():
    """The tool is rebuilt offline from artifact source under an unprivileged uid."""
    probe = subprocess.run(DROP + ['/usr/bin/python3', '-I', '-c',
        'from pathlib import Path; import os; '
        'assert os.getuid()==2000, "uid"; '
        'assert not os.access("/tests/model.py",os.R_OK), "model"; '
        'assert not os.access("/tests/cases.py",os.R_OK), "cases"; '
        'assert not os.access("/logs/verifier",os.W_OK), "logs"; '
        'assert not os.access("/proc/1/mem",os.R_OK), "parent memory"'], capture_output=True, timeout=10)
    assert probe.returncode == 0, probe.stderr.decode()
    check_dependencies()
    compiled = subprocess.run(DROP + ['/usr/local/go/bin/go', 'build', '-o', '/build/compact', '.'],
                              cwd='/app/src', env=ENV, capture_output=True, timeout=120)
    assert compiled.returncode == 0, compiled.stderr.decode(errors='replace')[-6000:]
    assert Path('/build/compact').is_file() and not Path('/build/compact').is_symlink()
    os.chown('/build/compact', 0, 0)
    os.chmod('/build/compact', 0o555)
    os.chown('/build', 0, 0)
    os.chmod('/build', 0o755)
    # Only the executable crosses the runtime boundary. In particular, neither
    # Python from the verifier image nor submitted scripts under /app/src do.
    shutil.copyfile('/build/compact', '/runtime/compact')
    os.chown('/runtime/compact', 0, 0)
    os.chmod('/runtime/compact', 0o555)
    assert {p.name for p in Path('/runtime').iterdir()} == {'compact', 'tmp'}
    return '/compact'


def unique_members(pairs):
    """Build a decoded object only if no member name repeats; dict() would keep the last."""
    names = [name for name, _ in pairs]
    assert len(names) == len(set(names)), 'output object repeats a member name: ' + repr(names)
    return dict(pairs)


def run(executable, request, input_bytes=None):
    assert executable == '/compact'
    # Expected records never enter the child's argv, environment, filesystem or
    # stdin. File capture avoids pipe backpressure for large nested-range outputs.
    payload = input_bytes if input_bytes is not None else json.dumps(request, separators=(',', ':')).encode()
    assert len(payload) <= 24 * 1024 * 1024, 'verifier input exceeds the documented envelope'
    # Each invocation starts with empty scratch space; its program cannot modify
    # the root-owned runtime executable or see the verifier/build filesystem.
    shutil.rmtree('/runtime/tmp')
    Path('/runtime/tmp').mkdir(mode=0o700)
    os.chown('/runtime/tmp', 2000, 2000)
    # An input file lets the parent wait4 the exact child without a competing
    # communicate()/waitpid() call. The child receives only this request on fd 0.
    with (open('/capture/input.json', 'w+b') as input_file,
          open('/capture/stdout.json', 'w+b') as output,
          open('/capture/stderr.txt', 'w+b') as error):
        input_file.write(payload)
        input_file.seek(0)
        started = time.monotonic()
        proc = subprocess.Popen(RUNTIME, stdin=input_file, stdout=output, stderr=error,
                                cwd='/runtime', env=RUNTIME_ENV, start_new_session=True, preexec_fn=limits)
        elapsed, peak_rss, timed_out = wait_measured(proc, started)
        # Linux wait4 reports ru_maxrss in KiB. This is an exact high-water mark,
        # not a sampled RSS value or the soft GOMEMLIMIT garbage-collection goal.
        # Some hosted runtimes expose a host cgroup, so its size is not a verdict.
        with open('/logs/verifier/resource-usage.jsonl', 'a') as metrics:
            metrics.write(json.dumps(dict(elapsed_seconds=elapsed, peak_rss_bytes=peak_rss,
                                          memory_limit_bytes=MEMORY_LIMIT_BYTES,
                                          returncode=proc.returncode, timed_out=timed_out)) + '\n')
        assert not timed_out and elapsed < 20, 'compaction exceeded the documented 20-second limit'
        assert peak_rss > 0, 'kernel peak-memory measurement is unavailable'
        assert peak_rss <= MEMORY_LIMIT_BYTES, f'compaction exceeded the documented 2 GiB memory limit: {peak_rss} bytes'
        error.seek(0)
        assert proc.returncode == 0, error.read(6000).decode(errors='replace')
        output.seek(0)
        raw = output.read()
    assert raw.startswith(b'{'), 'stdout must start with the JSON object'
    text = raw.decode('utf-8')
    # Every member occurrence is checked before the schema: the result has exactly
    # two members, and no point or fragment object may repeat a name either.
    got, end = json.JSONDecoder(object_pairs_hook=unique_members).raw_decode(text)
    assert text[end:] == '\n', 'JSON object must be followed by exactly one LF and nothing else'
    assert isinstance(got, dict) and set(got) == {'points', 'ranges'}
    for point in got['points']:
        assert set(point) == ({'key', 'seq', 'kind'} if point.get('kind') == 'delete' else {'key', 'seq', 'kind', 'value'})
        assert type(point['seq']) is int
        if 'value' in point:
            assert type(point['value']) is int
    for fragment in got['ranges']:
        assert set(fragment) == {'start', 'end', 'seqs'}
        assert all(type(seq) is int for seq in fragment['seqs'])
    return got, elapsed


@pytest.mark.parametrize('name,compaction,expected', FIXED, ids=[row[0] for row in FIXED])
def test_isolating_contract_cases(executable, name, compaction, expected):
    """Each named rule has a small hand-worked case, independent of the generator."""
    assert canonical(compaction) == expected, 'the independent model must agree with the hand-worked example'
    got, _ = run(executable, compaction)
    assert got == expected, name


@pytest.mark.parametrize('seed', range(96))
def test_interacting_histories(executable, seed):
    """Snapshot, interval, duplicate, and merge interactions match a literal reader model."""
    request = randomized(84000+seed)
    expected = canonical(request)
    got, _ = run(executable, request)
    assert got == expected
    # Separate semantic check: several possible opaque lower-file assignments,
    # absence versus zero, range boundaries, and keys with no local points.
    # Reads are judged against the post-cutoff, post-release, post-lift history,
    # matching the contract's survival obligation.
    reduced = apply_lift(apply_release(apply_cutoff(request)))
    keys = {p['key'] for t in reduced['tables'] for p in t['points']}
    keys.update(k for t in reduced['tables'] for r in t['ranges'] for k in (r['start'], r['end'], r['start']+'00'))
    for key in keys:
        covered = any(s['start'] <= key < s['end'] for s in reduced['lower'])
        for base in ([None, 0, -137, 251] if covered else [None]):
            for cut in set(reduced['snapshots'] + [reduced['latest']]):
                assert read_at(reduced, key, cut, base) == read_at(got, key, cut, base)


@pytest.mark.parametrize('seed', range(12))
def test_table_order_and_determinism(executable, seed):
    """Input shuffling and repeated invocations cannot change canonical output."""
    request = randomized(90000+seed)
    a, _ = run(executable, request)
    b, _ = run(executable, reshuffle(request, seed))
    c, _ = run(executable, request)
    assert a == b == c == canonical(request)


def test_operating_envelope(executable):
    """Maximum point/range counts and 128 cuts finish without a Cartesian range scan."""
    # Validate the analytic construction on a small version first.
    small, expected_small = scale_case(4)
    assert canonical(small) == expected_small
    request, expected = scale_case()
    got, elapsed = run(executable, request)
    assert got == expected
    assert elapsed < 20


@pytest.mark.parametrize('distinct_sequences', [False, True], ids=['same-sequence', 'distinct-sequences'])
def test_nested_range_envelope(executable, distinct_sequences):
    """Twelve thousand nested ranges and 128 cuts must not scan every active record per cut."""
    small, expected_small = nested_case(9, distinct_sequences)
    assert canonical(small) == expected_small
    request, expected = nested_case(12000, distinct_sequences)
    got, elapsed = run(executable, request)
    assert got == expected
    assert elapsed < 20


def test_combined_point_range_envelope(executable):
    """All point keys sit under all 12,000 ranges at the maximum record envelope."""
    small, expected_small = combined_case(12, 9)
    assert canonical(small) == expected_small
    request, expected = combined_case()
    assert sum(len(t['points']) for t in request['tables']) == 60000
    assert sum(len(t['ranges']) for t in request['tables']) == 12000
    assert len(request['lower']) == 4000 and len(request['tables']) == 128
    assert len(set(request['snapshots'] + [request['latest']])) == 128
    got, _ = run(executable, request)
    assert got == expected


def test_point_envelope_with_duplicate_snapshots(executable):
    """Sixty thousand keys plus a 65,536-duplicate snapshot array must use the cut set."""
    small, expected_small = many_points_duplicate_snapshots(8, 32)
    assert canonical(small) == expected_small
    request, expected = many_points_duplicate_snapshots()
    assert sum(len(t['points']) for t in request['tables']) == 60000
    assert request['snapshots'][:3] == [0, 0, 0] and request['snapshots'][-1] == 5
    got, elapsed = run(executable, request)
    assert got == expected
    assert elapsed < 20


def test_cutoff_point_envelope(executable):
    """A positive cutoff over 60,000 distinct keys must not rescan the whole table per key."""
    small, expected_small = cutoff_point_envelope(8)
    assert canonical(small) == expected_small
    request, expected = cutoff_point_envelope()
    assert sum(len(t['points']) for t in request['tables']) == 60000
    assert request['cutoff'] == 1
    got, elapsed = run(executable, request)
    assert got == expected
    assert elapsed < 20


def test_cutoff_covering_range_envelope(executable):
    """Cutoff must not rebuild the full covering-range history once per window key."""
    small, expected_small = cutoff_covering_range_envelope(8, 4, 3)
    assert canonical(small) == expected_small
    request, expected = cutoff_covering_range_envelope()
    assert sum(len(t['points']) for t in request['tables']) == 60000
    assert sum(len(t['ranges']) for t in request['tables']) == 12000
    assert len(request['lower']) == 4000
    assert request['cutoff'] == request['latest'] == 12002
    got, elapsed = run(executable, request)
    assert got == expected
    assert elapsed < 20


def test_lift_point_range_envelope(executable):
    """Positive lift at the point/range maxima must not scan every point per range."""
    small, expected_small = lift_point_range_envelope(8, 4)
    assert canonical(small) == expected_small
    request, expected = lift_point_range_envelope()
    assert sum(len(t['points']) for t in request['tables']) == 60000
    assert sum(len(t['ranges']) for t in request['tables']) == 12000
    assert request['lift'] == 2
    got, elapsed = run(executable, request)
    assert got == expected
    assert elapsed < 20


def test_lift_duplicate_range_envelope(executable):
    """Duplicate promotable ranges must be collapsed before the point-win check."""
    small, expected_small = lift_duplicate_range_envelope(8, 4)
    assert canonical(small) == expected_small
    request, expected = lift_duplicate_range_envelope()
    assert sum(len(t['points']) for t in request['tables']) == 60000
    assert sum(len(t['ranges']) for t in request['tables']) == 12000
    assert request['lift'] == 1
    got, elapsed = run(executable, request)
    assert got == expected
    assert elapsed < 20


@pytest.mark.parametrize('duplicates', [3000, 65536, None], ids=['3000', '65536', '24-mib'])
def test_long_snapshot_arrays(executable, duplicates):
    """Raw snapshot count is bounded only by JSON bytes, with a decisive final cut."""
    points = [make_point('61', 9, value=90), make_point('61', 5, value=50)]
    request = make_request(points, cuts=[5])
    size = 24 * 1024 * 1024
    if duplicates is None:
        base_size = len(json.dumps(request, separators=(',', ':')).encode())
        duplicates = (size - base_size) // 2  # each inserted '0,' takes two bytes
    request['snapshots'] = [0] * duplicates + [5]
    payload = json.dumps(request, separators=(',', ':')).encode()
    if duplicates > 65536:
        payload = payload[:-1] + b' ' * (size - len(payload)) + payload[-1:]
        assert len(payload) == size
    expected = dict(points=points, ranges=[])
    assert canonical(request) == expected
    got, _ = run(executable, request, payload)
    assert got == expected


@pytest.mark.parametrize('width', range(1, 33))
def test_key_and_endpoint_widths(executable, width):
    """Every legal byte width preserves suffix distinctions, prefixes, and interval bounds."""
    request, expected = width_case(width)
    assert canonical(request) == expected
    got, _ = run(executable, request)
    assert got == expected


def reorder_members(value):
    if isinstance(value, dict):
        return {key: reorder_members(value[key]) for key in reversed(value)}
    if isinstance(value, list):
        return [reorder_members(item) for item in value]
    return value


def reorder_kind(value, kind_keys, key_order):
    """Rebuild every object of one shape so its members follow key_order."""
    if isinstance(value, list):
        return [reorder_kind(item, kind_keys, key_order) for item in value]
    if isinstance(value, dict):
        nested = {key: reorder_kind(value[key], kind_keys, key_order) for key in value}
        if set(nested) == set(kind_keys):
            return {key: nested[key] for key in key_order}
        return nested
    return value


def fixed_case(name):
    return next(row for row in FIXED if row[0] == name)


def assert_same_request(text, request):
    assert json.loads(text) == request
    got_names = tuple(json.loads(text, object_pairs_hook=lambda pairs: [name for name, _ in pairs]))
    return got_names


@pytest.mark.parametrize('layout', ['indented', 'tabs-crlf', 'reordered', 'reordered-pretty', 'edge-whitespace', 'escaped-keys'])
def test_request_json_representations(executable, layout):
    """Whitespace, nested member order, and equivalent string escapes preserve request meaning."""
    request = next(req for name, req, _ in FIXED if name == 'documented-regression')
    if layout == 'indented':
        text = json.dumps(request, indent=2)
    elif layout == 'tabs-crlf':
        text = json.dumps(request, indent='\t').replace('\n', '\r\n')
    elif layout == 'reordered':
        text = json.dumps(reorder_members(request), separators=(',', ':'))
    elif layout == 'reordered-pretty':
        text = json.dumps(reorder_members(request), indent=3)
    elif layout == 'edge-whitespace':
        text = '\r\n\t ' + json.dumps(reorder_members(request), indent=1) + ' \t\r\n'
    else:
        text = json.dumps(request).replace('6374722f61', '\\u0036374722f61')
    assert json.loads(text) == request
    got, _ = run(executable, request, text.encode())
    assert got == canonical(request)


@pytest.mark.parametrize('size', [16 * 1024 * 1024 + 1, 24 * 1024 * 1024])
def test_json_byte_envelope(executable, size):
    """Legal JSON beyond 16 MiB and at exactly 24 MiB must reach the final object delimiter."""
    request, expected = scale_case(key_bytes=32)
    body = json.dumps(request, separators=(',', ':')).encode()
    assert len(body) < size
    # Padding is inside the object, so a smaller LimitReader cannot decode a
    # complete request and silently ignore a truncated whitespace suffix.
    payload = body[:-1] + b' ' * (size - len(body)) + body[-1:]
    assert len(payload) == size
    assert json.loads(payload) == request
    got, _ = run(executable, request, payload)
    assert got == expected


def run_reordered(executable, request, expected, kind_keys, key_order):
    ordered = reorder_kind(request, kind_keys, key_order)
    text = json.dumps(ordered, separators=(',', ':'))
    assert_same_request(text, request)
    got, _ = run(executable, request, text.encode())
    assert got == expected


@pytest.mark.parametrize(
    'root_order',
    list(itertools.permutations(('latest', 'snapshots', 'tables', 'lower'))),
    ids=lambda order: '-'.join(order),
)
def test_root_object_member_orders(executable, root_order):
    """Every top-level member order is legal, not only document order and full reversal."""
    _, request, expected = fixed_case('documented-regression')
    assert canonical(request) == expected
    ordered = reorder_kind(request, ('latest', 'snapshots', 'tables', 'lower'), root_order)
    assert tuple(ordered) == root_order
    text = json.dumps(ordered, separators=(',', ':'))
    assert tuple(assert_same_request(text, request)) == root_order
    got, _ = run(executable, request, text.encode())
    assert got == expected


@pytest.mark.parametrize(
    'root_order',
    list(itertools.permutations(('latest', 'snapshots', 'tables', 'lower', 'cutoff'))),
    ids=lambda order: '-'.join(order),
)
def test_root_object_member_orders_with_cutoff(executable, root_order):
    """Optional cutoff is legal in every root position, including first and non-final."""
    _, request, expected = fixed_case('cutoff-put-seq-rewritten')
    assert 'cutoff' in request and request['cutoff'] == 12
    assert canonical(request) == expected
    ordered = reorder_kind(request, ('latest', 'snapshots', 'tables', 'lower', 'cutoff'), root_order)
    assert tuple(ordered) == root_order
    text = json.dumps(ordered, separators=(',', ':'))
    assert tuple(assert_same_request(text, request)) == root_order
    got, _ = run(executable, request, text.encode())
    assert got == expected


@pytest.mark.parametrize(
    'root_order',
    list(itertools.permutations(('latest', 'snapshots', 'tables', 'lower', 'release'))),
    ids=lambda order: '-'.join(order),
)
def test_root_object_member_orders_with_release(executable, root_order):
    """Optional release is legal in every root position, including first and non-final."""
    _, request, expected = fixed_case('release-drops-barrier')
    assert 'release' in request and request['release'] == 10
    assert canonical(request) == expected
    ordered = reorder_kind(request, ('latest', 'snapshots', 'tables', 'lower', 'release'), root_order)
    assert tuple(ordered) == root_order
    text = json.dumps(ordered, separators=(',', ':'))
    assert tuple(assert_same_request(text, request)) == root_order
    got, _ = run(executable, request, text.encode())
    assert got == expected


@pytest.mark.parametrize(
    'root_order',
    list(itertools.permutations(('latest', 'snapshots', 'tables', 'lower', 'cutoff', 'release'))),
    ids=lambda order: '-'.join(order),
)
def test_root_object_member_orders_with_cutoff_and_release(executable, root_order):
    """When both optional members are present, every root order is legal."""
    _, request, expected = fixed_case('release-after-cutoff')
    assert 'cutoff' in request and 'release' in request
    assert canonical(request) == expected
    keys = ('latest', 'snapshots', 'tables', 'lower', 'cutoff', 'release')
    ordered = reorder_kind(request, keys, root_order)
    assert tuple(ordered) == root_order
    text = json.dumps(ordered, separators=(',', ':'))
    assert tuple(assert_same_request(text, request)) == root_order
    got, _ = run(executable, request, text.encode())
    assert got == expected


@pytest.mark.parametrize(
    'root_order',
    list(itertools.permutations(('latest', 'snapshots', 'tables', 'lower', 'lift'))),
    ids=lambda order: '-'.join(order),
)
def test_root_object_member_orders_with_lift(executable, root_order):
    """Optional lift is legal in every root position, including first and non-final."""
    _, request, expected = fixed_case('lift-promotes-free-range')
    assert 'lift' in request and request['lift'] == 12
    assert canonical(request) == expected
    ordered = reorder_kind(request, ('latest', 'snapshots', 'tables', 'lower', 'lift'), root_order)
    assert tuple(ordered) == root_order
    text = json.dumps(ordered, separators=(',', ':'))
    assert tuple(assert_same_request(text, request)) == root_order
    got, _ = run(executable, request, text.encode())
    assert got == expected


@pytest.mark.parametrize(
    'root_order',
    list(itertools.permutations(
        ('latest', 'snapshots', 'tables', 'lower', 'cutoff', 'release', 'lift'))),
    ids=lambda order: '-'.join(order),
)
def test_root_object_member_orders_with_cutoff_release_lift(executable, root_order):
    """When cutoff, release, and lift are all present, every root order is legal."""
    _, request, _ = fixed_case('lift-blocked-after-cutoff')
    request = dict(request)
    request['release'] = 0
    expected = canonical(request)
    keys = ('latest', 'snapshots', 'tables', 'lower', 'cutoff', 'release', 'lift')
    ordered = reorder_kind(request, keys, root_order)
    assert tuple(ordered) == root_order
    text = json.dumps(ordered, separators=(',', ':'))
    assert tuple(assert_same_request(text, request)) == root_order
    got, _ = run(executable, request, text.encode())
    assert got == expected


def test_panel_mixed_empty_root_order(executable):
    """The mixed order latest/tables/snapshots/lower is a complete empty request."""
    text = '{"latest":0,"tables":[],"snapshots":[],"lower":[]}'
    _, _, expected = fixed_case('latest-zero-no-tables')
    request = json.loads(text)
    assert tuple(assert_same_request(text, request)) == ('latest', 'tables', 'snapshots', 'lower')
    assert canonical(request) == expected == {'points': [], 'ranges': []}
    got, _ = run(executable, request, text.encode())
    assert got == expected


def test_standalone_carriage_return(executable):
    """A carriage return that is not half of a CRLF pair is legal JSON whitespace."""
    text = '{"latest":0,\r"tables":[],"snapshots":[],"lower":[]}'
    assert '\r' in text and '\r\n' not in text
    _, _, expected = fixed_case('latest-zero-no-tables')
    request = json.loads(text)
    assert canonical(request) == expected == {'points': [], 'ranges': []}
    got, _ = run(executable, request, text.encode())
    assert got == expected


def test_standalone_carriage_return_before_end(executable):
    """A carriage return before the closing brace is legal JSON whitespace."""
    _, request, expected = fixed_case('documented-regression')
    assert canonical(request) == expected
    compact = json.dumps(request, separators=(',', ':'))
    text = compact[:-1] + '\r' + compact[-1:]
    assert '\r' in text and '\r\n' not in text
    assert json.loads(text) == request
    got, _ = run(executable, request, text.encode())
    assert got == expected


@pytest.mark.parametrize(
    'name,gap',
    [
        ('root-tab', '\t'),
        ('root-space', ' '),
        ('nested-tab', '\t'),
        ('nested-cr', '\r'),
    ],
)
def test_whitespace_between_member_name_and_colon(executable, name, gap):
    """Whitespace between a member name and its colon is legal JSON whitespace."""
    if name.startswith('root'):
        _, _, expected = fixed_case('latest-zero-no-tables')
        text = '{"latest"%s:0,"snapshots":[],"tables":[],"lower":[]}' % gap
        request = json.loads(text)
        assert canonical(request) == expected == {'points': [], 'ranges': []}
    else:
        _, request, expected = fixed_case('documented-regression')
        assert canonical(request) == expected
        compact = json.dumps(request, separators=(',', ':'))
        text = compact.replace('"latest":', '"latest"%s:' % gap, 1)
        if name == 'nested-tab':
            text = text.replace('"seq":', '"seq"%s:' % gap, 1)
        assert json.loads(text) == request
    assert gap + ':' in text
    got, _ = run(executable, request, text.encode())
    assert got == expected


@pytest.mark.parametrize(
    'name,text',
    [
        ('snapshots-space', '{"latest":0,"snapshots":[ ],"tables":[],"lower":[]}'),
        ('snapshots-newline', '{"latest":0,"snapshots":[\n],"tables":[],"lower":[]}'),
        ('tables-tab', '{"latest":0,"snapshots":[],"tables":[\t],"lower":[]}'),
        ('lower-cr', '{"latest":0,"snapshots":[],"tables":[],"lower":[\r]}'),
        ('nested-points-space',
         '{"latest":0,"snapshots":[],"tables":[{"points":[ ],"ranges":[]}],"lower":[]}'),
        ('nested-ranges-newline',
         '{"latest":0,"snapshots":[],"tables":[{"points":[],"ranges":[\n]}],"lower":[]}'),
    ],
)
def test_whitespace_inside_empty_array(executable, name, text):
    """Whitespace between '[' and ']' of an empty array is legal JSON whitespace."""
    assert '[ ]' in text or '[\n]' in text or '[\t]' in text or '[\r]' in text
    _, _, expected = fixed_case('latest-zero-no-tables')
    request = json.loads(text)
    assert canonical(request) == expected == {'points': [], 'ranges': []}
    got, _ = run(executable, request, text.encode())
    assert got == expected


@pytest.mark.parametrize(
    'point_order',
    list(itertools.permutations(('key', 'seq', 'kind', 'value'))),
    ids=lambda order: '-'.join(order),
)
def test_point_object_member_orders(executable, point_order):
    """A point's member order is not part of its identity."""
    _, request, expected = fixed_case('documented-regression')
    assert canonical(request) == expected
    ordered = reorder_kind(request, ('key', 'seq', 'kind', 'value'), point_order)
    assert tuple(ordered['tables'][0]['points'][0]) == point_order
    run_reordered(executable, request, expected, ('key', 'seq', 'kind', 'value'), point_order)


@pytest.mark.parametrize(
    'range_order',
    list(itertools.permutations(('start', 'end', 'seq'))),
    ids=lambda order: '-'.join(order),
)
def test_range_object_member_orders(executable, range_order):
    """A range deletion's member order is not part of its identity."""
    _, request, expected = fixed_case('documented-regression')
    assert canonical(request) == expected
    ordered = reorder_kind(request, ('start', 'end', 'seq'), range_order)
    assert tuple(ordered['tables'][0]['ranges'][0]) == range_order
    run_reordered(executable, request, expected, ('start', 'end', 'seq'), range_order)


@pytest.mark.parametrize(
    'delete_order',
    list(itertools.permutations(('key', 'seq', 'kind'))),
    ids=lambda order: '-'.join(order),
)
def test_delete_object_member_orders(executable, delete_order):
    """A point deletion has no value member, and its three members may occur in any order."""
    _, request, expected = fixed_case('delete-stops-before-elision')
    assert canonical(request) == expected
    ordered = reorder_kind(request, ('key', 'seq', 'kind'), delete_order)
    delete = next(point for point in ordered['tables'][0]['points'] if point['kind'] == 'delete')
    assert tuple(delete) == delete_order
    run_reordered(executable, request, expected, ('key', 'seq', 'kind'), delete_order)


@pytest.mark.parametrize('name', ['lower-clips-range', 'old-range-needed-by-lower'])
def test_lower_span_member_order(executable, name):
    """A lower span may list end before start; the two names have no other permutation."""
    _, request, expected = fixed_case(name)
    assert canonical(request) == expected
    ordered = reorder_kind(request, ('start', 'end'), ('end', 'start'))
    assert tuple(ordered['lower'][0]) == ('end', 'start')
    run_reordered(executable, request, expected, ('start', 'end'), ('end', 'start'))
