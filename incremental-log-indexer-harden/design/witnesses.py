"""Source of the named witnesses. gen_tests.py mints each witness's answers where the
reference and the independent model agree and writes them, as literals, into
tests/test_outputs.py; the chain then reads the witnesses back from that file."""


def J(stops, *lines):
    head = "stopwords" + "".join(" " + s for s in stops.split())
    return "\n".join((head,) + lines) + "\n"


def batch(*ops):
    return ("begin",) + ops + ("commit",)


TESTS = {}


def W(test, name, journal, refuses):
    TESTS.setdefault(test, []).append({"name": name, "journal": journal, "refuses": refuses})


T = "test_journal_lines_and_stop_word_line"
W(T, "ONE_LINE_JOURNAL_PRINTS_NOTHING", J(""),
  "a program that prints a line when the journal holds no query")
W(T, "EMPTY_STOP_LIST_KEEPS_EVERY_WORD",
  J("", *batch("add n-1 disk on fire"), "query 0 0 on", "query 0 0 disk on fire"),
  "a built-in stop list used when the first line lists none")
W(T, "THE_WORD_STOPWORDS_IS_NOT_A_STOP_WORD",
  J("of", *batch("add s-1 stopwords list reloaded"), "query 0 0 stopwords", "query 0 0 stopwords list"),
  "the leading word of the first line read as a stop word")
W(T, "TWENTY_STOP_WORDS_WITH_A_TWELVE_LETTER_ONE",
  J("a an and are as at be by for from in is it of on or the to was acknowledged",
    *batch("add al-1 alert raised for disk"),
    "query 0 0 acknowledged alert", "query 0 0 alert acknowledged", "query 0 0 was disk"),
  "a stop list cut short before its twentieth word or its twelfth letter")
W(T, "FOUR_HUNDRED_LINES_ARE_ALL_READ",
  J("", "begin", *["add d-1 version %d rolled out" % i for i in range(395)], "commit",
    "query 0 0 version 0 rolled", "query 0 0 version 394 rolled"),
  "a reader that stops before the four hundredth line")

T = "test_line_fields_ids_slop_and_fuzz"
W(T, "ID_IS_NOT_PART_OF_THE_TEXT",
  J("", *batch("add web-1 disk full", "append web-1 again"), "query 0 0 web", "query 0 0 1", "query 0 0 full again"),
  "the id read as the first words of the text")
W(T, "SLOP_AND_FUZZ_ARE_NOT_QUERY_WORDS",
  J("", *batch("add m-1 disk 3 full 1 node"), "query 3 1 disk full", "query 1 0 full node"),
  "the slop or the fuzz read as words of the query")
W(T, "TEXT_MAY_BEGIN_AND_END_WITH_SPACES",
  J("", *batch("add sp-1    disk    full   "), "query 0 0   disk full  "),
  "a text that loses its tokens when it begins or ends with spaces")
W(T, "ID_OF_SIXTEEN_CHARACTERS",
  J("", *batch("add abcdefghij-12345 disk full", "add a disk"), "query 0 0 disk"),
  "an id cut short or rewritten in the output")
W(T, "TEXT_OF_TWO_HUNDRED_CHARACTERS",
  J("", *batch("add long-1 " + "x" * 191 + " tail end"), "query 0 0 tail end",
    "query 0 0 " + " " * 112 + "tail end"),
  "a line buffer that drops the end of a long text")
W(T, "SLOP_NINE_AND_FUZZ_TWO_AT_THEIR_ENDS",
  J("", *batch("add e-1 alpha one two three four five six seven eight nine beta"), "query 9 2 alpah bteaa",
    "query 8 2 alpah bteaa", "query 9 1 alpah bteaa"),
  "a slop or fuzz read with a smaller range than the notes give")

T = "test_batches_commit_and_roll_back"
W(T, "QUERY_BEFORE_ANY_COMMIT_FINDS_NOTHING",
  J("", "query 9 2 disk", "begin", "add d disk", "rollback", "query 9 2 disk"),
  "an index that holds documents before any commit")
W(T, "EMPTY_BATCH_KEEPS_THE_INDEX",
  J("", *batch("add d-1 disk full"), "begin", "commit", "query 0 0 disk full"),
  "a commit of an empty batch that clears the committed index")
