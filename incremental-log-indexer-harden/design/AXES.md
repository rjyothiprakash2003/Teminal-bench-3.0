# Five-axis table (companion section 7; marks of the prompts' section 6)

GREEN measured and holding · RED a defect · AMBER a bounded, declared risk · BLACK never measured.
Every figure is one a script printed on 2026-10-13 (design/CHAIN-LOG.txt, chain verdict GREEN).

## Axis 1: coherent_contract

| Criterion | Mark | Evidence |
|---|---|---|
| Every graded rule traced to a sentence | GREEN | 15 tests, each named for its rule; 66 witnesses each carry the reading it refuses; 53 mutants each fail a witness of the rule they break, 0 survivors |
| Input domain and boundaries specified | GREEN | R1 to R5, R11, R12 limits at both ends; domain sentence; inputs gate: 96 graded journals and 8 examples in domain, 10 of 10 planted out-of-domain journals refused |
| Instruction, artifacts and verifier paths agree | GREEN | artifacts `/app/logindex/`; verifier copies that path; instruction names it in its first sentence; farm oracle 16 of 16 |
| Examples support the rules | GREEN | reference prints all 8 example answers exactly; both cell victims print all 8 exactly (no example decides a cell) |
| No assertion stricter than the page | GREEN | over-strictness guard: reference control PASS, 5 tolerated trees (4 independently written programs, one stderr and unbuffered variant) all pass |
| Second readings of every rule | AMBER | the page-only builder named one doubt (R13), cured; bound: card 2's what-if showed that a careful reader can miss a dependency, so a panel judge could still find a reading no script tried |

## Axis 2: correct_reference_solution

| Criterion | Mark | Evidence |
|---|---|---|
| Ordinary and boundary cases traced through the reference | GREEN | reference and the independent model agree on 96 graded journals and on 800 fuzzed journals over seeds 101 and 202, 0 disagreements |
| Output bytes exact | GREEN | every comparison is whole stdout bytes; R17 format witnesses; distance printed per hit |
| Every state-changing branch | GREEN | add, replace, append, delete, delete of a missing id, commit, rollback, aliasing of a rolled-back append: witnesses under R5, R8, R9, R10, each with its mutant failing |
| Edit distance is the page's | GREEN | Damerau-Levenshtein recurrence equal to a breadth-first search over edit sequences on 3000 pairs (design/editcheck.py) |

## Axis 3: protected_ground_truth

| Criterion | Mark | Evidence |
|---|---|---|
| No answer the agent can read held by the verifier | GREEN | leak scan: 21 agent files, 65 verifier files, 191 graded strings, 0 identical pairs |
| Expected values computed independently | GREEN | every answer minted only where the reference and the page-only model agree |
| Held-out unreachable from the candidate | GREEN | /tests 0700 root, sealed files 0600; the autouse probe runs `cat` as uid 2000 on a sealed answer, the sealed directory and the reward file and fails closed; held-out loaded before the build |
| Access boundary tested from the candidate's context | GREEN | hostile tree (detached writer of reward.txt, reader of sealed answers) scored reward 0 |
| Neutral paths | GREEN | stdin and stdout are root-opened temporary files passed as descriptors; the run directory is an empty /sandbox/work/run |

## Axis 4: sound_verifier

| Criterion | Mark | Evidence |
|---|---|---|
| Every behaviour mapped to an assertion that fails without it | GREEN | 53 mutants, 0 survive their owner test |
| Full result compared | GREEN | exit status 0 and exact stdout for every witness and held-out journal |
| Isolating case per rule, not confounded | GREEN | each mutant fails a witness named for its own rule; each cell victim fails its two owner tests and no other test, held-out included |
| Plausible wrong solution rejected | GREEN | nop 0 of 16; lazy passes 12 of 16 and fails the 4 owner tests; hostile tree 0 of 16 |
| Valid alternatives accepted | GREEN | 5 tolerated trees pass |
| Levers graded at two or more sites | GREEN | each cell's victim fails two tests under two rules; cells per test at most 1 |

## Axis 5: deterministic_execution

| Criterion | Mark | Evidence |
|---|---|---|
| Fixed fixtures and stable seeds | GREEN | witnesses are literals; held-out minted once with seed 20261013 and sealed |
| Clock fixed where it matters | GREEN | no clock in the contract or the verifier |
| Dependencies pinned | GREEN | both images digest-pinned golang:1.24-bookworm; pytest and plugins hash-locked; GOTOOLCHAIN=local, GOPROXY=off |
| Order enforced only where required | GREEN | R17 fixes hit order and the reference sorts; Go map order never reaches output (8 programs agree) |
| Repeated grading stable | GREEN | oracle 1 on three farm runs; slowest graded reference run 0.008 s against a stop of 10 s |
| Platform runtime (gVisor, amd64) | BLACK | the local farm ran on arm64 Docker; Go crashes under amd64 emulation on this host, so the amd64 image was never exercised here; the GitHub farm was not run |

## What the task still lacks

- Difficulty: both cells screened 0 of 4 twice (SITTING.md). The task is built and clean,
  but no cell is measured to bite. The two trials (four Opus 5 runs, four GPT-5.6 runs
  through stb) have not been run; they are the author's.
- Before submission: the author's humanization (checklist line 10), the GitHub farm on a
  commit, and the zip with the rubric.
