"""The mutant matrix: every rule-bearing sentence of the notes has an exact patch on the
reference (solution/main.go) that breaks it, owned by the test of that rule. A patch whose
anchor is missing or not unique stops the sweep (apply raises). VICTIMS are the natural
wrong programs of the two cells; LAZY takes the wrong answer in both cells at once."""

GREEDY = '''func distance(doc *document, words []queryWord, fuzz int) (int, bool) {
	cand := make([][]int, len(words))
	for j, qw := range words {
		for p, t := range doc.tokens {
			if !t.stop && editDistance(qw.word, t.word) <= fuzz {
				cand[j] = append(cand[j], p)
			}
		}
		if len(cand[j]) == 0 {
			return 0, false
		}
	}
	best, found := 0, false
	for _, a := range cand[0] {
		used := map[int]bool{a: true}
		anchor := a - words[0].pos
		lo, hi, ok := anchor, anchor, true
		for j := 1; j < len(words) && ok; j++ {
			pick, gap := -1, 0
			for _, p := range cand[j] {
				if used[p] {
					continue
				}
				g := p - words[j].pos - anchor
				if g < 0 {
					g = -g
				}
				if pick < 0 || g < gap {
					pick, gap = p, g
				}
			}
			if pick < 0 {
				ok = false
				break
			}
			used[pick] = true
			off := pick - words[j].pos
			if off < lo {
				lo = off
			}
			if off > hi {
				hi = off
			}
		}
		if ok && (!found || hi-lo < best) {
			best, found = hi-lo, true
		}
	}
	return best, found
}

func unusedDistance(doc *document, words []queryWord, fuzz int) (int, bool) {'''

OSA = '''func editDistance(a, b string) int {
	d := make([][]int, len(a)+1)
	for i := range d {
		d[i] = make([]int, len(b)+1)
		d[i][0] = i
	}
	for j := 0; j <= len(b); j++ {
		d[0][j] = j
	}
	for i := 1; i <= len(a); i++ {
		for j := 1; j <= len(b); j++ {
			cost := 1
			if a[i-1] == b[j-1] {
				cost = 0
			}
			best := d[i-1][j-1] + cost
			if v := d[i-1][j] + 1; v < best {
				best = v
			}
			if v := d[i][j-1] + 1; v < best {
				best = v
			}
			if %s i > 1 && j > 1 && a[i-1] == b[j-2] && a[i-2] == b[j-1] && d[i-2][j-2]+1 < best {
				best = d[i-2][j-2] + 1
			}
			d[i][j] = best
		}
	}
	return d[len(a)][len(b)]
}

func unusedEditDistance(a, b string) int {'''

R1 = "test_journal_lines_and_stop_word_line"
R3 = "test_line_fields_ids_slop_and_fuzz"
R5 = "test_batches_commit_and_roll_back"
R6 = "test_tokens_split_on_punctuation_and_lowercase"
R7 = "test_stop_words_hold_positions_and_are_not_indexed"
R8 = "test_add_replaces_a_live_document"
R9 = "test_append_carries_positions_on"
R10 = "test_delete_ends_a_document"
R11 = "test_document_size_limits"
R12 = "test_query_words_keep_their_positions"
R13 = "test_edit_distance_is_the_least_number_of_edits"
R14 = "test_placements_take_distinct_tokens_within_the_fuzz"
R15 = "test_distance_is_the_least_spread"
R16 = "test_hits_lie_within_the_slop"
R17 = "test_output_order_and_format"

STOP_FUZZY = ("if !t.stop && editDistance(qw.word, t.word) <= fuzz {\n\t\t\t\tcand[j] = append(cand[j], p)\n\t\t\t\toffs",
              "if editDistance(qw.word, t.word) <= fuzz {\n\t\t\t\tcand[j] = append(cand[j], p)\n\t\t\t\toffs")
OSA_DP = ("func editDistance(a, b string) int {", OSA % "")

VICTIMS = {
    "victim_stop_token_fuzzy_match": ([R7, R14], [STOP_FUZZY]),
    "victim_restricted_edit_distance": ([R13, R16], [OSA_DP]),
}
LAZY = [STOP_FUZZY, OSA_DP]

