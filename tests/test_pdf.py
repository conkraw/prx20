"""PDF content and layout regressions with invented forms and residents."""

import copy
import io

import pandas as pd
import pytest
from pypdf import PdfReader

import epa_core as core
from epa_pdf import build_question_pdf, pdf_question_sections


CONTEXT = {
    "academic_year_label": "2026-2027",
    "months_label": "July, August",
    "date_basis_label": "Session month",
    "generated_at": "2026-11-03T12:00:00+00:00",
}


def _form(*, name="Example EPA", confirmed=True, prefix="1"):
    definitions = [
        (prefix + "02", "First selected question", True),
        (prefix + "01", "Administrative numeric item", False),
        (prefix + "03", "Narrative <b>exact</b> & source > text", False),
        (prefix + "04", "Excluded response code item", True),
        (prefix + "05", "Unanswered selected item", True),
        (prefix + "06", "Unmapped response item", True),
        (prefix + "07", "Explicit zero scale item", True),
    ]
    return {
        "id": core.form_id_for_questions([qid for qid, _, _ in definitions]),
        "name": name,
        "questions": [{"id": qid, "text": text, "include": included, "tags": ""}
                      for qid, text, included in definitions],
        "score_map": {str(n): float(n) for n in range(6)} | {"6": None},
        "scoring_confirmed": confirmed,
    }


def _record(*, resident="Example, Resident", evaluator="Example, Faculty", prefix="1", value="4", session="2026-07-01", completed="2026-10-01T10:00:00"):
    raw = {prefix + "02": value, prefix + "01": "5", prefix + "03": "",
           prefix + "04": "6", prefix + "05": "", prefix + "06": "9", prefix + "07": "0"}
    return {
        "resident": resident, "resident_key": core.normalize_key(resident),
        "evaluator": evaluator, "evaluator_key": core.normalize_key(evaluator),
        "evaluator_status": "Faculty", "rotation": "Example rotation",
        "session_date": session, "completed_at": completed,
        "responses": {qid: {"response": value, "comment": "", "dropdown": "", "tags": ""}
                      for qid, value in raw.items()},
        "overall_comments": "", "subject_comments": "", "source_files": ["example.tab"],
    }


def _state(*, confirmed=True):
    state = core.register_form(core.empty_state(), _form(confirmed=confirmed))
    return core.ingest_evaluations(state, [_record(), _record(evaluator="Example, Second", value="2")])[0]


def _sections(state):
    return pdf_question_sections(core.filter_rows(core.score_rows(state), academic_year=2026, months=[7, 8]), state["forms"])


def test_all_original_questions_stay_in_original_order_with_blank_noncalculating_scores():
    section = _sections(_state())[0]
    questions = section["questions"]
    assert [q["question_id"] for q in questions] == ["102", "101", "103", "104", "105", "106", "107"]
    assert questions[0]["average"] == 3
    assert questions[0]["average_text"] == "3.00"
    assert questions[0]["n"] == 2
    for question in questions[1:6]:
        assert question["average"] is None
        assert question["average_text"] == ""
    assert questions[6]["average"] == 0
    assert questions[6]["average_text"] == "0.00"
    assert section["n_evaluations"] == 2


def test_registry_gates_prevent_stale_numeric_cells_for_disabled_or_unconfirmed_questions():
    state = _state()
    rows = core.score_rows(state)
    rows.loc[rows.question_id.eq("101"), ["included", "status", "score"]] = [True, "scored", 999.0]
    questions = pdf_question_sections(rows, state["forms"])[0]["questions"]
    assert next(q for q in questions if q["question_id"] == "101")["average_text"] == ""
    forms = copy.deepcopy(state["forms"])
    next(iter(forms.values()))["scoring_confirmed"] = False
    assert all(q["average_text"] == "" for q in pdf_question_sections(rows, forms)[0]["questions"])


def test_question_registry_still_shows_questions_without_any_response_rows():
    state = _state()
    rows = core.score_rows(state)
    rows = rows.loc[~rows.question_id.eq("103")]
    questions = pdf_question_sections(rows, state["forms"])[0]["questions"]
    narrative = next(q for q in questions if q["question_id"] == "103")
    assert narrative["question"] == "Narrative <b>exact</b> & source > text"
    assert narrative["average_text"] == ""


