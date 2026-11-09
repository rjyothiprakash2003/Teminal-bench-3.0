SMF-2026 Scorecard Metric Fold Specification

This memo is the authoritative surface for `/app/fold.py`.

Processing order

Process every row of the pack's `manifest.jsonl` (for the staged pack: `/app/cards/manifest.jsonl`) in ascending `card_id` order. Each row is a JSON object that names `card_id`. A manifest row may also name `weight`. Unknown keys on a manifest row are ignored. Manifest records are separated only by U+000A (LF), and each record is one JSON object. Shipped manifests follow that grammar. Do not repair or drop a record. The card file is `<card_id>.json` beside the manifest. A missing file, a symlink, a file that is not UTF-8 text, or a file that is not JSON is disposition `reject_schema`. The tokens `NaN`, `Infinity`, and `-Infinity` are not JSON. Every integer written in a manifest, a card file, or a worked example has at most 1000 decimal digits; what the program does with any other input is not part of these notes.

Manifest weight

If `weight` is absent, the card's weight is 1. If `weight` is present, it must be an integer greater than or equal to 1; otherwise the card is disposition `reject_schema` and the card file is not folded (emit the usual zeroed reject row, including `weight` 0). Weight does not affect per-card average precision or ECE; it only enters the pack pools in `summary.json`.

CLI paths

`--cards` names a card-pack directory that contains `manifest.jsonl` (not the manifest file path itself). `--spec` names a directory that contains `SMF_SPEC.md` and `worked_examples/`. `--out` names an output directory.

Card document

A card is a JSON object. `card_id` must equal the file stem. `classes` is a non-empty array of distinct non-empty strings. `bin_count` is an integer in the closed set 2, 4, 5, 8, 10, 16, 20. `min_conf_bp` is an integer in 0..10000 inclusive. `slices` is a non-empty array. Each slice has a non-empty `slice_id` string and a non-empty `rows` array. Optional `mode` is the string `live` or `frozen`. If `mode` is absent, the card is `live`. Any other `mode` value is disposition `reject_schema`.

Rows

Each row has `example_id` (non-empty string), `label` (a string that must be a member of `classes`), and `p_bp` (an object). `p_bp` must contain exactly the class names as keys. Every mass is an integer in 0..10000. The masses on one row sum to exactly 10000. JSON booleans are not integers.

Duplicate identifiers

`example_id` values must be unique across every slice of a card. A repeated identifier is disposition `reject_schema`.

Dispositions

Every card receives exactly one of: `ok`, `reject_schema`, `reject_class`, `reject_fold`. Schema failures include missing fields, wrong JSON types, a `card_id` mismatch, a `classes` element that is not a non-empty string, a `p_bp` key set that does not equal `classes`, a mass sum other than 10000, empty slices or rows arrays, a missing or empty `slice_id`, duplicate `example_id`, an invalid `mode`, and an invalid manifest `weight`. Class failures include an empty or duplicated `classes` list and a `label` that is not in `classes`. Fold failures include a `bin_count` outside the closed set, a `min_conf_bp` outside 0..10000, and a card that has no surviving row after the confidence floor.

Validation order

Stop at the first matching failure, and finish each phase before the next begins. (1) Document schema: the card is a JSON object, `card_id` matches the file stem, `classes` is present and is an array whose every element is a non-empty string, `bin_count` and `min_conf_bp` have integer type, `mode` is absent or is exactly `live` or `frozen`, `slices` is a non-empty array of objects, each slice has a non-empty `slice_id` and a non-empty `rows` array, `example_id` values are unique, and each row has the required fields with a well-typed `p_bp` whose keys equal the class-name set and whose masses are integers in 0..10000 that sum to 10000. Those failures are `reject_schema`, except: if `classes` is an array that is empty or that contains duplicate names, emit `reject_class` immediately and do not reclassify the card as `reject_schema` because `p_bp` keys then fail to match. A missing or non-array `classes` field remains `reject_schema`. An empty-string or non-string element is `reject_schema`, not `reject_class`, and that element check precedes the duplicate-name exception. A missing `slices` field, an empty `slices` or `rows` array, or a missing or mistyped `p_bp` stays `reject_schema` even when `bin_count` is outside the closed set or a `label` is outside `classes`. (2) Each `label` must be a member of `classes`; otherwise `reject_class`. Phase (2) runs only after phase (1) succeeds, and it runs before phase (3): a label outside `classes` is `reject_class` even when `bin_count` is outside the closed set or `min_conf_bp` is outside 0..10000. (3) `bin_count` must be in the closed set and `min_conf_bp` in 0..10000; otherwise `reject_fold`. (4) If no row survives the confidence floor, `reject_fold`.

Confidence floor

A row survives when its survival confidence is at least `min_conf_bp`. Survival confidence uses one member from the closed space max and label. max uses the row's maximum mass. label uses the mass of the row's `label` class. Average precision and expected calibration error both use the surviving rows only. On a `live` card, the active survival member is the pack-recovered survival member. On a `frozen` card, survival confidence is always max, regardless of the pack-recovered survival member.

