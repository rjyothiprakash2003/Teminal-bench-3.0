`/app/decode.py` is an unfinished Brinewire session decoder. Make it decode every row of `/app/captures/manifest.jsonl` using BW-2026 from `/app/brinewire/BW_SPEC.md`. Sample sessions are in `/app/brinewire/worked_examples/`.

The program takes `--captures`, `--spec`, and `--out`, each a directory. Here that is `/app/captures` and `/app/brinewire`. Write `/app/output/sessions.jsonl` and `/app/output/summary.json`, and don't leave anything else in the `--out` directory. It needs to handle other capture packs the same way, not just `/app/captures`.
