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


ONE_LINE_JOURNAL_PRINTS_NOTHING = {
    "name": 'ONE_LINE_JOURNAL_PRINTS_NOTHING',
    "journal": 'stopwords\n',
    "answers": '',
    "refuses": 'a program that prints a line when the journal holds no query',
}

EMPTY_STOP_LIST_KEEPS_EVERY_WORD = {
    "name": 'EMPTY_STOP_LIST_KEEPS_EVERY_WORD',
    "journal": 'stopwords\nbegin\nadd n-1 disk on fire\ncommit\nquery 0 0 on\nquery 0 0 disk on fire\n',
    "answers": 'n-1@0\nn-1@0\n',
    "refuses": 'a built-in stop list used when the first line lists none',
}

THE_WORD_STOPWORDS_IS_NOT_A_STOP_WORD = {
    "name": 'THE_WORD_STOPWORDS_IS_NOT_A_STOP_WORD',
    "journal": 'stopwords of\nbegin\nadd s-1 stopwords list reloaded\ncommit\nquery 0 0 stopwords\nquery 0 0 stopwords list\n',
    "answers": 's-1@0\ns-1@0\n',
    "refuses": 'the leading word of the first line read as a stop word',
}

TWENTY_STOP_WORDS_WITH_A_TWELVE_LETTER_ONE = {
    "name": 'TWENTY_STOP_WORDS_WITH_A_TWELVE_LETTER_ONE',
    "journal": 'stopwords a an and are as at be by for from in is it of on or the to was acknowledged\nbegin\nadd al-1 alert raised for disk\ncommit\nquery 0 0 acknowledged alert\nquery 0 0 alert acknowledged\nquery 0 0 was disk\n',
    "answers": 'al-1@0\nal-1@0\nal-1@0\n',
    "refuses": 'a stop list cut short before its twentieth word or its twelfth letter',
}

FOUR_HUNDRED_LINES_ARE_ALL_READ = {
    "name": 'FOUR_HUNDRED_LINES_ARE_ALL_READ',
    "journal": 'stopwords\nbegin\nadd d-1 version 0 rolled out\nadd d-1 version 1 rolled out\nadd d-1 version 2 rolled out\nadd d-1 version 3 rolled out\nadd d-1 version 4 rolled out\nadd d-1 version 5 rolled out\nadd d-1 version 6 rolled out\nadd d-1 version 7 rolled out\nadd d-1 version 8 rolled out\nadd d-1 version 9 rolled out\nadd d-1 version 10 rolled out\nadd d-1 version 11 rolled out\nadd d-1 version 12 rolled out\nadd d-1 version 13 rolled out\nadd d-1 version 14 rolled out\nadd d-1 version 15 rolled out\nadd d-1 version 16 rolled out\nadd d-1 version 17 rolled out\nadd d-1 version 18 rolled out\nadd d-1 version 19 rolled out\nadd d-1 version 20 rolled out\nadd d-1 version 21 rolled out\nadd d-1 version 22 rolled out\nadd d-1 version 23 rolled out\nadd d-1 version 24 rolled out\nadd d-1 version 25 rolled out\nadd d-1 version 26 rolled out\nadd d-1 version 27 rolled out\nadd d-1 version 28 rolled out\nadd d-1 version 29 rolled out\nadd d-1 version 30 rolled out\nadd d-1 version 31 rolled out\nadd d-1 version 32 rolled out\nadd d-1 version 33 rolled out\nadd d-1 version 34 rolled out\nadd d-1 version 35 rolled out\nadd d-1 version 36 rolled out\nadd d-1 version 37 rolled out\nadd d-1 version 38 rolled out\nadd d-1 version 39 rolled out\nadd d-1 version 40 rolled out\nadd d-1 version 41 rolled out\nadd d-1 version 42 rolled out\nadd d-1 version 43 rolled out\nadd d-1 version 44 rolled out\nadd d-1 version 45 rolled out\nadd d-1 version 46 rolled out\nadd d-1 version 47 rolled out\nadd d-1 version 48 rolled out\nadd d-1 version 49 rolled out\nadd d-1 version 50 rolled out\nadd d-1 version 51 rolled out\nadd d-1 version 52 rolled out\nadd d-1 version 53 rolled out\nadd d-1 version 54 rolled out\nadd d-1 version 55 rolled out\nadd d-1 version 56 rolled out\nadd d-1 version 57 rolled out\nadd d-1 version 58 rolled out\nadd d-1 version 59 rolled out\nadd d-1 version 60 rolled out\nadd d-1 version 61 rolled out\nadd d-1 version 62 rolled out\nadd d-1 version 63 rolled out\nadd d-1 version 64 rolled out\nadd d-1 version 65 rolled out\nadd d-1 version 66 rolled out\nadd d-1 version 67 rolled out\nadd d-1 version 68 rolled out\nadd d-1 version 69 rolled out\nadd d-1 version 70 rolled out\nadd d-1 version 71 rolled out\nadd d-1 version 72 rolled out\nadd d-1 version 73 rolled out\nadd d-1 version 74 rolled out\nadd d-1 version 75 rolled out\nadd d-1 version 76 rolled out\nadd d-1 version 77 rolled out\nadd d-1 version 78 rolled out\nadd d-1 version 79 rolled out\nadd d-1 version 80 rolled out\nadd d-1 version 81 rolled out\nadd d-1 version 82 rolled out\nadd d-1 version 83 rolled out\nadd d-1 version 84 rolled out\nadd d-1 version 85 rolled out\nadd d-1 version 86 rolled out\nadd d-1 version 87 rolled out\nadd d-1 version 88 rolled out\nadd d-1 version 89 rolled out\nadd d-1 version 90 rolled out\nadd d-1 version 91 rolled out\nadd d-1 version 92 rolled out\nadd d-1 version 93 rolled out\nadd d-1 version 94 rolled out\nadd d-1 version 95 rolled out\nadd d-1 version 96 rolled out\nadd d-1 version 97 rolled out\nadd d-1 version 98 rolled out\nadd d-1 version 99 rolled out\nadd d-1 version 100 rolled out\nadd d-1 version 101 rolled out\nadd d-1 version 102 rolled out\nadd d-1 version 103 rolled out\nadd d-1 version 104 rolled out\nadd d-1 version 105 rolled out\nadd d-1 version 106 rolled out\nadd d-1 version 107 rolled out\nadd d-1 version 108 rolled out\nadd d-1 version 109 rolled out\nadd d-1 version 110 rolled out\nadd d-1 version 111 rolled out\nadd d-1 version 112 rolled out\nadd d-1 version 113 rolled out\nadd d-1 version 114 rolled out\nadd d-1 version 115 rolled out\nadd d-1 version 116 rolled out\nadd d-1 version 117 rolled out\nadd d-1 version 118 rolled out\nadd d-1 version 119 rolled out\nadd d-1 version 120 rolled out\nadd d-1 version 121 rolled out\nadd d-1 version 122 rolled out\nadd d-1 version 123 rolled out\nadd d-1 version 124 rolled out\nadd d-1 version 125 rolled out\nadd d-1 version 126 rolled out\nadd d-1 version 127 rolled out\nadd d-1 version 128 rolled out\nadd d-1 version 129 rolled out\nadd d-1 version 130 rolled out\nadd d-1 version 131 rolled out\nadd d-1 version 132 rolled out\nadd d-1 version 133 rolled out\nadd d-1 version 134 rolled out\nadd d-1 version 135 rolled out\nadd d-1 version 136 rolled out\nadd d-1 version 137 rolled out\nadd d-1 version 138 rolled out\nadd d-1 version 139 rolled out\nadd d-1 version 140 rolled out\nadd d-1 version 141 rolled out\nadd d-1 version 142 rolled out\nadd d-1 version 143 rolled out\nadd d-1 version 144 rolled out\nadd d-1 version 145 rolled out\nadd d-1 version 146 rolled out\nadd d-1 version 147 rolled out\nadd d-1 version 148 rolled out\nadd d-1 version 149 rolled out\nadd d-1 version 150 rolled out\nadd d-1 version 151 rolled out\nadd d-1 version 152 rolled out\nadd d-1 version 153 rolled out\nadd d-1 version 154 rolled out\nadd d-1 version 155 rolled out\nadd d-1 version 156 rolled out\nadd d-1 version 157 rolled out\nadd d-1 version 158 rolled out\nadd d-1 version 159 rolled out\nadd d-1 version 160 rolled out\nadd d-1 version 161 rolled out\nadd d-1 version 162 rolled out\nadd d-1 version 163 rolled out\nadd d-1 version 164 rolled out\nadd d-1 version 165 rolled out\nadd d-1 version 166 rolled out\nadd d-1 version 167 rolled out\nadd d-1 version 168 rolled out\nadd d-1 version 169 rolled out\nadd d-1 version 170 rolled out\nadd d-1 version 171 rolled out\nadd d-1 version 172 rolled out\nadd d-1 version 173 rolled out\nadd d-1 version 174 rolled out\nadd d-1 version 175 rolled out\nadd d-1 version 176 rolled out\nadd d-1 version 177 rolled out\nadd d-1 version 178 rolled out\nadd d-1 version 179 rolled out\nadd d-1 version 180 rolled out\nadd d-1 version 181 rolled out\nadd d-1 version 182 rolled out\nadd d-1 version 183 rolled out\nadd d-1 version 184 rolled out\nadd d-1 version 185 rolled out\nadd d-1 version 186 rolled out\nadd d-1 version 187 rolled out\nadd d-1 version 188 rolled out\nadd d-1 version 189 rolled out\nadd d-1 version 190 rolled out\nadd d-1 version 191 rolled out\nadd d-1 version 192 rolled out\nadd d-1 version 193 rolled out\nadd d-1 version 194 rolled out\nadd d-1 version 195 rolled out\nadd d-1 version 196 rolled out\nadd d-1 version 197 rolled out\nadd d-1 version 198 rolled out\nadd d-1 version 199 rolled out\nadd d-1 version 200 rolled out\nadd d-1 version 201 rolled out\nadd d-1 version 202 rolled out\nadd d-1 version 203 rolled out\nadd d-1 version 204 rolled out\nadd d-1 version 205 rolled out\nadd d-1 version 206 rolled out\nadd d-1 version 207 rolled out\nadd d-1 version 208 rolled out\nadd d-1 version 209 rolled out\nadd d-1 version 210 rolled out\nadd d-1 version 211 rolled out\nadd d-1 version 212 rolled out\nadd d-1 version 213 rolled out\nadd d-1 version 214 rolled out\nadd d-1 version 215 rolled out\nadd d-1 version 216 rolled out\nadd d-1 version 217 rolled out\nadd d-1 version 218 rolled out\nadd d-1 version 219 rolled out\nadd d-1 version 220 rolled out\nadd d-1 version 221 rolled out\nadd d-1 version 222 rolled out\nadd d-1 version 223 rolled out\nadd d-1 version 224 rolled out\nadd d-1 version 225 rolled out\nadd d-1 version 226 rolled out\nadd d-1 version 227 rolled out\nadd d-1 version 228 rolled out\nadd d-1 version 229 rolled out\nadd d-1 version 230 rolled out\nadd d-1 version 231 rolled out\nadd d-1 version 232 rolled out\nadd d-1 version 233 rolled out\nadd d-1 version 234 rolled out\nadd d-1 version 235 rolled out\nadd d-1 version 236 rolled out\nadd d-1 version 237 rolled out\nadd d-1 version 238 rolled out\nadd d-1 version 239 rolled out\nadd d-1 version 240 rolled out\nadd d-1 version 241 rolled out\nadd d-1 version 242 rolled out\nadd d-1 version 243 rolled out\nadd d-1 version 244 rolled out\nadd d-1 version 245 rolled out\nadd d-1 version 246 rolled out\nadd d-1 version 247 rolled out\nadd d-1 version 248 rolled out\nadd d-1 version 249 rolled out\nadd d-1 version 250 rolled out\nadd d-1 version 251 rolled out\nadd d-1 version 252 rolled out\nadd d-1 version 253 rolled out\nadd d-1 version 254 rolled out\nadd d-1 version 255 rolled out\nadd d-1 version 256 rolled out\nadd d-1 version 257 rolled out\nadd d-1 version 258 rolled out\nadd d-1 version 259 rolled out\nadd d-1 version 260 rolled out\nadd d-1 version 261 rolled out\nadd d-1 version 262 rolled out\nadd d-1 version 263 rolled out\nadd d-1 version 264 rolled out\nadd d-1 version 265 rolled out\nadd d-1 version 266 rolled out\nadd d-1 version 267 rolled out\nadd d-1 version 268 rolled out\nadd d-1 version 269 rolled out\nadd d-1 version 270 rolled out\nadd d-1 version 271 rolled out\nadd d-1 version 272 rolled out\nadd d-1 version 273 rolled out\nadd d-1 version 274 rolled out\nadd d-1 version 275 rolled out\nadd d-1 version 276 rolled out\nadd d-1 version 277 rolled out\nadd d-1 version 278 rolled out\nadd d-1 version 279 rolled out\nadd d-1 version 280 rolled out\nadd d-1 version 281 rolled out\nadd d-1 version 282 rolled out\nadd d-1 version 283 rolled out\nadd d-1 version 284 rolled out\nadd d-1 version 285 rolled out\nadd d-1 version 286 rolled out\nadd d-1 version 287 rolled out\nadd d-1 version 288 rolled out\nadd d-1 version 289 rolled out\nadd d-1 version 290 rolled out\nadd d-1 version 291 rolled out\nadd d-1 version 292 rolled out\nadd d-1 version 293 rolled out\nadd d-1 version 294 rolled out\nadd d-1 version 295 rolled out\nadd d-1 version 296 rolled out\nadd d-1 version 297 rolled out\nadd d-1 version 298 rolled out\nadd d-1 version 299 rolled out\nadd d-1 version 300 rolled out\nadd d-1 version 301 rolled out\nadd d-1 version 302 rolled out\nadd d-1 version 303 rolled out\nadd d-1 version 304 rolled out\nadd d-1 version 305 rolled out\nadd d-1 version 306 rolled out\nadd d-1 version 307 rolled out\nadd d-1 version 308 rolled out\nadd d-1 version 309 rolled out\nadd d-1 version 310 rolled out\nadd d-1 version 311 rolled out\nadd d-1 version 312 rolled out\nadd d-1 version 313 rolled out\nadd d-1 version 314 rolled out\nadd d-1 version 315 rolled out\nadd d-1 version 316 rolled out\nadd d-1 version 317 rolled out\nadd d-1 version 318 rolled out\nadd d-1 version 319 rolled out\nadd d-1 version 320 rolled out\nadd d-1 version 321 rolled out\nadd d-1 version 322 rolled out\nadd d-1 version 323 rolled out\nadd d-1 version 324 rolled out\nadd d-1 version 325 rolled out\nadd d-1 version 326 rolled out\nadd d-1 version 327 rolled out\nadd d-1 version 328 rolled out\nadd d-1 version 329 rolled out\nadd d-1 version 330 rolled out\nadd d-1 version 331 rolled out\nadd d-1 version 332 rolled out\nadd d-1 version 333 rolled out\nadd d-1 version 334 rolled out\nadd d-1 version 335 rolled out\nadd d-1 version 336 rolled out\nadd d-1 version 337 rolled out\nadd d-1 version 338 rolled out\nadd d-1 version 339 rolled out\nadd d-1 version 340 rolled out\nadd d-1 version 341 rolled out\nadd d-1 version 342 rolled out\nadd d-1 version 343 rolled out\nadd d-1 version 344 rolled out\nadd d-1 version 345 rolled out\nadd d-1 version 346 rolled out\nadd d-1 version 347 rolled out\nadd d-1 version 348 rolled out\nadd d-1 version 349 rolled out\nadd d-1 version 350 rolled out\nadd d-1 version 351 rolled out\nadd d-1 version 352 rolled out\nadd d-1 version 353 rolled out\nadd d-1 version 354 rolled out\nadd d-1 version 355 rolled out\nadd d-1 version 356 rolled out\nadd d-1 version 357 rolled out\nadd d-1 version 358 rolled out\nadd d-1 version 359 rolled out\nadd d-1 version 360 rolled out\nadd d-1 version 361 rolled out\nadd d-1 version 362 rolled out\nadd d-1 version 363 rolled out\nadd d-1 version 364 rolled out\nadd d-1 version 365 rolled out\nadd d-1 version 366 rolled out\nadd d-1 version 367 rolled out\nadd d-1 version 368 rolled out\nadd d-1 version 369 rolled out\nadd d-1 version 370 rolled out\nadd d-1 version 371 rolled out\nadd d-1 version 372 rolled out\nadd d-1 version 373 rolled out\nadd d-1 version 374 rolled out\nadd d-1 version 375 rolled out\nadd d-1 version 376 rolled out\nadd d-1 version 377 rolled out\nadd d-1 version 378 rolled out\nadd d-1 version 379 rolled out\nadd d-1 version 380 rolled out\nadd d-1 version 381 rolled out\nadd d-1 version 382 rolled out\nadd d-1 version 383 rolled out\nadd d-1 version 384 rolled out\nadd d-1 version 385 rolled out\nadd d-1 version 386 rolled out\nadd d-1 version 387 rolled out\nadd d-1 version 388 rolled out\nadd d-1 version 389 rolled out\nadd d-1 version 390 rolled out\nadd d-1 version 391 rolled out\nadd d-1 version 392 rolled out\nadd d-1 version 393 rolled out\nadd d-1 version 394 rolled out\ncommit\nquery 0 0 version 0 rolled\nquery 0 0 version 394 rolled\n',
    "answers": '-\nd-1@0\n',
    "refuses": 'a reader that stops before the four hundredth line',
}

