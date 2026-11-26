"""Run the report workflow in the installed Streamlit runtime with invented data."""
import copy
from datetime import date
from io import BytesIO
from unittest.mock import patch

import pytest
import streamlit as st
from pypdf import PdfReader
from streamlit.testing.v1 import AppTest

from epa_core import empty_state
from epa_competency_import import parse_competency_pdf
from epa_competency_reports import linked_report, save_report_links
from epa_appd import digest
from test_competency_import import pdf_bytes, report_text


SCRIPT = '''
import copy
import streamlit as st
from epa_competency_ui import competency_reports_page
from epa_performance import invalidate
def commit(store, transform):
    try:
        latest, message = transform(copy.deepcopy(st.session_state["archive"]))
        st.session_state["archive"] = latest
        st.session_state["save_result"] = message
        st.session_state["saves"] = st.session_state.get("saves", 0) + 1
        invalidate(st.session_state)
        return True
    except ValueError as exc:
        st.error(str(exc))
        return False
competency_reports_page(st.session_state["archive"], None, commit)
'''


def ui_state():
    state = empty_state()
    state["settings"]["appd"] = {"start_date": "2026-07-01", "end_date": "2026-11-20"}
    state["evaluations"] = {
        "eval_one": {
            "id": "eval_one", "resident": "Example, Taylor", "resident_key": "example, taylor",
            "evaluator": "Dr. Fictional", "evaluator_status": "Faculty",
            "rotation": "Inpatient Pediatrics", "completed_at": "2026-08-15",
            "session_date": "2026-08-01", "overall_comments": "Clear updates to families.",
            "subject_comments": "My reflection.", "source_files": ["synthetic_evaluations.tab"],
            "responses": {"123": {"response": "4", "comment": "Reassessed the patient promptly.",
                                     "question_text": "Follow-up and reassessment"}},
        },
        "eval_other": {
            "id": "eval_other", "resident": "Other, Resident", "resident_key": "other, resident",
            "rotation": "Emergency", "completed_at": "2026-08-15", "responses": {},
            "overall_comments": "Private comment for the other resident.",
        },
    }
    return state


def app_for(state=None):
    app = AppTest.from_string(SCRIPT, default_timeout=15)
    app.session_state["archive"] = copy.deepcopy(ui_state() if state is None else state)
    app.run()
    assert not app.exception
    return app


def button(app, label):
    return next(item for item in app.button if item.label == label)


def upload(app, data=None, filename="synthetic_report.pdf"):
    app.file_uploader[0].set_value([(filename, data or pdf_bytes(report_text()), "application/pdf")]).run()
    assert not app.exception
    return app


def save_first(app):
    button(app, "Save 1 report and link comments").click().run()
    assert not app.exception
    assert not app.error


def test_upload_confirm_save_view_and_download_real_streamlit():
    with patch("streamlit.download_button", wraps=st.download_button) as download:
        app = upload(app_for())
        assert not app.error
        assert app.selectbox[0].value == "example, taylor"
        assert app.date_input[0].value == date(2026, 7, 1)
        assert len(app.dataframe) == 6
        for frame in app.dataframe:
            assert set(frame.value["Score"]) == {"2 - 2.5"}
        save_first(app)
        assert app.radio(key="competency_action").value == "View saved reports"
        saved = app.session_state["archive"]["competency_reports"]
        assert len(saved) == 1
        report = next(iter(saved.values()))
        assert report["resident_key"] == "example, taylor"
        callback = next(call.kwargs["data"] for call in reversed(download.call_args_list)
                        if call.args[0] == "Download combined report (.pdf)")
        result = PdfReader(BytesIO(callback()))
        text = "\n".join(page.extract_text() for page in result.pages)
        assert "2 - 2.5" in text
        assert "Inpatient Pediatrics" in text
        assert "Clear updates to families." in text
        assert "Reassessed the patient promptly." in text
        assert "Private comment for the other resident." not in text
        assert app.session_state["archive"]["evaluations"] == ui_state()["evaluations"]


def test_unknown_pdf_name_is_not_assigned_to_first_resident():
    app = upload(app_for(), pdf_bytes(report_text().replace("Taylor", "Jordan")))
    assert app.selectbox[0].value == "__competency_source_name__"
    save_first(app)
    saved = next(iter(app.session_state["archive"]["competency_reports"].values()))
    assert saved["resident_key"] == "jordan example"
    assert any("No saved narrative comments" in item.value for item in app.info)


