"""Verifier for the Calderbank accrual schedule builder, servicing note SN-208."""

from harness import *


# Keep the panel's distinguishing runs visible here, independently of the
# longer sealed trap table. This is the ordinary seven-line TRADE facility.
PANEL_FACILITY = (b"facility F-TR-0000\nclass TRADE\nadvance 1000000\nrate 500\n"
                  b"drawn 2022-01-31\nperiods 3\nfrequency 1\n")


def test_the_year_2000_leap_day_is_valid_in_drawdowns_steps_and_period_ends():
    """Sections 3.5, 6 and 8: 2000 is divisible by four within the stated calendar."""
    one_period = PANEL_FACILITY.replace(b"periods 3\n", b"periods 1\n")
    drawn = one_period.replace(b"2022-01-31", b"2000-02-29")
    stepped = one_period.replace(b"2022-01-31", b"2000-02-01") + b"step 2000-02-29 500\n"
    endpoints = PANEL_FACILITY.replace(b"2022-01-31", b"2000-01-31")
    endpoints = endpoints.replace(b"periods 3\n", b"periods 2\n")
    # TRADE uses actual/360. A 29-day period on 1000000p at 500bp earns
    # 4027p with remainder 2800000; the next 31 days on 500000p earn 2153p.
    # The step on 2000-02-29 splits the 29 days, so 8.4 takes 7.5 twice and
    # the period interest is 4026p.
    cases = (
        (drawn, b"SCHEDULE F-TR-0000 TRADE K1 001\n"
         b"0001 2000-02-29 2000-03-29 000029 000000001000000 000000000004027 000000001000000\n"
         b"TOTAL 000000000004027 000000001000000\n"),
        (stepped, b"SCHEDULE F-TR-0000 TRADE K1 001\n"
         b"0001 2000-02-01 2000-03-01 000029 000000001000000 000000000004026 000000001000000\n"
         b"TOTAL 000000000004026 000000001000000\n"),
        (endpoints, b"SCHEDULE F-TR-0000 TRADE K1 002\n"
         b"0001 2000-01-31 2000-02-29 000029 000000001000000 000000000004027 000000000500000\n"
         b"0002 2000-02-29 2000-03-31 000031 000000000500000 000000000002153 000000000500000\n"
         b"TOTAL 000000000006180 000000001000000\n"),
    )
    wrong = []
    for facility, expected in cases:
        result = run_case(facility, CASES["crossley"][1])
        if (*result, LAST_RUN["left"]) != (0, b"", b"", expected, []):
            wrong.append({"facility": facility, "expected": expected,
                          "actual": result, "residue": LAST_RUN["left"]})
    assert wrong == [], "\n".join(repr(item) for item in wrong)


def test_the_year_2100_leap_day_is_valid_in_drawdowns_steps_and_period_ends():
    """Sections 3.5, 6 and 8: 2100 divides by four within the stated calendar."""
    one_period = PANEL_FACILITY.replace(b"periods 3\n", b"periods 1\n")
    drawn = one_period.replace(b"2022-01-31", b"2100-02-29")
    stepped = one_period.replace(b"2022-01-31", b"2100-02-01") + b"step 2100-02-29 500\n"
    endpoints = PANEL_FACILITY.replace(b"2022-01-31", b"2100-01-31")
    endpoints = endpoints.replace(b"periods 3\n", b"periods 2\n")
    cases = (
        (drawn, b"SCHEDULE F-TR-0000 TRADE K1 001\n"
         b"0001 2100-02-29 2100-03-29 000029 000000001000000 000000000004027 000000001000000\n"
         b"TOTAL 000000000004027 000000001000000\n"),
        (stepped, b"SCHEDULE F-TR-0000 TRADE K1 001\n"
         b"0001 2100-02-01 2100-03-01 000029 000000001000000 000000000004026 000000001000000\n"
         b"TOTAL 000000000004026 000000001000000\n"),
        (endpoints, b"SCHEDULE F-TR-0000 TRADE K1 002\n"
         b"0001 2100-01-31 2100-02-29 000029 000000001000000 000000000004027 000000000500000\n"
         b"0002 2100-02-29 2100-03-31 000031 000000000500000 000000000002153 000000000500000\n"
         b"TOTAL 000000000006180 000000001000000\n"),
    )
    wrong = []
    for facility, expected in cases:
        result = run_case(facility, CASES["crossley"][1])
        if (*result, LAST_RUN["left"]) != (0, b"", b"", expected, []):
            wrong.append({"facility": facility, "expected": expected,
                          "actual": result, "residue": LAST_RUN["left"]})
    assert wrong == [], "\n".join(repr(item) for item in wrong)




def test_a_one_period_term_that_ends_on_the_last_calendar_day_is_built():
    """Sections 3.5 and 6.3. PANEL_FACILITY drawn 2198-12-31 for one twelve-month
    period ends on 2199-12-31 and writes 50694p."""
    facility = PANEL_FACILITY.replace(b"2022-01-31", b"2198-12-31").replace(
        b"periods 3\n", b"periods 1\n").replace(b"frequency 1\n", b"frequency 12\n")
    expected = (
        b"SCHEDULE F-TR-0000 TRADE K1 001\n"
        b"0001 2198-12-31 2199-12-31 000365 000000001000000 000000000050694 000000001000000\n"
        b"TOTAL 000000000050694 000000001000000\n")
    result = run_case(facility, CASES["crossley"][1])
    assert (*result, LAST_RUN["left"]) == (0, b"", b"", expected, []), (
        result, LAST_RUN["left"])


def test_a_two_period_term_that_ends_on_the_last_calendar_day_is_built():
    """Sections 3.5 and 6.3. The same facility drawn 2198-12-31 for two six-month
    periods ends on 2199-12-31 and writes 37916p."""
    facility = PANEL_FACILITY.replace(b"2022-01-31", b"2198-12-31").replace(
        b"periods 3\n", b"periods 2\n").replace(b"frequency 1\n", b"frequency 6\n")
    expected = (
        b"SCHEDULE F-TR-0000 TRADE K1 002\n"
        b"0001 2198-12-31 2199-06-30 000181 000000001000000 000000000025138 000000000500000\n"
        b"0002 2199-06-30 2199-12-31 000184 000000000500000 000000000012778 000000000500000\n"
        b"TOTAL 000000000037916 000000001000000\n")
    result = run_case(facility, CASES["crossley"][1])
    assert (*result, LAST_RUN["left"]) == (0, b"", b"", expected, []), (
        result, LAST_RUN["left"])


def test_a_term_drawn_in_twenty_one_ninety_nine_is_built():
    """Sections 3.5 and 6.3. PANEL_FACILITY drawn 2199-01-31 for one period ends
    2199-02-28, still inside the inclusive calendar, and writes 3888p. A guard that
    refuses every drawdown year of 2199 is wrong."""
    facility = PANEL_FACILITY.replace(b"2022-01-31", b"2199-01-31").replace(
        b"periods 3\n", b"periods 1\n")
    expected = (
        b"SCHEDULE F-TR-0000 TRADE K1 001\n"
        b"0001 2199-01-31 2199-02-28 000028 000000001000000 000000000003888 000000001000000\n"
        b"TOTAL 000000000003888 000000001000000\n")
    result = run_case(facility, CASES["crossley"][1])
    assert (*result, LAST_RUN["left"]) == (0, b"", b"", expected, []), (
        result, LAST_RUN["left"])


def test_a_mid_year_term_drawn_in_twenty_one_ninety_nine_is_built():
    """Sections 3.5 and 6.3. The same facility drawn 2199-06-30 for one period ends
    2199-07-30 and writes 4166p."""
    facility = PANEL_FACILITY.replace(b"2022-01-31", b"2199-06-30").replace(
        b"periods 3\n", b"periods 1\n")
    expected = (
        b"SCHEDULE F-TR-0000 TRADE K1 001\n"
        b"0001 2199-06-30 2199-07-30 000030 000000001000000 000000000004166 000000001000000\n"
        b"TOTAL 000000000004166 000000001000000\n")
    result = run_case(facility, CASES["crossley"][1])
    assert (*result, LAST_RUN["left"]) == (0, b"", b"", expected, []), (
        result, LAST_RUN["left"])


def test_every_first_byte_hash_comment_is_ignored_in_both_inputs():
    """Sections 2.1 and 1.5: no space is required after #; physical lines still count."""
    facility, table, expected = CASES["crossley"]
    all_seconds = b"".join(b"#" + bytes([byte]) + b" ignored\n"
                           for byte in range(256) if byte != 10)
    wrong = []
    for comments in (b"#x\n", all_seconds):
        lines = comments.count(b"\n")
        cases = (
            (comments + facility, table, (0, b"", b"", expected, [])),
            (facility, comments + table, (0, b"", b"", expected, [])),
            (comments + PANEL_FACILITY.replace(b"rate 500\n", b"rate 20001\n"), table,
             (1, b"", b"schedbuild: facility line %d: bad rate\n" % (lines + 4), None, [])),
            (facility, comments + b"nospace\n",
             (1, b"", b"schedbuild: basis line %d: malformed entry\n" % (lines + 1), None, [])),
        )
        for work, basis, want in cases:
            result = run_case(work, basis)
            got = (*result, LAST_RUN["left"])
            if got != want:
                wrong.append({"comment lines": lines, "expected": want, "actual": got})
    assert wrong == [], "\n".join(repr(item) for item in wrong)


