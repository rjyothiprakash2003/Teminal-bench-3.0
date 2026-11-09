# Card: incremental-log-indexer (kt_omen-v4, KT-26 section 64)

- Pack: TerminalBench3-KT-kt_omen-v4-2026-10-12; companion of the same date.
- Language: Go (stub of one file, standard library only). Category Software,
  subcategory Data engineering.
- Real tool: a log search index in the Lucene and Elasticsearch family: batched ingest
  with commit and rollback, an analyzer with stop words and positions, and phrase
  queries with slop and fuzziness.
- Topics (three, of one tool): the ingest journal (R4, R5, R8 to R11); analysis (R6, R7,
  R12); phrase search (R13 to R16). Output R17.
- Interface: standard input to standard output, exit 0, standard error not read, no
  arguments, no refusals; the domain closed in one sentence ("What the program does with
  any other input is not part of these notes") and kept true by the inputs gate.
- Page: /app/docs/indexing-notes.md, 179 lines, 17 numbered rules, every limit at both
  ends. Examples: 8 plain sets under /app/examples, none decides a cell.
- Open method: the least spread over placements in which each query word takes its own
  token within the fuzz; the page states what is right and leaves the search open.
- Cells (both screened at 0 of 4, see SITTING.md, so carried as rules of the tool, not
  counted as difficulty):
  1. GLOSS on R7 "(no query word ever matches it)" at the join with R14's fuzzy
     placements. Natural wrong program: compares every stored token, stop words
     included. Smallest telling input: stop word `on`, document `disk on fire`, query
     `0 1 disk of fire`, right `-`, wrong a hit. Owner tests: R7 and R14.
  2. Open-method silent cell in R13: least number of edits with adjacent swaps, the true
     Damerau-Levenshtein distance. Natural wrong program: the textbook optimal string
     alignment recurrence. Smallest telling input: `ca` against `abc` at fuzz 2. Owner
     tests: R13 and R16.
- Promises: 17 page rules and 5 restrictive instruction sentences, each with its test
  (AUDIT.md, the promise count).
- Limits: run stop 10 s (reference's slowest graded run 0.008 s); build stop 300 s.
- versions_left: 8 (no version uploaded under this card).
- score: empty until the two trials and the platform return.
