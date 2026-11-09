# AUDIT: incremental-log-indexer (kt_omen-v4, 2026-10-13)

Rebuilt from the September repair task (a Python indexer with CLI flags, an output file
and refusals; backup at ../_backup/incremental-log-indexer-pre-kt_omen4) to the shape of
KT-26 sections 59 and 62 to 64. Chain verdict GREEN after the last edit
(design/CHAIN-LOG.txt; rerun with `design/run_chain.sh <scratch>`).

VERDICT CHAIN GREEN
VERDICT FARM ORACLE 1 NOP 0 LAZY 0 (local arm64 Docker; GitHub farm not run)
VERDICT SCREEN 0 of 4 on every cell, two sittings (design/SITTING.md)
VERDICT TRIALS not run (the author's)

## The tick checklist (KT-26 62.10, as amended by 63.9 and 64)

| # | Line | Tick | Evidence or reason |
|---|---|---|---|
| 1 | Language Go, never Rust | ✔ | languages = ["go"]; stub and reference in Go, standard library only |
| 2 | Category Software | ✔ | task.toml |
| 3 | Subcategory | ✔ | Data engineering (no taxonomy collision checked by script; the forge's override field is not in this tree) |
| 4 | The idea: a real tool with habits, fenced | ✔ | Lucene/Elasticsearch-style ingest, analyzer and phrase search; not cron, semver, CSS cascade, secret shares, list-append, tar tables |
| 5 | The plan: the card | ✔ | design/CARD.md |
| 6 | Difficulty by section 64: cells, sitting, careful reader, twins, proofs | ✘ open | the chain proves both cells are built (victims fail only their two owner tests, print every example, lazy fails all four), but both cells read 0 of 4 at two sittings; by 64.1 step 4 that card is not to be built, and it was finished on the author's word |
| 6b | The two trials | ✘ open | not run; four Opus 5 runs on the author's API key and four GPT-5.6 runs through stb are the author's to start |
| 7 | Two or three topics of one tool, the cells at their joins | ✔ | journal, analysis, phrase search; cell 1 at R7 × R14, cell 2 at R13 × R16 |
| 8 | Quality panel, one line an axis | ✔ | see below and design/AXES.md |
| 9 | The farm ORACLE 1, NOP 0, LAZY 0 on the commit | ◐ | local farm green three times, hostile tree 0; the GitHub farm on a commit is not run (the task folder is not in a repository) |
| 10 | Humanization by the human | ✘ open | editing page https://claude.ai/artifact/PS6i1imWk6fUHW8yxs5EeF (private, saves to its db collection humanization); no human text has come back yet |
| 11 | The zip with the rubric beside it | ✘ open | waits for line 10; rubric drafted in design/rubrics.txt |

Line 8, one line an axis:
- coherent_contract: every rule read for a second reading by the page-only builder; one doubt (R13) cured; 5 tolerated trees pass; 0 assertions found stricter than the page.
- correct_reference_solution: the reference and the independent model agree on 96 graded journals and 800 fuzzed ones; 0 disagreements.
- protected_ground_truth: leak scan 0 identical pairs; sealed files root-only and loaded before the build; probe and hostile tree both hold.
- sound_verifier: 53 mutants, 0 survivors; every mutant caught by a witness of its own rule; levers at two sites.
- deterministic_execution: literal witnesses and a sealed held-out set; pinned images and locks; oracle stable over three runs.

## The promise count (62.6 g)

| Promise | Enforcing test |
|---|---|
| Instruction: write the program in /app/logindex, built with go build | every test (session build fixture) |
| Instruction: Go, standard library only | build with GOPROXY=off and no network; a foreign import fails the build and every test |
| Instruction: exit 0 and exact bytes; stderr not read | `check` compares stdout and status in every test; stderr variant tolerated |
| Instruction: at most 10 s a run | every run is stopped at 10 s and recorded as a failure |
| Instruction: journals meet the domain | inputs gate (chain) |
| R1, R2 | test_journal_lines_and_stop_word_line |
| R3 | test_line_fields_ids_slop_and_fuzz |
| R4, R5 | test_batches_commit_and_roll_back |
| R6 | test_tokens_split_on_punctuation_and_lowercase |
| R7 | test_stop_words_hold_positions_and_are_not_indexed |
| R8 | test_add_replaces_a_live_document |
| R9 | test_append_carries_positions_on |
| R10 | test_delete_ends_a_document |
| R11 | test_document_size_limits |
| R12 | test_query_words_keep_their_positions |
| R13 | test_edit_distance_is_the_least_number_of_edits |
| R14 | test_placements_take_distinct_tokens_within_the_fuzz |
| R15 | test_distance_is_the_least_spread |
| R16 | test_hits_lie_within_the_slop |
| R17 | test_output_order_and_format |
| all rules at scale | test_held_out_journals (30 sealed journals, 5 at 400 lines) |

## Before version 1 (62.6 a to i)

- a, nothing left open: every finding of this session is fixed or recorded above as open
  with its reason.
- b, sibling sweep: wrong readings as mutants across every field (id, slop, fuzz, text),
  every line kind, both ends of each range; 53 of 53 refused.
- c, the three lenses: settings varied (stop list empty and at 20, slop 0 and 9, fuzz 0
  to 2), bytes of every line kind, sizes at their limits (400 lines, 300 tokens, 100
  documents, 200- and 120-character texts, 16-character ids).
- d, range-end walk: each inclusive limit reached by a named witness (FOUR_HUNDRED_LINES,
  TWENTY_STOP_WORDS_WITH_A_TWELVE_LETTER_ONE, ID_OF_SIXTEEN_CHARACTERS,
  TEXT_OF_TWO_HUNDRED_CHARACTERS, SLOP_NINE_AND_FUZZ_TWO_AT_THEIR_ENDS,
  DOCUMENT_OF_THREE_HUNDRED_TOKENS, ONE_HUNDRED_LIVE_DOCUMENTS, SIX_WORDS_AFTER_DROPPING,
  DISTANCE_NINE_AT_SLOP_NINE).
- e, mutant matrix: design/mutants.py, 53 patches, each owned by its rule's test.
- f, two programs agree: reference and page-only model, 96 graded and 800 fuzzed journals.
- g, promise count: above.
- h, harness: section 20's execution contract, written afresh (uid 2000, descriptor
  stdin, group kill, root-only reward, probe as an autouse fixture).
- i, farm: local only, see line 9.

Not covered, and why: the platform's amd64 gVisor runtime (Go crashes under amd64
emulation on this Apple Silicon host); the GitHub farm (no repository); the trials.