LONGEST_STOP_WORD_LINE = {
    "name": 'LONGEST_STOP_WORD_LINE',
    "journal": 'stopwords abcdefghijkl hijklmnopqrs opqrstuvwxyz vwxyzabcdefg cdefghijklmn jklmnopqrstu qrstuvwxyzab xyzabcdefghi efghijklmnop lmnopqrstuvw stuvwxyzabcd zabcdefghijk ghijklmnopqr nopqrstuvwxy uvwxyzabcdef bcdefghijklm ijklmnopqrst pqrstuvwxyza wxyzabcdefgh defghijklmno\nbegin\nadd ls-1 disk full\ncommit\nquery 0 0 defghijklmno disk full\nquery 0 0 disk abcdefghijkl full\n',
    "answers": 'ls-1@0\n-\n',
    "refuses": 'a first line read only up to a fixed length, losing its last stop words',
}


def test_journal_lines_and_stop_word_line(program):
    """R1 and R2: every line is read and the first line sets the stop words."""
    check(program, ONE_LINE_JOURNAL_PRINTS_NOTHING, EMPTY_STOP_LIST_KEEPS_EVERY_WORD, THE_WORD_STOPWORDS_IS_NOT_A_STOP_WORD, TWENTY_STOP_WORDS_WITH_A_TWELVE_LETTER_ONE, FOUR_HUNDRED_LINES_ARE_ALL_READ, LONGEST_STOP_WORD_LINE)


ID_IS_NOT_PART_OF_THE_TEXT = {
    "name": 'ID_IS_NOT_PART_OF_THE_TEXT',
    "journal": 'stopwords\nbegin\nadd web-1 disk full\nappend web-1 again\ncommit\nquery 0 0 web\nquery 0 0 1\nquery 0 0 full again\n',
    "answers": '-\n-\nweb-1@0\n',
    "refuses": 'the id read as the first words of the text',
}

SLOP_AND_FUZZ_ARE_NOT_QUERY_WORDS = {
    "name": 'SLOP_AND_FUZZ_ARE_NOT_QUERY_WORDS',
    "journal": 'stopwords\nbegin\nadd m-1 disk 3 full 1 node\ncommit\nquery 3 1 disk full\nquery 1 0 full node\n',
    "answers": 'm-1@1\nm-1@1\n',
    "refuses": 'the slop or the fuzz read as words of the query',
}

TEXT_MAY_BEGIN_AND_END_WITH_SPACES = {
    "name": 'TEXT_MAY_BEGIN_AND_END_WITH_SPACES',
    "journal": 'stopwords\nbegin\nadd sp-1    disk    full   \ncommit\nquery 0 0   disk full  \n',
    "answers": 'sp-1@0\n',
    "refuses": 'a text that loses its tokens when it begins or ends with spaces',
}

ID_OF_SIXTEEN_CHARACTERS = {
    "name": 'ID_OF_SIXTEEN_CHARACTERS',
    "journal": 'stopwords\nbegin\nadd abcdefghij-12345 disk full\nadd a disk\ncommit\nquery 0 0 disk\n',
    "answers": 'a@0 abcdefghij-12345@0\n',
    "refuses": 'an id cut short or rewritten in the output',
}

TEXT_OF_TWO_HUNDRED_CHARACTERS = {
    "name": 'TEXT_OF_TWO_HUNDRED_CHARACTERS',
    "journal": 'stopwords\nbegin\nadd long-1 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx tail end\ncommit\nquery 0 0 tail end\nquery 0 0                                                                                                                 tail end\n',
    "answers": 'long-1@0\nlong-1@0\n',
    "refuses": 'a line buffer that drops the end of a long text',
}

SLOP_NINE_AND_FUZZ_TWO_AT_THEIR_ENDS = {
    "name": 'SLOP_NINE_AND_FUZZ_TWO_AT_THEIR_ENDS',
    "journal": 'stopwords\nbegin\nadd e-1 alpha one two three four five six seven eight nine beta\ncommit\nquery 9 2 alpah bteaa\nquery 8 2 alpah bteaa\nquery 9 1 alpah bteaa\n',
    "answers": 'e-1@9\n-\n-\n',
    "refuses": 'a slop or fuzz read with a smaller range than the notes give',
}

APPEND_TEXT_OF_TWO_HUNDRED_CHARACTERS = {
    "name": 'APPEND_TEXT_OF_TWO_HUNDRED_CHARACTERS',
    "journal": 'stopwords\nbegin\nadd at-1 x\nappend at-1                                                                                                                                                                                                        z\ncommit\nquery 0 0 z\nquery 0 0 x z\n',
    "answers": 'at-1@0\nat-1@0\n',
    "refuses": 'an append text cut short below its two hundred characters',
}

IDS_MAY_START_WITH_A_DIGIT_OR_A_HYPHEN = {
    "name": 'IDS_MAY_START_WITH_A_DIGIT_OR_A_HYPHEN',
    "journal": 'stopwords\nbegin\nadd - disk\nadd 9 disk\nadd 0-a disk\nadd z disk\ncommit\nquery 0 0 disk\n',
    "answers": '-@0 0-a@0 9@0 z@0\n',
    "refuses": 'an id that is expected to start with a letter',
}

LONGEST_ADD_AND_APPEND_LINES = {
    "name": 'LONGEST_ADD_AND_APPEND_LINES',
    "journal": 'stopwords\nbegin\nadd abcdefghij-12345 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx tail end\nappend abcdefghij-12345 yyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyy final\ncommit\nquery 0 0 tail end\nquery 0 0 final\nquery 1 0 end final\n',
    "answers": 'abcdefghij-12345@0\nabcdefghij-12345@0\nabcdefghij-12345@1\n',
    "refuses": 'a line buffer shorter than the longest add or append line',
}


def test_line_fields_ids_slop_and_fuzz(program):
    """R3: ids, slop, fuzz and text are taken from the right fields at their full lengths."""
    check(program, ID_IS_NOT_PART_OF_THE_TEXT, SLOP_AND_FUZZ_ARE_NOT_QUERY_WORDS, TEXT_MAY_BEGIN_AND_END_WITH_SPACES, ID_OF_SIXTEEN_CHARACTERS, TEXT_OF_TWO_HUNDRED_CHARACTERS, SLOP_NINE_AND_FUZZ_TWO_AT_THEIR_ENDS, APPEND_TEXT_OF_TWO_HUNDRED_CHARACTERS, IDS_MAY_START_WITH_A_DIGIT_OR_A_HYPHEN, LONGEST_ADD_AND_APPEND_LINES)