def test_facility_line_faults_precede_an_unsuccessful_basis_table_read():
    """Section 10.1: parse facility lines before trying to read the basis table."""
    bad_rate = PANEL_FACILITY.replace(b"rate 500\n", b"rate 20001\n")
    missing_rate = PANEL_FACILITY.replace(b"rate 500\n", b"")
    missing_advance = PANEL_FACILITY.replace(b"advance 1000000\n", b"")
    layouts = ({"omit_table": True}, {"table_unreadable": True},
               {"table_is_directory": True})
    wrong = []
    for layout in layouts:
        for existing in (None, b"old schedule\n"):
            for facility, diagnostic in (
                    (bad_rate, b"schedbuild: facility line 4: bad rate\n"),
                    # A pending missing key must not beat a basis-table read failure.
                    (missing_rate, b"schedbuild: cannot read basis table\n"),
                    (missing_advance, b"schedbuild: cannot read basis table\n"),
                    # Control: valid facility lines let the same read failure surface.
                    (PANEL_FACILITY, b"schedbuild: cannot read basis table\n")):
                result = run_case(facility, CASES["crossley"][1],
                                  existing=existing, **layout)
                want = (1, b"", diagnostic, existing, [])
                got = (*result, LAST_RUN["left"])
                if got != want:
                    wrong.append({"layout": layout, "facility": facility.split(b"\n")[0],
                                  "expected": want, "actual": got})
    assert wrong == [], "\n".join(repr(item) for item in wrong)


def test_a_missing_required_facility_key_does_not_precede_an_unreadable_basis_table():
    """Section 10.1: a pending missing key must yield to cannot-read-basis-table.
    Second visible site: omit rate or periods with each unread-table layout."""
    missing_rate = PANEL_FACILITY.replace(b"rate 500\n", b"")
    missing_periods = PANEL_FACILITY.replace(b"periods 3\n", b"")
    layouts = ({"omit_table": True}, {"table_unreadable": True},
               {"table_is_directory": True})
    wrong = []
    for layout in layouts:
        for existing in (None, b"old schedule\n"):
            for facility in (missing_rate, missing_periods):
                result = run_case(facility, CASES["crossley"][1],
                                  existing=existing, **layout)
                want = (1, b"", b"schedbuild: cannot read basis table\n",
                        existing, [])
                got = (*result, LAST_RUN["left"])
                if got != want:
                    wrong.append({"layout": layout,
                                  "facility": facility.split(b"\n")[0],
                                  "expected": want, "actual": got})
    assert wrong == [], "\n".join(repr(item) for item in wrong)


def test_an_unreadable_facility_file_is_reported_before_an_unreadable_basis_table():
    """Section 10.1: the facility file is opened and read before anything else, so where
    neither input can be read the facility diagnostic decides, whatever stands at the output
    path. Controls: each facility read failure alone, and each basis read failure alone
    after valid facility lines."""
    facility, table, _ = CASES["crossley"]
    facility_faults = ({"omit_facility": True}, {"facility_unreadable": True},
                       {"facility_is_directory": True})
    table_faults = ({"omit_table": True}, {"table_unreadable": True},
                    {"table_is_directory": True})
    unread_facility = b"schedbuild: cannot read facility file\n"
    unread_table = b"schedbuild: cannot read basis table\n"
    layouts = [(dict(first, **second), unread_facility)
               for first in facility_faults for second in table_faults]
    layouts += [(dict(first), unread_facility) for first in facility_faults]
    layouts += [(dict(second), unread_table) for second in table_faults]
    wrong = []
    for layout, diagnostic in layouts:
        for existing in (None, b"old schedule\n"):
            result = run_case(facility, table, existing=existing, **layout)
            want = (1, b"", diagnostic, existing, [])
            got = (*result, LAST_RUN["left"])
            if got != want:
                wrong.append({"layout": layout, "expected": want, "actual": got})
    assert wrong == [], "\n".join(repr(item) for item in wrong)


def test_a_facility_whose_last_line_ends_at_the_end_of_the_file_is_built():
    """Section 2.1: the last line of a file may end at the end of the file instead of at a
    line feed. The seven-line facility with frequency 1 on line 7 ending at EOF builds the
    same schedule as its line-feed form, and so does every facility with a stored schedule
    once its final line feeds are taken off."""
    # TRADE is actual/360. Periods end 2022-02-28, 03-31 and 04-30: 28, 31 and 30 days.
    # Principal is 1000000 // 3 = 333333, the last period taking the remainder: 333334.
    # 1000000*500*28 = 14000000000 gives 3888p and carries 3200000;
    # 666667*500*31 + 3200000 = 10336538500 gives 2871p and carries 938500;
    # 333334*500*30 + 938500 = 5000948500 gives 1389p.
    expected = (
        b"SCHEDULE F-TR-0000 TRADE K1 003\n"
        b"0001 2022-01-31 2022-02-28 000028 000000001000000 000000000003888 000000000333333\n"
        b"0002 2022-02-28 2022-03-31 000031 000000000666667 000000000002871 000000000333333\n"
        b"0003 2022-03-31 2022-04-30 000030 000000000333334 000000000001389 000000000333334\n"
        b"TOTAL 000000000008148 000000001000000\n")
    unterminated = PANEL_FACILITY[:-1]
    assert unterminated.endswith(b"\nfrequency 1")
    table = CASES["crossley"][1]
    runs = [("line 7 ends in a line feed", PANEL_FACILITY, table, expected),
            ("line 7 ends at EOF", unterminated, table, expected)]
    for name, (facility, basis, schedule) in sorted(CASES.items()):
        if schedule is not None:
            runs.append((name + " without final line feeds", facility.rstrip(b"\n"), basis,
                         schedule))
    wrong = []
    for label, facility, basis, schedule in runs:
        result = run_case(facility, basis)
        got = (*result, LAST_RUN["left"])
        if got != (0, b"", b"", schedule, []):
            wrong.append({"case": label, "status": got[0], "stderr": got[2][:200],
                          "schedule matches": got[3] == schedule, "residue": got[4]})
    assert wrong == [], "\n".join(repr(item) for item in wrong)


def test_preexisting_neighbors_are_not_available_as_staging_files():
    """Sections 1.6 and 1.7: collide with observed names, not just known spellings.

    A name created in a system temporary directory is planted there as another
    user's file. The parent is writable and not sticky, so removing that file
    through the parent is the collision this run rejects. A unique temporary
    name, or an exclusive create that leaves the existing file alone, still
    completes.
    """
    facility, table, expected = CASES["crossley"]
    probe = run_case(facility, table, observe_creations=True)
    assert (*probe, LAST_RUN["left"]) == (0, b"", b"", expected, []), (
        probe, LAST_RUN["left"])
    observed = LAST_RUN["created in run tree"]
    filed = {".schedbuild.tmp": b"KEEP\n",
             ".tmp": b"keep the starting directory's draft\n",
             "issued/.tmp": b"KEEP\n",
             "issued/.schedbuild.tmp": b"KEEP\n",
             "issued/schedbuild.tmp": b"keep the other builder's draft\n",
             "issued/sched.out.tmp": b"keep the output draft\n",
             "issued/.sched.out.tmp": b"keep the hidden output draft\n"}
    # Prepopulate every observed filename in a fresh run with the same operands.
    # A fixed name of any spelling must now spare this independently chosen text;
    # unique names and exclusive-create/retry writers can still complete normally.
    # Absolute names are outside the run tree. They stay in GLOBAL_COLLISIONS and
    # are planted root-owned; putting them in beside would file them under the tree.
    for name in observed:
        if os.path.isabs(name):
            GLOBAL_COLLISIONS.add(name)
            continue
        filed.setdefault(name, b"KEEP observed staging neighbor\n")
    wrong = []
    # Exercise both publication and rename failure after the stage was written.
    for directory in (False, True):
        result = run_case(facility, table, beside=filed, output_is_directory=directory)
        want = ((1, b"", b"schedbuild: cannot write output file\n", NOT_A_FILE, [])
                if directory else (0, b"", b"", expected, []))
        got = (*result, LAST_RUN["left"])
        if got != want or (directory and not LAST_RUN["empty directory"]):
            wrong.append({"observed names": observed, "directory output": directory,
                          "expected": want, "actual": got})
    assert wrong == [], "\n".join(repr(item) for item in wrong)


