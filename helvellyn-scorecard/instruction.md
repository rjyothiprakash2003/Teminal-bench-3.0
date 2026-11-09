The evaluation harness desk left an unfinished scorecard folder at `/app/fold.py`. Implement that program so it folds every card named by `/app/cards/manifest.jsonl` using SMF-2026 in `/app/scorecard/SMF_SPEC.md`, writing `/app/output/folds.jsonl` and `/app/output/summary.json`. Field meanings, dispositions, validation order, mode behavior, pool definitions, member definitions, and serialization are fixed in `/app/scorecard/SMF_SPEC.md`.

- Average precision uses exactly one interpolant from the closed space all_point, voc11, and voc101.
- ECE placement confidence and ECE mean confidence each use one member from the closed space max and label; the two members are chosen independently and may differ.
- Survival confidence for the confidence floor also uses one member from max and label, chosen independently of the ECE members.
- Prediction ties use one member from the closed space earliest and latest.
- Ranking ties on equal class mass use one member from the closed space id_asc and id_desc.
- The `--spec` directory's worked examples record `ap_micro_u` and `ece_micro_u`; the active interpolant, placement member, mean-confidence member, survival member, prediction-tie member, and ranking-tie member are the unique 6-tuple whose values equal those figures.
- Cards may set `mode` to live or frozen; frozen cards lock survival, placement, mean confidence, and prediction ties to max/max/max/earliest while still using the pack interpolant and ranking-tie member.
- Manifest rows may carry a positive integer `weight` that feeds pack-level pools in `summary.json`.
- `/app/fold.py` must accept `--cards`, `--spec`, and `--out`, each naming a directory. For this pack those directories are `/app/cards`, `/app/scorecard`, and `/app/output`, and `/app/output` must contain only `/app/output/folds.jsonl` and `/app/output/summary.json`.
- The same program is also run with other `--cards` and `--spec` directories that are not this pack; only the worked-example 6-tuple changes across those runs.