QUERY_BEFORE_ANY_COMMIT_FINDS_NOTHING = {
    "name": 'QUERY_BEFORE_ANY_COMMIT_FINDS_NOTHING',
    "journal": 'stopwords\nquery 9 2 disk\nbegin\nadd d disk\nrollback\nquery 9 2 disk\n',
    "answers": '-\n-\n',
    "refuses": 'an index that holds documents before any commit',
}

EMPTY_BATCH_KEEPS_THE_INDEX = {
    "name": 'EMPTY_BATCH_KEEPS_THE_INDEX',
    "journal": 'stopwords\nbegin\nadd d-1 disk full\ncommit\nbegin\ncommit\nquery 0 0 disk full\n',
    "answers": 'd-1@0\n',
    "refuses": 'a commit of an empty batch that clears the committed index',
}

ROLLBACK_RESTORES_THE_COMMITTED_INDEX = {
    "name": 'ROLLBACK_RESTORES_THE_COMMITTED_INDEX',
    "journal": 'stopwords\nbegin\nadd a-1 disk full\nadd a-2 node down\ncommit\nbegin\ndelete a-1\nadd a-3 disk full\nadd a-2 cpu hot\nrollback\nquery 0 0 disk full\nquery 0 0 node down\nquery 0 0 cpu\n',
    "answers": 'a-1@0\na-2@0\n-\n',
    "refuses": 'a rollback that keeps some of the lines of its batch',
}

ROLLED_BACK_APPEND_LEAVES_THE_DOCUMENT = {
    "name": 'ROLLED_BACK_APPEND_LEAVES_THE_DOCUMENT',
    "journal": 'stopwords\nbegin\nadd r-1 disk\ncommit\nbegin\nappend r-1 full\nrollback\nquery 0 0 disk full\nquery 0 0 disk\n',
    "answers": '-\nr-1@0\n',
    "refuses": "a working copy that shares a document's tokens with the committed index",
}

LINES_OF_A_BATCH_SEE_EARLIER_LINES = {
    "name": 'LINES_OF_A_BATCH_SEE_EARLIER_LINES',
    "journal": 'stopwords\nbegin\nadd b-1 disk\nappend b-1 full\ndelete b-2\nadd b-2 node\ndelete b-2\ncommit\nquery 0 0 disk full\nquery 0 0 node\n',
    "answers": 'b-1@0\n-\n',
    "refuses": 'a batch whose lines are applied out of order or against the committed index',
}

COMMITS_STACK_IN_ORDER = {
    "name": 'COMMITS_STACK_IN_ORDER',
    "journal": 'stopwords\nbegin\nadd c-1 disk\ncommit\nbegin\nadd c-2 disk\ncommit\nquery 0 0 disk\nbegin\ndelete c-1\ncommit\nquery 0 0 disk\n',
    "answers": 'c-1@0 c-2@0\nc-2@0\n',
    "refuses": 'a commit that replaces earlier commits instead of building on them',
}

EMPTY_BATCH_ROLLED_BACK = {
    "name": 'EMPTY_BATCH_ROLLED_BACK',
    "journal": 'stopwords\nbegin\nadd e-1 disk\ncommit\nbegin\nrollback\nquery 0 0 disk\n',
    "answers": 'e-1@0\n',
    "refuses": 'a rollback of an empty batch that clears or breaks the committed index',
}

ONE_HUNDRED_THIRTY_TWO_BATCHES = {
    "name": 'ONE_HUNDRED_THIRTY_TWO_BATCHES',
    "journal": 'stopwords\nbegin\nadd bt-1 word0\ncommit\nbegin\nadd bt-1 word1\ncommit\nbegin\nadd bt-1 word2\ncommit\nbegin\nadd bt-1 word3\ncommit\nbegin\nadd bt-1 word4\ncommit\nbegin\nadd bt-1 word5\ncommit\nbegin\nadd bt-1 word6\ncommit\nbegin\nadd bt-1 word7\ncommit\nbegin\nadd bt-1 word8\ncommit\nbegin\nadd bt-1 word9\ncommit\nbegin\nadd bt-1 word10\ncommit\nbegin\nadd bt-1 word11\ncommit\nbegin\nadd bt-1 word12\ncommit\nbegin\nadd bt-1 word13\ncommit\nbegin\nadd bt-1 word14\ncommit\nbegin\nadd bt-1 word15\ncommit\nbegin\nadd bt-1 word16\ncommit\nbegin\nadd bt-1 word17\ncommit\nbegin\nadd bt-1 word18\ncommit\nbegin\nadd bt-1 word19\ncommit\nbegin\nadd bt-1 word20\ncommit\nbegin\nadd bt-1 word21\ncommit\nbegin\nadd bt-1 word22\ncommit\nbegin\nadd bt-1 word23\ncommit\nbegin\nadd bt-1 word24\ncommit\nbegin\nadd bt-1 word25\ncommit\nbegin\nadd bt-1 word26\ncommit\nbegin\nadd bt-1 word27\ncommit\nbegin\nadd bt-1 word28\ncommit\nbegin\nadd bt-1 word29\ncommit\nbegin\nadd bt-1 word30\ncommit\nbegin\nadd bt-1 word31\ncommit\nbegin\nadd bt-1 word32\ncommit\nbegin\nadd bt-1 word33\ncommit\nbegin\nadd bt-1 word34\ncommit\nbegin\nadd bt-1 word35\ncommit\nbegin\nadd bt-1 word36\ncommit\nbegin\nadd bt-1 word37\ncommit\nbegin\nadd bt-1 word38\ncommit\nbegin\nadd bt-1 word39\ncommit\nbegin\nadd bt-1 word40\ncommit\nbegin\nadd bt-1 word41\ncommit\nbegin\nadd bt-1 word42\ncommit\nbegin\nadd bt-1 word43\ncommit\nbegin\nadd bt-1 word44\ncommit\nbegin\nadd bt-1 word45\ncommit\nbegin\nadd bt-1 word46\ncommit\nbegin\nadd bt-1 word47\ncommit\nbegin\nadd bt-1 word48\ncommit\nbegin\nadd bt-1 word49\ncommit\nbegin\nadd bt-1 word50\ncommit\nbegin\nadd bt-1 word51\ncommit\nbegin\nadd bt-1 word52\ncommit\nbegin\nadd bt-1 word53\ncommit\nbegin\nadd bt-1 word54\ncommit\nbegin\nadd bt-1 word55\ncommit\nbegin\nadd bt-1 word56\ncommit\nbegin\nadd bt-1 word57\ncommit\nbegin\nadd bt-1 word58\ncommit\nbegin\nadd bt-1 word59\ncommit\nbegin\nadd bt-1 word60\ncommit\nbegin\nadd bt-1 word61\ncommit\nbegin\nadd bt-1 word62\ncommit\nbegin\nadd bt-1 word63\ncommit\nbegin\nadd bt-1 word64\ncommit\nbegin\nadd bt-1 word65\ncommit\nbegin\nadd bt-1 word66\ncommit\nbegin\nadd bt-1 word67\ncommit\nbegin\nadd bt-1 word68\ncommit\nbegin\nadd bt-1 word69\ncommit\nbegin\nadd bt-1 word70\ncommit\nbegin\nadd bt-1 word71\ncommit\nbegin\nadd bt-1 word72\ncommit\nbegin\nadd bt-1 word73\ncommit\nbegin\nadd bt-1 word74\ncommit\nbegin\nadd bt-1 word75\ncommit\nbegin\nadd bt-1 word76\ncommit\nbegin\nadd bt-1 word77\ncommit\nbegin\nadd bt-1 word78\ncommit\nbegin\nadd bt-1 word79\ncommit\nbegin\nadd bt-1 word80\ncommit\nbegin\nadd bt-1 word81\ncommit\nbegin\nadd bt-1 word82\ncommit\nbegin\nadd bt-1 word83\ncommit\nbegin\nadd bt-1 word84\ncommit\nbegin\nadd bt-1 word85\ncommit\nbegin\nadd bt-1 word86\ncommit\nbegin\nadd bt-1 word87\ncommit\nbegin\nadd bt-1 word88\ncommit\nbegin\nadd bt-1 word89\ncommit\nbegin\nadd bt-1 word90\ncommit\nbegin\nadd bt-1 word91\ncommit\nbegin\nadd bt-1 word92\ncommit\nbegin\nadd bt-1 word93\ncommit\nbegin\nadd bt-1 word94\ncommit\nbegin\nadd bt-1 word95\ncommit\nbegin\nadd bt-1 word96\ncommit\nbegin\nadd bt-1 word97\ncommit\nbegin\nadd bt-1 word98\ncommit\nbegin\nadd bt-1 word99\ncommit\nbegin\nadd bt-1 word100\ncommit\nbegin\nadd bt-1 word101\ncommit\nbegin\nadd bt-1 word102\ncommit\nbegin\nadd bt-1 word103\ncommit\nbegin\nadd bt-1 word104\ncommit\nbegin\nadd bt-1 word105\ncommit\nbegin\nadd bt-1 word106\ncommit\nbegin\nadd bt-1 word107\ncommit\nbegin\nadd bt-1 word108\ncommit\nbegin\nadd bt-1 word109\ncommit\nbegin\nadd bt-1 word110\ncommit\nbegin\nadd bt-1 word111\ncommit\nbegin\nadd bt-1 word112\ncommit\nbegin\nadd bt-1 word113\ncommit\nbegin\nadd bt-1 word114\ncommit\nbegin\nadd bt-1 word115\ncommit\nbegin\nadd bt-1 word116\ncommit\nbegin\nadd bt-1 word117\ncommit\nbegin\nadd bt-1 word118\ncommit\nbegin\nadd bt-1 word119\ncommit\nbegin\nadd bt-1 word120\ncommit\nbegin\nadd bt-1 word121\ncommit\nbegin\nadd bt-1 word122\ncommit\nbegin\nadd bt-1 word123\ncommit\nbegin\nadd bt-1 word124\ncommit\nbegin\nadd bt-1 word125\ncommit\nbegin\nadd bt-1 word126\ncommit\nbegin\nadd bt-1 word127\ncommit\nbegin\nadd bt-1 word128\ncommit\nbegin\nadd bt-1 word129\ncommit\nbegin\nadd bt-1 word130\ncommit\nbegin\nadd bt-1 word131\ncommit\nquery 0 0 word131\nquery 0 0 word130\n',
    "answers": 'bt-1@0\n-\n',
    "refuses": 'a program that stops taking commits after a fixed number of batches',
}


def test_batches_commit_and_roll_back(program):
    """R4 and R5: a batch changes the committed index only when it commits."""
    check(program, QUERY_BEFORE_ANY_COMMIT_FINDS_NOTHING, EMPTY_BATCH_KEEPS_THE_INDEX, ROLLBACK_RESTORES_THE_COMMITTED_INDEX, ROLLED_BACK_APPEND_LEAVES_THE_DOCUMENT, LINES_OF_A_BATCH_SEE_EARLIER_LINES, COMMITS_STACK_IN_ORDER, EMPTY_BATCH_ROLLED_BACK, ONE_HUNDRED_THIRTY_TWO_BATCHES)


PUNCTUATION_SEPARATES_TOKENS = {
    "name": 'PUNCTUATION_SEPARATES_TOKENS',
    "journal": 'stopwords\nbegin\nadd p-1 error:disk_full@node[7]\ncommit\nquery 0 0 disk full\nquery 0 0 full node 7\n',
    "answers": 'p-1@0\np-1@0\n',
    "refuses": 'a split on spaces alone, or on spaces and a few marks',
}

UNDERSCORE_SEPARATES_TOKENS = {
    "name": 'UNDERSCORE_SEPARATES_TOKENS',
    "journal": 'stopwords\nbegin\nadd u-1 worker_pool_3 stalled\ncommit\nquery 0 0 pool 3 stalled\nquery 0 0 worker_pool_3\n',
    "answers": 'u-1@0\nu-1@0\n',
    "refuses": 'word characters taken as letters, digits and underscore',
}

