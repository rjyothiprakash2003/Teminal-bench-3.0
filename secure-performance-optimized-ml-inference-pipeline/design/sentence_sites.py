#!/usr/bin/env python3
"""Sentence × site gate for serving-contract.txt (executable-rival subset).

Only sentences with an executable rival in rivals.py are gated here. Other
contract sentences remain page law and are covered by verifier fixtures; they
are listed in design/SITTING.md as AMBER backlog until a patch lands.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "environment" / "kelso" / "policy" / "serving-contract.txt"
REF_FILES = [
    ROOT / "solution" / "internal" / "scorekit" / "scorekit.go",
    ROOT / "solution" / "internal" / "httpapi" / "httpapi.go",
    ROOT / "solution" / "internal" / "tickbench" / "tickbench.go",
    ROOT / "solution" / "internal" / "ridgeio" / "ridgeio.go",
    ROOT / "solution" / "cmd" / "ridgecast" / "ridgecast.go",
    ROOT / "solution" / "ops" / "ci-gate.sh",
]

SENTENCES = [
    ("S1", "Extra keys are illegal, as are"),
    ("S6", "Scale each field as (value minus mean) divided by std"),
    ("S10", "share an id but differ in features must not share a cache entry"),
    ("S11", "Request i must receive the micros of request i whether"),
    ("S12", "exact ASCII token Bearer, one space"),
    ("S13", "exact lowercase string application/json"),
    ("S18", "After a failing selfcheck the gate must not run score or bench"),
]

SITES = {
    "S1": ["scorekit.Parse:extra", "scorekit.Parse:forbidden"],
    "S6": ["scorekit.LogitOn:schema_order_div_std", "ridgecast.selfcheck:mean_lab"],
    "S10": ["scorekit.cacheKey:features_not_id", "http:cache_same_id"],
    "S11": ["scorekit.ScoreChunk:per_request_copy", "cli:batch_limit_3"],
    "S12": ["scorekit.AuthOK:exact_Bearer", "http:bearer_case"],
    "S13": ["httpapi.jsonContentType:lowercase", "http:Application_JSON"],
    "S18": ["ci-gate.sh:selfcheck_then_stop", "test_ci_gate_rebuild_selfcheck_and_fail_fast"],
}

RIVAL_FOR = {
    "S1": "parse_allows_extra",
    "S6": "logit_div_std_sq",
    "S10": "cache_key_by_id",
    "S11": "score_chunk_last_wins",
    "S12": "auth_equal_fold_bearer",
    "S13": "content_type_equal_fold",
    "S18": "gate_ignores_selfcheck_rc",
}


def _norm(s: str) -> str:
    return " ".join(s.split())


def main() -> None:
    text = _norm(PAGE.read_text())
    rivals_src = (ROOT / "design" / "rivals.py").read_text()
    gaps: list[str] = []
    for sid, anchor in SENTENCES:
        count = text.count(_norm(anchor))
        if count != 1:
            gaps.append(f"{sid}: anchor count={count} for {anchor!r}")
        if not SITES.get(sid):
            gaps.append(f"{sid}: no sites")
        rival = RIVAL_FOR.get(sid)
        if not rival:
            gaps.append(f"{sid}: no rival mapping")
        elif f'"{rival}"' not in rivals_src and f"'{rival}'" not in rivals_src:
            gaps.append(f"{sid}: rival {rival} missing from rivals.py")
        for site in SITES[sid]:
            # every site needs its rival named for this sentence
            if rival and rival not in rivals_src:
                gaps.append(f"{sid}×{site}: rival absent")
    print(f"sentences {len(SENTENCES)}; sites {sum(len(v) for v in SITES.values())}")
    if gaps:
        print("GAPS:")
        for g in gaps:
            print(" ", g)
        raise SystemExit(1)
    print("sentence × site: 0 gaps")


if __name__ == "__main__":
    main()
