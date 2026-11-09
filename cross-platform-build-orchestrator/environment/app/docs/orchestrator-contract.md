This page is the authority for the orchestrator at `/app/src/orchestrator.py`.
Diagnostics other than the refusal lines named here, concurrent writers, and
durability of the output path are outside this interface.

Drive. Run:

```
python3 /app/src/orchestrator.py --config <dir> --out <file>
```

`<dir>` is a configuration directory. It must contain files named `project.json`
and `targets.json`. Optional directories `overlays/` and `toolchains/` may be
present. No other layout is graded. Symlink handling and filesystem metadata
beyond these names are outside this interface.

Project. `project.json` must be one UTF-8 JSON object. Graded project files use
ASCII JSON text. Graded configurations use only ASCII characters in string
values after JSON parsing (a `\uXXXX` escape that decodes to a non-ASCII code
point is outside the graded domain). Allowed top-level keys are only `cflags`,
`ldflags`, `env`, and `defines`. For a successful resolution, each present key
must have the type below; an absent key is treated as the empty value of that
type. Graded bad-project refusals are only the cases listed under Refusals;
other value-shape mismatches are outside the graded domain.

- `cflags` and `ldflags` are JSON arrays of strings.
- `env` and `defines` are JSON objects whose keys are strings and whose values
  are strings. The string exactly `$delete` is a legal value and is removed by
  Concrete (below) before a successful artifact is written.

Targets. `targets.json` must be one UTF-8 JSON object that, after parsing,
contains the key `targets` and no other keys. The value of `targets` is a JSON
array. Each element is an object with exactly the keys `id`, `arch`, `os`, and
`linker`, and may also carry the optional keys `override` and `toolchain`.
Field rules:

- `id` is a string matching `\A[a-z][a-z0-9_]{0,31}\Z` (entire string). Ids must
  be unique within the array.
- `arch` is a string equal to one of `x86_64`, `aarch64`, `riscv64`.
- `os` is a string equal to one of `linux`, `darwin`, `windows`.
- `linker` is a string equal to one of `bfd`, `lld`, `mold`, `ld64`, `link`.
- After the fields above are accepted as strings in those sets, the pair
  `(os, linker)` must be legal: `linux` with `bfd`/`lld`/`mold`; `darwin` with
  `ld64`/`lld`; `windows` with `link`/`lld`. Any other pair is a refusal.
- `toolchain`, if present, is a string matching `\A[a-z][a-z0-9_]{0,31}\Z`
  (entire string). It names the file `toolchains/<toolchain>.json` relative to
  the configuration directory (see Toolchains).
- `override`, if present, is a JSON object. Graded override faults are the
  `cflags` cases listed under Refusals and a forbidden top-level override key
  (any key outside the project key set). Other override value-shape mismatches
  are outside the graded domain.

An empty `targets` array is legal and yields the empty artifact list.

Overlays. If `overlays/` exists, every name in it must be exactly one of
`linux.json`, `darwin.json`, `windows.json`, or
`<os>_<arch>.json` for an allowed `os` and `arch` from the lists above (for
example `linux_aarch64.json`). No other name is permitted. Each overlay file is
a UTF-8 JSON object obeying the same key and value rules as `project.json`.
Graded configurations use only regular files under `overlays/`; directory
members are outside the graded domain. Graded success fixtures exercise only
the architecture-overlay names the verifier stages (`linux_x86_64`,
`linux_aarch64`, `linux_riscv64`, `darwin_aarch64`, `windows_x86_64`); other
legal `<os>_<arch>.json` names are outside the graded domain.

Toolchains. If `toolchains/` exists, every name in it must match
`<toolchain>.json` where `<toolchain>` matches `\A[a-z][a-z0-9_]{0,31}\Z`, and
each member must be a regular file. A toolchain file is a UTF-8 JSON object
whose keys are the project settings keys and, optionally, exactly one meta key
`extends`, whose value is a string matching the toolchain name pattern. Every
member must obey these rules whether or not a target names it. Graded
configurations use only regular files under `toolchains/`; directory members
are outside the graded domain. A target that names `toolchain` T requires that
`toolchains/T.json` exist among those members; a missing file is a refusal.

