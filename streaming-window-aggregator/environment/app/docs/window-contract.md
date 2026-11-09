This page is the authority for the aggregator at `/app/src/aggregator.py`.
Diagnostics other than the refusal lines named here, concurrent writers, and
durability of the output path are outside this interface.

Drive. Run:

```
python3 /app/src/aggregator.py --stream <dir> --out <file>
```

`<dir>` is a stream directory. It must contain exactly two members named
`config.json` and `events.jsonl`. Neither member may be a directory or a
symbolic link. Any other member, or a missing required file, is a refusal:
write nothing to `--out`, print exactly `refuse: bad stream` followed by one LF
on stderr, and exit with status 2.

`config.json` is one UTF-8 JSON object with no duplicate member names and exactly
the keys `size`, `slide`, and `lateness`, in any key order. `size` and `slide`
are integers in `[1, 2^53)`. `lateness` is an integer in `[0, 2^53)`. Any other
shape, type, key set, duplicate name, or out-of-range value is a refusal: write
nothing, print exactly `refuse: bad config` followed by one LF on stderr, and
exit 2.

`events.jsonl` is a sequence of UTF-8 lines ending in LF. A line that is only
whitespace is ignored. Here whitespace means any character removed by Python's
`str.strip()` with no arguments (Unicode whitespace, including characters such
as U+2003 EM SPACE), not only the JSON structural set space/tab/LF/CR. Every
other line must be one complete JSON object. If any line fails to parse as a
JSON object, or if the file ends with a partial line that has no terminating LF,
refuse the whole stream: write nothing, print
exactly `refuse: truncated events` followed by one LF on stderr, and exit 2. An
empty file (zero bytes) is legal.

Arrival order. Process accepted event lines in file order. Do not reorder by
timestamp before applying the watermark rule below. Timestamps decide window
membership; file order decides lateness. The program must produce the same
bytes for a stream whether it updates aggregates event by event or buffers only
the events the watermark rule accepts and assigns windows afterward; only the
final output bytes are graded.

Events. After a non-ignored line has parsed as a JSON object (otherwise the
truncated-events rule above already refused the stream), that object must have
no duplicate member names and exactly the keys `key`, `ts`, and `val`. `key` is
a nonempty string of ASCII letters, digits, and underscore, length at most 64.
`ts` is an integer in `[0, 2^53)`. `val` is an integer in
`(-2^53, 2^53)` that is not a JSON boolean. Any other object shape or type is a
refusal: write nothing, print exactly `refuse: bad event` followed by one LF on
stderr, and exit 2. A JSON number whose digit string raises a decoder conversion
error under the platform JSON parser is also a bad event (not truncated events).
A complete JSON value that is not an object (array, null, number, string, or
boolean) is truncated events, not bad event. The platform decoder for this
interface also rejects the nonfinite constants `NaN`, `Infinity`, and
`-Infinity` (alone on a line or as a member value). A line that contains one of
them therefore fails to parse as a complete JSON object and is truncated events,
not bad event.

Watermark. Maintain `max_seen` unset before the first event. After each event is
examined in arrival order, whether or not it is accepted, set
`max_seen = ts` if unset else `max(max_seen, ts)`, then set
`watermark = max_seen - lateness`. Before that update, if `max_seen` is already
set and `ts < watermark`, the event is late: discard it and do not add its
`val` to any window, then still perform the `max_seen` and `watermark` update.

Windows. Hopping windows have width `size` and step `slide`. A window that
starts at integer `w` covers the half-open event-time interval `[w, w + size)`.
Only windows with `w >= 0` are used. An accepted event at time `ts` belongs to
every such window whose start `w` is a multiple of `slide`, satisfies
`w >= 0`, and satisfies `ts - size < w <= ts`. Add `val` into that window's
total for `key`. An event may belong to more than one window when `slide < size`.

Output. On success, write a JSON object to `--out`, create parent directories as
needed, using the text produced by Python `json.dumps(..., indent=2,
ensure_ascii=False)` (two-space indentation, a space after each `:`, and a
comma immediately before the following LF between items), and end the file with
exactly one LF. The object has exactly one key, `windows`, whose value is a JSON
array. Each element is an object with exactly the keys `start`, `end`, and
`totals` in that order. `start` is `w`. `end` is `w + size`. `totals` is a JSON
object whose keys are the keys that received at least one accepted `val` in that
window (retain a key even when its sum is zero), sorted by bytewise ascending
order, each mapped to the integer sum of those values. Emit a window only when
`totals` is nonempty. Order the `windows` array by ascending `start`. Do not
emit any other key at any level.

The empty result (no accepted contributions) is:

```
{
  "windows": []
}
```

followed by one LF.

Success exits 0. Stdout must be empty on both success and refusal.

Limits. A stream with more than 10_000 event lines, or an `events.jsonl` larger
than 1_048_576 bytes, is outside the graded domain. Graded streams also keep
`size` and `slide` at most 10_000, keep `size <= 10 * slide` (so each event
belongs to at most 10 windows), use at most 256 distinct keys, and never require
emitting more than 10_000 windows. The program may assume every graded stream
stays inside those bounds. Each graded invocation is allowed at most 20 seconds
of wall time.
