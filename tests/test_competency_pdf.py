"""Synthetic PDF checks. No resident data or source uploads are bundled here."""
from __future__ import annotations

import copy
import io
import re

import pytest
from pypdf import PdfReader

from epa_competency_pdf import build_competency_comments_pdf


def sample_report():
    codes = [f"{prefix}{n}" for prefix, count in [
        ("PC", 5), ("MK", 2), ("SBP", 6), ("PBLI", 2), ("PROF", 4), ("ICS", 3)
    ] for n in range(1, count + 1)]
    return {
        "resident": "Alex River", "linked_resident": "River, Alex",
        "resident_key": "river, alex", "program": "Example Pediatric Residency",
        "season": "FALL", "pgy": 2, "source_filename": "Example_competency_report.pdf",
        "comment_start": "2026-07-01", "comment_end": "2026-12-31",
        "scores": [{"code": code, "label": code,
                    "score": "0" if code == "MK2" else "2 - 2.5", "domain": ""}
                   for code in codes], "warnings": [],
    }


def sample_evaluation():
    return {
        "id": "eval_example_001", "resident": "River, Alex",
        "date": "2026-08-20", "date_basis": "completed_at",
        "evaluator": "Dr. Example", "evaluator_status": "Faculty",
        "rotation": "General Pediatrics", "form_name": "Example EPA Form",
        "source_files": ["example.tab"],
        "feedback": [
            {"kind": "overall_comments", "text": "Explained the plan clearly."},
            {"kind": "question_comment", "question_order": 2, "question_id": "q2",
             "question": "What went well?", "text": "Listened carefully & followed up.\nA second original line."},
        ],
        "resident_comments": [{"kind": "subject_comments", "text": "My reflection is kept separate."}],
        "notes": [],
    }


def sample_comments():
    return {
        "resident_key": "river, alex", "start_date": "2026-07-01", "end_date": "2026-12-31",
        "rotations": [{"rotation": "General Pediatrics", "evaluations": [sample_evaluation()]}],
        "undated": [], "notes": [], "counts": {},
    }


def read_pdf(report=None, comments=None):
    data = build_competency_comments_pdf(report or sample_report(), comments or sample_comments())
    return PdfReader(io.BytesIO(data))


def all_text(reader):
    return "\n".join(page.extract_text() for page in reader.pages)


def test_scores_precede_comments_and_all_source_values_are_retained():
    reader = read_pdf()
    text = all_text(reader)
    first = reader.pages[0].extract_text()
    assert "Competency scores and rotation comments" in first
    for row in sample_report()["scores"]:
        assert row["code"] in first
    assert first.count("2 - 2.5") == 21
    assert re.search(r"MK2\s+0\b", first)
    assert "2.25" not in text
    assert text.index("APPD recommendations as reported") < text.index("Comments by rotation")
    assert text.index("Comments by rotation") < text.index("Explained the plan clearly.")
    assert "not final CCC decisions" in text


def test_question_context_source_and_resident_comments_are_separate():
    text = all_text(read_pdf())
    assert "River, Alex" in text
    assert "Name on source report: Alex River" in text
    assert "FALL | PGY 2" in text
    assert "2026-07-01 to 2026-12-31, inclusive" in text
    assert "General Pediatrics" in text
    assert "Evaluator: Dr. Example (Faculty)" in text
    assert "Source: example.tab" in text
    assert "Question 2: What went well?" in text
    assert "Listened carefully & followed up." in text
    assert "A second original line." in text
    assert text.index("Resident comments") < text.index("My reflection is kept separate.")
    assert "Evaluation reference: eval_example_001" in text


def test_html_looking_feedback_is_printed_literally_and_unicode_survives():
    comments = sample_comments()
    comments["rotations"][0]["evaluations"][0]["feedback"][0]["text"] = "Zoë saw <b>literal tags</b> & said ‘well done’.\nSecond line."
    text = all_text(read_pdf(comments=comments))
    assert "Zoë saw <b>literal tags</b> & said ‘well done’." in text
    assert "Second line." in text


def test_undated_evaluations_follow_all_dated_rotation_comments():
    comments = sample_comments()
    undated = sample_evaluation()
    undated.update(date=None, date_basis="unknown", rotation="Example Clinic", id="undated_002")
    undated["feedback"] = [{"kind": "overall_comments", "text": "Undated comment marker."}]
    undated["resident_comments"] = []
    comments["undated"] = [undated]
    text = all_text(read_pdf(comments=comments))
    assert text.index("Explained the plan clearly.") < text.index("Comments with no usable date")
    assert "cannot be assigned to the selected period" in text
    assert "Rotation: Example Clinic" in text
    assert text.index("Comments with no usable date") < text.index("Undated comment marker.")


