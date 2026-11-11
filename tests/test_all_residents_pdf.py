"""Bulk sidebar export broadens only the resident filter."""

import pytest
pytest.importorskip("streamlit", reason="Streamlit runtime is not installed in this test environment")

import copy
from unittest.mock import patch

import pandas as pd
import pytest
import streamlit as st

import epa_pdf
from epa_auth import hash_password
from test_pdf_ui import _report_state
from test_ui import MemoryStore, PASSWORD, _app, _sign_in


INDIVIDUAL = "Download PDF"
BULK = "Download PDF for all residents"


@pytest.fixture(scope="module")
def bulk_password_hash():
    return hash_password(PASSWORD)


def _call(download, label):
    return [call for call in download.call_args_list
            if (call.kwargs.get("label") or (call.args[0] if call.args else "")) == label][-1]


def _data(download, label):
    call = _call(download, label)
    return call.kwargs.get("data", call.args[1] if len(call.args) > 1 else None)


def _sections(builder):
    rows, forms, context = builder.call_args.args
    return epa_pdf.pdf_question_sections(rows, forms), rows, context


def _first_form(state):
    return next(key for key, form in state["forms"].items() if form["name"] == "Example EPA")


def test_bulk_export_bypasses_only_residents_and_preserves_individual_selection(bulk_password_hash):
    with patch("epa_storage.GitHubStore", MemoryStore), \
            patch("streamlit.download_button", wraps=st.download_button) as download, \
            patch("epa_pdf.build_question_pdf", return_value=b"%PDF-synthetic") as builder:
        state = _report_state()
        first_form = _first_form(state)
        app = _app(bulk_password_hash, state)
        _sign_in(app)
        app.multiselect(key="months_7").set_value([7]).run()
        app.multiselect(key="dashboard_residents").set_value(["example, resident"]).run()
        app.multiselect(key="dashboard_epas").set_value([first_form]).run()
        assert not app.exception
        assert builder.call_count == 0
        preferences = copy.deepcopy(app.session_state["_epa_report_filters"])
        individual_callback = _data(download, INDIVIDUAL)
        individual_callback()
        individual, _, _ = _sections(builder)
        assert len(individual) == 1
        assert individual[0]["questions"][0]["average"] == 4

        bulk_callback = _data(download, BULK)
        assert callable(bulk_callback)
        assert _call(download, BULK).kwargs.get("on_click") == "ignore"
        bulk_callback()
        sections, rows, context = _sections(builder)
        assert {section["resident_key"] for section in sections} == {"example, resident", "example, other resident"}
        assert {section["form_id"] for section in sections} == {first_form}
        assert sorted(section["questions"][0]["average"] for section in sections) == [4, 5]
        assert set(rows["period"]) == {"2026-07"}
        assert set(rows["academic_year"]) == {2026}
        assert context["academic_year_label"] == "2026-2027"
        assert context["months_label"] == "Jul 2026"
        assert context["date_basis_label"] == "Session month"
        for section in sections:
            assert len(section["questions"]) == 7
            assert next(question for question in section["questions"] if question["question_id"] == "101")["average_text"] == ""
        assert app.session_state["_epa_report_filters"] == preferences
        assert app.multiselect(key="dashboard_residents").value == ["example, resident"]
        assert MemoryStore.writes == 0

        app.sidebar.radio[0].set_value("Admin").run()
        app.selectbox(key="simple_admin_section").set_value("Data review").run()
        assert not app.exception
        _data(download, BULK)()
        sections, _, _ = _sections(builder)
        assert len(sections) == 2
        assert app.session_state["_epa_report_filters"] == preferences
        app.sidebar.radio[0].set_value("Dashboard").run()
        assert not app.exception
        assert app.multiselect(key="dashboard_residents").value == ["example, resident"]
        assert app.multiselect(key="dashboard_epas").value == [first_form]
        assert app.multiselect(key="months_7").value == [7]
        individual_callback()
        assert len(_sections(builder)[0]) == 1


def test_bulk_remains_available_when_selected_resident_has_no_rows_then_disables_for_empty_period(bulk_password_hash):
    with patch("epa_storage.GitHubStore", MemoryStore), \
            patch("streamlit.download_button", wraps=st.download_button) as download, \
            patch("epa_pdf.build_question_pdf", return_value=b"%PDF-synthetic") as builder:
        state = _report_state()
        app = _app(bulk_password_hash, state)
        _sign_in(app)
        app.multiselect(key="dashboard_residents").set_value(["example, other resident"]).run()
        app.multiselect(key="dashboard_epas").set_value([_first_form(state)]).run()
        app.multiselect(key="months_7").set_value([8]).run()
        assert not app.exception
        assert _call(download, INDIVIDUAL).kwargs.get("disabled") is True
        assert _data(download, INDIVIDUAL) == b""
        assert _call(download, BULK).kwargs.get("disabled", False) is False
        _data(download, BULK)()
        sections, rows, context = _sections(builder)
        assert len(sections) == 1
        assert sections[0]["resident_key"] == "example, resident"
        assert sections[0]["questions"][0]["average"] == 2
        assert context["months_label"] == "Aug 2026"
        assert set(rows["period"]) == {"2026-08"}
        assert app.multiselect(key="dashboard_residents").value == ["example, other resident"]
        app.multiselect(key="months_7").set_value([]).run()
        assert not app.exception
        for label in [INDIVIDUAL, BULK]:
            assert _call(download, label).kwargs.get("disabled") is True
            assert _data(download, label) == b""


def test_no_resident_filter_makes_both_exports_equal_and_keeps_completion_basis(bulk_password_hash):
    with patch("epa_storage.GitHubStore", MemoryStore), \
            patch("streamlit.download_button", wraps=st.download_button) as download, \
            patch("epa_pdf.build_question_pdf", return_value=b"%PDF-synthetic") as builder:
        state = _report_state()
        app = _app(bulk_password_hash, state)
        _sign_in(app)
        app.multiselect(key="dashboard_epas").set_value([_first_form(state)]).run()
        app.radio(key="dashboard_date_basis").set_value("Completion month").run()
        app.multiselect(key="months_7").set_value([10]).run()
        assert not app.exception
        _data(download, INDIVIDUAL)()
        individual_sections, individual_rows, individual_context = _sections(builder)
        individual_rows = individual_rows.copy(deep=True)
        _data(download, BULK)()
        bulk_sections, bulk_rows, bulk_context = _sections(builder)
        pd.testing.assert_frame_equal(individual_rows, bulk_rows)
        assert individual_sections == bulk_sections
        assert bulk_context["date_basis_label"] == individual_context["date_basis_label"] == "Completion month"
        assert bulk_context["months_label"] == "Oct 2026"
        assert len(bulk_sections) == 2
        resident = next(section for section in bulk_sections if section["resident_key"] == "example, resident")
        assert resident["n_evaluations"] == 3
        assert resident["questions"][0]["average"] == pytest.approx(7 / 3)
        assert app.multiselect(key="dashboard_residents").value == []