MUTANTS = {
    "r1_reads_at_most_399_lines": (R1, [("\tw := bufio.NewWriter(out)", "\tif len(lines) > 399 {\n\t\tlines = lines[:399]\n\t}\n\tw := bufio.NewWriter(out)")]),
    "r1_prints_a_line_without_queries": (R1, [("\tstops := map[string]bool{}\n", "\tif len(lines) == 1 {\n\t\tw.WriteString(\"-\\n\")\n\t}\n\tstops := map[string]bool{}\n")]),
    "r2_built_in_stop_list": (R1, [("\tstops := map[string]bool{}\n", "\tstops := map[string]bool{\"on\": true, \"the\": true, \"a\": true}\n")]),
    "r2_keyword_is_a_stop_word": (R1, [("strings.TrimPrefix(lines[0], \"stopwords\")", "lines[0]")]),
    "r2_stop_list_cut_at_19": (R1, [("\tfor _, s := range strings.Fields(strings.TrimPrefix(lines[0], \"stopwords\")) {",
                                     "\tfor i, s := range strings.Fields(strings.TrimPrefix(lines[0], \"stopwords\")) {\n\t\tif i >= 19 {\n\t\t\tbreak\n\t\t}")]),
    "r2_stop_word_cut_at_8_letters": (R1, [("\t\tstops[s] = true", "\t\tif len(s) > 8 {\n\t\t\ts = s[:8]\n\t\t}\n\t\tstops[s] = true")]),
    "r3_id_read_into_text": (R3, [("case \"add\":\n\t\t\tid, text := splitField(rest)", "case \"add\":\n\t\t\tid, _ := splitField(rest)\n\t\t\ttext := rest")]),
    "r3_fuzz_read_as_a_word": (R3, [("\t\t\tfuzzText, text := splitField(rest)", "\t\t\tfuzzText, _ := splitField(rest)\n\t\t\ttext := rest")]),
    "r3_id_cut_at_15": (R3, [("\t\t\tworking[id] = &document{tokens: analyze(text, stops)}", "\t\t\tif len(id) > 15 {\n\t\t\t\tid = id[:15]\n\t\t\t}\n\t\t\tworking[id] = &document{tokens: analyze(text, stops)}")]),
    "r3_line_cut_at_128": (R3, [("\tfor _, line := range lines[1:] {", "\tfor _, line := range lines[1:] {\n\t\tif len(line) > 128 {\n\t\t\tline = line[:128]\n\t\t}")]),
    "r3_fuzz_capped_at_1": (R3, [("\t\t\tfuzz, _ := strconv.Atoi(fuzzText)", "\t\t\tfuzz, _ := strconv.Atoi(fuzzText)\n\t\t\tif fuzz > 1 {\n\t\t\t\tfuzz = 1\n\t\t\t}")]),
    "r3_slop_capped_at_8": (R3, [("\t\t\tslop, _ := strconv.Atoi(slopText)", "\t\t\tslop, _ := strconv.Atoi(slopText)\n\t\t\tif slop > 8 {\n\t\t\t\tslop = 8\n\t\t\t}")]),
    "r5_empty_commit_clears_the_index": (R5, [
        ("case \"begin\":\n\t\t\tworking = committed.clone()", "case \"begin\":\n\t\t\tworking = nil"),
        ("case \"add\":\n\t\t\tid, text := splitField(rest)", "case \"add\":\n\t\t\tif working == nil {\n\t\t\t\tworking = committed.clone()\n\t\t\t}\n\t\t\tid, text := splitField(rest)"),
        ("case \"append\":\n\t\t\tid, text := splitField(rest)", "case \"append\":\n\t\t\tif working == nil {\n\t\t\t\tworking = committed.clone()\n\t\t\t}\n\t\t\tid, text := splitField(rest)"),
        ("case \"delete\":\n\t\t\tdelete(working, rest)", "case \"delete\":\n\t\t\tif working == nil {\n\t\t\t\tworking = committed.clone()\n\t\t\t}\n\t\t\tdelete(working, rest)")]),
    "r5_rollback_commits": (R5, [("case \"rollback\":\n\t\t\tworking = nil", "case \"rollback\":\n\t\t\tcommitted = working\n\t\t\tworking = nil")]),
    "r5_working_copy_shares_documents": (R5, [("\t\tcp := &document{tokens: make([]token, len(doc.tokens))}\n\t\tcopy(cp.tokens, doc.tokens)\n\t\tout[id] = cp", "\t\tout[id] = doc")]),
    "r5_batch_starts_from_empty": (R5, [("case \"begin\":\n\t\t\tworking = committed.clone()", "case \"begin\":\n\t\t\tworking = index{}")]),
    "r6_split_on_spaces_only": (R6, [("return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9')", "return c != ' '")]),
    "r6_underscore_is_a_word_character": (R6, [("|| (c >= '0' && c <= '9')", "|| (c >= '0' && c <= '9') || c == '_'")]),
    "r6_case_kept": (R6, [("\t\treturn c + ('a' - 'A')", "\t\treturn c")]),
    "r6_digits_separate": (R6, [("return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9')", "return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z')")]),
    "r7_stop_words_take_no_position": (R7, [("\t\tout = append(out, token{word: w, stop: stops[w]})", "\t\tif !stops[w] {\n\t\t\tout = append(out, token{word: w})\n\t\t}")]),
    "r8_add_extends_a_live_document": (R8, [("\t\t\tworking[id] = &document{tokens: analyze(text, stops)}",
                                             "\t\t\tif d, ok := working[id]; ok {\n\t\t\t\td.tokens = append(d.tokens, analyze(text, stops)...)\n\t\t\t} else {\n\t\t\t\tworking[id] = &document{tokens: analyze(text, stops)}\n\t\t\t}")]),
    "r8_first_add_wins": (R8, [("\t\t\tworking[id] = &document{tokens: analyze(text, stops)}", "\t\t\tif _, ok := working[id]; !ok {\n\t\t\t\tworking[id] = &document{tokens: analyze(text, stops)}\n\t\t\t}")]),
    "r8_replacement_keeps_the_old_length": (R8, [("\t\t\tworking[id] = &document{tokens: analyze(text, stops)}",
                                                  "\t\t\tnt := analyze(text, stops)\n\t\t\tif d, ok := working[id]; ok {\n\t\t\t\tfor i := len(nt); i < len(d.tokens); i++ {\n\t\t\t\t\tnt = append(nt, token{stop: true})\n\t\t\t\t}\n\t\t\t}\n\t\t\tworking[id] = &document{tokens: nt}")]),
    "r9_append_replaces_the_text": (R9, [("\t\t\tdoc.tokens = append(doc.tokens, analyze(text, stops)...)", "\t\t\tdoc.tokens = analyze(text, stops)")]),
    "r9_append_after_last_indexed_token": (R9, [("\t\t\tdoc.tokens = append(doc.tokens, analyze(text, stops)...)",
                                                 "\t\t\tfor len(doc.tokens) > 0 && doc.tokens[len(doc.tokens)-1].stop {\n\t\t\t\tdoc.tokens = doc.tokens[:len(doc.tokens)-1]\n\t\t\t}\n\t\t\tdoc.tokens = append(doc.tokens, analyze(text, stops)...)")]),
    "r9_append_runs_the_texts_together": (R9, [("\t\t\tdoc.tokens = append(doc.tokens, analyze(text, stops)...)",
                                                "\t\t\tnt := analyze(text, stops)\n\t\t\tif len(doc.tokens) > 0 && len(nt) > 0 {\n\t\t\t\tm := doc.tokens[len(doc.tokens)-1].word + nt[0].word\n\t\t\t\tdoc.tokens[len(doc.tokens)-1] = token{word: m, stop: stops[m]}\n\t\t\t\tnt = nt[1:]\n\t\t\t}\n\t\t\tdoc.tokens = append(doc.tokens, nt...)")]),
    "r10_delete_ignored": (R10, [("\t\t\tdelete(working, rest)", "\t\t\t_ = rest")]),
    "r10_deleted_document_comes_back": (R10, [
        ("type index map[string]*document", "type index map[string]*document\n\nvar graveyard = map[string][]token{}"),
        ("\t\t\tdelete(working, rest)", "\t\t\tif d, ok := working[rest]; ok {\n\t\t\t\tgraveyard[rest] = d.tokens\n\t\t\t}\n\t\t\tdelete(working, rest)"),
        ("\t\t\tworking[id] = &document{tokens: analyze(text, stops)}", "\t\t\told := append([]token(nil), graveyard[id]...)\n\t\t\tworking[id] = &document{tokens: append(old, analyze(text, stops)...)}")]),
    "r11_document_cut_at_256_tokens": (R11, [("\t\t\tdoc.tokens = append(doc.tokens, analyze(text, stops)...)", "\t\t\tdoc.tokens = append(doc.tokens, analyze(text, stops)...)\n\t\t\tif len(doc.tokens) > 256 {\n\t\t\t\tdoc.tokens = doc.tokens[:256]\n\t\t\t}")]),
    "r11_at_most_64_documents": (R11, [("\t\t\tworking[id] = &document{tokens: analyze(text, stops)}", "\t\t\tif _, ok := working[id]; ok || len(working) < 64 {\n\t\t\t\tworking[id] = &document{tokens: analyze(text, stops)}\n\t\t\t}")]),
    "r12_query_case_kept": (R12, [("\tvar out []queryWord\n\tfor pos, t := range analyze(text, stops) {", "\tvar out []queryWord\n\traw := strings.FieldsFunc(text, func(r rune) bool { return r > 127 || !isWordByte(byte(r)) })\n\tfor pos, t := range analyze(text, stops) {\n\t\tt.word = raw[pos]"),
                                  ("\t\tif t.stop {\n\t\t\tcontinue\n\t\t}\n\t\tout = append", "\t\tif stops[strings.ToLower(t.word)] {\n\t\t\tcontinue\n\t\t}\n\t\tout = append")]),
    "r12_query_stop_words_kept": (R12, [("\t\tif t.stop {\n\t\t\tcontinue\n\t\t}\n\t\tout = append", "\t\tout = append")]),
    "r12_query_words_renumbered": (R12, [("queryWord{word: t.word, pos: pos}", "queryWord{word: t.word, pos: len(out) + pos - pos}")]),
    "r12_repeated_words_merged": (R12, [("\t\tif t.stop {\n\t\t\tcontinue\n\t\t}\n\t\tout = append", "\t\tdup := false\n\t\tfor _, q := range out {\n\t\t\tdup = dup || q.word == t.word\n\t\t}\n\t\tif t.stop || dup {\n\t\t\tcontinue\n\t\t}\n\t\tout = append")]),
    "r12_query_cut_at_5_words": (R12, [("\t\tif t.stop {\n\t\t\tcontinue\n\t\t}\n\t\tout = append", "\t\tif t.stop || len(out) == 5 {\n\t\t\tcontinue\n\t\t}\n\t\tout = append")]),
    "r13_no_swaps": (R13, [("func editDistance(a, b string) int {", OSA % "false &&")]),
    "r13_swap_costs_two_edits_more": (R13, [("\t\t\tif v := d[k][l] + (i - k - 1) + 1 + (j - l - 1); v < best {", "\t\t\tif v := d[k][l] + (i - k - 1) + 2 + (j - l - 1); v < best {")]),
    "r14_missing_words_skipped": (R14, [("\t\tif len(cand[j]) == 0 {\n\t\t\treturn 0, false\n\t\t}\n\t}\n\tsort.Ints(offs)", "\t}\n\tif len(offs) == 0 {\n\t\treturn 0, false\n\t}\n\tsort.Ints(offs)"),
                                        ("\t\tif !try(j, map[int]bool{}) {", "\t\tif len(cand[j]) > 0 && !try(j, map[int]bool{}) {")]),
    "r14_tokens_shared_between_words": (R14, [("\t\t\tprev, taken := owner[p]", "\t\t\tprev, taken := 0, false")]),
    "r14_fuzz_ignored": (R14, [("&& editDistance(qw.word, t.word) <= fuzz {\n\t\t\t\tcand[j] = append(cand[j], p)\n\t\t\t\toffs", "&& qw.word == t.word {\n\t\t\t\tcand[j] = append(cand[j], p)\n\t\t\t\toffs")]),
    "r15_order_ignored": (R15, [("\t\t\t\toffs = append(offs, p-qw.pos)", "\t\t\t\toffs = append(offs, p)"), ("\t\t\toff := p - words[j].pos", "\t\t\toff := p")]),
    "r15_first_occurrence_only": (R15, [("if !t.stop && editDistance(qw.word, t.word) <= fuzz {\n\t\t\t\tcand[j] = append(cand[j], p)\n\t\t\t\toffs", "if len(cand[j]) == 0 && !t.stop && editDistance(qw.word, t.word) <= fuzz {\n\t\t\t\tcand[j] = append(cand[j], p)\n\t\t\t\toffs")]),
    "r15_first_window_found": (R15, [("\t\t\tbest, found = uniq[h]-uniq[l], true\n\t\t}", "\t\t\tbest, found = uniq[h]-uniq[l], true\n\t\t\tbreak\n\t\t}")]),
    "r15_nearest_token_greedy": (R15, [("func distance(doc *document, words []queryWord, fuzz int) (int, bool) {", GREEDY)]),
    "r16_slop_is_exclusive": (R16, [("\t\tif ok && d <= slop {", "\t\tif ok && d < slop {")]),
    "r16_slop_ignored": (R16, [("\t\tif ok && d <= slop {", "\t\tif ok {")]),
    "r17_ties_by_descending_id": (R17, [("\t\treturn hits[a].id < hits[b].id", "\t\treturn hits[a].id > hits[b].id")]),
    "r17_ties_by_length_then_id": (R17, [("\t\treturn hits[a].id < hits[b].id", "\t\tif len(hits[a].id) != len(hits[b].id) {\n\t\t\treturn len(hits[a].id) < len(hits[b].id)\n\t\t}\n\t\treturn hits[a].id < hits[b].id")]),
    "r17_sorted_by_id_alone": (R17, [("\t\tif hits[a].dist != hits[b].dist {\n\t\t\treturn hits[a].dist < hits[b].dist\n\t\t}\n", "")]),
    "r17_empty_line_without_hits": (R17, [("\t\treturn \"-\"", "\t\treturn \"\"")]),
    "r17_distance_omitted": (R17, [("\t\tparts[i] = h.id + \"@\" + strconv.Itoa(h.dist)", "\t\tparts[i] = h.id")]),
    "r17_answers_after_the_whole_journal": (R17, [
        ("\tcommitted := index{}\n\tvar working index\n", "\tcommitted := index{}\n\tvar working index\n\tvar pending []func() string\n"),
        ("\t\t\tw.WriteString(search(committed, slop, fuzz, queryWords(text, stops)))\n\t\t\tw.WriteString(\"\\n\")",
         "\t\t\tqw := queryWords(text, stops)\n\t\t\tpending = append(pending, func() string { return search(committed, slop, fuzz, qw) })"),
        ("\t\t}\n\t}\n}\n\nfunc main", "\t\t}\n\t}\n\tfor _, f := range pending {\n\t\tw.WriteString(f() + \"\\n\")\n\t}\n}\n\nfunc main")]),
}