LETTERS_ARE_LOWERCASED = {
    "name": 'LETTERS_ARE_LOWERCASED',
    "journal": 'stopwords\nbegin\nadd l-1 FATAL Error In NodeAgent\ncommit\nquery 0 0 fatal error\nquery 0 0 IN nodeagent\n',
    "answers": 'l-1@0\nl-1@0\n',
    "refuses": 'tokens compared with their case kept',
}

DIGITS_STAY_IN_TOKENS = {
    "name": 'DIGITS_STAY_IN_TOKENS',
    "journal": 'stopwords\nbegin\nadd g-1 sda1 failed at 07\ncommit\nquery 0 0 sda1\nquery 0 0 sda\nquery 0 0 failed at 07\n',
    "answers": 'g-1@0\n-\ng-1@0\n',
    "refuses": 'digits treated as separators or rewritten',
}

TEXT_WITH_NO_TOKEN = {
    "name": 'TEXT_WITH_NO_TOKEN',
    "journal": 'stopwords\nbegin\nadd z-1 -- ... --\nadd z-2 disk\ncommit\nquery 9 2 disk\n',
    "answers": 'z-2@0\n',
    "refuses": 'a text with no token that gains one',
}

EVERY_PRINTABLE_MARK_SEPARATES_TOKENS = {
    "name": 'EVERY_PRINTABLE_MARK_SEPARATES_TOKENS',
    "journal": 'stopwords\nbegin\nadd all-1 x0!x1"x2#x3$x4%x5&x6\'x7(x8)x9*x10+x11,x12-x13.x14/x15:x16;x17<x18=x19>x20?x21@x22[x23\\x24]x25^x26_x27`x28{x29|x30}x31~x32\ncommit\nquery 0 0 x0!x1\nquery 0 0 x1"x2\nquery 0 0 x2#x3\nquery 0 0 x3$x4\nquery 0 0 x4%x5\nquery 0 0 x5&x6\nquery 0 0 x6\'x7\nquery 0 0 x7(x8\nquery 0 0 x8)x9\nquery 0 0 x9*x10\nquery 0 0 x10+x11\nquery 0 0 x11,x12\nquery 0 0 x12-x13\nquery 0 0 x13.x14\nquery 0 0 x14/x15\nquery 0 0 x15:x16\nquery 0 0 x16;x17\nquery 0 0 x17<x18\nquery 0 0 x18=x19\nquery 0 0 x19>x20\nquery 0 0 x20?x21\nquery 0 0 x21@x22\nquery 0 0 x22[x23\nquery 0 0 x23\\x24\nquery 0 0 x24]x25\nquery 0 0 x25^x26\nquery 0 0 x26_x27\nquery 0 0 x27`x28\nquery 0 0 x28{x29\nquery 0 0 x29|x30\nquery 0 0 x30}x31\nquery 0 0 x31~x32\n',
    "answers": 'all-1@0\nall-1@0\nall-1@0\nall-1@0\nall-1@0\nall-1@0\nall-1@0\nall-1@0\nall-1@0\nall-1@0\nall-1@0\nall-1@0\nall-1@0\nall-1@0\nall-1@0\nall-1@0\nall-1@0\nall-1@0\nall-1@0\nall-1@0\nall-1@0\nall-1@0\nall-1@0\nall-1@0\nall-1@0\nall-1@0\nall-1@0\nall-1@0\nall-1@0\nall-1@0\nall-1@0\nall-1@0\n',
    "refuses": 'a tokenizer that splits on a chosen list of marks and keeps any other mark inside a token',
}

APOSTROPHE_SEPARATES_TOKENS = {
    "name": 'APOSTROPHE_SEPARATES_TOKENS',
    "journal": "stopwords\nbegin\nadd ap-1 disk'full: don't retry\ncommit\nquery 0 0 disk full\nquery 0 0 don t retry\nquery 0 0 don't\n",
    "answers": 'ap-1@0\nap-1@0\nap-1@0\n',
    "refuses": 'a word pattern that keeps apostrophes inside tokens',
}

LONG_TOKENS_ARE_KEPT_WHOLE = {
    "name": 'LONG_TOKENS_ARE_KEPT_WHOLE',
    "journal": 'stopwords\nbegin\nadd lw-1 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaax\nadd lw-2 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaay\ncommit\nquery 0 0 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaax\n',
    "answers": 'lw-1@0\n',
    "refuses": 'tokens cut to a fixed length, so two long tokens that differ late become equal',
}

TOKEN_LONGER_THAN_A_QUERY_TEXT_IS_KEPT_WHOLE = {
    "name": 'TOKEN_LONGER_THAN_A_QUERY_TEXT_IS_KEPT_WHOLE',
    "journal": 'stopwords\nbegin\nadd tw-1 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaab\nadd tw-2 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\ncommit\nquery 0 0 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\nquery 0 1 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\nquery 0 2 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n',
    "answers": '-\ntw-1@0\ntw-1@0\n',
    "refuses": 'tokens cut to the length of the longest query text',
}

EVERY_LETTER_AND_DIGIT_AT_BOTH_ENDS_OF_ITS_RANGE = {
    "name": 'EVERY_LETTER_AND_DIGIT_AT_BOTH_ENDS_OF_ITS_RANGE',
    "journal": 'stopwords\nbegin\nadd up-1 ABCDEFGHIJKLMNOPQRSTUVWXYZ\nadd lo-1 abcdefghijklmnopqrstuvwxyz\nadd dg-1 0123456789\nadd c-a A\nadd c-z Z\nadd l-a a\nadd l-z z\nadd d-0 0\nadd d-9 9\nadd mix-1 zA9 Az0\ncommit\nquery 0 0 abcdefghijklmnopqrstuvwxyz\nquery 0 0 ABCDEFGHIJKLMNOPQRSTUVWXYZ\nquery 0 0 0123456789\nquery 0 0 z\nquery 0 0 A\nquery 0 0 Z\nquery 0 0 a\nquery 0 0 0\nquery 0 0 9\nquery 0 0 za9 az0\n',
    "answers": 'lo-1@0 up-1@0\nlo-1@0 up-1@0\ndg-1@0\nc-z@0 l-z@0\nc-a@0 l-a@0\nc-z@0 l-z@0\nc-a@0 l-a@0\nd-0@0\nd-9@0\nmix-1@0\n',
    "refuses": 'a letter or digit range that stops one short of A, Z, a, z, 0 or 9, in reading or in lowercasing',
}


def test_tokens_split_on_punctuation_and_lowercase(program):
    """R6: tokens are runs of letters and digits, lowercased."""
    check(program, PUNCTUATION_SEPARATES_TOKENS, UNDERSCORE_SEPARATES_TOKENS, LETTERS_ARE_LOWERCASED, DIGITS_STAY_IN_TOKENS, TEXT_WITH_NO_TOKEN, EVERY_PRINTABLE_MARK_SEPARATES_TOKENS, APOSTROPHE_SEPARATES_TOKENS, LONG_TOKENS_ARE_KEPT_WHOLE, TOKEN_LONGER_THAN_A_QUERY_TEXT_IS_KEPT_WHOLE, EVERY_LETTER_AND_DIGIT_AT_BOTH_ENDS_OF_ITS_RANGE)


STOP_WORDS_HOLD_THEIR_POSITIONS = {
    "name": 'STOP_WORDS_HOLD_THEIR_POSITIONS',
    "journal": 'stopwords on the\nbegin\nadd s-1 disk on the node\ncommit\nquery 1 0 disk node\nquery 2 0 disk node\n',
    "answers": '-\ns-1@2\n',
    "refuses": 'stop words removed before the tokens are numbered',
}

CAPITAL_STOP_WORD_IN_A_QUERY_IS_DROPPED = {
    "name": 'CAPITAL_STOP_WORD_IN_A_QUERY_IS_DROPPED',
    "journal": 'stopwords the\nbegin\nadd c-1 the disk\ncommit\nquery 0 0 THE disk\nquery 0 0 The disk\n',
    "answers": 'c-1@0\nc-1@0\n',
    "refuses": 'a stop word recognised only when it is written in lowercase',
}

STOP_TOKEN_IS_NEVER_A_FUZZY_MATCH = {
    "name": 'STOP_TOKEN_IS_NEVER_A_FUZZY_MATCH',
    "journal": 'stopwords on the\nbegin\nadd f-1 disk on fire\nadd f-2 the cache\ncommit\nquery 0 1 disk of fire\nquery 0 1 tho\n',
    "answers": '-\n-\n',
    "refuses": 'a fuzzy word allowed to land on a stop word of the document',
}


def test_stop_words_hold_positions_and_are_not_indexed(program):
    """R7: stop words take positions and are never matched."""
    check(program, STOP_WORDS_HOLD_THEIR_POSITIONS, CAPITAL_STOP_WORD_IN_A_QUERY_IS_DROPPED, STOP_TOKEN_IS_NEVER_A_FUZZY_MATCH)


ADD_OF_A_LIVE_ID_REPLACES_THE_TEXT = {
    "name": 'ADD_OF_A_LIVE_ID_REPLACES_THE_TEXT',
    "journal": 'stopwords\nbegin\nadd a-1 disk full\ncommit\nbegin\nadd a-1 node down\ncommit\nquery 0 0 disk\nquery 0 0 node down\n',
    "answers": '-\na-1@0\n',
    "refuses": 'an add of a live id that keeps or extends the old text, or is ignored',
}

REPLACE_IN_THE_SAME_BATCH = {
    "name": 'REPLACE_IN_THE_SAME_BATCH',
    "journal": 'stopwords\nbegin\nadd a-2 alpha beta\nadd a-2 gamma\ncommit\nquery 9 0 alpha\nquery 0 0 gamma\n',
    "answers": '-\na-2@0\n',
    "refuses": 'a second add in one batch that loses to the first',
}

REPLACED_DOCUMENT_STARTS_AGAIN_AT_POSITION_ZERO = {
    "name": 'REPLACED_DOCUMENT_STARTS_AGAIN_AT_POSITION_ZERO',
    "journal": 'stopwords\nbegin\nadd a-3 alpha beta gamma delta\nadd a-3 node\nappend a-3 down\ncommit\nquery 0 0 node down\n',
    "answers": 'a-3@0\n',
    "refuses": 'a replaced document whose positions continue from the old text',
}


def test_add_replaces_a_live_document(program):
    """R8: an add of a live id replaces the whole document."""
    check(program, ADD_OF_A_LIVE_ID_REPLACES_THE_TEXT, REPLACE_IN_THE_SAME_BATCH, REPLACED_DOCUMENT_STARTS_AGAIN_AT_POSITION_ZERO)


APPENDED_TOKENS_FOLLOW_THE_DOCUMENT = {
    "name": 'APPENDED_TOKENS_FOLLOW_THE_DOCUMENT',
    "journal": 'stopwords\nbegin\nadd p-1 disk full\nappend p-1 on node 3\ncommit\nquery 0 0 full on node\nquery 2 0 disk node\nquery 1 0 disk node\n',
    "answers": 'p-1@0\np-1@2\n-\n',
    "refuses": 'appended tokens numbered from zero again',
}

APPEND_AFTER_A_TRAILING_STOP_WORD = {
    "name": 'APPEND_AFTER_A_TRAILING_STOP_WORD',
    "journal": 'stopwords the a\nbegin\nadd t-1 restart the\nappend t-1 worker pool\ncommit\nquery 0 0 restart worker\nquery 1 0 restart worker\n',
    "answers": '-\nt-1@1\n',
    "refuses": 'appended tokens numbered after the last indexed token instead of the last token',
}

APPEND_AFTER_AN_INNER_STOP_WORD = {
    "name": 'APPEND_AFTER_AN_INNER_STOP_WORD',
    "journal": 'stopwords on\nbegin\nadd t-2 job failed on node\nappend t-2 retry\ncommit\nquery 0 0 node retry\nquery 3 0 job retry\nquery 2 0 job retry\n',
    "answers": 't-2@0\nt-2@3\n-\n',
    "refuses": 'appended tokens numbered after the count of indexed tokens',
}

