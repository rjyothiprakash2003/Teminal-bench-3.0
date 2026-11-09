# Coverage sitting — pytest-json-ctrf isolation and read-only probe HOME

Package version: none. Last green before this return: oracle after the CITATIONS/docs rewrite (prior zip); this panel judged the timeout-raised package with verifier-global pytest-json-ctrf and a writable probe HOME. Line citations resolve on this tree.

No REVISION NOTES section and no extra quality-check FAIL list under the panel.

## Finding 1 — pytest-json-ctrf visible to submitted code

Disposition: MAKE IT TRUE. Verifier-only pins (`pytest==9.1.1`, `pytest-json-ctrf==0.5.2`) install with `--target=/tests/_verifier_python`. Candidate `requirements.txt` pins (including `pytest==8.4.1`) stay in site-packages. `tests/test.sh` puts `/tests/_verifier_python` on `PYTHONPATH` for pytest only. `/tests` is mode 700, so the uid-65534 probe cannot read that tree.

Files: `tests/Dockerfile`, `tests/test.sh`. Contract already required the candidate pinned set; no page edit.

Both sides (selected: `submitted_package or analytic_ode_event`):
- before: 2 passed (`pybamm-ctrf-r33`, 50.48 s). Candidate env: `PackageNotFoundError` for pytest-json-ctrf.
- after: 1 passed (package), 1 failed analytic `probe exited 1` (`pybamm-ctrf-after`, 14.76 s).
- global site-packages after rebuild: `pytest-json-ctrf` missing; under `/tests/_verifier_python`: `0.5.2`.

## Finding 2 — install / build under writable HOME

Disposition: MAKE IT TRUE. Probe `HOME` is a directory chmod'd `0555` after creating a writable `mpl/` for `MPLCONFIGDIR`. Result JSON is a pre-created file under the scratch base, outside HOME. Import-time `mkdir $HOME/generated` fails.

File: `tests/event_verify.py` `invoke()`. Contract already forbids new dependencies and build scripts; no page edit.

Both sides (same two tests):
- before: 2 passed (`pybamm-home-r33`, 53.25 s) with vendor copy into `$HOME/generated` from `__init__.py`.
- after: 1 passed (package), 1 failed analytic `probe exited 1` (`pybamm-home-after`, 3.60 s).

Overstrictness anchor: the page says do not install new dependencies or execute build scripts. Read-only HOME is that rule at the sandbox boundary named by the finding. Reference oracle still passes. A comment-only `__init__.py` change remains legal (null digest).

AMBER residual: `/tmp` (and other world-writable paths) are not sealed. The charged construction used HOME; a `/tmp`-targeted install is unmeasured this round.

## Axes

| Axis | Criterion | Mark | Evidence |
| --- | --- | --- | --- |
| 1 Coherent contract | Graded rule traced to sentence/artifact | GREEN | Delivery: candidate pinned packages; no new deps / build scripts. |
| 1 | Input domain / boundaries | GREEN | Unchanged this round. |
| 1 | Instruction, artifacts, verifier agree | GREEN | Instruction import path + 300 s; contract delivery; probe enforces. |
| 1 | Examples determine inferred rules | GREEN | Unchanged. |
| 1 | No assertion stricter than the page | GREEN | HOME 555 anchors the stated no-install/no-build rule. |
| 2 Correct reference | Ordinary + boundaries through reference | GREEN | Oracle r33: 20 passed, reward 1, 582.03 s. |
| 2 | Output bytes exact | GREEN | Same goldens; no remint. |
| 2 | Branches that update state | GREEN | Unchanged patch. |
| 2 | Recovery at interruptions | BLACK | Not re-run. |
| 3 Protected ground truth | No answer agent can read in verifier | GREEN | Prior chain; not re-opened. |
| 3 | Expected independent of submission | GREEN | Same. |
| 3 | Held-out unreachable | GREEN | Same. |
| 3 | Neutral paths | GREEN | Same. |
| 3 | Access boundary from candidate context | GREEN | Probe PYTHONPATH is only the submission symlink; ctrf under /tests mode 700. |
| 3 | Identical byte pairs declared | BLACK | Not re-enumerated. |
| 4 Sound verifier | Behaviour mapped to failing assertion | GREEN cured | ctrf import fails analytic; HOME mkdir fails analytic. |
| 4 | Full result compared | GREEN | Oracle 20/20. |
| 4 | Isolating case | GREEN | Each finding fails the analytic probe while package nonempty still passes. |
| 4 | Plausible wrong rejected | GREEN | Both adversarial trees fail ≥1 graded test after. |
| 4 | Contract-valid alternatives accepted | GREEN | Reference 20/20. |
| 4 | Lever at two or more sites | AMBER | Each new enforcement fails on every importing probe; measured on one named test pair this round. Bound: package-only still passes both mutations. |
| 5 Deterministic | Fixed fixtures | GREEN | Sealed JSON. |
| 5 | Clock fixed | GREEN | Unchanged. |
| 5 | Dependencies pinned | GREEN | Candidate pins in site-packages; verifier pins under /tests/_verifier_python. |
| 5 | Order where required | GREEN | Unchanged. |
| 5 | Repeated grading stable | GREEN | Oracle r33 reward 1; NOP r33 reward 0, 7.99 s. |

## Before / after

| Criterion | Returned | Now | Measurement |
| --- | --- | --- | --- |
| pytest-json-ctrf on probe path | Global install; mutation 2/2 | Target under /tests; mutation 1/2 fail analytic | before 2 passed; after analytic probe exited 1 |
| HOME install during import | Writable HOME; mutation 2/2 | HOME 0555; mutation 1/2 fail analytic | before 2 passed; after analytic probe exited 1 |

## Not covered

No rival sweep, no lazy run, no `/tmp`-targeted pip mutation. Instruction and the four `task.toml` explanation fields left alone (not falsified).