Predicted class

The predicted class is the class of greatest mass. When two or more classes share that mass, the predicted class uses one member from the closed space earliest and latest. earliest selects the lexicographically earliest class name among the tied names. latest selects the lexicographically latest class name among the tied names. On a `live` card, the active prediction-tie member is the pack-recovered prediction-tie member. On a `frozen` card, prediction ties always use earliest, regardless of the pack-recovered prediction-tie member.

One-versus-rest ranking

Average precision is one-versus-rest for every class that has at least one surviving ground-truth positive, then the equal-weight mean of those class values. A class with zero surviving positives is omitted from the mean. Macro averaging walks the card's `classes` array in document order (the order written in the card), not sorted order. For class c the ranking key is that class's mass, descending. Ties break by `example_id` using one member from the closed space id_asc and id_desc. id_asc breaks ties by `example_id` ascending. id_desc breaks ties by `example_id` descending. Slice identity does not change the ranking: all surviving rows form one ranking, not a mean of per-slice averages. The active ranking-tie member is always the pack-recovered ranking-tie member, including on `frozen` cards.

Interpolation

Average precision uses one interpolation rule from the closed space all_point, voc11, and voc101. all_point adds, at each true-positive rank, the increase in recall times the precision at that rank. voc11 and voc101 average the highest precision whose recall is at least the sample: voc11 uses the 11 levels 0, 0.1, ..., 1, and voc101 uses the 101 levels 0, 0.01, ..., 1. On every run, recover the interpolant, the placement member, the mean-confidence member, the survival member, the prediction-tie member, and the ranking-tie member together from the worked examples in the `--spec` directory passed to that run. A different `--spec` directory selects its own 6-tuple. Do not reuse a 6-tuple selected for another directory. Worked-example cards are folded as `live` cards (apply the candidate 6-tuple in full).

Bin confidence

ECE uses two independent members from the closed space max and label. Placement confidence selects the mass that indexes the equal-width bin. Mean confidence selects the mass that enters that bin's mean-confidence average. max uses the row's maximum mass. label uses the mass of the row's `label` class. The two members may differ. Survival confidence (above) is a third, independently chosen member from the same closed space and may differ from both ECE members. On a `live` card, placement and mean confidence use the pack-recovered members. On a `frozen` card, both placement and mean confidence are always max, regardless of the pack-recovered ECE members. Every worked-example JSON object records `ap_micro_u` and `ece_micro_u`. The unique 6-tuple (interpolant, placement, mean confidence, survival, prediction tie, ranking tie) whose both figures match every worked example in that `--spec` directory is required.

Expected calibration error

Partition surviving rows into `bin_count` equal-width bins on the selected placement confidence. Bin index is min(`bin_count` - 1, confidence * `bin_count` // 10000), so a confidence of 10000 sits in the last bin. Empty bins contribute nothing. For a non-empty bin, accuracy in basis points is half-even rounded 10000 * correct / n_bin, where correct counts rows whose predicted class equals `label`. Mean confidence in basis points is half-even rounded sum(mean-confidence masses) / n_bin. The ECE fraction is the n_bin-weighted mean of absolute accuracy-minus-confidence differences divided by 10000.

Integer micro units

`ap_micro_u` is half-even rounded average precision times 1_000_000. `ece_micro_u` is half-even rounded ECE times 1_000_000. Both are JSON integers, not booleans and not floats.

Output

`/app/fold.py` accepts `--cards`, `--spec`, and `--out`. It removes every pre-existing entry under the output directory, including directories, symlinks, and non-regular nodes, then writes only `folds.jsonl` and `summary.json`. When pointed at the staged pack those files are `/app/output/folds.jsonl` and `/app/output/summary.json`.

folds.jsonl

One object per card, keys sorted, rows sorted by `card_id`, fields `ap_micro_u`, `bin_count`, `card_id`, `class_count`, `disposition`, `ece_micro_u`, `row_count`, and `weight`. `row_count` is the number of rows that survive the confidence floor on that card. `class_count` is the length of the card's `classes` list, including classes that have zero surviving ground-truth positives. `weight` is the manifest weight used for that card (1 when absent). Reject rows use zeros for every numeric field other than the disposition itself, including `row_count`, `class_count`, and `weight`.

summary.json

Fields are `ap_pool_micro_u`, `by_disposition`, `card_count`, `ece_pool_micro_u`, and `ok_count` in that key order. The object `by_disposition` uses exactly these keys in this order: `ok`, `reject_schema`, `reject_class`, `reject_fold` (zeros allowed). `ap_pool_micro_u` is the half-even rounded weighted mean of `ap_micro_u` over cards with disposition `ok`, using each card's manifest weight; if there is no `ok` card, it is 0. `ece_pool_micro_u` is the same construction over `ece_micro_u`. Rejected cards do not enter either pool.