APPEND_JOINS_THE_TEXTS_WITH_A_SPACE = {
    "name": 'APPEND_JOINS_THE_TEXTS_WITH_A_SPACE',
    "journal": 'stopwords\nbegin\nadd j-1 dis\nappend j-1 k\ncommit\nquery 0 0 disk\nquery 0 0 dis k\n',
    "answers": '-\nj-1@0\n',
    "refuses": 'the two texts run together into one token',
}

MANY_APPENDS_ACCUMULATE = {
    "name": 'MANY_APPENDS_ACCUMULATE',
    "journal": 'stopwords\nbegin\nadd m-1 one\ncommit\nbegin\nappend m-1 two three\ncommit\nbegin\nappend m-1 four\ncommit\nquery 0 0 one two three four\nquery 2 0 one four\n',
    "answers": 'm-1@0\nm-1@2\n',
    "refuses": 'an append that keeps only the latest text',
}

APPEND_TO_A_DOCUMENT_WITH_NO_TOKEN = {
    "name": 'APPEND_TO_A_DOCUMENT_WITH_NO_TOKEN',
    "journal": 'stopwords\nbegin\nadd e-1 ---\nappend e-1 disk full\ncommit\nquery 0 0 disk full\n',
    "answers": 'e-1@0\n',
    "refuses": 'an append that cannot start from an empty document',
}

APPEND_WITH_NO_TOKEN_TAKES_NO_POSITION = {
    "name": 'APPEND_WITH_NO_TOKEN_TAKES_NO_POSITION',
    "journal": 'stopwords\nbegin\nadd tl-1 disk\nappend tl-1 ---\nappend tl-1      \nappend tl-1 full\ncommit\nquery 0 0 disk full\n',
    "answers": 'tl-1@0\n',
    "refuses": 'an append that reserves a position even when its text holds no token',
}

MORE_THAN_THREE_HUNDRED_APPENDS = {
    "name": 'MORE_THAN_THREE_HUNDRED_APPENDS',
    "journal": 'stopwords\nbegin\nadd many-1 disk\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 ---\nappend many-1 full\ncommit\nquery 0 0 disk full\n',
    "answers": 'many-1@0\n',
    "refuses": 'a document that stops taking appends after a fixed number of them',
}

THREE_HUNDRED_NINETY_THREE_APPENDS = {
    "name": 'THREE_HUNDRED_NINETY_THREE_APPENDS',
    "journal": 'stopwords\nbegin\nadd ma-1 disk\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 ---\nappend ma-1 full\ncommit\nquery 0 0 disk full\n',
    "answers": 'ma-1@0\n',
    "refuses": 'a document that stops taking appends after a fixed number of them',
}


def test_append_carries_positions_on(program):
    """R9: appended tokens carry on from the document's own positions."""
    check(program, APPENDED_TOKENS_FOLLOW_THE_DOCUMENT, APPEND_AFTER_A_TRAILING_STOP_WORD, APPEND_AFTER_AN_INNER_STOP_WORD, APPEND_JOINS_THE_TEXTS_WITH_A_SPACE, MANY_APPENDS_ACCUMULATE, APPEND_TO_A_DOCUMENT_WITH_NO_TOKEN, APPEND_WITH_NO_TOKEN_TAKES_NO_POSITION, MORE_THAN_THREE_HUNDRED_APPENDS, THREE_HUNDRED_NINETY_THREE_APPENDS)


DELETE_REMOVES_THE_DOCUMENT = {
    "name": 'DELETE_REMOVES_THE_DOCUMENT',
    "journal": 'stopwords\nbegin\nadd d-1 disk full\nadd d-2 disk full\ncommit\nbegin\ndelete d-1\ncommit\nquery 0 0 disk full\n',
    "answers": 'd-2@0\n',
    "refuses": 'a delete that is ignored or removes the wrong document',
}

DELETE_OF_AN_ID_NOT_LIVE_CHANGES_NOTHING = {
    "name": 'DELETE_OF_AN_ID_NOT_LIVE_CHANGES_NOTHING',
    "journal": 'stopwords\nbegin\nadd d-3 disk\ncommit\nbegin\ndelete d-9\ndelete d-3\ndelete d-3\ncommit\nbegin\ndelete d-3\ncommit\nquery 0 0 disk\nbegin\nadd d-3 node\ncommit\nquery 0 0 node\n',
    "answers": '-\nd-3@0\n',
    "refuses": 'a delete of a missing id that fails or disturbs other documents',
}

ADD_AFTER_DELETE_STARTS_A_NEW_DOCUMENT = {
    "name": 'ADD_AFTER_DELETE_STARTS_A_NEW_DOCUMENT',
    "journal": 'stopwords\nbegin\nadd d-4 disk full\ncommit\nbegin\ndelete d-4\nadd d-4 node\nappend d-4 down\ncommit\nquery 9 0 disk\nquery 0 0 node down\n',
    "answers": '-\nd-4@0\n',
    "refuses": 'a deleted document that comes back with its old tokens',
}


def test_delete_ends_a_document(program):
    """R10: a delete ends the document and a later add starts a new one."""
    check(program, DELETE_REMOVES_THE_DOCUMENT, DELETE_OF_AN_ID_NOT_LIVE_CHANGES_NOTHING, ADD_AFTER_DELETE_STARTS_A_NEW_DOCUMENT)


DOCUMENT_OF_THREE_HUNDRED_TOKENS = {
    "name": 'DOCUMENT_OF_THREE_HUNDRED_TOKENS',
    "journal": 'stopwords\nbegin\nadd big-1 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend big-1 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend big-1 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x q z\ncommit\nquery 0 0 q z\nquery 0 0 z\n',
    "answers": 'big-1@0\nbig-1@0\n',
    "refuses": 'a document cut short below three hundred tokens',
}

ONE_HUNDRED_LIVE_DOCUMENTS = {
    "name": 'ONE_HUNDRED_LIVE_DOCUMENTS',
    "journal": 'stopwords\nbegin\nadd n00 shared word 0\nadd n01 shared word 1\nadd n02 shared word 2\nadd n03 shared word 3\nadd n04 shared word 4\nadd n05 shared word 5\nadd n06 shared word 6\nadd n07 shared word 7\nadd n08 shared word 8\nadd n09 shared word 9\nadd n10 shared word 10\nadd n11 shared word 11\nadd n12 shared word 12\nadd n13 shared word 13\nadd n14 shared word 14\nadd n15 shared word 15\nadd n16 shared word 16\nadd n17 shared word 17\nadd n18 shared word 18\nadd n19 shared word 19\nadd n20 shared word 20\nadd n21 shared word 21\nadd n22 shared word 22\nadd n23 shared word 23\nadd n24 shared word 24\nadd n25 shared word 25\nadd n26 shared word 26\nadd n27 shared word 27\nadd n28 shared word 28\nadd n29 shared word 29\nadd n30 shared word 30\nadd n31 shared word 31\nadd n32 shared word 32\nadd n33 shared word 33\nadd n34 shared word 34\nadd n35 shared word 35\nadd n36 shared word 36\nadd n37 shared word 37\nadd n38 shared word 38\nadd n39 shared word 39\nadd n40 shared word 40\nadd n41 shared word 41\nadd n42 shared word 42\nadd n43 shared word 43\nadd n44 shared word 44\nadd n45 shared word 45\nadd n46 shared word 46\nadd n47 shared word 47\nadd n48 shared word 48\nadd n49 shared word 49\nadd n50 shared word 50\nadd n51 shared word 51\nadd n52 shared word 52\nadd n53 shared word 53\nadd n54 shared word 54\nadd n55 shared word 55\nadd n56 shared word 56\nadd n57 shared word 57\nadd n58 shared word 58\nadd n59 shared word 59\nadd n60 shared word 60\nadd n61 shared word 61\nadd n62 shared word 62\nadd n63 shared word 63\nadd n64 shared word 64\nadd n65 shared word 65\nadd n66 shared word 66\nadd n67 shared word 67\nadd n68 shared word 68\nadd n69 shared word 69\nadd n70 shared word 70\nadd n71 shared word 71\nadd n72 shared word 72\nadd n73 shared word 73\nadd n74 shared word 74\nadd n75 shared word 75\nadd n76 shared word 76\nadd n77 shared word 77\nadd n78 shared word 78\nadd n79 shared word 79\nadd n80 shared word 80\nadd n81 shared word 81\nadd n82 shared word 82\nadd n83 shared word 83\nadd n84 shared word 84\nadd n85 shared word 85\nadd n86 shared word 86\nadd n87 shared word 87\nadd n88 shared word 88\nadd n89 shared word 89\nadd n90 shared word 90\nadd n91 shared word 91\nadd n92 shared word 92\nadd n93 shared word 93\nadd n94 shared word 94\nadd n95 shared word 95\nadd n96 shared word 96\nadd n97 shared word 97\nadd n98 shared word 98\nadd n99 shared word 99\ncommit\nquery 0 0 shared word\nquery 0 0 word 99\n',
    "answers": 'n00@0 n01@0 n02@0 n03@0 n04@0 n05@0 n06@0 n07@0 n08@0 n09@0 n10@0 n11@0 n12@0 n13@0 n14@0 n15@0 n16@0 n17@0 n18@0 n19@0 n20@0 n21@0 n22@0 n23@0 n24@0 n25@0 n26@0 n27@0 n28@0 n29@0 n30@0 n31@0 n32@0 n33@0 n34@0 n35@0 n36@0 n37@0 n38@0 n39@0 n40@0 n41@0 n42@0 n43@0 n44@0 n45@0 n46@0 n47@0 n48@0 n49@0 n50@0 n51@0 n52@0 n53@0 n54@0 n55@0 n56@0 n57@0 n58@0 n59@0 n60@0 n61@0 n62@0 n63@0 n64@0 n65@0 n66@0 n67@0 n68@0 n69@0 n70@0 n71@0 n72@0 n73@0 n74@0 n75@0 n76@0 n77@0 n78@0 n79@0 n80@0 n81@0 n82@0 n83@0 n84@0 n85@0 n86@0 n87@0 n88@0 n89@0 n90@0 n91@0 n92@0 n93@0 n94@0 n95@0 n96@0 n97@0 n98@0 n99@0\nn99@0\n',
    "refuses": 'a working copy that holds fewer than one hundred documents',
}