def test_readable_inputs_and_neighbors_keep_their_original_owner_and_group():
    """P179, section 1.7: byte/mode-preserving replacement must preserve UID/GID too."""
    facility, table, expected = CASES["crossley"]
    filed = {"issued/keep.txt": b"KEEP\n"}
    bad_facility = PANEL_FACILITY + b"nospace\n"
    wrong = []
    # The three readable mode-0644 files have sandbox-writable parents. Vary
    # owner and group independently, after stage_run's recursive own(tree).
    for owner in ((0, 0), (0, SANDBOX_GID), (SANDBOX_UID, 0)):
        owners = {name: owner for name in (PATHS[0], PATHS[1], "issued/keep.txt")}
        for work, want in (
                (facility, (0, b"", b"", expected, [])),
                (bad_facility, (1, b"", b"schedbuild: facility line 8: malformed entry\n",
                                None, []))):
            result = run_case(work, table, beside=filed, file_owners=owners)
            got = (*result, LAST_RUN["left"])
            if got != want:
                wrong.append({"initial UID/GID": owner, "expected": want, "actual": got})
    assert wrong == [], "\n".join(repr(item) for item in wrong)


def test_semantic_term_faults_precede_an_output_writability_preflight():
    """P174, section 10.1: term checks precede writing into a mode-0555 parent.

    A missing rate with a date stepped twice inside the term is refused for the
    missing key, which 10.1 sets before the repeated step date.
    """
    calendar_overrun = PANEL_FACILITY.replace(b"2022-01-31", b"2199-12-31")
    calendar_overrun = calendar_overrun.replace(b"periods 3\n", b"periods 1\n")
    cases = (
        (calendar_overrun, b"schedbuild: term past the calendar\n"),
        (PANEL_FACILITY + b"step 2022-05-01 700\n",
         b"schedbuild: step out of term\n"),
        (PANEL_FACILITY + b"step 2022-02-15 700\nstep 2022-02-15 800\n",
         b"schedbuild: repeated step date\n"),
        (PANEL_FACILITY.replace(b"rate 500\n", b"")
         + b"step 2022-02-15 700\nstep 2022-02-15 800\n",
         b"schedbuild: missing key\n"),
        # Control: valid work reaches the write refusal in the same layout.
        (PANEL_FACILITY, b"schedbuild: cannot write output file\n"),
    )
    wrong = []
    for facility, diagnostic in cases:
        for existing in (None, b"old schedule\n"):
            result = run_case(facility, CASES["crossley"][1], existing=existing,
                              output_directory_unwritable=True)
            want = (1, b"", diagnostic, existing, [])
            got = (*result, LAST_RUN["left"])
            if got != want:
                wrong.append({"expected": want, "actual": got})
    assert wrong == [], "\n".join(repr(item) for item in wrong)


def test_june_and_november_31_are_refused_in_drawdowns_and_steps():
    """P156/P158/P162, sections 3.5-3.6: both omitted thirty-day guards bite."""
    for date in (b"2022-06-31", b"2022-11-31"):
        drawn = PANEL_FACILITY.replace(b"2022-01-31", date)
        # A twelve-month term keeps the normalized dates inside the term too.
        stepped = PANEL_FACILITY.replace(b"periods 3\n", b"periods 12\n")
        stepped += b"step " + date + b" 700\n"
        for facility, diagnostic in (
                (drawn, b"schedbuild: facility line 5: bad date\n"),
                (stepped, b"schedbuild: facility line 8: bad step\n")):
            result = run_case(facility, CASES["crossley"][1])
            assert (*result, LAST_RUN["left"]) == (1, b"", diagnostic, None, []), (
                date, diagnostic, result, LAST_RUN["left"])


def test_valid_work_does_not_create_missing_output_ancestors():
    """P166, section 1.6: valid inputs reach writing beneath an absent parent."""
    facility, table, _ = CASES["crossley"]
    for output in ("missing/schedule.out", "missing/deeper/schedule.out"):
        # output= overrides the operand after stage_run has made the ordinary
        # PATHS parents; neither missing nor missing/deeper is staged.
        result = run_case(facility, table, output=output)
        assert (*result, LAST_RUN["left"]) == (
            1, b"", b"schedbuild: cannot write output file\n", None, []), (
                output, result, LAST_RUN["left"])
        # LAST_RUN residue includes directories, even newly made empty ones.


def test_a_malformed_duplicate_basis_row_reports_format_before_repetition():
    """P167, section 10.1: two faults on the same row, not competing rows."""
    ordinary_rows = b"K2 actual 365\nK4 thirty-us 360\nK5 thirty-us 365\nK7 thirty-e 360\nK8 thirty-e 365\n"
    for malformed in (b"K1 actual 366", b"K1 unknown 360", b"K1  actual 360",
                      b"K1 actual 360 extra"):
        table = b"K1 actual 360\n" + malformed + b"\n" + ordinary_rows
        result = run_case(PANEL_FACILITY, table)
        assert (*result, LAST_RUN["left"]) == (
            1, b"", b"schedbuild: basis line 2: malformed entry\n", None, []), (
                malformed, result, LAST_RUN["left"])


def test_facility_parse_refusal_preserves_its_aliased_output():
    """P140/P143, sections 1.7 and 9.5: parsing fails before reading the table."""
    facility = PANEL_FACILITY + b"nospace\n"
    result = run_case(facility, CASES["crossley"][1],
                      names=("papers/f.lst", "ref/table.dat", "papers/f.lst"))
    assert (*result, LAST_RUN["left"]) == (
        1, b"", b"schedbuild: facility line 8: malformed entry\n", facility, []), (
            result, LAST_RUN["left"])


def test_each_missing_key_refusal_preserves_an_existing_output():
    """P157, section 9.5: the missing-advance case and every other required key.

    Section 10.1 also puts a missing key before two step lines carrying one
    date, so the same refusal comes back when 2022-02-15 is stepped twice.
    """
    stale = b"old schedule\n"
    lines = PANEL_FACILITY.splitlines(keepends=True)
    for omitted in lines:
        facility = b"".join(line for line in lines if line != omitted)
        for tail in (b"", b"step 2022-02-15 700\nstep 2022-02-15 800\n"):
            result = run_case(facility + tail, CASES["crossley"][1], existing=stale)
            assert (*result, LAST_RUN["left"]) == (
                1, b"", b"schedbuild: missing key\n", stale, []), (
                    omitted, tail, result, LAST_RUN["left"])


def test_a_basis_table_with_seven_significant_lines_is_incomplete():
    """Paragraph 4.3: seven significant lines that carry every pairing still leave the
    table incomplete, even when the six pairings are all present."""
    facility = (
        b"facility F-BR-TBL7\nclass BRIDGE\nadvance 1000000\nrate 500\n"
        b"drawn 2000-01-31\nperiods 2\nfrequency 1\n")
    table = (
        b"K1 actual 360\nK2 actual 365\nK4 thirty-us 360\n"
        b"K5 thirty-us 365\nK7 thirty-e 360\nK8 thirty-e 365\n"
        b"Q9 actual 360\n")
    result = run_case(facility, table)
    assert result[:3] == (1, b"", b"schedbuild: basis table incomplete\n"), result[:3]
    assert result[3] is None


def test_a_basis_table_with_six_lines_but_five_pairings_is_incomplete():
    """Paragraph 4.3: six significant lines are not enough when two of them carry the
    same pairing under different identifiers."""
    facility = (
        b"facility F-BR-TBL5\nclass BRIDGE\nadvance 1000000\nrate 500\n"
        b"drawn 2000-01-31\nperiods 2\nfrequency 1\n")
    table = (
        b"K1 actual 360\nK2 actual 365\nK4 thirty-us 360\n"
        b"K5 thirty-us 365\nK7 thirty-e 360\nZ9 thirty-e 360\n")
    result = run_case(facility, table)
    assert result[:3] == (1, b"", b"schedbuild: basis table incomplete\n"), result[:3]
    assert result[3] is None


def test_an_incomplete_basis_table_is_reported_before_a_missing_key():
    """Section 10.1: the six pairings are checked before a key of 2.4 standing on
    no significant line."""
    facility = PANEL_FACILITY.replace(b"rate 500\n", b"")
    incomplete = (
        b"K1 actual 360\nK2 actual 365\nK4 thirty-us 360\n"
        b"K5 thirty-us 365\nK7 thirty-e 360\n")
    stale = b"old schedule\n"
    result = run_case(facility, incomplete, existing=stale)
    assert (*result, LAST_RUN["left"]) == (
        1, b"", b"schedbuild: basis table incomplete\n", stale, []), (
            result, LAST_RUN["left"])
    result = run_case(facility, incomplete)
    assert (*result, LAST_RUN["left"]) == (
        1, b"", b"schedbuild: basis table incomplete\n", None, []), (
            result, LAST_RUN["left"])


def test_the_tool_directory_yields_a_program_the_verifier_can_run():
    """Grading takes the module file and the regular Go sources of the tool directory."""
    regular = sorted(entry.name for entry in SOURCE_DIR.iterdir()
                     if entry.is_file() and not entry.is_symlink()
                     and (entry.name == "go.mod" or entry.suffix == ".go"))
    assert SOURCE_DIR.is_dir() and sorted(BUILDER.staged) == regular, regular
    assert "go.mod" in regular and any(name.endswith(".go") for name in regular), regular
    require_binary()
    assert BUILDER.binary[:4] == b"\x7fELF", BUILDER.binary[:16]


