"""What the Calderbank verifier runs on: the build, the sandbox account, what a run
leaves behind it, and the table of traps that turns one sentence of SN-208 false at a
time. tests/test_outputs.py holds the assertions; everything they stand on is here."""

import ctypes
import hashlib
import importlib.util
import json
import os
import resource
import shutil
import signal
import stat
import struct
import subprocess
import tempfile
import time
from pathlib import Path
import pytest
TESTS_DIR = Path("/tests")
SEALED_DIR = TESTS_DIR / "sealed"
SHIPPED_DIR = TESTS_DIR / "shipped"
SOURCE_DIR = Path("/app/tools/schedbuild")
SANDBOX_ROOT = Path("/sandbox")
SANDBOX_UID = 2000
SANDBOX_GID = 2000
BUILD_TIMEOUT = 300
RUN_TIMEOUT = 120
SHORT_TIMEOUT = 5


BUILD_ENV = {
    "PATH": "/usr/local/go/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "HOME": "/home/sandbox",
    "LC_ALL": "C",
    "TMPDIR": "/sandbox/tmp",
    "GOCACHE": "/sandbox/gocache",
    "GOPATH": "/sandbox/gopath",
    "GOMODCACHE": "/sandbox/gopath/pkg/mod",
    "GOFLAGS": "-mod=mod",
    "GOPROXY": "off",
    "GOTOOLCHAIN": "local",
    "CGO_ENABLED": "0",
}


def _read_case(folder):
    facility = (folder / "facility.txt").read_bytes()
    table = (folder / "bases.tbl").read_bytes()
    expected = None
    if (folder / "expect.txt").exists():
        expected = (folder / "expect.txt").read_bytes()
    return facility, table, expected


def _read_sealed():
    """Two kinds of case, kept apart on purpose. tests/shipped holds the five settled
    facilities exactly as the candidate can read them, graded because a shipped example is a
    graded case. tests/sealed holds the cases the candidate cannot read, and no facility file
    and no schedule in it is byte-identical to anything under environment/."""
    cases = {}
    for room in (SEALED_DIR, SHIPPED_DIR):
        for entry in sorted(room.iterdir()):
            if entry.is_dir():
                cases[entry.name] = _read_case(entry)
    digests = {}
    for line in (SEALED_DIR / "digests.txt").read_text().splitlines():
        label, size, digest = line.split(" ")
        digests[label] = (int(size), digest)
    return cases, digests


