# Configuration merge notes

These notes are the authority for the merger at `/app/merge.py`. They say what
a merge root is, which roots are refused, what configuration a valid root
produces. How the program computes that configuration is left to the program.
The examples under `/app/examples` illustrate the notes; where an example and
the notes seem to differ, the notes decide.

## The run

**R1. Drive.** The program is run as

```
python3 -I -S /app/merge.py --root <dir> --out <file>
```

with both options given once each. The `-I -S` flags leave only Python's
standard library importable. What the program does with any other
command line is not part of these notes.

**R2. Outcomes.** Every run ends in exactly one of two ways.

- Success: the merged configuration is written to `<file>` as R16 says, any
  missing parent directories of `<file>` are created first, and the exit status
  is 0. Standard error is not read on success.
- Refusal: nothing is written to `<file>` (a file that already exists there
  keeps its bytes, and a missing one stays missing), standard error carries
  exactly one refusal line of R17 followed by a single LF byte (0x0A) and
  nothing else, and the exit status is 2.

Standard output stays empty in both cases.

## The root

**R3. Layout.** `<dir>` must name a directory that holds a regular file
`manifest.json` and a directory `sources`. Neither of those two may be a
symbolic link, and `manifest.json` may not be a directory, a FIFO or any other
non-regular file. A missing `<dir>`, a `<dir>` that is not a directory, and a
missing or wrongly typed `manifest.json` or `sources` are each a
`refuse: bad manifest`.

**R4. Manifest document.** `manifest.json` holds one JSON document in UTF-8:
the whole file is a single JSON value, optionally surrounded by JSON
whitespace. That value is an object with exactly one member, `sources`, whose
value is an array (an empty array is allowed). The tokens `NaN`, `Infinity`
and `-Infinity` are not JSON, and no object may name the same member twice.
Anything else is a `refuse: bad manifest`.

**R5. Manifest entries.** Each element of `sources` is an object with exactly
the two members `id` and `priority`, in either order.

- `id` is a string of 1 to 32 characters. Its first character is an ASCII
  lowercase letter `a` to `z`; every later character is an ASCII lowercase
  letter, an ASCII digit `0` to `9`, or `_`. No other character, including any
  non-ASCII letter and any trailing newline, may appear anywhere in it.
- `priority` is a JSON integer from 0 to 1000000 inclusive. A JSON number with
  a fraction or an exponent, such as `1.0` or `1e2`, is not an integer, and
  neither is `true` or `false`.
- No two entries share an `id`.

An entry that breaks any of these is a `refuse: bad manifest`.

**R6. Members of `sources`.** Every entry directly inside `sources` is a
member, and every member must be a regular file (not a symbolic link, not even
one that points at a regular file) named `<id>.json` for an `id` the manifest
lists. A subdirectory, a symbolic link, a FIFO, a socket, a file whose name
does not end in `.json`, and a `.json` file whose stem is not a listed `id` are
each a `refuse: bad manifest`. So is a listed `id` with no member
`<id>.json`.

**R7. Application order.** The listed sources are applied in ascending
`priority`, and sources of equal `priority` in ascending `id`, comparing ids
byte by byte. The position of an entry in the `sources` array and the order in
which the directory is read have no effect on the result.

## Source files

**R8. Source document.** Each `sources/<id>.json` holds one JSON document in
UTF-8, read as R4 reads the manifest (the whole file is one value, `NaN`,
`Infinity` and `-Infinity` are not JSON, and no object names a member twice).
The value must be an object. A file
that is not valid UTF-8, is not JSON, carries anything after its value other
than whitespace, or holds a value other than an object is a
`refuse: bad source`. Values are taken as Python's standard `json.loads`
decodes them: a number written without a fraction or exponent is an integer,
and any other number is a binary64 float. Numbers, strings and arrays reach
the output exactly as decoded, with no rounding or other change.