W(T, "ROLLBACK_RESTORES_THE_COMMITTED_INDEX",
  J("", *batch("add a-1 disk full", "add a-2 node down"),
    "begin", "delete a-1", "add a-3 disk full", "add a-2 cpu hot", "rollback",
    "query 0 0 disk full", "query 0 0 node down", "query 0 0 cpu"),
  "a rollback that keeps some of the lines of its batch")
W(T, "ROLLED_BACK_APPEND_LEAVES_THE_DOCUMENT",
  J("", *batch("add r-1 disk"), "begin", "append r-1 full", "rollback",
    "query 0 0 disk full", "query 0 0 disk"),
  "a working copy that shares a document's tokens with the committed index")
W(T, "LINES_OF_A_BATCH_SEE_EARLIER_LINES",
  J("", "begin", "add b-1 disk", "append b-1 full", "delete b-2", "add b-2 node", "delete b-2", "commit",
    "query 0 0 disk full", "query 0 0 node"),
  "a batch whose lines are applied out of order or against the committed index")
W(T, "COMMITS_STACK_IN_ORDER",
  J("", *batch("add c-1 disk"), *batch("add c-2 disk"), "query 0 0 disk", *batch("delete c-1"), "query 0 0 disk"),
  "a commit that replaces earlier commits instead of building on them")

T = "test_tokens_split_on_punctuation_and_lowercase"
W(T, "PUNCTUATION_SEPARATES_TOKENS",
  J("", *batch("add p-1 error:disk_full@node[7]"), "query 0 0 disk full", "query 0 0 full node 7"),
  "a split on spaces alone, or on spaces and a few marks")
W(T, "UNDERSCORE_SEPARATES_TOKENS",
  J("", *batch("add u-1 worker_pool_3 stalled"), "query 0 0 pool 3 stalled", "query 0 0 worker_pool_3"),
  "word characters taken as letters, digits and underscore")
W(T, "LETTERS_ARE_LOWERCASED",
  J("", *batch("add l-1 FATAL Error In NodeAgent"), "query 0 0 fatal error", "query 0 0 IN nodeagent"),
  "tokens compared with their case kept")
W(T, "DIGITS_STAY_IN_TOKENS",
  J("", *batch("add g-1 sda1 failed at 07"), "query 0 0 sda1", "query 0 0 sda", "query 0 0 failed at 07"),
  "digits treated as separators or rewritten")
W(T, "TEXT_WITH_NO_TOKEN",
  J("", *batch("add z-1 -- ... --", "add z-2 disk"), "query 9 2 disk"),
  "a text with no token that gains one")

T = "test_stop_words_hold_positions_and_are_not_indexed"
W(T, "STOP_WORDS_HOLD_THEIR_POSITIONS",
  J("on the", *batch("add s-1 disk on the node"), "query 1 0 disk node", "query 2 0 disk node"),
  "stop words removed before the tokens are numbered")
W(T, "CAPITAL_STOP_WORD_IN_A_QUERY_IS_DROPPED",
  J("the", *batch("add c-1 the disk"), "query 0 0 THE disk", "query 0 0 The disk"),
  "a stop word recognised only when it is written in lowercase")
W(T, "STOP_TOKEN_IS_NEVER_A_FUZZY_MATCH",
  J("on the", *batch("add f-1 disk on fire", "add f-2 the cache"), "query 0 1 disk of fire", "query 0 1 tho"),
  "a fuzzy word allowed to land on a stop word of the document")

T = "test_add_replaces_a_live_document"
W(T, "ADD_OF_A_LIVE_ID_REPLACES_THE_TEXT",
  J("", *batch("add a-1 disk full"), *batch("add a-1 node down"), "query 0 0 disk", "query 0 0 node down"),
  "an add of a live id that keeps or extends the old text, or is ignored")
W(T, "REPLACE_IN_THE_SAME_BATCH",
  J("", *batch("add a-2 alpha beta", "add a-2 gamma"), "query 9 0 alpha", "query 0 0 gamma"),
  "a second add in one batch that loses to the first")
W(T, "REPLACED_DOCUMENT_STARTS_AGAIN_AT_POSITION_ZERO",
  J("", *batch("add a-3 alpha beta gamma delta", "add a-3 node", "append a-3 down"), "query 0 0 node down"),
  "a replaced document whose positions continue from the old text")