Toolchain expansion. A toolchain file whose `extends` is `P` inherits from
`toolchains/P.json`: its expansion is the expansion of `P` with the file's own
settings keys Applied over it (one settings object, finished before it meets
the target). The expansion of a file without `extends` is its own settings
keys. Resolution Applies the expansion of the toolchain a target names.
That expansion refuses (`refuse: bad toolchain`) when its walk reaches an
`extends` parent that has no file, reaches a file the walk already took (a
cycle), or would take more than three files counting the named one (the walk
from a target's toolchain is the only expansion there is). Graded success
chains use at most two files (one `extends` hop). Graded cycle refusals stage a
two-node cycle.

Apply. Apply merges an overlay object into a base object. Start from a deep
copy of the base. For each key `k` in the overlay:

- If `k` is `cflags` or `ldflags`, replace the result's array with the
  concatenation of the base array for `k` (or `[]` if absent) followed by the
  overlay array.
- If `k` is `env` or `defines`, start from the base object for `k` (or `{}`),
  then for each sub-key `s` in the overlay object: if the overlay value is
  exactly `$delete`, remove `s` from the result if present (a missing `s` is a
  no-op); otherwise set `s` to the overlay value.
- Any other key is a refusal. Overlay and project objects may use only the
  project settings keys. Toolchain files may use those keys plus `extends` as
  described above; `extends` is consumed during Toolchain expansion and is never
  passed to Apply as a settings key.

Concrete. Before Compact and Stamp, remove every `env` or `defines` entry whose
value is exactly `$delete`. This applies to values that arrived from the
project as well as from overlays, toolchains, and overrides. Successful output
never contains the string `$delete` as an `env` or `defines` value; flag arrays
are not maps, so a `$delete` element in `cflags` or `ldflags` is an ordinary
flag and is kept. Comparison is exact string equality with `$delete`; a longer
string, a prefixed string, or a string that only matches after trimming
whitespace is not the sentinel and must be kept.

Arch inject. After overlays and before the linker profile, Apply one built-in
object chosen only from the target's `arch` (never from the host). The inject
contributes only `cflags` as follows, and contributes nothing else:

- `x86_64`: `["-m64"]`
- `aarch64`: `["-march=armv8-a"]`
- `riscv64`: `["-march=rv64gc"]`

Linker profiles. After Arch inject, Apply one built-in profile chosen only from
the target's `os` and `linker` (never from the host). The profile contributes
only `ldflags` as follows, and contributes nothing else:

- `linux`/`bfd`: `["-Wl,--hash-style=gnu"]`
- `linux`/`lld`: `["-fuse-ld=lld"]`
- `linux`/`mold`: `["-fuse-ld=mold"]`
- `darwin`/`ld64`: `["-Wl,-dead_strip"]`
- `darwin`/`lld`: `["-fuse-ld=lld"]`
- `windows`/`link`: `["/INCREMENTAL:NO"]`
- `windows`/`lld`: `["-fuse-ld=lld"]`

Profile extras. Immediately after the linker profile, if the triple
`(os, linker, arch)` appears in the table below, Apply one built-in object that
contributes only the listed `ldflags` (and nothing else). Triples absent from
the table contribute no extra. Graded extras are only these rows:

- `linux`/`mold`/`riscv64`: `["-Wl,--no-relax"]`
- `darwin`/`lld`/`aarch64`: `["-Wl,-dead_strip_dylibs"]`

Compact. After Concrete, rewrite `cflags` and `ldflags` independently by
walking each array left to right and dropping an element when it is exactly
equal to the element immediately before it. Non-adjacent duplicates are kept.
Maps are not compacted.

Stamp. After Compact, each artifact includes a `stamp` string: the lowercase
hexadecimal SHA-256 digest of one UTF-8 byte string built as follows. Let
`parts` be a list of strings. Append the literal `cflags`, then each entry of
the compacted `cflags` in order, then the literal `ldflags`, then each entry of
the compacted `ldflags` in order, then the literal `env`, then for each key of
`env` in ascending bytewise order the string `key=value`, then the literal
`defines`, then for each key of `defines` in ascending bytewise order the
string `key=value`, then exactly one of the literals `tc` or `notc`: use `tc`
when the target named a `toolchain` key (whether or not expansion added
settings), and `notc` when it did not. Join `parts` with a single NUL byte
(`U+0000`) between elements (no NUL before the first or after the last). Hash
those UTF-8 bytes with SHA-256. Graded stamps are exactly this digest; no other
fingerprint is accepted.

Resolution. For each target, independently (a mutation while resolving one
target must not affect another):

1. Start from a fresh copy of the project object (missing keys as empty arrays
   or objects).
2. If `overlays/<os>.json` exists, Apply it.
3. If `overlays/<os>_<arch>.json` exists, Apply it.
4. Apply the Arch inject for `arch`.
5. Apply the linker profile for `(os, linker)`.
6. Apply the Profile extra for `(os, linker, arch)` when the table lists one.
7. If the target has `toolchain`, expand that toolchain and Apply the result.
8. If the target has `override`, Apply it.
9. Concrete the maps.
10. Compact `cflags` and `ldflags`.
11. Compute Stamp from the compacted maps (including the `tc`/`notc` tag).

Host isolation. Graded fields (`cflags`, `ldflags`, `env`, `defines`, `triple`,
`stamp`) must be derived only from the configuration directory and the
built-in Arch-inject, linker-profile, and Profile-extra tables on this page.
The process environment, `sys.platform`, `os.uname`, `platform.machine`, and any
other host probe must not contribute values to those fields. Graded
host-isolation checks inject only these process-environment names:
`CROSS_HOST_INJECT`, `CC`, `CFLAGS`, `BUILD_HOST_TRIPLE`, and `SDKROOT`. Other
process-environment keys are outside the graded domain.

Output. On success, write one JSON object to `--out`, create parent directories
as needed, use two-space indentation, sort every object's keys by ascending
bytewise order at every depth, and end the file with exactly one LF. The object
has exactly one key, `artifacts`, whose value is a JSON array of artifact
objects sorted by ascending `id` (bytewise). Each artifact has exactly the keys
`id`, `triple`, `cflags`, `ldflags`, `env`, `defines`, `stamp`. `triple` is the
string `<arch>-<os>-<linker>`. The empty configuration yields:

```
{
  "artifacts": []
}
```

followed by one LF.

Success exits 0. Stdout must be empty on both success and refusal. On refusal,
write nothing to `--out` (do not leave a partial file from an earlier target).

Refusals. On any refusal: write nothing to `--out`, print exactly one of the
lines below followed by one LF on stderr, and exit with status 2. Every graded
refusal fixture exhibits exactly one of these faults. Configurations that
combine faults from more than one bullet, inputs that fail UTF-8 decoding or
JSON parsing, and value-shape violations other than those the verifier stages
are outside the graded domain.

- `refuse: bad project` — `project.json` missing, not a JSON object (graded
  non-objects are a JSON array and JSON `null`), a forbidden top-level key,
  `cflags` present with a non-string element, or `env` present but not a JSON
  object (graded as a JSON array).
- `refuse: bad targets` — `targets.json` missing, not an object whose only key
  is `targets` (graded: empty object `{}`, a non-object root such as `null` or
  `[]`, or an object with an extra key), `targets` not an array, duplicate ids,
  a non-object element, a target object with a forbidden extra key, an `id`
  that fails the pattern (graded: longer than allowed, or an uppercase initial
  letter), an `arch` outside the permitted set (graded invalid spelling:
  `mips`), an `arch` that is not a string, an `override` whose `cflags` is
  present but is not a JSON array of strings, a `toolchain` value that fails
  the toolchain name pattern (graded: an uppercase initial letter), or an
  `override` object with a forbidden top-level key (graded: a key other than
  the project key set). Invalid linker spellings other than the illegal pairs
  below, and invalid arch spellings other than `mips`, are outside the graded
  domain.
- `refuse: bad overlay` — a member of `overlays/` whose name is not on the
  whitelist above (graded: `weird.json`, `README`, and `linux_mips.json`), an
  overlay `env` value that is not a string (graded: a number), or a forbidden
  overlay key (graded: a key other than the project key set). Other overlay
  schema faults are outside the graded domain.
- `refuse: bad toolchain` — a member of `toolchains/` whose name is not a legal
  `<toolchain>.json` (graded: `Weird.json` and `ci.txt`), a toolchain file
  whose top-level key is forbidden (graded: a key other than the project key
  set and `extends`), a target that names a `toolchain` whose file is missing
  under `toolchains/` (graded: target names `missing` while `toolchains/` is
  absent or lacks `missing.json`), an `extends` value on any member that fails
  the name pattern (graded: `Ci`), an `extends` cycle encountered while
  expanding a target-named toolchain (graded: two files that extend each
  other), an `extends` parent name that has no file when expanding a
  target-named toolchain (graded: `extends` value `ghost` on the named file),
  or an `extends` chain that would take more than three toolchain files for
  one target (graded: a four-file chain). Other toolchain schema
  faults are outside the graded domain.
- `refuse: unknown platform` — a target's `arch`, `os`, and `linker` are each a
  permitted string from the lists above, but the pair `(os, linker)` is not in
  the legal set. Graded illegal pairs are only `linux`/`ld64` and
  `windows`/`bfd`; other illegal pairs are outside the graded domain.

Limits. Every graded configuration has at most 8 targets. Graded JSON files are
exactly those staged by the verifier; the program may assume each is small
enough to load entirely into memory. Each graded invocation is allowed at most
20 seconds of wall time.
