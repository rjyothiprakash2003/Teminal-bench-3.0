# Site log index: syslog batches, years and lag

These notes are the authority for the program in `/app/logindex`. The
program keeps the site's syslog index the way the collector does. Hosts
send their log lines in batches; the collector stamps each batch with the
instant it received it, files the lines into the index, drops lines that
have grown too old, and answers the operators' queries. The program reads
one journal on standard input, replays it, and writes the answer to every
query on standard output.

The rules are numbered R1 to R16 so that a review can cite them. Every
limit below is given at both ends and is inclusive. All instants are in
UTC; there are no time zones and no leap seconds anywhere in these notes.

## The journal

R1. The input. Standard input is a sequence of lines. Every line ends with
one LF, the last line included, and no line is empty. Apart from the LF, a
line holds only the printable ASCII characters from space (0x20) to tilde
(0x7E). The input holds from 1 to 600 lines.

R2. The retention line. The first line is `retain <days>`, where `<days>`
is a whole number from 1 to 3650 written without leading zeros. It says
how long the index keeps a line (R10).

R3. The other lines. Every later line is one of four kinds:

```
batch <instant>
<a log line, as R6 lays it out>
count <instant> <instant> <word>
lag <instant> <word>
```

A `batch` line opens a batch, and the log lines that follow it, up to the
next `batch`, `count` or `lag` line, are that batch's lines. A batch may
hold no log lines. A log line always belongs to a batch: it never comes
before the first `batch` line, and never straight after a query line.

R4. Instants. An instant is written `YYYY-MM-DDTHH:MM:SS`: a four-digit
year, a two-digit month and day, the letter `T`, and a two-digit hour,
minute and second, so `2027-01-01T00:00:05`. The year of a written instant
is from 2001 to 2097, the date is a real date of that year, the hour is
from 00 to 23, and the minute and the second from 00 to 59. The instants of
the `batch` lines strictly increase from one batch to the next.

R5. Calendar. Years follow the Gregorian calendar: a year is a leap year
when it divides by 4, except that a year that divides by 100 is not one
unless it also divides by 400. February has 29 days in a leap year and 28
otherwise. Every day has 86400 seconds. The number of seconds from one
instant to another is the plain count of seconds between them, negative
when the second comes first.

## Log lines

R6. Layout. A log line is laid out as

```
<Mmm> <dd> <hh>:<mm>:<ss> <host> <tag>: <message>
```

`<Mmm>` is one of `Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec`.
`<dd>` is the day of the month in two characters: a space and then the
digit for days 1 to 9, two digits for days 10 to 31; so the third of
October is written `Oct  3`, with two spaces. `<hh>`, `<mm>` and `<ss>`
are two digits each, in the ranges R4 gives. `<host>` is 1 to 32
characters, each a lowercase letter, a digit, `.` or `-`. `<tag>` is 1 to
32 letters, digits, `_`, `/` or `-`, followed either by nothing or by a
process id in square brackets, 1 to 7 digits, as in `nginx[2041]`. The tag
ends with a colon and one space, and `<message>` is everything after that
space, 0 to 200 characters of R1.

R7. The month and day exist. The month and day of every log line are a
day that some year has: `Feb 29` may stand in a line, but `Feb 30` and
`Apr 31` never do.

R8. The year of a line. A log line's stamp has no year, so a line's instant
is not fixed by the line alone: it is settled by the stamp together with
the instant its batch was received. The line takes the year that puts its
instant nearest to the instant its batch was received, nearest meaning the
fewest seconds either way, where the instant is the stamp's month, day and
time in that year (a stamp of February 29 is always read in a leap year).
When two years put it equally near, the line takes the earlier of the two.
The year found may lie outside the range R4 gives written instants.

R9. Words. The words of a message are its maximal runs of ASCII letters and
digits, each lowercased. Every other character only separates words. The
host and the tag are not part of the message and hold no words.

## The index

R10. Receiving a batch. When a batch is received, the collector first
drops from the index every line whose instant is more than `<days>` days
(of 86400 seconds) before the batch's instant. Then it files the batch's
own lines, in order, except a line whose instant is itself more than
`<days>` days before the batch's instant, which is never filed. A line
exactly `<days>` days before is kept. A line whose instant is later than
the batch's instant is filed like any other.

R11. Counting. `count <from> <to> <word>` counts the lines in the index
whose instant lies from `<from>` to `<to>`, both ends included, and whose
message holds `<word>` as one of its words. `<word>` is 1 to 32 lowercase
letters and digits, and `<from>` is never later than `<to>`.

R12. Lag. The lag of a line is the number of seconds from the line's
instant to the instant its batch was received. A line stamped later than
its batch's instant has a negative lag.

R13. The lag query. `lag <instant> <word>` lists the lines in the index
whose message holds `<word>` (as R11 reads it), in the order of their
instants, and lines of equal instant in the order they were filed. For each
line it gives three things: the line's instant, the line's lag, and the lag
that R12 would give the line were its batch received at the query's
`<instant>`.

## Output

R14. The answers. The program writes the answers in the order of the
queries, numbering the queries 1, 2, 3 and so on through the input. A
`count` query writes one line: its number, a space, and the count. A `lag`
query writes one line for each line it lists: its number, a space, the
line's instant written as R4 writes instants, a space, the lag, a space,
and the second lag. Lags are written in decimal with a leading `-` when
negative and no sign otherwise. A `lag` query that lists no line writes its
number, a space and `-`. Every line ends with one LF and nothing else is
written to standard output. The program exits with status 0; what it writes
to standard error is not read.

R15. Sizes. A journal holds at most 500 log lines in all and at most 40
queries, and the index never holds more than 500 lines.

R16. The domain. Every journal the program is given meets R1 to R4, R6, R7,
the limits of R11 and R15. What the program does with any other input is
not part of these notes.

## A short journal

The journal

```
retain 30
batch 2027-01-01T00:00:05
Dec 31 23:59:58 web1 nginx[201]: upstream timed out
Jan  1 00:00:03 web1 nginx[201]: upstream recovered
count 2026-12-31T00:00:00 2026-12-31T23:59:59 upstream
lag 2027-01-01T00:10:00 upstream
```

writes

```
1 1
2 2026-12-31T23:59:58 7 602
2 2027-01-01T00:00:03 2 597
```

The first line's stamp is read in 2026, the year that puts it seven seconds
before its batch, and the second in 2027.
