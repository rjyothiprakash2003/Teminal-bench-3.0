"""The mutant matrix: every rule-bearing sentence of the notes has an exact patch on the
reference (solution/main.go) that breaks it, owned by the test of that rule. A patch whose
anchor is missing or not unique stops the sweep (apply raises). VICTIMS are the natural
wrong programs of the cells: the counted what-if cell, and the February 29 search, which
the early sitting dropped as a cell and which stays a rule of the tool; LAZY takes the
wrong answer in both at once."""

R1 = "test_journal_lines_and_retention_line"
R3 = "test_batches_and_line_kinds"
R5 = "test_instants_and_calendar"
R6 = "test_log_line_layout"
R8 = "test_year_is_the_one_nearest_the_batch"
R9 = "test_words_of_a_message"
R10 = "test_retention_when_a_batch_is_received"
R11 = "test_count_query"
R12 = "test_lag_of_a_line"
R13 = "test_lag_query_and_the_second_lag"
R14 = "test_answers_are_numbered_and_written_exactly"
R15 = "test_sizes_at_their_limits"

KEEP_YEAR = ("\t\t\t\tagain := infer(e.st, at)", "\t\t\t\tagain := e.instant")
ONE_YEAR_EITHER_SIDE = ("\tfor y := base - 8; y <= base+8; y++ {\n\t\tif st.day > daysIn(st.month, y) {\n\t\t\tcontinue\n\t\t}",
                        "\tfor y := base - 1; y <= base+1; y++ {")

VICTIMS = {
    "victim_second_lag_keeps_the_year": ([R8, R13], [KEEP_YEAR]),
}
RULE_VICTIMS = {
    "victim_february_29_one_year_either_side": ([R8], [ONE_YEAR_EITHER_SIDE]),
}
LAZY = [KEEP_YEAR, ONE_YEAR_EITHER_SIDE]