T = "test_append_carries_positions_on"
W(T, "APPENDED_TOKENS_FOLLOW_THE_DOCUMENT",
  J("", *batch("add p-1 disk full", "append p-1 on node 3"), "query 0 0 full on node", "query 2 0 disk node",
    "query 1 0 disk node"),
  "appended tokens numbered from zero again")
W(T, "APPEND_AFTER_A_TRAILING_STOP_WORD",
  J("the a", *batch("add t-1 restart the", "append t-1 worker pool"), "query 0 0 restart worker",
    "query 1 0 restart worker"),
  "appended tokens numbered after the last indexed token instead of the last token")
W(T, "APPEND_AFTER_AN_INNER_STOP_WORD",
  J("on", *batch("add t-2 job failed on node", "append t-2 retry"), "query 0 0 node retry",
    "query 3 0 job retry", "query 2 0 job retry"),
  "appended tokens numbered after the count of indexed tokens")
W(T, "APPEND_JOINS_THE_TEXTS_WITH_A_SPACE",
  J("", *batch("add j-1 dis", "append j-1 k"), "query 0 0 disk", "query 0 0 dis k"),
  "the two texts run together into one token")
W(T, "MANY_APPENDS_ACCUMULATE",
  J("", *batch("add m-1 one"), *batch("append m-1 two three"), *batch("append m-1 four"),
    "query 0 0 one two three four", "query 2 0 one four"),
  "an append that keeps only the latest text")
W(T, "APPEND_TO_A_DOCUMENT_WITH_NO_TOKEN",
  J("", *batch("add e-1 ---", "append e-1 disk full"), "query 0 0 disk full"),
  "an append that cannot start from an empty document")

T = "test_delete_ends_a_document"
W(T, "DELETE_REMOVES_THE_DOCUMENT",
  J("", *batch("add d-1 disk full", "add d-2 disk full"), *batch("delete d-1"), "query 0 0 disk full"),
  "a delete that is ignored or removes the wrong document")
W(T, "DELETE_OF_AN_ID_NOT_LIVE_CHANGES_NOTHING",
  J("", *batch("add d-3 disk"), *batch("delete d-9", "delete d-3", "delete d-3"), *batch("delete d-3"),
    "query 0 0 disk", *batch("add d-3 node"), "query 0 0 node"),
  "a delete of a missing id that fails or disturbs other documents")
W(T, "ADD_AFTER_DELETE_STARTS_A_NEW_DOCUMENT",
  J("", *batch("add d-4 disk full"), *batch("delete d-4", "add d-4 node", "append d-4 down"),
    "query 9 0 disk", "query 0 0 node down"),
  "a deleted document that comes back with its old tokens")

T = "test_document_size_limits"
W(T, "DOCUMENT_OF_THREE_HUNDRED_TOKENS",
  J("", *batch("add big-1 " + " ".join(["x"] * 100), "append big-1 " + " ".join(["x"] * 100),
               "append big-1 " + " ".join(["x"] * 98) + " q z"),
    "query 0 0 q z", "query 0 0 z"),
  "a document cut short below three hundred tokens")
W(T, "ONE_HUNDRED_LIVE_DOCUMENTS",
  J("", *batch(*["add n%02d shared word %d" % (i, i) for i in range(100)]), "query 0 0 shared word",
    "query 0 0 word 99"),
  "a working copy that holds fewer than one hundred documents")

T = "test_query_words_keep_their_positions"
W(T, "QUERY_WORDS_ARE_LOWERCASED",
  J("", *batch("add q-1 disk full"), "query 0 0 DISK Full"),
  "query words compared with their case kept")
W(T, "QUERY_STOP_WORDS_ARE_DROPPED",
  J("the", *batch("add q-2 disk full"), "query 0 0 the disk full", "query 0 0 disk full the"),
  "stop words kept as words of the query")
W(T, "INNER_STOP_WORD_OF_A_QUERY_KEEPS_ITS_PLACE",
  J("of the", *batch("add q-3 out of memory", "add q-4 out memory", "add q-5 restart the worker"),
    "query 0 0 out of memory", "query 1 0 out of memory", "query 0 0 restart the worker"),
  "query words renumbered after the stop words are dropped")
W(T, "REPEATED_QUERY_WORD_IS_KEPT",
  J("", *batch("add q-6 ping ping", "add q-7 ping"), "query 0 0 ping ping"),
  "a query whose repeated words are merged into one")