def _beside(name):
    spec = importlib.util.spec_from_file_location(name, TESTS_DIR / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CASES, DIGESTS = _read_sealed()
# Read while the sealed room still stands, and gone from the disk with the rest of it. Each
# record is one run of the builder whose disposition the note settles: the bytes of a facility
# file, the table to hand it where that is not the one the desk ships, how the run is staged,
# and the status, the diagnostic and what stands at the output path afterwards.
TRAPS = json.loads((SEALED_DIR / "schedule-traps.json").read_text())
GENERATORS = _beside("genlib")
shutil.rmtree(SEALED_DIR)
shutil.rmtree(SHIPPED_DIR)
os.chmod("/app", 0o700)
os.chmod("/logs", 0o700)
SETTLED = ("crossley", "elverton", "northgate", "tarnside", "walmsgate")
HELD_OUT = ("sleddale", "brampton", "rakewood", "dalston", "mossdale")
_RUN_COUNTER = [0]
_TIMED_OUT = [False]
PATHS = ("papers/f.lst", "ref/table.dat", "issued/sched.out")
SPARE = ("a", "b", "c")


def live_sandbox_pids():
    """Processes of the sandbox account still alive, a zombie being already dead."""
    alive = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            if os.stat("/proc/" + entry).st_uid != SANDBOX_UID:
                continue
            with open("/proc/" + entry + "/stat", "rb") as handle:
                state = handle.read().rsplit(b")", 1)[1].split()[0]
        except (OSError, IndexError):
            continue
        if state != b"Z":
            alive.append(int(entry))
    return alive


def drain_sandbox(deadline=15.0):
    """A killed process is not gone the instant the signal is sent, so wait for it."""
    waited = 0.0
    while waited < deadline:
        if not live_sandbox_pids():
            return []
        time.sleep(0.02)
        waited += 0.02
    return live_sandbox_pids()


def own(path):
    subprocess.run(["/usr/bin/chown", "-R", "%d:%d" % (SANDBOX_UID, SANDBOX_GID), str(path)],
                   check=True, capture_output=True)


def run_unprivileged(command, cwd, timeout, fsize_limit=None):
    # A submission that hangs once hangs again, so only the first timeout is generous.
    if _TIMED_OUT[0]:
        timeout = SHORT_TIMEOUT
    for ancestor in list(Path(cwd).resolve().parents)[:-1]:
        try:
            ancestor.chmod(ancestor.stat().st_mode | 0o011)
        except OSError:
            pass
    dropped = ["/usr/bin/setpriv", "--reuid", str(SANDBOX_UID), "--regid", str(SANDBOX_GID),
               "--clear-groups", "--no-new-privs", "--"] + list(command)

    def limit_file_size():
        # SIGXFSZ would end the program instead of handing it a failed write, so it is ignored
        # and the limit surfaces as a write error the program has to notice.
        signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
        resource.setrlimit(resource.RLIMIT_FSIZE, (fsize_limit, fsize_limit))

    # A file size limit binds every regular file the program writes, standard error included
    # when that is a file, so under a limit the two streams are taken through pipes instead.
    piped = fsize_limit is not None
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        process = subprocess.Popen(dropped, cwd=str(cwd), env=BUILD_ENV,
                                   stdout=subprocess.PIPE if piped else out,
                                   stderr=subprocess.PIPE if piped else err,
                                   start_new_session=True,
                                   preexec_fn=limit_file_size if piped else None)
        group = process.pid
        timed_out = False
        streams = (b"", b"")
        try:
            if piped:
                streams = process.communicate(timeout=timeout)
            else:
                process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _TIMED_OUT[0] = True
        finally:
            try:
                os.killpg(group, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                pass
            if live_sandbox_pids():
                subprocess.run(["/usr/bin/pkill", "-9", "-u", str(SANDBOX_UID)],
                               capture_output=True)
            drain_sandbox()
        if timed_out:
            raise AssertionError("%s in %s did not finish within %ds"
                                 % (" ".join(command), cwd, timeout))
        if not piped:
            out.seek(0)
            err.seek(0)
            streams = (out.read(), err.read())
        return process.returncode, streams[0], streams[1]


def permitted_sources():
    """The module file and the regular Go sources standing directly in the tool directory."""
    chosen = []
    if not SOURCE_DIR.is_dir():
        return chosen
    for entry in sorted(SOURCE_DIR.iterdir()):
        if entry.is_symlink() or not entry.is_file():
            continue
        if entry.name == "go.mod" or entry.suffix == ".go":
            chosen.append(entry)
    return chosen


class Builder:
    def __init__(self):
        self.binary = None
        self.staged = []
        self.output = b""

    def prepare(self):
        tree = SANDBOX_ROOT / "build"
        if tree.exists():
            shutil.rmtree(tree)
        tree.mkdir(parents=True)
        for entry in permitted_sources():
            shutil.copyfile(str(entry), str(tree / entry.name))
            self.staged.append(entry.name)
        own(tree)
        code, stdout, stderr = run_unprivileged(
            ["/usr/local/go/bin/go", "build", "-o", "schedbuild", "."], tree, BUILD_TIMEOUT)
        self.output = stdout + stderr
        produced = tree / "schedbuild"
        if code == 0 and produced.exists():
            self.binary = produced.read_bytes()
        return code


BUILDER = Builder()
BUILD_STATUS = BUILDER.prepare()


def require_binary():
    if BUILDER.binary is None:
        pytest.fail("the sources in the tool directory did not build: %s"
                    % BUILDER.output.decode("utf-8", "replace")[-2000:])


def stage_run(facility, table, existing=None, names=PATHS, omit_facility=False,
              omit_table=False, table_is_directory=False, output_is_directory=False,
              facility_is_directory=False, beside=None, facility_unreadable=False,
              table_unreadable=False, output_directory_unwritable=False,
              output_readonly=False, file_owners=None):
    _RUN_COUNTER[0] += 1
    tree = SANDBOX_ROOT / ("run%03d" % _RUN_COUNTER[0])
    if tree.exists():
        shutil.rmtree(tree)
    facility_name, table_name, output_name = names
    tree.mkdir(parents=True)
    for name in (facility_name, table_name, output_name):
        (tree / name).parent.mkdir(parents=True, exist_ok=True)
    if output_is_directory:
        (tree / output_name).mkdir()
    elif existing is not None:
        (tree / output_name).write_bytes(existing)
    (tree / "schedbuild").write_bytes(BUILDER.binary)
    (tree / "schedbuild").chmod(0o755)
    if facility_is_directory:
        (tree / facility_name).mkdir()
    elif not omit_facility:
        (tree / facility_name).write_bytes(facility)
    if table_is_directory:
        (tree / table_name).mkdir()
    elif not omit_table:
        (tree / table_name).write_bytes(table)
    # Papers of the desk's that happen to be filed in the same directories the run works in.
    for name, blob in sorted((beside or {}).items()):
        (tree / name).parent.mkdir(parents=True, exist_ok=True)
        (tree / name).write_bytes(blob)
    own(tree)
    # Readable papers may belong to another account even though their parents
    # allow the builder to rename entries. Apply these fixtures after own(tree)
    # so a byte-identical replacement cannot silently inherit the expected owner.
    for name, (uid, gid) in (file_owners or {}).items():
        os.chown(tree / name, uid, gid)
        (tree / name).chmod(0o644)
    # Modes the running account cannot get past, set once the tree is its own: an operand it
    # cannot read and a directory it cannot write in are refusals of 10.1 like any other.
    if facility_unreadable:
        (tree / facility_name).chmod(0)
    if table_unreadable:
        (tree / table_name).chmod(0)
    if output_directory_unwritable:
        (tree / output_name).parent.chmod(0o555)
    # A schedule the desk filed read only. The directory it stands in is writable, so a run
    # that files its work by renaming it into place replaces it as 9.5 says, and a run that
    # opens the standing file and writes into it cannot, which is the difference 1.8 draws.
    if output_readonly:
        (tree / output_name).chmod(0o444)
    return tree


def invoke(tree, operands, fsize_limit=None):
    return run_unprivileged(["./schedbuild"] + list(operands), tree, RUN_TIMEOUT,
                            fsize_limit=fsize_limit)


def _remember_created(tree, folder, name, names):
    """A created name under the run tree stays relative. One in a temporary directory
    is remembered by its full path so a later run can put another user's file there."""
    full = folder / name
    try:
        recorded = str(full.relative_to(tree))
    except ValueError:
        recorded = str(full)
        GLOBAL_COLLISIONS.add(recorded)
    names.add(recorded)


def _creation_watch_dirs(tree):
    """The run tree, and the temporary directories a fixed-name stage actually uses.

    os.TempDir follows TMPDIR. /tmp and /var/tmp are the other places a builder
    reaches for when it wants a scratch name outside the run tree. Device
    directories are not watched: a pty created while the program runs is not a
    staging name.
    """
    found = []
    seen = set()

    def add(folder):
        folder = Path(folder)
        if folder.is_symlink() or not folder.is_dir():
            return
        key = os.path.realpath(folder)
        if key in seen:
            return
        seen.add(key)
        found.append(folder)

    add(tree)
    for path in tree.rglob("*"):
        if path.is_dir() and not path.is_symlink():
            add(path)
    for root in (Path(BUILD_ENV["TMPDIR"]), Path("/tmp"), Path("/var/tmp")):
        if not root.is_dir():
            continue
        for base, folders, _ in os.walk(str(root), topdown=True, onerror=lambda error: None):
            here = Path(base)
            add(here)
            folders[:] = [name for name in folders
                          if not os.path.islink(os.path.join(base, name))
                          and enterable(here / name)]
    return found


def invoke_observing_creations(tree, operands):
    """Learn paths created in the run tree and in the system temporary directories.

    Linux inotify queues events before the builder starts; no timing or polling
    window has to catch a short-lived stage. Names select later collision fixtures,
    never the expected schedule, diagnostic, or preserved bytes. An unnamed stage
    is not a refusal. A name outside the run tree is planted afterwards as another
    user's file; preservation of that file is what rejects a fixed-name stage.
    """
    libc = ctypes.CDLL(None, use_errno=True)
    libc.inotify_init1.argtypes = [ctypes.c_int]
    libc.inotify_init1.restype = ctypes.c_int
    libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
    libc.inotify_add_watch.restype = ctypes.c_int
    fd = libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
    if fd < 0:
        raise OSError(ctypes.get_errno(), "cannot open creation observer")
    try:
        # IN_CREATE | IN_MOVED_TO. Relative paths stay relative so a discovered
        # cwd stage is restaged in cwd, not issued/.
        watches = {}
        for folder in _creation_watch_dirs(tree):
            watch = libc.inotify_add_watch(fd, os.fsencode(folder), 0x180)
            if watch < 0:
                raise OSError(ctypes.get_errno(), "cannot watch %s" % folder)
            watches[watch] = folder
        result = invoke(tree, operands)
        names = set()
        while True:
            try:
                events = os.read(fd, 65536)
            except BlockingIOError:
                break
            if not events:
                break
            offset = 0
            while offset < len(events):
                wd, mask, _, length = struct.unpack_from("iIII", events, offset)
                start = offset + 16
                name = os.fsdecode(events[start:start + length].rstrip(b"\0"))
                offset = start + length
                # Queue metadata has no filename. Directories are not file fixtures.
                if wd in watches and mask & 0x180 and not mask & 0x40000000 and name:
                    _remember_created(tree, watches[wd], name, names)
        names.discard(str(Path(operands[2])))
        return result, sorted(names)
    finally:
        os.close(fd)


NOT_A_FILE = "an entry at the output path that is not a regular file"
LAST_RUN = {"left": []}


def record(path):
    """What stands at one path: the kind of entry it is, the whole of what it holds, and the
    mode and account it is filed under. Nothing here stands in for anything else, so a run that
    leaves the bytes of a directory's contents alone and changes the mode that directory is
    filed under has still changed it."""
    try:
        return standing(path)
    except OSError:
        return ("unreadable", None, None)


def standing(path):
    facts = path.lstat()
    filed = (stat.S_IMODE(facts.st_mode), facts.st_uid, facts.st_gid)
    if stat.S_ISLNK(facts.st_mode):
        return ("link", os.readlink(str(path)), filed)
    if stat.S_ISDIR(facts.st_mode):
        return ("directory", None, filed)
    if not stat.S_ISREG(facts.st_mode):
        return ("other", None, filed)
    return ("file", hashlib.sha256(path.read_bytes()).hexdigest(), filed)


def enterable(folder):
    """Whether the running account can enter a directory. It can leave nothing in one it cannot
    enter, and nothing below one either, which is the only ground on which the walk below ever
    stops short of anywhere."""
    try:
        facts = folder.stat()
    except OSError:
        return False
    if facts.st_uid == SANDBOX_UID:
        return bool(facts.st_mode & 0o100)
    if facts.st_gid == SANDBOX_GID:
        return bool(facts.st_mode & 0o010)
    return bool(facts.st_mode & 0o001)


def close_off(folder):
    """Makes a directory root's own and enterable by nobody else. The account is given the tree
    of the run being graded and the temporary directories and nothing else: what it has finished
    with is closed behind it, so that the walk passes it by because the account cannot reach it
    and not because the verifier has chosen to look away."""
    try:
        os.chown(str(folder), 0, 0)
        os.chmod(str(folder), 0o700)
    except OSError:
        pass


def writable_rooms():
    """Every directory the running account can write in, found by walking the image rather than
    by naming the conventional few, so that a builder staging its work in /var/tmp, or in any
    other corner the account can reach, answers for it as surely as one staging it beside the
    output path. The walk goes everywhere the account itself could go, to any depth, and stops
    only where the account would be stopped. The two trees left out are the kernel's own, which
    hold no file a run could leave behind; the goldens, the tool directory and the reward are
    closed to the account by their modes and so are passed over by that same rule, which
    test_the_build_account_reaches_neither_the_goldens_nor_the_reward checks from the account's
    own side."""
    rooms = []
    for base, folders, _ in os.walk("/", topdown=True, onerror=lambda error: None):
        folders[:] = [name for name in folders
                      if os.path.join(base, name) not in ("/proc", "/sys")
                      and not os.path.islink(os.path.join(base, name))
                      and enterable(Path(base) / name)]
        try:
            facts = os.stat(base)
        except OSError:
            continue
        if (facts.st_mode & 0o002
                or (facts.st_uid == SANDBOX_UID and facts.st_mode & 0o200)
                or (facts.st_gid == SANDBOX_GID and facts.st_mode & 0o020)):
            rooms.append(base)
            # Nothing below a room needs its own entry: the room is walked whole.
            folders[:] = []
    return tuple(Path(room) for room in rooms)


for _spent in ("build", "gocache", "gopath"):
    if (SANDBOX_ROOT / _spent).is_dir():
        close_off(SANDBOX_ROOT / _spent)


SPILL = writable_rooms()


# A fixed scratch name in the system temporary directory. Section 1.7 already
# requires every pre-existing file to stand, whoever owns it. This one is owned
# by root and mode 0644. /sandbox/tmp is mode 0755 with no sticky bit, so the
# sandbox account can remove it through the parent even though it cannot open
# it for writing. Observed names from the temporary directories join this set.
GLOBAL_COLLISIONS = {str(Path(BUILD_ENV["TMPDIR"]) / "schedbuild.tmp")}


def plant_root_kept(path):
    """Put another user's ordinary file at path. The bytes are not a schedule."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.path.lexists(path):
        if path.is_dir() and not path.is_symlink():
            os.rmdir(path)
        else:
            os.unlink(path)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    try:
        os.write(fd, b"KEEP\n")
    finally:
        os.close(fd)
    os.chown(str(path), 0, 0)
    os.chmod(str(path), 0o644)


def arm_global_collisions():
    """Restore the preserved temporary files before the before-snapshot.

    Called only on that side. The after-snapshot must see a file the run removed
    or replaced, and must not put the file back first.
    """
    for name in sorted(GLOBAL_COLLISIONS):
        plant_root_kept(name)


def gather(room, found):
    found[str(room)] = record(room)
    for base, folders, files in os.walk(str(room), topdown=True, onerror=lambda error: None):
        for name in folders + files:
            path = Path(base) / name
            # Section 1.7 keeps contents, mode, UID and GID, whoever owns the entry.
            # Skipping another user's file is how a fixed-name stage in a writable,
            # non-sticky directory deletes it and still looks clean.
            found[str(path)] = record(path)
        # Recorded first, then walked into: an entry the account cannot enter is still an entry
        # it could have changed, and only what lies below it is out of its reach.
        folders[:] = [name for name in folders if enterable(Path(base) / name)]


def snapshot(tree):
    """Every entry under the run tree, and every entry in a directory the account can write.

    A file staged beside the output, in the starting directory, or among the temporary
    files is the same kind of entry. Ownership does not take an entry out of the picture.
    """
    found = {}
    gather(tree, found)
    for room in SPILL:
        if room.is_dir():
            gather(room, found)
    return found


def residue(before, after, spared, status=0):
    """What one run left behind it.

    A completing run may replace the schedule, so that path is set aside when
    the status is 0. Any other ending leaves the file as it stood: the same
    bytes, mode, UID and GID. Sections 9.5 and 1.7.
    """
    kept = str(spared)
    left = ["made " + name for name in sorted(set(after) - set(before)) if name != kept]
    left += ["took away " + name for name in sorted(set(before) - set(after)) if name != kept]
    for name in sorted(set(before) & set(after)):
        if before[name] == after[name]:
            continue
        if name == kept and status == 0:
            continue
        left.append("disturbed " + name)
    return left


def standing_at(path):
    if not os.path.lexists(path):
        return None
    if path.is_file() and not path.is_symlink():
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return None
    return NOT_A_FILE


def run_case(facility, table, existing=None, names=PATHS, output=None,
             observe_creations=False, **staging):
    require_binary()
    tree = stage_run(facility, table, existing=existing, names=names, **staging)
    target = output or names[2]
    landed = tree / target
    arm_global_collisions()
    before = snapshot(tree)
    operands = (names[0], names[1], target)
    LAST_RUN["created in run tree"] = []
    if observe_creations:
        result, created = invoke_observing_creations(tree, operands)
        LAST_RUN["created in run tree"] = created
        code, stdout, stderr = result
    else:
        code, stdout, stderr = invoke(tree, operands)
    LAST_RUN["left"] = residue(before, snapshot(tree), landed, code)
    written = standing_at(landed)
    # Whether an empty directory still stands at the output path, which is what a refused run
    # leaves where it found one. Anything put inside it is already reported as made.
    LAST_RUN["empty directory"] = landed.is_dir() and not landed.is_symlink()
    close_off(tree)
    return code, stdout, stderr, written


def build_case(name):
    facility, table, expected = CASES[name]
    code, stdout, stderr, written = run_case(facility, table)
    assert (code, stderr) == (0, b""), (
        "case %s was refused with %r" % (name, stderr[:300]))
    assert stdout == b"", "case %s wrote to standard output" % name
    assert written is not None and written is not NOT_A_FILE, "case %s wrote no schedule" % name
    assert LAST_RUN["left"] == [], "case %s also left %s" % (name, LAST_RUN["left"])
    return written, expected


def first_difference(left, right):
    for position in range(min(len(left), len(right))):
        if left[position] != right[position]:
            return position
    return min(len(left), len(right))


def parse(blob):
    text = blob.decode("latin-1")
    assert text.endswith("\n"), "the schedule does not end in a line feed"
    lines = text[:-1].split("\n")
    header = lines[0].split(" ")
    trailer = lines[-1].split(" ")
    rows = []
    for line in lines[1:-1]:
        fields = line.split(" ")
        assert len(fields) == 7, "a schedule line carries %d fields: %r" % (len(fields), line)
        rows.append({
            "number": fields[0], "begin": fields[1], "end": fields[2],
            "days": int(fields[3]), "balance": int(fields[4]),
            "interest": int(fields[5]), "principal": int(fields[6]),
            "widths": [len(field) for field in fields],
        })
    return {"header": header, "rows": rows, "trailer": trailer}


def thirty_us(first, second):
    """Paragraph 6.6, used only to show that a fixture reaches the geometry it was built for."""
    y1, m1, d1 = (int(part) for part in first.split("-"))
    y2, m2, d2 = (int(part) for part in second.split("-"))
    if d1 == 31:
        d1 = 30
    if d2 == 31 and d1 == 30:
        d2 = 30
    return 360 * (y2 - y1) + 30 * (m2 - m1) + (d2 - d1)


SHIPPED_TABLE = CASES["crossley"][1]
STALE = b"the schedule the desk issued at drawdown\n" * 30


def generated_traps():
    """The four traps too long to keep in the table: a fault on the line after a comment far
    past any read buffer, one after three thousand ignorable lines, one on the last of four
    hundred step lines, and one in a table of long comments."""
    built = (("long_comment", "facility", "bad step"),
             ("many_ignorable", "facility", "bad step"),
             ("late_step_fault", "facility", "bad step"),
             ("wide_table", "basis", "malformed entry"))
    for maker, side, reason in built:
        facility, table = getattr(GENERATORS, maker)()
        where = getattr(GENERATORS, maker + "_line")()
        yield {"facility": facility.decode("latin-1"), "table": table.decode("latin-1"),
               "status": 1, "output": "none",
               "stderr": "schedbuild: %s line %d: %s\n" % (side, where, reason)}


def count_sweep(facility, table):
    """Paragraph 1.2 across a generated range and larger boundary counts."""
    usage = b"schedbuild: usage: schedbuild <facility-file> <basis-table> <output-file>\n"
    wrong = []
    counts = [count for count in range(65) if count != 3]
    counts += [127, 128, 129, 255, 256, 257, 1024]
    for count in counts:
        operands = (PATHS[:count] if count < 3 else
                    PATHS + tuple("extra-%d" % index for index in range(count - 3)))
        tree = stage_run(facility, table)
        arm_global_collisions()
        before = snapshot(tree)
        code, stdout, stderr = invoke(tree, operands)
        left = residue(before, snapshot(tree), tree / PATHS[2], code)
        got = (code, stdout, stderr, standing_at(tree / PATHS[2]), left)
        want = (2, b"", usage, None, [])
        if got != want:
            wrong.append({"operand count": count, "expected": want, "actual": got})
        close_off(tree)
    return wrong


def publish_over_held_file(facility, table, expected, names, existing=None):
    """Paragraph 1.8's last sentence, held without a signal or a timing window. The verifier
    opens the file standing at the output path read only before the run and keeps that
    descriptor to itself. A builder that renames finished work into place leaves the bytes of
    that file as they were. One that truncates it, or writes the schedule into it, changes the
    very file the descriptor holds, however quickly the copy finishes, and a kill between that
    truncation and that copy would have left nothing where the file stood."""
    tree = stage_run(facility, table, existing=existing, names=names)
    target = tree / names[2]
    original = standing_at(target)
    arm_global_collisions()
    before = snapshot(tree)
    held = os.open(str(target), os.O_RDONLY | os.O_NOFOLLOW)
    try:
        code, stdout, stderr = invoke(tree, names)
        facts = os.fstat(held)
        kept = b""
        while True:
            chunk = os.pread(held, 65536, len(kept))
            if not chunk:
                break
            kept += chunk
        try:
            now = os.lstat(target)
            still_there = (now.st_dev, now.st_ino) == (facts.st_dev, facts.st_ino)
        except OSError:
            still_there = False
    finally:
        os.close(held)
    written = standing_at(target)
    left = residue(before, snapshot(tree), target, code)
    close_off(tree)
    assert not (still_there and kept != original), {
        "operands": names, "status": code,
        "the file at the output path was written in place": True,
        "original bytes": len(original), "bytes that file holds now": len(kept),
        "prefix": kept[:100]}
    assert (code, stdout, stderr, written, left) == (0, b"", b"", expected, []), (
        names, code, stderr[:200], left)


def fsize_sweep(facility, table, expected, names=PATHS):
    """A write failing at its first byte, partway and at its last, over a schedule already
    filed and over nothing, and then one with exactly enough room to finish."""
    stale = b"the schedule the desk issued at drawdown\n"
    wrong = []
    room = [("no room for the first byte", 0, None), ("room for one byte", 1, None),
            ("room for half the schedule", len(expected) // 2, None),
            ("room for every byte but the last", len(expected) - 1, None),
            ("exactly enough room", len(expected), expected)]
    for label, limit, finishes in room:
        standings = (stale, None) if finishes is None and names[2] not in names[:2] else (stale,)
        for standing in standings:
            tree = stage_run(facility, table, existing=standing, names=names)
            original = standing_at(tree / names[2])
            arm_global_collisions()
            before = snapshot(tree)
            code, stdout, stderr = invoke(tree, names, fsize_limit=limit)
            want = (0, b"", b"") if finishes else (1, b"", b"schedbuild: cannot write output file\n")
            stood = finishes if finishes else original
            left = residue(before, snapshot(tree), tree / names[2], code)
            if (code, stdout, stderr, standing_at(tree / names[2]), left) != want + (stood, []):
                wrong.append((label, code, stderr[:120], left))
            close_off(tree)
    return wrong


def destroy_sweep(facility, table, size, digest, names=PATHS):
    """Paragraph 1.8 under destruction, over each thing that can stand at the output path: a
    schedule already filed, nothing, and an empty directory. Thirty offsets fifty microseconds
    apart, four further out, and three killed the instant the output path stops being what it
    was, whenever the builder brings that about. An empty directory cannot be replaced by one
    rename, so a builder that clears it away first opens exactly the gap the last three close
    on: the path carrying nothing although something stood there."""
    stale = b"the schedule the desk issued at drawdown\n" * 30
    pauses = [0.00005 * step for step in range(30)] + [0.003, 0.006, 0.010, 0.020]
    wrong = []
    layouts = (("a schedule already filed", stale, False),
               ("nothing", None, False), ("an empty directory", NOT_A_FILE, True))
    if names[2] in names[:2]:
        layouts = (("an aliased input", facility if names[2] == names[0] else table, False),)
    for label, stood, folder in layouts:
        runs = [(pause, "killed %g seconds in" % pause) for pause in pauses]
        runs += [(None, "killed as it filed its schedule")] * 3
        for pause, how in runs:
            tree = stage_run(facility, table, existing=None if folder else stood, names=names,
                             output_is_directory=folder)
            if pause is None:
                invoke_and_destroy_at_publication(tree, names, stood)
            else:
                invoke_and_destroy(tree, names, pause)
            landed = tree / names[2]
            written = standing_at(landed)
            if folder:
                as_it_was = landed.is_dir() and not landed.is_symlink() and not any(
                    landed.iterdir())
            else:
                as_it_was = written == stood
            close_off(tree)
            if as_it_was:
                continue
            if (written is None or written is NOT_A_FILE or len(written) != size
                    or hashlib.sha256(written).hexdigest() != digest):
                wrong.append((label, how, len(written) if isinstance(written, bytes) else written))
        # Paragraph 1.8. Sleeps and a poll can both miss the gap they are aimed at.
        # Each of these stops is a syscall return, before the next syscall.
        if label == "nothing":
            tree = stage_run(facility, table, existing=None, names=names)
            invoke_and_stop_when_the_output_is_opened(tree, names)
            how = "stopped when the absent output was opened"
        elif not folder and stood is not None:
            tree = stage_run(facility, table, existing=stood, names=names)
            invoke_and_stop_when_the_existing_output_is_removed(tree, names)
            how = "stopped when the existing output was removed"
        else:
            continue
        written = standing_at(tree / names[2])
        close_off(tree)
        if written != stood and (written is None or written is NOT_A_FILE or len(written) != size
                                 or hashlib.sha256(written).hexdigest() != digest):
            wrong.append((label, how, len(written) if isinstance(written, bytes) else written))
    return wrong


def account_probe():
    """What the dropped account can reach, asked from its own side."""
    probe = SANDBOX_ROOT / "probe"
    if probe.exists():
        shutil.rmtree(probe)
    probe.mkdir(parents=True)
    own(probe)
    script = ("id -u; ls /tests || echo TESTS_DENIED; ls /app || echo APP_DENIED; "
              "cat /logs/verifier/reward.txt || echo REWARD_DENIED; "
              "ls /sandbox/build || echo BUILD_DENIED; "
              "ls /sandbox/gocache || echo CACHE_DENIED; "
              "ls /sandbox/gopath || echo MODULES_DENIED; "
              "(sleep 300 >/dev/null 2>&1 &) ; echo SPAWNED")
    code, stdout, stderr = run_unprivileged(["/bin/sh", "-c", script], probe, RUN_TIMEOUT)
    close_off(probe)
    return stdout.decode("utf-8", "replace")


def first_trap():
    """One work the note refuses, taken from the head of the table, so that the tests wanting a
    refusal and the table itself cannot drift apart."""
    rec = TRAPS[0]
    table = SHIPPED_TABLE if rec.get("table") is None else rec["table"].encode("latin-1")
    return rec["facility"].encode("latin-1"), table, rec["stderr"].encode("latin-1")


def apply_trap(rec):
    """Runs one trap and holds the builder to the disposition the note gives it: the status,
    the one diagnostic line, an empty standard output, what stands at the output path, and
    nothing left anywhere the account can write. Returns None where the run answered, and what
    it did instead where it did not."""
    facility = rec["facility"].encode("latin-1")
    table = SHIPPED_TABLE if rec.get("table") is None else rec["table"].encode("latin-1")
    staging = dict(rec.get("staging") or {})
    existing = STALE if staging.pop("existing", False) else None
    # Operands given as positions in PATHS, so one path can stand for more than one of them.
    if "names" in staging:
        staging["names"] = tuple(PATHS[which] for which in staging["names"])
    code, stdout, stderr, written = run_case(facility, table, existing=existing, **staging)
    output = rec["output"]
    if output.startswith("schedule:"):
        stood = CASES[output.split(":", 1)[1]][2]
    else:
        stood = {"none": None, "stale": STALE, "directory": NOT_A_FILE,
                 "facility": facility, "table": table}[output]
    want = (rec["status"], b"", rec["stderr"].encode("latin-1"), stood, [])
    kept = output != "directory" or LAST_RUN["empty directory"]
    if (code, stdout, stderr, written, LAST_RUN["left"]) == want and kept:
        return None
    return {"trap": rec.get("label", rec["stderr"].strip()), "staged": rec.get("staging"),
            "status": code,
            "stdout": stdout[:60], "stderr": stderr[:160],
            "at the output path": "a schedule" if written else written,
            "left behind": LAST_RUN["left"]}


def digest_cases():
    for name in ("wardlow", "yarnfield"):
        facility, table, _ = CASES[name]
        code, stdout, stderr, written = run_case(facility, table)
        assert (code, stdout, stderr) == (0, b"", b""), (name, stdout[:200], stderr[:300])
        assert LAST_RUN["left"] == [], (name, LAST_RUN["left"])
        size, digest = DIGESTS[name]
        assert written is not None and len(written) == size, (name, len(written or b""))
        assert hashlib.sha256(written).hexdigest() == digest, name


def start_run(tree, operands, out, err):
    dropped = ["/usr/bin/setpriv", "--reuid", str(SANDBOX_UID), "--regid", str(SANDBOX_GID),
               "--clear-groups", "--no-new-privs", "--", "./schedbuild"] + list(operands)
    return subprocess.Popen(dropped, cwd=str(tree), env=BUILD_ENV, stdout=out, stderr=err,
                            start_new_session=True)


def destroy(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        pass
    if live_sandbox_pids():
        subprocess.run(["/usr/bin/pkill", "-9", "-u", str(SANDBOX_UID)], capture_output=True)
    drain_sandbox()
    return process.returncode


def invoke_and_destroy(tree, operands, pause):
    """Starts a run and kills it outright after the given pause, which is what 1.8 has to hold
    against: the signal cannot be taken, so nothing the builder registered for its own way out
    ever runs."""
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        process = start_run(tree, operands, out, err)
        time.sleep(pause)
        return destroy(process)


# aarch64 openat / openat2. The stop is the syscall return, which is before the
# builder can issue the write. SECCOMP_RET_TRACE reports only those calls, so the
# rest of the run is not single-stepped.
_PTRACE_TRACEME = 0
_PTRACE_PEEKDATA = 2
_PTRACE_CONT = 7
_PTRACE_SYSCALL = 24
_PTRACE_SETOPTIONS = 0x4200
_PTRACE_GETREGSET = 0x4204
_NT_PRSTATUS = 1
_PTRACE_O_TRACESYSGOOD = 1
_PTRACE_O_TRACEFORK = 2
_PTRACE_O_TRACEVFORK = 4
_PTRACE_O_TRACECLONE = 8
_PTRACE_O_TRACEEXEC = 0x10
_PTRACE_O_TRACESECCOMP = 0x80
_PTRACE_O_EXITKILL = 0x100000
_PTRACE_EVENT_SECCOMP = 7
_PR_SET_NO_NEW_PRIVS = 38
_PR_SET_SECCOMP = 22
_SECCOMP_MODE_FILTER = 2
_SECCOMP_RET_ALLOW = 0x7FFF0000
_SECCOMP_RET_TRACE = 0x7FF00000
_O_ACCMODE = 3
_WALL = 0x40000000


class _SockFilter(ctypes.Structure):
    _fields_ = [("code", ctypes.c_uint16), ("jt", ctypes.c_uint8),
                ("jf", ctypes.c_uint8), ("k", ctypes.c_uint32)]


class _SockFprog(ctypes.Structure):
    _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.POINTER(_SockFilter))]


class _ArmRegs(ctypes.Structure):
    _fields_ = [("regs", ctypes.c_uint64 * 31), ("sp", ctypes.c_uint64),
                ("pc", ctypes.c_uint64), ("pstate", ctypes.c_uint64)]


class _X86Regs(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in (
        "r15", "r14", "r13", "r12", "rbp", "rbx", "r11", "r10", "r9", "r8",
        "rax", "rcx", "rdx", "rsi", "rdi", "orig_rax", "rip", "cs", "eflags", "rsp", "ss",
        "fs_base", "gs_base", "ds", "es", "fs", "gs")]


class _Iovec(ctypes.Structure):
    _fields_ = [("iov_base", ctypes.c_void_p), ("iov_len", ctypes.c_size_t)]


def _open_tracer():
    libc = ctypes.CDLL(None, use_errno=True)
    libc.ptrace.argtypes = [ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
    libc.ptrace.restype = ctypes.c_long
    libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
                           ctypes.c_ulong, ctypes.c_ulong]
    libc.prctl.restype = ctypes.c_int
    libc.waitpid.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_int), ctypes.c_int]
    libc.waitpid.restype = ctypes.c_int
    return libc


def _install_open_trace(libc, audit_arch, watched):
    watched = [number for number in watched if number is not None]
    filt = [
        _SockFilter(0x20, 0, 0, 4),
        _SockFilter(0x15, 1, 0, audit_arch),
        _SockFilter(0x06, 0, 0, _SECCOMP_RET_ALLOW),
        _SockFilter(0x20, 0, 0, 0),
    ]
    count = len(watched)
    for index, number in enumerate(watched):
        filt.append(_SockFilter(0x15, count - index, 0, number))
    filt.append(_SockFilter(0x06, 0, 0, _SECCOMP_RET_ALLOW))
    filt.append(_SockFilter(0x06, 0, 0, _SECCOMP_RET_TRACE))
    filters = (_SockFilter * len(filt))(*filt)
    prog = _SockFprog(len(filt), filters)
    if libc.prctl(_PR_SET_SECCOMP, _SECCOMP_MODE_FILTER, ctypes.addressof(prog), 0, 0) != 0:
        os._exit(121)


def _native_open_numbers():
    """Open and unlink numbers for this kernel. The words are read with ptrace."""
    machine = os.uname().machine
    if machine in ("aarch64", "arm64"):
        return 0xC00000B7, 56, 437, 35, None, "arm"
    if machine in ("x86_64", "amd64"):
        return 0xC000003E, 257, 437, 263, 87, "x86"
    raise OSError("the creation stop has no syscall numbers for %s" % machine)


def _load_regs(libc, tid, kind):
    regs = _ArmRegs() if kind == "arm" else _X86Regs()
    iov = _Iovec()
    iov.iov_base = ctypes.addressof(regs)
    iov.iov_len = ctypes.sizeof(regs)
    if libc.ptrace(_PTRACE_GETREGSET, tid, _NT_PRSTATUS, ctypes.addressof(iov)) != 0:
        raise OSError(ctypes.get_errno(), "cannot read registers")
    return regs


def _traced_args(libc, tid, kind):
    regs = _load_regs(libc, tid, kind)
    if kind == "arm":
        return int(regs.regs[8]), int(regs.regs[0]), int(regs.regs[1]), int(regs.regs[2])
    return int(regs.orig_rax), int(regs.rdi), int(regs.rsi), int(regs.rdx)


def _syscall_result(libc, tid, kind):
    regs = _load_regs(libc, tid, kind)
    word = int(regs.regs[0]) if kind == "arm" else int(regs.rax)
    word &= 0xFFFFFFFFFFFFFFFF
    if word >= 2 ** 63:
        word -= 2 ** 64
    return word


def _traced_cstr(libc, tid, addr):
    out = bytearray()
    addr &= 0xFFFFFFFFFFFFFFFF
    while len(out) < 4096:
        ctypes.set_errno(0)
        word = libc.ptrace(_PTRACE_PEEKDATA, tid, addr, 0)
        if word == -1 and ctypes.get_errno():
            return None
        raw = int(word & 0xFFFFFFFFFFFFFFFF).to_bytes(8, "little")
        for byte in raw:
            if byte == 0:
                return out.decode("latin-1", "replace")
            out.append(byte)
        addr += 8
    return out.decode("latin-1", "replace")


def _names_output(path, tree, operand):
    if path is None:
        return False
    want = os.path.normpath(operand)
    got = os.path.normpath(path)
    absolute = os.path.normpath(os.path.join(str(tree), want))
    return got == want or got == absolute


def _opens_output_for_writing(libc, tid, tree, operand, openat, openat2, kind):
    number, _dirfd, path_addr, third = _traced_args(libc, tid, kind)
    if number == openat:
        flags = third
        path = _traced_cstr(libc, tid, path_addr)
    elif number == openat2:
        path = _traced_cstr(libc, tid, path_addr)
        ctypes.set_errno(0)
        word = libc.ptrace(_PTRACE_PEEKDATA, tid, third, 0)
        if word == -1 and ctypes.get_errno():
            return False
        flags = int(word & 0xFFFFFFFFFFFFFFFF)
    else:
        return False
    if path is None or (flags & _O_ACCMODE) == 0:
        return False
    return _names_output(path, tree, operand)


def _unlinks_output(libc, tid, tree, operand, unlinkat, unlink, kind):
    number, first, second, _flags = _traced_args(libc, tid, kind)
    if number == unlinkat:
        path = _traced_cstr(libc, tid, second)
    elif unlink is not None and number == unlink:
        path = _traced_cstr(libc, tid, first)
    else:
        return False
    return _names_output(path, tree, operand)


def invoke_and_stop_when_the_output_is_opened(tree, operands):
    """Paragraph 1.8 for an output path that does not yet exist."""
    _invoke_and_stop(tree, operands, "open")


def invoke_and_stop_when_the_existing_output_is_removed(tree, operands):
    """Paragraph 1.8 for an output path that already holds a file.

    The builder is stopped when unlink of that path returns success, and it is
    killed before the next syscall. A rename onto the existing file never unlinks
    it, so that run is left to finish. A remove followed by a rename is stopped
    while the path carries nothing, which a sleep or a poll does not guarantee.
    """
    _invoke_and_stop(tree, operands, "unlink")


def _invoke_and_stop(tree, operands, which):
    libc = _open_tracer()
    output = operands[2]
    audit_arch, openat, openat2, unlinkat, unlink, kind = _native_open_numbers()
    leader = os.fork()
    if leader == 0:
        try:
            if libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
                os._exit(120)
            _install_open_trace(libc, audit_arch, (openat, openat2, unlinkat, unlink))
            if libc.ptrace(_PTRACE_TRACEME, 0, 0, 0) != 0:
                os._exit(122)
            os.setsid()
            os.chdir(tree)
            os.environ.clear()
            os.environ.update(BUILD_ENV)
            os.execv("/usr/bin/setpriv", [
                "/usr/bin/setpriv", "--reuid", str(SANDBOX_UID), "--regid", str(SANDBOX_GID),
                "--clear-groups", "--no-new-privs", "--", "./schedbuild", *operands])
        finally:
            os._exit(123)
    status_box = ctypes.c_int()
    waited = libc.waitpid(leader, ctypes.byref(status_box), 0)
    if waited != leader or not os.WIFSTOPPED(status_box.value):
        raise OSError(ctypes.get_errno(), "the creation stop did not arm")
    options = (_PTRACE_O_TRACESECCOMP | _PTRACE_O_TRACECLONE | _PTRACE_O_TRACEFORK
               | _PTRACE_O_TRACEVFORK | _PTRACE_O_TRACEEXEC | _PTRACE_O_EXITKILL
               | _PTRACE_O_TRACESYSGOOD)
    if libc.ptrace(_PTRACE_SETOPTIONS, leader, 0, options) != 0:
        raise OSError(ctypes.get_errno(), "cannot trace the builder")
    if libc.ptrace(_PTRACE_CONT, leader, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "cannot continue the builder")
    armed = None
    deadline = time.monotonic() + 60.0
    while time.monotonic() < deadline:
        waited = libc.waitpid(-1, ctypes.byref(status_box), _WALL | os.WNOHANG)
        if waited == 0:
            time.sleep(0.005)
            continue
        if waited < 0:
            if ctypes.get_errno() == 10:
                break
            raise OSError(ctypes.get_errno(), "waiting for the builder")
        state = status_box.value
        if os.WIFEXITED(state) or os.WIFSIGNALED(state):
            if waited == leader:
                break
            continue
        if not os.WIFSTOPPED(state):
            continue
        event = (state >> 16) & 0xFFFF
        if isinstance(armed, tuple) and armed[0] == waited:
            if armed[1] == "unlink" and _syscall_result(libc, waited, kind) != 0:
                armed = None
                if libc.ptrace(_PTRACE_CONT, waited, 0, 0) != 0:
                    raise OSError(ctypes.get_errno(), "cannot continue after a failed unlink")
                continue
            try:
                os.kill(-leader, signal.SIGKILL)
            except ProcessLookupError:
                pass
            break
        watched = None
        if event == _PTRACE_EVENT_SECCOMP and which == "open" and _opens_output_for_writing(
                libc, waited, tree, output, openat, openat2, kind):
            watched = "open"
        elif event == _PTRACE_EVENT_SECCOMP and which == "unlink" and _unlinks_output(
                libc, waited, tree, output, unlinkat, unlink, kind):
            watched = "unlink"
        if watched is not None:
            armed = (waited, watched)
            if libc.ptrace(_PTRACE_SYSCALL, waited, 0, 0) != 0:
                raise OSError(ctypes.get_errno(), "cannot step the publication call")
            continue
        sig = os.WSTOPSIG(state)
        injected = sig if event == 0 and sig not in (signal.SIGTRAP, signal.SIGSTOP) else 0
        if libc.ptrace(_PTRACE_CONT, waited, 0, injected) != 0:
            raise OSError(ctypes.get_errno(), "cannot continue a traced thread")
    else:
        try:
            os.kill(-leader, signal.SIGKILL)
        except ProcessLookupError:
            pass
        raise TimeoutError("the creation stop did not finish")
    for _ in range(100):
        waited = libc.waitpid(-1, ctypes.byref(status_box), _WALL | os.WNOHANG)
        if waited <= 0:
            break
    if live_sandbox_pids():
        subprocess.run(["/usr/bin/pkill", "-9", "-u", str(SANDBOX_UID)], capture_output=True)
    drain_sandbox()


def invoke_and_destroy_at_publication(tree, operands, stood, patience=30.0):
    """Starts a run and kills it the instant the output path stops being what it was, rather
    than after a delay of the verifier's own choosing. A builder that files its schedule by
    renaming it into place has already finished writing every byte when that instant arrives,
    so the signal can only land after the work is whole. A builder that opens the output path
    and writes the schedule into it reaches that instant as soon as it has opened the file, so
    the signal lands inside the window where half a schedule stands there, and it lands there
    however long the builder waited first."""
    watched = tree / operands[2]
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        process = start_run(tree, operands, out, err)
        deadline = time.monotonic() + patience
        while time.monotonic() < deadline:
            if standing_at(watched) != stood:
                break
            if process.poll() is not None:
                break
        return destroy(process)