def test_held_out_facilities_of_every_class_are_built_exactly():
    """Paragraph 5.1, which the note leaves to the agreed schedules: one facility of each
    class, sharing no reference, advance, rate, drawdown or term with a settled one."""
    classes = set()
    for name in HELD_OUT:
        written, expected = build_case(name)
        assert written == expected, "%s differs at byte %d of %d" % (
            name, first_difference(written, expected), len(expected))
        classes.add(parse(written)["header"][2])
        assert all(written != CASES[settled][2] for settled in SETTLED), name
    assert classes == {"BRIDGE", "INVOICE", "PLANT", "TRADE", "WORKING"}, classes


def test_the_settled_schedules_are_reproduced_byte_for_byte():
    """The five schedules the desk agreed with its borrowers are rebuilt exactly."""
    for name in SETTLED:
        written, expected = build_case(name)
        assert written == expected, "%s differs at byte %d of %d" % (
            name, first_difference(written, expected), len(expected))


def test_the_schedule_is_shaped_as_section_9_writes_it():
    """Paragraphs 9.2 to 9.4: the five header fields, the width of every column, and a trailer
    that sums the two columns above it."""
    for name in ("tarnside", "kelso", "quarrell", "nantwich", "yelverton", "zennor",
                 "crossley", "foxdale", "lindale", "oxton", "walmsgate", "rushton", "xelston"):
        written, expected = build_case(name)
        assert written == expected, name
        volume = parse(written)
        header, trailer = volume["header"], volume["trailer"]
        assert len(header) == 5 and header[0] == "SCHEDULE", (name, header)
        assert header[2] in ("BRIDGE", "INVOICE", "PLANT", "TRADE", "WORKING"), name
        assert 1 <= len(header[1]) <= 16, (name, header[1])
        assert len(header[3]) == 2 and header[3][0].isupper() and header[3][1].isdigit(), name
        assert len(header[4]) == 3 and int(header[4]) == len(volume["rows"]), name
        for row in volume["rows"]:
            assert row["widths"] == [4, 10, 10, 6, 15, 15, 15], (name, row["widths"])
            assert row["days"] > 0, (name, row["number"])
        assert trailer[0] == "TOTAL" and len(trailer) == 3, (name, trailer)
        assert all(len(field) == 15 for field in trailer[1:]), name
        assert int(trailer[1]) == sum(row["interest"] for row in volume["rows"]), name
        assert int(trailer[2]) == sum(row["principal"] for row in volume["rows"]), name


def test_the_periods_run_and_the_money_falls_out_as_sections_6_to_8_have_it():
    """Period ends taken from the month of the drawdown, including both ends of the calendar
    3.5 reaches and drawdowns on the last day of April, September and November, the months no
    other case is drawn at the end of; the balance falling by the principal until the advance
    clears; the remainder carried out of one period deciding the pennies of the next; and a
    rate change inside a period reaching a day count the whole period never gives."""
    for name in ("kelso", "lindale", "stanlow", "merrow", "pendle", "garsdale", "hutton",
                 "ormside", "sedbergh", "tebay",
                 "tarnside", "quarrell", "nantwich", "vowchurch", "foxdale", "harrowby",
                 "rushton", "northgate", "upperby", "yelverton", "zennor"):
        written, expected = build_case(name)
        assert written == expected, "%s differs at byte %d" % (
            name, first_difference(written, expected))
        rows = parse(written)["rows"]
        assert rows[-1]["end"] <= "2199-12-31", name
        for earlier, later in zip(rows, rows[1:]):
            assert earlier["end"] == later["begin"], name
            assert later["balance"] == earlier["balance"] - earlier["principal"], name
        assert rows[-1]["balance"] == rows[-1]["principal"], name
        even = rows[0]["principal"]
        assert all(row["principal"] == even for row in rows[:-1]), name
        assert rows[-1]["principal"] >= even, name
    rows = parse(build_case("oxton")[0])["rows"]
    paid = [row for row in rows if row["interest"] > 0]
    assert 0 < len(paid) < len(rows) and all(row["balance"] == 1 for row in rows), len(paid)
    rows = parse(build_case("foxdale")[0])["rows"]
    assert [row for row in rows if row["days"] != thirty_us(row["begin"], row["end"])], (
        "no period of foxdale reports a count the whole period does not give")
    digest_cases()


def test_the_identifier_is_taken_from_the_table_the_builder_is_given():
    """Paragraphs 5.2 and 4.1: the label is the one the table in front of the builder gives,
    and it is read at both ends of both of its bytes, the agreed schedule standing still."""
    for name in ("upperby", "xelston", "arncliffe", "braithwell"):
        written, expected = build_case(name)
        assert written == expected, name
    reissued = parse(build_case("upperby")[0])["header"][3]
    shipped = parse(build_case("crossley")[0])["header"][3]
    assert reissued != shipped, "the same class reported %s under both tables" % reissued
    code, stdout, stderr, written = run_case(CASES["upperby"][0], CASES["crossley"][1])
    assert (code, stdout, stderr) == (0, b"", b"") and parse(written)["header"][3] == shipped
    facility, table, expected = CASES["crossley"]
    rows = table.split(b"\n")
    carried = expected.split(b"\n", 1)[0].split(b" ")[3]
    standing = set(row.split(b" ")[0] for row in rows)
    for label in (b"A0", b"A9", b"Z0", b"Z9"):
        assert carried in standing and label not in standing, (carried, sorted(standing))
        relabelled = b"\n".join(label + row[len(carried):]
                                if row.split(b" ")[0] == carried else row for row in rows)
        code, stdout, stderr, written = run_case(facility, relabelled)
        assert (code, stdout, stderr, LAST_RUN["left"]) == (0, b"", b"", []), (label, stderr[:200])
        assert written == expected.replace(b" " + carried + b" ", b" " + label + b" ", 1), label


STEP_TABLE = (
    b"K1 actual 360\nK2 actual 365\nK4 thirty-us 360\n"
    b"K5 thirty-us 365\nK7 thirty-e 360\nK8 thirty-e 365\n")


def steps_on_each_fifteenth(last_rate):
    """Working facility, 480 months from 2031-03-31, and 257 step lines.

    One step stands on the 15th of each month from 2031-04 through 2052-08.
    The first 256 of them carry rate 1. The last carries last_rate, and it
    falls inside the period that ends on 2052-08-31. Section 2.4 allows any
    number of step lines, so a store that keeps 256 and drops the rest is wrong
    even though it still validates the dropped line.
    """
    lines = ["facility F-STEP-257", "class WORKING", "advance 9876543210", "rate 725",
             "drawn 2031-03-31", "periods 480", "frequency 1"]
    year, month = 2031, 4
    for index in range(257):
        rate = 1 if index < 256 else last_rate
        lines.append("step %04d-%02d-15 %d" % (year, month, rate))
        month += 1
        if month == 13:
            month = 1
            year += 1
    assert lines[-1] == "step 2052-08-15 %d" % last_rate
    return ("\n".join(lines) + "\n").encode("latin-1")


def test_a_step_past_two_hundred_fifty_six_changes_the_august_2052_period():
    """Section 2.4. The 257th step is rate 20000 on 2052-08-15.

    WORKING is thirty-us/365. That step splits the period 2052-07-31 to
    2052-08-31, so the reported day count is 31 and the interest is 404100358p.
    Keeping only the first 256 steps leaves the period whole, at 30 days, and
    still accruing at rate 1.
    """
    facility = steps_on_each_fifteenth(20000)
    code, stdout, stderr, written = run_case(facility, STEP_TABLE)
    assert (code, stdout, stderr, LAST_RUN["left"]) == (0, b"", b"", []), stderr[:300]
    rows = written.splitlines()
    assert len(rows) == 482 and rows[0] == b"SCHEDULE F-STEP-257 WORKING K5 480"
    assert rows[257] == (
        b"0257 2052-07-31 2052-08-31 000031 000004609053674 "
        b"000000404100358 000000020576131")
    assert rows[-1] == b"TOTAL 000085156984293 000009876543210"


def test_a_zero_rate_on_the_two_hundred_fifty_seventh_step_is_applied():
    """Section 2.4 again, with the 257th step at rate 0 rather than 20000.

    The same period still splits, so its day count stays 31, and its interest
    is 18941p. The total interest of the term is 44961897p. Dropping that step
    keeps every later period at rate 1.
    """
    facility = steps_on_each_fifteenth(0)
    code, stdout, stderr, written = run_case(facility, STEP_TABLE)
    assert (code, stdout, stderr, LAST_RUN["left"]) == (0, b"", b"", []), stderr[:300]
    rows = written.splitlines()
    assert len(rows) == 482
    assert rows[257] == (
        b"0257 2052-07-31 2052-08-31 000031 000004609053674 "
        b"000000000018941 000000020576131")
    assert rows[-1] == b"TOTAL 000000044961897 000009876543210"


