Write an accrual schedule builder program at `/app/tools/schedbuild`. The
directory already holds `go.mod` and a handover note.

- `/app/docs/servicing-note.txt` is the authority for the schedule and every
  refusal the desk will see.
- `/app/docs/desk-procedure.txt` covers how the desk runs the builder.
- The basis table is at `/app/data/bases.tbl`; the builder is handed a copy
  rather than that path.
- Where the note says the settled facilities carry a rule, recover that rule
  from the schedules under `/app/settled`.
- Grading copies `/app/tools/schedbuild/go.mod` and the regular `.go` files
  directly in that directory, nothing else, into an empty tree and runs
  `CGO_ENABLED=0 go build -o schedbuild .` with no network, so the standard
  library is all you get. It is then run on facility files the settled folders
  never show, with what it writes compared byte for byte and what it refuses
  compared against the note.
