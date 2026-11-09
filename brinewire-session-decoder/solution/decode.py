#!/usr/bin/env python3
"""BW-2026 Brinewire capture session decoder."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any


DISPOSITION_ORDER = [
    "ok",
    "reject_syntax",
    "reject_flag",
    "reject_nested_encap",
]

MAGIC = 0x42
KIND_DATA = 0x01
KIND_ACK = 0x02
KIND_ENCAP = 0x03
KIND_TERM = 0x04
VALID_KINDS = {KIND_DATA, KIND_ACK, KIND_ENCAP, KIND_TERM}
SEAL_XOR = 0xA5
ALLOWED_FLAGS = ("e", "i", "x")


class DecodeError(Exception):
    def __init__(self, disposition: str):
        self.disposition = disposition
        super().__init__(disposition)


def compute_seal(body: bytes, payload_len: int) -> int:
    acc = 0
    for byte in body:
        acc ^= byte
    return (acc ^ SEAL_XOR ^ (payload_len & 0xFF)) & 0xFF


def pack_frame(kind: int, seq: int, payload: bytes, extended: bool) -> bytes:
    if extended:
        if len(payload) > 65535:
            raise ValueError("payload too long for extended length")
        body = bytes(
            [MAGIC, kind, seq & 0xFF, (len(payload) >> 8) & 0xFF, len(payload) & 0xFF]
        ) + payload
    else:
        if len(payload) > 255:
            raise ValueError("payload too long for one-byte length")
        body = bytes([MAGIC, kind, seq & 0xFF, len(payload)]) + payload
    return body + bytes([compute_seal(body, len(payload))])


def to_hex(raw: bytes, prefix: bool = False) -> str:
    text = raw.hex()
    if prefix:
        return "0x" + text
    return text


def validate_flags(flags: str) -> None:
    if not isinstance(flags, str):
        raise DecodeError("reject_syntax")
    seen: set[str] = set()
    for ch in flags:
        if ch not in ALLOWED_FLAGS or ch in seen:
            raise DecodeError("reject_flag")
        seen.add(ch)


HEX_DIGITS = set("0123456789abcdefABCDEF")


def parse_hex(text: str, ignore_prefix: bool) -> bytes:
    if not isinstance(text, str) or text == "":
        raise DecodeError("reject_syntax")
    blob = text
    if len(blob) >= 2 and blob[:2].lower() == "0x":
        if not ignore_prefix:
            raise DecodeError("reject_syntax")
        blob = blob[2:]
    if not blob or len(blob) % 2 != 0:
        raise DecodeError("reject_syntax")
    for char in blob:
        if char not in HEX_DIGITS:
            raise DecodeError("reject_syntax")
    return bytes.fromhex(blob)


def parse_frame(raw: bytes, extended: bool, check_seal: bool) -> tuple[int, int, bytes]:
    minimum = 6 if extended else 5
    if len(raw) < minimum:
        raise DecodeError("reject_syntax")
    if raw[0] != MAGIC:
        raise DecodeError("reject_syntax")
    kind = raw[1]
    seq = raw[2]
    if kind not in VALID_KINDS:
        raise DecodeError("reject_syntax")
    if extended:
        payload_len = (raw[3] << 8) | raw[4]
        header_len = 5
    else:
        payload_len = raw[3]
        header_len = 4
    if len(raw) != header_len + payload_len + 1:
        raise DecodeError("reject_syntax")
    payload = raw[header_len : header_len + payload_len]
    body = raw[: header_len + payload_len]
    seal = raw[-1]
    expected = compute_seal(body, payload_len)
    if check_seal and seal != expected:
        raise DecodeError("reject_syntax")
    return kind, seq, payload


def unwrap_encap(payload: bytes, extended: bool, check_seal: bool) -> bytes:
    inner_kind, _inner_seq, inner_payload = parse_frame(payload, extended, check_seal)
    if inner_kind == KIND_ENCAP:
        raise DecodeError("reject_nested_encap")
    if inner_kind != KIND_DATA:
        raise DecodeError("reject_syntax")
    return inner_payload


def decode_session(flags: str, frames: list[str]) -> dict[str, Any]:
    validate_flags(flags)
    if not isinstance(frames, list) or not frames:
        raise DecodeError("reject_syntax")
    extended = "e" in flags
    check_seal = "x" in flags
    ignore_prefix = "i" in flags
    expected_seq: int | None = None
    last_data_seq: int | None = None
    saw_term = False
    collected: list[int] = []
    for item in frames:
        if saw_term:
            raise DecodeError("reject_syntax")
        raw = parse_hex(item, ignore_prefix)
        kind, seq, payload = parse_frame(raw, extended, check_seal)
        if kind == KIND_ACK:
            if len(payload) != 1 or last_data_seq is None or payload[0] != last_data_seq:
                raise DecodeError("reject_syntax")
            continue
        if expected_seq is None:
            expected_seq = seq
        if seq != expected_seq:
            raise DecodeError("reject_syntax")
        expected_seq = (expected_seq + 1) & 0xFF
        if kind == KIND_DATA:
            collected.extend(payload)
            last_data_seq = seq
        elif kind == KIND_ENCAP:
            inner = unwrap_encap(payload, extended, check_seal)
            collected.extend(inner)
            last_data_seq = seq
        elif kind == KIND_TERM:
            if payload:
                raise DecodeError("reject_syntax")
            saw_term = True
        else:
            raise DecodeError("reject_syntax")
    if not saw_term:
        raise DecodeError("reject_syntax")
    return {
        "bytes": collected,
        "disposition": "ok",
        "frame_count": len(frames),
    }


def decode_row(row: dict[str, Any]) -> dict[str, Any]:
    flags = row.get("flags", "")
    frames = row.get("frames", [])
    try:
        decoded = decode_session(flags, frames)
        return {
            "bytes": decoded["bytes"],
            "disposition": "ok",
            "flags": flags,
            "frame_count": decoded["frame_count"],
            "session_id": row["session_id"],
        }
    except DecodeError as exc:
        return {
            "bytes": [],
            "disposition": exc.disposition,
            "flags": flags,
            "frame_count": 0,
            "session_id": row["session_id"],
        }


def sorted_row(obj: dict[str, Any]) -> dict[str, Any]:
    return {key: obj[key] for key in sorted(obj)}


def clear_out(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for path in list(out_dir.iterdir()):
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()


def decode_manifest(captures_dir: Path, out_dir: Path) -> None:
    manifest_path = captures_dir / "manifest.jsonl"
    rows_in: list[dict[str, Any]] = []
    with manifest_path.open() as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows_in.append(json.loads(line))
    rows_in.sort(key=lambda row: row["session_id"])

    out_rows = []
    by_disp = {key: 0 for key in DISPOSITION_ORDER}
    for row in rows_in:
        result = sorted_row(decode_row(row))
        out_rows.append(result)
        by_disp[result["disposition"]] += 1

    clear_out(out_dir)

    with (out_dir / "sessions.jsonl").open("w") as handle:
        for row in out_rows:
            handle.write(json.dumps(row, separators=(",", ":"), ensure_ascii=False) + "\n")

    summary_text = (
        '{"session_count":'
        + json.dumps(len(out_rows))
        + ',"by_disposition":{'
        + ",".join(
            json.dumps(key) + ":" + json.dumps(by_disp[key]) for key in DISPOSITION_ORDER
        )
        + '},"ok_count":'
        + json.dumps(by_disp["ok"])
        + "}"
    )
    (out_dir / "summary.json").write_text(summary_text + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BW-2026 Brinewire session decoder")
    parser.add_argument("--captures", required=True, type=Path)
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)

    spec_md = args.spec / "BW_SPEC.md"
    examples = args.spec / "worked_examples"
    if not spec_md.is_file() or not examples.is_dir():
        print("missing BW spec surfaces", file=sys.stderr)
        return 1

    decode_manifest(args.captures, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
