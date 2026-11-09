# Counter column-family compaction contract

This is the offline compaction core, not an SST file reader. The caller has already
validated and decoded the files. Every request is valid; corrupt-input diagnostics,
file durability, and concurrent calls are outside this interface.

## Wire interface

Read one JSON object from stdin and emit one JSON object followed by exactly one LF
on stdout, then exit 0. Input accepts all legal JSON whitespace and object-member
orders. Output starts with `{`; whitespace inside the object and object-member
order are immaterial, but its closing `}` is followed only by that single LF.
Arrays have the orders specified below. No other stdout text is permitted.

A request has `latest`, `snapshots`, `tables`, and `lower` members, and may
include `cutoff`, `release`, and `lift`. `latest` and all sequence numbers are exact unsigned 64-bit
integers. Input records have sequence numbers in [1, latest]. `snapshots` contains
any number of sequence numbers in [0, latest], possibly duplicated or unordered.
The read cuts are the distinct snapshots plus latest; even a request without
snapshots has the latest read cut. The 128-cut limit applies after deduplication,
not to the snapshot-array length. `latest` may be zero; then there are no input
point or range records. `cutoff` is an unsigned integer in [0, latest]. If the
member is omitted, the cutoff is 0. `release` is an unsigned integer in [0, latest].
If the member is omitted, the release is 0. `lift` is an unsigned integer in
[0, latest]. If the member is omitted, the lift is 0.

Each table has `points` and `ranges` arrays, both in arbitrary order. A point is
`{"key":"61","seq":7,"kind":"put","value":40}`. Kinds are `put`, `merge`,
and `delete`; delete has no value. Values and all operand sums fit signed 64-bit
integers. A range deletion is `{"start":"61","end":"7a","seq":8}`.
Keys and endpoints are nonempty, lowercase, even-length hexadecimal encodings of
arbitrary byte strings. Compare the decoded bytes lexicographically, with a proper
prefix before its extension. Ranges always have start < end and are half open.
Do not interpret a key as a number or as UTF-8 text.

The same point may occur in several tables; exact duplicates represent one logical
record. Different points at the same key never share a sequence number. Identical
range records may also be duplicated. Different ranges may share a sequence.
A range and a point can have equal sequences; the range deletion wins that tie.
Table order has no precedence.

`lower` is an arbitrary, possibly overlapping list of {start,end} half-open spans.
Its union describes keys that may still have a value at sequence 0 in files NOT
included in this compaction. Their values are unknown. Outside that union there is
no lower-level state. Lower spans are metadata, not deletions or output records.

## Reader semantics

At a key and read cut, ignore records newer than the cut. Combine point versions
and covering range deletions in descending sequence order (range wins a tie).
A put terminates the read with its value plus any newer merge operands. A point or
range deletion terminates it with absence if no merge was encountered, otherwise
with the sum of those operands on a zero base. Merge operands accumulate until a
terminal record or the end. At the end, use the lower value if present, otherwise
zero; without any merge and without a base, the key is absent. A zero-sum merge
therefore creates a present value of zero even over an absent base.

Only the stated read cuts need to survive this compaction. That survival is
judged against the history after cutoff collapse, release pruning, and range
lift below, not against the raw input: a dropped or promoted range may change
the value observed at a stated cut. Lower values, if any, must be preserved for
every possible assignment, not guessed to be zero.

## Compaction cutoff

A cutoff of 0 leaves every input record unchanged; the rest of this page then
applies to the original history. When cutoff is positive, collapse is applied
first, using the original history, and only afterwards are release pruning,
range lift, liveness, merge folding, range cells, and the retention rules decided.

Let F be the newest read cut that is strictly less than cutoff, or 0 if no read
cut is smaller. Point records with sequence <= F or greater than cutoff are not
rewritten. Point records with sequence in (F, cutoff] are the collapse window.
Original range records are never rewritten: lifting a range to cutoff would
invert any point that originally won a sequence comparison against it.

For every key that has at least one point in the window, read the original
history at cutoff. If that read terminates (it reaches a put, a point delete,
or a range delete) or the key lies outside the lower union, replace every
window point at that key with one materialized record at sequence cutoff: a put
holding the read's value when the key is present, or a delete when it is
absent. If the read does not terminate and the key lies in the lower union,
leave the window points in place; do not invent a put or delete. Materializing
a determined merge chain as a put is required here and is the only exception to
the later ban on turning a merge into a put.

Cutoff is not itself a read cut unless it already equals a snapshot or latest.
Unstated intermediate sequences need not survive. The rest of this page applies
to the reduced history as if it were the original input, so point liveness and
merge folding see materialized records, not the pre-collapse window points they
replaced. Range cells and tombstone stacks use the range sequences that survive
release pruning and then range lift below.

## Compaction release

A release of 0 leaves every range record unchanged. When release is positive,
apply it after cutoff collapse, before range lift, and before liveness, merge
folding, and range fragmentation.

Let G be the newest read cut that is strictly less than release, or 0 if no read
cut is smaller. Drop every range record whose sequence lies in (G, release] and
whose half-open interval does not overlap the lower union. Two half-open
intervals [a,b) and [c,d) overlap when a < d and c < b. Dropped ranges are absent
from the reduced history: they do not terminate reads, do not break merge runs,
and do not contribute to tombstone stacks. Ranges that overlap the lower union,
and ranges with sequence <= G or greater than release, stay. Release is not
itself a read cut unless it already equals a snapshot or latest. Point records
are never dropped by release. Because a dropped range is absent from the reduced
history, a stated cut may observe a different value after release than it would
on the raw input; the survival obligation above is to the reduced history.

