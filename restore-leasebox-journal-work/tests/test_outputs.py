"""Verifier for restore-leasebox-journal."""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import socket
import stat
import subprocess
import time
import zlib
from pathlib import Path

import pytest

# Gate integers: signed decimal spelling only (optional leading +/-). Underscores/hex/base-0 rejected.
_SIGNED_DEC = re.compile(r"^[+-]?[0-9]+$")

APP, SRC = Path("/app"), Path("/app/leasebox")
CONF_LIVE, REPORT = APP / "conf/gate.ini", APP / "out/lease-report.json"
DEFAULT_JOURNAL, SEAL = APP / "var/journal.bin", Path("/var/verifier-seal/journal.rules")
HOLDOUT, WORKDIR = Path("/tests/holdout/clash.pair"), Path("/tmp/leasework")
PIN, BIN = WORKDIR / "pin", WORKDIR / "leasebox"
DROP = ["setpriv", "--reuid=12000", "--regid=12000", "--clear-groups"]


def kv(path: Path) -> dict[str, str]:
    return {k.strip(): v.strip() for line in path.read_text().splitlines() if line.strip() and not line.startswith("#") and "=" in line for k, v in [line.split("=", 1)]}


def parse(path: Path) -> dict[str, int | str]:
    return {k: (v if k == "MAGIC" else int(v)) for k, v in kv(path).items()}


def rules() -> dict[str, int | str]:
    return parse(SEAL)


def pins() -> tuple[Path, Path]:
    PIN.mkdir(parents=True, exist_ok=True)
    os.chmod(WORKDIR, 0o1777)
    rp, cp = PIN / "journal.rules", PIN / "gate.ini"
    if not rp.exists():
        rp.write_bytes(SEAL.read_bytes()); os.chmod(rp, 0o444)
    if not cp.exists():
        cp.write_bytes(CONF_LIVE.read_bytes()); os.chmod(cp, 0o444)
    for p in (CONF_LIVE, APP / "spec/journal.rules"):
        if p.exists():
            os.chmod(p, 0o444)
    return rp, cp


