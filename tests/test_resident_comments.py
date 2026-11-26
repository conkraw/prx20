"""Synthetic tests for linking original feedback to resident reports."""
import copy
import unittest

from epa_core import empty_state, form_id_for_questions
from epa_resident_comments import collect_resident_comments, MISSING_ROTATION


def synthetic_state():
    state = empty_state()
    fid = form_id_for_questions(["101", "102", "103"])
    state["forms"][fid] = {
        "id": fid, "name": "Synthetic clinical evaluation", "scoring_confirmed": False,
        "score_map": {"1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": None, "H": 5},
        "questions": [
            {"id": "101", "text": "Clinical communication", "include": False},
            {"id": "102", "text": "What are the resident's strengths?", "include": False},
            {"id": "103", "text": "What are the next steps?", "include": False},
        ],
    }
    state["evaluations"]["e1"] = evaluation()
    return state


def evaluation(**values):
    record = {
        "resident": "Jordan Example", "resident_key": "jordan example", "evaluator": "Dr Test",
        "evaluator_status": "Faculty", "rotation": "Inpatient", "completed_at": "2026-10-15T09:30:00",
        "session_date": "2026-09-01", "source_files": ["synthetic_evaluation.tab"],
        "responses": {
            "101": {"response": "4", "comment": "Explains the plan clearly."},
            "102": {"response": "Organized, attentive, and reliable.", "comment": ""},
            "103": {"response": "", "comment": "Present a concise differential.\nThen discuss next steps."},
        },
        "overall_comments": "Good progress this month.",
        "subject_comments": "I plan to work on concise presentations.",
    }
    record.update(values)
    return record


def entries(result):
    return [e for rotation in result["rotations"] for e in rotation["evaluations"]]


