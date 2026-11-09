#!/usr/bin/env python3
"""SMF-2026 scorecard folder: average precision and ECE over labeled packs."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path
from typing import Any

sys.set_int_max_str_digits(0)

DISPOSITION_ORDER = ["ok", "reject_schema", "reject_class", "reject_fold"]
ALLOWED_BINS = frozenset({2, 4, 5, 8, 10, 16, 20})
MICRO = Decimal(1_000_000)
BP_SUM = 10000
INTERPOLANT = "voc11"
CLOSED_SPACE = ("all_point", "voc11", "voc101")
CLOSED_ECE = ("max", "label")
CLOSED_PLACE = CLOSED_ECE
CLOSED_MEAN = CLOSED_ECE
CLOSED_SURVIVAL = ("max", "label")
CLOSED_TIE = ("earliest", "latest")
CLOSED_RANK = ("id_asc", "id_desc")
Policy = tuple[str, str, str, str, str, str]


class FoldError(Exception):
    def __init__(self, disposition: str):
        self.disposition = disposition
        super().__init__(disposition)


def _round_half_even_int(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_EVEN))


def _micro_u(fraction: Decimal) -> int:
    return _round_half_even_int(fraction * MICRO)


def _is_int(value: object) -> bool:
    return type(value) is int


def _clear_out(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for entry in list(path.iterdir()):
        if entry.is_symlink() or entry.is_file():
            entry.unlink()
        elif entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()


def _require_obj(value: object, key: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise FoldError("reject_schema")
    return value


def _require_str(value: object) -> str:
    if type(value) is not str or value == "":
        raise FoldError("reject_schema")
    return value


def _require_int(value: object) -> int:
    if not _is_int(value):
        raise FoldError("reject_schema")
    return value


def _load_json(path: Path) -> Any:
    def _reject_constant(token: str) -> None:
        raise json.JSONDecodeError(f"nonstandard JSON token {token}", token, 0)

    try:
        text = path.read_bytes().decode("utf-8")
        return json.loads(text, parse_constant=_reject_constant)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FoldError("reject_schema") from exc


def _parse_p_bp(raw: object, classes: list[str]) -> dict[str, int]:
    if not isinstance(raw, dict):
        raise FoldError("reject_schema")
    if set(raw.keys()) != set(classes):
        raise FoldError("reject_schema")
    parsed: dict[str, int] = {}
    total = 0
    for name in classes:
        mass = raw[name]
        if not _is_int(mass) or mass < 0 or mass > BP_SUM:
            raise FoldError("reject_schema")
        parsed[name] = mass
        total += mass
    if total != BP_SUM:
        raise FoldError("reject_schema")
    return parsed


def _parse_classes(classes_raw: object) -> list[str]:
    if not isinstance(classes_raw, list):
        raise FoldError("reject_schema")
    if not classes_raw:
        raise FoldError("reject_class")
    classes: list[str] = []
    for item in classes_raw:
        if type(item) is not str or item == "":
            raise FoldError("reject_schema")
        classes.append(item)
    if len(classes) != len(set(classes)):
        raise FoldError("reject_class")
    return classes


def _parse_row_schema(raw: object, classes: list[str]) -> dict[str, Any]:
    row = _require_obj(raw, "row")
    example_id = _require_str(row.get("example_id"))
    label = row.get("label")
    if type(label) is not str:
        raise FoldError("reject_schema")
    p_bp = _parse_p_bp(row.get("p_bp"), classes)
    return {"example_id": example_id, "label": label, "p_bp": p_bp}


def _reject_labels(slices: list[dict[str, Any]], classes: list[str]) -> None:
    allowed = set(classes)
    for sl in slices:
        for row in sl["rows"]:
            if row["label"] not in allowed:
                raise FoldError("reject_class")


def _parse_card(raw: object, card_id: str) -> dict[str, Any]:
    card = _require_obj(raw, "card")
    if _require_str(card.get("card_id")) != card_id:
        raise FoldError("reject_schema")
    classes = _parse_classes(card.get("classes"))
    bin_count = _require_int(card.get("bin_count"))
    min_conf_bp = _require_int(card.get("min_conf_bp"))
    if "mode" in card:
        mode = card.get("mode")
        if mode not in ("live", "frozen"):
            raise FoldError("reject_schema")
    else:
        mode = "live"
    slices_raw = card.get("slices")
    if not isinstance(slices_raw, list) or not slices_raw:
        raise FoldError("reject_schema")
    slices: list[dict[str, Any]] = []
    for item in slices_raw:
        sl = _require_obj(item, "slice")
        slice_id = _require_str(sl.get("slice_id"))
        rows_raw = sl.get("rows")
        if not isinstance(rows_raw, list) or not rows_raw:
            raise FoldError("reject_schema")
        rows = [_parse_row_schema(row, classes) for row in rows_raw]
        slices.append({"slice_id": slice_id, "rows": rows})
    slices.sort(key=lambda sl: sl["slice_id"])
    example_ids = [row["example_id"] for sl in slices for row in sl["rows"]]
    if len(example_ids) != len(set(example_ids)):
        raise FoldError("reject_schema")
    _reject_labels(slices, classes)
    if bin_count not in ALLOWED_BINS or min_conf_bp < 0 or min_conf_bp > BP_SUM:
        raise FoldError("reject_fold")
    return {
        "card_id": card_id,
        "classes": classes,
        "bin_count": bin_count,
        "min_conf_bp": min_conf_bp,
        "mode": mode,
        "slices": slices,
    }


def _conf_bp(p_bp: dict[str, int], survival: str = "max", label: str | None = None) -> int:
    if survival == "label":
        if label is None:
            raise ValueError("label required for survival=label")
        return p_bp[label]
    return max(p_bp.values())


def _pred_class(p_bp: dict[str, int], pred_tie: str = "earliest") -> str:
    best = max(p_bp.values())
    names = sorted(name for name, mass in p_bp.items() if mass == best)
    if pred_tie == "latest":
        return names[-1]
    return names[0]


def _surviving_rows(card: dict[str, Any], survival: str = "max") -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for sl in card["slices"]:
        for row in sl["rows"]:
            conf = _conf_bp(row["p_bp"], survival, row["label"])
            if conf >= card["min_conf_bp"]:
                kept.append(row)
    if not kept:
        raise FoldError("reject_fold")
    return kept


def _ranked_labels(
    rows: list[dict[str, Any]], class_name: str, rank_secondary: str = "id_asc"
) -> list[str]:
    if rank_secondary == "id_desc":
        ordered = sorted(
            rows, key=lambda row: (row["p_bp"][class_name], row["example_id"]), reverse=True
        )
    else:
        ordered = sorted(rows, key=lambda row: (-row["p_bp"][class_name], row["example_id"]))
    return [row["label"] for row in ordered]


def _positive_points(labels: list[str], positive: str) -> tuple[int, list[tuple[Decimal, Decimal]]] | None:
    n_pos = sum(1 for label in labels if label == positive)
    if n_pos == 0:
        return None
    points: list[tuple[Decimal, Decimal]] = []
    tp = 0
    fp = 0
    for label in labels:
        if label == positive:
            tp += 1
        else:
            fp += 1
            continue
        recall = Decimal(tp) / Decimal(n_pos)
        precision = Decimal(tp) / Decimal(tp + fp)
        points.append((recall, precision))
    return n_pos, points


def ap_all_point(labels: list[str], positive: str) -> Decimal | None:
    packed = _positive_points(labels, positive)
    if packed is None:
        return None
    n_pos, points = packed
    ap = Decimal(0)
    prev = Decimal(0)
    tp = 0
    fp = 0
    for label in labels:
        if label == positive:
            tp += 1
        else:
            fp += 1
            continue
        recall = Decimal(tp) / Decimal(n_pos)
        precision = Decimal(tp) / Decimal(tp + fp)
        ap += (recall - prev) * precision
        prev = recall
    return ap


def ap_voc(labels: list[str], positive: str, samples: int) -> Decimal | None:
    packed = _positive_points(labels, positive)
    if packed is None:
        return None
    _, points = packed
    total = Decimal(0)
    for i in range(samples):
        recall_at = Decimal(i) / Decimal(samples - 1)
        candidates = [prec for rec, prec in points if rec >= recall_at]
        total += max(candidates) if candidates else Decimal(0)
    return total / Decimal(samples)


def average_precision(labels: list[str], positive: str, interpolant: str) -> Decimal | None:
    if interpolant == "all_point":
        return ap_all_point(labels, positive)
    if interpolant == "voc11":
        return ap_voc(labels, positive, 11)
    if interpolant == "voc101":
        return ap_voc(labels, positive, 101)
    raise ValueError(interpolant)


def macro_ap(
    rows: list[dict[str, Any]],
    classes: list[str],
    interpolant: str,
    rank_secondary: str = "id_asc",
) -> Decimal:
    parts: list[Decimal] = []
    for class_name in classes:
        labels = _ranked_labels(rows, class_name, rank_secondary)
        value = average_precision(labels, class_name, interpolant)
        if value is not None:
            parts.append(value)
    return sum(parts, Decimal(0)) / Decimal(len(parts))


def _ece_conf_bp(row: dict[str, Any], ece_conf: str) -> int:
    if ece_conf == "label":
        return row["p_bp"][row["label"]]
    return max(row["p_bp"].values())


def expected_calibration_error(
    rows: list[dict[str, Any]],
    bin_count: int,
    place_conf: str = "max",
    mean_conf: str = "max",
    pred_tie: str = "earliest",
) -> Decimal:
    buckets: list[list[dict[str, Any]]] = [[] for _ in range(bin_count)]
    for row in rows:
        conf = _ece_conf_bp(row, place_conf)
        index = min(bin_count - 1, conf * bin_count // BP_SUM)
        buckets[index].append(row)
    n_total = len(rows)
    total = Decimal(0)
    for bucket in buckets:
        n_bin = len(bucket)
        if n_bin == 0:
            continue
        correct = sum(
            1 for row in bucket if _pred_class(row["p_bp"], pred_tie) == row["label"]
        )
        acc_bp = _round_half_even_int(Decimal(10000 * correct) / Decimal(n_bin))
        conf_sum = sum(_ece_conf_bp(row, mean_conf) for row in bucket)
        conf_bp = _round_half_even_int(Decimal(conf_sum) / Decimal(n_bin))
        total += Decimal(n_bin) * Decimal(abs(acc_bp - conf_bp)) * Decimal(100) / Decimal(n_total)
    return total / Decimal(1_000_000)


def _effective_members(
    card: dict[str, Any],
    interpolant: str,
    place_conf: str,
    mean_conf: str,
    survival: str,
    pred_tie: str,
    rank_secondary: str,
) -> Policy:
    if card.get("mode", "live") == "frozen":
        return interpolant, "max", "max", "max", "earliest", rank_secondary
    return interpolant, place_conf, mean_conf, survival, pred_tie, rank_secondary


def fold_card(
    card: dict[str, Any],
    interpolant: str = INTERPOLANT,
    place_conf: str = "max",
    mean_conf: str = "max",
    survival: str = "max",
    pred_tie: str = "earliest",
    rank_secondary: str = "id_asc",
) -> dict[str, Any]:
    inter, place, mean, surv, tie, rank = _effective_members(
        card, interpolant, place_conf, mean_conf, survival, pred_tie, rank_secondary
    )
    rows = _surviving_rows(card, surv)
    ap = macro_ap(rows, card["classes"], inter, rank)
    ece = expected_calibration_error(rows, card["bin_count"], place, mean, tie)
    return {
        "ap_micro_u": _micro_u(ap),
        "bin_count": card["bin_count"],
        "card_id": card["card_id"],
        "class_count": len(card["classes"]),
        "disposition": "ok",
        "ece_micro_u": _micro_u(ece),
        "row_count": len(rows),
    }


def empty_row(card_id: str, disposition: str, weight: int = 0) -> dict[str, Any]:
    return {
        "ap_micro_u": 0,
        "bin_count": 0,
        "card_id": card_id,
        "class_count": 0,
        "disposition": disposition,
        "ece_micro_u": 0,
        "row_count": 0,
        "weight": weight,
    }


def fold_pack_card(
    cards_dir: Path,
    card_id: str,
    weight: int,
    interpolant: str = INTERPOLANT,
    place_conf: str = "max",
    mean_conf: str = "max",
    survival: str = "max",
    pred_tie: str = "earliest",
    rank_secondary: str = "id_asc",
) -> dict[str, Any]:
    if weight < 1:
        return empty_row(card_id, "reject_schema", 0)
    # Card file must be "<stem>.json" beside the manifest. Only '/' is a path
    # separator on this Linux runtime; '\\', '.', and '..' are ordinary stems
    # when the file is a regular sibling of the manifest (e.g. "..json").
    if "/" in card_id:
        return empty_row(card_id, "reject_schema", 0)
    path = cards_dir / f"{card_id}.json"
    try:
        if path.resolve().parent != cards_dir.resolve():
            return empty_row(card_id, "reject_schema", 0)
    except OSError:
        return empty_row(card_id, "reject_schema", 0)
    if not path.is_file() or path.is_symlink():
        return empty_row(card_id, "reject_schema", 0)
    try:
        raw = _load_json(path)
        # Document card_id must equal the actual file stem, not a path-ish manifest id.
        card = _parse_card(raw, path.stem)
        folded = fold_card(
            card, interpolant, place_conf, mean_conf, survival, pred_tie, rank_secondary
        )
        folded["weight"] = weight
        return folded
    except FoldError as exc:
        return empty_row(card_id, exc.disposition, 0)


def read_manifest(cards_dir: Path) -> list[tuple[str, int]]:
    manifest = cards_dir / "manifest.jsonl"
    if not manifest.is_file():
        raise FileNotFoundError(str(manifest))
    entries: list[tuple[str, int]] = []
    for line in manifest.read_bytes().decode("utf-8").split("\n"):
        if not line.strip():
            continue
        row = json.loads(line)
        card_id = row["card_id"]
        if type(card_id) is not str or not card_id:
            raise ValueError("manifest card_id")
        if "weight" in row:
            weight = row["weight"]
            if not _is_int(weight) or weight < 1:
                entries.append((card_id, 0))
                continue
        else:
            weight = 1
        entries.append((card_id, weight))
    return sorted(entries, key=lambda item: item[0])


def dumps_sorted(row: dict[str, Any]) -> str:
    return json.dumps(row, separators=(",", ":"), sort_keys=True, ensure_ascii=False)


def _half_even_div(numer: int, denom: int) -> int:
    """Half-even round numer/denom using exact integer arithmetic."""
    if denom == 0:
        return 0
    quot, rem = divmod(numer, denom)
    twice = rem * 2
    if twice > denom:
        return quot + 1
    if twice < denom:
        return quot
    return quot if quot % 2 == 0 else quot + 1


def write_summary(rows: list[dict[str, Any]], path: Path) -> None:
    counts = {name: 0 for name in DISPOSITION_ORDER}
    weight_sum = 0
    ap_acc = 0
    ece_acc = 0
    for row in rows:
        counts[row["disposition"]] += 1
        if row["disposition"] == "ok":
            w = row["weight"]
            weight_sum += w
            ap_acc += w * row["ap_micro_u"]
            ece_acc += w * row["ece_micro_u"]
    ap_pool = _half_even_div(ap_acc, weight_sum)
    ece_pool = _half_even_div(ece_acc, weight_sum)
    inner = ",".join(json.dumps(name) + ":" + json.dumps(counts[name]) for name in DISPOSITION_ORDER)
    text = (
        '{"ap_pool_micro_u":'
        + json.dumps(ap_pool)
        + ',"by_disposition":{'
        + inner
        + '},"card_count":'
        + json.dumps(len(rows))
        + ',"ece_pool_micro_u":'
        + json.dumps(ece_pool)
        + ',"ok_count":'
        + json.dumps(counts["ok"])
        + "}\n"
    )
    path.write_text(text)


def recover_policy(spec_dir: Path) -> Policy:
    examples_dir = spec_dir / "worked_examples"
    payloads: list[Any] = []
    for path in sorted(examples_dir.glob("*.json")):
        if path.is_symlink() or not path.is_file():
            continue
        payloads.append(_load_json(path))
    if not payloads:
        raise ValueError("no worked examples")
    matching: list[Policy] = []
    for policy in CLOSED_SPACE:
        for place_conf in CLOSED_PLACE:
            for mean_conf in CLOSED_MEAN:
                for survival in CLOSED_SURVIVAL:
                    for pred_tie in CLOSED_TIE:
                        for rank_secondary in CLOSED_RANK:
                            matched = True
                            for payload in payloads:
                                if not isinstance(payload, dict):
                                    raise ValueError("example payload")
                                card_obj = payload.get("card")
                                if not isinstance(card_obj, dict):
                                    raise ValueError("example card")
                                try:
                                    parsed = _parse_card(
                                        card_obj, _require_str(card_obj.get("card_id"))
                                    )
                                    # Worked examples are always folded as live regardless of
                                    # any mode written on the example card document.
                                    parsed["mode"] = "live"
                                    folded = fold_card(
                                        parsed,
                                        policy,
                                        place_conf,
                                        mean_conf,
                                        survival,
                                        pred_tie,
                                        rank_secondary,
                                    )
                                except FoldError:
                                    matched = False
                                    break
                                expected_ap = payload.get("ap_micro_u")
                                expected_ece = payload.get("ece_micro_u")
                                if (
                                    not _is_int(expected_ap)
                                    or not _is_int(expected_ece)
                                    or folded["ap_micro_u"] != expected_ap
                                    or folded["ece_micro_u"] != expected_ece
                                ):
                                    matched = False
                                    break
                            if matched:
                                matching.append(
                                    (
                                        policy,
                                        place_conf,
                                        mean_conf,
                                        survival,
                                        pred_tie,
                                        rank_secondary,
                                    )
                                )
    if len(matching) != 1:
        raise ValueError(f"policy not unique: {matching}")
    return matching[0]


def compile_manifest(
    cards_dir: Path,
    out_dir: Path,
    spec_dir: Path,
) -> list[dict[str, Any]]:
    interpolant, place_conf, mean_conf, survival, pred_tie, rank_secondary = recover_policy(
        spec_dir
    )
    _clear_out(out_dir)
    entries = read_manifest(cards_dir)
    rows = [
        fold_pack_card(
            cards_dir,
            card_id,
            weight,
            interpolant,
            place_conf,
            mean_conf,
            survival,
            pred_tie,
            rank_secondary,
        )
        for card_id, weight in entries
    ]
    with (out_dir / "folds.jsonl").open("w") as handle:
        for row in rows:
            handle.write(dumps_sorted(row) + "\n")
    write_summary(rows, out_dir / "summary.json")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cards", required=True, type=Path)
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    spec_md = args.spec / "SMF_SPEC.md"
    examples = args.spec / "worked_examples"
    if not spec_md.is_file() or not examples.is_dir():
        print("missing SMF spec surfaces", file=sys.stderr)
        return 1
    try:
        compile_manifest(args.cards, args.out, args.spec)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(f"pack error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