def genv() -> dict[str, str]:
    e = dict(os.environ)
    path = e.get("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
    if "/usr/local/go/bin" not in path:
        path = "/usr/local/go/bin:" + path
    e.update(PATH=path, GO111MODULE="on", GOPROXY="off", GOTOOLCHAIN="local", CGO_ENABLED="0", GOCACHE="/tmp/gocache", GOPATH="/tmp/gopath")
    for k in list(e):
        if k.upper().startswith(("PYTEST", "TOX")):
            e.pop(k, None)
    return e


def run(bin_path: Path, args: list[str], extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    e = genv()
    for k in ("LEASEBOX_NOW", "LEASEBOX_RULES", "LEASEBOX_CONF", "LEASEBOX_JOURNAL"):
        e.pop(k, None)
    if extra:
        e.update(extra)
    return subprocess.run(
        DROP + [str(bin_path), *args],
        env=e,
        cwd="/tmp",
        capture_output=True,
        text=True,
        timeout=20,
    )


@pytest.fixture(scope="session")
def binary() -> Path:
    WORKDIR.mkdir(parents=True, exist_ok=True); os.chmod(WORKDIR, 0o1777); pins()
    subprocess.run(["go", "build", "-o", str(BIN), "."], cwd=str(SRC), env=genv(), check=True, capture_output=True, text=True)
    os.chmod(BIN, 0o0755)
    # Default-path checks need /app/spec/journal.rules. Prefer the staged agent copy;
    # if separate-mode staging omitted it, seed from the sealed pristine twin.
    spec_live = APP / "spec" / "journal.rules"
    if not spec_live.is_file():
        spec_live.parent.mkdir(parents=True, exist_ok=True)
        spec_live.write_bytes(SEAL.read_bytes())
        os.chmod(spec_live, 0o444)
    spec0 = spec_live.read_bytes()
    yield BIN
    assert spec_live.read_bytes() == spec0
    deny = subprocess.run(DROP + ["cat", "/tests/test_outputs.py"], capture_output=True)
    assert deny.returncode != 0
    deny2 = subprocess.run(DROP + ["cat", "/var/verifier-seal/journal.rules"], capture_output=True)
    assert deny2.returncode != 0


@pytest.fixture
def box(binary: Path) -> tuple[Path, Path]:
    return binary, WORKDIR / f"{secrets.token_hex(16)}.bin"


def env(j: Path | None = None, **x: str) -> dict[str, str]:
    rp, cp = pins()
    out = {"LEASEBOX_NOW": "1700000000", "LEASEBOX_RULES": str(rp), "LEASEBOX_CONF": str(cp), **x}
    if j is not None:
        out["LEASEBOX_JOURNAL"] = str(j)
    return out


def env_wall(j: Path | None = None, **x: str) -> dict[str, str]:
    """Same as env() but LEASEBOX_NOW unset so the binary must use the wall clock."""
    out = env(j, **x)
    out.pop("LEASEBOX_NOW", None)
    return out


def jp() -> Path:
    return WORKDIR / f"{secrets.token_hex(16)}.bin"


def wconf(m: int | str, t: int | str) -> Path:
    p = WORKDIR / f"{secrets.token_hex(8)}.ini"; p.write_text(f"MAX_LEASES={m}\nTTL_SEC={t}\n"); os.chmod(p, 0o644); return p


def wrules(**o: str) -> Path:
    text = SEAL.read_text()
    for k, v in o.items():
        text = text.replace(next(l for l in text.splitlines() if l.startswith(k + "=")), f"{k}={v}", 1)
    p = WORKDIR / f"{secrets.token_hex(8)}.rules"; p.write_text(text); os.chmod(p, 0o644); return p


def rec(j: Path, i: int = 0, s: dict[str, int | str] | None = None) -> bytes:
    s = s or rules(); n = int(s["RECORD_BYTES"]); return j.read_bytes()[i * n:(i + 1) * n]


def be(raw: bytes, off: int, w: int) -> int:
    return int.from_bytes(raw[off:off + w], "big")


def name_field(j: Path, i: int = 0, s: dict[str, int | str] | None = None) -> str:
    s = s or rules()
    raw = rec(j, i, s)
    return raw[int(s["NAME_OFF"]) : int(s["NAME_OFF"]) + int(s["NAME_LEN"])].rstrip(b"\x00").decode()


def nonce(j: Path, i: int = 0, s: dict[str, int | str] | None = None) -> int:
    s = s or rules(); return be(rec(j, i, s), int(s["NONCE_OFF"]), int(s["NONCE_LEN"]))


def nrows(j: Path, s: dict[str, int | str] | None = None) -> int:
    s = s or rules()
    n = int(s["RECORD_BYTES"])
    if not j.exists() or n <= 0:
        return 0
    return len(j.read_bytes()) // n


def last_nonce(j: Path, s: dict[str, int | str] | None = None) -> int:
    return nonce(j, nrows(j, s) - 1, s)


def crc_ok(raw: bytes, s: dict[str, int | str]) -> bool:
    off = int(s["CRC_OFF"]); return be(raw, off, int(s["CRC_LEN"])) == (zlib.crc32(raw[:off]) & 0xFFFFFFFF)


def field_slice(raw: bytes, s: dict[str, int | str], key: str) -> bytes:
    return raw[int(s[f"{key}_OFF"]) : int(s[f"{key}_OFF"]) + int(s[f"{key}_LEN"])]


def survivor_payload(raw: bytes, s: dict[str, int | str]) -> tuple[bytes, bytes, bytes, bytes]:
    """Name/owner/expiry/nonce bytes compact must leave unchanged on kept rows."""
    return (
        field_slice(raw, s, "NAME"),
        field_slice(raw, s, "OWNER"),
        field_slice(raw, s, "EXPIRE"),
        field_slice(raw, s, "NONCE"),
    )


def nonce_raw(j: Path, i: int = 0, s: dict[str, int | str] | None = None) -> bytes:
    s = s or rules()
    return field_slice(rec(j, i, s), s, "NONCE")


def check_revoke_integrity(b: Path, j: Path, e: dict[str, str], s: dict[str, int | str], revoked: str) -> None:
    """Revoke must physically remove every NAME_LEN-prefix match (no inactive tombstone)."""
    ok(run(b, ["verify"], e))
    if not j.exists() or j.stat().st_size == 0:
        return
    n, data = int(s["RECORD_BYTES"]), j.read_bytes()
    want = revoked.encode()[: int(s["NAME_LEN"])].rstrip(b"\x00")
    for i in range(len(data) // n):
        raw = data[i * n : (i + 1) * n]
        name = raw[int(s["NAME_OFF"]) : int(s["NAME_OFF"]) + int(s["NAME_LEN"])].rstrip(b"\x00")
        assert name != want, "revoke must remove every matching stored record"


def live(j: Path, s: dict[str, int | str], now: int) -> int:
    if not j.exists():
        return 0
    n, data, fo, no = int(s["RECORD_BYTES"]), j.read_bytes(), len(str(s["MAGIC"])) + 1, int(s["NAME_OFF"])
    c = 0
    for i in range(len(data) // n):
        raw = data[i * n:(i + 1) * n]
        if fo < no and raw[fo] != 1:
            continue
        if now < 0 or be(raw, int(s["EXPIRE_OFF"]), int(s["EXPIRE_LEN"])) > now:
            c += 1
    return c


def active_stored(j: Path, s: dict[str, int | str] | None = None) -> int:
    """Count stored rows with active flag set (includes expired-active; not still-live)."""
    s = s or rules()
    if not j.exists():
        return 0
    n, data, fo, no = int(s["RECORD_BYTES"]), j.read_bytes(), len(str(s["MAGIC"])) + 1, int(s["NAME_OFF"])
    c = 0
    for i in range(len(data) // n):
        raw = data[i * n : (i + 1) * n]
        if fo < no and raw[fo] == 1:
            c += 1
    return c


def plant(j: Path, data: bytes) -> None:
    j.write_bytes(data); os.chown(j, 12000, 12000); os.chmod(j, 0o666)


def plant_inactive_nonces(j: Path, s: dict[str, int | str], nonces: list[int] | range) -> None:
    """Plant inactive journal rows that reserve the given nonce encodings (observable bytes only)."""
    if isinstance(nonces, range) and len(nonces) >= 1024 and nonces.step == 1:
        tmpl = bytearray(mk("_", "c", 9999, 0, s, flag=0))
        noff, nlen = int(s["NONCE_OFF"]), int(s["NONCE_LEN"])
        coff, clen = int(s["CRC_OFF"]), int(s["CRC_LEN"])
        n = int(s["RECORD_BYTES"])
        out = bytearray(len(nonces) * n)
        pos = 0
        for nv in nonces:
            raw = tmpl[:]
            raw[noff : noff + nlen] = int(nv).to_bytes(nlen, "big")
            raw[coff : coff + clen] = (zlib.crc32(bytes(raw[:coff])) & 0xFFFFFFFF).to_bytes(clen, "big")
            out[pos : pos + n] = raw
            pos += n
        plant(j, bytes(out))
        return
    blob = b"".join(mk(f"_{i}", "c", 9999, int(nv), s, flag=0) for i, nv in enumerate(nonces))
    plant(j, blob)


def plant_inactive_nonces_except(j: Path, s: dict[str, int | str], free: int, space: int = 65536) -> None:
    """Plant inactive rows for every nonce in [0, space) except `free` (sole-free anchor)."""
    tmpl = bytearray(mk("_", "c", 9999, 0, s, flag=0))
    noff, nlen = int(s["NONCE_OFF"]), int(s["NONCE_LEN"])
    coff, clen = int(s["CRC_OFF"]), int(s["CRC_LEN"])
    n = int(s["RECORD_BYTES"])
    out = bytearray((space - 1) * n)
    pos = 0
    for nv in range(space):
        if nv == free:
            continue
        raw = tmpl[:]
        raw[noff : noff + nlen] = int(nv).to_bytes(nlen, "big")
        raw[coff : coff + clen] = (zlib.crc32(bytes(raw[:coff])) & 0xFFFFFFFF).to_bytes(clen, "big")
        out[pos : pos + n] = raw
        pos += n
    plant(j, bytes(out))


def mk(name: str, owner: str, exp: int, nv: int, s: dict[str, int | str], flag: int = 1) -> bytes:
    raw = bytearray(int(s["RECORD_BYTES"])); m = str(s["MAGIC"]).encode(); raw[:len(m)] = m
    vo, fo, no = len(m), len(m) + 1, int(s["NAME_OFF"])
    if vo < no: raw[vo] = int(s["VERSION"])
    if fo < no: raw[fo] = flag
    nb, ob = name.encode()[:int(s["NAME_LEN"])], owner.encode()[:int(s["OWNER_LEN"])]
    raw[int(s["NAME_OFF"]):int(s["NAME_OFF"]) + len(nb)] = nb
    raw[int(s["OWNER_OFF"]):int(s["OWNER_OFF"]) + len(ob)] = ob
    raw[int(s["EXPIRE_OFF"]):int(s["EXPIRE_OFF"]) + int(s["EXPIRE_LEN"])] = exp.to_bytes(int(s["EXPIRE_LEN"]), "big")
    raw[int(s["NONCE_OFF"]):int(s["NONCE_OFF"]) + int(s["NONCE_LEN"])] = nv.to_bytes(int(s["NONCE_LEN"]), "big")
    off = int(s["CRC_OFF"]); raw[off:off + int(s["CRC_LEN"])] = (zlib.crc32(bytes(raw[:off])) & 0xFFFFFFFF).to_bytes(int(s["CRC_LEN"]), "big")
    return bytes(raw)


def listed(b: Path, e: dict[str, str]) -> str:
    """Debug helper: JSON of one accepted name-set interpretation (prefer largest)."""
    return json.dumps(sorted(lease_names(b, e)))


def name_sets(data) -> list[set[str]]:
    """Every contract-legal reading of list JSON as a set of live names.

    LEASEBOX.md allows a name-field object (with any extra metadata), a leases/items
    array of name-objects, or a name-keyed object. Ambiguous JSON may admit more than
    one reading; callers must accept a match against any reading.
    """
    if data is None or data == [] or data == {}:
        return [set()]
    if isinstance(data, list):
        if data == []:
            return [set()]
        assert all(isinstance(item, dict) and isinstance(item.get("name"), str) for item in data)
        return [{item["name"] for item in data}]
    if not isinstance(data, dict):
        raise AssertionError("list JSON must be an object or an array of objects")

    out: list[set[str]] = []

    for wrap in ("leases", "items"):
        if wrap in data and isinstance(data[wrap], list) and data[wrap]:
            if all(isinstance(item, dict) and isinstance(item.get("name"), str) for item in data[wrap]):
                out.append({item["name"] for item in data[wrap]})

    if isinstance(data.get("name"), str):
        # Single lease object with a name field (metadata keys unrestricted by the page).
        out.append({data["name"]})

    if data:
        # Name-keyed object: keys are lease names (values may be scalars or empty arrays).
        out.append({str(k) for k in data})

    if data and set(data.keys()) <= {"leases", "items"} and all(
        isinstance(data[k], list) and data[k] == [] for k in data
    ):
        out.append(set())

    uniq: list[set[str]] = []
    for s in out:
        if s not in uniq:
            uniq.append(s)
    return uniq or [set()]


def names_from_json(data) -> list[str]:
    """Pick one reading for unit probes; prefer the singleton name-field reading when present."""
    sets = name_sets(data)
    for s in sets:
        if isinstance(data, dict) and isinstance(data.get("name"), str) and s == {data["name"]}:
            return sorted(s)
    return sorted(sets[0])


def lease_array_listings(data) -> list[set[str]]:
    """Name-sets from recognized lease-array forms (top-level or leases/items wrappers).

    Empty arrays are skipped so {"name":"ab","leases":[]} stays a pure name-field reading.
    """
    out: list[set[str]] = []
    if isinstance(data, list):
        if data and all(isinstance(item, dict) and isinstance(item.get("name"), str) for item in data):
            out.append({item["name"] for item in data})
        return out
    if not isinstance(data, dict):
        return out
    for wrap in ("leases", "items"):
        arr = data.get(wrap)
        if isinstance(arr, list) and arr and all(
            isinstance(item, dict) and isinstance(item.get("name"), str) for item in arr
        ):
            out.append({item["name"] for item in arr})
    return out


def loads_json(text: str):
    """Strict JSON: reject NaN/Infinity (Python's default json.loads would accept them)."""
    return json.loads(text, parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))


def lease_names(b: Path, e: dict[str, str]) -> list[str]:
    p = run(b, ["list"], e); assert p.returncode == 0 and p.stdout.strip(), p.stderr
    sets = name_sets(loads_json(p.stdout))
    # Prefer the largest set among readings (stable for multi-name fixtures).
    return sorted(max(sets, key=len))


def list_json(b: Path, e: dict[str, str]):
    p = run(b, ["list"], e); assert p.returncode == 0 and p.stdout.strip(), p.stderr
    return loads_json(p.stdout)


def list_matches(b: Path, e: dict[str, str], expected: set[str]) -> None:
    data = list_json(b, e)
    sets = name_sets(data)
    assert expected in sets, (data, sets, expected)
    # Complete name-keyed object (keys == live set): values have no disclosed schema —
    # do not reinterpret leases/items array values as additional lease listings.
    if isinstance(data, dict) and {str(k) for k in data} == expected:
        return
    # Page: list only still-live. Recognized lease arrays must not name outside the live set
    # (rejects dual wrappers that park expired/inactive names in items/leases).
    # A wrapper-level "name" string beside a leases/items array is unrestricted metadata —
    # not an additional lease entry (LEASEBOX.md supplies no wrapper schema).
    arrays = lease_array_listings(data)
    for listing in arrays:
        assert listing <= expected, (data, listing, expected)


def list_has(b: Path, e: dict[str, str], name: str) -> None:
    data = list_json(b, e)
    sets = name_sets(data)
    assert any(name in s for s in sets), (data, sets, name)


def list_lacks(b: Path, e: dict[str, str], name: str) -> None:
    """Name is absent from every reading that could be the live set for this payload."""
    data = list_json(b, e)
    sets = name_sets(data)
    # Pass if some reading equals the live set without name, or no reading contains name
    # when combined with an expected companion check via list_matches.
    assert any(name not in s for s in sets), (data, sets, name)


def report(b: Path, e: dict[str, str]) -> dict:
    REPORT.parent.mkdir(parents=True, exist_ok=True); os.chmod(REPORT.parent, 0o1777)
    if REPORT.exists() or REPORT.is_symlink():
        if REPORT.is_dir() and not REPORT.is_symlink():
            shutil.rmtree(REPORT)
        else:
            REPORT.unlink(missing_ok=True)
    p = run(b, ["report"], e); assert p.returncode == 0 and isinstance(p.stdout, str)
    text = REPORT.read_text()
    # Reject non-JSON constants (NaN/Infinity) that Python's default json.loads would accept.
    data = loads_json(text)
    assert isinstance(data, dict)
    return data


def ok(p: subprocess.CompletedProcess[str], code: int = 0) -> None:
    assert p.returncode == code and isinstance(p.stdout, str)


def assert_report(p: dict, magic: str, records: int, active: int) -> None:
    assert p["magic"] == magic and type(p["records"]) is int and type(p["active"]) is int
    assert p["records"] == records and p["active"] == active


def empty_list_ok(p: subprocess.CompletedProcess[str]) -> None:
    ok(p)
    data = loads_json(p.stdout)
    assert data is None or data == [] or data == {}


def test_core_and_names(box):
    b, j = box
    s = rules()
    gate_raw = kv(CONF_LIVE)
    for _gk in ("MAX_LEASES", "TTL_SEC"):
        assert _gk in gate_raw and _SIGNED_DEC.fullmatch(gate_raw[_gk]), gate_raw
    c = {k: int(v) for k, v in gate_raw.items()}
    assert int(s["MAX_LEASES_MIN"]) <= c["MAX_LEASES"] <= int(s["MAX_LEASES_MAX"])
    assert int(s["TTL_MIN"]) <= c["TTL_SEC"] <= int(s["TTL_MAX"])
    e = env(j); empty_list_ok(run(b, ["list"], e)); ok(run(b, ["verify"], e)); assert not j.exists()
    plant(j, b""); ok(run(b, ["verify"], e)); empty_list_ok(run(b, ["list"], e))
    assert_report(report(b, e), str(s["MAGIC"]), 0, 0)
    now = 1700000000; ok(run(b, ["grant", "ab", "cd"], env(j, LEASEBOX_NOW=str(now))))
    raw, m = rec(j), str(s["MAGIC"]).encode()
    assert raw[:len(m)] == m and raw[len(m)] == int(s["VERSION"]) and raw[len(m) + 1] == 1 and crc_ok(raw, s)
    assert be(raw, int(s["EXPIRE_OFF"]), int(s["EXPIRE_LEN"])) == now + c["TTL_SEC"]
    list_has(b, e, "ab")
    assert {"ab"} in name_sets({"name": "ab", "owner": "cd"})
    assert {"ab"} in name_sets({"name": "ab", "note": "x"})  # metadata beyond owner/expires/nonce
    assert {"ab"} in name_sets({"name": "ab", "items": []})
    assert {"ab"} in name_sets({"name": "ab", "leases": []})
    assert {"name", "magic", "owner", "leases", "items"} in name_sets(
        {"name": "name", "leases": [{"name": "name"}, {"name": "magic"}, {"name": "owner"}, {"name": "leases"}, {"name": "items"}]}
    )
    assert {"leases"} in name_sets({"leases": []})  # sole live name "leases" as name-keyed map
    assert {"name", "owner"} in name_sets({"name": "o", "owner": "o"})
    # Clash name-keyed form with a leases array listing one live key: five-set reading
    # and subset check (listing {name} ⊆ five keys) must both pass.
    _clash = {"name": "o", "magic": "o", "owner": "o", "leases": [{"name": "name"}], "items": "o"}
    _five = {"name", "magic", "owner", "leases", "items"}
    assert _five in name_sets(_clash)
    for _listing in lease_array_listings(_clash):
        assert _listing <= _five, (_clash, _listing, _five)
    ok(run(b, ["grant", "ab", "zz"], e), int(s["EXIT_DUP"])); assert live(j, s, now) == 1
    ok(run(b, ["grant", "beta", "c2"], e)); ok(run(b, ["revoke", "beta", "x"], e))
    check_revoke_integrity(b, j, e, s, "beta")
    list_matches(b, e, {"ab"})
    # Grant appends: later name must not reorder earlier rows (page: "appends one active record").
    ap = jp(); ape = env(ap, LEASEBOX_NOW="1000")
    ok(run(b, ["grant", "z", "first"], ape)); before_z = rec(ap, 0)
    ok(run(b, ["grant", "a", "second"], ape))
    assert name_field(ap, 0) == "z" and name_field(ap, 1) == "a"
    assert nonce(ap, 0) != nonce(ap, 1)
    # Prior row owner/name/expiry/nonce bytes must survive the rewrite that appends.
    assert field_slice(rec(ap, 0), s, "OWNER") == b"first" + b"\x00" * (int(s["OWNER_LEN"]) - 5)
    assert field_slice(rec(ap, 0), s, "OWNER") == field_slice(before_z, s, "OWNER")
    assert field_slice(rec(ap, 0), s, "NAME") == field_slice(before_z, s, "NAME")
    assert field_slice(rec(ap, 0), s, "NONCE") == field_slice(before_z, s, "NONCE")
    assert field_slice(rec(ap, 0), s, "EXPIRE") == field_slice(before_z, s, "EXPIRE")
    # Expiry uses the repaired gate's legal TTL_SEC (any in-band value), not a fixed 60.
    assert be(rec(ap, 0), int(s["EXPIRE_OFF"]), int(s["EXPIRE_LEN"])) == 1000 + c["TTL_SEC"]
    ap2 = jp(); ape2 = env(ap2, LEASEBOX_NOW="1000")
    ok(run(b, ["grant", "m", "ownM"], ape2)); before_m = rec(ap2, 0)
    ok(run(b, ["grant", "b", "ownB"], ape2))
    assert name_field(ap2, 0) == "m" and name_field(ap2, 1) == "b"
    assert nonce(ap2, 0) != nonce(ap2, 1)
    assert field_slice(rec(ap2, 0), s, "OWNER") == field_slice(before_m, s, "OWNER")
    assert field_slice(rec(ap2, 0), s, "OWNER").startswith(b"ownM")
    pair = jp(); pe2 = env(pair, LEASEBOX_NOW="1000")
    ok(run(b, ["grant", "alpha", "c1"], pe2)); before_a = rec(pair, 0)
    ok(run(b, ["grant", "beta", "c2"], pe2)); ok(run(b, ["grant", "gamma", "c3"], pe2))
    list_matches(b, pe2, {"alpha", "beta", "gamma"})
    raw1, m1 = rec(pair, 1), str(s["MAGIC"]).encode()
    assert raw1[:len(m1)] == m1 and raw1[len(m1)] == int(s["VERSION"]) and raw1[len(m1) + 1] == 1 and crc_ok(raw1, s)
    assert field_slice(rec(pair, 0), s, "OWNER") == field_slice(before_a, s, "OWNER")
    assert field_slice(rec(pair, 0), s, "OWNER").startswith(b"c1")
    ok(run(b, ["verify"], pe2))
    ok(run(b, ["revoke", "alpha", "x"], pe2))
    check_revoke_integrity(b, pair, pe2, s, "alpha")
    list_matches(b, pe2, {"beta", "gamma"})
    rv = jp(); plant(rv, mk("old", "c", 999, 11, s) + mk("beta", "x", 2000, 12, s))
    ok(run(b, ["revoke", "beta", "x"], env(rv, LEASEBOX_NOW="1000")))
    data, rb, no, nl = rv.read_bytes(), int(s["RECORD_BYTES"]), int(s["NAME_OFF"]), int(s["NAME_LEN"])
    assert any(data[i + no:i + no + nl].rstrip(b"\x00") == b"old" for i in range(0, len(data) - rb + 1, rb))
    j2 = jp(); e2 = env(j2); ok(run(b, ["grant", "abcdefghijklmnopq", "c1"], e2))
    ok(run(b, ["grant", "abcdefghijklmnopz", "c2"], e2), int(s["EXIT_DUP"]))
    px = jp(); pxe = env(px); ok(run(b, ["grant", "abcdefghijklmnopA", "c1"], pxe))
    ok(run(b, ["revoke", "abcdefghijklmnopB", "x"], pxe))
    list_matches(b, pxe, set()); assert live(px, s, 1700000000) == 0
    assert not px.exists() or px.stat().st_size == 0  # sole match physically removed (no inactive tombstone)
    # Second default-NAME_LEN=16 prefix-revoke site (distinct 17-char args).
    px2 = jp(); pxe2 = env(px2); ok(run(b, ["grant", "zyxwvutsrqponmlkA", "c1"], pxe2))
    ok(run(b, ["revoke", "zyxwvutsrqponmlkB", "x"], pxe2))
    list_matches(b, pxe2, set()); assert live(px2, s, 1700000000) == 0
    assert not px2.exists() or px2.stat().st_size == 0
    pref = jp(); pe = env(pref); ok(run(b, ["grant", "alphabet", "c1"], pe))
    before_pref = pref.read_bytes()
    ok(run(b, ["revoke", "alpha", "x"], pe))
    list_has(b, pe, "alphabet")
    assert pref.read_bytes() == before_pref  # unmatched revoke: journal bytes unchanged
    # Unmatched revoke must not rewrite padding (byte-identical, including nonzero PAD).
    pad_j = jp()
    pad_raw = bytearray(mk("livepad", "c1", 2000, 3, s))
    poff, plen = int(s["PAD_OFF"]), int(s["PAD_LEN"])
    assert plen > 0
    pad_raw[poff] = 0xA5
    plant(pad_j, bytes(pad_raw))
    assert crc_ok(rec(pad_j), s)
    before_pad = pad_j.read_bytes()
    ok(run(b, ["revoke", "nosuch", "x"], env(pad_j, LEASEBOX_NOW="1000")))
    assert pad_j.read_bytes() == before_pad
    list_has(b, env(pad_j, LEASEBOX_NOW="1000"), "livepad")
    # Unmatched revoke on a missing journal must not create the file.
    absent = jp()
    assert not absent.exists()
    ok(run(b, ["revoke", "ghost", "x"], env(absent)))
    assert not absent.exists()
    names = [ln.strip() for ln in HOLDOUT.read_text().splitlines() if ln.strip()]
    try:
        HOLDOUT.unlink()
    except OSError:
        pass
    h = jp(); he = env(h); ok(run(b, ["grant", names[0], "a"], he)); ok(run(b, ["grant", names[1], "b"], he), int(s["EXIT_DUP"]))
    empty = jp(); ee = env(empty); ok(run(b, ["grant", "alpha", "c1"], ee)); n0 = nonce(empty)
    nw = int(s["NONCE_LEN"])
    assert 0 <= n0 < (1 << (8 * nw))
    ok(run(b, ["revoke", "alpha", "x"], ee))
    assert not empty.exists() or empty.stat().st_size == 0  # sole-row revoke deletes, not flag-clear
    ok(run(b, ["grant", "alpha", "c2"], ee))
    assert last_nonce(empty) != n0
    assert nrows(empty) == 1
    cross = jp(); xe = env(cross)
    ok(run(b, ["grant", "alpha", "c1"], xe)); n_a = nonce(cross)
    ok(run(b, ["revoke", "alpha", "x"], xe))
    assert not cross.exists() or cross.stat().st_size == 0
    ok(run(b, ["grant", "beta", "c2"], xe))
    assert last_nonce(cross) != n_a
    assert nrows(cross) == 1
    clash = jp(); cle = env(clash)
    ok(run(b, ["grant", "name", "o"], cle)); ok(run(b, ["grant", "magic", "o"], cle)); ok(run(b, ["grant", "owner", "o"], cle))
    ok(run(b, ["grant", "leases", "o"], cle)); ok(run(b, ["grant", "items", "o"], cle))
    list_matches(b, cle, {"name", "magic", "owner", "leases", "items"})
    # Legal name-keyed map name→owner (string values): five-key reading; no lease-array form,
    # so top-level "name":"o" must not be treated as a mandatory lease-name field.
    _owner_map = {"name": "o", "magic": "o", "owner": "o", "leases": "o", "items": "o"}
    _five = {"name", "magic", "owner", "leases", "items"}
    assert _five in name_sets(_owner_map)
    assert lease_array_listings(_owner_map) == []
    # Clash object with an array-valued leases entry is still a legal name-keyed form.
    _clash_arr = {"name": "o", "magic": "o", "owner": "o", "leases": [{"name": "name"}], "items": "o"}
    assert _five in name_sets(_clash_arr)
    assert lease_array_listings(_clash_arr) == [{"name"}]
    assert {str(k) for k in _clash_arr} == _five  # complete name-keyed reading
    assert {"name", "magic", "owner", "leases", "items"} in name_sets({"name": "o", "magic": "o", "owner": "o", "leases": "o", "items": "o"})
    assert {"name", "magic", "owner", "leases", "items"} in name_sets({"name": [], "magic": [], "owner": [], "leases": [], "items": []})
    # Sole lease named leases as name-keyed map must be accepted.
    solo = jp(); soe = env(solo)
    ok(run(b, ["grant", "leases", "c"], soe)); list_matches(b, soe, {"leases"})
    abc = jp(); ae = env(abc, LEASEBOX_NOW="1000", LEASEBOX_CONF=str(wconf(8, 60)))
    ok(run(b, ["grant", "A", "c1"], ae)); ok(run(b, ["grant", "B", "c2"], ae)); ok(run(b, ["grant", "C", "c3"], ae))
    list_matches(b, ae, {"A", "B", "C"})


def test_compact_cap_verify(box):
    b, _ = box; s = rules()
    j = jp(); e = env(j, LEASEBOX_NOW="1000", LEASEBOX_CONF=str(wconf(16, 60))); ok(run(b, ["grant", "alpha", "c1"], e)); prior = nonce(j)
    ok(run(b, ["compact"], env(j, LEASEBOX_NOW="1001"))); list_has(b, env(j, LEASEBOX_NOW="1001"), "alpha")
    keep2 = jp(); k2 = env(keep2, LEASEBOX_NOW="1000", LEASEBOX_CONF=str(wconf(16, 60)))
    ok(run(b, ["grant", "A", "c1"], k2)); ok(run(b, ["grant", "B", "c2"], k2))
    list_matches(b, k2, {"A", "B"})
    ok(run(b, ["compact"], env(keep2, LEASEBOX_NOW="1001", LEASEBOX_CONF=str(wconf(16, 60)))))
    list_matches(b, env(keep2, LEASEBOX_NOW="1001"), {"A", "B"})
    # Compact must retain every unexpired same-name row (not a name-keyed survivor map).
    dupc = jp()
    plant(dupc, mk("same", "a", 2000, 1, s) + mk("same", "b", 2000, 2, s))
    de = env(dupc, LEASEBOX_NOW="1000")
    assert_report(report(b, de), str(s["MAGIC"]), 2, 2)  # row counts, not distinct names
    before_dup = dupc.read_bytes()
    ok(run(b, ["compact"], de))
    assert dupc.read_bytes() == before_dup
    assert nrows(dupc) == 2
    assert_report(report(b, de), str(s["MAGIC"]), 2, 2)
    # Inactive + live same name: compact keeps both; report records=2, active=1.
    dmix = jp()
    plant(dmix, mk("same", "a", 2000, 3, s, flag=0) + mk("same", "b", 2000, 4, s, flag=1))
    dme = env(dmix, LEASEBOX_NOW="1000")
    assert_report(report(b, dme), str(s["MAGIC"]), 2, 1)
    ok(run(b, ["compact"], dme))
    assert dmix.stat().st_size == 2 * int(s["RECORD_BYTES"])
    assert_report(report(b, dme), str(s["MAGIC"]), 2, 1)
    reuse = jp(); ru = env(reuse, LEASEBOX_NOW="1000", LEASEBOX_CONF=str(wconf(16, 15)))
    ok(run(b, ["grant", "alpha", "c1"], ru)); n_old = nonce(reuse)
    ru2 = env(reuse, LEASEBOX_NOW="1015", LEASEBOX_CONF=str(wconf(16, 15)))
    ok(run(b, ["grant", "alpha", "c2"], ru2))
    assert nrows(reuse) == 2, "grant appends; must not prune the expired predecessor"
    assert name_field(reuse, 0) == "alpha" and name_field(reuse, 1) == "alpha"
    assert nonce(reuse, 1) != n_old
    assert nonce(reuse, 0) == n_old
    ok(run(b, ["revoke", "alpha", "x"], ru2))
    list_matches(b, ru2, set()); assert live(reuse, s, 1015) == 0
    # Grant of a distinct name must keep an expired stored row and append after it.
    keep_exp = jp()
    plant(keep_exp, mk("old", "c", 999, 1, s))
    ke = env(keep_exp, LEASEBOX_NOW="1000", LEASEBOX_CONF=str(wconf(8, 60)))
    ok(run(b, ["grant", "new", "o"], ke))
    assert nrows(keep_exp) == 2
    assert name_field(keep_exp, 0) == "old" and name_field(keep_exp, 1) == "new"
    assert field_slice(rec(keep_exp, 0), s, "EXPIRE") == (999).to_bytes(int(s["EXPIRE_LEN"]), "big")
    assert crc_ok(rec(keep_exp, 0), s) and crc_ok(rec(keep_exp, 1), s)
    list_matches(b, ke, {"new"})
    assert_report(report(b, ke), str(s["MAGIC"]), 2, 1)
    # Second site: expired + grant another name still preserves both rows.
    keep_exp2 = jp()
    plant(keep_exp2, mk("stale", "x", 500, 7, s))
    ke2 = env(keep_exp2, LEASEBOX_NOW="1000", LEASEBOX_CONF=str(wconf(8, 60)))
    ok(run(b, ["grant", "fresh", "y"], ke2))
    assert nrows(keep_exp2) == 2
    assert name_field(keep_exp2, 0) == "stale" and name_field(keep_exp2, 1) == "fresh"
    # Two simultaneously live same-prefix rows: revoke must clear both (not stop after first).
    twin = jp()
    plant(twin, mk("same", "a", 2000, 1, s) + mk("same", "b", 2000, 2, s))
    te = env(twin, LEASEBOX_NOW="1000")
    assert live(twin, s, 1000) == 2
    ok(run(b, ["revoke", "same", "x"], te))
    list_matches(b, te, set()); assert live(twin, s, 1000) == 0
    assert twin.stat().st_size == 0  # both stored matches removed, not list-filtered
    # Inactive + live same prefix: revoke must remove the inactive row too (not still-live-only).
    mix = jp()
    plant(mix, mk("same", "a", 2000, 1, s, flag=0) + mk("same", "b", 2000, 2, s, flag=1))
    me = env(mix, LEASEBOX_NOW="1000")
    assert live(mix, s, 1000) == 1
    ok(run(b, ["revoke", "same", "x"], me))
    list_matches(b, me, set()); assert live(mix, s, 1000) == 0
    assert mix.stat().st_size == 0
    assert nrows(mix) == 0
    # Expired + live same prefix: revoke removes the expired stored match as well.
    expm = jp()
    plant(expm, mk("same", "a", 999, 1, s, flag=1) + mk("same", "b", 2000, 2, s, flag=1))
    ee = env(expm, LEASEBOX_NOW="1000")
    assert live(expm, s, 1000) == 1
    ok(run(b, ["revoke", "same", "x"], ee))
    list_matches(b, ee, set()); assert live(expm, s, 1000) == 0
    assert expm.stat().st_size == 0
    # Matched revoke must keep an unrelated inactive unexpired row (and its owner bytes).
    rv_keep = jp()
    plant(rv_keep, mk("keep", "ownerKeep", 2000, 1, s, flag=0) + mk("drop", "ownerDrop", 2000, 2, s, flag=1))
    rke = env(rv_keep, LEASEBOX_NOW="1000")
    ok(run(b, ["revoke", "drop", "x"], rke))
    assert nrows(rv_keep) == 1
    assert name_field(rv_keep, 0) == "keep"
    assert field_slice(rec(rv_keep, 0), s, "OWNER").startswith(b"ownerKeep")
    assert crc_ok(rec(rv_keep, 0), s)
    assert_report(report(b, rke), str(s["MAGIC"]), 1, 0)
    list_matches(b, rke, set())
    # Second matched-revoke site: both rows active; survivor owner must not be cleared.
    rv_own = jp()
    plant(rv_own, mk("keep", "ownerKeep", 2000, 1, s) + mk("drop", "ownerDrop", 2000, 2, s))
    before_keep = field_slice(rec(rv_own, 0), s, "OWNER")
    roe = env(rv_own, LEASEBOX_NOW="1000")
    ok(run(b, ["revoke", "drop", "x"], roe))
    assert nrows(rv_own) == 1 and name_field(rv_own, 0) == "keep"
    assert field_slice(rec(rv_own, 0), s, "OWNER") == before_keep
    assert field_slice(rec(rv_own, 0), s, "OWNER").startswith(b"ownerKeep")
    list_matches(b, roe, {"keep"})
    # Unmatched revoke must leave a sole inactive row byte-identical.
    rv_abs = jp()
    plant(rv_abs, mk("keep", "c", 2000, 3, s, flag=0))
    before_abs = rv_abs.read_bytes()
    rae = env(rv_abs, LEASEBOX_NOW="1000")
    ok(run(b, ["revoke", "absent", "x"], rae))
    assert rv_abs.read_bytes() == before_abs
    assert_report(report(b, rae), str(s["MAGIC"]), 1, 0)
    planted_n = jp(); plant(planted_n, mk("old", "o", 10, 0, s))
    ok(run(b, ["compact"], env(planted_n, LEASEBOX_NOW="0", LEASEBOX_CONF=str(wconf(16, 60)))))
    ok(run(b, ["compact"], env(planted_n, LEASEBOX_NOW="10", LEASEBOX_CONF=str(wconf(16, 60)))))
    ok(run(b, ["grant", "old", "o"], env(planted_n, LEASEBOX_NOW="10", LEASEBOX_CONF=str(wconf(16, 60)))))
    assert nonce(planted_n) != 0
    cut = jp(); cue = env(cut, LEASEBOX_NOW="100", LEASEBOX_CONF=str(wconf(16, 60)))
    ok(run(b, ["grant", "a", "c"], cue)); n_a = nonce(cut)
    ok(run(b, ["grant", "b", "c"], cue)); n_b = nonce(cut, 1)
    assert n_a != n_b
    ok(run(b, ["compact"], env(cut, LEASEBOX_NOW="160", LEASEBOX_CONF=str(wconf(16, 60)))))
    ok(run(b, ["grant", "c", "c"], env(cut, LEASEBOX_NOW="160", LEASEBOX_CONF=str(wconf(16, 60)))))
    assert nonce(cut) not in (n_a, n_b)
    rvn = jp(); plant(rvn, mk("x", "o", 2000, 0, s))
    ok(run(b, ["revoke", "x", "o"], env(rvn, LEASEBOX_NOW="0")))
    ok(run(b, ["grant", "x", "o"], env(rvn, LEASEBOX_NOW="0", LEASEBOX_CONF=str(wconf(16, 60)))))
    assert last_nonce(rvn) != 0
    ok(run(b, ["compact"], env(j, LEASEBOX_NOW="1060"))); assert j.stat().st_size == 0
    ok(run(b, ["grant", "beta", "c2"], env(j, LEASEBOX_NOW="1000", LEASEBOX_CONF=str(wconf(16, 60))))); assert nonce(j) != prior
    mixed = jp()
    ok(run(b, ["grant", "alpha", "c1"], env(mixed, LEASEBOX_NOW="1000", LEASEBOX_CONF=str(wconf(16, 15)))))
    ok(run(b, ["grant", "beta", "c2"], env(mixed, LEASEBOX_NOW="1000", LEASEBOX_CONF=str(wconf(16, 300)))))
    assert be(rec(mixed, 1), int(s["EXPIRE_OFF"]), int(s["EXPIRE_LEN"])) == 1300
    mid = env(mixed, LEASEBOX_NOW="1020"); assert_report(report(b, mid), str(s["MAGIC"]), 2, 1)
    # Before compact: only beta is still-live; list must not also name expired alpha.
    list_matches(b, mid, {"beta"})
    ok(run(b, ["compact"], mid)); list_matches(b, mid, {"beta"})
    # Compact must physically drop expired rows (not only filter them from list).
    assert_report(report(b, mid), str(s["MAGIC"]), 1, 1)
    assert mixed.stat().st_size == int(s["RECORD_BYTES"])
    pool = jp(); full = env(pool, LEASEBOX_NOW="1000", LEASEBOX_CONF=str(wconf(9, 15)))
    for i in range(9): ok(run(b, ["grant", f"n{i}", "c"], full))
    stale = env(pool, LEASEBOX_NOW="1015", LEASEBOX_CONF=str(wconf(9, 15)))
    ok(run(b, ["grant", "overflow", "c"], stale))
    # At clock 1015, grants from 1000 with TTL 15 have expiry==1015 and are not still-live.
    list_matches(b, stale, {"overflow"})
    cap = jp(); ce = env(cap, LEASEBOX_CONF=str(wconf(9, 61)), LEASEBOX_NOW="1700000000")
    for i in range(9): ok(run(b, ["grant", f"n{i}", "c"], ce))
    list_matches(b, ce, {f"n{i}" for i in range(9)})
    ok(run(b, ["grant", "n0", "x"], ce), int(s["EXIT_DUP"])); ok(run(b, ["grant", "ov", "c"], ce), int(s["EXIT_FULL"]))
    assert live(cap, s, 1700000000) == 9
    assert_report(report(b, ce), str(s["MAGIC"]), 9, 9)
    low = env(cap, LEASEBOX_CONF=str(wconf(8, 61)), LEASEBOX_NOW="1700000000")
    ok(run(b, ["grant", "n0", "x"], low), int(s["EXIT_DUP"])); ok(run(b, ["grant", "later", "c"], low), int(s["EXIT_FULL"]))
    # Stored rows stay 9 under lower conf cap; records is row count (not min(rows, MAX_LEASES)).
    assert_report(report(b, low), str(s["MAGIC"]), 9, 9)
    list_lacks(b, low, "later"); list_matches(b, low, {f"n{i}" for i in range(9)})
    # Grant capacity counts still-live only: 1 live + 7 expired-active must not EXIT_FULL at cap 8.
    mixcap = jp()
    plant(
        mixcap,
        mk("live", "c", 2000, 1, s)
        + b"".join(mk(f"e{i}", "c", 999, i + 2, s) for i in range(7)),
    )
    mce = env(mixcap, LEASEBOX_NOW="1000", LEASEBOX_CONF=str(wconf(8, 60)))
    assert live(mixcap, s, 1000) == 1
    ok(run(b, ["grant", "new", "c"], mce))
    assert live(mixcap, s, 1000) == 2
    list_matches(b, mce, {"live", "new"})
    # Second site: cap 9 with 1 live + 8 expired-active.
    mixcap2 = jp()
    plant(
        mixcap2,
        mk("live", "c", 2000, 1, s)
        + b"".join(mk(f"x{i}", "c", 999, i + 2, s) for i in range(8)),
    )
    mce2 = env(mixcap2, LEASEBOX_NOW="1000", LEASEBOX_CONF=str(wconf(9, 60)))
    assert live(mixcap2, s, 1000) == 1
    ok(run(b, ["grant", "new", "c"], mce2))
    assert live(mixcap2, s, 1000) == 2
    # Legal caps through the rules band (rejects hardcoded 9/16/17/32 ceilings).
    ten = jp(); te = env(ten, LEASEBOX_NOW="1000", LEASEBOX_CONF=str(wconf(10, 60)))
    for i in range(10):
        ok(run(b, ["grant", f"t{i}", "c"], te))
    assert live(ten, s, 1000) == 10
    ok(run(b, ["grant", "t10", "c"], te), int(s["EXIT_FULL"])); assert live(ten, s, 1000) == 10
    mid = jp(); me = env(mid, LEASEBOX_NOW="1000", LEASEBOX_CONF=str(wconf(33, 60)))
    for i in range(33):
        ok(run(b, ["grant", f"m{i}", "c"], me))
    assert live(mid, s, 1000) == 33
    ok(run(b, ["grant", "m33", "c"], me), int(s["EXIT_FULL"])); assert live(mid, s, 1000) == 33
    top = jp(); toe = env(top, LEASEBOX_NOW="1000", LEASEBOX_CONF=str(wconf(int(s["MAX_LEASES_MAX"]), 60)))
    cap_max = int(s["MAX_LEASES_MAX"])
    for i in range(cap_max):
        ok(run(b, ["grant", f"x{i}", "c"], toe))
    assert live(top, s, 1000) == cap_max
    ok(run(b, ["grant", "xover", "c"], toe), int(s["EXIT_FULL"])); assert live(top, s, 1000) == cap_max
    # Pool bound itself must move: MAX_LEASES_MAX=65 admits 65 (rejects hardcoded 64).
    band = wrules(MAX_LEASES_MAX="65")
    b65 = jp(); e65 = env(b65, LEASEBOX_RULES=str(band), LEASEBOX_CONF=str(wconf(65, 60)), LEASEBOX_NOW="1000")
    for i in range(65):
        ok(run(b, ["grant", f"b{i}", "c"], e65))
    assert live(b65, s, 1000) == 65
    list_matches(b, e65, {f"b{i}" for i in range(65)})
    assert_report(report(b, e65), str(s["MAGIC"]), 65, 65)
    ok(run(b, ["grant", "b65", "c"], e65), int(s["EXIT_FULL"])); assert live(b65, s, 1000) == 65
    list_has(b, e65, "b64")
    idle = jp(); plant(idle, mk("quiet", "c", 2000, 9, s, flag=0)); idle_e = env(idle, LEASEBOX_NOW="1000")
    list_matches(b, idle_e, set()); assert_report(report(b, idle_e), str(s["MAGIC"]), 1, 0)
    ok(run(b, ["verify"], idle_e))
    ok(run(b, ["grant", "quiet", "c"], idle_e)); list_matches(b, idle_e, {"quiet"})
    edge = jp(); plant(edge, mk("edge", "c", 1000, 1, s)); assert_report(report(b, env(edge, LEASEBOX_NOW="1000")), str(s["MAGIC"]), 1, 0)
    ghosts = jp(); plant(ghosts, b"".join(mk(f"g{i}", "c", 2000, i + 1, s, flag=0) for i in range(8)))
    ok(run(b, ["grant", "fresh", "c"], env(ghosts, LEASEBOX_NOW="1000", LEASEBOX_CONF=str(wconf(8, 60)))))
    list_matches(b, env(ghosts, LEASEBOX_NOW="1000"), {"fresh"})
    vj = jp(); ve = env(vj); ok(run(b, ["grant", "a", "c1"], ve)); ok(run(b, ["verify"], ve))
    bad = bytearray(vj.read_bytes()); bad[7] ^= 0x7F; vj.write_bytes(bad); ok(run(b, ["verify"], ve), int(s["EXIT_CRC"]))
    v2 = jp(); e2v = env(v2); ok(run(b, ["grant", "a", "c1"], e2v)); ok(run(b, ["grant", "b", "c2"], e2v))
    ok(run(b, ["verify"], e2v)); rawb, mb = rec(v2, 1), str(s["MAGIC"]).encode()
    assert rawb[:len(mb)] == mb and rawb[len(mb)] == int(s["VERSION"]) and crc_ok(rawb, s)
    bad2 = bytearray(v2.read_bytes()); bad2[int(s["RECORD_BYTES"])] ^= 0x7F; v2.write_bytes(bad2)
    ok(run(b, ["verify"], e2v), int(s["EXIT_CRC"]))
    trunc = jp(); te = env(trunc); ok(run(b, ["grant", "a", "c1"], te)); trunc.write_bytes(trunc.read_bytes()[: int(s["RECORD_BYTES"]) - 1]); os.chmod(trunc, 0o666)
    ok(run(b, ["verify"], te), int(s["EXIT_CRC"]))
    trail = jp(); tre = env(trail); ok(run(b, ["grant", "a", "c1"], tre)); trail.write_bytes(trail.read_bytes() + b"\x00"); os.chmod(trail, 0o666)
    ok(run(b, ["verify"], tre), int(s["EXIT_CRC"]))
    both_exp = jp(); plant(both_exp, mk("e1", "c", 999, 1, s) + mk("e2", "c", 999, 2, s))
    ok(run(b, ["compact"], env(both_exp, LEASEBOX_NOW="1000"))); assert both_exp.stat().st_size == 0
    # Compact must drop expired inactive rows (not only active expired).
    ia_only = jp(); plant(ia_only, mk("old", "c", 999, 11, s, flag=0))
    ok(run(b, ["compact"], env(ia_only, LEASEBOX_NOW="1000"))); assert ia_only.stat().st_size == 0
    ia_mix = jp(); plant(ia_mix, mk("old", "c", 999, 11, s, flag=0) + mk("keep", "ownerKeep", 2000, 12, s))
    ok(run(b, ["compact"], env(ia_mix, LEASEBOX_NOW="1000")))
    assert ia_mix.stat().st_size == int(s["RECORD_BYTES"])
    list_matches(b, env(ia_mix, LEASEBOX_NOW="1000"), {"keep"})
    assert_report(report(b, env(ia_mix, LEASEBOX_NOW="1000")), str(s["MAGIC"]), 1, 1)
    # Inactive expiry==clock must drop on compact (LEASEBOX.md: expiry <= clock).
    ia_eq = jp(); plant(ia_eq, mk("old", "c", 1000, 11, s, flag=0))
    ok(run(b, ["compact"], env(ia_eq, LEASEBOX_NOW="1000"))); assert ia_eq.stat().st_size == 0
    # Compact must keep inactive rows whose expiry is still greater than the clock.
    ia_keep = jp()
    plant(ia_keep, mk("quiet", "c", 2000, 9, s, flag=0))
    before_ia = ia_keep.read_bytes()
    ok(run(b, ["compact"], env(ia_keep, LEASEBOX_NOW="1000")))
    assert ia_keep.read_bytes() == before_ia
    assert ia_keep.stat().st_size == int(s["RECORD_BYTES"])
    assert_report(report(b, env(ia_keep, LEASEBOX_NOW="1000")), str(s["MAGIC"]), 1, 0)
    # Compact must preserve every unexpired inactive row past MAX_LEASES_MAX=64 (not truncate to 64).
    c65keep = jp()
    plant(c65keep, b"".join(mk(f"k{i}", "c", 2000, i, s, flag=0) for i in range(65)))
    before_c65 = c65keep.read_bytes()
    ok(run(b, ["compact"], env(c65keep, LEASEBOX_NOW="1000")))
    assert c65keep.read_bytes() == before_c65
    assert nrows(c65keep) == 65
    assert_report(report(b, env(c65keep, LEASEBOX_NOW="1000")), str(s["MAGIC"]), 65, 0)
    # Second site: 66 unexpired inactive survivors.
    c66keep = jp()
    plant(c66keep, b"".join(mk(f"m{i}", "c", 2000, i, s, flag=0) for i in range(66)))
    before_c66 = c66keep.read_bytes()
    ok(run(b, ["compact"], env(c66keep, LEASEBOX_NOW="1000")))
    assert c66keep.read_bytes() == before_c66
    assert nrows(c66keep) == 66
    # Compact on inactive-only journal must seed issued nonces from kept rows (not empty ledger).
    ia0 = jp()
    plant(ia0, mk("quiet", "c", 2000, 0, s, flag=0))
    ok(run(b, ["compact"], env(ia0, LEASEBOX_NOW="1000")))
    assert nrows(ia0) == 1
    assert nonce(ia0, 0) == 0
    ok(run(b, ["grant", "fresh", "c"], env(ia0, LEASEBOX_NOW="1000", LEASEBOX_CONF=str(wconf(8, 60)))))
    assert nrows(ia0) == 2
    assert nonce(ia0, 1) != 0
    assert nonce(ia0, 0) == 0
    # Second site: two kept inactive nonces; grant must avoid both.
    ia01 = jp()
    plant(ia01, mk("a", "c", 2000, 0, s, flag=0) + mk("b", "c", 2000, 1, s, flag=0))
    ok(run(b, ["compact"], env(ia01, LEASEBOX_NOW="1000")))
    assert nrows(ia01) == 2
    ok(run(b, ["grant", "fresh", "c"], env(ia01, LEASEBOX_NOW="1000", LEASEBOX_CONF=str(wconf(8, 60)))))
    assert nonce(ia01, 2) not in (0, 1)
    # Compact must scan past the default MAX_LEASES_MAX=64 pool: drop all 65 expired rows.
    c65 = jp()
    plant(c65, b"".join(mk(f"e{i}", "c", 999, i, s, flag=0) for i in range(65)))
    assert nrows(c65) == 65
    ok(run(b, ["compact"], env(c65, LEASEBOX_NOW="1000")))
    assert c65.stat().st_size == 0
    # CRC mismatch in record index 2 (third record) must be EXIT_CRC — not only records 0/1.
    v3 = jp(); e3v = env(v3)
    ok(run(b, ["grant", "r0", "c"], e3v)); ok(run(b, ["grant", "r1", "c"], e3v)); ok(run(b, ["grant", "r2", "c"], e3v))
    ok(run(b, ["verify"], e3v))
    bad3 = bytearray(v3.read_bytes()); bad3[2 * int(s["RECORD_BYTES"]) + 7] ^= 0x7F; v3.write_bytes(bad3)
    ok(run(b, ["verify"], e3v), int(s["EXIT_CRC"]))
    stale_row = jp(); plant(stale_row, mk("stale", "c", 999, 3, s)); empty_list_ok(run(b, ["list"], env(stale_row, LEASEBOX_NOW="1000")))
    # Ancient expiry (epoch+1): always ≤ any real wall clock; avoids ±2s plant/compact skew flakes.
    wall = jp(); we = env_wall(wall); plant(wall, mk("old", "c", 1, 11, s))
    ok(run(b, ["compact"], we)); assert wall.stat().st_size == 0
    # Unset LEASEBOX_NOW must use wall clock for grant, list, and report (not only compact).
    jg = jp(); conf60 = wconf(16, 60)
    t0 = int(time.time())
    ok(run(b, ["grant", "wallg", "o"], env_wall(jg, LEASEBOX_CONF=str(conf60))))
    t1 = int(time.time())
    exp = be(rec(jg), int(s["EXPIRE_OFF"]), int(s["EXPIRE_LEN"]))
    # Exact wall+TTL window (rejects minute-truncate and double-TTL unset-clock bugs).
    assert t0 + 60 <= exp <= t1 + 60, (t0, t1, exp)
    assert abs(exp - 1_700_000_000) > 1000
    # Second unset-clock grant site: different TTL; same tight window.
    jg2 = jp(); conf45 = wconf(16, 45)
    t0b = int(time.time())
    ok(run(b, ["grant", "wall2", "o"], env_wall(jg2, LEASEBOX_CONF=str(conf45))))
    t1b = int(time.time())
    exp2 = be(rec(jg2), int(s["EXPIRE_OFF"]), int(s["EXPIRE_LEN"]))
    assert t0b + 45 <= exp2 <= t1b + 45, (t0b, t1b, exp2)
    jl = jp(); now_w = int(time.time())
    # Far-past / far-future relative to wall: immune to second-boundary and short clock steps.
    plant(jl, mk("gone", "c", 1, 21, s) + mk("stay", "c", now_w + 10**7, 22, s))
    wl = env_wall(jl)
    list_matches(b, wl, {"stay"})
    assert_report(report(b, wl), str(s["MAGIC"]), 2, 1)
    # Minute-rounded wall clock must not keep a lease that expired mid-minute.
    # At wall T with floor=T//60*60, expiry floor+1 is expired when T>=floor+2, but a clock
    # rounded to floor would still treat it as live.
    now_w = int(time.time())
    floor = (now_w // 60) * 60
    if now_w < floor + 2:
        time.sleep(2.1)
        now_w = int(time.time())
        floor = (now_w // 60) * 60
    assert now_w >= floor + 2
    edge_exp = floor + 1
    jm = jp()
    plant(jm, mk("edge", "c", edge_exp, 31, s) + mk("keep", "c", now_w + 10**7, 32, s))
    wm = env_wall(jm)
    list_matches(b, wm, {"keep"})
    assert_report(report(b, wm), str(s["MAGIC"]), 2, 1)
    ok(run(b, ["compact"], wm))
    assert jm.stat().st_size == int(s["RECORD_BYTES"])
    list_matches(b, wm, {"keep"})
    # Second site: distinct names, same minute-boundary shape.
    now_w2 = int(time.time())
    floor2 = (now_w2 // 60) * 60
    if now_w2 < floor2 + 2:
        time.sleep(2.1)
        now_w2 = int(time.time())
        floor2 = (now_w2 // 60) * 60
    jm2 = jp()
    plant(jm2, mk("mid", "c", floor2 + 1, 41, s) + mk("far", "c", now_w2 + 10**7, 42, s))
    list_matches(b, env_wall(jm2), {"far"})


def test_layouts_clocks_concurrency(box):
    b, _ = box; s = rules()
    sel = wrules(MAGIC="ABCDEFGHI", RECORD_BYTES="64", NAME_OFF="12", NAME_LEN="8", OWNER_OFF="28", OWNER_LEN="8",
                 EXPIRE_OFF="36", EXPIRE_LEN="9", NONCE_OFF="45", NONCE_LEN="8", CRC_OFF="53", CRC_LEN="5",
                 PAD_OFF="58", PAD_LEN="6", EXIT_DUP="7", EXIT_FULL="8", EXIT_CRC="9")
    ss = parse(sel)
    jm = jp(); em = env(jm, LEASEBOX_RULES=str(sel), LEASEBOX_CONF=str(wconf(8, 60)), LEASEBOX_NOW="-61")
    ok(run(b, ["grant", "ab", "cd"], em)); rawm = rec(jm, 0, ss)
    assert rawm[:9] == b"ABCDEFGHI" and rawm[9] == int(ss["VERSION"]) and rawm[10] == 1
    assert rawm[12:14] == b"ab" and rawm[28:30] == b"cd" and rawm[36:45] == b"\xff" * 9
    assert crc_ok(rawm, ss) and rawm[53] == 0
    assert_report(report(b, em), "ABCDEFGHI", 1, 1)
    # CRC_LEN=5: mutating only the high CRC byte (CRC_OFF) must be EXIT_CRC (not low-4-only compare).
    hi = jp(); plant(hi, rawm)
    eh = env(hi, LEASEBOX_RULES=str(sel), LEASEBOX_CONF=str(wconf(8, 60)), LEASEBOX_NOW="-61")
    ok(run(b, ["verify"], eh))
    bad_hi = bytearray(hi.read_bytes()); bad_hi[int(ss["CRC_OFF"])] = 1; hi.write_bytes(bad_hi)
    assert bad_hi[int(ss["CRC_OFF"]) + 1:int(ss["CRC_OFF"]) + 5] == rawm[54:58]
    ok(run(b, ["verify"], eh), int(ss["EXIT_CRC"]))
    # OWNER_LEN=8 full fill: owner field is all 8 chars (no NUL terminator reserved).
    jof = jp(); eof = env(jof, LEASEBOX_RULES=str(sel), LEASEBOX_CONF=str(wconf(8, 60)), LEASEBOX_NOW="1000")
    ok(run(b, ["grant", "nm", "abcdefgh"], eof)); rawof = rec(jof, 0, ss)
    assert field_slice(rawof, ss, "OWNER") == b"abcdefgh"
    assert rawof[28:36] == b"abcdefgh"
    # Owner longer than OWNER_LEN: admit and store the field-width prefix (no length reject).
    jol = jp(); eol = env(jol, LEASEBOX_RULES=str(sel), LEASEBOX_CONF=str(wconf(8, 60)), LEASEBOX_NOW="1000")
    ok(run(b, ["grant", "n", "abcdefghijklmnopq"], eol))
    rawol = rec(jol, 0, ss)
    assert field_slice(rawol, ss, "OWNER") == b"abcdefgh"
    assert rawol[28:36] == b"abcdefgh"
    assert crc_ok(rawol, ss)
    list_has(b, eol, "n")
    jp9 = jp(); e9 = env(jp9, LEASEBOX_RULES=str(sel), LEASEBOX_CONF=str(wconf(8, 60)), LEASEBOX_NOW="1000")
    ok(run(b, ["grant", "ab", "cd"], e9)); assert rec(jp9, 0, ss)[36:45] == (1060).to_bytes(9, "big")
    list_has(b, e9, "ab")
    # Nine-byte expiry 2^64: must stay live at NOW=0 (full-width compare, not Uint64 narrowing to 0).
    exp_2_64 = 1 << 64  # 18446744073709551616 → bytes 01 00 00 00 00 00 00 00 00
    jw64 = jp()
    plant(jw64, mk("wide", "c", exp_2_64, 1, ss))
    assert rec(jw64, 0, ss)[36:45] == bytes.fromhex("010000000000000000")
    ew64 = env(jw64, LEASEBOX_RULES=str(sel), LEASEBOX_CONF=str(wconf(8, 60)), LEASEBOX_NOW="0")
    list_has(b, ew64, "wide")
    assert report(b, ew64)["active"] == 1
    ok(run(b, ["compact"], ew64))
    assert jw64.stat().st_size == int(ss["RECORD_BYTES"])
    list_has(b, ew64, "wide")
    # Second site: same wide expiry under a different nonnegative clock still below 2^64.
    ew64b = env(jw64, LEASEBOX_RULES=str(sel), LEASEBOX_CONF=str(wconf(8, 60)), LEASEBOX_NOW="1000")
    list_has(b, ew64b, "wide")
    assert report(b, ew64b)["active"] == 1
    # Signed int64 addition overflow: MaxInt64+60 must encode the true sum in EXPIRE_LEN=9
    # (int64 intermediate wraps and corrupts the field).
    jov = jp()
    eov = env(jov, LEASEBOX_RULES=str(sel), LEASEBOX_CONF=str(wconf(8, 60)), LEASEBOX_NOW="9223372036854775807")
    ok(run(b, ["grant", "ab", "cd"], eov))
    assert rec(jov, 0, ss)[36:45] == (9223372036854775807 + 60).to_bytes(9, "big")
    assert rec(jov, 0, ss)[36:45] == bytes.fromhex("00800000000000003b")
    jov2 = jp()
    eov2 = env(jov2, LEASEBOX_RULES=str(sel), LEASEBOX_CONF=str(wconf(8, 60)), LEASEBOX_NOW="9223372036854775806")
    ok(run(b, ["grant", "xy", "z"], eov2))
    assert rec(jov2, 0, ss)[36:45] == (9223372036854775806 + 60).to_bytes(9, "big")
    # MinInt64 + (-1) underflow into nine-byte modular wrap.
    rneg = wrules(
        MAGIC="ABCDEFGHI", RECORD_BYTES="64", NAME_OFF="12", NAME_LEN="8", OWNER_OFF="28", OWNER_LEN="8",
        EXPIRE_OFF="36", EXPIRE_LEN="9", NONCE_OFF="45", NONCE_LEN="8", CRC_OFF="53", CRC_LEN="5",
        PAD_OFF="58", PAD_LEN="6", EXIT_DUP="7", EXIT_FULL="8", EXIT_CRC="9", TTL_MIN="-1",
    )
    rs_neg = parse(rneg)
    jund = jp()
    eund = env(jund, LEASEBOX_RULES=str(rneg), LEASEBOX_CONF=str(wconf(8, -1)), LEASEBOX_NOW="-9223372036854775808")
    ok(run(b, ["grant", "ab", "cd"], eund))
    want_und = ((-9223372036854775808) + (-1)) % (1 << 72)
    assert rec(jund, 0, rs_neg)[36:45] == want_und.to_bytes(9, "big")
    jund2 = jp()
    eund2 = env(jund2, LEASEBOX_RULES=str(rneg), LEASEBOX_CONF=str(wconf(8, -1)), LEASEBOX_NOW="-9223372036854775807")
    ok(run(b, ["grant", "xy", "z"], eund2))
    want_und2 = ((-9223372036854775807) + (-1)) % (1 << 72)
    assert rec(jund2, 0, rs_neg)[36:45] == want_und2.to_bytes(9, "big")
    ok(run(b, ["grant", "xy", "z"], e9)); ok(run(b, ["grant", "zz", "q"], e9)); ok(run(b, ["compact"], e9))
    list_matches(b, e9, {"ab", "xy", "zz"})
    sel_v = wrules(MAGIC="ABCDEFGHI", RECORD_BYTES="64", NAME_OFF="12", NAME_LEN="8", OWNER_OFF="28", OWNER_LEN="8",
                   EXPIRE_OFF="36", EXPIRE_LEN="9", NONCE_OFF="45", NONCE_LEN="8", CRC_OFF="53", CRC_LEN="5",
                   PAD_OFF="58", PAD_LEN="6", EXIT_DUP="7", EXIT_FULL="8", EXIT_CRC="9", VERSION="2")
    sv = parse(sel_v); jv = jp(); ev = env(jv, LEASEBOX_RULES=str(sel_v), LEASEBOX_CONF=str(wconf(8, 60)), LEASEBOX_NOW="1000")
    ok(run(b, ["grant", "ab", "cd"], ev)); rawv = rec(jv, 0, sv)
    assert rawv[:9] == b"ABCDEFGHI" and rawv[9] == 2 and crc_ok(rawv, sv)
    longm = wrules(MAGIC="ABCDEFGHIJKLMNOPQ", RECORD_BYTES="64", NAME_OFF="19", NAME_LEN="8",
                   OWNER_OFF="27", OWNER_LEN="8", EXPIRE_OFF="35", EXPIRE_LEN="8", NONCE_OFF="43",
                   NONCE_LEN="8", CRC_OFF="51", CRC_LEN="4", PAD_OFF="55", PAD_LEN="9")
    lm = parse(longm); jl = jp(); el = env(jl, LEASEBOX_RULES=str(longm), LEASEBOX_CONF=str(wconf(8, 60)), LEASEBOX_NOW="1000")
    ok(run(b, ["grant", "ab", "cd"], el)); rawl = rec(jl, 0, lm)
    assert rawl[:17] == b"ABCDEFGHIJKLMNOPQ" and rawl[17] == int(lm["VERSION"]) and rawl[18] == 1
    assert rawl[19:21] == b"ab" and crc_ok(rawl, lm)
    assert_report(report(b, el), "ABCDEFGHIJKLMNOPQ", 1, 1)
    ok(run(b, ["revoke", "ab", "x"], e9))
    list_matches(b, e9, {"xy", "zz"})
    jt = jp(); et = env(jt, LEASEBOX_RULES=str(sel), LEASEBOX_CONF=str(wconf(16, 60)), LEASEBOX_NOW="1000")
    ok(run(b, ["grant", "t1", "c"], et)); jt.write_bytes(jt.read_bytes()[:63]); os.chmod(jt, 0o666)
    ok(run(b, ["verify"], et), 9)
    jd = jp(); ed = env(jd, LEASEBOX_RULES=str(sel), LEASEBOX_CONF=str(wconf(16, 60)), LEASEBOX_NOW="1000")
    ok(run(b, ["grant", "abcdefghX", "c1"], ed)); ok(run(b, ["grant", "abcdefghY", "c2"], ed), 7)
    j8 = jp(); e8 = env(j8, LEASEBOX_RULES=str(sel), LEASEBOX_CONF=str(wconf(16, 60)), LEASEBOX_NOW="1000")
    ok(run(b, ["grant", "abcdefghX", "c1"], e8)); ok(run(b, ["revoke", "abcdefghY", "x"], e8))
    list_matches(b, e8, set())
    assert not j8.exists() or j8.stat().st_size == 0
    jf = jp(); ef = env(jf, LEASEBOX_RULES=str(sel), LEASEBOX_CONF=str(wconf(8, 60)), LEASEBOX_NOW="1000")
    for i in range(8):
        ok(run(b, ["grant", f"f{i}", "c"], ef))
    list_matches(b, ef, {f"f{i}" for i in range(8)})
    ok(run(b, ["grant", "later", "c"], ef), 8)
    assert live(jf, ss, 1000) == 8; list_lacks(b, ef, "later")
    jc = jp(); ec = env(jc, LEASEBOX_RULES=str(sel), LEASEBOX_CONF=str(wconf(16, 60)), LEASEBOX_NOW="1000")
    ok(run(b, ["grant", "p1", "c1"], ec)); ok(run(b, ["grant", "p2", "c2"], ec))
    badc = bytearray(jc.read_bytes()); badc[int(ss["RECORD_BYTES"])] ^= 0x7F; jc.write_bytes(badc)
    ok(run(b, ["verify"], ec), 9)
    # Second site: CRC fault in record index 2 under alternate layout.
    j3c = jp(); e3c = env(j3c, LEASEBOX_RULES=str(sel), LEASEBOX_CONF=str(wconf(16, 60)), LEASEBOX_NOW="1000")
    ok(run(b, ["grant", "a0", "c"], e3c)); ok(run(b, ["grant", "a1", "c"], e3c)); ok(run(b, ["grant", "a2", "c"], e3c))
    ok(run(b, ["verify"], e3c))
    bad3c = bytearray(j3c.read_bytes()); bad3c[2 * int(ss["RECORD_BYTES"]) + 12] ^= 0x01; j3c.write_bytes(bad3c)
    ok(run(b, ["verify"], e3c), 9)
    two = wrules(RECORD_BYTES="50", EXPIRE_OFF="38", EXPIRE_LEN="2", NONCE_OFF="40", NONCE_LEN="2", CRC_OFF="42", CRC_LEN="4", PAD_OFF="46", PAD_LEN="4")
    tspec = parse(two); jbe = jp(); ebe = env(jbe, LEASEBOX_RULES=str(two), LEASEBOX_CONF=str(wconf(16, 60)), LEASEBOX_NOW="1000")
    ok(run(b, ["grant", "a", "c"], ebe)); n_be = last_nonce(jbe, tspec)
    assert jbe.stat().st_size == int(tspec["RECORD_BYTES"])
    assert_report(report(b, ebe), str(tspec["MAGIC"]), 1, 1)
    ok(run(b, ["revoke", "a", "x"], ebe))
    assert not jbe.exists() or jbe.stat().st_size == 0  # sole match physically removed
    ok(run(b, ["grant", "a", "c"], ebe))
    assert last_nonce(jbe, tspec) != n_be
    list_matches(b, ebe, {"a"})
    assert live(jbe, tspec, 1000) == 1
    assert_report(report(b, ebe), str(tspec["MAGIC"]), 1, 1)
    assert nrows(jbe, tspec) == 1
    # Second revoke-integrity site: revoke then verify (physical delete → empty/remaining OK).
    jrv = jp(); erv = env(jrv, LEASEBOX_RULES=str(two), LEASEBOX_CONF=str(wconf(16, 60)), LEASEBOX_NOW="1000")
    ok(run(b, ["grant", "keep", "ownerKeep"], erv)); ok(run(b, ["grant", "drop", "ownerDrop"], erv))
    keep_owner = field_slice(rec(jrv, 0, tspec), tspec, "OWNER")
    ok(run(b, ["revoke", "drop", "x"], erv))
    check_revoke_integrity(b, jrv, erv, tspec, "drop")
    list_matches(b, erv, {"keep"})
    assert field_slice(rec(jrv, 0, tspec), tspec, "OWNER") == keep_owner
    assert field_slice(rec(jrv, 0, tspec), tspec, "OWNER").startswith(b"ownerKee")
    jpos = jp(); epos = env(jpos, LEASEBOX_RULES=str(two), LEASEBOX_CONF=str(wconf(16, 60)), LEASEBOX_NOW="65530")
    ok(run(b, ["grant", "a", "c"], epos)); assert rec(jpos, 0, tspec)[38:40] == (54).to_bytes(2, "big")
    assert jpos.stat().st_size == int(tspec["RECORD_BYTES"])
    j50 = jp(); plant(j50, mk("a", "c", 2000, 1, tspec) + b"\x00")
    ok(run(b, ["verify"], env(j50, LEASEBOX_RULES=str(two))), int(tspec["EXIT_CRC"]))
    jok = jp(); plant(jok, mk("a", "c", 2000, 1, tspec) + mk("b", "c", 2000, 2, tspec))
    ok(run(b, ["verify"], env(jok, LEASEBOX_RULES=str(two))))
    jpad = jp(); plant(jpad, mk("a", "c", 2000, 1, tspec) + bytes(14))
    ok(run(b, ["verify"], env(jpad, LEASEBOX_RULES=str(two))), int(tspec["EXIT_CRC"]))
    # One-byte nonce exhaustion via inactive journal rows (contract-visible bytes only).
    one = wrules(RECORD_BYTES="57", NONCE_OFF="46", NONCE_LEN="1", CRC_OFF="47", PAD_OFF="51", PAD_LEN="6")
    ospec = parse(one)
    jh = jp(); plant_inactive_nonces(jh, ospec, range(256))
    eh = env(jh, LEASEBOX_RULES=str(one), LEASEBOX_CONF=str(wconf(16, 60)), LEASEBOX_NOW="1000")
    rows_jh, act_jh = nrows(jh, ospec), active_stored(jh, ospec)
    ok(run(b, ["grant", "slot", "c"], eh), 1)
    assert live(jh, ospec, 1000) == 0
    assert nrows(jh, ospec) == rows_jh and active_stored(jh, ospec) == act_jh  # no extra active row on fail
    jo = jp(); eo = env(jo, LEASEBOX_RULES=str(one), LEASEBOX_CONF=str(wconf(16, 15)), LEASEBOX_NOW="1000")
    plant_inactive_nonces(jo, ospec, range(255))
    ok(run(b, ["grant", "slot", "c"], eo)); assert last_nonce(jo, ospec) == 255  # sole free 1-byte encoding
    rows_jo, act_jo = nrows(jo, ospec), active_stored(jo, ospec)
    ok(run(b, ["grant", "other", "c"], eo), 1)
    assert live(jo, ospec, 1000) == 1
    assert nrows(jo, ospec) == rows_jo and active_stored(jo, ospec) == act_jo
    ok(run(b, ["revoke", "slot", "x"], eo)); ok(run(b, ["compact"], eo))
    rows_jc, act_jc = nrows(jo, ospec), active_stored(jo, ospec)
    ok(run(b, ["grant", "slot", "c"], eo), 1)
    assert live(jo, ospec, 1000) == 0
    assert nrows(jo, ospec) == rows_jc and active_stored(jo, ospec) == act_jc
    # Sole free encoding is zero: allocator must not treat 0 as a reserved sentinel.
    jz = jp(); ez = env(jz, LEASEBOX_RULES=str(one), LEASEBOX_CONF=str(wconf(16, 60)), LEASEBOX_NOW="1000")
    plant_inactive_nonces(jz, ospec, range(1, 256))
    ok(run(b, ["grant", "slot", "c"], ez))
    assert last_nonce(jz, ospec) == 0
    assert nonce_raw(jz, nrows(jz, ospec) - 1, ospec) == b"\x00"
    # Second site: same sole-zero under alternate one-byte layout.
    one2 = wrules(RECORD_BYTES="58", NONCE_OFF="46", NONCE_LEN="1", CRC_OFF="47", PAD_OFF="51", PAD_LEN="7")
    ospec2 = parse(one2)
    jz2 = jp(); ez2 = env(jz2, LEASEBOX_RULES=str(one2), LEASEBOX_CONF=str(wconf(16, 60)), LEASEBOX_NOW="1000")
    plant_inactive_nonces(jz2, ospec2, range(1, 256))
    ok(run(b, ["grant", "zero", "c"], ez2))
    assert last_nonce(jz2, ospec2) == 0
    # Multi-byte space: grant must succeed with any unused encoding (not a forced lowest-unused value).
    jwide = jp(); plant_inactive_nonces(jwide, s, range(256))
    ewide = env(jwide, LEASEBOX_NOW="1000", LEASEBOX_CONF=str(wconf(16, 60)))
    ok(run(b, ["grant", "slot", "c"], ewide))
    nw = last_nonce(jwide)
    assert nw not in range(256) and 0 <= nw < (1 << (8 * int(s["NONCE_LEN"])))
    n9 = wrules(RECORD_BYTES="65", NONCE_OFF="46", NONCE_LEN="9", CRC_OFF="55", PAD_OFF="59", PAD_LEN="6")
    n9s = parse(n9); jn = jp(); en = env(jn, LEASEBOX_RULES=str(n9), LEASEBOX_CONF=str(wconf(8, 60)))
    ok(run(b, ["grant", "ab", "cd"], en)); assert crc_ok(rec(jn, 0, n9s), n9s); list_has(b, en, "ab")
    n9_first = nonce_raw(jn, 0, n9s)
    ok(run(b, ["grant", "ef", "gh"], en)); assert crc_ok(rec(jn, 1, n9s), n9s); list_matches(b, en, {"ab", "ef"})
    assert nonce_raw(jn, 1, n9s) != n9_first  # 9-byte nonce uniqueness across grants
    ew, eoff = int(s["EXPIRE_LEN"]), int(s["EXPIRE_OFF"])
    wrap = jp(); ok(run(b, ["grant", "alpha", "o"], env(wrap, LEASEBOX_NOW="-62", LEASEBOX_CONF=str(wconf(16, 60)))))
    assert be(rec(wrap), eoff, ew) == (1 << (8 * ew)) - 2
    early = env(wrap, LEASEBOX_NOW="-1")
    list_has(b, early, "alpha"); assert report(b, early)["active"] == 1
    ok(run(b, ["compact"], early)); assert wrap.stat().st_size > 0
    # Same wrapped expiry must stay live under a nonnegative clock (unsigned compare, not int64 narrowing).
    nonneg = env(wrap, LEASEBOX_NOW="0")
    list_has(b, nonneg, "alpha")
    assert report(b, nonneg)["active"] == 1
    ok(run(b, ["compact"], nonneg))
    assert wrap.stat().st_size > 0
    list_has(b, nonneg, "alpha")
    zero = jp(); plant(zero, mk("zero", "c", 0, 1, s)); zn = env(zero, LEASEBOX_NOW="-1")
    list_has(b, zn, "zero"); assert report(b, zn)["active"] == 1
    ok(run(b, ["compact"], zn)); assert zero.stat().st_size > 0
    i32 = jp(); ok(run(b, ["grant", "alpha", "o"], env(i32, LEASEBOX_NOW="2147483588", LEASEBOX_CONF=str(wconf(16, 60)))))
    i32e = env(i32, LEASEBOX_NOW="2147483648")
    list_matches(b, i32e, set()); assert report(b, i32e)["active"] == 0
    ok(run(b, ["compact"], i32e)); assert i32.stat().st_size == 0
    # Clock beyond uint32: list/report/compact must not narrow NOW to uint32(0).
    beyond = jp()
    plant(beyond, mk("old", "c", 1, 1, s))
    big_now = env(beyond, LEASEBOX_NOW="4294967296")  # 2^32
    list_matches(b, big_now, set())
    assert report(b, big_now)["active"] == 0
    assert_report(report(b, big_now), str(s["MAGIC"]), 1, 0)
    ok(run(b, ["compact"], big_now))
    assert beyond.stat().st_size == 0
    # Second site: distinct planted expiry still removed under the same wide clock.
    beyond2 = jp()
    plant(beyond2, mk("gone", "o", 100, 2, s))
    big2 = env(beyond2, LEASEBOX_NOW="4294967296")
    list_matches(b, big2, set())
    assert report(b, big2)["active"] == 0
    ok(run(b, ["compact"], big2))
    assert beyond2.stat().st_size == 0
    wide_n = wrules(RECORD_BYTES="65", NAME_LEN="17", OWNER_OFF="23", EXPIRE_OFF="39", NONCE_OFF="47", CRC_OFF="55", PAD_OFF="59", PAD_LEN="6")
    nspec = parse(wide_n); jnl = jp(); enl = env(jnl, LEASEBOX_RULES=str(wide_n), LEASEBOX_CONF=str(wconf(8, 60)), LEASEBOX_NOW="1000")
    ok(run(b, ["grant", "abcdefghijklmnopX", "c1"], enl)); ok(run(b, ["grant", "abcdefghijklmnopY", "c2"], enl))
    list_matches(b, enl, {"abcdefghijklmnopX", "abcdefghijklmnopY"})
    assert rec(jnl, 0, nspec)[6:23] == b"abcdefghijklmnopX" and rec(jnl, 1, nspec)[6:23] == b"abcdefghijklmnopY"
    wide = wrules(RECORD_BYTES="68", OWNER_OFF="22", OWNER_LEN="20", EXPIRE_OFF="42", NONCE_OFF="50", CRC_OFF="58", PAD_OFF="62", PAD_LEN="6")
    wspec = parse(wide); jw = jp(); ew = env(jw, LEASEBOX_RULES=str(wide), LEASEBOX_CONF=str(wconf(8, 60)), LEASEBOX_NOW="1000")
    ok(run(b, ["grant", "n", "abcdefghijklmnopqr"], ew)); raww = rec(jw, 0, wspec)
    assert jw.stat().st_size == int(wspec["RECORD_BYTES"])
    assert raww[22:40] == b"abcdefghijklmnopqr" and raww[39] == ord("r")
    assert raww[40:42] == b"\x00\x00"
    # Baseline OWNER_LEN=16: owner longer than the field must still admit (prefix stored).
    jol16 = jp()
    eol16 = env(jol16, LEASEBOX_CONF=str(wconf(8, 60)), LEASEBOX_NOW="1000")
    long_owner = "abcdefghijklmnopq"  # 17 bytes > OWNER_LEN=16
    assert len(long_owner) > int(s["OWNER_LEN"])
    ok(run(b, ["grant", "n", long_owner], eol16))
    raw16 = rec(jol16)
    assert field_slice(raw16, s, "OWNER") == long_owner.encode()[: int(s["OWNER_LEN"])]
    assert crc_ok(raw16, s)
    list_has(b, eol16, "n")
    tri = wrules(RECORD_BYTES="57", EXPIRE_OFF="38", EXPIRE_LEN="3", NONCE_OFF="41", NONCE_LEN="3", CRC_OFF="44", PAD_OFF="48", PAD_LEN="9")
    t3 = parse(tri); j3 = jp(); e3 = env(j3, LEASEBOX_RULES=str(tri), LEASEBOX_CONF=str(wconf(8, 60)), LEASEBOX_NOW="1000")
    ok(run(b, ["grant", "a", "c"], e3)); assert rec(j3, 0, t3)[38:41] == (1060).to_bytes(3, "big")
    n3 = last_nonce(j3, t3)
    ok(run(b, ["revoke", "a", "x"], e3)); ok(run(b, ["grant", "b", "c"], e3))
    assert last_nonce(j3, t3) != n3
    for extra in DEFAULT_JOURNAL.parent.glob("journal.bin*"):
        if extra != DEFAULT_JOURNAL:
            extra.unlink()
    if DEFAULT_JOURNAL.exists(): DEFAULT_JOURNAL.unlink()
    os.chmod(DEFAULT_JOURNAL.parent, 0o1777); os.chmod(REPORT.parent, 0o1777)
    lr = APP / "spec/journal.rules"; oc, orr = CONF_LIVE.read_bytes(), lr.read_bytes()
    try:
        for pth in (CONF_LIVE, lr): os.chmod(pth, 0o644)
        CONF_LIVE.write_text("MAX_LEASES=9\nTTL_SEC=61\n")
        lr.write_text(orr.decode().replace("MAGIC=LBOX", "MAGIC=ZEST").replace("VERSION=1", "VERSION=2").replace("EXIT_CRC=2", "EXIT_CRC=9"))
        for pth in (CONF_LIVE, lr): os.chmod(pth, 0o444)
        def_e = {"LEASEBOX_NOW": "1000"}
        ok(run(b, ["grant", "beta", "c2"], def_e)); raw = rec(DEFAULT_JOURNAL)
        assert raw[:4] == b"ZEST" and raw[4] == 2 and raw[5] == 1
        assert be(raw, int(s["EXPIRE_OFF"]), int(s["EXPIRE_LEN"])) == 1061
        assert_report(report(b, def_e), "ZEST", 1, 1)
        list_has(b, def_e, "beta")
        ok(run(b, ["compact"], def_e)); list_has(b, def_e, "beta")
        ok(run(b, ["grant", "gamma", "c3"], def_e)); ok(run(b, ["revoke", "beta", "x"], def_e))
        list_matches(b, def_e, {"gamma"})
        for i in range(8): ok(run(b, ["grant", f"n{i}", "c"], def_e))
        ok(run(b, ["grant", "overflow", "c"], def_e), 4)
        bad_def = bytearray(DEFAULT_JOURNAL.read_bytes()); bad_def[int(s["RECORD_BYTES"])] ^= 0x7F
        DEFAULT_JOURNAL.write_bytes(bad_def); ok(run(b, ["verify"], def_e), 9)
    finally:
        for pth in (CONF_LIVE, lr): os.chmod(pth, 0o644)
        CONF_LIVE.write_bytes(oc); lr.write_bytes(orr)
        for pth in (CONF_LIVE, lr): os.chmod(pth, 0o444)
    # Independent path overrides: RULES unset must still honor JOURNAL and CONF.
    if DEFAULT_JOURNAL.exists():
        DEFAULT_JOURNAL.unlink()
    for extra in DEFAULT_JOURNAL.parent.glob("journal.bin*"):
        extra.unlink()
    jstand = WORKDIR / f"standalone_{secrets.token_hex(6)}.bin"
    conf61 = wconf(8, 61)
    e_ind = {"LEASEBOX_NOW": "1000", "LEASEBOX_JOURNAL": str(jstand), "LEASEBOX_CONF": str(conf61)}
    ok(run(b, ["grant", "ab", "cd"], e_ind))
    assert jstand.exists() and jstand.stat().st_size == int(s["RECORD_BYTES"])
    assert be(rec(jstand), int(s["EXPIRE_OFF"]), int(s["EXPIRE_LEN"])) == 1061
    assert not DEFAULT_JOURNAL.exists()
    list_has(b, e_ind, "ab")
    # Second site: different CONF TTL with RULES still unset.
    jstand2 = WORKDIR / f"standalone2_{secrets.token_hex(6)}.bin"
    conf45 = wconf(8, 45)
    e_ind2 = {"LEASEBOX_NOW": "2000", "LEASEBOX_JOURNAL": str(jstand2), "LEASEBOX_CONF": str(conf45)}
    ok(run(b, ["grant", "xy", "z"], e_ind2))
    assert jstand2.exists()
    assert be(rec(jstand2), int(s["EXPIRE_OFF"]), int(s["EXPIRE_LEN"])) == 2045
    assert not DEFAULT_JOURNAL.exists()


def test_grant_append_preserves_prior_owner(box):
    """Second grant must not zero earlier OWNER bytes (lossy rewrite of prior rows)."""
    b, _ = box; s = rules()
    ol = int(s["OWNER_LEN"])
    j = jp(); e = env(j, LEASEBOX_NOW="1000", LEASEBOX_CONF=str(wconf(8, 60)))
    ok(run(b, ["grant", "z", "first"], e))
    before = rec(j, 0)
    want_owner = b"first" + b"\x00" * (ol - 5)
    assert field_slice(before, s, "OWNER") == want_owner
    ok(run(b, ["grant", "a", "second"], e))
    after = rec(j, 0)
    assert name_field(j, 0) == "z" and name_field(j, 1) == "a"
    assert nonce(j, 0) != nonce(j, 1)
    assert field_slice(after, s, "OWNER") == want_owner
    assert field_slice(after, s, "OWNER") == field_slice(before, s, "OWNER")
    assert field_slice(after, s, "NAME") == field_slice(before, s, "NAME")
    assert field_slice(after, s, "NONCE") == field_slice(before, s, "NONCE")
    assert field_slice(after, s, "EXPIRE") == field_slice(before, s, "EXPIRE")
    assert be(after, int(s["EXPIRE_OFF"]), int(s["EXPIRE_LEN"])) == 1000 + 60  # this site's wconf TTL
    assert crc_ok(after, s) and crc_ok(rec(j, 1), s)
    # Second site: distinct owners, same clock.
    j2 = jp(); e2 = env(j2, LEASEBOX_NOW="1000", LEASEBOX_CONF=str(wconf(8, 60)))
    ok(run(b, ["grant", "m", "ownM"], e2)); pre_m = rec(j2, 0)
    ok(run(b, ["grant", "b", "ownB"], e2))
    assert field_slice(rec(j2, 0), s, "OWNER") == field_slice(pre_m, s, "OWNER")
    assert field_slice(rec(j2, 0), s, "OWNER").startswith(b"ownM")
    assert field_slice(rec(j2, 1), s, "OWNER").startswith(b"ownB")


def test_compact_survivor_bytes_live(box):
    """Compact that keeps live rows must not blank owner/name/nonce/expiry (site 1)."""
    b, _ = box; s = rules()
    j = jp(); e = env(j, LEASEBOX_NOW="1000", LEASEBOX_CONF=str(wconf(16, 60)))
    ok(run(b, ["grant", "alpha", "ownA"], e))
    pre = survivor_payload(rec(j), s)
    e2 = env(j, LEASEBOX_NOW="1001", LEASEBOX_CONF=str(wconf(16, 60)))
    ok(run(b, ["compact"], e2))
    assert survivor_payload(rec(j), s) == pre
    assert field_slice(rec(j), s, "OWNER").startswith(b"ownA")
    assert crc_ok(rec(j), s)
    ok(run(b, ["verify"], e2))


def test_compact_inactive_expiry_eq_clock_keeps_live(box):
    """Inactive expiry==clock beside a still-live companion: compact keeps only live."""
    b, _ = box; s = rules()
    j = jp()
    plant(j, mk("old", "c", 1000, 11, s, flag=0) + mk("keep", "ownerKeep", 2000, 12, s))
    ok(run(b, ["compact"], env(j, LEASEBOX_NOW="1000")))
    assert j.stat().st_size == int(s["RECORD_BYTES"])
    list_matches(b, env(j, LEASEBOX_NOW="1000"), {"keep"})


def test_compact_survivor_bytes_mixed(box):
    """Compact dropping expired inactive must preserve survivor owner bytes (site 2)."""
    b, _ = box; s = rules()
    j = jp()
    plant(j, mk("old", "c", 999, 11, s, flag=0) + mk("keep", "ownMixSurv", 2000, 12, s))
    pre = survivor_payload(rec(j, 1), s)
    e = env(j, LEASEBOX_NOW="1000")
    ok(run(b, ["compact"], e))
    assert j.stat().st_size == int(s["RECORD_BYTES"])
    assert survivor_payload(rec(j), s) == pre
    assert field_slice(rec(j), s, "OWNER").startswith(b"ownMixSurv")
    assert crc_ok(rec(j), s)
    ok(run(b, ["verify"], e))


def test_nonce_be_encoding_sole_free_1234(box):
    """Sole free NONCE_LEN=2 value 0x1234 must be stored as big-endian (catches LE)."""
    b, _ = box
    T1 = 0x1234
    be2 = wrules(RECORD_BYTES="50", EXPIRE_OFF="38", EXPIRE_LEN="2", NONCE_OFF="40", NONCE_LEN="2", CRC_OFF="42", CRC_LEN="4", PAD_OFF="46", PAD_LEN="4")
    be2s = parse(be2)
    j = jp(); plant_inactive_nonces_except(j, be2s, T1)
    e = env(j, LEASEBOX_RULES=str(be2), LEASEBOX_CONF=str(wconf(16, 60)), LEASEBOX_NOW="1000")
    ok(run(b, ["grant", "slot", "c"], e))
    assert last_nonce(j, be2s) == T1
    assert nonce_raw(j, nrows(j, be2s) - 1, be2s) == T1.to_bytes(2, "big")


def test_nonce_be_encoding_sole_free_abcd(box):
    """Sole free NONCE_LEN=2 value 0xABCD on alternate layout must be big-endian."""
    b, _ = box
    T2 = 0xABCD
    be2b = wrules(RECORD_BYTES="52", EXPIRE_OFF="38", EXPIRE_LEN="2", NONCE_OFF="40", NONCE_LEN="2", CRC_OFF="42", CRC_LEN="4", PAD_OFF="46", PAD_LEN="6")
    be2s = parse(be2b)
    j = jp(); plant_inactive_nonces_except(j, be2s, T2)
    e = env(j, LEASEBOX_RULES=str(be2b), LEASEBOX_CONF=str(wconf(16, 60)), LEASEBOX_NOW="1000")
    ok(run(b, ["grant", "slot", "c"], e))
    assert last_nonce(j, be2s) == T2
    assert nonce_raw(j, nrows(j, be2s) - 1, be2s) == T2.to_bytes(2, "big")


def test_dup_uses_still_live_not_flag_alone(box):
    """Expired flag-set name is not still-live: re-grant must succeed without compact (site 1)."""
    b, _ = box; s = rules()
    j = jp(); e = env(j, LEASEBOX_NOW="1000", LEASEBOX_CONF=str(wconf(16, 15)))
    ok(run(b, ["grant", "alpha", "c1"], e))
    e2 = env(j, LEASEBOX_NOW="1015", LEASEBOX_CONF=str(wconf(16, 15)))
    ok(run(b, ["grant", "alpha", "c2"], e2))  # must not EXIT_DUP
    assert live(j, s, 1015) == 1


def test_dup_expired_prefix_still_live_only(box):
    """Second site: expired uncompacted row does not block same-name grant via flag alone."""
    b, _ = box; s = rules()
    j = jp()
    plant(j, mk("beta", "old", 1000, 5, s, flag=1))  # expiry==clock → not still-live
    e = env(j, LEASEBOX_NOW="1000", LEASEBOX_CONF=str(wconf(16, 60)))
    ok(run(b, ["grant", "beta", "new"], e))  # still-live check, not flag-only
    assert live(j, s, 1000) == 1


def test_gate_zero_max_leases_exit_full(box):
    """MAX_LEASES=0 with MAX_LEASES_MIN=0: empty journal grant is EXIT_FULL (not zero-as-missing→8)."""
    b, _ = box
    alt = wrules(MAX_LEASES_MIN="0")
    rs = parse(alt)
    assert int(rs["MAX_LEASES_MIN"]) <= 0 <= int(rs["MAX_LEASES_MAX"])
    j = jp()
    e = env(j, LEASEBOX_RULES=str(alt), LEASEBOX_CONF=str(wconf(0, 60)), LEASEBOX_NOW="1000")
    ok(run(b, ["grant", "ab", "cd"], e), int(rs["EXIT_FULL"]))
    assert not j.exists() or j.stat().st_size == 0
    # Second site: distinct name/rules text.
    alt2 = wrules(MAX_LEASES_MIN="0", MAX_LEASES_MAX="64")
    rs2 = parse(alt2)
    j2 = jp()
    e2 = env(j2, LEASEBOX_RULES=str(alt2), LEASEBOX_CONF=str(wconf(0, 60)), LEASEBOX_NOW="2000")
    ok(run(b, ["grant", "xy", "z"], e2), int(rs2["EXIT_FULL"]))
    assert not j2.exists() or j2.stat().st_size == 0


def test_gate_zero_ttl_expiry_eq_now(box):
    """TTL_SEC=0 with TTL_MIN=0: grant stores expiry == NOW (not zero-as-missing→60)."""
    b, _ = box
    alt = wrules(TTL_MIN="0")
    rs = parse(alt)
    assert int(rs["TTL_MIN"]) <= 0 <= int(rs["TTL_MAX"])
    j = jp()
    e = env(j, LEASEBOX_RULES=str(alt), LEASEBOX_CONF=str(wconf(8, 0)), LEASEBOX_NOW="1000")
    ok(run(b, ["grant", "ab", "cd"], e))
    assert be(rec(j, 0, rs), int(rs["EXPIRE_OFF"]), int(rs["EXPIRE_LEN"])) == 1000
    # Second site.
    j2 = jp()
    e2 = env(j2, LEASEBOX_RULES=str(alt), LEASEBOX_CONF=str(wconf(8, 0)), LEASEBOX_NOW="2000")
    ok(run(b, ["grant", "xy", "z"], e2))
    assert be(rec(j2, 0, rs), int(rs["EXPIRE_OFF"]), int(rs["EXPIRE_LEN"])) == 2000


def test_gate_explicit_plus_signed_decimals(box):
    """Gate MAX_LEASES=+N TTL_SEC=+T must parse (rejects ^-?[0-9]+$ validators)."""
    b, _ = box
    s = rules()
    # Non-default +9/+61: minus-only validators that fall back to 8/60 encode 1060 instead of 1061.
    j = jp()
    e = env(j, LEASEBOX_CONF=str(wconf("+9", "+61")), LEASEBOX_NOW="1000")
    ok(run(b, ["grant", "ab", "cd"], e))
    assert be(rec(j), int(s["EXPIRE_OFF"]), int(s["EXPIRE_LEN"])) == 1061
    list_has(b, e, "ab")
    # MAX_LEASES=+9 must be decisive: nine distinct grants succeed; a tenth is EXIT_FULL.
    # (A plus-rejecting MAX_LEASES parser that falls back to 8 fails on the 9th grant.)
    for i in range(1, 9):
        ok(run(b, ["grant", f"n{i}", "c"], e))
    list_matches(b, e, {"ab"} | {f"n{i}" for i in range(1, 9)})
    assert live(j, s, 1000) == 9
    ok(run(b, ["grant", "overflow", "c"], e), int(s["EXIT_FULL"]))
    # Second site: different NOW / names; still non-default positives.
    j2 = jp()
    e2 = env(j2, LEASEBOX_CONF=str(wconf("+9", "+61")), LEASEBOX_NOW="2000")
    ok(run(b, ["grant", "xy", "z"], e2))
    assert be(rec(j2), int(s["EXPIRE_OFF"]), int(s["EXPIRE_LEN"])) == 2061
    for i in range(8):
        ok(run(b, ["grant", f"m{i}", "c"], e2))
    ok(run(b, ["grant", "full9", "c"], e2), int(s["EXIT_FULL"]))
    assert live(j2, s, 2000) == 9
    # Third site: +8/+60 still legal (catches exit-1 on any '+').
    j3 = jp()
    e3 = env(j3, LEASEBOX_CONF=str(wconf("+8", "+60")), LEASEBOX_NOW="1000")
    ok(run(b, ["grant", "pq", "r"], e3))
    assert be(rec(j3), int(s["EXPIRE_OFF"]), int(s["EXPIRE_LEN"])) == 1060
    for i in range(7):
        ok(run(b, ["grant", f"p{i}", "c"], e3))
    ok(run(b, ["grant", "cap8", "c"], e3), int(s["EXIT_FULL"]))
    assert live(j3, s, 1000) == 8


def test_nonce_3byte_past_65536_issued(box):
    """NONCE_LEN=3 after 65536 issued encodings: grant must admit some unused 3-byte nonce (any policy)."""
    b, _ = box
    tri = wrules(RECORD_BYTES="57", EXPIRE_OFF="38", EXPIRE_LEN="3", NONCE_OFF="41", NONCE_LEN="3", CRC_OFF="44", PAD_OFF="48", PAD_LEN="9")
    t3 = parse(tri)
    j = jp()
    plant_inactive_nonces(j, t3, range(65536))
    e = env(j, LEASEBOX_RULES=str(tri), LEASEBOX_CONF=str(wconf(8, 60)), LEASEBOX_NOW="1000")
    ok(run(b, ["grant", "slot", "c"], e))
    n = last_nonce(j, t3)
    assert n not in range(65536), n
    assert 0 <= n < (1 << 24)
    assert len(nonce_raw(j, nrows(j, t3) - 1, t3)) == 3
    # Second site: alternate layout still past the two-byte ceiling.
    tri2 = wrules(RECORD_BYTES="58", EXPIRE_OFF="38", EXPIRE_LEN="3", NONCE_OFF="41", NONCE_LEN="3", CRC_OFF="44", PAD_OFF="48", PAD_LEN="10")
    t32 = parse(tri2)
    j2 = jp()
    plant_inactive_nonces(j2, t32, range(65536))
    e2 = env(j2, LEASEBOX_RULES=str(tri2), LEASEBOX_CONF=str(wconf(8, 60)), LEASEBOX_NOW="1000")
    ok(run(b, ["grant", "next", "c"], e2))
    n2 = last_nonce(j2, t32)
    assert n2 not in range(65536), n2
    assert 0 <= n2 < (1 << 24)


def test_nonce_len_zero_admits_empty_encoding(box):
    """NONCE_LEN=0: the sole empty encoding admits once; stays issued across revoke."""
    b, _ = box
    zero = wrules(NONCE_LEN="0", CRC_OFF="46", PAD_OFF="50", PAD_LEN="14")
    zs = parse(zero)
    assert int(zs["NONCE_LEN"]) == 0
    j = jp()
    e = env(j, LEASEBOX_RULES=str(zero), LEASEBOX_CONF=str(wconf(16, 60)), LEASEBOX_NOW="1000")
    ok(run(b, ["grant", "n", "o"], e))
    assert j.stat().st_size == int(zs["RECORD_BYTES"])
    assert field_slice(rec(j, 0, zs), zs, "NONCE") == b""
    ok(run(b, ["grant", "m", "o"], e), 1)
    assert nrows(j, zs) == 1
    # Issued nonces survive revoke: empty encoding remains exhausted.
    ok(run(b, ["revoke", "n", "x"], e))
    ok(run(b, ["grant", "p", "o"], e), 1)
    # Second site: fresh journal still admits the empty encoding once.
    j2 = jp()
    e2 = env(j2, LEASEBOX_RULES=str(zero), LEASEBOX_CONF=str(wconf(16, 60)), LEASEBOX_NOW="1000")
    ok(run(b, ["grant", "p", "o"], e2))
    assert field_slice(rec(j2, 0, zs), zs, "NONCE") == b""


def test_revoke_two_live_same_prefix_alt_layout(box):
    """Second site: two live same-prefix rows under alternate NAME_LEN layout."""
    b, _ = box
    alt = wrules(
        MAGIC="ABCDEFGHI", RECORD_BYTES="64", NAME_OFF="12", NAME_LEN="8", OWNER_OFF="28", OWNER_LEN="8",
        EXPIRE_OFF="36", EXPIRE_LEN="9", NONCE_OFF="45", NONCE_LEN="8", CRC_OFF="53", CRC_LEN="5",
        PAD_OFF="58", PAD_LEN="6", EXIT_DUP="7", EXIT_FULL="8", EXIT_CRC="9",
    )
    rs = parse(alt)
    j = jp()
    plant(j, mk("twinxx", "a", 5000, 1, rs) + mk("twinxx", "b", 5000, 2, rs))
    e = env(j, LEASEBOX_RULES=str(alt), LEASEBOX_CONF=str(wconf(8, 60)), LEASEBOX_NOW="1000")
    assert live(j, rs, 1000) == 2
    ok(run(b, ["revoke", "twinxx", "x"], e))
    list_matches(b, e, set())
    assert live(j, rs, 1000) == 0
    assert j.stat().st_size == 0
    # Inactive + expired same prefix under alt layout: both stored matches must disappear.
    j2 = jp()
    plant(
        j2,
        mk("twinxx", "a", 5000, 3, rs, flag=0) + mk("twinxx", "b", 999, 4, rs, flag=1),
    )
    e2 = env(j2, LEASEBOX_RULES=str(alt), LEASEBOX_CONF=str(wconf(8, 60)), LEASEBOX_NOW="1000")
    assert live(j2, rs, 1000) == 0
    ok(run(b, ["revoke", "twinxx", "x"], e2))
    assert j2.stat().st_size == 0
    assert nrows(j2, rs) == 0


def test_compact_report_same_name_alt_layout(box):
    """Alt layout: compact keeps both unexpired same-name rows; report counts rows not names."""
    b, _ = box
    alt = wrules(
        MAGIC="ABCDEFGHI", RECORD_BYTES="64", NAME_OFF="12", NAME_LEN="8", OWNER_OFF="28", OWNER_LEN="8",
        EXPIRE_OFF="36", EXPIRE_LEN="9", NONCE_OFF="45", NONCE_LEN="8", CRC_OFF="53", CRC_LEN="5",
        PAD_OFF="58", PAD_LEN="6", EXIT_DUP="7", EXIT_FULL="8", EXIT_CRC="9",
    )
    rs = parse(alt)
    j = jp()
    plant(j, mk("twinxx", "a", 5000, 1, rs) + mk("twinxx", "b", 5000, 2, rs))
    e = env(j, LEASEBOX_RULES=str(alt), LEASEBOX_CONF=str(wconf(8, 60)), LEASEBOX_NOW="1000")
    assert_report(report(b, e), "ABCDEFGHI", 2, 2)
    before = j.read_bytes()
    ok(run(b, ["compact"], e))
    assert j.read_bytes() == before
    assert nrows(j, rs) == 2
    assert_report(report(b, e), "ABCDEFGHI", 2, 2)
    # Inactive + live same prefix under alt: compact retains both; active=1.
    j2 = jp()
    plant(j2, mk("twinxx", "a", 5000, 3, rs, flag=0) + mk("twinxx", "b", 5000, 4, rs, flag=1))
    e2 = env(j2, LEASEBOX_RULES=str(alt), LEASEBOX_CONF=str(wconf(8, 60)), LEASEBOX_NOW="1000")
    assert_report(report(b, e2), "ABCDEFGHI", 2, 1)
    ok(run(b, ["compact"], e2))
    assert j2.stat().st_size == 2 * int(rs["RECORD_BYTES"])
    assert_report(report(b, e2), "ABCDEFGHI", 2, 1)


def test_gate_out_of_band_rejected(box):
    """Out-of-band gate (below min or above max) must reject grant with no journal write."""
    b, _ = box
    s = rules()
    assert int(s["MAX_LEASES_MIN"]) == 8 and int(s["TTL_MIN"]) == 15
    assert int(s["MAX_LEASES_MAX"]) == 64 and int(s["TTL_MAX"]) == 300
    j = jp()
    e = env(j, LEASEBOX_CONF=str(wconf(7, 15)), LEASEBOX_NOW="1000")
    p = run(b, ["grant", "n", "o"], e)
    assert p.returncode != 0
    assert not j.exists() or j.stat().st_size == 0
    # Second site: TTL below TTL_MIN with an otherwise legal MAX_LEASES.
    j2 = jp()
    e2 = env(j2, LEASEBOX_CONF=str(wconf(8, 14)), LEASEBOX_NOW="1000")
    p2 = run(b, ["grant", "n", "o"], e2)
    assert p2.returncode != 0
    assert not j2.exists() or j2.stat().st_size == 0
    # Upper band: MAX_LEASES above MAX_LEASES_MAX without raising the rules ceiling.
    j3 = jp()
    e3 = env(j3, LEASEBOX_CONF=str(wconf(65, 60)), LEASEBOX_NOW="1000")
    p3 = run(b, ["grant", "n", "o"], e3)
    assert p3.returncode != 0
    assert not j3.exists() or j3.stat().st_size == 0
    # Upper band: TTL_SEC above TTL_MAX without raising the rules ceiling.
    j4 = jp()
    e4 = env(j4, LEASEBOX_CONF=str(wconf(8, 301)), LEASEBOX_NOW="1000")
    p4 = run(b, ["grant", "n", "o"], e4)
    assert p4.returncode != 0
    assert not j4.exists() or j4.stat().st_size == 0
    # Second upper-cap site: different NOW; still no write under MAX_LEASES=65.
    j5 = jp()
    e5 = env(j5, LEASEBOX_CONF=str(wconf(65, 60)), LEASEBOX_NOW="2000")
    p5 = run(b, ["grant", "ab", "cd"], e5)
    assert p5.returncode != 0
    assert not j5.exists() or j5.stat().st_size == 0
    # Second upper-TTL site.
    j6 = jp()
    e6 = env(j6, LEASEBOX_CONF=str(wconf(8, 301)), LEASEBOX_NOW="2000")
    p6 = run(b, ["grant", "xy", "z"], e6)
    assert p6.returncode != 0
    assert not j6.exists() or j6.stat().st_size == 0


def test_gate_leading_zero_decimal(box):
    """Leading-zero gate decimals are base-10 (017 → 17), not octal (base-0 ParseInt)."""
    b, _ = box
    s = rules()
    j = jp()
    e = env(j, LEASEBOX_CONF=str(wconf(8, "017")), LEASEBOX_NOW="1000")
    ok(run(b, ["grant", "ab", "cd"], e))
    assert be(rec(j), int(s["EXPIRE_OFF"]), int(s["EXPIRE_LEN"])) == 1017
    list_has(b, e, "ab")
    # Second site: leading-zero MAX_LEASES and TTL under a different NOW.
    j2 = jp()
    e2 = env(j2, LEASEBOX_CONF=str(wconf("08", "060")), LEASEBOX_NOW="2000")
    ok(run(b, ["grant", "xy", "z"], e2))
    assert be(rec(j2), int(s["EXPIRE_OFF"]), int(s["EXPIRE_LEN"])) == 2060
    for i in range(7):
        ok(run(b, ["grant", f"n{i}", "c"], e2))
    ok(run(b, ["grant", "overflow", "c"], e2), int(s["EXIT_FULL"]))
    assert live(j2, s, 2000) == 8


def test_gate_raised_dynamic_lower_band(box):
    """Raised TTL_MIN / MAX_LEASES_MIN must reject gates below the new minimum."""
    b, _ = box
    hi_ttl = wrules(TTL_MIN="100")
    rs = parse(hi_ttl)
    assert int(rs["TTL_MIN"]) == 100
    j = jp()
    e = env(j, LEASEBOX_RULES=str(hi_ttl), LEASEBOX_CONF=str(wconf(8, 99)), LEASEBOX_NOW="1000")
    p = run(b, ["grant", "n", "o"], e)
    assert p.returncode != 0
    assert not j.exists() or j.stat().st_size == 0
    # Second site: raised MAX_LEASES_MIN rejects MAX_LEASES just below it.
    hi_cap = wrules(MAX_LEASES_MIN="10")
    rs2 = parse(hi_cap)
    assert int(rs2["MAX_LEASES_MIN"]) == 10
    j2 = jp()
    e2 = env(j2, LEASEBOX_RULES=str(hi_cap), LEASEBOX_CONF=str(wconf(9, 60)), LEASEBOX_NOW="1000")
    p2 = run(b, ["grant", "n", "o"], e2)
    assert p2.returncode != 0
    assert not j2.exists() or j2.stat().st_size == 0
    # Control: values at/above the raised floors still admit.
    j3 = jp()
    e3 = env(j3, LEASEBOX_RULES=str(hi_ttl), LEASEBOX_CONF=str(wconf(8, 100)), LEASEBOX_NOW="1000")
    ok(run(b, ["grant", "ok", "o"], e3))
    assert be(rec(j3, 0, rs), int(rs["EXPIRE_OFF"]), int(rs["EXPIRE_LEN"])) == 1100


def test_gate_ttl_above_shipped_ceiling(box):
    """TTL_MAX raised above the shipped 300: TTL_SEC=301 must encode NOW+301 (not clamp at 300)."""
    b, _ = box
    s = rules()
    assert int(s["TTL_MAX"]) == 300
    hi = wrules(TTL_MAX="301")
    rs = parse(hi)
    assert int(rs["TTL_MAX"]) == 301
    j = jp()
    e = env(j, LEASEBOX_RULES=str(hi), LEASEBOX_CONF=str(wconf(8, 301)), LEASEBOX_NOW="1000")
    ok(run(b, ["grant", "ab", "cd"], e))
    assert be(rec(j, 0, rs), int(rs["EXPIRE_OFF"]), int(rs["EXPIRE_LEN"])) == 1301
    list_has(b, e, "ab")
    # Second site: different NOW; still rejects a hardcoded min(TTL, 300) clamp.
    j2 = jp()
    e2 = env(j2, LEASEBOX_RULES=str(hi), LEASEBOX_CONF=str(wconf(8, 301)), LEASEBOX_NOW="2000")
    ok(run(b, ["grant", "xy", "z"], e2))
    assert be(rec(j2, 0, rs), int(rs["EXPIRE_OFF"]), int(rs["EXPIRE_LEN"])) == 2301


def test_gate_ttl_negative_in_band(box):
    """TTL_SEC=-1 with TTL_MIN=-1 in band: grant encodes NOW-1 (rejects nonnegative-only parsers)."""
    b, _ = box
    alt = wrules(TTL_MIN="-1")
    rs = parse(alt)
    assert int(rs["TTL_MIN"]) <= -1 <= int(rs["TTL_MAX"])
    j = jp()
    e = env(j, LEASEBOX_RULES=str(alt), LEASEBOX_CONF=str(wconf(8, -1)), LEASEBOX_NOW="1000")
    ok(run(b, ["grant", "ab", "cd"], e))
    assert be(rec(j, 0, rs), int(rs["EXPIRE_OFF"]), int(rs["EXPIRE_LEN"])) == 999
    # Second site: different negative TTL and NOW (unsigned parsers fall back to 60 → 1060).
    j2 = jp()
    e2 = env(j2, LEASEBOX_RULES=str(alt), LEASEBOX_CONF=str(wconf(8, -1)), LEASEBOX_NOW="2000")
    ok(run(b, ["grant", "xy", "z"], e2))
    assert be(rec(j2, 0, rs), int(rs["EXPIRE_OFF"]), int(rs["EXPIRE_LEN"])) == 1999


def test_gate_max_leases_negative_in_band(box):
    """MAX_LEASES=-1 in band: empty journal already at/over cap → EXIT_FULL (not unsigned fallback 8)."""
    b, _ = box
    alt = wrules(MAX_LEASES_MIN="-1", MAX_LEASES_MAX="-1")
    rs = parse(alt)
    assert int(rs["MAX_LEASES_MIN"]) <= -1 <= int(rs["MAX_LEASES_MAX"])
    j = jp()
    e = env(j, LEASEBOX_RULES=str(alt), LEASEBOX_CONF=str(wconf(-1, 60)), LEASEBOX_NOW="1000")
    ok(run(b, ["grant", "ab", "cd"], e), int(rs["EXIT_FULL"]))
    assert not j.exists() or j.stat().st_size == 0
    # Second site: MAX_LEASES=-2 under matching band.
    alt2 = wrules(MAX_LEASES_MIN="-2", MAX_LEASES_MAX="-2")
    rs2 = parse(alt2)
    j2 = jp()
    e2 = env(j2, LEASEBOX_RULES=str(alt2), LEASEBOX_CONF=str(wconf(-2, 60)), LEASEBOX_NOW="1000")
    ok(run(b, ["grant", "xy", "z"], e2), int(rs2["EXIT_FULL"]))


def test_report_missing_journal(box):
    """Report on a journal path that does not exist: exit 0, magic from rules, records=0, active=0.

    Distinct from plant(empty): do NOT create the file before report.
    Does not assert post-report filesystem side-effects (creating or not).
    """
    b, _ = box
    s = rules()
    missing = jp()  # Path only; never plant
    assert not missing.exists()
    e = env(missing)
    assert_report(report(b, e), str(s["MAGIC"]), 0, 0)


def test_report_rewrites_existing_regular_file(box):
    """Report must overwrite an existing regular lease-report.json (not create-once)."""
    b, _ = box
    s = rules()
    j = jp()
    e = env(j, LEASEBOX_NOW="1000", LEASEBOX_CONF=str(wconf(8, 60)))
    # First write via helper (clears path): empty journal → records=0 active=0.
    assert_report(report(b, e), str(s["MAGIC"]), 0, 0)
    assert REPORT.is_file() and not REPORT.is_symlink()
    stale = REPORT.read_bytes()
    ok(run(b, ["grant", "ab", "cd"], e))
    # Second report WITHOUT deleting the existing regular file.
    p = run(b, ["report"], e)
    ok(p)
    assert REPORT.is_file() and not REPORT.is_symlink()
    data = loads_json(REPORT.read_text())
    assert_report(data, str(s["MAGIC"]), 1, 1)
    assert REPORT.read_bytes() != stale
    # Second site: another journal transition (0→1 live) leaving the prior regular report in place.
    j2 = jp()
    e2 = env(j2, LEASEBOX_NOW="2000", LEASEBOX_CONF=str(wconf(8, 60)))
    assert_report(report(b, e2), str(s["MAGIC"]), 0, 0)
    stale2 = REPORT.read_bytes()
    plant(j2, mk("zz", "o", 3000, 9, s))
    p2 = run(b, ["report"], e2)
    ok(p2)
    data2 = loads_json(REPORT.read_text())
    assert_report(data2, str(s["MAGIC"]), 1, 1)
    assert REPORT.read_bytes() != stale2


def test_list_rejects_expired_in_top_level_name(box):
    """Leases-array listing must be live-only; wrapper-level name is unrestricted metadata."""
    # Legal: leases array names only live beta; top-level name may be app metadata.
    dual = {"leases": [{"name": "beta"}], "name": "leasebox"}
    expected = {"beta"}
    assert expected in name_sets(dual)
    assert lease_array_listings(dual) == [{"beta"}]
    # Expired names must not appear inside the leases/items arrays themselves.
    bad_arr = {"leases": [{"name": "beta"}, {"name": "alpha"}], "name": "leasebox"}
    assert lease_array_listings(bad_arr) == [{"beta", "alpha"}]
    # Contract-legal name-keyed owner map.
    owner_map = {"name": "o", "magic": "o", "owner": "o", "leases": "o", "items": "o"}
    five = {"name", "magic", "owner", "leases", "items"}
    assert five in name_sets(owner_map)
    assert lease_array_listings(owner_map) == []
    # Name-keyed clash with array-valued leases: keys == expected → accepted.
    clash_arr = {"name": "o", "magic": "o", "owner": "o", "leases": [{"name": "name"}], "items": "o"}
    assert five in name_sets(clash_arr)
    assert {str(k) for k in clash_arr} == five
    # Live binary path: mixed journal at NOW between the two expiries.
    b, _ = box
    s = rules()
    j = jp()
    plant(j, mk("alpha", "c1", 1015, 1, s) + mk("beta", "c2", 1300, 2, s))
    list_matches(b, env(j, LEASEBOX_NOW="1020"), {"beta"})


def test_report_out_unix_socket_obstacle(box):
    """Report replaces a Unix-domain socket obstacle (general non-regular, not only named examples)."""
    b, _ = box
    s = rules()
    j = jp()
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(REPORT.parent, 0o1777)
    if REPORT.exists() or REPORT.is_symlink():
        if REPORT.is_dir() and not REPORT.is_symlink():
            shutil.rmtree(REPORT)
        else:
            REPORT.unlink(missing_ok=True)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(str(REPORT))
    sock.close()
    os.chown(REPORT, 12000, 12000)
    os.chmod(REPORT, 0o666)
    assert stat.S_ISSOCK(REPORT.lstat().st_mode)
    t0 = time.time()
    p = run(b, ["report"], env(j))
    assert time.time() - t0 < 20
    ok(p)
    assert REPORT.is_file() and not REPORT.is_symlink()
    assert_report(loads_json(REPORT.read_text()), str(s["MAGIC"]), 0, 0)
    # Second site: socket obstacle + one live row.
    j2 = jp()
    plant(j2, mk("a", "c", 2000, 1, s))
    if REPORT.exists() or REPORT.is_symlink():
        if REPORT.is_dir() and not REPORT.is_symlink():
            shutil.rmtree(REPORT)
        else:
            REPORT.unlink(missing_ok=True)
    sock2 = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock2.bind(str(REPORT))
    sock2.close()
    os.chown(REPORT, 12000, 12000)
    os.chmod(REPORT, 0o666)
    t0 = time.time()
    p = run(b, ["report"], env(j2, LEASEBOX_NOW="1000"))
    assert time.time() - t0 < 20
    ok(p)
    assert REPORT.is_file() and not REPORT.is_symlink()
    assert_report(loads_json(REPORT.read_text()), str(s["MAGIC"]), 1, 1)


def test_report_out_fifo_respects_deadline(box):
    """Report must finish within the 20s command limit when lease-report.json is an unread FIFO."""
    b, _ = box
    s = rules()
    j = jp()
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(REPORT.parent, 0o1777)
    if REPORT.exists() or REPORT.is_symlink():
        REPORT.unlink(missing_ok=True)
    os.mkfifo(REPORT)
    os.chown(REPORT, 12000, 12000)
    os.chmod(REPORT, 0o666)
    t0 = time.time()
    p = run(b, ["report"], env(j))
    assert time.time() - t0 < 20
    ok(p)
    assert REPORT.is_file() and not REPORT.is_symlink()
    data = loads_json(REPORT.read_text())
    assert_report(data, str(s["MAGIC"]), 0, 0)
    # Second site: one live row; same FIFO obstacle on the disclosed report path.
    j2 = jp()
    plant(j2, mk("a", "c", 2000, 1, s))
    if REPORT.exists() or REPORT.is_symlink():
        REPORT.unlink(missing_ok=True)
    os.mkfifo(REPORT)
    os.chown(REPORT, 12000, 12000)
    os.chmod(REPORT, 0o666)
    t0 = time.time()
    p = run(b, ["report"], env(j2, LEASEBOX_NOW="1000"))
    assert time.time() - t0 < 20
    ok(p)
    assert REPORT.is_file() and not REPORT.is_symlink()
    data = loads_json(REPORT.read_text())
    assert_report(data, str(s["MAGIC"]), 1, 1)


def test_report_out_directory_obstacle(box):
    """Report replaces a directory at lease-report.json (empty and nonempty) with a regular file."""
    b, _ = box
    s = rules()
    j = jp()
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(REPORT.parent, 0o1777)
    if REPORT.exists() or REPORT.is_symlink():
        if REPORT.is_dir() and not REPORT.is_symlink():
            shutil.rmtree(REPORT)
        else:
            REPORT.unlink(missing_ok=True)
    REPORT.mkdir()
    os.chown(REPORT, 12000, 12000)
    os.chmod(REPORT, 0o777)
    t0 = time.time()
    p = run(b, ["report"], env(j))
    assert time.time() - t0 < 20
    ok(p)
    assert REPORT.is_file() and not REPORT.is_dir()
    assert_report(loads_json(REPORT.read_text()), str(s["MAGIC"]), 0, 0)
    # Nonempty directory obstacle (Remove would fail; RemoveAll required).
    if REPORT.exists():
        REPORT.unlink()
    REPORT.mkdir()
    child = REPORT / "child"
    child.write_text("x")
    os.chown(REPORT, 12000, 12000)
    os.chown(child, 12000, 12000)
    os.chmod(REPORT, 0o777)
    j2 = jp()
    plant(j2, mk("a", "c", 2000, 1, s))
    t0 = time.time()
    p = run(b, ["report"], env(j2, LEASEBOX_NOW="1000"))
    assert time.time() - t0 < 20
    ok(p)
    assert REPORT.is_file() and not REPORT.is_dir()
    assert_report(loads_json(REPORT.read_text()), str(s["MAGIC"]), 1, 1)


def test_verify_crc_record_index_3(box):
    """CRC fault in record index 3 (fourth of four) must be EXIT_CRC — not only indices 0–2."""
    b, _ = box
    s = rules()
    j = jp()
    e = env(j)
    for name in ("r0", "r1", "r2", "r3"):
        ok(run(b, ["grant", name, "c"], e))
    ok(run(b, ["verify"], e))
    assert nrows(j) == 4
    bad = bytearray(j.read_bytes())
    bad[3 * int(s["RECORD_BYTES"]) + 7] ^= 0x7F
    j.write_bytes(bad)
    ok(run(b, ["verify"], e), int(s["EXIT_CRC"]))


def test_verify_crc_record_index_64(box):
    """CRC fault past a plausible MAX_LEASES_MAX=64 scan limit must still be EXIT_CRC."""
    b, _ = box
    s = rules()
    assert int(s["MAX_LEASES_MAX"]) == 64
    j = jp()
    plant(j, b"".join(mk(f"r{i}", "c", 2000, i + 1, s) for i in range(65)))
    e = env(j, LEASEBOX_NOW="1000")
    ok(run(b, ["verify"], e))
    assert nrows(j) == 65
    bad = bytearray(j.read_bytes())
    bad[64 * int(s["RECORD_BYTES"]) + 7] ^= 0x7F
    j.write_bytes(bad)
    ok(run(b, ["verify"], e), int(s["EXIT_CRC"]))
    # Second site: index 64 under a planted inactive+live mix still scanned.
    j2 = jp()
    plant(j2, b"".join(mk(f"s{i}", "c", 2000, i + 1, s, flag=(0 if i % 2 else 1)) for i in range(65)))
    e2 = env(j2, LEASEBOX_NOW="1000")
    ok(run(b, ["verify"], e2))
    bad2 = bytearray(j2.read_bytes())
    bad2[64 * int(s["RECORD_BYTES"]) + 8] ^= 0x01
    j2.write_bytes(bad2)
    ok(run(b, ["verify"], e2), int(s["EXIT_CRC"]))


def test_report_out_block_device_obstacle(box):
    """Report replaces a block-device node (not only chr/fifo/sock/dir/symlink enums)."""
    b, _ = box
    s = rules()
    j = jp()
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(REPORT.parent, 0o1777)
    if REPORT.exists() or REPORT.is_symlink():
        if REPORT.is_dir() and not REPORT.is_symlink():
            shutil.rmtree(REPORT)
        else:
            REPORT.unlink(missing_ok=True)
    # Unconfigured loop-style block device: major 7 minor 0.
    os.mknod(REPORT, 0o666 | stat.S_IFBLK, os.makedev(7, 0))
    os.chown(REPORT, 12000, 12000)
    assert stat.S_ISBLK(REPORT.lstat().st_mode)
    t0 = time.time()
    p = run(b, ["report"], env(j))
    assert time.time() - t0 < 20
    ok(p)
    assert REPORT.is_file() and not REPORT.is_symlink()
    assert not stat.S_ISBLK(REPORT.lstat().st_mode)
    assert_report(loads_json(REPORT.read_text()), str(s["MAGIC"]), 0, 0)
    # Second site: one live row; same block-device obstacle.
    j2 = jp()
    plant(j2, mk("a", "c", 2000, 1, s))
    if REPORT.exists() or REPORT.is_symlink():
        if REPORT.is_dir() and not REPORT.is_symlink():
            shutil.rmtree(REPORT)
        else:
            REPORT.unlink(missing_ok=True)
    os.mknod(REPORT, 0o666 | stat.S_IFBLK, os.makedev(7, 0))
    os.chown(REPORT, 12000, 12000)
    t0 = time.time()
    p = run(b, ["report"], env(j2, LEASEBOX_NOW="1000"))
    assert time.time() - t0 < 20
    ok(p)
    assert REPORT.is_file() and not REPORT.is_symlink()
    assert not stat.S_ISBLK(REPORT.lstat().st_mode)
    assert_report(loads_json(REPORT.read_text()), str(s["MAGIC"]), 1, 1)


def test_report_out_char_device_obstacle(box):
    """Report replaces a character-device node (e.g. /dev/null major:minor) via Lstat, not WriteFile-through."""
    b, _ = box
    s = rules()
    j = jp()
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(REPORT.parent, 0o1777)
    if REPORT.exists() or REPORT.is_symlink():
        if REPORT.is_dir() and not REPORT.is_symlink():
            shutil.rmtree(REPORT)
        else:
            REPORT.unlink(missing_ok=True)
    # /dev/null-equivalent: char device major 1 minor 3.
    os.mknod(REPORT, 0o666 | stat.S_IFCHR, os.makedev(1, 3))
    os.chown(REPORT, 12000, 12000)
    assert stat.S_ISCHR(REPORT.lstat().st_mode)
    t0 = time.time()
    p = run(b, ["report"], env(j))
    assert time.time() - t0 < 20
    ok(p)
    assert REPORT.is_file() and not REPORT.is_symlink()
    assert not stat.S_ISCHR(REPORT.lstat().st_mode)
    assert_report(loads_json(REPORT.read_text()), str(s["MAGIC"]), 0, 0)
    # Second site: one live row; same char-device obstacle.
    j2 = jp()
    plant(j2, mk("a", "c", 2000, 1, s))
    if REPORT.exists() or REPORT.is_symlink():
        if REPORT.is_dir() and not REPORT.is_symlink():
            shutil.rmtree(REPORT)
        else:
            REPORT.unlink(missing_ok=True)
    os.mknod(REPORT, 0o666 | stat.S_IFCHR, os.makedev(1, 3))
    os.chown(REPORT, 12000, 12000)
    t0 = time.time()
    p = run(b, ["report"], env(j2, LEASEBOX_NOW="1000"))
    assert time.time() - t0 < 20
    ok(p)
    assert REPORT.is_file() and not REPORT.is_symlink()
    assert not stat.S_ISCHR(REPORT.lstat().st_mode)
    assert_report(loads_json(REPORT.read_text()), str(s["MAGIC"]), 1, 1)


def test_report_out_dangling_symlink_obstacle(box):
    """Report must replace a dangling symlink (Lstat), not skip via following-Stat ENOENT."""
    b, _ = box
    s = rules()
    j = jp()
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(REPORT.parent, 0o1777)
    if REPORT.exists() or REPORT.is_symlink():
        if REPORT.is_dir() and not REPORT.is_symlink():
            shutil.rmtree(REPORT)
        else:
            REPORT.unlink(missing_ok=True)
    missing = WORKDIR / f"missing_rep_{secrets.token_hex(4)}.json"
    if missing.exists():
        missing.unlink()
    os.symlink(missing, REPORT)
    os.chown(REPORT, 12000, 12000, follow_symlinks=False)
    assert REPORT.is_symlink() and not missing.exists()
    t0 = time.time()
    p = run(b, ["report"], env(j))
    assert time.time() - t0 < 20
    ok(p)
    assert REPORT.is_file() and not REPORT.is_symlink()
    assert_report(loads_json(REPORT.read_text()), str(s["MAGIC"]), 0, 0)
    # Second site: one live row; same dangling-symlink obstacle.
    j2 = jp()
    plant(j2, mk("a", "c", 2000, 1, s))
    if REPORT.exists() or REPORT.is_symlink():
        REPORT.unlink(missing_ok=True)
    missing2 = WORKDIR / f"missing_rep2_{secrets.token_hex(4)}.json"
    if missing2.exists():
        missing2.unlink()
    os.symlink(missing2, REPORT)
    os.chown(REPORT, 12000, 12000, follow_symlinks=False)
    t0 = time.time()
    p = run(b, ["report"], env(j2, LEASEBOX_NOW="1000"))
    assert time.time() - t0 < 20
    ok(p)
    assert REPORT.is_file() and not REPORT.is_symlink()
    assert_report(loads_json(REPORT.read_text()), str(s["MAGIC"]), 1, 1)


def test_report_out_symlink_to_regular_obstacle(box):
    """Report must replace a symlink-to-regular-file (Lstat), not write through Stat."""
    b, _ = box
    s = rules()
    j = jp()
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(REPORT.parent, 0o1777)
    if REPORT.exists() or REPORT.is_symlink():
        if REPORT.is_dir() and not REPORT.is_symlink():
            shutil.rmtree(REPORT)
        else:
            REPORT.unlink(missing_ok=True)
    target = WORKDIR / f"rep_target_{secrets.token_hex(4)}.json"
    target.write_text('{"magic":"STALE","records":9,"active":9}\n')
    os.chown(target, 12000, 12000)
    os.chmod(target, 0o666)
    os.symlink(target, REPORT)
    os.chown(REPORT, 12000, 12000, follow_symlinks=False)
    assert REPORT.is_symlink()
    t0 = time.time()
    p = run(b, ["report"], env(j))
    assert time.time() - t0 < 20
    ok(p)
    assert REPORT.is_file() and not REPORT.is_symlink()
    assert_report(loads_json(REPORT.read_text()), str(s["MAGIC"]), 0, 0)
    # Second site: one live row; same symlink obstacle.
    j2 = jp()
    plant(j2, mk("a", "c", 2000, 1, s))
    if REPORT.exists() or REPORT.is_symlink():
        REPORT.unlink(missing_ok=True)
    target2 = WORKDIR / f"rep_target2_{secrets.token_hex(4)}.json"
    target2.write_text('{"magic":"STALE2","records":3,"active":3}\n')
    os.chown(target2, 12000, 12000)
    os.chmod(target2, 0o666)
    os.symlink(target2, REPORT)
    os.chown(REPORT, 12000, 12000, follow_symlinks=False)
    t0 = time.time()
    p = run(b, ["report"], env(j2, LEASEBOX_NOW="1000"))
    assert time.time() - t0 < 20
    ok(p)
    assert REPORT.is_file() and not REPORT.is_symlink()
    assert_report(loads_json(REPORT.read_text()), str(s["MAGIC"]), 1, 1)


def test_empty_magic_grant_and_verify(box):
    """MAGIC= (empty) is legal: grant writes clean records; verify must exit 0."""
    b, _ = box
    empty = wrules(MAGIC="")
    rs = parse(empty)
    assert str(rs["MAGIC"]) == ""
    j = jp()
    e = env(j, LEASEBOX_RULES=str(empty), LEASEBOX_CONF=str(wconf(16, 60)), LEASEBOX_NOW="1700000000")
    ok(run(b, ["grant", "alpha", "owner"], e))
    assert j.stat().st_size == int(rs["RECORD_BYTES"])
    raw = rec(j, 0, rs)
    assert raw[0] == int(rs["VERSION"]) and raw[1] == 1
    ok(run(b, ["verify"], e))
    list_has(b, e, "alpha")
    # Second site: different NOW / name under empty MAGIC.
    j2 = jp()
    e2 = env(j2, LEASEBOX_RULES=str(empty), LEASEBOX_CONF=str(wconf(16, 60)), LEASEBOX_NOW="1000")
    ok(run(b, ["grant", "beta", "o"], e2))
    ok(run(b, ["verify"], e2))
    list_matches(b, e2, {"beta"})