NEW_MUTANTS = {
    "r6_separators_listed_without_hash": (R6, [("return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9')", "return !strings.ContainsRune(\" ,.:;_/@=()[]!-\", rune(c))")]),
    "r6_separators_listed_without_bang": (R6, [("return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9')", "return !strings.ContainsRune(\" -.,:/()=_@[]#\", rune(c))")]),
    "r6_apostrophe_kept_in_tokens": (R6, [("|| (c >= '0' && c <= '9')", "|| (c >= '0' && c <= '9') || c == '\\''")]),
    "r9_tokenless_append_takes_a_position": (R9, [("\t\t\tdoc.tokens = append(doc.tokens, analyze(text, stops)...)", "\t\t\tnt := analyze(text, stops)\n\t\t\tif len(nt) == 0 {\n\t\t\t\tnt = []token{{stop: true}}\n\t\t\t}\n\t\t\tdoc.tokens = append(doc.tokens, nt...)")]),
    "r3_append_text_cut_at_199": (R3, [("\t\t\tdoc := working[id]\n", "\t\t\tif len(text) > 199 {\n\t\t\t\ttext = text[:199]\n\t\t\t}\n\t\t\tdoc := working[id]\n")]),
    "r9_at_most_300_appends": (R9, [("type index map[string]*document", "type index map[string]*document\n\nvar appends = map[string]int{}"),
                                    ("\t\t\tdoc := working[id]\n", "\t\t\tappends[id]++\n\t\t\tif appends[id] > 300 {\n\t\t\t\tbreak\n\t\t\t}\n\t\t\tdoc := working[id]\n")]),
    "r13_fuzzy_refuses_tokens_over_32": (R13, [("if !t.stop && editDistance(qw.word, t.word) <= fuzz {\n\t\t\t\tcand[j] = append(cand[j], p)\n\t\t\t\toffs",
                                               "if !t.stop && (fuzz == 0 && qw.word == t.word || fuzz > 0 && len(qw.word) <= 32 && len(t.word) <= 32 && editDistance(qw.word, t.word) <= fuzz) {\n\t\t\t\tcand[j] = append(cand[j], p)\n\t\t\t\toffs")]),
    "r3_id_must_start_with_a_letter": (R3, [("case \"add\":\n\t\t\tid, text := splitField(rest)\n", "case \"add\":\n\t\t\tid, text := splitField(rest)\n\t\t\tif id[0] < 'a' || id[0] > 'z' {\n\t\t\t\tbreak\n\t\t\t}\n")]),
    "r11_at_most_100_ids_ever": (R11, [("type index map[string]*document", "type index map[string]*document\n\nvar everSeen = map[string]bool{}"),
                                       ("case \"add\":\n\t\t\tid, text := splitField(rest)\n", "case \"add\":\n\t\t\tid, text := splitField(rest)\n\t\t\tif !everSeen[id] {\n\t\t\t\tif len(everSeen) >= 100 {\n\t\t\t\t\tbreak\n\t\t\t\t}\n\t\t\t\teverSeen[id] = true\n\t\t\t}\n")]),
}
MUTANTS.update(NEW_MUTANTS)