W(T, "SIX_WORDS_AFTER_DROPPING",
  J("to", *batch("add q-8 a b c d e f"), "query 0 0 a b c d e f", "query 0 0 a b c d e g",
    "query 0 0 to a b c d e g to"),
  "a query cut short below six words")

T = "test_edit_distance_is_the_least_number_of_edits"
W(T, "ONE_EDIT_OF_EACH_KIND",
  J("", *batch("add e-1 disk", "add e-2 disks", "add e-3 dsk", "add e-4 dusk", "add e-5 dsik", "add e-6 kids"),
    "query 0 1 disk", "query 0 0 disk"),
  "an edit kind left out, or a swap counted as two edits")
W(T, "TWO_EDITS_AT_FUZZ_TWO_AND_NOT_AT_ONE",
  J("", *batch("add e-7 desks", "add e-8 dk", "add e-9 sidk"), "query 0 2 disk", "query 0 1 disk"),
  "an edit distance that counts a pair of edits as one, or caps it wrongly")
W(T, "SWAP_THEN_INSERT_BETWEEN_IS_TWO_EDITS",
  J("", *batch("add t-1 cpu load high", "add t-2 api gateway up"), "query 0 2 cuop load", "query 0 2 aiep gateway"),
  "the row-by-row distance that never edits inside a swapped pair")
W(T, "DISTANCE_IS_THE_SAME_EITHER_WAY",
  J("", *batch("add t-3 ack timeout", "add t-4 akec timeout"), "query 0 2 akec timeout", "query 0 2 ack timeout"),
  "a distance that depends on which token comes first")

T = "test_placements_take_distinct_tokens_within_the_fuzz"
W(T, "DOCUMENT_MISSING_A_WORD_HAS_NO_PLACEMENT",
  J("", *batch("add m-1 disk full"), "query 9 0 disk node", "query 9 1 disk fill"),
  "a document that matches with some words missing")
W(T, "REPEATED_WORD_NEEDS_TWO_TOKENS",
  J("", *batch("add r-1 timeout timeout", "add r-2 timeout"), "query 1 0 timeout timeout"),
  "one token allowed to serve a repeated word twice")
W(T, "TWO_FUZZY_WORDS_CANNOT_SHARE_A_TOKEN",
  J("", *batch("add r-3 disk", "add r-4 disk dusk"), "query 2 1 disk dusk", "query 2 1 dusk disk"),
  "one token allowed to serve two different words that it is close to")
W(T, "STOP_TOKEN_CANNOT_SHORTEN_A_PLACEMENT",
  J("at", *batch("add a-1 error at node as"), "query 2 1 error as node", "query 1 1 error as node"),
  "a stop word of the document used as the token of a close query word")

T = "test_distance_is_the_least_spread"
W(T, "REVERSED_ORDER_COSTS_TWO",
  J("", *batch("add o-1 down link"), "query 2 0 link down", "query 1 0 link down"),
  "a spread that ignores the order of the words")
W(T, "NEAREST_TOKEN_IS_NOT_ALWAYS_THE_LEAST_SPREAD",
  J("", *batch("add g-1 edge node pool sync miss cache hit rate low miss storm"), "query 9 0 cache miss storm"),
  "each word's token chosen nearest to the first word's")
W(T, "LEAST_SPREAD_OVER_EVERY_OCCURRENCE",
  J("", *batch("add g-2 disk one two three four five full x disk full"), "query 9 0 disk full"),
  "the spread taken from the first occurrence of each word")
W(T, "REPEATED_WORD_SPREAD_USES_TWO_TOKENS",
  J("", *batch("add g-3 failed retry x y z retry"), "query 9 0 retry failed retry"),
  "a repeated word allowed to reuse one token while finding the spread")
W(T, "ONE_WORD_QUERY_HAS_DISTANCE_ZERO",
  J("", *batch("add g-4 a b c d e f g node"), "query 0 0 node", "query 0 1 nodes"),
  "a one-word query given its position as a distance")
W(T, "FUZZY_TOKEN_CAN_GIVE_THE_LEAST_SPREAD",
  J("", *batch("add g-5 disk x x x full disc full"), "query 9 1 disk full", "query 9 0 disk full"),
  "the spread taken from exact tokens only when the fuzz allows near ones")

T = "test_hits_lie_within_the_slop"
W(T, "DISTANCE_EQUAL_TO_THE_SLOP_IS_A_HIT",
  J("", *batch("add h-1 disk x full"), "query 1 0 disk full", "query 0 0 disk full"),
  "a hit only when the distance is below the slop")