MORE_THAN_ONE_HUNDRED_IDS_OVER_TIME = {
    "name": 'MORE_THAN_ONE_HUNDRED_IDS_OVER_TIME',
    "journal": 'stopwords\nbegin\nadd id000 word\ndelete id000\nadd id001 word\ndelete id001\nadd id002 word\ndelete id002\nadd id003 word\ndelete id003\nadd id004 word\ndelete id004\nadd id005 word\ndelete id005\nadd id006 word\ndelete id006\nadd id007 word\ndelete id007\nadd id008 word\ndelete id008\nadd id009 word\ndelete id009\nadd id010 word\ndelete id010\nadd id011 word\ndelete id011\nadd id012 word\ndelete id012\nadd id013 word\ndelete id013\nadd id014 word\ndelete id014\nadd id015 word\ndelete id015\nadd id016 word\ndelete id016\nadd id017 word\ndelete id017\nadd id018 word\ndelete id018\nadd id019 word\ndelete id019\nadd id020 word\ndelete id020\nadd id021 word\ndelete id021\nadd id022 word\ndelete id022\nadd id023 word\ndelete id023\nadd id024 word\ndelete id024\nadd id025 word\ndelete id025\nadd id026 word\ndelete id026\nadd id027 word\ndelete id027\nadd id028 word\ndelete id028\nadd id029 word\ndelete id029\nadd id030 word\ndelete id030\nadd id031 word\ndelete id031\nadd id032 word\ndelete id032\nadd id033 word\ndelete id033\nadd id034 word\ndelete id034\nadd id035 word\ndelete id035\nadd id036 word\ndelete id036\nadd id037 word\ndelete id037\nadd id038 word\ndelete id038\nadd id039 word\ndelete id039\nadd id040 word\ndelete id040\nadd id041 word\ndelete id041\nadd id042 word\ndelete id042\nadd id043 word\ndelete id043\nadd id044 word\ndelete id044\nadd id045 word\ndelete id045\nadd id046 word\ndelete id046\nadd id047 word\ndelete id047\nadd id048 word\ndelete id048\nadd id049 word\ndelete id049\nadd id050 word\ndelete id050\nadd id051 word\ndelete id051\nadd id052 word\ndelete id052\nadd id053 word\ndelete id053\nadd id054 word\ndelete id054\nadd id055 word\ndelete id055\nadd id056 word\ndelete id056\nadd id057 word\ndelete id057\nadd id058 word\ndelete id058\nadd id059 word\ndelete id059\nadd id060 word\ndelete id060\nadd id061 word\ndelete id061\nadd id062 word\ndelete id062\nadd id063 word\ndelete id063\nadd id064 word\ndelete id064\nadd id065 word\ndelete id065\nadd id066 word\ndelete id066\nadd id067 word\ndelete id067\nadd id068 word\ndelete id068\nadd id069 word\ndelete id069\nadd id070 word\ndelete id070\nadd id071 word\ndelete id071\nadd id072 word\ndelete id072\nadd id073 word\ndelete id073\nadd id074 word\ndelete id074\nadd id075 word\ndelete id075\nadd id076 word\ndelete id076\nadd id077 word\ndelete id077\nadd id078 word\ndelete id078\nadd id079 word\ndelete id079\nadd id080 word\ndelete id080\nadd id081 word\ndelete id081\nadd id082 word\ndelete id082\nadd id083 word\ndelete id083\nadd id084 word\ndelete id084\nadd id085 word\ndelete id085\nadd id086 word\ndelete id086\nadd id087 word\ndelete id087\nadd id088 word\ndelete id088\nadd id089 word\ndelete id089\nadd id090 word\ndelete id090\nadd id091 word\ndelete id091\nadd id092 word\ndelete id092\nadd id093 word\ndelete id093\nadd id094 word\ndelete id094\nadd id095 word\ndelete id095\nadd id096 word\ndelete id096\nadd id097 word\ndelete id097\nadd id098 word\ndelete id098\nadd id099 word\ndelete id099\nadd id100 word\ndelete id100\nadd id101 word\ndelete id101\nadd id102 word\ndelete id102\nadd id103 word\ndelete id103\nadd id104 word\ndelete id104\nadd id105 word\ndelete id105\nadd id106 word\ndelete id106\nadd id107 word\ndelete id107\nadd id108 word\ndelete id108\nadd id109 word\ndelete id109\nadd id110 word\ndelete id110\nadd id111 word\ndelete id111\nadd id112 word\ndelete id112\nadd id113 word\ndelete id113\nadd id114 word\ndelete id114\nadd id115 word\ndelete id115\nadd id116 word\ndelete id116\nadd id117 word\ndelete id117\nadd id118 word\ndelete id118\nadd id119 word\ndelete id119\nadd id120 word\ndelete id120\nadd id121 word\ndelete id121\nadd id122 word\ndelete id122\nadd id123 word\ndelete id123\nadd id124 word\ndelete id124\nadd id125 word\ndelete id125\nadd id126 word\ndelete id126\nadd id127 word\ndelete id127\nadd id128 word\ndelete id128\nadd id129 word\ndelete id129\nadd id130 word\ndelete id130\nadd id131 word\ndelete id131\nadd id132 word\ndelete id132\nadd id133 word\ndelete id133\nadd id134 word\ndelete id134\nadd id135 word\ndelete id135\nadd id136 word\ndelete id136\nadd id137 word\ndelete id137\nadd id138 word\ndelete id138\nadd id139 word\ndelete id139\nadd id140 word\ndelete id140\nadd id141 word\ndelete id141\nadd id142 word\ndelete id142\nadd id143 word\ndelete id143\nadd id144 word\ndelete id144\nadd id145 word\ndelete id145\nadd id146 word\ndelete id146\nadd id147 word\nadd id148 word\nadd id149 word\ncommit\nquery 0 0 word\n',
    "answers": 'id147@0 id148@0 id149@0\n',
    "refuses": 'an index that refuses new ids once a fixed number have ever been used',
}

PHRASES_ACROSS_POSITIONS_128_AND_256 = {
    "name": 'PHRASES_ACROSS_POSITIONS_128_AND_256',
    "journal": 'stopwords\nbegin\nadd wr-1 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend wr-1 x x x x x x x x x x x x x x x x x x x x x x x x x x x a b\nappend wr-1 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend wr-1 x x x x x x x x x x x x x x x x x x x x x x x x x x c d\ncommit\nquery 0 0 a b\nquery 0 0 c d\nquery 0 0 b x\nquery 0 0 x c\n',
    "answers": 'wr-1@0\nwr-1@0\nwr-1@0\nwr-1@0\n',
    "refuses": 'positions kept in a small field that wraps at 128 or 256',
}

ONE_HUNDRED_NINETY_SIX_IDS_OVER_TIME = {
    "name": 'ONE_HUNDRED_NINETY_SIX_IDS_OVER_TIME',
    "journal": 'stopwords\nbegin\nadd id000 word\ndelete id000\nadd id001 word\ndelete id001\nadd id002 word\ndelete id002\nadd id003 word\ndelete id003\nadd id004 word\ndelete id004\nadd id005 word\ndelete id005\nadd id006 word\ndelete id006\nadd id007 word\ndelete id007\nadd id008 word\ndelete id008\nadd id009 word\ndelete id009\nadd id010 word\ndelete id010\nadd id011 word\ndelete id011\nadd id012 word\ndelete id012\nadd id013 word\ndelete id013\nadd id014 word\ndelete id014\nadd id015 word\ndelete id015\nadd id016 word\ndelete id016\nadd id017 word\ndelete id017\nadd id018 word\ndelete id018\nadd id019 word\ndelete id019\nadd id020 word\ndelete id020\nadd id021 word\ndelete id021\nadd id022 word\ndelete id022\nadd id023 word\ndelete id023\nadd id024 word\ndelete id024\nadd id025 word\ndelete id025\nadd id026 word\ndelete id026\nadd id027 word\ndelete id027\nadd id028 word\ndelete id028\nadd id029 word\ndelete id029\nadd id030 word\ndelete id030\nadd id031 word\ndelete id031\nadd id032 word\ndelete id032\nadd id033 word\ndelete id033\nadd id034 word\ndelete id034\nadd id035 word\ndelete id035\nadd id036 word\ndelete id036\nadd id037 word\ndelete id037\nadd id038 word\ndelete id038\nadd id039 word\ndelete id039\nadd id040 word\ndelete id040\nadd id041 word\ndelete id041\nadd id042 word\ndelete id042\nadd id043 word\ndelete id043\nadd id044 word\ndelete id044\nadd id045 word\ndelete id045\nadd id046 word\ndelete id046\nadd id047 word\ndelete id047\nadd id048 word\ndelete id048\nadd id049 word\ndelete id049\nadd id050 word\ndelete id050\nadd id051 word\ndelete id051\nadd id052 word\ndelete id052\nadd id053 word\ndelete id053\nadd id054 word\ndelete id054\nadd id055 word\ndelete id055\nadd id056 word\ndelete id056\nadd id057 word\ndelete id057\nadd id058 word\ndelete id058\nadd id059 word\ndelete id059\nadd id060 word\ndelete id060\nadd id061 word\ndelete id061\nadd id062 word\ndelete id062\nadd id063 word\ndelete id063\nadd id064 word\ndelete id064\nadd id065 word\ndelete id065\nadd id066 word\ndelete id066\nadd id067 word\ndelete id067\nadd id068 word\ndelete id068\nadd id069 word\ndelete id069\nadd id070 word\ndelete id070\nadd id071 word\ndelete id071\nadd id072 word\ndelete id072\nadd id073 word\ndelete id073\nadd id074 word\ndelete id074\nadd id075 word\ndelete id075\nadd id076 word\ndelete id076\nadd id077 word\ndelete id077\nadd id078 word\ndelete id078\nadd id079 word\ndelete id079\nadd id080 word\ndelete id080\nadd id081 word\ndelete id081\nadd id082 word\ndelete id082\nadd id083 word\ndelete id083\nadd id084 word\ndelete id084\nadd id085 word\ndelete id085\nadd id086 word\ndelete id086\nadd id087 word\ndelete id087\nadd id088 word\ndelete id088\nadd id089 word\ndelete id089\nadd id090 word\ndelete id090\nadd id091 word\ndelete id091\nadd id092 word\ndelete id092\nadd id093 word\ndelete id093\nadd id094 word\ndelete id094\nadd id095 word\ndelete id095\nadd id096 word\ndelete id096\nadd id097 word\ndelete id097\nadd id098 word\ndelete id098\nadd id099 word\ndelete id099\nadd id100 word\ndelete id100\nadd id101 word\ndelete id101\nadd id102 word\ndelete id102\nadd id103 word\ndelete id103\nadd id104 word\ndelete id104\nadd id105 word\ndelete id105\nadd id106 word\ndelete id106\nadd id107 word\ndelete id107\nadd id108 word\ndelete id108\nadd id109 word\ndelete id109\nadd id110 word\ndelete id110\nadd id111 word\ndelete id111\nadd id112 word\ndelete id112\nadd id113 word\ndelete id113\nadd id114 word\ndelete id114\nadd id115 word\ndelete id115\nadd id116 word\ndelete id116\nadd id117 word\ndelete id117\nadd id118 word\ndelete id118\nadd id119 word\ndelete id119\nadd id120 word\ndelete id120\nadd id121 word\ndelete id121\nadd id122 word\ndelete id122\nadd id123 word\ndelete id123\nadd id124 word\ndelete id124\nadd id125 word\ndelete id125\nadd id126 word\ndelete id126\nadd id127 word\ndelete id127\nadd id128 word\ndelete id128\nadd id129 word\ndelete id129\nadd id130 word\ndelete id130\nadd id131 word\ndelete id131\nadd id132 word\ndelete id132\nadd id133 word\ndelete id133\nadd id134 word\ndelete id134\nadd id135 word\ndelete id135\nadd id136 word\ndelete id136\nadd id137 word\ndelete id137\nadd id138 word\ndelete id138\nadd id139 word\ndelete id139\nadd id140 word\ndelete id140\nadd id141 word\ndelete id141\nadd id142 word\ndelete id142\nadd id143 word\ndelete id143\nadd id144 word\ndelete id144\nadd id145 word\ndelete id145\nadd id146 word\ndelete id146\nadd id147 word\ndelete id147\nadd id148 word\ndelete id148\nadd id149 word\ndelete id149\nadd id150 word\ndelete id150\nadd id151 word\ndelete id151\nadd id152 word\ndelete id152\nadd id153 word\ndelete id153\nadd id154 word\ndelete id154\nadd id155 word\ndelete id155\nadd id156 word\ndelete id156\nadd id157 word\ndelete id157\nadd id158 word\ndelete id158\nadd id159 word\ndelete id159\nadd id160 word\ndelete id160\nadd id161 word\ndelete id161\nadd id162 word\ndelete id162\nadd id163 word\ndelete id163\nadd id164 word\ndelete id164\nadd id165 word\ndelete id165\nadd id166 word\ndelete id166\nadd id167 word\ndelete id167\nadd id168 word\ndelete id168\nadd id169 word\ndelete id169\nadd id170 word\ndelete id170\nadd id171 word\ndelete id171\nadd id172 word\ndelete id172\nadd id173 word\ndelete id173\nadd id174 word\ndelete id174\nadd id175 word\ndelete id175\nadd id176 word\ndelete id176\nadd id177 word\ndelete id177\nadd id178 word\ndelete id178\nadd id179 word\ndelete id179\nadd id180 word\ndelete id180\nadd id181 word\ndelete id181\nadd id182 word\ndelete id182\nadd id183 word\ndelete id183\nadd id184 word\ndelete id184\nadd id185 word\ndelete id185\nadd id186 word\ndelete id186\nadd id187 word\ndelete id187\nadd id188 word\ndelete id188\nadd id189 word\ndelete id189\nadd id190 word\ndelete id190\nadd id191 word\ndelete id191\nadd id192 word\ndelete id192\nadd id193 word\nadd id194 word\nadd id195 word\ncommit\nquery 0 0 word\n',
    "answers": 'id193@0 id194@0 id195@0\n',
    "refuses": 'an index that refuses new ids once a fixed number have ever been used',
}