PROBE_MUTANTS = {
    "r6_tokens_cut_at_64": (R6, [("\t\tout = append(out, b.String())", "\t\ttok := b.String()\n\t\tif len(tok) > 64 {\n\t\t\ttok = tok[:64]\n\t\t}\n\t\tout = append(out, tok)")]),
    "r12_query_tokens_cut_at_8_before_stops": (R12, [("\tfor pos, t := range analyze(text, stops) {", "\tall := analyze(text, stops)\n\tif len(all) > 8 {\n\t\tall = all[:8]\n\t}\n\tfor pos, t := range all {")]),
    "r13_no_fuzz_for_words_not_longer_than_fuzz": (R13, [("if !t.stop && editDistance(qw.word, t.word) <= fuzz {\n\t\t\t\tcand[j] = append(cand[j], p)\n\t\t\t\toffs",
                                                         "if !t.stop && (qw.word == t.word || len(qw.word) > fuzz && editDistance(qw.word, t.word) <= fuzz) {\n\t\t\t\tcand[j] = append(cand[j], p)\n\t\t\t\toffs")]),
    "r13_first_letter_must_match": (R13, [("if !t.stop && editDistance(qw.word, t.word) <= fuzz {\n\t\t\t\tcand[j] = append(cand[j], p)\n\t\t\t\toffs",
                                          "if !t.stop && qw.word[0] == t.word[0] && editDistance(qw.word, t.word) <= fuzz {\n\t\t\t\tcand[j] = append(cand[j], p)\n\t\t\t\toffs")]),
    "r14_candidates_capped_at_100": (R14, [("if !t.stop && editDistance(qw.word, t.word) <= fuzz {\n\t\t\t\tcand[j] = append(cand[j], p)\n\t\t\t\toffs",
                                           "if len(cand[j]) < 100 && !t.stop && editDistance(qw.word, t.word) <= fuzz {\n\t\t\t\tcand[j] = append(cand[j], p)\n\t\t\t\toffs")]),
}
MUTANTS.update(PROBE_MUTANTS)

