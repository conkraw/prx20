"""Behavioral checks using fictional residents and locally supplied exports."""

import copy
import csv
import io
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import epa_core as core


def tab_bytes(headers, rows, encoding="utf-16"):
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, delimiter="\t", lineterminator="\r\n")
    writer.writerow(headers)
    writer.writerows(rows)
    return buffer.getvalue().encode(encoding)


def form_bytes(ids=("101", "102")):
    return tab_bytes(
        ["QuestionID", "Tags", "[Question Part 1]", "[Question Part 2]"],
        [[qid, "", f"Clinical skill {qid}", ""] for qid in ids],
    )


def eval_bytes(answers=("5", "1"), session="7/1/2026", completed="7/31/2026 3:00:00 PM",
               name="Example, Resident", ids=("101", "102"), encoding="utf-16",
               evaluator="Teacher, Fictional", source_id=None):
    headers = ["Session Date", "Date Completed", "Evaluator Name", "Evaluator Status",
               "Subject Name", "Subject Rotation", "Overall Comments", "Subject Comments"]
    row = [session, completed, evaluator, "Faculty", name, "Rotation A", "", ""]
    if source_id is not None:
        headers.append("Evaluation ID")
        row.append(source_id)
    for number, (qid, answer) in enumerate(zip(ids, answers), start=1):
        headers.extend([f"[Question {number} ID]", f"[Question {number} Response]",
                        f"[Question {number} Comment]", f"[Question {number} Tags]",
                        f"[Question {number} Drop Down List]"])
        row.extend([qid, answer, "", "", ""])
    return tab_bytes(headers, [row], encoding)


def record(**kwargs):
    return core.parse_upload(eval_bytes(**kwargs), "evaluation.tab")["evaluations"][0]


def configured_state(records=()):
    form = core.parse_form(form_bytes(), "Example EPA.tab")
    form["scoring_confirmed"] = True
    state = core.register_form(core.empty_state(), form, "Example EPA")
    return core.ingest_evaluations(state, list(records))[0]


class ParseTests(unittest.TestCase):
    def test_utf16_form_and_evaluation(self):
        form = core.parse_upload(form_bytes(), "form.tab")
        self.assertEqual(form["kind"], "form")
        self.assertEqual(len(form["form"]["questions"]), 2)
        evaluation = core.parse_upload(eval_bytes(), "evaluation.tab")
        self.assertEqual(evaluation["kind"], "evaluations")
        self.assertEqual(evaluation["warnings"], [])
        self.assertEqual(evaluation["evaluations"][0]["session_date"], "2026-07-01")
        self.assertEqual(evaluation["evaluations"][0]["completed_at"], "2026-07-31T15:00:00")

    def test_utf8_cp1252_and_numeric_normalization(self):
        for encoding in ("utf-8-sig", "cp1252"):
            parsed = core.parse_upload(eval_bytes(encoding=encoding), "x.tab")
            self.assertEqual(len(parsed["evaluations"]), 1)
        self.assertEqual(core.normalize_code("4.00"), "4")
        self.assertEqual(core.normalize_code(" N/A "), "n/a")
        self.assertEqual(core.normalize_question_id("00101.0"), "101")

    def test_malformed_row_fails_whole_file(self):
        raw = b"QuestionID\t[Question Part 1]\n101\tGood\n102\tBad\tExtra\n"
        with self.assertRaisesRegex(core.ParseError, "columns"):
            core.parse_upload(raw, "form.tab")

    def test_missing_subject_rejected(self):
        with self.assertRaisesRegex(core.ParseError, "resident name"):
            core.parse_upload(eval_bytes(name="  "), "x.tab")

    def test_dates_retained_with_warning(self):
        parsed = core.parse_upload(eval_bytes(session="not a date", completed=""), "x.tab")
        self.assertEqual(len(parsed["warnings"]), 2)
        self.assertIsNone(parsed["evaluations"][0]["session_date"])
        self.assertEqual(parsed["evaluations"][0]["session_date_raw"], "not a date")

    def test_form_proposal_excludes_admin(self):
        raw = tab_bytes(["QuestionID", "[Question Part 1]"], [
            ["1", "How much time did you spend working with this resident?"],
            ["2", "Provides supervision and feedback to junior learners."],
            ["3", "MAINTAINING PROFESSIONALISM: Sense of duty."],
            ["4", "Overall Comments:"],
        ])
        form = core.parse_form(raw)
        self.assertEqual([q["include"] for q in form["questions"]], [False, True, False, False])
        self.assertFalse(form["scoring_confirmed"])