def test_ambiguous_source_name_cannot_silently_link_to_same_normalized_key():
    state = ui_state()
    state["evaluations"]["ambiguous"] = {
        "resident_key": "taylor example", "resident": "Taylor Example", "responses": {},
        "completed_at": "2026-08-02", "overall_comments": "Separate resident record.",
    }
    app = upload(app_for(state))
    assert app.selectbox[0].value == "__competency_source_name__"
    assert button(app, "Save 1 report and link comments").disabled
    assert any("More than one saved resident" in item.value for item in app.warning)
    assert not app.session_state["archive"].get("competency_reports")
    app.selectbox[0].set_value("example, taylor").run()
    assert not button(app, "Save 1 report and link comments").disabled
    save_first(app)
    saved = next(iter(app.session_state["archive"]["competency_reports"].values()))
    assert saved["resident_key"] == "example, taylor"


def test_user_can_choose_comments_dates_and_correct_saved_resident_link():
    app = upload(app_for())
    app.date_input[0].set_value(date(2025, 7, 1))
    app.date_input[1].set_value(date(2025, 12, 31))
    save_first(app)
    saved = next(iter(app.session_state["archive"]["competency_reports"].values()))
    assert saved["comment_start"] == "2025-07-01"
    assert any("No saved narrative comments" in item.value for item in app.info)
    match = next(box for box in app.selectbox if box.label == "Link comments for resident")
    match.set_value("other, resident")
    app.date_input[0].set_value(date(2026, 7, 1))
    app.date_input[1].set_value(date(2026, 11, 20))
    button(app, "Save resident and dates").click().run()
    assert not app.exception and not app.error
    saved = next(iter(app.session_state["archive"]["competency_reports"].values()))
    assert saved["resident_key"] == "other, resident"
    assert saved["resident"] == "Taylor Example"
    assert saved["comment_start"] == "2026-07-01"
    assert app.session_state["saves"] == 2


def test_invalid_period_does_not_save_and_keeps_upload_for_correction():
    app = upload(app_for())
    app.date_input[0].set_value(date(2027, 7, 1))
    button(app, "Save 1 report and link comments").click().run()
    assert not app.exception
    assert any("valid comment date range" in item.value for item in app.error)
    assert not app.session_state["archive"].get("competency_reports")
    assert app.file_uploader[0].value


def test_mixed_batch_saves_only_readable_files_and_skips_identical_bytes():
    app = app_for()
    source = pdf_bytes(report_text())
    app.file_uploader[0].set_value([
        ("valid.pdf", source, "application/pdf"),
        ("copy.pdf", source, "application/pdf"),
        ("not_a_report.pdf", b"bad file", "application/pdf"),
    ]).run()
    assert not app.exception
    assert len(app.error) == 1
    assert any("Only the readable reports" in item.value for item in app.warning)
    assert any("Duplicate file skipped" in item.value for item in app.caption)
    save_first(app)
    assert len(app.session_state["archive"]["competency_reports"]) == 1


def test_report_can_be_saved_before_evaluations_exist():
    app = upload(app_for(empty_state()))
    save_first(app)
    saved = next(iter(app.session_state["archive"]["competency_reports"].values()))
    assert saved["linked_resident"] == "Taylor Example"
    assert len(app.dataframe) == 6
    assert any("No saved narrative comments" in item.value for item in app.info)


def test_upload_parse_is_reused_on_resident_selection_rerun():
    with patch("epa_competency_import.parse_competency_pdf", wraps=parse_competency_pdf) as parser:
        app = upload(app_for())
        assert parser.call_count == 1
        app.selectbox[0].set_value("other, resident").run()
        assert not app.exception
        assert parser.call_count == 1


def test_existing_admin_routes_to_new_workspace():
    script = '''
import streamlit as st
from epa_simple_ui import admin_page
admin_page(st.session_state["archive"], None, lambda *args: False,
    forms_page=lambda *args: None, review_page=lambda *args: None, settings_page=lambda *args: None)
'''
    app = AppTest.from_string(script, default_timeout=15)
    app.session_state["archive"] = empty_state()
    app.run()
    app.selectbox(key="simple_admin_section").set_value("Competency reports and comments").run()
    assert not app.exception and not app.error
    assert app.file_uploader[0].label == "Competency report PDFs"


def test_saved_report_opens_without_reparsing_original_pdf():
    data = pdf_bytes(report_text())
    parsed = parse_competency_pdf(data)
    entry = linked_report(parsed, data, resident_key="example, taylor", resident_name="Example, Taylor",
                          start_date="2026-07-01", end_date="2026-11-20")
    state, _ = save_report_links(ui_state(), [entry], {entry["id"]: digest(None)})
    with patch("epa_competency_import.parse_competency_pdf", side_effect=AssertionError("unneeded parse")):
        app = app_for(state)
        assert not app.error
        assert len(app.dataframe) == 6