ROUND3_MUTANTS = {
    "r6_tokens_cut_at_120": (R6, [("\t\tout = append(out, b.String())", "\t\ttok := b.String()\n\t\tif len(tok) > 120 {\n\t\t\ttok = tok[:120]\n\t\t}\n\t\tout = append(out, tok)")]),
    "r6_tokens_cut_at_122": (R6, [("\t\tout = append(out, b.String())", "\t\ttok := b.String()\n\t\tif len(tok) > 122 {\n\t\t\ttok = tok[:122]\n\t\t}\n\t\tout = append(out, tok)")]),
    "r3_line_cut_at_220": (R3, [("\tfor _, line := range lines[1:] {", "\tfor _, line := range lines[1:] {\n\t\tif len(line) > 220 {\n\t\t\tline = line[:220]\n\t\t}")]),
}
MUTANTS.update(ROUND3_MUTANTS)

ROUND3_PROBE = {
    "r2_first_line_cut_at_256": (R1, [("\tstops := map[string]bool{}\n", "\tif len(lines[0]) > 256 {\n\t\tlines[0] = lines[0][:256]\n\t}\n\tstops := map[string]bool{}\n")]),
}
MUTANTS.update(ROUND3_PROBE)

ROUND4_MUTANTS = {
    "r11_positions_wrap_at_256": (R11, [("\tfor j, qw := range words {\n\t\tfor p, t := range doc.tokens {", "\tfor j, qw := range words {\n\t\tfor p0, t := range doc.tokens {\n\t\t\tp := int(uint8(p0))")]),
    "r11_positions_wrap_at_128": (R11, [("\tfor j, qw := range words {\n\t\tfor p, t := range doc.tokens {", "\tfor j, qw := range words {\n\t\tfor p0, t := range doc.tokens {\n\t\t\tp := int(int8(p0))")]),
}
MUTANTS.update(ROUND4_MUTANTS)

