# Sitting — companion §7 / Prompt A axes (2026-09-27)

## What I checked and found CLEAN
- Instruction first sentence names absolute `/app` goal; ≤300 words (110); no discriminator list of naive readings.
- Time limits the verifier enforces (180 / 120 / 8s) are stated in instruction.md.
- Independent model (`design/model.py`) reproduces mean lab, off-mean lab, week41 scores, and bench ledger from the page — not from the Go reference.
- Executable rival sweep (`design/rivals.py`) patches reference, rebuilds in Docker at `/app`, runs surface; **0 survivors**.
- Sentence × site for the 7 swept sentences: **0 gaps**.
- Over-strictness: control passes first; tolerated unexported rename passes.
- Leak scan: weight twins declared; **removed** sealed expects of agent-visible week41/bench inputs; verifier mints those from sealed weights + live fixtures.
- Inputs check: week41 rows legal; extra-key refusal tagged `extra`.

## What I found BROKEN / open
- ~13 further contract sentences lack executable rivals (AMBER sound-verifier backlog).
- Several rivals fail only one design-surface check (near-miss shape at the instrument layer; pytest may still multi-site).
- Harbor oracle / nop / lazy not executed this sitting → Axis 2 output bytes AMBER, Axis 5 repeat BLACK.
- `oracle_micros` in `ridge_helpers.py` duplicates the independent model arithmetic (copy risk); long-term cure is import from a shared sealed module baked into the verifier image.

## Disposition of previous design gaps
| Gap | Answer taken | Measurement |
|---|---|---|
| No design chain | MAKE IT TRUE — added `design/` | CHAIN VERDICT GREEN |
| Instruction handed discriminators | MAKE IT TRUE — rewritten | 110 words; rules on page |
| Undisclosed timeouts | MAKE IT TRUE — stated in instruction | 180/120/8 |
| Sealed expects of agent-visible inputs | MAKE IT TRUE — deleted goldens; mint at grade | leak_scan clean |
| Over-strictness unmeasured | MAKE IT TRUE — overstrict.py | 1/1 PASS |