W(T, "DISTANCE_NINE_AT_SLOP_NINE",
  J("", *batch("add h-2 alpha a b c d e f g h i beta"), "query 9 0 alpha beta", "query 8 0 alpha beta"),
  "a slop that is ignored or capped")
W(T, "NEAREST_TOKEN_MISSES_A_HIT",
  J("", *batch("add h-3 w x y full queue z u v full alert"), "query 3 0 queue full alert",
    "query 2 0 queue full alert"),
  "each word's token chosen nearest to the first word's")
W(T, "SWAP_THEN_INSERT_FINDS_A_HIT",
  J("", *batch("add h-4 auth failed for user", "add h-5 cpu spike"), "query 0 2 auhat failed", "query 0 2 cuap spike",
    "query 0 1 cuap spike"),
  "the row-by-row distance that never edits inside a swapped pair")

T = "test_output_order_and_format"
W(T, "TIES_GO_BY_ID_IN_BYTE_ORDER",
  J("", *batch("add app2 disk", "add app10 disk", "add app-1 disk", "add app disk", "add b disk"), "query 0 0 disk"),
  "ties broken by insertion order, number value or descending ids")
W(T, "DISTANCE_COMES_BEFORE_ID",
  J("", *batch("add a disk x x full", "add b disk x full", "add c disk full"), "query 9 0 disk full"),
  "hits ordered by id alone")
W(T, "NO_HIT_WRITES_A_DASH",
  J("", *batch("add n-1 disk"), "query 0 0 node", "query 0 0 disk", "query 0 0 cpu"),
  "an empty line or no line for a query without hits")
W(T, "QUERIES_ANSWER_IN_INPUT_ORDER",
  J("", "query 0 0 disk", *batch("add o-1 disk"), "query 0 0 disk", *batch("delete o-1"), "query 0 0 disk",
    *batch("add o-2 disk", "add o-1 disk"), "query 0 0 disk"),
  "answers printed out of order or only for the final index")


PUNCT = "".join(chr(c) for c in range(33, 127) if not chr(c).isalnum())
W("test_tokens_split_on_punctuation_and_lowercase", "EVERY_PRINTABLE_MARK_SEPARATES_TOKENS",
  J("", *batch("add all-1 x0" + "".join(p + "x%d" % (i + 1) for i, p in enumerate(PUNCT))),
    *["query 0 0 x%d%sx%d" % (i, p, i + 1) for i, p in enumerate(PUNCT)]),
  "a tokenizer that splits on a chosen list of marks and keeps any other mark inside a token")
W("test_tokens_split_on_punctuation_and_lowercase", "APOSTROPHE_SEPARATES_TOKENS",
  J("", *batch("add ap-1 disk'full: don't retry"), "query 0 0 disk full", "query 0 0 don t retry",
    "query 0 0 don't"),
  "a word pattern that keeps apostrophes inside tokens")
W("test_append_carries_positions_on", "APPEND_WITH_NO_TOKEN_TAKES_NO_POSITION",
  J("", *batch("add tl-1 disk", "append tl-1 ---", "append tl-1      ", "append tl-1 full"), "query 0 0 disk full"),
  "an append that reserves a position even when its text holds no token")
W("test_append_carries_positions_on", "MORE_THAN_THREE_HUNDRED_APPENDS",
  J("", "begin", "add many-1 disk", *(["append many-1 ---"] * 300), "append many-1 full", "commit",
    "query 0 0 disk full"),
  "a document that stops taking appends after a fixed number of them")
W("test_line_fields_ids_slop_and_fuzz", "APPEND_TEXT_OF_TWO_HUNDRED_CHARACTERS",
  J("", *batch("add at-1 x", "append at-1 " + " " * 199 + "z"), "query 0 0 z", "query 0 0 x z"),
  "an append text cut short below its two hundred characters")
W("test_line_fields_ids_slop_and_fuzz", "IDS_MAY_START_WITH_A_DIGIT_OR_A_HYPHEN",
  J("", *batch("add - disk", "add 9 disk", "add 0-a disk", "add z disk"), "query 0 0 disk"),
  "an id that is expected to start with a letter")