def test_identically_named_forms_and_different_residents_remain_separate():
    state = _state()
    state = core.register_form(state, _form(prefix="2"))
    state, _ = core.ingest_evaluations(state, [
        _record(prefix="2", value="5"),
        _record(resident="Example, Another resident", prefix="1", value="1"),
    ])
    sections = _sections(state)
    assert len(sections) == 3
    assert len({(section["form_id"], section["resident_key"]) for section in sections}) == 3
    assert sorted(section["questions"][0]["average"] for section in sections) == [1, 3, 5]


def test_filtered_year_month_date_basis_and_resident_are_respected_without_expansion():
    state = core.register_form(core.empty_state(), _form())
    state, _ = core.ingest_evaluations(state, [
        _record(session="2026-06-30", value="1"),
        _record(session="2026-07-01", value="4"),
        _record(session="2026-08-01", value="2"),
        _record(resident="Example, Another resident", value="5"),
    ])
    rows = core.score_rows(state)
    chosen = core.filter_rows(rows, academic_year=2026, months=[7], residents=["Example, Resident"])
    section = pdf_question_sections(chosen, state["forms"])[0]
    assert section["n_evaluations"] == 1
    assert section["questions"][0]["average"] == 4
    completion = core.filter_rows(rows, academic_year=2026, months=[10], date_basis="completed_at", residents=["Example, Resident"])
    section = pdf_question_sections(completion, state["forms"])[0]
    assert section["n_evaluations"] == 3
    assert section["questions"][0]["average"] == pytest.approx(7 / 3)


def test_pdf_preserves_literal_source_markup_and_does_not_mutate_source():
    state = _state()
    rows = core.score_rows(state)
    original_rows = rows.copy(deep=True)
    original_forms = copy.deepcopy(state["forms"])
    data = build_question_pdf(rows, state["forms"], CONTEXT)
    assert data.startswith(b"%PDF-")
    text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages)
    assert "Narrative <b>exact</b> & source > text" in text
    assert "Example, Resident" in text
    assert "2026-2027" in text
    assert "July, August" in text
    assert "Session month" in text
    assert "3.00" in text
    assert "999.00" not in text
    assert state["forms"] == original_forms
    pd.testing.assert_frame_equal(rows, original_rows)


def test_long_question_can_span_pages_without_losing_start_or_end():
    state = _state()
    form = next(iter(state["forms"].values()))
    form["questions"][0]["text"] = "LONG_QUESTION_START " + " ".join(["long source question wording"] * 450) + " LONG_QUESTION_END"
    data = build_question_pdf(core.score_rows(state), state["forms"], CONTEXT)
    reader = PdfReader(io.BytesIO(data))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert len(reader.pages) > 1
    assert "LONG_QUESTION_START" in text
    assert "LONG_QUESTION_END" in text
    assert "Administrative numeric item" in text
    assert "Explicit zero scale item" in text
    assert all((page.extract_text() or "").count("Average") == 1 for page in reader.pages)


def test_two_digit_question_numbers_stay_on_one_line():
    form = _form()
    form["questions"] = [{"id": str(300 + index), "text": f"Original question number {index}.",
                         "include": True, "tags": ""} for index in range(1, 16)]
    state = core.register_form(core.empty_state(), form)
    record = _record()
    record["responses"] = {question["id"]: {"response": "4", "comment": "", "dropdown": "", "tags": ""}
                           for question in form["questions"]}
    state, _ = core.ingest_evaluations(state, [record])
    data = build_question_pdf(core.score_rows(state), state["forms"], CONTEXT)
    lines = [line.strip() for page in PdfReader(io.BytesIO(data)).pages
             for line in (page.extract_text() or "").splitlines()]
    assert all(str(number) in lines for number in range(10, 16))


def test_empty_filtered_export_is_reported_instead_of_making_a_misleading_pdf():
    state = _state()
    rows = core.filter_rows(core.score_rows(state), academic_year=2025)
    with pytest.raises(ValueError):
        build_question_pdf(rows, state["forms"], CONTEXT)

