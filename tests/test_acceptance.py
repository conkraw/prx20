"""Independent end-to-end checks using invented residents and form content."""

import csv
import io
import json

import pytest

import epa_core as core


def _tab(headers, rows, encoding="utf-16"):
    out = io.StringIO(newline="")
    writer = csv.writer(out, delimiter="\t")
    writer.writerow(headers)
    writer.writerows(rows)
    return out.getvalue().encode(encoding)


def _form(ids=("101", "102"), name="Example EPA"):
    raw = _tab(
        ["QuestionID", "Tags", "[Question Part 1]", "[Question Part 2]", "[Question Part 3]"],
        [[qid, "", f"Demonstrates clinical skill {i + 1}.", "", ""] for i, qid in enumerate(ids)],
    )
    form = core.parse_upload(raw, "example_form.tab")["form"]
    form["name"] = name
    for question in form["questions"]:
        question["include"] = True
    form["score_map"] = {str(i): float(i) for i in range(1, 6)} | {"6": None}
    form["scoring_confirmed"] = True
    return form


def _evaluations(rows, ids=("101", "102"), filename="example_evaluations.tab", encoding="utf-16"):
    headers = ["Session Date", "Date Completed", "Evaluator Name", "Evaluator Status", "Subject Name", "Subject Rotation", "Overall Comments", "Subject Comments"]
    for position, _ in enumerate(ids, 1):
        headers += [f"[Question {position} {suffix}]" for suffix in ["ID", "Tags", "Response", "Comment", "Drop Down List"]]
    values = []
    for row in rows:
        values.append([
            row.get("session", "7/1/2026"),
            row.get("completed", "7/30/2026 10:00:00 AM"),
            row.get("evaluator", "Faculty, Example"),
            "Faculty",
            row.get("resident", "Resident, Example"),
            "PEDS-EXAMPLE",
            row.get("comment", ""),
            "",
        ] + [cell for qid in ids for cell in [qid, "", row.get(qid, ""), "", ""]])
    return core.parse_upload(_tab(headers, values, encoding), filename)["evaluations"]


def _state(form=None):
    return core.register_form(core.empty_state(), form or _form())


def test_end_to_end_duplicate_independent_of_filename_encoding_and_question_slot():
    first = _evaluations([{"101": "4", "102": "3"}])
    reordered = _evaluations(
        [{"101": "4.0", "102": "3", "resident": "  Resident,   Example  ", "session": "07/01/2026"}],
        ids=("102", "101"),
        filename="renamed_export.tab",
        encoding="utf-8-sig",
    )
    state, stats = core.ingest_evaluations(_state(), first)
    assert stats["added"] == 1
    state, stats = core.ingest_evaluations(state, reordered)
    assert len(state["evaluations"]) == 1
    assert stats["duplicates"] == 1
    assert not state["conflicts"]


def test_new_form_learned_after_evaluations_survives_json_roundtrip():
    records = _evaluations([{"101": "4", "102": "2"}])
    state, _ = core.ingest_evaluations(core.empty_state(), records)
    assert core.match_form(records[0], state["forms"])["status"] == "unknown"
    form = _form()
    form["scoring_confirmed"] = False
    state = core.register_form(state, form)
    rows = core.score_rows(state)
    assert rows["score"].notna().sum() == 0
    state["forms"][form["id"]]["scoring_confirmed"] = True
    restored = json.loads(json.dumps(state))
    scored = core.score_rows(restored)
    assert sorted(scored["score"].dropna().tolist()) == [2.0, 4.0]
    assert core.match_form(records[0], restored["forms"])["status"] == "exact"


def test_partial_shared_question_ids_do_not_guess_between_forms():
    state = _state(_form(("101", "102"), "Form A"))
    state = core.register_form(state, _form(("101", "103"), "Form B"))
    record = _evaluations([{"101": "5"}], ids=("101",))[0]
    assert core.match_form(record, state["forms"])["status"] == "ambiguous"
    state, _ = core.ingest_evaluations(state, [record])
    assert core.score_rows(state).empty


def test_epa_composite_weights_questions_equally_and_blank_is_not_zero():
    records = _evaluations([
        {"101": "5", "102": "1", "evaluator": "Faculty, One"},
        {"101": "3", "102": "", "evaluator": "Faculty, Two"},
    ])
    state, _ = core.ingest_evaluations(_state(), records)
    rows = core.score_rows(state)
    summary = core.epa_summary(rows).iloc[0]
    assert summary["average"] == pytest.approx(2.5)
    assert summary["n_evaluations"] == 2
    assert summary["n_ratings"] == 3
    questions = core.question_summary(rows).set_index("question_id")
    assert questions.loc["101", "average"] == pytest.approx(4)
    assert questions.loc["102", "average"] == pytest.approx(1)
    assert questions.loc["102", "n"] == 1


def test_scoring_changes_recompute_from_raw_responses_without_reupload():
    state, _ = core.ingest_evaluations(_state(), _evaluations([{"101": "6", "102": "2"}]))
    before = core.epa_summary(core.score_rows(state)).iloc[0]
    assert before["average"] == 2
    fid = next(iter(state["forms"]))
    state["forms"][fid]["score_map"]["6"] = 6.0
    after = core.epa_summary(core.score_rows(state)).iloc[0]
    assert after["average"] == 4
    assert after["n_ratings"] == 2


