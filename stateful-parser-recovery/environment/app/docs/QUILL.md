This page is the authority for the Quill nest parser at `/app/src/quill.py`.
Diagnostics other than the refusal lines and fault kinds named here, concurrent
writers, and durability of the output path are outside this interface.

Drive. Run:

```
python3 /app/src/quill.py --src <file> --out <file>
```

Arguments. Both `--src` and `--out` are required and must appear with a
non-empty value. `--src` must name an existing regular file that is not a
symbolic link (`lstat` must be a regular file). If any required flag is absent
or lacks a value, or if `--src` fails those checks (including a nonexistent
path, a directory, a symbolic link, or any other non-regular-file `lstat`
result), refuse: write nothing new to `--out` (when `--out` is absent as a
path, do not create it; when it names a regular file, leave its bytes
unchanged; when it is a symbolic link, do not follow it and leave its target's
bytes unchanged if that target is a regular file); print exactly
`refuse: bad args` followed by one LF on standard error; exit with status 2.
Standard output is empty on refusal and on success.

Source file. `--src` is a UTF-8 text file of LF-terminated lines. A line that
is only whitespace is ignored. Here whitespace means any character removed by
Python's `str.strip()` with no arguments. Every other line must end with LF in
the file; if the file ends with a partial line that has no terminating LF, or
if the bytes are not valid UTF-8, refuse: write nothing to `--out`, print
exactly `refuse: bad source` followed by one LF on stderr, and exit 2. An
empty file (zero bytes) is legal.

Statements. After ignoring whitespace-only lines, each remaining line is one
statement. Tokens on a statement are separated by one or more ASCII space or
tab characters. Leading and trailing whitespace around the line is stripped
before tokenization. Statement forms and exact arities:

- `open UNIT <name>` — three tokens; opens a UNIT frame named `<name>`.
- `open BLOCK <name>` — three tokens; opens a BLOCK frame named `<name>`.
- `end UNIT` — two tokens; closes the innermost open frame when that frame is UNIT.
- `end BLOCK` — two tokens; closes the innermost open frame when that frame is BLOCK.
- `bind VAL <name> <int>` — four tokens; binds `<name>` as kind VAL to `<int>` in the current frame.
- `bind ALIAS <name> <target>` — four tokens; binds `<name>` as kind ALIAS to `<target>` in the current frame.
- `use <name>` — two tokens; records a use of `<name>`.

Any other token count, any unrecognized keyword sequence, or any token that
fails the form rules below is a FORM fault on that line. The first token of a
line is always the verb; verbs are exactly the strings `open`, `end`, `bind`,
and `use` (case-sensitive). A line whose first token is not one of those verbs
is FORM.

Names. A `<name>` or `<target>` is a nonempty string of ASCII letters, digits,
and underscore whose UTF-8 byte length is at most 16. A name that is empty,
contains any other byte, or exceeds 16 UTF-8 bytes is FORM on that line. Graded
sources never place a NUL byte inside a token.

Integers. A `<int>` is a signed decimal integer literal with an optional
leading ASCII `-`, then one or more ASCII digits, with no leading `+`, no
leading zeros unless the whole value is exactly `0` or `-0` (both mean zero),
and whose mathematical value lies in `(-2^53, 2^53)`. Any other token in the
integer position is FORM.

Frames and the stack. There is a stack of open frames. The file begins with an
empty stack. An `open` statement that is well-formed pushes a new frame of the
named kind with that name onto the stack; that push is the apply for `open`.
`bind` and `use` require a nonempty stack; if the stack is empty when a
well-formed `bind` or `use` is reached, that line is FORM (not UNBOUND). An
`end` statement that is well-formed pops the innermost frame when the top
frame's kind equals the closer's kind.

FORM. On a FORM fault: record a FORM fault at that line number (1-based among
physical file lines, counting ignored whitespace lines and counting from the
first byte of the file), do not apply the statement (in particular, a malformed
`open` does not push a child frame), and halt later bindings: no later `open`,
`bind`, `end`, or `use` statement is applied. Later lines are still scanned
only for the purpose of discovering faults that compete in `pick` below; they
do not change the stack or the applied binding table.