**R9. Metadata.** A top-level member of a source whose name starts with `$`
is metadata, and the only metadata allowed is `$extends`. Every other top-level
member is a data member, and members below the top level are data whatever
their names. `$extends`, when present, is an array of strings; each string is
an `id` the manifest lists, and no string appears in it twice. The ids it
names are the source's parents, in the order written. Following parents from
parent to parent must never lead back to a source already on the way, the
source itself included. A source with any other top-level `$` member, or with
an `$extends` that breaks these rules, is a `refuse: bad source`.

## The configuration

**R10. Values and markers.** Data values are any JSON values. A member whose
value is exactly the string `$delete` is a deletion marker when the object
holding it is reached from the top of a source's data through objects alone.
Arrays are ordinary values: they are never merged, only kept or replaced
whole, and nothing inside an array is a marker. A string that only resembles
`$delete`, such as `$delete!` or `$DELETE`, is an ordinary string, and `null`
is an ordinary value like any other.

**R11. Applying.** Applying an object O onto an object B gives a new object:
B with each member `k` of O acted on in turn.

- A deletion marker removes `k`; if B has no `k`, nothing happens.
- An object value is applied onto the value B holds at `k`, and the result
  becomes the value at `k`. Where B holds no object at `k`, the object is
  applied onto `{}` (an object always meets the object below it, an empty one
  if need be, so markers inside it act wherever it lands).
- Any other value (array, string, number, `true`, `false` or `null`) becomes
  the value at `k`, replacing whatever B held there.

Members of B that O does not name are kept as they are.

**R12. Layers.** Each source contributes one layer, an object. A source with
no parents contributes its data members as written. A source with parents
contributes the configurations of its parents (R13), applied onto `{}` one
after another in the order its `$extends` lists them, with its own data
members laid over the result. Laying a member puts its value at its key as
written, replacing what is there, except that where both the member's value
and the value already there are objects, the two are laid member by member in
the same way (a layer is laid and not applied, so a deletion marker in a
source's own data is still in its layer when the layer is applied, and removes
its key from whatever the sources applied before it have set).

**R13. Configurations.** The configuration of a source is its layer applied
onto `{}`. A source inherits from each parent the parent's configuration
(the parent's own result, its markers already spent there, which is all that
reaches the child).

**R14. The merged configuration.** The merged configuration is `{}` with the
layer of every listed source applied onto it, one source at a time in
application order. A key that one source removes may be set again by a source
applied later, and where several sources set one key, the source applied last
decides its value. Sources that set the same keys are never a refusal.

**R15. One merge per root.** Each source's layer depends only on the root's
files. The same root always gives the same configuration, whichever order the
program happens to do its work in, and a source that several others name as a
parent contributes the same configuration to each of them.

## Output and refusals

**R16. Output bytes.** On success, `<file>` holds exactly the bytes of the
Python standard library call

```
json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True)
```

applied to the merged configuration, followed by exactly one LF byte. That call
fixes the indentation, sorts the keys of every object at every depth
(including objects inside arrays), writes integers and floats in Python's own
spelling, and escapes every non-ASCII character. An empty configuration is
written as `{}` and an LF.

**R17. Refusal lines.** These are the only two refusal lines.

- `refuse: bad manifest` covers R3 to R6: the root, the manifest, its entries,
  and the members of `sources`.
- `refuse: bad source` covers R8 and R9: a source document that is not a
  strict JSON object, disallowed metadata, a malformed `$extends`, an `$extends`
  that names an unlisted id, and parents that lead back round.

## Bounds

**R18. The graded domain.** Grading feeds only roots inside these bounds.

- A root lists from 0 to 64 sources.
- Each source file is from 2 to 70,000 bytes on its own. The limit is per
  file: the files of one root may together exceed 70,000 bytes.
- Objects and arrays nest from 1 to 6 levels deep in any source, counting the
  top-level object as level 1.
- Every number is written with at most 300 digits, and every number with a
  fraction or exponent decodes to a finite binary64 value.
- Every file and directory in the root can be read by the program, and no
  file begins with a byte-order mark.
- A refused root breaks rules under only one of the two refusal lines.

Each run is given at most 4 seconds of wall time, and every graded root can
be merged in well under one second. What the program does
with any other input is not part of these notes.