def test_a_working_period_split_in_february_reports_its_part_interest():
    """WORKING term: after the 15th carries 20000, 2022-01-31 to 2022-02-28 is
    accrued in parts under the remainder rule of 8.4, and
    the period interest is 73286p."""
    facility = (
        b"facility F-WK-PART\nclass WORKING\nadvance 1000000\nrate 500\n"
        b"drawn 2022-01-31\nperiods 3\nfrequency 1\nstep 2022-02-15 20000\n")
    code, stdout, stderr, written = run_case(facility, STEP_TABLE)
    assert (code, stdout, stderr, LAST_RUN["left"]) == (0, b"", b"", []), stderr[:300]
    rows = written.splitlines()
    assert rows[0] == b"SCHEDULE F-WK-PART WORKING K5 003"
    assert rows[1] == (
        b"0001 2022-01-31 2022-02-28 000028 000000001000000 "
        b"000000000073286 000000000333333")
    assert rows[-1] == b"TOTAL 000000000248629 000000001000000"


def test_a_trade_period_split_in_february_reports_its_part_interest():
    """TRADE term: after the 15th carries 700, 2022-01-31 to 2022-02-28 is accrued
    in parts under the remainder rule of 8.4, and the period
    interest is 4610p."""
    facility = (
        b"facility F-TR-PART\nclass TRADE\nadvance 1000000\nrate 500\n"
        b"drawn 2022-01-31\nperiods 3\nfrequency 1\nstep 2022-02-15 700\n")
    code, stdout, stderr, written = run_case(facility, STEP_TABLE)
    assert (code, stdout, stderr, LAST_RUN["left"]) == (0, b"", b"", []), stderr[:300]
    rows = written.splitlines()
    assert rows[0] == b"SCHEDULE F-TR-PART TRADE K1 003"
    assert rows[1] == (
        b"0001 2022-01-31 2022-02-28 000028 000000001000000 "
        b"000000000004610 000000000333333")
    assert rows[-1] == b"TOTAL 000000000010573 000000001000000"


def test_a_trade_quarter_through_february_twenty_one_hundred_counts_ninety():
    """TRADE is actual/360. A quarterly period from 2100-01-31 to 2100-04-30 holds
    the twenty-ninth of February under 3.5,
    so the day count is 90 and the interest is 86400p. A Gregorian calendar yields
    89 days and a different schedule."""
    facility = (
        b"facility F-TR-Q2100\nclass TRADE\nadvance 4800000\nrate 720\n"
        b"drawn 2100-01-31\nperiods 2\nfrequency 3\n")
    expected = (
        b"SCHEDULE F-TR-Q2100 TRADE K1 002\n"
        b"0001 2100-01-31 2100-04-30 000090 000000004800000 000000000086400 000000002400000\n"
        b"0002 2100-04-30 2100-07-31 000092 000000002400000 000000000044160 000000002400000\n"
        b"TOTAL 000000000130560 000000004800000\n")
    result = run_case(facility, STEP_TABLE)
    assert (*result, LAST_RUN["left"]) == (0, b"", b"", expected, []), (
        result, LAST_RUN["left"])


def test_a_trade_quarter_through_february_twenty_one_hundred_and_four_counts_ninety():
    """The same quarterly shape in 2104: the day count through February is again
    90 under 3.5."""
    facility = (
        b"facility F-TR-Q2104\nclass TRADE\nadvance 4800000\nrate 720\n"
        b"drawn 2104-01-31\nperiods 2\nfrequency 3\n")
    expected = (
        b"SCHEDULE F-TR-Q2104 TRADE K1 002\n"
        b"0001 2104-01-31 2104-04-30 000090 000000004800000 000000000086400 000000002400000\n"
        b"0002 2104-04-30 2104-07-31 000092 000000002400000 000000000044160 000000002400000\n"
        b"TOTAL 000000000130560 000000004800000\n")
    result = run_case(facility, STEP_TABLE)
    assert (*result, LAST_RUN["left"]) == (0, b"", b"", expected, []), (
        result, LAST_RUN["left"])


def test_the_year_twenty_one_hundred_and_four_leap_day_is_valid_in_drawdowns_and_steps():
    """Section 3.5: 2104 divides by four within the stated calendar, matching the
    leap years of 3.5. A century-only Gregorian special case for 2100
    does not cover it."""
    one_period = PANEL_FACILITY.replace(b"periods 3\n", b"periods 1\n")
    drawn = one_period.replace(b"2022-01-31", b"2104-02-29")
    stepped = one_period.replace(b"2022-01-31", b"2104-02-01") + b"step 2104-02-29 500\n"
    cases = (
        (drawn, b"SCHEDULE F-TR-0000 TRADE K1 001\n"
         b"0001 2104-02-29 2104-03-29 000029 000000001000000 000000000004027 000000001000000\n"
         b"TOTAL 000000000004027 000000001000000\n"),
        (stepped, b"SCHEDULE F-TR-0000 TRADE K1 001\n"
         b"0001 2104-02-01 2104-03-01 000029 000000001000000 000000000004026 000000001000000\n"
         b"TOTAL 000000000004026 000000001000000\n"),
    )
    wrong = []
    for facility, expected in cases:
        result = run_case(facility, CASES["crossley"][1])
        if (*result, LAST_RUN["left"]) != (0, b"", b"", expected, []):
            wrong.append({"facility": facility, "expected": expected,
                          "actual": result, "residue": LAST_RUN["left"]})
    assert wrong == [], "\n".join(repr(item) for item in wrong)



def test_a_bridge_period_that_ends_on_the_leap_day_uses_three_hundred_sixty_six():
    """BRIDGE period ending on 2000-02-29: under 7.5 the leap day on the second date counts, so the division uses 366
    and period interest is 3961p. An exclusive-end leap test yields 3972p."""
    facility = (
        b"facility F-BR-END\nclass BRIDGE\nadvance 1000000\nrate 500\n"
        b"drawn 2000-01-31\nperiods 2\nfrequency 1\n")
    expected = (
        b"SCHEDULE F-BR-END BRIDGE K2 002\n"
        b"0001 2000-01-31 2000-02-29 000029 000000001000000 000000000003961 000000000500000\n"
        b"0002 2000-02-29 2000-03-31 000031 000000000500000 000000000002118 000000000500000\n"
        b"TOTAL 000000000006079 000000001000000\n")
    result = run_case(facility, STEP_TABLE)
    assert (*result, LAST_RUN["left"]) == (0, b"", b"", expected, []), (
        result, LAST_RUN["left"])


def test_a_bridge_period_that_ends_on_the_twenty_one_hundred_leap_day_uses_three_hundred_sixty_six():
    """The same shape in 2100: ending on 2100-02-29 uses 366 under the recovered
    endpoint rule, with period interest 3961p."""
    facility = (
        b"facility F-BR-END21\nclass BRIDGE\nadvance 1000000\nrate 500\n"
        b"drawn 2100-01-31\nperiods 2\nfrequency 1\n")
    expected = (
        b"SCHEDULE F-BR-END21 BRIDGE K2 002\n"
        b"0001 2100-01-31 2100-02-29 000029 000000001000000 000000000003961 000000000500000\n"
        b"0002 2100-02-29 2100-03-31 000031 000000000500000 000000000002118 000000000500000\n"
        b"TOTAL 000000000006079 000000001000000\n")
    result = run_case(facility, STEP_TABLE)
    assert (*result, LAST_RUN["left"]) == (0, b"", b"", expected, []), (
        result, LAST_RUN["left"])


def test_a_bridge_period_that_ends_on_the_twenty_one_hundred_and_four_leap_day_uses_three_hundred_sixty_six():
    """BRIDGE actual/365 in 2104: ending on 2104-02-29 uses 366. A leap-day lookup
    for the denominator that stops at 2100 yields 3972p instead of 3961p."""
    facility = (
        b"facility F-BR-2104\nclass BRIDGE\nadvance 1000000\nrate 500\n"
        b"drawn 2104-01-31\nperiods 2\nfrequency 1\n")
    expected = (
        b"SCHEDULE F-BR-2104 BRIDGE K2 002\n"
        b"0001 2104-01-31 2104-02-29 000029 000000001000000 000000000003961 000000000500000\n"
        b"0002 2104-02-29 2104-03-31 000031 000000000500000 000000000002118 000000000500000\n"
        b"TOTAL 000000000006079 000000001000000\n")
    result = run_case(facility, STEP_TABLE)
    assert (*result, LAST_RUN["left"]) == (0, b"", b"", expected, []), (
        result, LAST_RUN["left"])


