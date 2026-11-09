This page is the authority for the syncer at `/app/src/syncer.py`.
Concurrent writers, network filesystems, and diagnostics other than the refusal
and skip reasons named here are outside this interface.

Drive. Run:

```
python3 /app/src/syncer.py --src <dir> --dst <dir> --report <file>
```

Arguments. Each of `--src`, `--dst`, and `--report` is required and must appear
with a non-empty value. `--src` must name an existing directory that is not a
symbolic link: `lstat` of `--src` must be a directory. `--dst` must likewise
name an existing directory that is not a symbolic link. The real paths of
`--src` and `--dst` must differ, and neither may be a path beneath the other
(compare absolute resolved paths: equality, or a strict prefix ending with `/`).
`--report` names the path where the program writes the report on success. If any
required flag is absent or lacks a value,
if `--src` or `--dst` fails those checks (including a nonexistent path, a
regular file, a symbolic link, or any other non-directory `lstat` result), if
the real path of `--dst` cannot be resolved, or if `--src` and `--dst` resolve
to the same directory or one beneath the other, refuse: write nothing new to `--report` (when `--report`
is absent as a path, do not create it; when it names a regular file, leave its
bytes unchanged; when it is a symbolic link, do not follow it and leave its
target's bytes unchanged if that target is a regular file); print exactly
`refuse: bad args` followed by one LF on standard error; exit with status 2.
Standard output is empty on refusal and on success.

Walk. Discover entries under `--src` with a depth-first walk that never follows
symbolic links. At each directory, list entries with `listdir`, ignore the
names `.` and `..`, and process the remaining names in ascending order of their
filename bytes as returned by the filesystem (`os.fsencode`). Classify each
entry by `lstat` only. A symbolic link is a leaf: do not enter it as a
directory even when its target is a directory. File types other than regular
file, directory, or symbolic link are skipped with reason `unsupported`.
Classify that skip from the source `lstat` alone, before any destination-safety
check: an unsupported source entry is reported as `unsupported` even when its
destination parent path would be unsafe. Graded trees use FIFO and Unix-domain
socket as the unsupported source types; other special nodes (for example
character or block devices) are outside the graded domain.

Relative paths. The relative path of an entry is the path from `--src` using
`/` as the separator and with no leading `/`. The walk root itself is the empty
string (not `.`). Graded trees never place a `.` or `..` component in a relative
path.

Directories. For a source directory, first ensure a directory exists at the
matching path under `--dst` and set its mode to `st_mode & 0o7777` from the
source `lstat`. If a non-directory already stands at that destination path,
skip with reason `type conflict` and do not alter it. When the directory rule
succeeds (whether it created the directory, or an existing directory was
already present and had its mode set), include that relative path in `copied`.
Only after that ensure-and-mode step, list the source directory's children and
process them. A type conflict on a directory does not stop discovery of that
directory's source children; each child is still considered under these walk
and copy rules.

Regular files. Open the source with `O_RDONLY` combined with `O_NOFOLLOW` when
the platform provides `O_NOFOLLOW`. Copy every byte into a regular file at the
destination path, replacing a regular file if one already stands there, then
set the destination mode to `st_mode & 0o7777`. If a directory or symbolic link
stands at the destination path, skip with reason `type conflict` and do not
alter it. If opening or reading the source fails (for example `PermissionError`
on a mode-`000` file), skip with reason `unsupported` and do not alter the
destination. Graded regular-file modes use only bits in `0o3777` (permission
bits, setgid, and sticky); setuid on regular files is outside the graded domain.

Symbolic links. Create a symbolic link at the destination whose target string
equals the source link text from `os.readlink` (decode source target bytes
with the `surrogateescape` error handler when reading bytes). If a
non-symlink stands at the destination path, skip with reason `type conflict`.
If a symlink already stands there, replace it. Never open the symlink target,
never `chmod` the target, and never `chown` the target. Mode bits on the
symlink inode itself are not graded. A dangling source symlink (target absent)
is still recreated at the destination.

Unsafe destinations. Before creating or replacing an entry at relative path
`rel`, walk each path component under `--dst` except the final component. For
each walked component that already exists:

- If it is a symbolic link, resolve it with `realpath`. If the resolved path is
  not equal to `--dst`'s real path and is not a path beneath `--dst`'s real
  path (compare absolute resolved paths: equality, or a strict prefix ending
  with `/` on the destination real path), the entry is unsafe.
- If it is not a symbolic link and not a directory (for example a regular file
  or FIFO), the entry is unsafe.

When an entry is unsafe, skip it with reason `unsafe` and write nothing for it.
An intermediate symbolic link that resolves to `--dst` or to a path beneath
`--dst` is permitted: continue considering the entry. When such a permitted
intermediate symlink's referent does not yet exist, create the missing
directories at the resolved path under `--dst` before writing through the link.
A source directory whose own destination path is unsafe is still listed as
unsafe, and discovery of its source children continues so each descendant is
also considered (and typically also skipped as unsafe).

Inaccessible sources. If listing a source directory raises `PermissionError`
after the directory ensure-and-mode step above has already succeeded, still
record that relative path in `copied`, also append a `skipped` row with reason
`inaccessible` for the same path, and continue with its siblings. Do not remove
the destination directory that was ensured, and do not abort the run. The walk
root (relative path empty string) is ensured like any other source directory
before its listing is attempted, so a root listing failure after a successful
ensure records both `copied` and `inaccessible` for `""`.

Report. When the run is not a `refuse: bad args` exit, write `--report` as the
UTF-8 text of `json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False)`
ending with exactly one LF. The object has exactly the keys `copied` and
`skipped`. `copied` is a JSON array of relative path strings for every entry
the copy rules successfully applied as defined above, sorted ascending by
those strings as Unicode. `skipped` is a JSON array of objects with exactly
the keys `path` and `reason`, sorted ascending by `path`; each `reason` is one
of `type conflict`, `unsafe`, `inaccessible`, or `unsupported`. Exit 0 on that
completion.

Limits. Graded trees hold at most 40 entries, depth at most 11, and no
regular file larger than 65536 bytes. Graded trees use only well-formed UTF-8
filename bytes (no unpaired surrogate names). Each graded invocation may use
at most 20 seconds of wall time.