## Range lift

A lift of 0 leaves every range sequence unchanged. When lift is positive, apply
it after release pruning and before liveness, merge folding, and range
fragmentation.

Let L be the newest read cut that is strictly less than lift, or 0 if no read
cut is smaller. For every surviving range whose sequence lies in (L, lift],
decide whether promotion would invert a point win: if the post-cutoff,
post-release history contains any point P with R.start <= P.key < R.end and
R.seq < P.seq <= lift, leave that range's sequence unchanged. Otherwise replace
the range's sequence with lift. Cutoff deliberately never rewrites range
sequences; lift is the only operation that may. After lift, identical
(start, end, seq) triples deduplicate like other duplicate range records. Lift
is not itself a read cut unless it already equals a snapshot or latest. Point
records are never modified by lift. Because a promoted range moves its sequence,
it may become a newer merge barrier or a different tombstone-stack winner than
the pre-lift history had.

## Canonical point retention and merge folding

The result has exactly two members: `points` and `ranges`. Empty arrays are `[]`.
No output object, including a point or fragment, repeats a member name.
A source point is live if at least one read at a stated cut encounters it before
terminating, using the union of point and range records that remain after cutoff
collapse, release pruning, and range lift. Retain exactly those live points.
Omit a live point deletion exactly when its
sequence is less than or equal to the oldest read cut and its key lies outside
the lower union. Every read cut counts, including a cut of zero. Sequence 1 is
compared the same way as every other sequence: it is omitted when it is less
than or equal to that oldest cut and the key is outside the lower union, and it
is retained otherwise. The decision uses only that deletion's sequence, the
read cuts, and whether its key lies in the lower union. Liveness is decided
first: an omitted deletion still participates in those post-transform reads. Do not
discard puts with value zero.

For each live merge operand, its snapshot stripe is the smallest read cut >= its
sequence. Collapse each maximal run of consecutive live merge operands in the
per-key descending event stream of the post-transform history when they share a
stripe. A put, a point
delete, or a covering range deletion breaks a run, even if that terminal record
will be discarded from the output. Emit one merge at the newest sequence in the
run, with the exact sum. Keep zero-sum runs. Never turn a merge into a put or fold
it into a put: its base may be elsewhere, except for a put that cutoff
materialization required. No other synthesis or sequence rewriting is allowed. Sort output points by byte key ascending, then sequence descending.

## Canonical range fragments

Consider the elementary half-open cells induced by ALL surviving range endpoints
after cutoff collapse, release pruning, and range lift, together with all
lower-span endpoints. Point keys do not create cell boundaries. Each cell
has a tombstone stack. For each read cut, if any surviving range covers the cell
at a sequence less than or equal to that cut, select the greatest such sequence.
The stack is exactly those selected sequences, duplicates removed, sorted
descending. Then drop a selected sequence when it is less than or equal to the
oldest read cut and the cell lies outside the lower union. Every read cut counts,
including a cut of zero. Sequence 1 is compared the same way as every other
sequence. Do not place any other covering sequence on the stack, including an
older sequence that is not the greatest sequence visible at some read cut. The
decision uses only the sequences of the surviving ranges covering the cell, the
read cuts, and whether the cell lies in the lower union.

Omit cells with empty stacks. Coalesce adjacent cells with identical nonempty
stacks, including across an input endpoint or a change in lower coverage if their
resulting stacks agree. Never join across a cell with an empty stack. Emit each
maximal fragment as {"start":...,"end":...,"seqs":[...]}, sorted by start.
Range fragmentation and point liveness both refer to the post-cutoff,
post-release, post-lift record set, not to the output fragments. These retention
rules define the output exactly, beyond merely preserving the observed values.
Deletion omission and stack elision depend only on sequences, read cuts, and
lower coverage—not on values, table placement, or which non-deletion kind a live
point has. Which points are live still follows reader termination: a put ends a
read while a merge continues to older records.

## Operating envelope

The total request has at most 60,000 point records, 12,000 range records, 4,000 lower
spans, 128 distinct read cuts (including latest), and 128 tables. Keys are at most
32 bytes. JSON is at most 24 MiB. On two CPU cores with 2 GiB available to the
process, a warm compiled executable must finish one request within 20 seconds.
The engine must handle endpoints with no point records, nested/overlapping ranges,
and sequence numbers above 2^53 without losing integer precision. Use Go 1.24's
standard library and Go source included in the submitted main module under /app/src.
Local helper packages, including adapted or copied Go source, are allowed. This
rule concerns compiled module membership, not source authorship. Dependencies
resolved through other modules, vendor directories, or local replacements of
external modules are not allowed. All processing, including sorting and JSON
encoding, must stay inside the executable built from the submitted Go module.
In-process interpreters, virtual machines, and textual DSL dispatch implemented
by Go code in that executable are allowed, including evaluation of embedded
scripts or bytecode. They must satisfy the same output and resource limits as
other implementations. Do not launch another executable to perform processing.
At grading time the executable runs as an unprivileged
user in an isolated filesystem containing only that executable and a writable
`/tmp` directory. It receives the request on stdin and writes the result on stdout;
source files, external commands, and the build environment are unavailable there.
Go helper packages and data embedded at build time remain part of the executable.
Starting another executable is blocked, including an interpreter or helper
extracted into `/tmp`.
Temporary data files remain allowed. The runtime's inherited file-descriptor limit
can be queried but cannot be changed.
No network access is available.