def test_a_bridge_period_that_ends_on_the_twenty_one_hundred_and_eight_leap_day_uses_three_hundred_sixty_six():
    """The same BRIDGE shape in 2108: the actual/365 leap denominator must reach
    past 2100 across the contractual calendar, with period interest 3961p."""
    facility = (
        b"facility F-BR-2108\nclass BRIDGE\nadvance 1000000\nrate 500\n"
        b"drawn 2108-01-31\nperiods 2\nfrequency 1\n")
    expected = (
        b"SCHEDULE F-BR-2108 BRIDGE K2 002\n"
        b"0001 2108-01-31 2108-02-29 000029 000000001000000 000000000003961 000000000500000\n"
        b"0002 2108-02-29 2108-03-31 000031 000000000500000 000000000002118 000000000500000\n"
        b"TOTAL 000000000006079 000000001000000\n")
    result = run_case(facility, STEP_TABLE)
    assert (*result, LAST_RUN["left"]) == (0, b"", b"", expected, []), (
        result, LAST_RUN["left"])


def test_a_bridge_leap_period_split_ending_on_the_leap_day_counts_the_endpoint():
    """BRIDGE split on 2000-02-15 inside a period that ends on the leap day: the
    part that ends on 2000-02-29 counts that leap day, discards the earlier
    remainder, and yields period interest 196788p."""
    facility = (
        b"facility F-BR-ENDSPLIT\nclass BRIDGE\nadvance 5000000\nrate 1200\n"
        b"drawn 2000-01-31\nperiods 2\nfrequency 1\nstep 2000-02-15 9000\n")
    expected = (
        b"SCHEDULE F-BR-ENDSPLIT BRIDGE K2 002\n"
        b"0001 2000-01-31 2000-02-29 000029 000000005000000 000000000196788 000000002500000\n"
        b"0002 2000-02-29 2000-03-31 000031 000000002500000 000000000190573 000000002500000\n"
        b"TOTAL 000000000387361 000000005000000\n")
    result = run_case(facility, STEP_TABLE)
    assert (*result, LAST_RUN["left"]) == (0, b"", b"", expected, []), (
        result, LAST_RUN["left"])


def test_a_bridge_leap_period_split_ending_on_the_twenty_one_hundred_leap_day_counts_the_endpoint():
    """The same split ending on 2100-02-29: endpoint leap inclusion and discard
    together give period interest 196788p."""
    facility = (
        b"facility F-BR-ENDSP21\nclass BRIDGE\nadvance 5000000\nrate 1200\n"
        b"drawn 2100-01-31\nperiods 2\nfrequency 1\nstep 2100-02-15 9000\n")
    expected = (
        b"SCHEDULE F-BR-ENDSP21 BRIDGE K2 002\n"
        b"0001 2100-01-31 2100-02-29 000029 000000005000000 000000000196788 000000002500000\n"
        b"0002 2100-02-29 2100-03-31 000031 000000002500000 000000000190573 000000002500000\n"
        b"TOTAL 000000000387361 000000005000000\n")
    result = run_case(facility, STEP_TABLE)
    assert (*result, LAST_RUN["left"]) == (0, b"", b"", expected, []), (
        result, LAST_RUN["left"])


def test_a_bridge_leap_period_split_before_the_leap_day_discards_the_first_remainder():
    """BRIDGE period 2000-01-31 to 2000-02-29 with a step on 2000-02-20 and a high
    rate: the part before the leap day uses 365 and the part that holds it uses
    366, and only the last part's remainder leaves. Period interest is 143531p."""
    facility = (
        b"facility F-BR-HOLD1\nclass BRIDGE\nadvance 5000000\nrate 1200\n"
        b"drawn 2000-01-31\nperiods 2\nfrequency 1\nstep 2000-02-20 9000\n")
    expected = (
        b"SCHEDULE F-BR-HOLD1 BRIDGE K2 002\n"
        b"0001 2000-01-31 2000-02-29 000029 000000005000000 000000000143531 000000002500000\n"
        b"0002 2000-02-29 2000-03-31 000031 000000002500000 000000000190574 000000002500000\n"
        b"TOTAL 000000000334105 000000005000000\n")
    result = run_case(facility, STEP_TABLE)
    assert (*result, LAST_RUN["left"]) == (0, b"", b"", expected, []), (
        result, LAST_RUN["left"])


def test_a_bridge_leap_period_split_before_the_twenty_one_hundred_leap_day_discards_the_first_remainder():
    """The same shape in 2100: step on 2100-02-20, first part at 365 and the leap
    part at 366, with the non-last remainder discarded. Period interest is 143531p."""
    facility = (
        b"facility F-BR-HOLD2\nclass BRIDGE\nadvance 5000000\nrate 1200\n"
        b"drawn 2100-01-31\nperiods 2\nfrequency 1\nstep 2100-02-20 9000\n")
    expected = (
        b"SCHEDULE F-BR-HOLD2 BRIDGE K2 002\n"
        b"0001 2100-01-31 2100-02-29 000029 000000005000000 000000000143531 000000002500000\n"
        b"0002 2100-02-29 2100-03-31 000031 000000002500000 000000000190574 000000002500000\n"
        b"TOTAL 000000000334105 000000005000000\n")
    result = run_case(facility, STEP_TABLE)
    assert (*result, LAST_RUN["left"]) == (0, b"", b"", expected, []), (
        result, LAST_RUN["left"])


def test_a_bridge_leap_period_split_twice_discards_every_remainder_but_the_last():
    """Two steps inside the 2000 leap period give three parts. Only the last part
    keeps a remainder; the first two discards and the per-part year lengths make
    period interest 268177p."""
    facility = (
        b"facility F-BR-TRIPLE\nclass BRIDGE\nadvance 8000000\nrate 1000\n"
        b"drawn 2000-01-31\nperiods 2\nfrequency 1\n"
        b"step 2000-02-10 5000\nstep 2000-02-22 7500\n")
    expected = (
        b"SCHEDULE F-BR-TRIPLE BRIDGE K2 002\n"
        b"0001 2000-01-31 2000-02-29 000029 000000008000000 000000000268177 000000004000000\n"
        b"0002 2000-02-29 2000-03-31 000031 000000004000000 000000000254098 000000004000000\n"
        b"TOTAL 000000000522275 000000008000000\n")
    result = run_case(facility, STEP_TABLE)
    assert (*result, LAST_RUN["left"]) == (0, b"", b"", expected, []), (
        result, LAST_RUN["left"])


def test_a_remainder_carried_into_a_bridge_leap_split_is_applied_only_to_the_first_part():
    """A remainder leaves the December 1999 period and enters the leap split on
    2000-02-20. It is added only to the first part of that period; later parts
    discard their own remainders. Period 2 interest is 95688p."""
    facility = (
        b"facility F-BR-CARRY\nclass BRIDGE\nadvance 5000000\nrate 1200\n"
        b"drawn 1999-12-31\nperiods 3\nfrequency 1\nstep 2000-02-20 9000\n")
    expected = (
        b"SCHEDULE F-BR-CARRY BRIDGE K2 003\n"
        b"0001 1999-12-31 2000-01-31 000031 000000005000000 000000000050958 000000001666666\n"
        b"0002 2000-01-31 2000-02-29 000029 000000003333334 000000000095688 000000001666666\n"
        b"0003 2000-02-29 2000-03-31 000031 000000001666668 000000000127049 000000001666668\n"
        b"TOTAL 000000000273695 000000005000000\n")
    result = run_case(facility, STEP_TABLE)
    assert (*result, LAST_RUN["left"]) == (0, b"", b"", expected, []), (
        result, LAST_RUN["left"])




def test_an_invoice_span_from_april_thirtieth_to_may_thirty_first_counts_thirty():
    """Paragraph 6.6. INVOICE drawn 2021-03-31:
    period 2 runs 2021-04-30 to 2021-05-31. D1 is already 30, so D2 adjusts,
    the count is 30 and that period's interest is 2084p."""
    facility = (
        b"facility F-IN-3031\nclass INVOICE\nadvance 1000000\nrate 500\n"
        b"drawn 2021-03-31\nperiods 2\nfrequency 1\n")
    expected = (
        b"SCHEDULE F-IN-3031 INVOICE K4 002\n"
        b"0001 2021-03-31 2021-04-30 000030 000000001000000 000000000004166 000000000500000\n"
        b"0002 2021-04-30 2021-05-31 000030 000000000500000 000000000002084 000000000500000\n"
        b"TOTAL 000000000006250 000000001000000\n")
    result = run_case(facility, CASES["crossley"][1])
    assert (*result, LAST_RUN["left"]) == (0, b"", b"", expected, []), (
        result, LAST_RUN["left"])