class IdentityTests(unittest.TestCase):
    def test_renamed_overlapping_export_deduplicates(self):
        first = record()
        second = core.parse_upload(eval_bytes(encoding="utf-8-sig"), "renamed.tab")["evaluations"][0]
        state, stats = core.ingest_evaluations(core.empty_state(), [first, second])
        self.assertEqual(stats, {"added": 1, "duplicates": 1, "conflicts": 0})
        self.assertEqual(len(state["evaluations"]), 1)
        self.assertEqual(sorted(next(iter(state["evaluations"].values()))["source_files"]),
                         ["evaluation.tab", "renamed.tab"])

    def test_formatting_and_question_slot_order_do_not_duplicate(self):
        first = record()
        second = record(name="  example,   resident ", answers=("1.0", "5.00"), ids=("102", "101"),
                        session="2026-07-01", completed="2026-07-31T15:00:00")
        state, stats = core.ingest_evaluations(core.empty_state(), [first, second])
        self.assertEqual(stats["duplicates"], 1)
        self.assertEqual(len(state["evaluations"]), 1)

    def test_edit_is_reviewed_and_not_double_counted(self):
        state, stats = core.ingest_evaluations(core.empty_state(), [record(), record(answers=("4", "1"))])
        self.assertEqual(stats["conflicts"], 1)
        self.assertEqual(len(state["evaluations"]), 1)
        conflict_id = next(iter(state["conflicts"]))
        self.assertEqual(state["conflicts"][conflict_id]["kind"], "correction")
        with self.assertRaises(ValueError):
            core.resolve_conflict(state, conflict_id, "add")
        fixed = core.resolve_conflict(state, conflict_id, "replace")
        self.assertEqual(next(iter(fixed["evaluations"].values()))["responses"]["101"]["response"], "4")
        fixed, stats = core.ingest_evaluations(fixed, [record()])
        self.assertEqual(stats["duplicates"], 1)
        self.assertFalse(fixed["conflicts"])

    def test_changed_completion_time_requires_review(self):
        first = record()
        changed = record(completed="8/1/2026 3:00:00 PM")
        state, stats = core.ingest_evaluations(core.empty_state(), [first, changed])
        self.assertEqual(stats["conflicts"], 1)
        conflict_id = next(iter(state["conflicts"]))
        self.assertEqual(state["conflicts"][conflict_id]["kind"], "possible_duplicate")
        separate = core.resolve_conflict(state, conflict_id, "add")
        self.assertEqual(len(separate["evaluations"]), 2)
        replaced = core.resolve_conflict(state, conflict_id, "replace")
        replaced, stats = core.ingest_evaluations(replaced, [changed])
        self.assertEqual(stats["duplicates"], 1)
        self.assertEqual(len(replaced["evaluations"]), 1)

    def test_keep_decision_is_remembered(self):
        first, changed = record(), record(answers=("4", "1"))
        state, _ = core.ingest_evaluations(core.empty_state(), [first, changed])
        state = core.resolve_conflict(state, next(iter(state["conflicts"])), "keep")
        state, stats = core.ingest_evaluations(state, [changed])
        self.assertEqual(stats["duplicates"], 1)
        self.assertFalse(state["conflicts"])

    def test_partial_question_export_is_reviewed_not_added(self):
        state = configured_state([record()])
        subset = record(answers=("5",), ids=("101",))
        state, stats = core.ingest_evaluations(state, [subset])
        self.assertEqual(stats["added"], 0)
        self.assertEqual(stats["conflicts"], 1)
        self.assertEqual(len(state["evaluations"]), 1)
        self.assertEqual(next(iter(state["conflicts"].values()))["kind"], "possible_duplicate")

    def test_multiple_candidates_need_explicit_replace_target(self):
        first, second = record(), record(completed="8/1/2026 3:00:00 PM")
        state, _ = core.ingest_evaluations(core.empty_state(), [first, second])
        state = core.resolve_conflict(state, next(iter(state["conflicts"])), "add")
        third = record(completed="8/2/2026 3:00:00 PM", answers=("3", "1"))
        state, _ = core.ingest_evaluations(state, [third])
        conflict_id = next(iter(state["conflicts"]))
        self.assertEqual(len(state["conflicts"][conflict_id]["candidate_ids"]), 2)
        with self.assertRaisesRegex(ValueError, "Select the exact"):
            core.resolve_conflict(state, conflict_id, "replace")
        replaced = core.resolve_conflict(state, conflict_id, "replace", target_id=second["id"])
        self.assertEqual(replaced["evaluations"][second["id"]]["responses"]["101"]["response"], "3")
        self.assertEqual(replaced["evaluations"][first["id"]]["responses"]["101"]["response"], "5")

    def test_pending_alternatives_remain_resolvable_after_replace(self):
        state, _ = core.ingest_evaluations(core.empty_state(), [
            record(), record(answers=("3", "1")), record(answers=("2", "1")),
        ])
        conflict_ids = list(state["conflicts"])
        state = core.resolve_conflict(state, conflict_ids[0], "replace")
        state = core.resolve_conflict(state, conflict_ids[1], "replace")
        self.assertFalse(state["conflicts"])
        self.assertEqual(next(iter(state["evaluations"].values()))["responses"]["101"]["response"], "2")

    def test_distinct_known_subset_forms_can_both_be_stored(self):
        state = configured_state([record()])
        state = core.register_form(state, core.parse_form(form_bytes(("101",))), "Different EPA")
        state, stats = core.ingest_evaluations(state, [record(answers=("5",), ids=("101",))])
        self.assertEqual(stats["added"], 1)
        self.assertEqual(stats["conflicts"], 0)

    def test_distinct_source_ids_allow_separate_evaluations(self):
        state, stats = core.ingest_evaluations(core.empty_state(), [
            record(source_id="one"), record(source_id="two"),
        ])
        self.assertEqual(stats["added"], 2)
        self.assertFalse(state["conflicts"])


