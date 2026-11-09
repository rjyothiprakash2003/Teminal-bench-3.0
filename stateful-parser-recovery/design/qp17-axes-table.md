# Axes table — stateful-parser-recovery round 17 (Prompt B)

## Axis 1 — Coherent Contracts
| Criterion | Mark | Evidence |
|-----------|------|----------|
| Rules ↔ page / domain / paths / examples / no over-strict | GREEN | Panel CC clean; fixtures enforce existing sentences |
| Over-strictness guard | GREEN | no new QUILL.md sentence |

## Axis 2 — Correct Reference Solution
| Criterion | Mark | Evidence |
|-----------|------|----------|
| Ordinary + boundary / bytes / state / recovery | GREEN | Panel CR clean; ref matches all new expects |

## Axis 3 — Protected Ground Truth
| Criterion | Mark | Evidence |
|-----------|------|----------|
| No agent-readable answers / independent expects | GREEN | panel clean |
| Held-out unreachable / neutral paths | GREEN→cured | s03b ≠ examples/simple bytes |
| Access boundary / byte pairs | GREEN | inherited |

## Axis 4 — Sound Verifier
| Criterion | Mark | Evidence |
|-----------|------|----------|
| Behaviour → assert / isolating / wrong rejected | GREEN→cured | r45–r48; r46/r47 |
| Contract-valid accepted | GREEN | spr-qp17-oracle3 Mean **1.000** (3/3) |
| Levers ≥2 sites | GREEN | r45a/b; r48a/b; s03a+s03b distinct |

## Axis 5 — Deterministic Execution
| Criterion | Mark | Evidence |
|-----------|------|----------|
| All DE criteria | GREEN | spr-qp17-oracle3 Mean **1.000** ×3; nop Mean **0.000** |

## Before → After
| Criterion | Returned | Now | Measurement |
|-----------|----------|-----|-------------|
| PGT s03b = example | demo/answer/42 | cell/n/7 | example replay ≠ s03b expect |
| SV identical-payload DUP | only 1→2 / x→y | r45a/b identical | idempotent skip → ok; ref dup@3 |
| SV U+1680 strip | absent | r46a | omit-1680 mutant form@1 |
| SV interior NEL | absent | r47a | splitlines → ok; ref form@1 |
| SV fault line@2000 | success-only s90a | r48a/b | 8-bit trunc → 208; ref 2000 |

## Container evidence
- `spr-qp17-oracle3` Mean **1.000** (3/3, Reward 1.0×3).
- `spr-qp17-nop` Mean **0.000** (Reward 0.0×1).
- Zip: `../stateful-parser-recovery.zip` rebuilt post-Harbor (excludes design/).