def test_an_invoice_span_from_september_thirtieth_to_october_thirty_first_counts_thirty():
    """Paragraph 6.6. INVOICE drawn 2021-08-31 for two months: period 2 runs
    2021-09-30 to 2021-10-31. D1 is already 30, so D2 adjusts, the count is 30
    and that period's interest is 2084p."""
    facility = (
        b"facility F-IN-SEP30\nclass INVOICE\nadvance 1000000\nrate 500\n"
        b"drawn 2021-08-31\nperiods 2\nfrequency 1\n")
    expected = (
        b"SCHEDULE F-IN-SEP30 INVOICE K4 002\n"
        b"0001 2021-08-31 2021-09-30 000030 000000001000000 000000000004166 000000000500000\n"
        b"0002 2021-09-30 2021-10-31 000030 000000000500000 000000000002084 000000000500000\n"
        b"TOTAL 000000000006250 000000001000000\n")
    result = run_case(facility, CASES["crossley"][1])
    assert (*result, LAST_RUN["left"]) == (0, b"", b"", expected, []), (
        result, LAST_RUN["left"])


def test_an_invoice_part_from_the_fifteenth_to_the_thirty_first_keeps_d2():
    """Paragraph 6.6. INVOICE drawn 2022-03-15 with
    a step on 2022-03-31: D1=15 and D2=31, so D2 stays 31. Reports 31 days and
    earns 4305p."""
    facility = (
        b"facility F-IN-MID31\nclass INVOICE\nadvance 1000000\nrate 500\n"
        b"drawn 2022-03-15\nperiods 1\nfrequency 1\nstep 2022-03-31 500\n")
    expected = (
        b"SCHEDULE F-IN-MID31 INVOICE K4 001\n"
        b"0001 2022-03-15 2022-04-15 000031 000000001000000 000000000004305 000000001000000\n"
        b"TOTAL 000000000004305 000000001000000\n")
    result = run_case(facility, CASES["crossley"][1])
    assert (*result, LAST_RUN["left"]) == (0, b"", b"", expected, []), (
        result, LAST_RUN["left"])


def test_a_working_part_from_the_fifteenth_to_the_thirty_first_keeps_d2():
    """Paragraph 6.6. WORKING drawn 2022-07-15 with
    a step on 2022-07-31: D1=15 and D2=31, so D2 stays 31. Reports 31 days and
    earns 4245p."""
    facility = (
        b"facility F-WK-MID31\nclass WORKING\nadvance 1000000\nrate 500\n"
        b"drawn 2022-07-15\nperiods 1\nfrequency 1\nstep 2022-07-31 500\n")
    expected = (
        b"SCHEDULE F-WK-MID31 WORKING K5 001\n"
        b"0001 2022-07-15 2022-08-15 000031 000000001000000 000000000004245 000000001000000\n"
        b"TOTAL 000000000004245 000000001000000\n")
    result = run_case(facility, CASES["crossley"][1])
    assert (*result, LAST_RUN["left"]) == (0, b"", b"", expected, []), (
        result, LAST_RUN["left"])


def test_a_working_span_from_february_twenty_eighth_to_march_thirty_first_counts_thirty_three():
    """Paragraph 6.6 (walmsgate period 5 shows the same count): WORKING
    from 2019-02-28 to 2019-03-31 keeps D2=31 because D1 is not 30, so the count
    is 33 and period interest is 59671p. An always-adjust-D2 reading yields 32."""
    facility = (
        b"facility F-WK-2831\nclass WORKING\nadvance 9900000\nrate 2000\n"
        b"drawn 2018-12-31\nperiods 3\nfrequency 1\n")
    expected = (
        b"SCHEDULE F-WK-2831 WORKING K5 003\n"
        b"0001 2018-12-31 2019-01-31 000030 000000009900000 000000000162739 000000003300000\n"
        b"0002 2019-01-31 2019-02-28 000028 000000006600000 000000000101261 000000003300000\n"
        b"0003 2019-02-28 2019-03-31 000033 000000003300000 000000000059671 000000003300000\n"
        b"TOTAL 000000000323671 000000009900000\n")
    result = run_case(facility, STEP_TABLE)
    assert (*result, LAST_RUN["left"]) == (0, b"", b"", expected, []), (
        result, LAST_RUN["left"])


def test_a_step_on_the_drawdown_beats_a_term_past_the_calendar():
    """Section 10.1: step out of term precedes term past the calendar. Drawn
    2199-12-31 for one period ends after the calendar, and a step on the
    drawdown date is outside 8.2."""
    facility = (PANEL_FACILITY.replace(b"2022-01-31", b"2199-12-31")
                .replace(b"periods 3\n", b"periods 1\n")
                + b"step 2199-12-31 700\n")
    code, stdout, stderr, written = run_case(facility, CASES["crossley"][1])
    assert (code, stdout, stderr, written, LAST_RUN["left"]) == (
        1, b"", b"schedbuild: step out of term\n", None, []), (
        code, stdout, stderr, written, LAST_RUN["left"])


def test_a_step_before_the_drawdown_beats_a_term_past_the_calendar():
    """Section 10.1: step out of term precedes term past the calendar. Drawn
    2199-12-31 for one period ends after the calendar, and a step before the
    drawdown is outside 8.2."""
    facility = (PANEL_FACILITY.replace(b"2022-01-31", b"2199-12-31")
                .replace(b"periods 3\n", b"periods 1\n")
                + b"step 2199-12-01 700\n")
    code, stdout, stderr, written = run_case(facility, CASES["crossley"][1])
    assert (code, stdout, stderr, written, LAST_RUN["left"]) == (
        1, b"", b"schedbuild: step out of term\n", None, []), (
        code, stdout, stderr, written, LAST_RUN["left"])


def test_every_rate_change_on_a_long_term_is_kept_and_applied():
    """Paragraph 2.4's step lines in any number: 480 periods carrying 156 rate changes on
    distinct dates, in no order in the file, six of them inside one period."""
    facility, table = GENERATORS.many_steps()
    assert facility.count(b"\nstep ") == 156
    code, stdout, stderr, written = run_case(facility, table)
    assert (code, stdout, stderr, LAST_RUN["left"]) == (0, b"", b"", []), stderr[:300]
    size, digest = DIGESTS["hebden"]
    assert written is not None and len(written) == size, len(written or b"")
    assert hashlib.sha256(written).hexdigest() == digest, "the schedule differs"
    rows = parse(written)["rows"]
    assert len(rows) == 480 and rows[-1]["end"] == "2071-03-31"


# The reasons 10.2 lists, and no others. The table has to reach every one of them, or a run
# of it that loaded nothing would pass while grading nothing.
REASONS = ("cannot read facility file", "cannot read basis table", "malformed entry",
           "unknown key", "repeated key", "missing key", "bad reference", "unknown class",
           "bad advance", "bad rate", "bad date", "bad period count", "bad frequency",
           "bad step", "duplicate identifier", "basis table incomplete", "step out of term",
           "repeated step date", "term past the calendar", "cannot write output file")


def test_the_contract_traps():
    """The sealed trap table. Each record turns one sentence of the note false and nothing
    else, or holds one input the note allows, and holds the run to the status, the diagnostic,
    an empty standard output, what stands at the output path and what it left anywhere the
    account can write. Between them the records reach each of the twenty reasons of 10.2, the
    order 10.1 decides between two faults in, the last day of every month and the day after it
    for a drawdown and for a step alike, an operand that cannot be read or an output that
    cannot be written for want of a mode, a directory standing at the output path, a path
    standing for more than one operand at each stage a refusal can come from, and a schedule
    already filed that a refusal has to spare."""
    reached = set(rec["stderr"].rsplit(": ", 1)[-1].strip() for rec in TRAPS if rec["status"])
    assert reached == set(REASONS) and len(TRAPS) >= 330, (len(TRAPS), sorted(reached))
    wrong = [answer for answer in (apply_trap(rec) for rec in TRAPS) if answer]
    wrong += [answer for answer in (apply_trap(rec) for rec in generated_traps()) if answer]
    assert wrong == [], wrong[:8]


def test_an_operand_count_other_than_three_is_refused_with_status_two():
    """Paragraph 1.2: sweep through 64 and larger counts up to 1024, including seven.
    Every overlong invocation starts with valid facility, basis and output operands."""
    require_binary()
    wrong = count_sweep(*CASES["crossley"][:2])
    assert wrong == [], wrong[:3]


def test_a_file_already_at_the_output_path_is_replaced_only_by_a_run_that_completes():
    """Paragraph 9.5: a completed run replaces the file, a refused run leaves it, a path given
    as both the facility file and the output path is the output path, and a schedule filed
    read only is replaced too, which a run renaming its work into place does without opening
    that file and a run writing over the output path cannot do at all."""
    facility, table, expected = CASES["elverton"]
    stale = b"the schedule the desk issued last week\n" * 40
    for staging, outcome in (({}, expected), ({"output_readonly": True}, expected),
                             ({"names": (PATHS[0], PATHS[1], PATHS[0])}, expected)):
        code, stdout, stderr, written = run_case(facility, table, existing=stale, **staging)
        assert (code, stdout, stderr) == (0, b"", b""), (staging, stderr[:200])
        assert written == outcome, staging
    # The standing schedule is replaced, not written over: 1.8's last sentence.
    publish_over_held_file(facility, table, expected, PATHS, existing=stale)
    # The parent is not writable, so a rename cannot land. The standing file's
    # mode is part of "as it stands" (9.5, 1.7), not only its bytes.
    code, stdout, stderr, written = run_case(
        facility, table, existing=stale, output_directory_unwritable=True)
    assert (code, stdout, stderr, written, LAST_RUN["left"]) == (
        1, b"", b"schedbuild: cannot write output file\n", stale, []), (
            code, stderr[:200], LAST_RUN["left"])
    bad_facility, bad_table, diagnostic = first_trap()
    code, stdout, stderr, written = run_case(bad_facility, bad_table, existing=stale)
    assert (code, stdout, stderr, written, LAST_RUN["left"]) == (1, b"", diagnostic, stale, [])