ONE_HUNDRED_FULL_DOCUMENTS = {
    "name": 'ONE_HUNDRED_FULL_DOCUMENTS',
    "journal": 'stopwords\nbegin\nadd f00 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f00 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f00 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f01 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f01 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f01 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f02 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f02 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f02 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f03 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f03 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f03 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f04 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f04 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f04 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f05 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f05 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f05 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f06 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f06 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f06 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f07 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f07 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f07 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f08 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f08 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f08 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f09 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f09 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f09 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f10 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f10 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f10 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f11 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f11 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f11 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f12 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f12 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f12 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f13 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f13 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f13 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f14 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f14 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f14 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f15 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f15 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f15 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f16 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f16 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f16 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f17 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f17 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f17 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f18 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f18 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f18 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f19 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f19 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f19 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f20 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f20 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f20 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f21 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f21 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f21 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f22 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f22 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f22 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f23 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f23 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f23 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f24 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f24 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f24 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f25 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f25 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f25 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f26 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f26 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f26 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f27 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f27 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f27 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f28 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f28 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f28 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f29 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f29 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f29 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f30 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f30 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f30 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f31 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f31 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f31 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f32 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f32 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f32 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f33 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f33 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f33 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f34 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f34 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f34 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f35 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f35 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f35 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f36 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f36 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f36 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f37 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f37 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f37 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f38 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f38 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f38 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f39 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f39 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f39 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f40 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f40 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f40 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f41 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f41 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f41 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f42 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f42 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f42 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f43 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f43 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f43 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f44 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f44 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f44 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f45 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f45 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f45 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f46 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f46 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f46 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f47 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f47 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f47 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f48 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f48 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f48 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f49 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f49 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f49 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f50 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f50 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f50 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f51 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f51 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f51 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f52 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f52 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f52 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f53 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f53 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f53 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f54 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f54 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f54 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f55 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f55 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f55 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f56 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f56 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f56 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f57 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f57 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f57 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f58 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f58 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f58 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f59 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f59 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f59 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f60 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f60 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f60 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f61 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f61 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f61 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f62 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f62 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f62 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f63 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f63 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f63 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f64 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f64 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f64 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f65 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f65 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f65 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f66 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f66 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f66 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f67 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f67 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f67 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f68 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f68 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f68 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f69 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f69 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f69 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f70 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f70 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f70 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f71 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f71 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f71 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f72 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f72 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f72 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f73 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f73 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f73 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f74 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f74 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f74 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f75 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f75 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f75 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f76 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f76 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f76 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f77 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f77 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f77 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f78 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f78 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f78 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f79 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f79 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f79 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f80 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f80 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f80 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f81 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f81 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f81 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f82 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f82 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f82 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f83 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f83 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f83 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f84 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f84 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f84 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f85 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f85 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f85 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f86 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f86 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f86 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f87 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f87 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f87 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f88 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f88 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f88 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f89 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f89 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f89 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f90 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f90 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f90 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f91 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f91 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f91 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f92 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f92 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f92 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f93 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f93 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f93 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f94 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f94 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f94 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f95 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f95 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f95 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f96 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f96 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f96 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f97 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f97 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f97 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f98 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f98 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f98 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nadd f99 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f99 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend f99 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x q z\ncommit\nquery 0 0 q z\nquery 0 0 z\n',
    "answers": 'f99@0\nf99@0\n',
    "refuses": 'an index that stops filing tokens beyond a fixed total',
}


def test_document_size_limits(program):
    """R11: documents of 300 tokens and 100 live documents are handled whole."""
    check(program, DOCUMENT_OF_THREE_HUNDRED_TOKENS, ONE_HUNDRED_LIVE_DOCUMENTS, MORE_THAN_ONE_HUNDRED_IDS_OVER_TIME, PHRASES_ACROSS_POSITIONS_128_AND_256, ONE_HUNDRED_NINETY_SIX_IDS_OVER_TIME, ONE_HUNDRED_FULL_DOCUMENTS)


QUERY_WORDS_ARE_LOWERCASED = {
    "name": 'QUERY_WORDS_ARE_LOWERCASED',
    "journal": 'stopwords\nbegin\nadd q-1 disk full\ncommit\nquery 0 0 DISK Full\n',
    "answers": 'q-1@0\n',
    "refuses": 'query words compared with their case kept',
}

QUERY_STOP_WORDS_ARE_DROPPED = {
    "name": 'QUERY_STOP_WORDS_ARE_DROPPED',
    "journal": 'stopwords the\nbegin\nadd q-2 disk full\ncommit\nquery 0 0 the disk full\nquery 0 0 disk full the\n',
    "answers": 'q-2@0\nq-2@0\n',
    "refuses": 'stop words kept as words of the query',
}

INNER_STOP_WORD_OF_A_QUERY_KEEPS_ITS_PLACE = {
    "name": 'INNER_STOP_WORD_OF_A_QUERY_KEEPS_ITS_PLACE',
    "journal": 'stopwords of the\nbegin\nadd q-3 out of memory\nadd q-4 out memory\nadd q-5 restart the worker\ncommit\nquery 0 0 out of memory\nquery 1 0 out of memory\nquery 0 0 restart the worker\n',
    "answers": 'q-3@0\nq-3@0 q-4@1\nq-5@0\n',
    "refuses": 'query words renumbered after the stop words are dropped',
}

REPEATED_QUERY_WORD_IS_KEPT = {
    "name": 'REPEATED_QUERY_WORD_IS_KEPT',
    "journal": 'stopwords\nbegin\nadd q-6 ping ping\nadd q-7 ping\ncommit\nquery 0 0 ping ping\n',
    "answers": 'q-6@0\n',
    "refuses": 'a query whose repeated words are merged into one',
}

SIX_WORDS_AFTER_DROPPING = {
    "name": 'SIX_WORDS_AFTER_DROPPING',
    "journal": 'stopwords to\nbegin\nadd q-8 a b c d e f\ncommit\nquery 0 0 a b c d e f\nquery 0 0 a b c d e g\nquery 0 0 to a b c d e g to\n',
    "answers": 'q-8@0\n-\n-\n',
    "refuses": 'a query cut short below six words',
}

MANY_STOP_WORDS_BEFORE_THE_WORDS = {
    "name": 'MANY_STOP_WORDS_BEFORE_THE_WORDS',
    "journal": 'stopwords the a of\nbegin\nadd sw-1 disk x full\ncommit\nquery 0 0 the a of the a of the a of disk the full\nquery 1 0 the a of the a of the a of disk full\n',
    "answers": 'sw-1@0\nsw-1@1\n',
    "refuses": 'a query read only up to a fixed number of tokens before its stop words are dropped',
}

WORDS_AFTER_FIFTY_FOUR_STOP_TOKENS = {
    "name": 'WORDS_AFTER_FIFTY_FOUR_STOP_TOKENS',
    "journal": 'stopwords a\nbegin\nadd qs-1 disk full\ncommit\nquery 0 0 a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a disk full\nquery 0 0 a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a disk a full\nquery 1 0 a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a disk a full\n',
    "answers": 'qs-1@0\n-\nqs-1@1\n',
    "refuses": 'a query read only up to a fixed number of tokens',
}


def test_query_words_keep_their_positions(program):
    """R12: query words are lowercased, stop words dropped, positions kept."""
    check(program, QUERY_WORDS_ARE_LOWERCASED, QUERY_STOP_WORDS_ARE_DROPPED, INNER_STOP_WORD_OF_A_QUERY_KEEPS_ITS_PLACE, REPEATED_QUERY_WORD_IS_KEPT, SIX_WORDS_AFTER_DROPPING, MANY_STOP_WORDS_BEFORE_THE_WORDS, WORDS_AFTER_FIFTY_FOUR_STOP_TOKENS)


ONE_EDIT_OF_EACH_KIND = {
    "name": 'ONE_EDIT_OF_EACH_KIND',
    "journal": 'stopwords\nbegin\nadd e-1 disk\nadd e-2 disks\nadd e-3 dsk\nadd e-4 dusk\nadd e-5 dsik\nadd e-6 kids\ncommit\nquery 0 1 disk\nquery 0 0 disk\n',
    "answers": 'e-1@0 e-2@0 e-3@0 e-4@0 e-5@0\ne-1@0\n',
    "refuses": 'an edit kind left out, or a swap counted as two edits',
}

TWO_EDITS_AT_FUZZ_TWO_AND_NOT_AT_ONE = {
    "name": 'TWO_EDITS_AT_FUZZ_TWO_AND_NOT_AT_ONE',
    "journal": 'stopwords\nbegin\nadd e-7 desks\nadd e-8 dk\nadd e-9 sidk\ncommit\nquery 0 2 disk\nquery 0 1 disk\n',
    "answers": 'e-7@0 e-8@0 e-9@0\n-\n',
    "refuses": 'an edit distance that counts a pair of edits as one, or caps it wrongly',
}

SWAP_THEN_INSERT_BETWEEN_IS_TWO_EDITS = {
    "name": 'SWAP_THEN_INSERT_BETWEEN_IS_TWO_EDITS',
    "journal": 'stopwords\nbegin\nadd t-1 cpu load high\nadd t-2 api gateway up\ncommit\nquery 0 2 cuop load\nquery 0 2 aiep gateway\n',
    "answers": 't-1@0\nt-2@0\n',
    "refuses": 'the row-by-row distance that never edits inside a swapped pair',
}

DISTANCE_IS_THE_SAME_EITHER_WAY = {
    "name": 'DISTANCE_IS_THE_SAME_EITHER_WAY',
    "journal": 'stopwords\nbegin\nadd t-3 ack timeout\nadd t-4 akec timeout\ncommit\nquery 0 2 akec timeout\nquery 0 2 ack timeout\n',
    "answers": 't-3@0 t-4@0\nt-3@0 t-4@0\n',
    "refuses": 'a distance that depends on which token comes first',
}

LONG_TOKENS_ARE_COMPARED_IN_FULL = {
    "name": 'LONG_TOKENS_ARE_COMPARED_IN_FULL',
    "journal": 'stopwords\nbegin\nadd lt-1 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\nadd lt-2 bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbcd\ncommit\nquery 0 1 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\nquery 0 0 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\nquery 0 1 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaax\nquery 0 1 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\nquery 0 1 bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbdc\nquery 0 2 bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbdce\n',
    "answers": 'lt-1@0\nlt-1@0\nlt-1@0\n-\nlt-2@0\nlt-2@0\n',
    "refuses": 'a fuzzy comparison that gives up on tokens longer than a fixed size',
}

SHORT_WORDS_ARE_FUZZY_TOO = {
    "name": 'SHORT_WORDS_ARE_FUZZY_TOO',
    "journal": 'stopwords\nbegin\nadd sh-1 ab\nadd sh-2 x\nadd sh-3 abcd\ncommit\nquery 0 1 a\nquery 0 2 ab\n',
    "answers": 'sh-1@0 sh-2@0\nsh-1@0 sh-2@0 sh-3@0\n',
    "refuses": 'a fuzzy match refused for words no longer than the fuzz',
}


def test_edit_distance_is_the_least_number_of_edits(program):
    """R13: the edit distance is the least number of edits."""
    check(program, ONE_EDIT_OF_EACH_KIND, TWO_EDITS_AT_FUZZ_TWO_AND_NOT_AT_ONE, SWAP_THEN_INSERT_BETWEEN_IS_TWO_EDITS, DISTANCE_IS_THE_SAME_EITHER_WAY, LONG_TOKENS_ARE_COMPARED_IN_FULL, SHORT_WORDS_ARE_FUZZY_TOO)


DOCUMENT_MISSING_A_WORD_HAS_NO_PLACEMENT = {
    "name": 'DOCUMENT_MISSING_A_WORD_HAS_NO_PLACEMENT',
    "journal": 'stopwords\nbegin\nadd m-1 disk full\ncommit\nquery 9 0 disk node\nquery 9 1 disk fill\n',
    "answers": '-\nm-1@0\n',
    "refuses": 'a document that matches with some words missing',
}