W("test_edit_distance_is_the_least_number_of_edits", "LONG_TOKENS_ARE_COMPARED_IN_FULL",
  J("", *batch("add lt-1 " + "a" * 33, "add lt-2 " + "b" * 100 + "cd"),
    "query 0 1 " + "a" * 33, "query 0 0 " + "a" * 33, "query 0 1 " + "a" * 32 + "x",
    "query 0 1 " + "a" * 31, "query 0 1 " + "b" * 100 + "dc", "query 0 2 " + "b" * 99 + "dce"),
  "a fuzzy comparison that gives up on tokens longer than a fixed size")
W("test_document_size_limits", "MORE_THAN_ONE_HUNDRED_IDS_OVER_TIME",
  J("", "begin", *[l for i in range(150) for l in ("add id%03d word" % i,) + (("delete id%03d" % i,) if i < 147 else ())],
    "commit", "query 0 0 word"),
  "an index that refuses new ids once a fixed number have ever been used")
W("test_batches_commit_and_roll_back", "EMPTY_BATCH_ROLLED_BACK",
  J("", *batch("add e-1 disk"), "begin", "rollback", "query 0 0 disk"),
  "a rollback of an empty batch that clears or breaks the committed index")
W("test_tokens_split_on_punctuation_and_lowercase", "LONG_TOKENS_ARE_KEPT_WHOLE",
  J("", *batch("add lw-1 " + "a" * 70 + "x", "add lw-2 " + "a" * 70 + "y"), "query 0 0 " + "a" * 70 + "x"),
  "tokens cut to a fixed length, so two long tokens that differ late become equal")
W("test_query_words_keep_their_positions", "MANY_STOP_WORDS_BEFORE_THE_WORDS",
  J("the a of", *batch("add sw-1 disk x full"), "query 0 0 the a of the a of the a of disk the full",
    "query 1 0 the a of the a of the a of disk full"),
  "a query read only up to a fixed number of tokens before its stop words are dropped")
W("test_edit_distance_is_the_least_number_of_edits", "SHORT_WORDS_ARE_FUZZY_TOO",
  J("", *batch("add sh-1 ab", "add sh-2 x", "add sh-3 abcd"), "query 0 1 a", "query 0 2 ab"),
  "a fuzzy match refused for words no longer than the fuzz")
W("test_placements_take_distinct_tokens_within_the_fuzz", "OCCURRENCE_BEYOND_THE_HUNDREDTH",
  J("", *batch("add oc-1 " + " ".join(["a"] * 100), "append oc-1 " + " ".join(["a"] * 50) + " b"), "query 0 0 a b"),
  "candidate tokens of a word capped at a fixed count")
W("test_tokens_split_on_punctuation_and_lowercase", "TOKEN_LONGER_THAN_A_QUERY_TEXT_IS_KEPT_WHOLE",
  J("", *batch("add tw-1 " + "a" * 120 + "b", "add tw-2 " + "a" * 200), "query 0 0 " + "a" * 120,
    "query 0 1 " + "a" * 120, "query 0 2 " + "a" * 120),
  "tokens cut to the length of the longest query text")
W("test_line_fields_ids_slop_and_fuzz", "LONGEST_ADD_AND_APPEND_LINES",
  J("", *batch("add abcdefghij-12345 " + "x" * 191 + " tail end", "append abcdefghij-12345 " + "y" * 194 + " final"),
    "query 0 0 tail end", "query 0 0 final", "query 1 0 end final"),
  "a line buffer shorter than the longest add or append line")
LONG_STOPS = " ".join("".join(chr(97 + (i * 7 + k) % 26) for k in range(12)) for i in range(20))
W("test_journal_lines_and_stop_word_line", "LONGEST_STOP_WORD_LINE",
  J(LONG_STOPS, *batch("add ls-1 disk full"), "query 0 0 " + LONG_STOPS.split()[-1] + " disk full",
    "query 0 0 disk " + LONG_STOPS.split()[0] + " full"),
  "a first line read only up to a fixed length, losing its last stop words")
W("test_document_size_limits", "PHRASES_ACROSS_POSITIONS_128_AND_256",
  J("", *batch("add wr-1 " + " ".join(["x"] * 100), "append wr-1 " + " ".join(["x"] * 27) + " a b",
               "append wr-1 " + " ".join(["x"] * 100), "append wr-1 " + " ".join(["x"] * 26) + " c d"),
    "query 0 0 a b", "query 0 0 c d", "query 0 0 b x", "query 0 0 x c"),
  "positions kept in a small field that wraps at 128 or 256")