class MatchingAndReportingTests(unittest.TestCase):
    def test_unknown_form_then_later_registration(self):
        state, _ = core.ingest_evaluations(core.empty_state(), [record()])
        self.assertEqual(core.match_form(record(), state["forms"])["status"], "unknown")
        self.assertTrue(core.score_rows(state).empty)
        form = core.parse_form(form_bytes())
        form["scoring_confirmed"] = True
        state = core.register_form(state, form, "New Rotation")
        self.assertEqual(len(core.score_rows(state)), 2)

    def test_exact_partial_ambiguous_matches(self):
        form_a = core.parse_form(form_bytes(("101", "102")))
        form_b = core.parse_form(form_bytes(("101", "103")))
        state = core.register_form(core.empty_state(), form_a, "A")
        partial = record(answers=("4",), ids=("101",))
        self.assertEqual(core.match_form(partial, state["forms"])["status"], "partial")
        state = core.register_form(state, form_b, "B")
        self.assertEqual(core.match_form(partial, state["forms"])["status"], "ambiguous")
        self.assertEqual(core.match_form(record(), state["forms"])["status"], "exact")

    def test_unconfirmed_form_never_scores(self):
        state = core.register_form(core.empty_state(), core.parse_form(form_bytes()))
        state, _ = core.ingest_evaluations(state, [record()])
        rows = core.score_rows(state)
        self.assertTrue(rows["score"].isna().all())
        self.assertEqual(set(rows["status"]), {"unconfirmed"})

    def test_question_means_have_equal_epa_weight(self):
        state = configured_state([
            record(answers=("5", "1"), evaluator="Teacher A"),
            record(answers=("3", ""), evaluator="Teacher B"),
        ])
        rows = core.score_rows(state)
        questions = core.question_summary(rows)
        self.assertEqual(questions["average"].tolist(), [4.0, 1.0])
        self.assertEqual(questions["n"].tolist(), [2, 1])
        self.assertEqual(questions["n_missing"].tolist(), [0, 1])
        summary = core.epa_summary(rows).iloc[0]
        self.assertEqual(summary["average"], 2.5)
        self.assertEqual(summary["n_evaluations"], 2)
        self.assertEqual(summary["n_ratings"], 3)

    def test_exclusion_unmapped_and_question_override(self):
        state = configured_state([record(answers=("6", "7"))])
        rows = core.score_rows(state)
        self.assertEqual(rows["status"].tolist(), ["excluded", "unmapped"])
        self.assertTrue(core.epa_summary(rows)["average"].isna().all())
        form = next(iter(state["forms"].values()))
        form["questions"][1]["score_map"] = {"7.0": 2.0}
        rows = core.score_rows(state)
        self.assertEqual(rows["status"].tolist(), ["excluded", "scored"])
        self.assertEqual(core.epa_summary(rows).iloc[0]["average"], 2.0)

    def test_repeat_form_preserves_settings(self):
        state = configured_state()
        form_id = next(iter(state["forms"]))
        state["forms"][form_id]["questions"][0]["include"] = False
        state["forms"][form_id]["score_map"]["6"] = 6
        state = core.register_form(state, core.parse_form(form_bytes()), None)
        form = state["forms"][form_id]
        self.assertEqual(form["name"], "Example EPA")
        self.assertTrue(form["scoring_confirmed"])
        self.assertFalse(form["questions"][0]["include"])
        self.assertEqual(form["score_map"]["6"], 6)

    def test_same_form_wording_change_requires_scoring_review(self):
        state = configured_state()
        incoming = core.parse_form(form_bytes())
        incoming["questions"][0]["text"] = "An updated clinical skill description"
        updated = core.register_form(state, incoming)
        self.assertFalse(next(iter(updated["forms"].values()))["scoring_confirmed"])
        whitespace_only = core.parse_form(form_bytes())
        whitespace_only["questions"][0]["text"] = "  Clinical   skill 101  "
        unchanged = core.register_form(state, whitespace_only)
        self.assertTrue(next(iter(unchanged["forms"].values()))["scoring_confirmed"])

    def test_reupload_without_changes_is_noop(self):
        state = configured_state([record()])
        state["updated_at"] = "previous-save"
        same_form = core.register_form(state, core.parse_form(form_bytes()))
        self.assertEqual(same_form, state)
        same_evaluation, stats = core.ingest_evaluations(state, [record()])
        self.assertEqual(stats["duplicates"], 1)
        self.assertEqual(same_evaluation, state)

    def test_academic_year_and_session_basis(self):
        state = configured_state([
            record(session="6/30/2026", completed="8/5/2026 1:00:00 PM"),
            record(session="7/1/2026", completed="8/6/2026 1:00:00 PM"),
        ])
        rows = core.score_rows(state)
        july_year = core.filter_rows(rows, academic_year=2026, months=[7])
        self.assertEqual(july_year["record_id"].nunique(), 1)
        self.assertEqual(set(july_year["academic_month"]), {1})
        june_year = core.filter_rows(rows, academic_year=2025, months=[6])
        self.assertEqual(set(june_year["academic_month"]), {12})
        completion = core.filter_rows(rows, academic_year=2026, months=[8], date_basis="completed_at")
        self.assertEqual(completion["record_id"].nunique(), 2)

    def test_cumulative_recalculates_underlying_question_means(self):
        state = configured_state([
            record(answers=("5", "1"), session="7/1/2026", evaluator="Teacher A"),
            record(answers=("3", ""), session="8/1/2026", evaluator="Teacher B"),
            record(answers=("3", ""), session="8/1/2026", evaluator="Teacher C"),
        ])
        rows = core.filter_rows(core.score_rows(state), academic_year=2026)
        monthly = core.monthly_summary(rows)
        cumulative = core.monthly_summary(rows, cumulative=True)
        self.assertEqual(monthly["period"].tolist(), ["2026-07", "2026-08"])
        self.assertAlmostEqual(cumulative.iloc[-1]["average"], ((11 / 3) + 1) / 2)
        self.assertEqual(cumulative.iloc[-1]["n_ratings"], 4)