REPEATED_WORD_NEEDS_TWO_TOKENS = {
    "name": 'REPEATED_WORD_NEEDS_TWO_TOKENS',
    "journal": 'stopwords\nbegin\nadd r-1 timeout timeout\nadd r-2 timeout\ncommit\nquery 1 0 timeout timeout\n',
    "answers": 'r-1@0\n',
    "refuses": 'one token allowed to serve a repeated word twice',
}

TWO_FUZZY_WORDS_CANNOT_SHARE_A_TOKEN = {
    "name": 'TWO_FUZZY_WORDS_CANNOT_SHARE_A_TOKEN',
    "journal": 'stopwords\nbegin\nadd r-3 disk\nadd r-4 disk dusk\ncommit\nquery 2 1 disk dusk\nquery 2 1 dusk disk\n',
    "answers": 'r-4@0\nr-4@0\n',
    "refuses": 'one token allowed to serve two different words that it is close to',
}

STOP_TOKEN_CANNOT_SHORTEN_A_PLACEMENT = {
    "name": 'STOP_TOKEN_CANNOT_SHORTEN_A_PLACEMENT',
    "journal": 'stopwords at\nbegin\nadd a-1 error at node as\ncommit\nquery 2 1 error as node\nquery 1 1 error as node\n',
    "answers": 'a-1@2\n-\n',
    "refuses": 'a stop word of the document used as the token of a close query word',
}

OCCURRENCE_BEYOND_THE_HUNDREDTH = {
    "name": 'OCCURRENCE_BEYOND_THE_HUNDREDTH',
    "journal": 'stopwords\nbegin\nadd oc-1 a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a\nappend oc-1 a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a b\ncommit\nquery 0 0 a b\n',
    "answers": 'oc-1@0\n',
    "refuses": 'candidate tokens of a word capped at a fixed count',
}

THE_LAST_OF_299_OCCURRENCES = {
    "name": 'THE_LAST_OF_299_OCCURRENCES',
    "journal": 'stopwords\nbegin\nadd mo-1 a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a\nappend mo-1 a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a\nappend mo-1 a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a a b\ncommit\nquery 0 0 a b\nquery 0 0 a a a a a b\nquery 1 0 b a\nquery 2 0 b a\n',
    "answers": 'mo-1@0\nmo-1@0\n-\nmo-1@2\n',
    "refuses": 'candidate tokens of a word kept only up to a fixed count below the document size',
}


def test_placements_take_distinct_tokens_within_the_fuzz(program):
    """R14: a placement gives each word its own indexed token within the fuzz."""
    check(program, DOCUMENT_MISSING_A_WORD_HAS_NO_PLACEMENT, REPEATED_WORD_NEEDS_TWO_TOKENS, TWO_FUZZY_WORDS_CANNOT_SHARE_A_TOKEN, STOP_TOKEN_CANNOT_SHORTEN_A_PLACEMENT, OCCURRENCE_BEYOND_THE_HUNDREDTH, THE_LAST_OF_299_OCCURRENCES)


REVERSED_ORDER_COSTS_TWO = {
    "name": 'REVERSED_ORDER_COSTS_TWO',
    "journal": 'stopwords\nbegin\nadd o-1 down link\ncommit\nquery 2 0 link down\nquery 1 0 link down\n',
    "answers": 'o-1@2\n-\n',
    "refuses": 'a spread that ignores the order of the words',
}

NEAREST_TOKEN_IS_NOT_ALWAYS_THE_LEAST_SPREAD = {
    "name": 'NEAREST_TOKEN_IS_NOT_ALWAYS_THE_LEAST_SPREAD',
    "journal": 'stopwords\nbegin\nadd g-1 edge node pool sync miss cache hit rate low miss storm\ncommit\nquery 9 0 cache miss storm\n',
    "answers": 'g-1@3\n',
    "refuses": "each word's token chosen nearest to the first word's",
}

LEAST_SPREAD_OVER_EVERY_OCCURRENCE = {
    "name": 'LEAST_SPREAD_OVER_EVERY_OCCURRENCE',
    "journal": 'stopwords\nbegin\nadd g-2 disk one two three four five full x disk full\ncommit\nquery 9 0 disk full\n',
    "answers": 'g-2@0\n',
    "refuses": 'the spread taken from the first occurrence of each word',
}

REPEATED_WORD_SPREAD_USES_TWO_TOKENS = {
    "name": 'REPEATED_WORD_SPREAD_USES_TWO_TOKENS',
    "journal": 'stopwords\nbegin\nadd g-3 failed retry x y z retry\ncommit\nquery 9 0 retry failed retry\n',
    "answers": 'g-3@4\n',
    "refuses": 'a repeated word allowed to reuse one token while finding the spread',
}

ONE_WORD_QUERY_HAS_DISTANCE_ZERO = {
    "name": 'ONE_WORD_QUERY_HAS_DISTANCE_ZERO',
    "journal": 'stopwords\nbegin\nadd g-4 a b c d e f g node\ncommit\nquery 0 0 node\nquery 0 1 nodes\n',
    "answers": 'g-4@0\ng-4@0\n',
    "refuses": 'a one-word query given its position as a distance',
}

FUZZY_TOKEN_CAN_GIVE_THE_LEAST_SPREAD = {
    "name": 'FUZZY_TOKEN_CAN_GIVE_THE_LEAST_SPREAD',
    "journal": 'stopwords\nbegin\nadd g-5 disk x x x full disc full\ncommit\nquery 9 1 disk full\nquery 9 0 disk full\n',
    "answers": 'g-5@0\ng-5@3\n',
    "refuses": 'the spread taken from exact tokens only when the fuzz allows near ones',
}

SPREAD_ACROSS_OFFSETS_128_AND_256 = {
    "name": 'SPREAD_ACROSS_OFFSETS_128_AND_256',
    "journal": 'stopwords\nbegin\nadd so-1 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend so-1 x x x x x x x x x x x x x x x x x x x x x x x x x x x a b\nappend so-1 x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x x\nappend so-1 x x x x x x x x x x x x x x x x x x x x x x x x x x c d\ncommit\nquery 2 0 b a\nquery 1 0 b a\nquery 2 0 d c\n',
    "answers": 'so-1@2\n-\nso-1@2\n',
    "refuses": 'offsets kept in a small signed or unsigned field that wraps',
}


def test_distance_is_the_least_spread(program):
    """R15: the distance is the least spread over all placements."""
    check(program, REVERSED_ORDER_COSTS_TWO, NEAREST_TOKEN_IS_NOT_ALWAYS_THE_LEAST_SPREAD, LEAST_SPREAD_OVER_EVERY_OCCURRENCE, REPEATED_WORD_SPREAD_USES_TWO_TOKENS, ONE_WORD_QUERY_HAS_DISTANCE_ZERO, FUZZY_TOKEN_CAN_GIVE_THE_LEAST_SPREAD, SPREAD_ACROSS_OFFSETS_128_AND_256)


DISTANCE_EQUAL_TO_THE_SLOP_IS_A_HIT = {
    "name": 'DISTANCE_EQUAL_TO_THE_SLOP_IS_A_HIT',
    "journal": 'stopwords\nbegin\nadd h-1 disk x full\ncommit\nquery 1 0 disk full\nquery 0 0 disk full\n',
    "answers": 'h-1@1\n-\n',
    "refuses": 'a hit only when the distance is below the slop',
}

DISTANCE_NINE_AT_SLOP_NINE = {
    "name": 'DISTANCE_NINE_AT_SLOP_NINE',
    "journal": 'stopwords\nbegin\nadd h-2 alpha a b c d e f g h i beta\ncommit\nquery 9 0 alpha beta\nquery 8 0 alpha beta\n',
    "answers": 'h-2@9\n-\n',
    "refuses": 'a slop that is ignored or capped',
}

NEAREST_TOKEN_MISSES_A_HIT = {
    "name": 'NEAREST_TOKEN_MISSES_A_HIT',
    "journal": 'stopwords\nbegin\nadd h-3 w x y full queue z u v full alert\ncommit\nquery 3 0 queue full alert\nquery 2 0 queue full alert\n',
    "answers": 'h-3@3\n-\n',
    "refuses": "each word's token chosen nearest to the first word's",
}

SWAP_THEN_INSERT_FINDS_A_HIT = {
    "name": 'SWAP_THEN_INSERT_FINDS_A_HIT',
    "journal": 'stopwords\nbegin\nadd h-4 auth failed for user\nadd h-5 cpu spike\ncommit\nquery 0 2 auhat failed\nquery 0 2 cuap spike\nquery 0 1 cuap spike\n',
    "answers": 'h-4@0\nh-5@0\n-\n',
    "refuses": 'the row-by-row distance that never edits inside a swapped pair',
}


def test_hits_lie_within_the_slop(program):
    """R16: a document is a hit when its distance is at most the slop."""
    check(program, DISTANCE_EQUAL_TO_THE_SLOP_IS_A_HIT, DISTANCE_NINE_AT_SLOP_NINE, NEAREST_TOKEN_MISSES_A_HIT, SWAP_THEN_INSERT_FINDS_A_HIT)


TIES_GO_BY_ID_IN_BYTE_ORDER = {
    "name": 'TIES_GO_BY_ID_IN_BYTE_ORDER',
    "journal": 'stopwords\nbegin\nadd app2 disk\nadd app10 disk\nadd app-1 disk\nadd app disk\nadd b disk\ncommit\nquery 0 0 disk\n',
    "answers": 'app@0 app-1@0 app10@0 app2@0 b@0\n',
    "refuses": 'ties broken by insertion order, number value or descending ids',
}

DISTANCE_COMES_BEFORE_ID = {
    "name": 'DISTANCE_COMES_BEFORE_ID',
    "journal": 'stopwords\nbegin\nadd a disk x x full\nadd b disk x full\nadd c disk full\ncommit\nquery 9 0 disk full\n',
    "answers": 'c@0 b@1 a@2\n',
    "refuses": 'hits ordered by id alone',
}

NO_HIT_WRITES_A_DASH = {
    "name": 'NO_HIT_WRITES_A_DASH',
    "journal": 'stopwords\nbegin\nadd n-1 disk\ncommit\nquery 0 0 node\nquery 0 0 disk\nquery 0 0 cpu\n',
    "answers": '-\nn-1@0\n-\n',
    "refuses": 'an empty line or no line for a query without hits',
}

QUERIES_ANSWER_IN_INPUT_ORDER = {
    "name": 'QUERIES_ANSWER_IN_INPUT_ORDER',
    "journal": 'stopwords\nquery 0 0 disk\nbegin\nadd o-1 disk\ncommit\nquery 0 0 disk\nbegin\ndelete o-1\ncommit\nquery 0 0 disk\nbegin\nadd o-2 disk\nadd o-1 disk\ncommit\nquery 0 0 disk\n',
    "answers": '-\no-1@0\n-\no-1@0 o-2@0\n',
    "refuses": 'answers printed out of order or only for the final index',
}

THREE_HUNDRED_NINETY_FIVE_QUERIES = {
    "name": 'THREE_HUNDRED_NINETY_FIVE_QUERIES',
    "journal": 'stopwords\nbegin\nadd oa-1 alpha\nadd ob-1 beta\ncommit\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\nquery 0 0 gamma\nquery 0 0 alpha\nquery 0 0 beta\n',
    "answers": 'oa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n-\noa-1@0\nob-1@0\n',
    "refuses": 'a program that stops answering after a fixed number of queries',
}


def test_output_order_and_format(program):
    """R17: hits are ordered by distance then id and written in the stated form."""
    check(program, TIES_GO_BY_ID_IN_BYTE_ORDER, DISTANCE_COMES_BEFORE_ID, NO_HIT_WRITES_A_DASH, QUERIES_ANSWER_IN_INPUT_ORDER, THREE_HUNDRED_NINETY_FIVE_QUERIES)


def test_held_out_journals(program, held_out):
    """R1 to R17: journals the shipped tree does not hold are answered exactly."""
    check(program, *held_out)
