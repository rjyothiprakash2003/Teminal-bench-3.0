# Site log index: the ingest journal and phrase search

These notes are the authority for the program in `/app/logindex`. The
program keeps the site's log index the way the ingest service does: log
events arrive in batches, each batch is committed or rolled back as a whole,
and operators search the committed index with phrase queries that allow
some slop. The program reads one journal on standard input, replays it, and
writes the answer to every query it holds on standard output.

The rules are numbered R1 to R17 so that a review can cite them. Every
limit below is given at both ends and is inclusive.

## The journal

R1. The input. Standard input is a sequence of lines. Every line ends with
one LF, the last line included, and no line is empty. Apart from the LF, a
line holds only the printable ASCII characters from space (0x20) to tilde
(0x7E). The input holds from 1 to 400 lines.

R2. The stop-word line. The first line is the word `stopwords`, followed
by zero to 20 stop words, each preceded by exactly one space. A stop word
is 1 to 12 lowercase letters from `a` to `z`. No stop word is listed twice.
A first line of just `stopwords` means the journal has no stop words.

R3. The other lines. Every later line has one of these seven forms, with
exactly one space wherever one is shown:

```
begin
commit
rollback
add <id> <text>
append <id> <text>
delete <id>
query <slop> <fuzz> <text>
```

An `<id>` is 1 to 16 characters, each a lowercase letter `a` to `z`, a
digit, or a hyphen. A `<slop>` is a single digit from `0` to `9`, and a
`<fuzz>` is a single digit from `0` to `2`. A `<text>` is everything after
the single space that follows the id or the fuzz, up to the LF. It may hold any character R1 allows, spaces and
punctuation included, so it may begin or end with spaces. An add or append
text is 1 to 200 characters long and a query text is 1 to 120.

R4. Batches. `begin` opens a batch and `commit` or `rollback` closes it.
The `add`, `append` and `delete` lines stand only inside an open batch;
`begin` and `query` lines stand only outside one. Batches do not nest, a
batch may hold no lines at all, and the input never ends while a batch is
open.

R5. Applying a batch. When a batch opens, the program takes a working copy
of the committed index. The batch's lines are applied to that copy one by
one, in the order they appear, so a line sees the effect of every earlier
line of the same batch. `commit` makes the working copy the committed
index. `rollback` throws the working copy away and leaves the committed
index exactly as it was before `begin`. A query always reads the committed
index; before the first commit, that index holds no documents.

## Analysis

R6. Tokens. The tokens of a text are its maximal runs of ASCII letters and
digits, taken left to right. Every other character, the space and all
punctuation alike, only separates tokens and is never part of one. Each
token is lowercased, `A` to `Z` becoming `a` to `z`; digits are kept as
they are. For example, the text `Disk-FULL on /dev/sda1 (retry=3)` has the
tokens `disk`, `full`, `on`, `dev`, `sda1`, `retry` and `3`. A text may
hold no token at all, such as `-- ... --`.

R7. Positions. The tokens of a text are numbered 0, 1, 2 and so on in the
order they appear; a token's number is its position. A token that is equal
to a stop word of R2, after lowercasing, is a stop word. A stop word takes
a position like any other token, but it is not indexed (no query word ever
matches it).

## The documents

R8. Adding. `add <id> <text>` puts the document `<id>` into the working
copy, holding the tokens of `<text>` at their positions, and the id is then
live. If `<id>` is already live in the working copy, the old document is
replaced whole and nothing of its text survives: the new document's
positions start again from 0.

R9. Appending. `append <id> <text>` names an id that is live in the working
copy at that line. It adds the tokens of `<text>` to the end of that
document, after the tokens it already holds (the positions carry on from
the document's own, as if the two texts had been written as one, with a
space between them). A document may be appended to any number of times.

R10. Deleting. `delete <id>` removes the document `<id>` from the working
copy, so the id is no longer live and none of its tokens are indexed. A
later `add` of the same id starts a new document. A `delete` of an id that
is not live in the working copy changes nothing.

R11. Size of a document. At every point of the journal, a live document
holds from 0 to 300 tokens, stop words included, and the working copy holds
at most 100 live documents.

## Phrase search

R12. Query words. The words of `query <slop> <fuzz> <text>` are the tokens of its
text, found and lowercased by R6 and numbered by R7, with every stop word
dropped (the words that stay keep their positions). From 1 to 6 words stay.
A word may stand in a query more than once.

R13. Edit distance. The edit distance between two tokens is the least
number of edits that turn the one into the other. One edit inserts one
character, deletes one character, replaces one character with another, or
swaps two characters that stand side by side. The edits are made one after
another, each on the text that the one before it left. So `disk` and `disk`
are at distance 0, `disk` and `dusk` at 1, `disk` and `dsik` at 1, and
`disk` and `desks` at 2. The distance is the same read either way round.

R14. Placements. A placement of a query in a live document gives each word
of the query its own token of the document, so that no token serves two
words, and each token is within the query's fuzz of its word: its edit
distance from the word is at most the fuzz. With a fuzz of 0, every token
must equal its word. A word the query holds twice therefore takes two
tokens. A document that cannot give every word such a token has no
placement.

R15. Spread and distance. For a placement, take each word's offset: the
position of its token in the document minus the word's position in the
query. The spread of the placement is the largest offset minus the
smallest. The distance of a document for a query is the least spread of its
admissible placements (a placement that gives some word a token not equal
to it is admissible only where the document has no placement whose tokens
all equal their words). The edit count of the document is the least total,
over the words of the query, of each word's edit distance from its token,
among the admissible placements whose spread is the distance. For example,
the query `disk full` in the document `full disk`, at fuzz 0, has one
placement, with offsets -1 for `disk` and 1 for `full`, so its spread and
the distance are both 2: words found in the opposite order cost two. A
query of one word has distance 0 in every document where it has a
placement.

R16. Hits. A live document of the committed index is a hit of a query when
it has a placement and its distance is at most the query's slop. How the
least spread is found is left to the program; only the result is graded.

R17. Output. The program writes one line for each query, in the order the
queries appear in the input. The line lists the query's hits, each written
as `<id>@<distance>`, with a single space between hits. Hits are ordered
by distance, smallest first, then by edit count, smallest first, and hits
that tie on both by id in ascending byte order, so `db-2` comes before `db10`, and `db10` before
`db2`. A query with no hit writes the line `-`. Every line ends with one
LF and nothing else is written to standard output. The program exits with
status 0; what it writes to standard error is not read.

## The domain

Every journal the program is given meets R1 to R5 and R11, every query in
it keeps from 1 to 6 words under R12, and every `append` names a live id.
What the program does with any other input is not part of these notes.

## A short journal

The journal

```
stopwords on the
begin
add web-1 Disk full on the web host
add db-2 db host disk FULL
commit
query 0 0 disk full
query 2 0 full disk
begin
delete web-1
rollback
query 9 1 hosts
```

writes

```
db-2@0 web-1@0
db-2@2 web-1@2
db-2@0 web-1@0
```

In `web-1`, the tokens are `disk` 0, `full` 1, `on` 2, `the` 3, `web` 4 and
`host` 5, and `on` and `the` are not indexed. The rolled-back delete leaves
`web-1` live, so the last query finds both documents, where `host` is one
edit from the query's `hosts`.