class ResidentCommentsTests(unittest.TestCase):
    def test_active_feedback_without_scoring_confirmation_or_epa_mapping(self):
        result = collect_resident_comments(synthetic_state(), "jordan example")
        row = entries(result)[0]
        self.assertEqual(row["form_match_status"], "exact")
        self.assertEqual(row["feedback"][0]["text"], "Good progress this month.")
        self.assertEqual(len(row["feedback"]), 4)
        self.assertEqual([f["question_order"] for f in row["feedback"]], [None, 1, 2, 3])
        self.assertNotIn("4", [f["text"] for f in row["feedback"]])

    def test_separate_resident_comments_and_original_metadata(self):
        row = entries(collect_resident_comments(synthetic_state(), "jordan example"))[0]
        self.assertEqual(row["resident_comments"], [{"kind": "subject_comments", "text": "I plan to work on concise presentations."}])
        self.assertEqual(row["evaluator"], "Dr Test")
        self.assertEqual(row["evaluator_status"], "Faculty")
        self.assertEqual(row["source_files"], ["synthetic_evaluation.tab"])
        self.assertEqual(row["date"], "2026-10-15")
        self.assertEqual(row["date_basis"], "completed_at")

    def test_preserves_unicode_quotes_comma_and_line_breaks(self):
        state = synthetic_state()
        text = '  First line, "quoted".\nCafé and naïve, plus José.\n  Last line.  '
        state["evaluations"]["e1"]["responses"]["103"]["comment"] = text
        result = collect_resident_comments(state, "jordan example")
        self.assertEqual(entries(result)[0]["feedback"][-1]["text"], text)

    def test_inclusive_completion_date_bounds(self):
        state = synthetic_state()
        state["evaluations"]["e2"] = evaluation(completed_at="2026-10-31T23:59:59")
        state["evaluations"]["e3"] = evaluation(completed_at="2026-11-01T00:00:00")
        result = collect_resident_comments(state, "jordan example", "2026-10-15", "2026-10-31")
        self.assertEqual({e["id"] for e in entries(result)}, {"e1", "e2"})
        self.assertEqual(result["counts"]["out_of_period_evaluations"], 1)

    def test_session_date_fallback_is_visible(self):
        state = synthetic_state()
        state["evaluations"]["e1"]["completed_at"] = None
        result = collect_resident_comments(state, "jordan example", "2026-09-01", "2026-09-01")
        row = entries(result)[0]
        self.assertEqual(row["date"], "2026-09-01")
        self.assertEqual(row["date_basis"], "session_date")
        self.assertTrue(any("Session date" in n for n in row["notes"]))
        self.assertEqual(result["counts"]["session_date_fallback_evaluations"], 1)

    def test_undated_are_always_separate_even_without_date_filter(self):
        state = synthetic_state()
        state["evaluations"]["e1"].update(completed_at=None, session_date="invalid")
        for bounds in ((None, None), ("2026-01-01", "2026-12-31")):
            result = collect_resident_comments(state, "jordan example", *bounds)
            self.assertEqual(result["rotations"], [])
            self.assertEqual(len(result["undated"]), 1)
            self.assertEqual(result["undated"][0]["date_basis"], "unknown")
            self.assertEqual(result["counts"]["undated_feedback_items"], 4)
            self.assertEqual(result["counts"]["feedback_items"], 0)
            self.assertTrue(result["notes"])

    def test_exact_normalized_identity_no_substring_or_name_order_guess(self):
        state = synthetic_state()
        state["evaluations"]["e2"] = evaluation(resident="Jordan Example Jr", resident_key="jordan example jr")
        state["evaluations"]["e3"] = evaluation(resident="Example Jordan", resident_key="example jordan")
        self.assertEqual([r["id"] for r in entries(collect_resident_comments(state, "  JORDAN   EXAMPLE "))], ["e1"])
        self.assertEqual(entries(collect_resident_comments(state, "jordan")), [])

    def test_pending_conflict_alternatives_never_appear(self):
        state = synthetic_state()
        state["conflicts"]["c1"] = {"incoming": evaluation(overall_comments="Must not appear")}
        result = collect_resident_comments(state, "jordan example")
        self.assertEqual(result["counts"]["resident_evaluations"], 1)
        self.assertNotIn("Must not appear", str(result))

    def test_unknown_form_still_includes_comments_and_written_response(self):
        state = synthetic_state()
        state["forms"] = {}
        result = collect_resident_comments(state, "jordan example")
        row = entries(result)[0]
        self.assertEqual(len(row["feedback"]), 4)
        self.assertIsNone(row["form_id"])
        self.assertEqual(row["feedback"][1]["question"], "Question ID 101")
        self.assertTrue(row["feedback"][1]["question_wording_missing"])

    def test_explicit_evaluation_prompt_precedes_changed_form_wording(self):
        state = synthetic_state()
        state["evaluations"]["e1"]["responses"]["102"]["question_text"] = "Original strengths prompt"
        result = collect_resident_comments(state, "jordan example")
        self.assertEqual(entries(result)[0]["feedback"][2]["question"], "Original strengths prompt")

    def test_duplicate_response_and_comment_suppressed_only_within_question(self):
        state = synthetic_state()
        text = "An independent observation."
        state["evaluations"]["e1"].update(overall_comments=text)
        state["evaluations"]["e1"]["responses"]["102"].update(response=text, comment=text)
        state["evaluations"]["e1"]["responses"]["103"].update(response=text, comment=text)
        state["evaluations"]["e2"] = copy.deepcopy(state["evaluations"]["e1"])
        result = collect_resident_comments(state, "jordan example")
        for row in entries(result):
            self.assertEqual(sum(f["text"] == text for f in row["feedback"]), 3)
            self.assertEqual(row["duplicate_fields_suppressed"], 2)
        self.assertEqual(len(entries(result)), 2)

    def test_different_text_fields_are_not_deduplicated(self):
        state = synthetic_state()
        answer = state["evaluations"]["e1"]["responses"]["102"]
        answer.update(response="First observation.", comment="Second observation.")
        row = entries(collect_resident_comments(state, "jordan example"))[0]
        self.assertEqual(len([f for f in row["feedback"] if f["question_id"] == "102"]), 2)

    def test_rotations_separate_evaluations_with_matching_text(self):
        state = synthetic_state()
        state["evaluations"]["e2"] = evaluation(rotation="Outpatient")
        state["evaluations"]["e3"] = evaluation(rotation="")
        result = collect_resident_comments(state, "jordan example")
        self.assertEqual([r["rotation"] for r in result["rotations"]], ["Inpatient", "Outpatient", MISSING_ROTATION])
        self.assertEqual(result["counts"]["missing_rotation_evaluations"], 1)
        self.assertEqual(result["counts"]["evaluations_with_comments"], 3)

    def test_numeric_rating_codes_and_scale_labels_not_narrative(self):
        for value in ("2", "2.5", "2-2.5", "3b", "Level 3", "4 = Independent", "H", "Excellent", "Not observed", "nan", "inf"):
            with self.subTest(value=value):
                state = synthetic_state()
                state["evaluations"]["e1"]["responses"]["101"].update(response=value, comment="")
                row = entries(collect_resident_comments(state, "jordan example"))[0]
                self.assertFalse(any(f["question_id"] == "101" for f in row["feedback"]))

    def test_short_explicit_narrative_response_is_retained(self):
        state = synthetic_state()
        state["evaluations"]["e1"]["responses"]["102"]["response"] = "Excellent"
        row = entries(collect_resident_comments(state, "jordan example"))[0]
        self.assertEqual(next(f["text"] for f in row["feedback"] if f["question_id"] == "102"), "Excellent")

    def test_selected_dropdown_text_is_not_written_feedback(self):
        state = synthetic_state()
        state["evaluations"]["e1"]["responses"]["101"].update(response="Works under direct supervision", dropdown="Works under direct supervision", comment="")
        row = entries(collect_resident_comments(state, "jordan example"))[0]
        self.assertFalse(any(f["question_id"] == "101" for f in row["feedback"]))

    def test_resident_only_comments_still_include_evaluation(self):
        state = synthetic_state()
        state["evaluations"]["e1"].update(responses={}, overall_comments="", subject_comments="Resident reflection")
        result = collect_resident_comments(state, "jordan example")
        self.assertEqual(len(entries(result)), 1)
        self.assertEqual(result["counts"]["feedback_items"], 0)
        self.assertEqual(result["counts"]["resident_comment_items"], 1)

    def test_blank_evaluations_do_not_create_empty_rotation_sections(self):
        state = synthetic_state()
        state["evaluations"]["e1"].update(responses={}, overall_comments=" ", subject_comments="")
        result = collect_resident_comments(state, "jordan example")
        self.assertEqual(result["rotations"], [])
        self.assertEqual(result["counts"]["in_period_evaluations"], 1)
        self.assertEqual(result["counts"]["evaluations_with_comments"], 0)

    def test_empty_or_invalid_arguments_raise_clear_errors(self):
        state = synthetic_state()
        for args in (("", None, None), ("jordan example", "invalid", None),
                     ("jordan example", "2026-10-01", "2026-09-01")):
            with self.subTest(args=args), self.assertRaises(ValueError):
                collect_resident_comments(state, *args)

    def test_does_not_mutate_state(self):
        state = synthetic_state()
        original = copy.deepcopy(state)
        collect_resident_comments(state, "jordan example", "2026-01-01", "2026-12-31")
        self.assertEqual(state, original)


if __name__ == "__main__":
    unittest.main()
