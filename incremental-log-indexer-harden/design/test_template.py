import os
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path

import pytest

SANDBOX_UID = 2000
SANDBOX_GID = 2000
SOURCE = Path("/app/logindex")
SEALED = Path("/tests/sealed")
WORK = Path("/sandbox/work")
BUILD_TIMEOUT = 300
DEP_FORMAT = "{{if not .Standard}}{{if not .Module}}{{.ImportPath}}{{else if not .Module.Main}}{{.ImportPath}}{{end}}{{end}}"
RUN_TIMEOUT = 10
GO_ENV = {
    "PATH": "/usr/local/go/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "HOME": "/home/sandbox",
    "GOCACHE": "/sandbox/gocache",
    "GOPATH": "/sandbox/gopath",
    "GOTOOLCHAIN": "local",
    "GOFLAGS": "-mod=mod",
    "GOPROXY": "off",
    "CGO_ENABLED": "0",
    "LANG": "C.UTF-8",
}


def run_unprivileged(command, cwd, timeout, feed=b""):
    with tempfile.TemporaryFile() as stdin, tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        stdin.write(feed)
        stdin.seek(0)
        process = subprocess.Popen(
            command,
            cwd=str(cwd),
            env=GO_ENV,
            stdin=stdin,
            stdout=out,
            stderr=err,
            user=SANDBOX_UID,
            group=SANDBOX_GID,
            extra_groups=[],
            start_new_session=True,
        )
        timed_out = False
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                pass
        out.seek(0)
        err.seek(0)
        return {
            "timed_out": timed_out,
            "returncode": process.returncode,
            "stdout": out.read().decode("utf-8", "replace"),
            "stderr": err.read().decode("utf-8", "replace"),
        }


def load_sealed():
    cases = []
    for journal in sorted(SEALED.glob("*.journal")):
        answers = journal.with_suffix(".answers")
        cases.append({
            "name": "held-out " + journal.stem,
            "journal": journal.read_text(),
            "answers": answers.read_text(),
            "refuses": "a program that departs from the notes on a journal it has not seen",
        })
    return cases


@pytest.fixture(scope="session", autouse=True)
def held_out():
    cases = load_sealed()
    assert len(cases) >= 20, "the held-out journals are missing"
    probe = sorted(SEALED.glob("*.answers"))[0]
    for target in (str(probe), str(SEALED), "/logs/verifier/reward.txt"):
        result = run_unprivileged(["cat", target], "/", 30)
        assert result["returncode"] != 0 and result["stdout"] == "", "the sandbox account can read " + target
    return cases


@pytest.fixture(scope="session")
def program(held_out):
    assert SOURCE.is_dir(), "/app/logindex is missing"
    if WORK.exists():
        shutil.rmtree(WORK)
    src = WORK / "src"
    shutil.copytree(SOURCE, src, symlinks=True)
    (WORK / "bin").mkdir()
    (WORK / "run").mkdir()
    subprocess.run(["chown", "-R", "%d:%d" % (SANDBOX_UID, SANDBOX_GID), str(WORK)], check=True)
    binary = WORK / "bin" / "logindex"
    result = run_unprivileged(["go", "build", "-o", str(binary), "."], src, BUILD_TIMEOUT)
    assert not result["timed_out"], "go build did not finish within %d seconds" % BUILD_TIMEOUT
    assert result["returncode"] == 0 and binary.is_file(), "go build failed:\n" + result["stderr"][-4000:]
    deps = run_unprivileged(["go", "list", "-deps", "-f", DEP_FORMAT, "."], src, BUILD_TIMEOUT)
    outside = deps["stdout"].split()
    assert deps["returncode"] == 0 and not outside, (
        "the program uses packages outside the Go standard library: " + " ".join(outside) + deps["stderr"][-2000:])
    return binary


def check(program, *witnesses):
    failures = []
    for witness in witnesses:
        result = run_unprivileged([str(program)], WORK / "run", RUN_TIMEOUT, witness["journal"].encode())
        if result["timed_out"]:
            failures.append("%s: no answer within %d seconds" % (witness["name"], RUN_TIMEOUT))
        elif result["returncode"] != 0:
            failures.append("%s: exit status %d" % (witness["name"], result["returncode"]))
        elif result["stdout"] != witness["answers"]:
            failures.append("%s (refuses %s)\nexpected:\n%sgot:\n%s" % (
                witness["name"], witness["refuses"], witness["answers"], result["stdout"][:2000]))
    assert not failures, "\n\n".join(failures)
