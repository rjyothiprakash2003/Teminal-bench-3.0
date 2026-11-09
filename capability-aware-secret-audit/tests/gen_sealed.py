#!/usr/bin/env python3
"""Generate sealed manifest.json and roster.json fixtures (not expect.json)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

Dir = dict[str, Any]
Case = dict[str, Any]


def _dir(path: str, mode: str = "0755", uid: int = 0, gid: int = 0) -> Dir:
    return {"path": path, "kind": "dir", "mode": mode, "uid": uid, "gid": gid}


def _file(
    path: str,
    mode: str,
    uid: int = 1000,
    gid: int = 1000,
    content: str = "t",
) -> Dir:
    return {
        "path": path,
        "kind": "file",
        "mode": mode,
        "uid": uid,
        "gid": gid,
        "content": content,
    }


def _symlink(path: str, target: str) -> Dir:
    return {"path": path, "kind": "symlink", "target": target}


def _fifo(path: str, mode: str = "0644", uid: int = 0, gid: int = 0) -> Dir:
    return {"path": path, "kind": "fifo", "mode": mode, "uid": uid, "gid": gid}


def _socket(path: str, mode: str = "0644", uid: int = 0, gid: int = 0) -> Dir:
    return {"path": path, "kind": "socket", "mode": mode, "uid": uid, "gid": gid}


def _device(path: str, mode: str = "0644", uid: int = 0, gid: int = 0) -> Dir:
    return {"path": path, "kind": "device", "mode": mode, "uid": uid, "gid": gid}


def _manifest(
    nodes: list[Dir],
    *,
    root_mode: str = "0755",
    extra: list[Dir] | None = None,
    tree_extra: list[Dir] | None = None,
    hostile_parent: bool = False,
    root_via_alias: bool = False,
) -> dict[str, Any]:
    m: dict[str, Any] = {"nodes": nodes, "extra": extra or [], "root_mode": root_mode}
    if tree_extra:
        m["tree_extra"] = tree_extra
    if hostile_parent:
        m["hostile_parent"] = True
    if root_via_alias:
        m["root_via_alias"] = True
    return m


def _entry(path: str, allow_uids: list[int], allow_gids: list[int]) -> dict[str, Any]:
    return {"path": path, "allow_uids": allow_uids, "allow_gids": allow_gids}


def _subject(uid: int, gids: list[int]) -> dict[str, Any]:
    return {"uid": uid, "gids": gids}


def _roster(entries: list[dict[str, Any]], subjects: list[dict[str, Any]]) -> dict[str, Any]:
    return {"entries": entries, "subjects": subjects}


def _nested_deep_nodes() -> list[Dir]:
    parts = ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k"]
    nodes: list[Dir] = []
    for i in range(len(parts)):
        path = "/".join(parts[: i + 1])
        nodes.append(_dir(path))
    token_path = "/".join(parts + ["token"])
    nodes.append(_file(token_path, "0644", uid=50, gid=50))
    return nodes


def _scale_48_nodes() -> tuple[list[Dir], list[dict[str, Any]]]:
    nodes: list[Dir] = []
    entries: list[dict[str, Any]] = []
    for i in range(48):
        name = f"s{i:02d}"
        nodes.append(_file(name, "0644", uid=1000, gid=1000, content=name))
        entries.append(_entry(name, [], []))
    return nodes, entries


_SCALE48_NODES, _SCALE48_ENTRIES = _scale_48_nodes()


CASES: dict[str, Case] = {
    # --- existing (semantics preserved; nested_deep depth updated) ---
    "world_readable": {
        "manifest": _manifest([_dir("conf"), _file("conf/token", "0644")]),
        "roster": _roster(
            [_entry("conf/token", [1000], [1000])],
            [
                _subject(1000, [1000]),
                _subject(1001, [1001]),
                _subject(2000, [2000]),
            ],
        ),
    },
    "group_overshare": {
        "manifest": _manifest([_dir("conf"), _file("conf/token", "0640")]),
        "roster": _roster(
            [_entry("conf/token", [1000], [])],
            [
                _subject(1000, [1000]),
                _subject(1001, [1000]),
                _subject(2000, [2000]),
            ],
        ),
    },
    "parent_blocks_other": {
        "manifest": _manifest([_dir("conf", "0710"), _file("conf/token", "0644")]),
        "roster": _roster(
            [_entry("conf/token", [1000], [1000])],
            [_subject(1000, [1000]), _subject(2000, [2000])],
        ),
    },
    "root_noexec_blocks": {
        "manifest": _manifest([_dir("conf"), _file("conf/token", "0644")], root_mode="0700"),
        "roster": _roster(
            [_entry("conf/token", [1000], [1000])],
            [_subject(2000, [2000])],
        ),
    },
    "root_owner_exec_exposes": {
        # Root 0700 owned by uid 0: other-execute is off, but subject uid 0 has
        # owner-execute on --root and owner-read on the leaf.
        "manifest": _manifest(
            [_file("secret", "0400", uid=0, gid=0, content="s")],
            root_mode="0700",
        ),
        "roster": _roster(
            [_entry("secret", [], [])],
            [_subject(0, [0])],
        ),
    },
    "root_group_exec_exposes": {
        # Root 0710 owned by 0/0: other-execute off; subject uid 2000 with gid 0
        # gets group-execute on --root and other-read on the leaf.
        "manifest": _manifest(
            [_file("secret", "0644", uid=1000, gid=1000, content="s")],
            root_mode="0710",
        ),
        "roster": _roster(
            [_entry("secret", [], [])],
            [_subject(2000, [0])],
        ),
    },
    "mid_noexec": {
        "manifest": _manifest(
            [
                _dir("conf"),
                _dir("conf/locked", "0700", uid=1000, gid=1000),
                _file("conf/locked/token", "0644"),
            ]
        ),
        "roster": _roster(
            [_entry("conf/locked/token", [1000], [1000])],
            [_subject(1000, [1000]), _subject(2000, [2000])],
        ),
    },
    "deep_mid_noexec": {
        "manifest": _manifest(
            [
                _dir("conf"),
                _dir("conf/open"),
                _dir("conf/open/locked", "0700", uid=1000, gid=1000),
                _file("conf/open/locked/token", "0644"),
            ]
        ),
        "roster": _roster(
            [_entry("conf/open/locked/token", [1000], [1000])],
            [_subject(1000, [1000]), _subject(2000, [2000])],
        ),
    },
    "parent_allows_file_blocks": {
        "manifest": _manifest([_dir("conf"), _file("conf/token", "0600")]),
        "roster": _roster(
            [_entry("conf/token", [1000], [])],
            [_subject(1000, [1000]), _subject(2000, [2000])],
        ),
    },
    "owner_only_ok": {
        "manifest": _manifest([_dir("conf"), _file("conf/token", "0600")]),
        "roster": _roster(
            [_entry("conf/token", [1000], [1000])],
            [_subject(1000, [1000]), _subject(1001, [1001])],
        ),
    },
    "escaped_final": {
        "manifest": _manifest(
            [_dir("conf"), _symlink("conf/link", "../../extra/secret.dat")],
            extra=[_fifo("secret.dat", "0644", uid=0, gid=0)],
        ),
        "roster": _roster(
            [_entry("conf/link", [0], [0])],
            [_subject(0, [0]), _subject(1000, [1000])],
        ),
    },
    "escaped_intermediate": {
        # Intermediate link escapes to an outside FIFO: opening the target blocks.
        "manifest": _manifest(
            [
                _dir("conf"),
                _symlink("conf/door", "../../extra/trap.fifo"),
                _file("conf/keep", "0600", uid=0, gid=0, content="k"),
            ],
            extra=[_fifo("trap.fifo", "0644", uid=0, gid=0)],
        ),
        "roster": _roster(
            [
                _entry("conf/door/secret.dat", [0], [0]),
                _entry("conf/keep", [0], [0]),
            ],
            [_subject(0, [0]), _subject(1000, [1000])],
        ),
    },
    "safe_symlink_inner": {
        "manifest": _manifest(
            [
                _dir("conf"),
                _symlink("alias", "conf"),
                _file("conf/token", "0644"),
            ]
        ),
        "roster": _roster(
            [_entry("alias/token", [1000], [1000])],
            [_subject(1000, [1000]), _subject(2000, [2000])],
        ),
    },
    "intermediate_file_symlink_unsupported": {
        # In-root intermediate symlink to a regular file is unsupported (not missing).
        "manifest": _manifest(
            [
                _file("token", "0644"),
                _symlink("alias", "token"),
            ]
        ),
        "roster": _roster(
            [_entry("alias/child", [], [])],
            [_subject(2000, [2000])],
        ),
    },
    # In-root intermediate symlink to a FIFO: must be unsupported, not ENOTDIR→missing.
    "intermediate_fifo_symlink_unsupported": {
        "manifest": _manifest(
            [
                _fifo("pipe"),
                _symlink("alias", "pipe"),
            ]
        ),
        "roster": _roster(
            [_entry("alias/child", [], [])],
            [],
        ),
    },
    # In-root intermediate symlink to a socket: same non-directory guard.
    "intermediate_socket_symlink_unsupported": {
        "manifest": _manifest(
            [
                _socket("sock"),
                _symlink("alias", "sock"),
            ]
        ),
        "roster": _roster(
            [_entry("alias/child", [], [])],
            [],
        ),
    },
    # In-root intermediate symlink to a character device: same non-directory guard.
    "intermediate_device_symlink_unsupported": {
        "manifest": _manifest(
            [
                _device("dev"),
                _symlink("alias", "dev"),
            ]
        ),
        "roster": _roster(
            [_entry("alias/child", [], [])],
            [],
        ),
    },
    "file_intermediate_unsupported": {
        # Ordinary regular file used as an intermediate component is unsupported
        # (not missing / ENOTDIR-as-missing).
        "manifest": _manifest([_file("a", "0644")]),
        "roster": _roster(
            [_entry("a/child", [], [])],
            [_subject(2000, [2000])],
        ),
    },
    "fifo_intermediate_unsupported": {
        "manifest": _manifest([_fifo("a")]),
        "roster": _roster(
            [_entry("a/child", [], [])],
            [_subject(2000, [2000])],
        ),
    },
    "socket_intermediate_unsupported": {
        "manifest": _manifest([_socket("a")]),
        "roster": _roster(
            [_entry("a/child", [], [])],
            [_subject(2000, [2000])],
        ),
    },
    "device_intermediate_unsupported": {
        "manifest": _manifest([_device("a")]),
        "roster": _roster(
            [_entry("a/child", [], [])],
            [_subject(2000, [2000])],
        ),
    },
    "other_exec_no_read_dir": {
        # Directory other-execute without other-read (0701): other-class subjects
        # must still traverse; using other-read (0o004) for traverse would miss.
        "manifest": _manifest(
            [
                _dir("d", "0701"),
                _file("d/s", "0644"),
            ]
        ),
        "roster": _roster(
            [_entry("d/s", [], [])],
            [_subject(2000, [2000])],
        ),
    },
    "missing_path": {
        "manifest": _manifest([_dir("conf")]),
        "roster": _roster([_entry("conf/nope", [0], [0])], [_subject(0, [0])]),
    },
    "fifo_unsupported": {
        "manifest": _manifest([_dir("conf"), _fifo("conf/pipe")]),
        "roster": _roster([_entry("conf/pipe", [0], [0])], [_subject(0, [0])]),
    },
    "dir_final_unsupported": {
        "manifest": _manifest([_dir("conf"), _dir("conf/subdir")]),
        "roster": _roster([_entry("conf/subdir", [0], [0])], [_subject(0, [0])]),
    },
    "symlink_to_fifo_unsupported": {
        "manifest": _manifest(
            [_dir("conf"), _fifo("conf/pipe"), _symlink("conf/link", "pipe")]
        ),
        "roster": _roster([_entry("conf/link", [0], [0])], [_subject(0, [0])]),
    },
    "setgid_preserved": {
        "manifest": _manifest([_dir("conf"), _file("conf/token", "2644")]),
        "roster": _roster(
            [_entry("conf/token", [1000], [1000])],
            [_subject(1000, [1000]), _subject(2000, [2000])],
        ),
    },
    "uid0_not_magic": {
        "manifest": _manifest([_dir("conf"), _file("conf/token", "0600")]),
        "roster": _roster(
            [_entry("conf/token", [1000], [])],
            [_subject(0, [0]), _subject(1000, [1000])],
        ),
    },
    "uid0_exec_denies": {
        # uid 0 is not magic for directory execute: mode 0000 still blocks traversal.
        "manifest": _manifest(
            [
                _dir("d", "0000", uid=0, gid=0),
                _file("d/s", "0400", uid=0, gid=0),
            ]
        ),
        "roster": _roster(
            [_entry("d/s", [], [])],
            [_subject(0, [0])],
        ),
    },
    "allow_via_gid_only": {
        "manifest": _manifest([_dir("conf"), _file("conf/token", "0640")]),
        "roster": _roster(
            [_entry("conf/token", [], [1000])],
            [_subject(2000, [1000]), _subject(3000, [3000])],
        ),
    },
    "group_allowed_keep": {
        "manifest": _manifest([_dir("conf"), _file("conf/token", "0640")]),
        "roster": _roster(
            [_entry("conf/token", [1000], [1000])],
            [
                _subject(1000, [1000]),
                _subject(1001, [1000]),
                _subject(2000, [2000]),
            ],
        ),
    },
    "remediation_group_clear": {
        "manifest": _manifest([_dir("conf"), _file("conf/token", "0664")]),
        "roster": _roster(
            [_entry("conf/token", [1000], [])],
            [
                _subject(1000, [1000]),
                _subject(1001, [1000]),
                _subject(2000, [2000]),
            ],
        ),
    },
    "nested_deep": {
        "manifest": _manifest(_nested_deep_nodes()),
        "roster": _roster(
            [_entry("a/b/c/d/e/f/g/h/i/j/k/token", [50], [50])],
            [_subject(50, [50]), _subject(51, [51])],
        ),
    },
    "multi_entry_mix": {
        "manifest": _manifest(
            [
                _dir("a"),
                _dir("b"),
                _file("a/one", "0644", content="1"),
                _file("b/two", "0600", content="2"),
                _file("b/gone", "0600", content="3"),
            ]
        ),
        "roster": _roster(
            [
                _entry("b/gone", [1000], []),
                _entry("a/one", [1000], [1000]),
                _entry("b/two", [1000], []),
                _entry("missing/x", [0], [0]),
            ],
            [_subject(1000, [1000]), _subject(2000, [2000])],
        ),
    },
    "sort_findings": {
        "manifest": _manifest(
            [
                _dir("z"),
                _dir("a"),
                _file("z/late", "0644", uid=1, gid=1, content="z"),
                _file("a/early", "0644", uid=1, gid=1, content="a"),
            ]
        ),
        "roster": _roster(
            [
                _entry("z/late", [1], [1]),
                _entry("a/early", [1], [1]),
            ],
            [_subject(1, [1]), _subject(9, [9])],
        ),
    },
    # --- scale, size and boundary cases ---
    "scale_48_entries": {
        "manifest": _manifest(_SCALE48_NODES),
        "roster": _roster(_SCALE48_ENTRIES, [_subject(2000, [2000])]),
    },
    "large_file_bound": {
        "manifest": _manifest(
            [_dir("conf"), _file("conf/big.bin", "0644", content="bytes:65536")]
        ),
        "roster": _roster(
            [_entry("conf/big.bin", [], [])],
            [_subject(2000, [2000])],
        ),
    },
    "unicode_path": {
        "manifest": _manifest([_file("café", "0644", uid=1000, gid=1000, content="u")]),
        "roster": _roster(
            [_entry("café", [], [])],
            [_subject(2000, [2000])],
        ),
    },
    "emoji_path": {
        # Supplementary-plane UTF-8 must stay literal (ensure_ascii=False), not \\ud83d\\ude00.
        "manifest": _manifest([_file("a😀b", "0644", uid=1000, gid=1000, content="e")]),
        "roster": _roster(
            [_entry("a😀b", [], [])],
            [_subject(2000, [2000])],
        ),
    },
    "html_special_path": {
        "manifest": _manifest([_file("a&b", "0644", uid=1000, gid=1000, content="h")]),
        "roster": _roster(
            [_entry("a&b", [], [])],
            [_subject(2000, [2000])],
        ),
    },
    "angle_path": {
        "manifest": _manifest([_file("a<b", "0644", uid=1000, gid=1000, content="h")]),
        "roster": _roster(
            [_entry("a<b", [], [])],
            [_subject(2000, [2000])],
        ),
    },
    "backslash_name": {
        "manifest": _manifest([_file(r"a\b", "0644", uid=1000, gid=1000, content="b")]),
        "roster": _roster(
            [_entry(r"a\b", [], [])],
            [_subject(2000, [2000])],
        ),
    },
    "skip_sort_reorder": {
        "manifest": _manifest([_dir("conf")]),
        "roster": _roster(
            [
                _entry("z/missing", [0], [0]),
                _entry("a/missing", [0], [0]),
            ],
            [_subject(0, [0])],
        ),
    },
    "dup_subject_uid": {
        "manifest": _manifest([_dir("conf"), _file("conf/token", "0644")]),
        "roster": _roster(
            [_entry("conf/token", [], [])],
            [_subject(2000, [2000]), _subject(2000, [2001])],
        ),
    },
    "dup_uid_class_union": {
        # Same uid, other-class then group-class readable: remediation must clear both.
        "manifest": _manifest([_dir("conf"), _file("conf/token", "0644")]),
        "roster": _roster(
            [_entry("conf/token", [], [])],
            [_subject(2000, [2000]), _subject(2000, [1000])],
        ),
    },
    "dup_uid_class_union_rev": {
        # Reverse order: group-class then other-class must still union to 0600.
        "manifest": _manifest([_dir("conf"), _file("conf/token", "0644")]),
        "roster": _roster(
            [_entry("conf/token", [], [])],
            [_subject(2000, [1000]), _subject(2000, [2000])],
        ),
    },
    "exposure_unicode_sort": {
        "manifest": _manifest([_dir("conf"), _file("conf/token", "0644")]),
        "roster": _roster(
            [_entry("conf/token", [], [])],
            [_subject(9, [9]), _subject(10, [10])],
        ),
    },
    "owner_read_exposure": {
        "manifest": _manifest([_dir("conf"), _file("conf/token", "0400")]),
        "roster": _roster(
            [_entry("conf/token", [], [])],
            [_subject(1000, [1000])],
        ),
    },
    "owner_exec_denies": {
        "manifest": _manifest(
            [
                _dir("conf", "0600", uid=1000, gid=1000),
                _file("conf/token", "0644"),
            ]
        ),
        "roster": _roster(
            [_entry("conf/token", [], [])],
            [_subject(1000, [1000])],
        ),
    },
    "owner_exec_denies_group_x": {
        # Owner class wins even when group execute is set: 0610 selects owner (no 0100).
        "manifest": _manifest(
            [
                _dir("d", "0610", uid=1000, gid=1000),
                _file("d/s", "0400", uid=1000, gid=1000),
            ]
        ),
        "roster": _roster(
            [_entry("d/s", [], [])],
            [_subject(1000, [1000])],
        ),
    },
    "group_exec_denies": {
        # 0741: group lacks execute while other has it; other-x lets the sandbox
        # traverse without an ACL mask that would inflate group bits in st_mode.
        "manifest": _manifest(
            [
                _dir("conf", "0741", uid=0, gid=1000),
                _file("conf/token", "0640", uid=0, gid=1000),
            ]
        ),
        "roster": _roster(
            [_entry("conf/token", [], [])],
            [_subject(2000, [1000])],
        ),
    },
    "group_denies_other_grants_exec": {
        "manifest": _manifest(
            [
                _dir("conf", "0741", uid=1000, gid=1000),
                _file("conf/token", "0644"),
            ]
        ),
        "roster": _roster(
            [_entry("conf/token", [], [])],
            [_subject(2000, [1000])],
        ),
    },
    "owner_denies_group_grants_read": {
        "manifest": _manifest([_dir("conf"), _file("conf/token", "0040")]),
        "roster": _roster(
            [_entry("conf/token", [], [])],
            [_subject(1000, [1000])],
        ),
    },
    "group_denies_other_grants_read": {
        "manifest": _manifest([_dir("conf"), _file("conf/token", "0604")]),
        "roster": _roster(
            [_entry("conf/token", [], [])],
            [_subject(2000, [1000])],
        ),
    },
    # Selected class has execute (or write) but not read: any-class-bit predicates fail.
    "owner_exec_only_no_read": {
        "manifest": _manifest([_file("f", "0100", uid=1000, gid=1000)]),
        "roster": _roster([_entry("f", [], [])], [_subject(1000, [])]),
    },
    "group_exec_only_no_read": {
        "manifest": _manifest([_file("f", "0010", uid=1000, gid=1000)]),
        "roster": _roster([_entry("f", [], [])], [_subject(2000, [1000])]),
    },
    "other_exec_only_no_read": {
        "manifest": _manifest([_file("f", "0001", uid=1000, gid=1000)]),
        "roster": _roster([_entry("f", [], [])], [_subject(2000, [])]),
    },
    # Owner class selected; other-read alone must not grant (no ownerRead||otherRead).
    "owner_denies_other_grants_read": {
        "manifest": _manifest([_file("f", "0004", uid=1000, gid=1000)]),
        "roster": _roster([_entry("f", [], [])], [_subject(1000, [1000])]),
    },
    # Other-class directory has read but not execute: any-other-bit traverse fails.
    "other_read_no_exec_dir": {
        "manifest": _manifest(
            [
                _dir("d", "0704"),
                _file("d/f", "0644", uid=1000, gid=1000),
            ]
        ),
        "roster": _roster([_entry("d/f", [], [])], [_subject(2000, [2000])]),
    },
    # Group-class directory has write but not execute: write-or-execute traverse fails.
    "group_write_no_exec_dir": {
        "manifest": _manifest(
            [
                _dir("d", "0721", uid=0, gid=1000),
                _file("d/s", "0644", uid=1000, gid=1000),
            ]
        ),
        "roster": _roster([_entry("d/s", [], [])], [_subject(2000, [1000])]),
    },
    # Other-class directory has write but not execute: write-or-execute traverse fails.
    "other_write_no_exec_dir": {
        "manifest": _manifest(
            [
                _dir("d", "0702", uid=0, gid=1000),
                _file("d/s", "0644", uid=1000, gid=1000),
            ]
        ),
        "roster": _roster([_entry("d/s", [], [])], [_subject(3000, [])]),
    },
    # Later permitted link must not drop earlier pivot ancestry (a stays checked).
    "permitted_link_keeps_prior_dir": {
        "manifest": _manifest(
            [
                _dir("a", "0700"),
                _dir("b"),
                _file("b/f", "0644"),
                _symlink("l", "a"),
                _symlink("a/back", "../b"),
            ]
        ),
        "roster": _roster(
            [_entry("l/back/f", [], [])],
            [_subject(1000, [])],
        ),
    },
    # Owner-class directory execute without owner-read must still traverse.
    "owner_dir_exec_only_exposes": {
        "manifest": _manifest(
            [
                _dir("d", "0100", uid=1000, gid=1000),
                _file("d/s", "0400", uid=1000, gid=1000),
            ]
        ),
        "roster": _roster([_entry("d/s", [], [])], [_subject(1000, [])]),
    },
    # Selected class write-only is not file read.
    "owner_write_only_no_read": {
        "manifest": _manifest([_file("f", "0200", uid=1000, gid=1000)]),
        "roster": _roster([_entry("f", [], [])], [_subject(1000, [])]),
    },
    "group_write_only_no_read": {
        "manifest": _manifest([_file("f", "0020", uid=1000, gid=1000)]),
        "roster": _roster([_entry("f", [], [])], [_subject(2000, [1000])]),
    },
    "other_write_only_no_read": {
        "manifest": _manifest([_file("f", "0002", uid=1000, gid=1000)]),
        "roster": _roster([_entry("f", [], [])], [_subject(2000, [])]),
    },
    # Subject uid that collides with st_uid after uint32 narrowing must not become owner.
    "uid_uint32_narrow_not_owner": {
        "manifest": _manifest([_file("f", "0400", uid=1000, gid=1000)]),
        "roster": _roster(
            [_entry("f", [], [])],
            [_subject(4294968296, [])],
        ),
    },
    # Subject gid that collides with st_gid after uint32 narrowing must not become group.
    "gid_uint32_narrow_not_group": {
        "manifest": _manifest([_file("f", "0640", uid=1000, gid=1000)]),
        "roster": _roster(
            [_entry("f", [], [])],
            [_subject(2000, [4294968296])],
        ),
    },
    # Same narrowing fault on directory group-execute selection.
    "gid_uint32_narrow_not_group_dir": {
        "manifest": _manifest(
            [
                _dir("d", "0710", uid=0, gid=1000),
                _file("d/s", "0644", uid=1000, gid=1000),
            ]
        ),
        "roster": _roster(
            [_entry("d/s", [], [])],
            [_subject(2000, [4294968296])],
        ),
    },
    "secondary_gid_match": {
        "manifest": _manifest([_dir("conf"), _file("conf/token", "0640")]),
        "roster": _roster(
            [_entry("conf/token", [], [])],
            [_subject(2000, [1, 1000])],
        ),
    },
    "exec_bit_preserved": {
        "manifest": _manifest([_dir("conf"), _file("conf/token", "0744")]),
        "roster": _roster(
            [_entry("conf/token", [], [])],
            [_subject(2000, [2000])],
        ),
    },
    "owner_exposure_keeps_other": {
        "manifest": _manifest([_dir("conf"), _file("conf/token", "0644")]),
        "roster": _roster(
            [_entry("conf/token", [], [])],
            [_subject(1000, [1000])],
        ),
    },
    "setuid_preserved": {
        "manifest": _manifest([_dir("conf"), _file("conf/token", "4644")]),
        "roster": _roster(
            [_entry("conf/token", [], [])],
            [_subject(2000, [2000])],
        ),
    },
    "sticky_preserved": {
        "manifest": _manifest([_dir("conf"), _file("conf/token", "1644")]),
        "roster": _roster(
            [_entry("conf/token", [], [])],
            [_subject(2000, [2000])],
        ),
    },
    "final_symlink_regular": {
        "manifest": _manifest(
            [
                _dir("conf"),
                _file("conf/token", "0644"),
                _symlink("conf/link", "token"),
            ]
        ),
        "roster": _roster(
            [_entry("conf/link", [], [])],
            [_subject(2000, [2000])],
        ),
    },
    "final_symlink_chain": {
        # Final link chain a -> b -> token must realpath to the regular file.
        "manifest": _manifest(
            [
                _dir("conf"),
                _file("conf/token", "0644"),
                _symlink("conf/b", "token"),
                _symlink("conf/a", "b"),
            ]
        ),
        "roster": _roster(
            [_entry("conf/a", [], [])],
            [_subject(2000, [2000])],
        ),
    },
    "entry_symlink_pivot_dotdot": {
        # alias -> pivot/../real must resolve pivot (symlink) before '..', not Clean first.
        "manifest": _manifest(
            [
                _dir("nested"),
                _dir("nested/deep"),
                _dir("nested/real"),
                _file("nested/real/token", "0600"),
                _dir("real"),
                _file("real/token", "0644"),
                _symlink("pivot", "nested/deep"),
                _symlink("alias", "pivot/../real"),
            ]
        ),
        "roster": _roster(
            [_entry("alias/token", [], [])],
            [_subject(2000, [2000])],
        ),
    },
    "final_symlink_dir_unsupported": {
        "manifest": _manifest(
            [
                _dir("conf"),
                _dir("conf/subdir"),
                _symlink("conf/link", "subdir"),
            ]
        ),
        "roster": _roster(
            [_entry("conf/link", [0], [0])],
            [_subject(0, [0])],
        ),
    },
    "dangling_final_unsupported": {
        "manifest": _manifest(
            [_dir("conf"), _symlink("conf/link", "no-such-file")]
        ),
        "roster": _roster(
            [_entry("conf/link", [0], [0])],
            [_subject(0, [0])],
        ),
    },
    "symlink_to_socket_unsupported": {
        "manifest": _manifest(
            [
                _dir("conf"),
                _socket("conf/sock"),
                _symlink("conf/link", "sock"),
            ]
        ),
        "roster": _roster([_entry("conf/link", [0], [0])], [_subject(0, [0])]),
    },
    "symlink_to_device_unsupported": {
        "manifest": _manifest(
            [
                _dir("conf"),
                _device("conf/dev"),
                _symlink("conf/link", "dev"),
            ]
        ),
        "roster": _roster([_entry("conf/link", [0], [0])], [_subject(0, [0])]),
    },
    "cyclic_final_unsupported": {
        "manifest": _manifest([_symlink("loop", "loop")]),
        "roster": _roster(
            [_entry("loop", [0], [0])],
            [_subject(0, [0])],
        ),
    },
    "cyclic_intermediate_escaped": {
        # Intermediate self-loop fails resolution (ELOOP) → escaped, not unsupported.
        "manifest": _manifest([_symlink("loop", "loop")]),
        "roster": _roster(
            [_entry("loop/token", [], [])],
            [_subject(2000, [2000])],
        ),
    },
    "symlink_locked_ancestors": {
        "manifest": _manifest(
            [
                _symlink("link", "locked/secret"),
                _dir("locked", "0700", uid=2000, gid=2000),
                _file("locked/secret", "0644", uid=2000, gid=2000),
            ]
        ),
        "roster": _roster(
            [_entry("link", [], [])],
            [_subject(1000, [1000])],
        ),
    },
    "intermediate_symlink_private": {
        "manifest": _manifest(
            [
                _dir("public"),
                _symlink("public/jump", "../private/deep"),
                _dir("private", "0700", uid=2000, gid=2000),
                _dir("private/deep"),
                _file("private/deep/secret", "0644", uid=2000, gid=2000),
            ]
        ),
        "roster": _roster(
            [_entry("public/jump/secret", [], [])],
            [_subject(1000, [1000])],
        ),
    },
    "intermediate_link_resolved_dir_denies": {
        # alias resolves to private itself; execute denial on that resolved
        # directory must be checked (not only parent(resolved)).
        "manifest": _manifest(
            [
                _dir("private", "0700", uid=2000, gid=2000),
                _file("private/token", "0644", uid=2000, gid=2000),
                _symlink("alias", "private"),
            ]
        ),
        "roster": _roster(
            [_entry("alias/token", [], [])],
            [_subject(1000, [1000])],
        ),
    },
    "resolved_link_deep_ancestor_denies": {
        # alias -> a/b/c; blocking execute on a (two levels above resolved c) must apply.
        "manifest": _manifest(
            [
                _dir("a", "0700", uid=2000, gid=2000),
                _dir("a/b"),
                _dir("a/b/c"),
                _file("a/b/c/token", "0644", uid=2000, gid=2000),
                _symlink("alias", "a/b/c"),
            ]
        ),
        "roster": _roster(
            [_entry("alias/token", [], [])],
            [_subject(1000, [])],
        ),
    },
    "escape_reenter": {
        "manifest": _manifest(
            [
                _dir("conf"),
                _file("conf/token", "0644"),
                _symlink("door", "../extra"),
            ],
            extra=[_symlink("back", "../tree")],
        ),
        "roster": _roster(
            [_entry("door/back/conf/token", [], [])],
            [_subject(2000, [2000])],
        ),
    },
    "root_equality_link": {
        "manifest": _manifest(
            [
                _dir("conf"),
                _symlink("alias", "."),
                _file("conf/token", "0644"),
            ]
        ),
        "roster": _roster(
            [_entry("alias/conf/token", [], [])],
            [_subject(2000, [2000])],
        ),
    },
    "prefix_escape": {
        "manifest": _manifest(
            [_symlink("alias", "../tree_extra/token")],
            tree_extra=[_file("token", "0644", uid=0, gid=0, content="x")],
        ),
        "roster": _roster(
            [_entry("alias", [0], [0])],
            [_subject(0, [0])],
        ),
    },
    "prefix_escape_intermediate": {
        "manifest": _manifest(
            [_symlink("alias", "../tree_extra")],
            tree_extra=[_file("token", "0644", uid=0, gid=0, content="x")],
        ),
        "roster": _roster([_entry("alias/token", [0], [0])], [_subject(0, [0])]),
    },
    "linked_parent_above_ignored": {
        "manifest": _manifest(
            [
                _dir("conf"),
                _file("conf/token", "0644"),
                _symlink("alias", "conf"),
            ],
            hostile_parent=True,
        ),
        "roster": _roster(
            [_entry("alias/token", [], [])],
            [_subject(1001, [1001])],
        ),
    },
    "owner_dir_exec_exposes": {
        "manifest": _manifest(
            [
                _dir("conf", "0700", uid=1000, gid=1000),
                _file("conf/token", "0644", uid=0, gid=0),
            ]
        ),
        "roster": _roster(
            [_entry("conf/token", [], [])],
            [_subject(1000, [1, 1000])],
        ),
    },
    "group_dir_exec_exposes": {
        "manifest": _manifest(
            [
                _dir("conf", "0710", uid=0, gid=1000),
                _file("conf/token", "0644"),
            ]
        ),
        "roster": _roster(
            [_entry("conf/token", [], [])],
            [_subject(1000, [1, 1000])],
        ),
    },
    "uid0_owner_exposes": {
        "manifest": _manifest(
            [_dir("conf"), _file("conf/token", "0400", uid=0, gid=0)]
        ),
        "roster": _roster(
            [_entry("conf/token", [], [])],
            [_subject(0, [0])],
        ),
    },
    "other_write_preserved": {
        "manifest": _manifest([_dir("conf"), _file("conf/token", "0646")]),
        "roster": _roster(
            [_entry("conf/token", [], [])],
            [_subject(2000, [2000])],
        ),
    },
    # Other-execute must survive clearing only other-read (0645 -> 0641).
    "other_exec_preserved": {
        "manifest": _manifest([_dir("conf"), _file("conf/token", "0645")]),
        "roster": _roster(
            [_entry("conf/token", [], [])],
            [_subject(2000, [2000])],
        ),
    },
    # Group-execute must survive clearing only group-read (0750 -> 0710).
    "group_exec_preserved": {
        "manifest": _manifest(
            [_dir("conf"), _file("conf/token", "0750", uid=0, gid=1000)]
        ),
        "roster": _roster(
            [_entry("conf/token", [], [])],
            [_subject(2000, [1000])],
        ),
    },
    # Group-write must survive clearing only group-read (0670 -> 0630).
    "group_write_preserved": {
        "manifest": _manifest(
            [_file("secret", "0670", uid=1000, gid=1000)]
        ),
        "roster": _roster(
            [_entry("secret", [], [])],
            [_subject(2000, [1000])],
        ),
    },
    # 2^63 subject uid must appear unsigned in exposure strings.
    "uint64_high_uid_exposure": {
        "manifest": _manifest([_dir("conf"), _file("conf/token", "0644")]),
        "roster": _roster(
            [_entry("conf/token", [], [])],
            [_subject(9223372036854775808, [])],
        ),
    },
    # Escaping intermediate to a regular file (open must not occur).
    "escaped_intermediate_regular": {
        "manifest": _manifest(
            [
                _dir("conf"),
                _symlink("conf/door", "../../extra/secret.dat"),
            ],
            extra=[_file("secret.dat", "0644", uid=0, gid=0, content="trap")],
        ),
        "roster": _roster(
            [_entry("conf/door/any", [], [])],
            [_subject(2000, [2000])],
        ),
    },
    "uint64_gid_exact": {
        "manifest": _manifest([_dir("conf"), _file("conf/token", "0644")]),
        "roster": _roster(
            [_entry("conf/token", [], [18446744073709551614])],
            [_subject(2000, [18446744073709551615])],
        ),
    },
    "uint64_uid_ok": {
        "manifest": _manifest([]),
        "roster": _roster([], [_subject(9223372036854775808, [])]),
    },
    # float64 mantissa+1 must survive exact uint64 decode (not round to 2^53).
    "mantissa_uid_exposure": {
        "manifest": _manifest([_dir("conf"), _file("conf/token", "0644")]),
        "roster": _roster(
            [_entry("conf/token", [], [])],
            [_subject(9007199254740993, [9007199254740993])],
        ),
    },
    "mantissa_gid_unequal": {
        "manifest": _manifest([_dir("conf"), _file("conf/token", "0644")]),
        "roster": _roster(
            [_entry("conf/token", [], [9007199254740992])],
            [_subject(2000, [9007199254740993])],
        ),
    },
    "mantissa_allow_uids_unequal": {
        "manifest": _manifest([_dir("conf"), _file("conf/token", "0644")]),
        "roster": _roster(
            [_entry("conf/token", [9007199254740992], [])],
            [_subject(9007199254740993, [])],
        ),
    },
    # 2^63 accepted in allow_uids and subject gids (not only subject uid).
    "uint64_allow_uids_ok": {
        "manifest": _manifest([]),
        "roster": _roster(
            [_entry("a", [9223372036854775808], [])],
            [],
        ),
    },
    "uint64_subject_gids_ok": {
        "manifest": _manifest([]),
        "roster": _roster([], [_subject(1, [9223372036854775808])]),
    },
    # Independent per-finding remediation: other-clear vs group-clear.
    "multi_clear_independent": {
        "manifest": _manifest(
            [
                _dir("d"),
                _file("d/a", "0644", uid=1000, gid=3000),
                _file("d/b", "0644", uid=2001, gid=2002),
            ]
        ),
        "roster": _roster(
            [
                _entry("d/a", [], []),
                _entry("d/b", [2001], []),
            ],
            [
                _subject(2001, [2001]),
                _subject(2003, [2002]),
            ],
        ),
    },
    # Independent per-finding remediations: 0640 vs retained 0400.
    "multi_mode_independent": {
        "manifest": _manifest(
            [
                _dir("d"),
                _file("d/a", "0644", uid=1000, gid=1000),
                _file("d/b", "0400", uid=1001, gid=1001),
            ]
        ),
        "roster": _roster(
            [
                _entry("d/a", [], []),
                _entry("d/b", [], []),
            ],
            [
                _subject(2000, [2000]),
                _subject(1001, [1001]),
            ],
        ),
    },
    "socket_unsupported": {
        "manifest": _manifest([_dir("conf"), _socket("conf/sock")]),
        "roster": _roster([_entry("conf/sock", [0], [0])], [_subject(0, [0])]),
    },
    "device_unsupported": {
        "manifest": _manifest([_dir("conf"), _device("conf/dev")]),
        "roster": _roster([_entry("conf/dev", [0], [0])], [_subject(0, [0])]),
    },
    "symlink_ancestor_block": {
        "manifest": _manifest(
            [
                _symlink("via", "blocked/inner"),
                _dir("blocked", "0700", uid=2000, gid=2000),
                _dir("blocked/inner"),
                _file("blocked/inner/secret", "0644", uid=2000, gid=2000),
            ]
        ),
        "roster": _roster(
            [_entry("via/secret", [], [])],
            [_subject(1000, [1000])],
        ),
    },
    "dangling_intermediate_escaped": {
        "manifest": _manifest([_symlink("link", "nonexistent")]),
        "roster": _roster([_entry("link/token", [], [])], []),
    },
    "owner_exec_denies_other_grants": {
        "manifest": _manifest(
            [
                _dir("conf", "0601", uid=1000, gid=1000),
                _file("conf/token", "0644"),
            ]
        ),
        "roster": _roster(
            [_entry("conf/token", [], [])],
            [_subject(1000, [1000])],
        ),
    },
    "allow_via_secondary_gid": {
        "manifest": _manifest([_dir("conf"), _file("conf/token", "0644")]),
        "roster": _roster(
            [_entry("conf/token", [], [1000])],
            [_subject(2000, [1, 1000])],
        ),
    },
    "allow_via_later_gid": {
        # Second allow_gids element is decisive; consulting only [0] wrongly exposes.
        "manifest": _manifest([_file("secret", "0640", uid=1000, gid=1000)]),
        "roster": _roster(
            [_entry("secret", [], [9, 1000])],
            [_subject(2000, [1000])],
        ),
    },
    "dup_subject_uid_group": {
        "manifest": _manifest([_dir("conf"), _file("conf/token", "0640")]),
        "roster": _roster(
            [_entry("conf/token", [], [])],
            [_subject(2000, [2000]), _subject(2000, [1000])],
        ),
    },
    "group_clear_nonempty_allow": {
        "manifest": _manifest([_dir("conf"), _file("conf/token", "0640")]),
        "roster": _roster(
            [_entry("conf/token", [1000], [9])],
            [
                _subject(1000, [1000]),
                _subject(1001, [1000]),
            ],
        ),
    },
    "gt_path": {
        "manifest": _manifest([_file("a>b", "0644", uid=1000, gid=1000, content="h")]),
        "roster": _roster(
            [_entry("a>b", [], [])],
            [_subject(2000, [2000])],
        ),
    },
    "u2028_path": {
        "manifest": _manifest(
            [_file("a\u2028b", "0644", uid=1000, gid=1000, content="u")]
        ),
        "roster": _roster(
            [_entry("a\u2028b", [], [])],
            [_subject(2000, [2000])],
        ),
    },
    "u2029_path": {
        "manifest": _manifest(
            [_file("a\u2029b", "0644", uid=1000, gid=1000, content="u")]
        ),
        "roster": _roster(
            [_entry("a\u2029b", [], [])],
            [_subject(2000, [2000])],
        ),
    },
    "backspace_path": {
        "manifest": _manifest(
            [_file("a\x08b", "0644", uid=1000, gid=1000, content="b")]
        ),
        "roster": _roster(
            [_entry("a\x08b", [], [])],
            [_subject(2000, [2000])],
        ),
    },
    "bell_path": {
        # U+0007 must be JSON \u0007, not Go strconv.Quote's \a.
        "manifest": _manifest(
            [_file("a\x07b", "0644", uid=1000, gid=1000, content="g")]
        ),
        "roster": _roster(
            [_entry("a\x07b", [], [])],
            [_subject(2000, [2000])],
        ),
    },
    "formfeed_path": {
        "manifest": _manifest(
            [_file("a\x0cb", "0644", uid=1000, gid=1000, content="f")]
        ),
        "roster": _roster(
            [_entry("a\x0cb", [], [])],
            [_subject(2000, [2000])],
        ),
    },
    "group_exposure_keeps_other": {
        "manifest": _manifest([_dir("conf"), _file("conf/token", "0644")]),
        "roster": _roster(
            [_entry("conf/token", [1000, 2000], [])],
            [
                _subject(1000, [1000]),
                _subject(1001, [1000]),
                _subject(2000, [2000]),
            ],
        ),
    },
    "parent_above_root_ignored": {
        "manifest": _manifest(
            [_dir("conf"), _file("conf/token", "0644")],
            hostile_parent=True,
        ),
        "roster": _roster(
            [_entry("conf/token", [], [])],
            [_subject(1001, [1001])],
        ),
    },
    "root_via_alias_link": {
        # --root is reached through a symlinked ancestor; final link stays under
        # the root's real path and must produce a finding (not escaped).
        "manifest": _manifest(
            [
                _file("secret", "0644", uid=0, gid=0),
                _symlink("inside", "secret"),
            ],
            root_via_alias=True,
        ),
        "roster": _roster(
            [_entry("inside", [], [])],
            [_subject(1000, [1000])],
        ),
    },
}


def write_cases(base: Path) -> int:
    base.mkdir(parents=True, exist_ok=True)
    for name, case in CASES.items():
        cdir = base / name
        cdir.mkdir(parents=True, exist_ok=True)
        for fname, key in (("manifest.json", "manifest"), ("roster.json", "roster")):
            text = json.dumps(case[key], indent=2, ensure_ascii=False) + "\n"
            (cdir / fname).write_text(text, encoding="utf-8")
    return len(CASES)


if __name__ == "__main__":
    out = Path(__file__).resolve().parent / "sealed"
    n = write_cases(out)
    print(n)