ROUND4_PROBE = {
    "r15_offsets_kept_in_int8": (R15, [("\t\t\t\toffs = append(offs, p-qw.pos)", "\t\t\t\toffs = append(offs, int(int8(p-qw.pos)))"), ("\t\t\toff := p - words[j].pos", "\t\t\toff := int(int8(p - words[j].pos))")]),
    "r15_offsets_kept_in_uint8": (R15, [("\t\t\t\toffs = append(offs, p-qw.pos)", "\t\t\t\toffs = append(offs, int(uint8(p-qw.pos)))"), ("\t\t\toff := p - words[j].pos", "\t\t\toff := int(uint8(p - words[j].pos))")]),
}
MUTANTS.update(ROUND4_PROBE)

WORD = "return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9')"

ROUND5_MUTANTS = {
    "r6_lowercasing_stops_before_Z": (R6, [("\tif c >= 'A' && c <= 'Z' {\n\t\treturn c + ('a' - 'A')", "\tif c >= 'A' && c < 'Z' {\n\t\treturn c + ('a' - 'A')")]),
    "r6_lowercasing_starts_after_A": (R6, [("\tif c >= 'A' && c <= 'Z' {\n\t\treturn c + ('a' - 'A')", "\tif c > 'A' && c <= 'Z' {\n\t\treturn c + ('a' - 'A')")]),
    "r6_letters_end_before_z": (R6, [(WORD, WORD.replace("c <= 'z'", "c < 'z'"))]),
    "r6_letters_start_after_a": (R6, [(WORD, WORD.replace("c >= 'a'", "c > 'a'"))]),
    "r6_capitals_end_before_Z": (R6, [(WORD, WORD.replace("c <= 'Z'", "c < 'Z'"))]),
    "r6_capitals_start_after_A": (R6, [(WORD, WORD.replace("c >= 'A'", "c > 'A'"))]),
    "r6_digits_end_before_9": (R6, [(WORD, WORD.replace("c <= '9'", "c < '9'"))]),
    "r6_digits_start_after_0": (R6, [(WORD, WORD.replace("c >= '0'", "c > '0'"))]),
}
MUTANTS.update(ROUND5_MUTANTS)

