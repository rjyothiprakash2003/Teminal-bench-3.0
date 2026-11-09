"""Inputs that are built rather than stored, because a file of them would be large. Nothing
here is random and nothing here depends on the clock, so two runs build the same bytes."""

TABLE_HEAD = ("# Calderbank Commercial Finance, accrual basis table, issue 7.\n"
              "# Retired identifiers are not reused, which is why the numbering has gaps.\n")
ROWS = ("K1 actual 360\nK2 actual 365\nK4 thirty-us 360\n"
        "K5 thirty-us 365\nK7 thirty-e 360\nK8 thirty-e 365\n")
TABLE = (TABLE_HEAD + ROWS).encode("latin-1")

BODY = ("facility F-TR-0000\nclass TRADE\nadvance 1000000\nrate 500\n"
        "drawn 2022-01-31\nperiods 3\nfrequency 1\n")


def long_comment():
    """A comment line far past any read buffer, with the fault on the line after it."""
    lines = ["# " + "x" * 70000, BODY.rstrip("\n"), "step 2022-02-15 700x"]
    return ("\n".join(lines) + "\n").encode("latin-1"), TABLE


def long_comment_line():
    return 9


def many_ignorable():
    """Three thousand ignorable lines ahead of the entries, so the number is large."""
    lines = ["# desk note %d" % index if index % 2 else "" for index in range(3000)]
    lines.append(BODY.rstrip("\n"))
    lines.append("step 2022-02-15 700x")
    return ("\n".join(lines) + "\n").encode("latin-1"), TABLE


def many_ignorable_line():
    return 3008


def late_step_fault():
    """Four hundred step lines with the fault on the last of them."""
    lines = [BODY.rstrip("\n")]
    for index in range(399):
        lines.append("step 2022-02-%02d %d" % (index % 27 + 1, 600 + index))
    lines.append("step 2022-02-15 700x")
    return ("\n".join(lines) + "\n").encode("latin-1"), TABLE


def late_step_fault_line():
    return 407


def wide_table():
    """A basis table carrying long comments between its rows."""
    rows = ROWS.rstrip("\n").split("\n")
    lines = []
    for index, row in enumerate(rows):
        lines.append("# " + "issue seven " * 400)
        lines.append(row if index != 4 else "K7 thirty-e 366")
        lines.append("")
    return BODY.encode("latin-1"), ("\n".join(lines) + "\n").encode("latin-1")


def wide_table_line():
    return 14


def many_steps():
    """A term of 480 monthly periods carrying 156 rate changes on distinct dates, standing in
    no order in the file: 150 spread through the term, the last of them in its final year,
    and six more inside a single period, so the schedule depends on every one of them."""
    head = ["facility WK-STEPS-156", "class WORKING", "advance 9876543210", "rate 725",
            "drawn 2031-03-31", "periods 480", "frequency 1"]
    steps = {}
    for index in range(150):
        offset = (index * 37) % 470 + 1
        year, month = 2031 + (offset + 2) // 12, (offset + 2) % 12 + 1
        steps["%04d-%02d-%02d" % (year, month, index * 11 % 28 + 1)] = index * 613 % 20001
    crowded = next(offset for offset in range(100, 470)
                   if all(not key.startswith("%04d-%02d" % (2031 + (offset + 2) // 12,
                                                            (offset + 2) % 12 + 1))
                          for key in steps))
    year, month = 2031 + (crowded + 2) // 12, (crowded + 2) % 12 + 1
    for number, day in enumerate((3, 9, 14, 20, 25, 28)):
        steps["%04d-%02d-%02d" % (year, month, day)] = 19000 + number * 200
    dated = sorted(steps.items())
    order = sorted(range(len(dated)), key=lambda position: (position * 97) % len(dated))
    lines = head + ["step %s %d" % dated[position] for position in order]
    return ("\n".join(lines) + "\n").encode("latin-1"), TABLE
