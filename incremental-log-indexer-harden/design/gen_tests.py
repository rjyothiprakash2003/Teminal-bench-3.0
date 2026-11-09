"""Writes tests/test_outputs.py: the harness of test_template.py, then every witness of
witnesses.py as a literal constant with answers minted where the reference binary and
the independent model agree, then one test per rule. Refuses a witness the inputs gate
rejects or on which the two programs disagree."""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import model  # noqa: E402
from witnesses import TESTS  # noqa: E402

DOCSTRINGS = {
    "test_journal_lines_and_stop_word_line": "R1 and R2: every line is read and the first line sets the stop words.",
    "test_line_fields_ids_slop_and_fuzz": "R3: ids, slop, fuzz and text are taken from the right fields at their full lengths.",
    "test_batches_commit_and_roll_back": "R4 and R5: a batch changes the committed index only when it commits.",
    "test_tokens_split_on_punctuation_and_lowercase": "R6: tokens are runs of letters and digits, lowercased.",
    "test_stop_words_hold_positions_and_are_not_indexed": "R7: stop words take positions and are never matched.",
    "test_add_replaces_a_live_document": "R8: an add of a live id replaces the whole document.",
    "test_append_carries_positions_on": "R9: appended tokens carry on from the document's own positions.",
    "test_delete_ends_a_document": "R10: a delete ends the document and a later add starts a new one.",
    "test_document_size_limits": "R11: documents of 300 tokens and 100 live documents are handled whole.",
    "test_query_words_keep_their_positions": "R12: query words are lowercased, stop words dropped, positions kept.",
    "test_edit_distance_is_the_least_number_of_edits": "R13: the edit distance is the least number of edits.",
    "test_placements_take_distinct_tokens_within_the_fuzz": "R14: a placement gives each word its own indexed token within the fuzz.",
    "test_distance_is_the_least_spread": "R15: the distance is the least spread over all placements.",
    "test_hits_lie_within_the_slop": "R16: a document is a hit when its distance is at most the slop.",
    "test_output_order_and_format": "R17: hits are ordered by distance then id and written in the stated form.",
}


def ref_answer(binary, journal):
    r = subprocess.run([binary], input=journal.encode(), capture_output=True, timeout=10)
    assert r.returncode == 0, r.stderr
    return r.stdout.decode()


def main(binary):
    out = [(HERE / "test_template.py").read_text().rstrip("\n"), ""]
    problems = []
    for test, witnesses in TESTS.items():
        for w in witnesses:
            bad = model.violations(w["journal"])
            if bad:
                problems.append("%s outside the domain: %s" % (w["name"], bad))
                continue
            a, m = ref_answer(binary, w["journal"]), model.answer(w["journal"])
            if a != m:
                problems.append("%s: reference %r model %r" % (w["name"], a, m))
                continue
            out.append("")
            out.append("%s = {" % w["name"])
            out.append("    \"name\": %r," % w["name"])
            out.append("    \"journal\": %r," % w["journal"])
            out.append("    \"answers\": %r," % a)
            out.append("    \"refuses\": %r," % w["refuses"])
            out.append("}")
        out.append("")
        out.append("")
        out.append("def %s(program):" % test)
        out.append("    \"\"\"%s\"\"\"" % DOCSTRINGS[test])
        out.append("    check(program, %s)" % ", ".join(w["name"] for w in witnesses))
        out.append("")
    out.append("")
    out.append("def test_held_out_journals(program, held_out):")
    out.append("    \"\"\"R1 to R17: journals the shipped tree does not hold are answered exactly.\"\"\"")
    out.append("    check(program, *held_out)")
    if problems:
        print("\n".join(problems))
        sys.exit(1)
    text = "\n".join(out) + "\n"
    while "\n\n\n\n" in text:
        text = text.replace("\n\n\n\n", "\n\n\n")
    (HERE.parent / "tests" / "test_outputs.py").write_text(text)
    print("wrote %d witnesses in %d tests" % (sum(len(v) for v in TESTS.values()), len(TESTS) + 1))


if __name__ == "__main__":
    main(sys.argv[1])