W("test_distance_is_the_least_spread", "SPREAD_ACROSS_OFFSETS_128_AND_256",
  J("", *batch("add so-1 " + " ".join(["x"] * 100), "append so-1 " + " ".join(["x"] * 27) + " a b",
               "append so-1 " + " ".join(["x"] * 100), "append so-1 " + " ".join(["x"] * 26) + " c d"),
    "query 2 0 b a", "query 1 0 b a", "query 2 0 d c"),
  "offsets kept in a small signed or unsigned field that wraps")
W("test_tokens_split_on_punctuation_and_lowercase", "EVERY_LETTER_AND_DIGIT_AT_BOTH_ENDS_OF_ITS_RANGE",
  J("", *batch("add up-1 ABCDEFGHIJKLMNOPQRSTUVWXYZ", "add lo-1 abcdefghijklmnopqrstuvwxyz", "add dg-1 0123456789",
               "add c-a A", "add c-z Z", "add l-a a", "add l-z z", "add d-0 0", "add d-9 9", "add mix-1 zA9 Az0"),
    "query 0 0 abcdefghijklmnopqrstuvwxyz", "query 0 0 ABCDEFGHIJKLMNOPQRSTUVWXYZ", "query 0 0 0123456789",
    "query 0 0 z", "query 0 0 A", "query 0 0 Z", "query 0 0 a", "query 0 0 0", "query 0 0 9", "query 0 0 za9 az0"),
  "a letter or digit range that stops one short of A, Z, a, z, 0 or 9, in reading or in lowercasing")


def ids_over_time(n):
    ops = []
    for i in range(n):
        ops.append("add id%03d word" % i)
        if i < n - 3:
            ops.append("delete id%03d" % i)
    return ops


W("test_placements_take_distinct_tokens_within_the_fuzz", "THE_LAST_OF_299_OCCURRENCES",
  J("", *batch("add mo-1 " + " ".join(["a"] * 100), "append mo-1 " + " ".join(["a"] * 100),
               "append mo-1 " + " ".join(["a"] * 99) + " b"),
    "query 0 0 a b", "query 0 0 a a a a a b", "query 1 0 b a", "query 2 0 b a"),
  "candidate tokens of a word kept only up to a fixed count below the document size")
W("test_query_words_keep_their_positions", "WORDS_AFTER_FIFTY_FOUR_STOP_TOKENS",
  J("a", *batch("add qs-1 disk full"), "query 0 0 " + "a " * 54 + "disk full", "query 0 0 " + "a " * 53 + "disk a full",
    "query 1 0 " + "a " * 53 + "disk a full"),
  "a query read only up to a fixed number of tokens")
W("test_output_order_and_format", "THREE_HUNDRED_NINETY_FIVE_QUERIES",
  J("", *batch("add oa-1 alpha", "add ob-1 beta"), *["query 0 0 %s" % ("alpha", "beta", "gamma")[i % 3] for i in range(395)]),
  "a program that stops answering after a fixed number of queries")
W("test_batches_commit_and_roll_back", "ONE_HUNDRED_THIRTY_TWO_BATCHES",
  J("", *[l for i in range(132) for l in ("begin", "add bt-1 word%d" % i, "commit")], "query 0 0 word131", "query 0 0 word130"),
  "a program that stops taking commits after a fixed number of batches")
W("test_document_size_limits", "ONE_HUNDRED_NINETY_SIX_IDS_OVER_TIME",
  J("", "begin", *ids_over_time(196), "commit", "query 0 0 word"),
  "an index that refuses new ids once a fixed number have ever been used")
W("test_append_carries_positions_on", "THREE_HUNDRED_NINETY_THREE_APPENDS",
  J("", "begin", "add ma-1 disk", *(["append ma-1 ---"] * 393), "append ma-1 full", "commit", "query 0 0 disk full"),
  "a document that stops taking appends after a fixed number of them")
W("test_document_size_limits", "ONE_HUNDRED_FULL_DOCUMENTS",
  J("", "begin", *[l for i in range(100) for l in (
      "add f%02d " % i + " ".join(["x"] * 100), "append f%02d " % i + " ".join(["x"] * 100),
      "append f%02d " % i + " ".join(["x"] * 98) + (" q z" if i == 99 else " x x"))], "commit", "query 0 0 q z", "query 0 0 z"),
  "an index that stops filing tokens beyond a fixed total")