ALIASES = [("the basis table given again as the output path", (0, 1, 1), "table"),
           ("the facility file given again as the basis table", (0, 0, 2), "facility"),
           ("one path given for all three operands", (0, 0, 0), "facility")]


def test_a_path_standing_for_more_than_one_operand_is_taken_as_1_7_takes_it():
    """The rest of 1.7's aliasing: the basis table given as the output path is read and then
    replaced by the schedule, so a builder that will not write over a file it has just read
    turns away work the note allows; the facility file given as the basis table is read twice
    and refused against the table's first significant line."""
    facility, table, expected = CASES["crossley"]
    # Paragraph 2.1: the first line that holds bytes and does not open with a number sign.
    significant = 2
    first, second_line = facility.split(b"\n")[:2]
    assert first.startswith(b"#") and second_line.startswith(b"facility "), (first, second_line)
    refused = b"schedbuild: basis line %d: malformed entry\n" % significant
    wrong = []
    for label, operands, second in ALIASES:
        names = tuple(PATHS[which] for which in operands)
        code, stdout, stderr, written = run_case(
            facility, table if second == "table" else facility, names=names)
        if names[0] == names[1]:
            outcome = (1, b"", refused, facility if names[2] == names[0] else None, [])
        else:
            outcome = (0, b"", b"", expected, [])
        if (code, stdout, stderr, written, LAST_RUN["left"]) != outcome:
            wrong.append((label, code, stdout[:60], stderr[:160], LAST_RUN["left"]))
    assert wrong == [], wrong


def test_a_run_leaves_the_rest_of_the_directory_as_it_found_it():
    """Paragraph 1.7 on a run that completes and one that is refused: papers filed beside the
    schedule, below it and among the operands stand unchanged, contents and mode alike."""
    facility, table, expected = CASES["elverton"]
    filed = {"issued/keep.txt": b"KEEP\n",
             "issued/older.out": b"the schedule the desk issued last week\n" * 12,
             "issued/back/note.txt": b"filed by the desk head, do not remove\n",
             "papers/covering.txt": b"drawdown advice\n"}
    code, stdout, stderr, written = run_case(facility, table, beside=filed)
    assert (code, stdout, stderr, written, LAST_RUN["left"]) == (0, b"", b"", expected, [])
    bad_facility, bad_table, diagnostic = first_trap()
    code, stdout, stderr, written = run_case(bad_facility, bad_table, beside=filed)
    assert (code, stdout, stderr, written, LAST_RUN["left"]) == (1, b"", diagnostic, None, [])


def test_inputs_the_note_allows_are_read_as_it_reads_them():
    """Input a narrower reader would turn away: a table whose last row ends at the end of the
    file, a table opening with a comment far past a line buffer, and references of one byte,
    of sixteen, and opening with a digit or a hyphen, which 3.1 allows."""
    facility, table, expected = CASES["crossley"]
    for label, run_table in (("a last row with no line feed", table.rstrip(b"\n")),
                             ("a comment of 70000 bytes", b"#" + b"x" * 70000 + b"\n" + table)):
        code, stdout, stderr, written = run_case(facility, run_table)
        assert (code, stdout, stderr, written, LAST_RUN["left"]) == (
            0, b"", b"", expected, []), (label, stderr[:200])
    for reference in (b"9", b"0-PL-0916", b"-PL-0916", b"0123456789ABCDEF"):
        renamed = facility.replace(b"facility F-PL-0916\n", b"facility " + reference + b"\n")
        assert renamed != facility
        code, stdout, stderr, written = run_case(renamed, table)
        assert (code, stdout, stderr, LAST_RUN["left"]) == (0, b"", b"", []), reference
        assert written == expected.replace(
            b"SCHEDULE F-PL-0916 ", b"SCHEDULE " + reference + b" "), reference


def test_a_write_that_fails_partway_leaves_the_file_already_there():
    """Paragraph 9.5 where the write fails at its first byte, partway and at its last, with a
    schedule already filed and with nothing there: a builder that truncates the standing file
    before its write has finished, or leaves part of a new one, fails."""
    require_binary()
    facility, table, expected = CASES["crossley"]
    assert fsize_sweep(facility, table, expected) == []


def test_the_output_path_never_carries_part_of_a_schedule():
    """Paragraph 1.8 under destruction, over a schedule already filed, over nothing and over an
    empty directory, at thirty four moments of the verifier's choosing and then the instant
    the output path stops being what it was, whenever the builder brings that about. Where
    nothing stood at the path, the run is also stopped when that path is opened for writing,
    before the next syscall. Where a file stood, the run is stopped when unlink of that
    path returns, before the next syscall. However late the signal lands, the path carries
    what stood there,
    or nothing where nothing stood, or the whole of the new schedule, never a part of one."""
    require_binary()
    facility, table = GENERATORS.many_steps()
    size, digest = DIGESTS["hebden"]
    assert destroy_sweep(facility, table, size, digest) == []
    code, stdout, stderr, written = run_case(facility, table)
    assert (code, stdout, stderr, LAST_RUN["left"]) == (0, b"", b"", []), stderr[:300]
    assert written is not None and hashlib.sha256(written).hexdigest() == digest


def test_an_absent_output_opened_for_writing_holds_nothing_or_the_whole_schedule():
    """Paragraph 1.8 on a short TRADE term with nothing at the output path. The stop
    is the return from opening that path for writing, before the next syscall. The
    path then holds nothing, or the whole schedule."""
    require_binary()
    facility, table, expected = CASES["crossley"]
    tree = stage_run(facility, table, existing=None)
    try:
        invoke_and_stop_when_the_output_is_opened(tree, PATHS)
        written = standing_at(tree / PATHS[2])
    finally:
        close_off(tree)
    assert written is None or written == expected, (
        None if written is None else len(written))


def test_interrupted_publication_preserves_inputs_used_as_the_output():
    """Paragraphs 1.7 and 1.8 apply together, for each input/output alias. A builder
    that stages correctly but then truncates the aliased input and copies the schedule
    into it has written over the output path itself, and a kill between the truncation
    and the copy leaves nothing where the input stood. The same sweep stops when unlink
    of that shared path returns, before the rename that would put the schedule there.
    The file standing at the shared
    path is never written in place, and under write failures and kills the path holds
    the whole input or the whole schedule."""
    require_binary()
    facility, table, expected = CASES["crossley"]
    for names in ((PATHS[0], PATHS[1], PATHS[0]),
                  (PATHS[0], PATHS[1], PATHS[1])):
        publish_over_held_file(facility, table, expected, names)
        assert fsize_sweep(facility, table, expected, names=names) == [], names
        wrong = destroy_sweep(facility, table, len(expected),
                              hashlib.sha256(expected).hexdigest(), names=names)
        assert wrong == [], (names, wrong)


def test_two_runs_of_one_facility_write_the_same_bytes():
    """Paragraph 9.6, the second run under operand paths of another shape, where the operands
    and the program stand in the directory the schedule is filed in."""
    facility, table, expected = CASES["harrowby"]
    first = run_case(facility, table)
    second = run_case(facility, table, names=SPARE)
    assert first[:3] == second[:3] == (0, b"", b""), (first[2][:200], second[2][:200])
    assert first[3] == second[3] == expected and LAST_RUN["left"] == []


def test_the_build_account_reaches_neither_the_goldens_nor_the_reward():
    """The dropped account's reach, the reward channel's mode, the areas closed behind it once
    the sources were compiled, and that no process of its outlives its execution."""
    text = account_probe()
    assert text.splitlines()[0] == str(SANDBOX_UID), text
    for denied in ("TESTS_DENIED", "APP_DENIED", "REWARD_DENIED",
                   "BUILD_DENIED", "CACHE_DENIED", "MODULES_DENIED"):
        assert denied in text, (denied, text)
    for spent in ("build", "gocache", "gopath"):
        folder = SANDBOX_ROOT / spent
        facts = folder.stat()
        assert (facts.st_uid, stat.S_IMODE(facts.st_mode)) == (0, 0o700), (spent, facts)
        assert not enterable(folder), spent
    assert "SPAWNED" in text, text
    assert drain_sandbox() == [], "processes of the sandbox account outlived their execution"
    assert oct(os.stat("/logs/verifier").st_mode & 0o777) == "0o700"
    assert oct(os.stat("/logs/verifier/reward.txt").st_mode & 0o777) == "0o600"