NEST from a closer. When an `end` statement is well-formed as tokens but the
stack is empty, or the top frame's kind does not equal the closer's kind,
record a NEST fault on THIS closer line, halt later bindings as under FORM, and
set the closer-mismatch flag. When the closer-mismatch flag is set, end-of-file
still-open nest (below) does not fire.

DUP. Binding names collide inside a single frame regardless of kind: if a
well-formed `bind VAL` or `bind ALIAS` names an identifier already bound in the
current (innermost) frame by any kind, record a DUP fault on that line, do not
apply the bind, and halt later declarations: no later `open`, `bind`, or `use`
is applied. A later well-formed `end` still applies after a DUP halt, so a
matching closer may empty the stack and prevent end-of-file nest from firing.
A failed bind never creates a child frame.

UNBOUND. A well-formed `use <name>` looks up `<name>` from the innermost frame
outward through still-open frames, considering only bindings that were actually
applied (a bind that faulted is not applied). ALIAS bindings resolve one step to
their `<target>` in the same frame; if that target is not an applied VAL binding
in the same frame, the use is UNBOUND. If no applied VAL is found after at most
one ALIAS step, record UNBOUND on that use line. UNBOUND does not halt later
bindings: later well-formed statements still apply, so a later matching `end`
may close the frame and prevent end-of-file nest from firing. Nested frames see
parent bindings; a name bound only in a child is not visible in the parent after
that child is closed.

Still-open at end of file. After the last line, if the stack is still nonempty
and the closer-mismatch flag is not set, record a NEST fault at the header line
of the outermost still-open frame (the bottom of the stack). If the
closer-mismatch flag is set, do not record that end-of-file nest.

Pick. Among all recorded faults, choose exactly one with `pick`: the fault with
the earliest line number wins; when two faults share a line, rank by kind in
this order: FORM, then NEST, then DUP, then UNBOUND (FORM beats NEST beats DUP
beats UNBOUND). Graded sources never place two same-kind faults on one line.

Applied table. While no halt has occurred, applied `bind VAL` entries store the
integer; applied `bind ALIAS` entries store the target name string. A `use` is
applied only when the lookup rule above succeeds on that line (otherwise the
line records UNBOUND and no use is applied). After `pick`, if no fault was
selected, each applied use is reported as the integer of the VAL that lookup
reached at the use line. Uses that were not applied (because halt already
fired, or because the use was UNBOUND) do not appear in the output.

Output. On a successful CLI run (not a refusal), write a JSON object to `--out`,
creating parent directories as needed, using the text produced by Python
`json.dumps(..., indent=2, ensure_ascii=False)` and ending the file with exactly
one LF. Key order at every object level is fixed as listed here.

When `pick` selected a fault, the object has exactly the keys `status`, `fault`,
`line`, in that order: `status` is the string `fault`; `fault` is one of
`form`, `nest`, `dup`, `unbound` (lowercase); `line` is the 1-based line number.

When no fault was recorded, the object has exactly the keys `status`, `frames`,
`bindings`, `uses`, in that order: `status` is the string `ok`; `frames` is a
JSON array of objects in push order, each with keys `kind`, `name`, `line` in
that order (`kind` is `UNIT` or `BLOCK`, `name` is the frame name, `line` is the
`open` line); after a matching `end`, that frame is omitted from the final
array (only frames that would still be open are listed; on a fully balanced
file `frames` is `[]`); `bindings` is a JSON array of every applied bind in
apply order, each object with keys `frame`, `kind`, `name`, `value`, `line` in
that order where `frame` is the frame name at bind time, `kind` is `VAL` or
`ALIAS`, `value` is the integer for VAL or the target string for ALIAS, and
`line` is the bind line; `uses` is a JSON array of applied uses in apply order,
each object with keys `name`, `value`, `line` in that order where `value` is the
resolved integer.

Success exits 0. Stdout must be empty on both success and refusal.

Limits. A source with more than 2_000 lines, or larger than 65_536 bytes, is
outside the graded domain. Graded sources use at most 64 open frames at peak
depth and at most 256 applied bindings. The program may assume every graded
source stays inside those bounds. Each run may take at most 20 seconds.