def test_no_comments_produces_explicit_empty_message_without_losing_scores():
    comments = sample_comments()
    comments["rotations"] = []
    text = all_text(read_pdf(comments=comments))
    assert "No dated evaluation comments are available" in text
    assert "PC1" in text and "2 - 2.5" in text


def test_two_short_rotations_fit_without_orphaned_provenance():
    comments = sample_comments()
    first = comments["rotations"][0]["evaluations"][0]
    first["feedback"] = [{"kind": "overall_comments",
                          "text": "Explained the plan clearly and followed up with families."}]
    first["resident_comments"] = []
    second = sample_evaluation()
    second.update(id="eval_example_002", date="2026-09-15", rotation="Pediatric Emergency",
                  form_name="Example Emergency Evaluation")
    second["feedback"] = [{"kind": "question_comment", "question_id": "q4", "question_order": 4,
                           "question": "What went well?",
                           "text": "Recognized a change in the patient and asked for help promptly."}]
    second["resident_comments"] = []
    comments["rotations"].append({"rotation": "Pediatric Emergency", "evaluations": [second]})
    reader = read_pdf(comments=comments)
    assert len(reader.pages) == 1
    text = all_text(reader)
    assert text.index("eval_example_002") < text.index("Recognized a change in the patient")


def test_session_date_fallback_is_labeled():
    comments = sample_comments()
    comments["rotations"][0]["evaluations"][0]["date_basis"] = "session_date"
    assert "Session date used because completion date is unavailable" in all_text(read_pdf(comments=comments))


def test_long_feedback_spans_pages_without_loss(tmp_path):
    comments = sample_comments()
    long_text = "Opening unique marker. " + "Careful observation and useful narrative feedback. " * 900 + " Final unique marker."
    comments["rotations"][0]["evaluations"][0]["feedback"] = [
        {"kind": "question_comment", "question_id": "long", "question_order": 8,
         "question": "Detailed feedback", "text": long_text}
    ]
    report = sample_report()
    data = build_competency_comments_pdf(report, comments)
    path = tmp_path / "long_comments.pdf"
    path.write_bytes(data)
    reader = PdfReader(io.BytesIO(data))
    text = all_text(reader)
    assert len(reader.pages) >= 5
    assert "Opening unique marker." in text
    assert "Final unique marker." in text
    assert len(re.findall(r"\bCareful\b", text)) == 900
    assert len(re.findall(r"\bobservation\b", text)) == 900
    for i, page in enumerate(reader.pages, 1):
        assert "EPA TRACKER | River, Alex" in page.extract_text()
        assert f"Page {i}" in page.extract_text()


def test_large_unbroken_word_can_split_without_layout_error():
    comments = sample_comments()
    comments["rotations"][0]["evaluations"][0]["feedback"][0]["text"] = "Z" * 5000 + " WORD_END"
    text = all_text(read_pdf(comments=comments))
    assert "WORD_END" in text
    assert sum(len(run) for run in re.findall(r"Z{10,}", text)) == 5000


def test_renderer_does_not_mutate_report_or_comments():
    report = sample_report()
    report["scores"].reverse()
    comments = sample_comments()
    original = copy.deepcopy((report, comments))
    build_competency_comments_pdf(report, comments)
    assert (report, comments) == original


def test_different_resident_is_rejected():
    comments = sample_comments()
    comments["resident_key"] = "someone else"
    with pytest.raises(ValueError, match="different resident"):
        read_pdf(comments=comments)


def test_mismatched_period_is_rejected():
    comments = sample_comments()
    comments["end_date"] = "2027-06-30"
    with pytest.raises(ValueError, match="comment dates changed"):
        read_pdf(comments=comments)


@pytest.mark.parametrize("problem", ["duplicate", "missing_value", "missing_code", "no_scores"])
def test_incomplete_or_duplicate_scores_are_rejected(problem):
    report = sample_report()
    if problem == "duplicate":
        report["scores"].append(copy.deepcopy(report["scores"][0]))
    elif problem == "missing_value":
        report["scores"][0]["score"] = ""
    elif problem == "missing_code":
        report["scores"][0].update(code="", label="")
    else:
        report["scores"] = []
    with pytest.raises(ValueError):
        read_pdf(report=report)
