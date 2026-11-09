"""Source of the named witnesses. gen_tests.py mints each witness's answers where the
reference and the independent model agree and writes them, as literals, into
tests/test_outputs.py; the chain then reads the witnesses back from that file."""


def J(days, *lines):
    return "\n".join(("retain %d" % days,) + lines) + "\n"


TESTS = {}


def W(test, name, journal, refuses):
    TESTS.setdefault(test, []).append({"name": name, "journal": journal, "refuses": refuses})


L = "web1 nginx[201]: "
ALL = "2001-01-01T00:00:00 2097-12-31T23:59:59"
OCT = "2026-10-01T00:00:00 2026-10-31T00:00:00"


def six_hundred():
    lines = ["batch 2026-02-01T00:%02d:00" % m for m in range(59)]
    logs = ["Feb  1 00:%02d:%02d web1 app: tick" % (i // 60, i % 60) for i in range(499)]
    body = lines[:1] + logs[:250] + lines[1:59] + logs[250:] + ["Feb  1 00:59:59 web1 app: last"]
    body += ["count 2026-02-01T00:00:00 2026-02-01T23:59:59 tick"] * 39
    body.append("count 2026-02-01T00:00:00 2026-02-01T23:59:59 last")
    return J(3650, *body)


T = "test_journal_lines_and_retention_line"
W(T, "JOURNAL_WITHOUT_QUERIES_PRINTS_NOTHING", J(5, "batch 2026-10-13T12:00:00", "Oct 13 11:00:00 " + L + "up"),
  "a program that prints something when the journal holds no query")
W(T, "RETENTION_IS_COUNTED_IN_DAYS",
  J(2, "batch 2026-10-10T00:00:00", "Oct  8 00:00:00 " + L + "edge", "Oct  7 23:59:59 " + L + "edge",
    "count 2026-10-01T00:00:00 2026-10-31T00:00:00 edge"),
  "a retention read in hours, or ignored")
W(T, "RETENTION_OF_3650_DAYS",
  J(3650, "batch 2036-10-10T00:00:00", "Oct 10 00:00:00 " + L + "old", "batch 2046-10-07T00:00:00",
    "count %s old" % ALL, "batch 2046-10-08T00:00:01", "count %s old" % ALL),
  "a retention capped below its largest value")
W(T, "SIX_HUNDRED_LINES_ARE_ALL_READ", six_hundred(), "a reader that stops before the six hundredth line")

T = "test_batches_and_line_kinds"
W(T, "QUERY_BEFORE_ANY_BATCH_SEES_AN_EMPTY_INDEX",
  J(30, "count %s up" % ALL, "lag 2026-01-01T00:00:00 up", "batch 2026-01-01T00:00:00", "Jan  1 00:00:00 " + L + "up",
    "count %s up" % ALL),
  "a query that fails or invents lines before the first batch")
W(T, "EMPTY_BATCH_STILL_DROPS_OLD_LINES",
  J(1, "batch 2026-04-01T00:00:00", "Apr  1 00:00:00 " + L + "kept", "count 2026-03-01T00:00:00 2026-05-01T00:00:00 kept",
    "batch 2026-04-03T00:00:00", "count 2026-03-01T00:00:00 2026-05-01T00:00:00 kept"),
  "an empty batch that skips the retention sweep")
W(T, "A_BATCH_ADDS_TO_THE_INDEX",
  J(30, "batch 2026-04-01T00:00:00", "Apr  1 00:00:00 " + L + "one", "batch 2026-04-02T00:00:00",
    "Apr  2 00:00:00 " + L + "one", "count 2026-04-01T00:00:00 2026-04-02T00:00:00 one"),
  "a batch that replaces the index instead of adding to it")
W(T, "LINES_AFTER_A_NEW_BATCH_TAKE_ITS_INSTANT",
  J(30, "batch 2026-06-01T00:00:00", "Jun  1 00:00:00 " + L + "first", "count 2026-05-01T00:00:00 2026-07-01T00:00:00 first",
    "batch 2026-06-02T00:00:00", "Jun  1 23:00:00 " + L + "second", "lag 2026-06-02T00:00:00 second"),
  "log lines filed under the instant of an earlier batch")

T = "test_instants_and_calendar"
W(T, "INSTANT_IS_WRITTEN_BACK_WITH_SECONDS",
  J(30, "batch 2026-02-03T04:05:06", "Feb  3 04:05:01 " + L + "x", "lag 2026-02-03T04:05:06 x"),
  "an instant written without its seconds, its T or with a zone")
W(T, "SECONDS_ACROSS_A_LEAP_DAY",
  J(400, "batch 2028-03-01T00:00:00", "Feb 28 00:00:00 " + L + "span", "lag 2028-03-01T00:00:00 span"),
  "a calendar that gives February 28 days in a leap year")
W(T, "YEAR_2000_IS_A_LEAP_YEAR",
  J(3650, "batch 2001-03-01T00:00:00", "Feb 29 12:00:00 " + L + "leap", "lag 2001-03-01T00:00:00 leap"),
  "a leap rule that forgets the years that divide by 400")
W(T, "INSTANTS_COMPARED_TO_THE_SECOND",
  J(30, "batch 2026-09-09T09:09:09", "Sep  9 09:09:08 " + L + "s", "Sep  9 09:09:09 " + L + "s",
    "count 2026-09-09T09:09:09 2026-09-09T09:09:09 s", "count 2026-09-09T09:09:08 2026-09-09T09:09:08 s"),
  "instants compared at a coarser grain than the second")

T = "test_log_line_layout"
W(T, "SPACE_PADDED_DAY",
  J(30, "batch 2026-10-03T12:00:00", "Oct  3 11:00:00 " + L + "pad", "lag 2026-10-03T12:00:00 pad"),
  "a day read wrongly when it is padded with a space")
W(T, "TAG_WITHOUT_A_PROCESS_ID",
  J(30, "batch 2026-10-03T12:00:00", "Oct  3 11:00:00 db1 kernel: oops at 0x1f", "count %s oops" % OCT,
    "count %s kernel" % OCT),
  "a tag expected to carry a process id, or read as part of the message")
W(T, "MESSAGE_MAY_HOLD_COLONS_AND_BRACKETS",
  J(30, "batch 2026-10-03T12:00:00", "Oct  3 11:00:00 db1 app/worker-2[77]: retry: [3] of 5: ok",
    "count %s retry" % OCT, "count %s worker" % OCT, "count %s 77" % OCT, "count %s ok" % OCT),
  "a message cut at a later colon, or a tag read past its bracket")
W(T, "EMPTY_MESSAGE",
  J(30, "batch 2026-10-03T12:00:00", "Oct  3 11:00:00 db1 app: ", "Oct  3 11:00:01 db1 app: app", "lag 2026-10-03T12:00:00 app"),
  "a line with an empty message that is dropped or misread")
W(T, "HOST_AND_TAG_AT_THEIR_LONGEST",
  J(30, "batch 2026-10-03T12:00:00",
    "Oct  3 11:00:00 a1234567890.b1234567890-c12345678 tag_with/thirty-two-characters--[1234567]: fine",
    "count %s fine" % OCT),
  "a host or tag cut short below its limit")
W(T, "MESSAGE_OF_200_CHARACTERS",
  J(30, "batch 2026-10-03T12:00:00", "Oct  3 11:00:00 " + L + "x" * 194 + " tail", "count %s tail" % OCT),
  "a line buffer that drops the end of a long message")

T = "test_year_is_the_one_nearest_the_batch"
W(T, "DECEMBER_LINE_IN_A_JANUARY_BATCH",
  J(30, "batch 2027-01-01T00:00:05", "Dec 31 23:59:58 " + L + "turn", "Jan  1 00:00:01 " + L + "turn",
    "lag 2027-01-01T00:00:05 turn"),
  "the batch's own year given to every line")
W(T, "JANUARY_LINE_IN_A_DECEMBER_BATCH",
  J(30, "batch 2026-12-31T23:59:00", "Jan  1 00:00:30 " + L + "ahead", "lag 2026-12-31T23:59:00 ahead"),
  "a line never read in the year after its batch")
W(T, "EQUALLY_NEAR_TAKES_THE_EARLIER_YEAR",
  J(3650, "batch 2026-07-02T12:00:00", "Jan  1 00:00:00 " + L + "tie", "lag 2026-07-02T12:00:00 tie"),
  "a tie between two years given to the later one")
W(T, "FEBRUARY_29_IN_A_YEAR_WITHOUT_ONE",
  J(3650, "batch 2027-03-01T00:00:00", "Feb 29 10:00:00 " + L + "leap", "lag 2027-03-01T00:00:00 leap"),
  "a February 29 rolled into March 1 of the batch's year")
W(T, "FEBRUARY_29_TWO_YEARS_AWAY",
  J(3650, "batch 2030-06-01T00:00:00", "Feb 29 10:00:00 " + L + "leap", "lag 2030-06-01T00:00:00 leap"),
  "a search for the year that looks only one year either side")
W(T, "LINE_READ_AGAIN_UNDER_THE_QUERY_INSTANT",
  J(3650, "batch 2027-01-01T00:00:05", "Dec 31 23:59:58 " + L + "turn", "lag 2027-12-30T00:00:00 turn"),
  "a second lag that keeps the year the line was given when its batch came")

T = "test_words_of_a_message"
W(T, "WORDS_ARE_LOWERCASED_RUNS_OF_LETTERS_AND_DIGITS",
  J(30, "batch 2026-10-03T12:00:00", "Oct  3 11:00:00 " + L + "GET /api/v2_items?id=9 -> 500",
    "count %s get" % OCT, "count %s v2" % OCT, "count %s items" % OCT, "count %s 500" % OCT, "count %s id" % OCT),
  "words split on spaces alone, kept in their case, or joined by underscores")
W(T, "A_WORD_IS_NOT_PART_OF_A_WORD",
  J(30, "batch 2026-10-03T12:00:00", "Oct  3 11:00:00 " + L + "timeouts and retimed", "count %s time" % OCT,
    "count %s timeouts" % OCT),
  "a word found inside a longer word")
W(T, "HOST_AND_TAG_HOLD_NO_WORDS",
  J(30, "batch 2026-10-03T12:00:00", "Oct  3 11:00:00 cache cache[1]: miss", "count %s cache" % OCT,
    "count %s 1" % OCT, "count %s miss" % OCT),
  "the host or the tag searched as part of the message")
W(T, "A_LINE_COUNTS_ONCE_HOWEVER_OFTEN_THE_WORD_STANDS",
  J(30, "batch 2026-10-03T12:00:00", "Oct  3 11:00:00 " + L + "retry retry retry", "count %s retry" % OCT),
  "a line counted once for every time the word appears")

T = "test_retention_when_a_batch_is_received"
W(T, "LINE_EXACTLY_AT_THE_LIMIT_IS_KEPT",
  J(10, "batch 2026-05-20T10:00:00", "May 20 09:30:00 " + L + "w", "May 20 09:29:59 " + L + "w", "batch 2026-05-30T09:30:00",
    "count 2026-05-01T00:00:00 2026-05-31T00:00:00 w"),
  "a retention that drops a line exactly at the limit, or keeps one past it")
W(T, "OLD_LINE_OF_A_NEW_BATCH_IS_NEVER_FILED",
  J(1, "batch 2026-08-14T06:00:00", "Aug 13 05:59:59 " + L + "late", "Aug 13 06:00:00 " + L + "late",
    "count 2026-08-01T00:00:00 2026-08-31T23:59:59 late"),
  "a sweep that runs only before a batch's own lines are filed")
W(T, "FUTURE_LINE_IS_FILED_AND_KEPT",
  J(1, "batch 2026-08-14T06:00:00", "Aug 15 06:00:00 " + L + "early", "batch 2026-08-15T07:00:00",
    "count 2026-08-01T00:00:00 2026-08-31T23:59:59 early", "lag 2026-08-15T07:00:00 early"),
  "a line stamped after its batch that is refused or dropped")
W(T, "SWEEP_USES_THE_NEW_BATCH_INSTANT",
  J(2, "batch 2026-01-10T00:00:00", "Jan 10 00:00:00 " + L + "a", "batch 2026-01-11T00:00:00", "Jan 11 00:00:00 " + L + "a",
    "batch 2026-01-12T00:00:01", "count 2026-01-01T00:00:00 2026-01-31T00:00:00 a"),
  "a sweep measured from each line's own batch instead of the batch now received")

T = "test_count_query"
W(T, "COUNT_INCLUDES_BOTH_ENDS",
  J(30, "batch 2026-03-03T03:03:03", "Mar  3 01:00:00 " + L + "e", "Mar  3 02:00:00 " + L + "e", "Mar  3 03:00:00 " + L + "e",
    "count 2026-03-03T01:00:00 2026-03-03T03:00:00 e", "count 2026-03-03T01:00:01 2026-03-03T02:59:59 e"),
  "a count that leaves out one end of its range")
W(T, "COUNT_OF_A_WORD_NOWHERE_IS_ZERO",
  J(30, "batch 2026-03-03T03:03:03", "Mar  3 01:00:00 " + L + "e", "count 2026-03-03T00:00:00 2026-03-03T23:00:00 f"),
  "a count that writes a dash or nothing for zero")
W(T, "COUNT_USES_THE_YEAR_OF_THE_LINE",
  J(30, "batch 2027-01-01T00:00:05", "Dec 31 23:59:58 " + L + "y", "count 2027-12-31T00:00:00 2027-12-31T23:59:59 y",
    "count 2026-12-31T00:00:00 2026-12-31T23:59:59 y"),
  "a count that matches the stamp's month and day in any year")

T = "test_lag_of_a_line"
W(T, "LAG_RUNS_FROM_THE_LINE_TO_ITS_BATCH",
  J(30, "batch 2026-10-13T12:00:10", "Oct 13 11:59:58 " + L + "g", "lag 2026-10-13T12:00:10 g"),
  "a lag measured the wrong way round")
W(T, "LAG_IS_NEGATIVE_FOR_A_LINE_FROM_THE_FUTURE",
  J(30, "batch 2026-10-13T12:00:00", "Oct 13 12:00:05 " + L + "g", "lag 2026-10-13T12:00:00 g"),
  "a lag written without its sign, or clamped at zero")
W(T, "LAG_KEEPS_ITS_OWN_BATCH",
  J(30, "batch 2026-10-13T12:00:00", "Oct 13 11:00:00 " + L + "g", "batch 2026-10-14T12:00:00", "Oct 14 11:30:00 " + L + "g",
    "lag 2026-10-14T12:00:00 g"),
  "a lag measured to the latest batch instead of the line's own")
W(T, "LAG_OF_SEVERAL_DAYS",
  J(30, "batch 2026-10-13T12:00:00", "Oct  3 12:00:00 " + L + "g", "lag 2026-10-13T12:00:00 g"),
  "a lag that loses whole days")

T = "test_lag_query_and_the_second_lag"
W(T, "LINES_IN_ORDER_OF_INSTANT_THEN_FILING",
  J(30, "batch 2026-10-13T12:00:00", "Oct 13 11:00:02 " + L + "q b", "Oct 13 11:00:01 " + L + "q a",
    "Oct 13 11:00:02 " + L + "q c", "lag 2026-10-13T12:00:00 q"),
  "lines listed in filing order, or equal instants out of filing order")
W(T, "SECOND_LAG_UNDER_A_NEARBY_INSTANT",
  J(30, "batch 2026-10-13T12:00:00", "Oct 13 11:00:00 " + L + "n", "lag 2026-10-13T13:00:00 n", "lag 2026-10-13T10:00:00 n"),
  "a second lag measured from the batch instead of the query's instant")
W(T, "SECOND_LAG_WHEN_THE_QUERY_INSTANT_IS_HALF_A_YEAR_ON",
  J(3650, "batch 2027-07-10T13:00:00", "Jul 10 12:00:00 db1 pg: vacuum done", "lag 2028-03-01T00:00:00 vacuum",
    "lag 2027-07-10T14:00:00 vacuum"),
  "a second lag that keeps the year the line was given when its batch came")
W(T, "LAG_QUERY_WITH_NO_LINE_WRITES_A_DASH",
  J(30, "batch 2026-10-13T12:00:00", "Oct 13 11:00:00 " + L + "n", "lag 2026-10-13T13:00:00 m"),
  "a lag query without lines that writes nothing")

T = "test_answers_are_numbered_and_written_exactly"
W(T, "QUERIES_NUMBERED_ACROSS_BOTH_KINDS",
  J(30, "batch 2026-10-13T12:00:00", "Oct 13 11:00:00 " + L + "n", "count 2026-10-13T00:00:00 2026-10-13T23:59:59 n",
    "lag 2026-10-13T12:00:00 n", "count 2026-10-13T00:00:00 2026-10-13T23:59:59 m", "lag 2026-10-13T12:00:00 m"),
  "queries numbered separately for each kind, or from zero")
W(T, "ANSWERS_FOLLOW_THE_INDEX_AS_IT_STOOD",
  J(1, "batch 2026-10-13T12:00:00", "Oct 13 11:00:00 " + L + "n", "count 2026-10-13T00:00:00 2026-10-13T23:59:59 n",
    "batch 2026-10-15T12:00:00", "count 2026-10-13T00:00:00 2026-10-13T23:59:59 n"),
  "answers given for the final index instead of the index at the query")

T = "test_sizes_at_their_limits"
W(T, "INDEX_OF_500_LINES",
  J(3650, "batch 2026-02-02T00:00:00", *["Feb  1 %02d:%02d:00 web%d app: bulk" % (i // 60, i % 60, i % 3) for i in range(500)],
    "count 2026-02-01T00:00:00 2026-02-01T23:59:59 bulk", "count 2026-02-01T08:19:00 2026-02-01T08:19:00 bulk"),
  "an index that holds fewer than five hundred lines")
W(T, "FORTY_QUERIES",
  J(30, "batch 2026-10-13T12:00:00", "Oct 13 11:00:00 " + L + "n",
    *(["count 2026-10-13T00:00:00 2026-10-13T23:59:59 n"] * 39), "lag 2026-10-13T12:00:00 n"),
  "a program that stops answering before the fortieth query")
