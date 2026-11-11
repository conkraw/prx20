"""Sidebar PDF filters and deferred-export snapshots, using only fake data."""

import pytest
pytest.importorskip("streamlit", reason="Streamlit runtime is not installed in this test environment")

import copy
from unittest.mock import patch

import pytest
import streamlit as st

import epa_core as core
import epa_pdf
from epa_auth import hash_password
from test_pdf import _form, _record
from test_ui import MemoryStore, PASSWORD, _app, _button, _sign_in


@pytest.fixture(scope="module")
def pdf_password_hash():
    return hash_password(PASSWORD)


def _report_state():
    state = core.register_form(core.empty_state(), _form())
    state = core.register_form(state, _form(name="Other EPA", prefix="2"))
    state, _ = core.ingest_evaluations(state, [
        _record(value="4", session="2026-07-01"),
        _record(value="2", session="2026-08-01"),
        _record(value="1", session="2025-07-01"),
        _record(resident="Example, Other resident", value="5", session="2026-07-01"),
        _record(prefix="2", value="5", session="2026-07-01"),
    ])
    return state


def _last_pdf_call(download):
    return [call for call in download.call_args_list
            if (call.kwargs.get("label") or (call.args[0] if call.args else "")) == "Download PDF"][-1]


def _callback(download):
    call = _last_pdf_call(download)
    return call.kwargs.get("data", call.args[1] if len(call.args) > 1 else None)


def _built_sections(builder):
    rows, forms, context = builder.call_args.args
    return epa_pdf.pdf_question_sections(rows, forms), context


def test_sidebar_pdf_uses_current_filters_and_keeps_them_across_navigation(pdf_password_hash):
    original_builder = epa_pdf.build_question_pdf
    with patch("epa_storage.GitHubStore", MemoryStore), \
            patch("streamlit.download_button", wraps=st.download_button) as download, \
            patch("epa_pdf.build_question_pdf", wraps=original_builder) as builder:
        state = _report_state()
        app = _app(pdf_password_hash, state)
        _sign_in(app)
        assert builder.call_count == 0
        first_form = next(key for key, form in state["forms"].items() if form["name"] == "Example EPA")
        app.multiselect(key="months_7").set_value([7]).run()
        app.multiselect(key="dashboard_residents").set_value(["example, resident"]).run()
        app.multiselect(key="dashboard_epas").set_value([first_form]).run()
        assert not app.exception
        july_callback = _callback(download)
        assert callable(july_callback)
        assert _last_pdf_call(download).kwargs.get("on_click") == "ignore"
        assert july_callback().startswith(b"%PDF-")
        sections, context = _built_sections(builder)
        assert len(sections) == 1
        assert sections[0]["n_evaluations"] == 1
        assert sections[0]["questions"][0]["average"] == 4
        assert context["academic_year_label"] == "2026-2027"
        assert context["months_label"] == "Jul 2026"
        assert context["date_basis_label"] == "Session month"
        for page in ["Forms", "Review", "Settings", "Upload"]:
            app.sidebar.radio[0].set_value(page).run()
            assert not app.exception, page
            _callback(download)()
            sections, context = _built_sections(builder)
            assert len(sections) == 1
            assert sections[0]["questions"][0]["average"] == 4
            assert context["months_label"] == "Jul 2026"
        app.sidebar.radio[0].set_value("Dashboard").run()
        assert app.multiselect(key="months_7").value == [7]
        app.multiselect(key="months_7").set_value([8]).run()
        _callback(download)()
        sections, context = _built_sections(builder)
        assert sections[0]["questions"][0]["average"] == 2
        assert context["months_label"] == "Aug 2026"
        july_callback()
        sections, context = _built_sections(builder)
        assert sections[0]["questions"][0]["average"] == 4
        assert context["months_label"] == "Jul 2026"


def test_pdf_date_basis_year_and_empty_month_selection_are_exact(pdf_password_hash):
    original_builder = epa_pdf.build_question_pdf
    with patch("epa_storage.GitHubStore", MemoryStore), \
            patch("streamlit.download_button", wraps=st.download_button) as download, \
            patch("epa_pdf.build_question_pdf", wraps=original_builder) as builder:
        state = _report_state()
        app = _app(pdf_password_hash, state)
        _sign_in(app)
        first_form = next(key for key, form in state["forms"].items() if form["name"] == "Example EPA")
        app.multiselect(key="dashboard_residents").set_value(["example, resident"]).run()
        app.multiselect(key="dashboard_epas").set_value([first_form]).run()
        app.selectbox(key="dashboard_year_7").set_value(2025).run()
        app.multiselect(key="months_7").set_value([7]).run()
        _callback(download)()
        sections, context = _built_sections(builder)
        assert sections[0]["questions"][0]["average"] == 1
        assert context["academic_year_label"] == "2025-2026"
        app.selectbox(key="dashboard_year_7").set_value(2026).run()
        app.radio(key="dashboard_date_basis").set_value("Completion month").run()
        app.multiselect(key="months_7").set_value([10]).run()
        assert not app.exception
        _callback(download)()
        sections, context = _built_sections(builder)
        assert sections[0]["n_evaluations"] == 3
        assert sections[0]["questions"][0]["average"] == pytest.approx(7 / 3)
        assert context["date_basis_label"] == "Completion month"
        assert context["months_label"] == "Oct 2026"
        app.multiselect(key="months_7").set_value([]).run()
        assert not app.exception
        assert _last_pdf_call(download).kwargs["disabled"] is True
        assert _callback(download) == b""


def test_saved_scoring_change_refreshes_pdf_without_mutating_old_snapshot(pdf_password_hash):
    original_builder = epa_pdf.build_question_pdf
    with patch("epa_storage.GitHubStore", MemoryStore), \
            patch("streamlit.download_button", wraps=st.download_button) as download, \
            patch("epa_pdf.build_question_pdf", wraps=original_builder) as builder:
        state = _report_state()
        app = _app(pdf_password_hash, state)
        _sign_in(app)
        first_form = next(key for key, form in state["forms"].items() if form["name"] == "Example EPA")
        app.multiselect(key="months_7").set_value([7]).run()
        app.multiselect(key="dashboard_residents").set_value(["example, resident"]).run()
        app.multiselect(key="dashboard_epas").set_value([first_form]).run()
        original_callback = _callback(download)
        app.sidebar.radio[0].set_value("Admin").run()
        app.selectbox(key="simple_admin_section").set_value("Forms and EPA mapping").run()
        form_selector = next(box for box in app.selectbox if box.label == "Form")
        form_selector.set_value(first_form).run()
        confirmation = next(box for box in app.checkbox if box.label.startswith("I checked"))
        confirmation.uncheck().run()
        _button(app, "Save form setup").click().run()
        assert not app.exception
        assert not app.error
        assert not MemoryStore.saved["forms"][first_form]["scoring_confirmed"]
        _callback(download)()
        sections, _ = _built_sections(builder)
        assert all(question["average_text"] == "" for question in sections[0]["questions"])
        original_callback()
        sections, _ = _built_sections(builder)
        assert sections[0]["questions"][0]["average"] == 4

