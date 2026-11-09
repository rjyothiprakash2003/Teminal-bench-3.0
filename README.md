# Teminal-bench-3.0

A curated collection of **17 advanced Terminal-Bench–style tasks** for evaluating AI coding agents in realistic terminal environments.

These tasks follow the [Terminal-Bench](https://www.tbench.ai/) / Harbor task shape: a containerized workspace, a human-written instruction, a reference solution (oracle), and sealed verifiers that score **final outcomes** rather than command traces. Difficulty comes from long-horizon exploration, interacting constraints, and domain reasoning—not from ambiguity or missing inputs.

> **Note:** The repository name preserves the spelling `Teminal-bench-3.0` as published. The tasks themselves target Terminal-Bench 3.0–style agent evaluation.

---

## What’s inside

| Metric | Value |
| --- | --- |
| Tasks | 17 |
| Difficulty | Advanced (all) |
| Primary languages | Go, Python |
| Domains | Software systems, security, ML/eval, science, hardware/RTL, operations/finance, data engineering |
| Typical expert estimate | ~3–10 hours per task |

Each task is self-contained: an agent lands in a Docker environment, reads the instruction and contract docs, implements or repairs the deliverable, and is graded by a **separate verifier** against sealed holdouts the agent never sees.

---

## Task layout

Every task directory uses the same Harbor / Terminal-Bench shape:

```text
<task-name>/
├── instruction.md      # Agent-facing prompt (what to build / repair)
├── task.toml           # Metadata, timeouts, network/CPU/memory, artifacts
├── environment/        # Docker image + staged /app contents
├── solution/           # Reference (oracle) solution
├── tests/              # Verifier, sealed fixtures, reward harness
└── design/             # Optional authoring notes (some tasks)
```

### How grading works

1. **Agent phase** — The agent works in an isolated container (often with no network) under the timeouts in `task.toml`.
2. **Artifact handoff** — Declared artifacts (binaries, source trees, reports) are copied out of the agent environment.
3. **Verifier phase** — A separate container rebuilds and/or runs the submission against sealed corpora, compares byte-exact outputs / exit codes / dispositions, and emits a binary reward.
4. **Anti–reward-hack** — Sealed expects are loaded then removed from disk; reward paths stay inaccessible to candidate processes; many tasks rebuild from submitted sources under an unprivileged UID.

---

## Task catalog

### Operations / Finance

| Task | Language | Summary |
| --- | --- | --- |
| [`accrual-schedule-rebuild`](./accrual-schedule-rebuild) | Go | Build an accrual schedule generator (`schedbuild`) that recovers day-count bases from settled facilities, applies interacting day-count / month-end / rate-split / fractional-penny rules, and publishes schedules atomically with exact refusal precedence. |

### Security

| Task | Language | Summary |
| --- | --- | --- |
| [`brinewire-session-decoder`](./brinewire-session-decoder) | Python | Finish a Brinewire (BW-2026) capture decoder: TLV frames, seals, extended length, ACK/TERM session rebuild, nested ENCAP rejection, and deterministic `sessions.jsonl` / `summary.json` output. |
| [`capability-aware-secret-audit`](./capability-aware-secret-audit) | Go | Repair `casaudit` so effective secret reachability respects ancestor execute + leaf read across owner/group/other classes, contains symlink escapes, and proposes leaf-only remediation modes. |

### Software / Systems

| Task | Language | Summary |
| --- | --- | --- |
| [`cross-platform-build-orchestrator`](./cross-platform-build-orchestrator) | Python | Fix a cross-compile orchestrator: profile extras, `extends` expansion (including root `$delete` semantics), adjacent compaction, and `tc`/`notc` SHA-256 stamps. |
| [`deterministic-config-merger-cure`](./deterministic-config-merger-cure) | Python | Implement a layered config merger with priority/id ordering, deep-merge vs replace, `$delete` markers, parent/child inheritance, and strict JSON validation. |
| [`privilege-aware-file-sync`](./privilege-aware-file-sync) | Python | Harden a filesystem syncer: `lstat`-only discovery, symlink-as-leaf recreation, no chmod-through-links, unsafe destination intermediate skips, and sorted JSON reports. |
| [`restore-leasebox-journal-work`](./restore-leasebox-journal-work) | Go | Repair an exclusive-lease binary journal (`grant` / `revoke` / `list` / `compact` / `verify` / `report`) with CRC, capacity, duplicate refusal, and durable nonces. |
| [`sandbox-policy-evaluator`](./sandbox-policy-evaluator) | Go | Finish an SPE-2026 cordon policy engine: specificity ranking, equal-rank deny-over-allow, path/DNS inheritance, segment globs, and holdout packs. |
| [`secure-performance-optimized-ml-inference-pipeline`](./secure-performance-optimized-ml-inference-pipeline) | Go | Restore a Kelso ridgecast scoring sidecar + CI gate: intake schema, ridge-v3 logits, Bearer/Content-Type rules, cache isolation, and tick-bench ledger recovery. |
| [`stateful-parser-recovery`](./stateful-parser-recovery) | Python | Repair the Quill nest parser fault machine (FORM / DUP / UNBOUND / closer mismatch / EOF nest) and same-frame ALIAS resolution per `QUILL.md`. |
| [`transactional-cache-recovery`](./transactional-cache-recovery) | Python | Fix a WAL transactional cache: durable commits, version conflicts, idempotent `op_id` retries, crash recovery, and sorted report JSON. |

### Databases / Indexing / Storage

| Task | Language | Summary |
| --- | --- | --- |
| [`incremental-log-indexer-harden`](./incremental-log-indexer-harden) | Go | Build a journaled inverted indexer with commit/rollback batches, phrase search with slop, stop-word positions, and true Damerau–Levenshtein fuzz. |
| [`snapshot-range-compactor`](./snapshot-range-compactor) | Go | Repair an LSM/MVCC-style range compactor so pinned snapshots keep visible values, range tombstones don’t resurrect lower data, and merge operands stay correct across cuts. |

### Data engineering

| Task | Language | Summary |
| --- | --- | --- |
| [`streaming-window-aggregator`](./streaming-window-aggregator) | Python | Fix a hopping-window aggregator: arrival-order watermarks, half-open assignment, truncation vs bad-event taxonomy, and exact JSON bytes on success/refusal. |

### ML / Evaluation

| Task | Language | Summary |
| --- | --- | --- |
| [`helvellyn-scorecard`](./helvellyn-scorecard) | Python | Implement SMF-2026 scorecard folding: recover a unique AP/ECE 6-tuple from worked examples, apply live vs frozen modes, and emit weighted pack pools. |

### Science

| Task | Language | Summary |
| --- | --- | --- |
| [`pybamm-event-sensitivities`](./pybamm-event-sensitivities) | Python | Ship an importable PyBaMM package where event-time sensitivities (`phase_sensitivities` / `time_sensitivities`) stay correct across IDAKLU steps, holds, resumes, and copies. |

### Hardware / RTL

| Task | Language | Summary |
| --- | --- | --- |
| [`netlist-provenance-state-drift`](./netlist-provenance-state-drift) | Go | Repair a netlist provenance reconstructor (`netloom`) so journal epochs, liberty matrix rules, ghost recovers, area regress, and sealed attest/graph outputs match the ordinance. |

---

## Language & domain breakdown

```text
Go ........ accrual-schedule-rebuild, capability-aware-secret-audit,
            incremental-log-indexer-harden, netlist-provenance-state-drift,
            restore-leasebox-journal-work, sandbox-policy-evaluator,
            secure-performance-optimized-ml-inference-pipeline,
            snapshot-range-compactor

Python .... brinewire-session-decoder, cross-platform-build-orchestrator,
            deterministic-config-merger-cure, helvellyn-scorecard,
            privilege-aware-file-sync, pybamm-event-sensitivities,
            stateful-parser-recovery, streaming-window-aggregator,
            transactional-cache-recovery
```

---

## Working with a single task

Each task’s `instruction.md` is the agent brief. Contracts and notes usually live under the staged environment as `/app/docs/...` (or similar) once the image is built.

Typical local inspection:

```bash
# Read the agent instruction
less <task>/instruction.md

# Inspect metadata (timeouts, artifacts, difficulty)
less <task>/task.toml

# Peek at the oracle entrypoint
ls <task>/solution

# Peek at verifier / tests
ls <task>/tests
```

Running tasks end-to-end requires a Harbor / Terminal-Bench–compatible harness and the task Docker environments. Prefer the official [Harbor](https://github.com/harbor-framework/terminal-bench) tooling and the [tbench.ai](https://www.tbench.ai/) docs for oracle and agent trials.

---

## Design principles reflected in these tasks

- **Contract-first** — Behavior is defined by a desk note / ordinance / `*.md` contract, not by guessing from examples.
- **Byte-exact acceptance** — Successful outputs, refusal lines, key order, and exit codes are compared as specified.
- **Sealed holdouts** — Graded inputs are absent from the agent-visible tree; matching only the shipped examples is not enough.
- **Separate verifier** — Agent and grader containers are isolated to reduce reward hacking.
- **Realistic failure modes** — Symlink escapes, crash mid-write, out-of-order streams, nested policy specificity, MVCC visibility, and similar production-shaped traps.

---

## Repository map

```text
.
├── README.md
├── accrual-schedule-rebuild/
├── brinewire-session-decoder/
├── capability-aware-secret-audit/
├── cross-platform-build-orchestrator/
├── deterministic-config-merger-cure/
├── helvellyn-scorecard/
├── incremental-log-indexer-harden/
├── netlist-provenance-state-drift/
├── privilege-aware-file-sync/
├── pybamm-event-sensitivities/
├── restore-leasebox-journal-work/
├── sandbox-policy-evaluator/
├── secure-performance-optimized-ml-inference-pipeline/
├── snapshot-range-compactor/
├── stateful-parser-recovery/
├── streaming-window-aggregator/
└── transactional-cache-recovery/
```

---

## References

- [Terminal-Bench 3.0 announcement](https://www.tbench.ai/news/terminal-bench-3-0)
- [Terminal-Bench project site](https://www.tbench.ai/)
- [Harbor / Terminal-Bench harness](https://github.com/harbor-framework/terminal-bench)
- [Terminal-Bench authoring overview](https://deaimer.github.io/tb-docs/tracks/terminal-bench)

---

## License & attribution

Task metadata, instructions, environments, solutions, and verifiers are included as packaged for agent evaluation. Individual tasks may carry their own author fields in `task.toml`. Consult upstream Terminal-Bench / Harbor licensing before redistributing commercially.
