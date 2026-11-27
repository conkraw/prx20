"""Exercise automatic resident links and local-date drafts through Streamlit."""
import copy
from datetime import date, datetime, timezone
from io import BytesIO
from unittest.mock import patch

import pytest
import streamlit as st
from pypdf import PdfReader

from epa_appd import digest
from epa_competency_import import parse_competency_pdf
from epa_competency_reports import choose_resident_link, linked_report, save_report_links
from test_competency_import import pdf_bytes, report_text
from test_competency_ui import app_for, button, save_first, selector, ui_state, upload


def through(app):
    return next(item for item in app.date_input if item.label == "Through")


def saved_state(*, end="2026-11-20"):
    data = pdf_bytes(report_text())
    parsed = parse_competency_pdf(data)
    entry = linked_report(parsed, data, resident_key="example, taylor", resident_name="Example, Taylor",
                          start_date="2026-07-01", end_date=end, date_basis="session_date")
    state, _ = save_report_links(ui_state(), [entry], {entry["id"]: digest(None)})
    return state


def download_text(mock):
    callback = next(call.kwargs["data"] for call in reversed(mock.call_args_list)
                    if call.args[0] == "Download combined report (.pdf)")
    return "\n".join(page.extract_text() for page in PdfReader(BytesIO(callback())).pages)


@pytest.mark.parametrize("program_end", ["2026-11-20", "2027-06-30"])
def test_new_upload_through_uses_today_instead_of_program_end(program_end):
    state = ui_state()
    state["settings"]["appd"]["end_date"] = program_end
    with patch("epa_competency_ui._today", return_value=date(2026, 11, 26)):
        app = upload(app_for(state))
        assert through(app).value == date(2026, 11, 26)
        save_first(app)
        record = next(iter(app.session_state["archive"]["competency_reports"].values()))
        assert record["comment_end"] == "2026-11-26"
        assert app.session_state["archive"]["settings"] == state["settings"]


def test_existing_report_prefills_today_but_updates_comments_and_pdf_only_when_saved():
    state = saved_state()
    recent = copy.deepcopy(state["evaluations"]["eval_one"])
    recent.update(id="recent", session_date="2026-11-25", completed_at="2026-11-25",
                  overall_comments="A newly available fictional comment.", responses={})
    state["evaluations"]["recent"] = recent
    with patch("epa_competency_ui._today", return_value=date(2026, 11, 26)), \
         patch("streamlit.download_button", wraps=st.download_button) as download:
        app = app_for(state)
        assert through(app).value == date(2026, 11, 26)
        assert app.session_state["archive"] == state
        assert any("through 2026-11-20" in item.value for item in app.caption)
        assert not any("newly available" in item.value for item in app.text)
        assert "newly available" not in download_text(download)
        button(app, "Update comments").click().run()
        assert not app.exception and not app.error
        record = next(iter(app.session_state["archive"]["competency_reports"].values()))
        assert record["comment_end"] == "2026-11-26"
        assert any("through 2026-11-26" in item.value for item in app.caption)
        assert any("newly available" in item.value for item in app.text)
        assert "A newly available fictional comment." in download_text(download)


def test_explicit_cutoff_survives_rerun_upload_save_and_report_revision():
    with patch("epa_competency_ui._today", return_value=date(2026, 11, 26)):
        app = upload(app_for())
        through(app).set_value(date(2026, 11, 22)).run()
        selector(app, "Link comments for resident").set_value("other, resident").run()
        assert through(app).value == date(2026, 11, 22)
        save_first(app)
        assert through(app).value == date(2026, 11, 22)
        through(app).set_value(date(2026, 11, 23))
        button(app, "Update comments").click().run()
        assert not app.exception and not app.error
        assert through(app).value == date(2026, 11, 23)
        record = next(iter(app.session_state["archive"]["competency_reports"].values()))
        assert record["comment_end"] == "2026-11-23"
        app.run()
        assert through(app).value == date(2026, 11, 23)