MUTANTS = {
    "r1_reads_at_most_599_lines": (R1, [("\tw := bufio.NewWriter(out)", "\tif len(lines) > 599 {\n\t\tlines = lines[:599]\n\t}\n\tw := bufio.NewWriter(out)")]),
    "r1_prints_a_line_without_queries": (R1, [("\tquery := 0\n", "\tquery := 0\n\tw.WriteString(\"\\n\")\n")]),
    "r2_retention_in_hours": (R1, [("\tkeep := days * 86400", "\tkeep := days * 3600")]),
    "r2_retention_capped_at_3600_days": (R1, [("\tkeep := days * 86400", "\tif days > 3600 {\n\t\tdays = 3600\n\t}\n\tkeep := days * 86400")]),
    "r3_batch_replaces_the_index": (R3, [("\t\t\tkept := index[:0]", "\t\t\tindex = nil\n\t\t\tkept := index[:0]")]),
    "r3_empty_batch_skips_the_sweep": (R3, [("\t\t\treceived = parseInstant(fields[1])\n", "\t\t\treceived = parseInstant(fields[1])\n\t\t\tcontinue\n"),
                                            ("\t\t\tinst := infer(st, received)\n", "\t\t\tkept := index[:0]\n\t\t\tfor _, e := range index {\n\t\t\t\tif received-e.instant <= keep {\n\t\t\t\t\tkept = append(kept, e)\n\t\t\t\t}\n\t\t\t}\n\t\t\tindex = kept\n\t\t\tinst := infer(st, received)\n")]),
    "r3_lines_keep_the_first_batch_instant": (R3, [("\t\t\treceived = parseInstant(fields[1])\n", "\t\t\tif received == 0 {\n\t\t\t\treceived = parseInstant(fields[1])\n\t\t\t}\n")]),
    "r4_instant_written_without_seconds": (R5, [("Format(\"2006-01-02T15:04:05\")", "Format(\"2006-01-02T15:04\")")]),
    "r4_seconds_ignored_when_read": (R5, [("\tt, _ := time.Parse(\"2006-01-02T15:04:05\", s)", "\tt, _ := time.Parse(\"2006-01-02T15:04\", s[:16])")]),
    "r5_february_has_28_days_always": (R5, [("\t\tif isLeap(y) {\n\t\t\treturn 29\n\t\t}\n\t\treturn 28", "\t\treturn 28")]),
    "r5_century_years_never_leap": (R5, [("return y%4 == 0 && (y%100 != 0 || y%400 == 0)", "return y%4 == 0 && y%100 != 0")]),
    "r5_seconds_lose_the_leap_day": (R5, [("\treturn time.Date(y, m, d, hh, mm, ss, 0, time.UTC).Unix()", "\tu := time.Date(y, m, d, hh, mm, ss, 0, time.UTC).Unix()\n\tif isLeap(y) && m > time.February {\n\t\tu -= 86400\n\t}\n\treturn u")]),
    "r6_padded_day_read_as_zero": (R6, [("\tst.day, _ = strconv.Atoi(strings.TrimSpace(line[4:6]))", "\tst.day, _ = strconv.Atoi(line[4:6])\n\tif st.day == 0 {\n\t\tst.day = 1\n\t}")]),
    "r6_message_after_the_last_colon": (R6, [("\tmessage := rest[strings.Index(rest, \": \")+2:]", "\tmessage := rest[strings.LastIndex(rest, \": \")+2:]")]),
    "r6_tag_must_carry_a_pid": (R6, [("\tmessage := rest[strings.Index(rest, \": \")+2:]", "\tmessage := rest[strings.Index(rest, \"]: \")+3:]")]),
    "r6_message_cut_at_128": (R6, [("\treturn st, message", "\tif len(message) > 128 {\n\t\tmessage = message[:128]\n\t}\n\treturn st, message")]),
    "r6_host_cut_at_31": (R6, [("\tst, message := parseLog(line)", "\tst, message := parseLog(line)\n\t\t\tif len(strings.Fields(line)[3]) > 31 {\n\t\t\t\tcontinue\n\t\t\t}")]),
    "r8_batch_year_for_every_line": (R8, [("\tfor y := base - 8; y <= base+8; y++ {", "\tfor y := base; y <= base; y++ {")]),
    "r8_tie_goes_to_the_later_year": (R8, [("\t\tif !found || dist < bestDist {", "\t\tif !found || dist <= bestDist {")]),
    "r8_never_the_year_after": (R8, [("\tfor y := base - 8; y <= base+8; y++ {", "\tfor y := base - 8; y <= base; y++ {")]),
    "r8_february_29_rolls_into_march": (R8, [("\t\tif st.day > daysIn(st.month, y) {\n\t\t\tcontinue\n\t\t}\n", "")]),
    "r9_split_on_spaces_only": (R9, [("\t\tcase (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9'):", "\t\tcase c != ' ':")]),
    "r9_case_kept": (R9, [("\t\t\tb.WriteByte(c + ('a' - 'A'))", "\t\t\tb.WriteByte(c)")]),
    "r9_underscore_joins_words": (R9, [("\t\tcase (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9'):", "\t\tcase (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_':")]),
    "r9_substring_match": (R11, [("\t\t\t\tif e.instant >= from && e.instant <= to && e.words[word] {", "\t\t\t\tif e.instant >= from && e.instant <= to && hasPart(e.words, word) {"),
                                 ("func isLeap(y int) bool {", "func hasPart(ws map[string]bool, w string) bool {\n\tfor k := range ws {\n\t\tif strings.Contains(k, w) {\n\t\t\treturn true\n\t\t}\n\t}\n\treturn false\n}\n\nfunc isLeap(y int) bool {")]),
    "r9_host_and_tag_are_words": (R9, [("\treturn st, message", "\treturn st, line[16:]")]),
    "r10_limit_exclusive": (R10, [("\t\t\t\tif received-e.instant <= keep {", "\t\t\t\tif received-e.instant < keep {")]),
    "r10_new_lines_always_filed": (R10, [("\t\t\tif received-inst <= keep {\n", "\t\t\tif true {\n")]),
    "r10_future_lines_refused": (R10, [("\t\t\tif received-inst <= keep {\n", "\t\t\tif received-inst <= keep && inst <= received {\n")]),
    "r10_sweep_from_each_lines_batch": (R10, [("\t\t\t\tif received-e.instant <= keep {", "\t\t\t\tif e.received-e.instant <= keep {")]),
    "r11_upper_end_excluded": (R11, [("\t\t\t\tif e.instant >= from && e.instant <= to && e.words[word] {", "\t\t\t\tif e.instant >= from && e.instant < to && e.words[word] {")]),
    "r11_lower_end_excluded": (R11, [("\t\t\t\tif e.instant >= from && e.instant <= to && e.words[word] {", "\t\t\t\tif e.instant > from && e.instant <= to && e.words[word] {")]),
    "r11_count_matches_month_and_day": (R11, [("\t\t\t\tif e.instant >= from && e.instant <= to && e.words[word] {", "\t\t\t\tif inRange(e.instant, from, to) && e.words[word] {"),
                                              ("func isLeap(y int) bool {", "func inRange(u, from, to int64) bool {\n\tfor d := int64(-1); d <= 1; d++ {\n\t\tv := u + d*365*86400\n\t\tif v >= from && v <= to {\n\t\t\treturn true\n\t\t}\n\t}\n\treturn false\n}\n\nfunc isLeap(y int) bool {")]),
    "r12_lag_reversed": (R12, [("strconv.FormatInt(e.received-e.instant, 10)", "strconv.FormatInt(e.instant-e.received, 10)")]),
    "r12_lag_clamped_at_zero": (R12, [("strconv.FormatInt(e.received-e.instant, 10)", "strconv.FormatInt(max(0, e.received-e.instant), 10)")]),
    "r12_lag_to_the_latest_batch": (R12, [("strconv.FormatInt(e.received-e.instant, 10)", "strconv.FormatInt(received-e.instant, 10)")]),
    "r12_lag_modulo_a_day": (R12, [("strconv.FormatInt(e.received-e.instant, 10)", "strconv.FormatInt((e.received-e.instant)%86400, 10)")]),
    "r13_lines_in_filing_order": (R13, [("\t\t\tsort.SliceStable(hits, func(a, b int) bool { return hits[a].instant < hits[b].instant })\n", "")]),
    "r13_equal_instants_reversed": (R13, [("\t\t\tsort.SliceStable(hits, func(a, b int) bool { return hits[a].instant < hits[b].instant })",
                                           "\t\t\tsort.SliceStable(hits, func(a, b int) bool { return hits[a].instant <= hits[b].instant })")]),
    "r13_second_lag_from_the_batch": (R13, [("strconv.FormatInt(at-again, 10)", "strconv.FormatInt(e.received-again, 10)")]),
    "r13_no_line_writes_nothing": (R13, [("\t\t\t\tw.WriteString(strconv.Itoa(query) + \" -\\n\")", "\t\t\t\t_ = query")]),
    "r14_numbered_per_kind": (R14, [("\tquery := 0\n", "\tquery := 0\n\tlags := 0\n"), ("\t\tcase \"lag\":\n\t\t\tquery++", "\t\tcase \"lag\":\n\t\t\tlags++\n\t\t\tquery := lags")]),
    "r14_numbered_from_zero": (R14, [("\tquery := 0\n", "\tquery := -1\n")]),
    "r14_answers_after_the_journal": (R14, [("\tquery := 0\n", "\tquery := 0\n\tvar deferred []string\n"),
                                            ("\t\t\tw.WriteString(strconv.Itoa(query) + \" \" + strconv.Itoa(n) + \"\\n\")", "\t\t\tdeferred = append(deferred, strconv.Itoa(query))"),
                                            ("func main() {", "func unusedMain() {}\n\nfunc main() {"),
                                            ("\t\t\t\tindex = append(index, &entry{", "\t\t\t\t_ = deferred\n\t\t\t\tindex = append(index, &entry{")]),
    "r15_index_capped_at_256": (R15, [("\t\t\t\tindex = append(index, &entry{", "\t\t\t\tif len(index) >= 256 {\n\t\t\t\t\tcontinue\n\t\t\t\t}\n\t\t\t\tindex = append(index, &entry{")]),
    "r15_stops_after_32_queries": (R15, [("\t\tcase \"count\":\n\t\t\tquery++", "\t\tcase \"count\":\n\t\t\tquery++\n\t\t\tif query > 32 {\n\t\t\t\tcontinue\n\t\t\t}")]),
}


def apply(source, patches):
    for old, new in patches:
        n = source.count(old)
        if n != 1:
            raise SystemExit("patch anchor found %d times: %r" % (n, old[:80]))
        source = source.replace(old, new)
    return source