def test_unmapped_codes_never_enter_mean_as_arbitrary_numbers():
    state, _ = core.ingest_evaluations(_state(), _evaluations([{"101": "999", "102": "4"}]))
    rows = core.score_rows(state)
    unknown = rows.loc[rows["question_id"] == "101"].iloc[0]
    assert unknown["status"] == "unmapped"
    assert core.epa_summary(rows).iloc[0]["average"] == 4


def test_academic_year_and_session_month_ignore_later_completion_date():
    records = _evaluations([
        {"session": "6/30/2026", "completed": "10/1/2026 10:00:00 AM", "101": "1", "102": "1"},
        {"session": "7/1/2026", "completed": "10/2/2026 10:00:00 AM", "101": "5", "102": "5"},
    ])
    state, _ = core.ingest_evaluations(_state(), records)
    rows = core.score_rows(state)
    current = core.filter_rows(rows, academic_year=2026, months=[7], start_month=7)
    assert current["record_id"].nunique() == 1
    assert current["period"].unique().tolist() == ["2026-07"]
    assert current["academic_month"].unique().tolist() == [1]
    assert core.epa_summary(current).iloc[0]["average"] == 5
    prior = core.filter_rows(rows, academic_year=2025, months=[6], start_month=7)
    assert prior["academic_month"].unique().tolist() == [12]
    assert core.epa_summary(prior).iloc[0]["average"] == 1


def test_edited_answer_requires_resolution_and_does_not_double_count():
    original = _evaluations([{"101": "2", "102": "4"}])
    edited = _evaluations([{"101": "5", "102": "4"}])
    state, _ = core.ingest_evaluations(_state(), original)
    state, stats = core.ingest_evaluations(state, edited)
    assert stats["conflicts"] == 1
    assert len(state["evaluations"]) == 1
    assert core.epa_summary(core.score_rows(state)).iloc[0]["average"] == 3
    conflict_id = next(iter(state["conflicts"]))
    resolved = core.resolve_conflict(state, conflict_id, "replace")
    assert len(resolved["evaluations"]) == 1
    assert core.epa_summary(core.score_rows(resolved)).iloc[0]["average"] == 4.5


def test_cumulative_months_reaggregate_answers_not_monthly_means():
    records = _evaluations([
        {"101": "1", "102": "5", "evaluator": "Faculty, One"},
        {"101": "5", "102": "", "evaluator": "Faculty, Two"},
        {"101": "5", "102": "1", "session": "8/1/2026"},
    ])
    state, _ = core.ingest_evaluations(_state(), records)
    rows = core.filter_rows(core.score_rows(state), academic_year=2026)
    cumulative = core.monthly_summary(rows, cumulative=True).set_index("period")
    assert cumulative.loc["2026-07", "average"] == 4
    assert cumulative.loc["2026-08", "average"] == pytest.approx(10 / 3)
    assert cumulative.loc["2026-08", "n_evaluations"] == 3


def test_mixed_forms_in_one_export_match_each_individual_row():
    headers = ["Session Date", "Date Completed", "Evaluator Name", "Subject Name", "Subject Rotation"]
    for position in (1, 2):
        headers += [f"[Question {position} ID]", f"[Question {position} Response]"]
    raw = _tab(headers, [
        ["7/1/2026", "7/30/2026 10:00:00 AM", "Faculty, One", "Resident, One", "Rotation A", "101", "4", "102", "2"],
        ["7/1/2026", "7/30/2026 10:01:00 AM", "Faculty, Two", "Resident, One", "Rotation B", "201", "5", "202", "3"],
    ])
    records = core.parse_upload(raw, "combined.tab")["evaluations"]
    state = _state(_form(("101", "102"), "EPA A"))
    state = core.register_form(state, _form(("201", "202"), "EPA B"))
    state, stats = core.ingest_evaluations(state, records)
    assert stats["added"] == 2
    result = core.epa_summary(core.score_rows(state)).set_index("epa")
    assert result.loc["EPA A", "average"] == 3
    assert result.loc["EPA B", "average"] == 4


@pytest.mark.parametrize("action, expected_count", [("keep", 1), ("replace", 1), ("add", 2)])
def test_changed_completion_time_is_reviewed_and_decision_survives_reupload(action, expected_count):
    original = _evaluations([{"101": "2", "102": "4"}])
    later = _evaluations([{"101": "5", "102": "4", "completed": "8/1/2026 10:00:00 AM"}])
    state, _ = core.ingest_evaluations(_state(), original)
    state, stats = core.ingest_evaluations(state, later)
    assert stats["conflicts"] == 1
    assert len(state["evaluations"]) == 1
    conflict_id = next(iter(state["conflicts"]))
    assert state["conflicts"][conflict_id]["kind"] == "possible_duplicate"
    resolved = core.resolve_conflict(state, conflict_id, action)
    assert len(resolved["evaluations"]) == expected_count
    again, stats = core.ingest_evaluations(resolved, original + later)
    assert len(again["evaluations"]) == expected_count
    assert not again["conflicts"]
    assert stats["duplicates"] == 2