def test_untouched_default_advances_after_local_midnight_without_saving_report():
    state = saved_state()
    with patch("epa_competency_ui._today", return_value=date(2026, 11, 26)) as today:
        app = app_for(state)
        assert through(app).value == date(2026, 11, 26)
        today.return_value = date(2026, 11, 27)
        app.run()
        assert through(app).value == date(2026, 11, 27)
        assert app.session_state["archive"] == state


def test_explicit_future_cutoff_stays_fixed_when_that_day_arrives_and_passes():
    with patch("epa_competency_ui._today", return_value=date(2026, 11, 26)) as today:
        app = app_for(saved_state())
        through(app).set_value(date(2026, 11, 27))
        button(app, "Update comments").click().run()
        saved = copy.deepcopy(app.session_state["archive"])
        for day in (27, 28):
            today.return_value = date(2026, 11, day)
            app.run()
            assert through(app).value == date(2026, 11, 27)
            assert app.session_state["archive"] == saved


@pytest.mark.parametrize("existing", [True, False])
def test_cutoff_edited_before_midnight_survives_submission_after_midnight(existing):
    with patch("epa_competency_ui._today", return_value=date(2026, 11, 26)) as today:
        app = app_for(saved_state()) if existing else upload(app_for())
        through(app).set_value(date(2026, 10, 31))
        today.return_value = date(2026, 11, 27)
        label = "Update comments" if existing else "Save 1 report and link comments"
        button(app, label).click().run()
        assert not app.exception and not app.error
        record = next(iter(app.session_state["archive"]["competency_reports"].values()))
        assert record["comment_end"] == "2026-10-31"
        assert through(app).value == date(2026, 10, 31)


def test_local_today_uses_new_york_when_utc_calendar_day_is_already_tomorrow():
    from epa_competency_ui import _today
    instant = datetime(2026, 11, 27, 1, 30, tzinfo=timezone.utc)
    with patch("epa_competency_ui.datetime") as clock:
        clock.now.side_effect = lambda tz: instant.astimezone(tz)
        assert _today() == date(2026, 11, 26)
        assert str(clock.now.call_args.args[0]) == "America/New_York"


def test_manual_resident_override_is_remembered_after_reload_for_a_different_pdf():
    with patch("epa_competency_ui._today", return_value=date(2026, 11, 26)):
        app = upload(app_for())
        assert selector(app, "Link comments for resident").value == "example, taylor"
        selector(app, "Link comments for resident").set_value("other, resident").run()
        through(app).set_value(date(2026, 11, 24)).run()
        assert selector(app, "Link comments for resident").value == "other, resident"
        save_first(app)
        archive = app.session_state["archive"]
        assert choose_resident_link(archive, "Taylor Example")["method"] == "remembered"
        app = app_for(archive)
        app.radio(key="competency_action").set_value("Upload reports").run()
        data = pdf_bytes(report_text().replace("2 - 2.5", "3 - 3.5"))
        upload(app, data, "later_report.pdf")
        assert selector(app, "Link comments for resident").value == "other, resident"
        assert any("Using your saved resident link" in item.value for item in app.caption)
        save_first(app)
        assert len(app.session_state["archive"]["competency_reports"]) == 2
        assert {entry["resident_key"] for entry in app.session_state["archive"]["competency_reports"].values()} == {"other, resident"}


def test_changed_target_name_refreshes_best_match_instead_of_retaining_outdated_link():
    with patch("epa_competency_ui._today", return_value=date(2026, 11, 26)):
        app = upload(app_for())
        selector(app, "Link comments for resident").set_value("other, resident").run()
        save_first(app)
        state = copy.deepcopy(app.session_state["archive"])
        state["evaluations"]["eval_other"]["resident"] = "Changed, Name"
        choice = choose_resident_link(state, "Taylor Example")
        assert choice["remembered_invalidated"]
        app = app_for(state)
        assert selector(app, "Link comments for resident").value == "example, taylor"
        button(app, "Update comments").click().run()
        assert not app.exception and not app.error
        choice = choose_resident_link(app.session_state["archive"], "Taylor Example")
        assert choice["resident_key"] == "example, taylor"
        assert choice["method"] == "remembered"