ROUND6_MUTANTS = {
    "r14_occurrences_capped_at_256": (R14, [("if !t.stop && editDistance(qw.word, t.word) <= fuzz {\n\t\t\t\tcand[j] = append(cand[j], p)\n\t\t\t\toffs",
                                            "if len(cand[j]) < 256 && !t.stop && editDistance(qw.word, t.word) <= fuzz {\n\t\t\t\tcand[j] = append(cand[j], p)\n\t\t\t\toffs")]),
    "r12_query_tokens_capped_at_32": (R12, [("\tfor pos, t := range analyze(text, stops) {", "\tall := analyze(text, stops)\n\tif len(all) > 32 {\n\t\tall = all[:32]\n\t}\n\tfor pos, t := range all {")]),
    "r17_at_most_256_answers": (R17, [("\t\t\tw.WriteString(search(committed, slop, fuzz, queryWords(text, stops)))\n\t\t\tw.WriteString(\"\\n\")",
                                      "\t\t\tanswered++\n\t\t\tif answered > 256 {\n\t\t\t\tbreak\n\t\t\t}\n\t\t\tw.WriteString(search(committed, slop, fuzz, queryWords(text, stops)))\n\t\t\tw.WriteString(\"\\n\")"),
                                     ("\tcommitted := index{}\n\tvar working index\n", "\tcommitted := index{}\n\tvar working index\n\tanswered := 0\n")]),
    "r5_at_most_128_commits": (R5, [("case \"commit\":\n\t\t\tcommitted = working", "case \"commit\":\n\t\t\tcommits++\n\t\t\tif commits <= 128 {\n\t\t\t\tcommitted = working\n\t\t\t}"),
                                    ("\tcommitted := index{}\n\tvar working index\n", "\tcommitted := index{}\n\tvar working index\n\tcommits := 0\n")]),
    "r11_at_most_192_ids_ever": (R11, [("type index map[string]*document", "type index map[string]*document\n\nvar seenIDs = map[string]bool{}"),
                                       ("case \"add\":\n\t\t\tid, text := splitField(rest)\n", "case \"add\":\n\t\t\tid, text := splitField(rest)\n\t\t\tif !seenIDs[id] {\n\t\t\t\tif len(seenIDs) >= 192 {\n\t\t\t\t\tbreak\n\t\t\t\t}\n\t\t\t\tseenIDs[id] = true\n\t\t\t}\n")]),
    "r9_at_most_350_appends": (R9, [("type index map[string]*document", "type index map[string]*document\n\nvar appendCount = map[string]int{}"),
                                    ("\t\t\tdoc := working[id]\n", "\t\t\tappendCount[id]++\n\t\t\tif appendCount[id] > 350 {\n\t\t\t\tbreak\n\t\t\t}\n\t\t\tdoc := working[id]\n")]),
    "r11_index_capped_at_16384_tokens": (R11, [("type index map[string]*document", "type index map[string]*document\n\nvar filed = 0"),
                                               ("\t\t\tdoc := working[id]\n", "\t\t\tfiled += len(analyze(text, stops))\n\t\t\tif filed > 16384 {\n\t\t\t\tbreak\n\t\t\t}\n\t\t\tdoc := working[id]\n")]),
}
MUTANTS.update(ROUND6_MUTANTS)


def apply(source, patches):
    for old, new in patches:
        n = source.count(old)
        if n != 1:
            raise SystemExit("patch anchor found %d times: %r" % (n, old[:80]))
        source = source.replace(old, new)
    return source