class SuppliedFileTests(unittest.TestCase):
    def test_supplied_exports_if_available(self):
        uploads = Path(__file__).resolve().parents[2] / "upload"
        if not (uploads / "acutes_form.tab").exists():
            self.skipTest("Supplied resident exports are intentionally excluded from the app package.")
        state = core.empty_state()
        expected = [("acutes", 15, 9, 13), ("adol_epa", 13, 7, 7)]
        for prefix, question_count, included_count, evaluation_count in expected:
            form = core.parse_upload((uploads / f"{prefix}_form.tab").read_bytes(), f"{prefix}_form.tab")["form"]
            self.assertEqual(len(form["questions"]), question_count)
            self.assertEqual(sum(q["include"] for q in form["questions"]), included_count)
            evaluations = core.parse_upload((uploads / f"{prefix}_evaluation.tab").read_bytes(), f"{prefix}_evaluation.tab")
            self.assertEqual(len(evaluations["evaluations"]), evaluation_count)
            state = core.register_form(state, form)
            state, _ = core.ingest_evaluations(state, evaluations["evaluations"])
        self.assertEqual(len(state["evaluations"]), 20)
        self.assertEqual(len(state["forms"]), 2)
        self.assertFalse(state["conflicts"])
        self.assertTrue(core.score_rows(state)["score"].isna().all())


if __name__ == "__main__":
    unittest.main()
