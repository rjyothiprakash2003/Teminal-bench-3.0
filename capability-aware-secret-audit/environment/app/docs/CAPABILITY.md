This page is the authority for the auditor built from /app/cmd/casaudit.

Outside this interface: concurrent writers, ACLs beyond the Unix mode bits named
here, SELinux labels, and network filesystems.

Drive. The auditor is the program built from /app/cmd/casaudit. Invoke it with:

<auditor> --root <dir> --audience <file> --report <file>

Grading rebuilds from those sources and drives that interface. Sample trees under
/app/samples are for local checks only.

Arguments. Each of --root, --audience, and --report is required and must appear
with a non-empty value. --root must name an existing directory that is not a
symbolic link. Trailing path separators on --root are ignored for that check:
remove every trailing / except that a path consisting only of one or more /
characters is treated as /; then lstat of the resulting path must report a
directory that is not a symbolic link (so link/, link//, and similar forms of a
symlink-to-directory are refused, while / itself remains a valid root).
--audience must name an existing regular file that is not a symbolic link. The
audience file is UTF-8 JSON whose top-level value is a JSON object with exactly
the keys entries and subjects (no other top-level keys; no duplicate top-level
keys; a top-level null, array, or scalar is refused). entries must be a JSON
array (not null) of objects;
each object has exactly the keys path, allow_uids, and allow_gids (no other
keys; no duplicate member names inside the object). path is a
relative path under --root using / as the only separator, with no leading /, no
empty components, and no . or .. components; a backslash may appear inside a
component as an ordinary filename byte. allow_uids and allow_gids must each be a
JSON array (not null) whose elements are JSON numbers that are non-negative
integers (not null) that fit in an unsigned 64-bit range (0 through
18446744073709551615). subjects must be a JSON array (not null) of objects; each
object has exactly the keys uid and gids (no other keys; no duplicate member
names inside the object); uid must be present as a JSON number
that is a non-negative integer (not null) in that same unsigned 64-bit range;
gids must be a JSON array (not null) of non-negative integer numbers (not null)
in that same range. JSON strings that look like integers are refused. Duplicate
paths among entries are
refused even when other fields differ. If any required flag is absent or lacks a
value, if --root or --audience fails those checks, if the audience bytes are not
valid UTF-8, if the audience JSON fails the shape above, or if any entry path is
malformed as defined here, refuse: write nothing new to --report (when the path
is absent do not create it; when it names a regular file leave its bytes
unchanged; when it is a symbolic link do not follow it and leave the target
bytes unchanged if that target is a regular file); print exactly refuse: bad args
followed by one LF on standard error; exit with status 2. Standard output is
empty on refusal and on success.

Path steps. For each audience entry, walk the relative path one component at a
time starting from --root using lstat only. Containment comparisons use the
real path of --root (resolve every symlink in the --root pathname itself with
realpath / EvalSymlinks; the --root final component remains a non-symlink
directory under lstat as required above). For an intermediate (non-final)
symbolic link: resolve that link alone with realpath; if resolution fails, or
the resolved path is not equal to --root's real path and is not a path beneath
--root's real path (compare absolute resolved paths: equality, or a strict
prefix ending with / on the root real path), skip that entry with reason
escaped and do not open the link target. An intermediate symbolic link that
stays under --root is permitted: continue from the resolved directory, and for
effective-read later include every directory from --root down to that resolved
directory (inclusive of intermediate directories on the resolved absolute path,
exclusive of directories above --root). A link that resolves exactly to --root
is permitted. An intermediate component that exists and is not a symbolic link
must be a directory under lstat; an existing regular file, FIFO, socket,
device, or other non-directory intermediate is skipped as unsupported (not
missing). For a final symbolic link the final-link rule below takes
precedence over the intermediate failure rule: if its target cannot be
resolved, or its resolved target is not a regular file under --root's real
path, skip as unsupported (not escaped). The final component must otherwise
resolve to a regular file under --root after following only permitted links. A
missing non-symlink component is skipped as missing. FIFO, socket, device, and
directory finals are unsupported. Do not open escaping link targets.

Effective read. A subject can effectively read a secret when --root itself and
every directory at or beneath --root on the resolved path grant that subject
execute, and the final regular file grants that subject read. Directories above
--root are never consulted. Class selection for a node uses the node's st_uid
and st_gid from lstat: if the subject's uid equals st_uid use the owner bits;
else if any of the subject's gids equals st_gid use the group bits (any matching
gid counts, not only the first); else use the other bits. Do not fall through to
a lower-priority class when the selected class lacks the needed bit. Execute is
bit 0o100/0o010/0o001 for owner/group/other. Read is bit 0o400/0o040/0o004 for
owner/group/other. Root uid 0 is not special: apply the same class rules.

Exposure. A subject is unintended for an entry when the subject's uid is absent
from allow_uids and none of the subject's gids appear in allow_gids. When an
unintended subject can effectively read the secret, record exposure string
uid:<uid> for that subject. Exposure strings are unique (one string per distinct
uid even if multiple subject records share that uid) and sorted ascending as
Unicode. Emit a finding only when the exposure list is non-empty.

Remediation. For each finding, compute a target mode for the final regular file
only. Start from st_mode & 0o7777 of that file. Clear other-read (0o004) only
when any recorded exposure reached the file through the other class on the file
itself. Clear group-read (0o040) only when any recorded exposure reached the
file through the group class on the file itself and the file's st_gid is not
listed in allow_gids. Never clear owner-read. Leave setuid, setgid, sticky, and
all other mode bits unchanged except the clears just named. The remediation
object has exactly the key mode, a four-digit octal string of the resulting mask
(for example 0640). Parent directory modes are never proposed as remediation.

Report. When the run is not a refuse: bad args exit, write --report as UTF-8
text equivalent to Python json.dumps(obj, indent=2, sort_keys=True,
ensure_ascii=False) ending with exactly one LF. Characters such as <, >, &,
U+2028, U+2029, and other non-ASCII code points must appear as themselves in the
JSON text, not as \\uXXXX escapes, unless JSON syntax requires an escape. Create
any missing parent directories of --report as needed before writing. When
--report names an existing symbolic link whose target is a regular file, follow
that link and overwrite the target while leaving the symlink itself in place.
The object has exactly the keys findings and skipped. findings is an array of
objects with exactly the keys path, exposure, and remediation, sorted ascending
by path. skipped is an array of objects with exactly the keys path and reason,
sorted ascending by path; each reason is one of escaped, missing, or
unsupported. Exit 0 on that completion.

Limits. Graded trees hold at most 48 audience entries, depth at most 12
(components in a relative path), and no regular file larger than 65536 bytes.
Graded trees use only well-formed UTF-8 filename bytes. Each graded invocation
may use at most 20 seconds of wall time. Entries are processed in the order they
appear in the audience file for walking, but findings and skipped arrays are
sorted as stated above.
