"""UI control-flow tests with a deterministic Streamlit double, not a browser."""
import ast
import calendar
import copy
import sys
import time
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import epa_appd as appd
import epa_auth as auth
import epa_core as core
import epa_performance as perf
from test_start_date_ui import UI, Rerun, load_ui
from test_start_dates_and_visible_averages import with_start
from test_simple_ui import StaffUI, load_simple

ROOT = Path(__file__).resolve().parents[1]


class Stop(Exception):
    pass


class Slot:
    def __init__(self, ui): self.ui = ui
    def container(self): return self.ui
    def empty(self): self.ui.events.append("clear block")


class FastUI(StaffUI):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.events = []
        self.sidebar = self
        self.secrets = {}
    def markdown(self, text, **kwargs): self.messages.append(text)
    def empty(self):
        self.events.append("create block")
        return Slot(self)
    def spinner(self, text):
        self.events.append(text)
        return nullcontext()
    def stop(self): raise Stop()
    def set_page_config(self, **kwargs): pass
    def divider(self): pass
    def title(self, text): self.messages.append(text)
    def success(self, text): self.messages.append(text)
    def file_uploader(self, *args, **kwargs): return []


def dashboard_function(fake, appd_module=appd, path=None):
    nodes = ast.parse((path or ROOT / "epa_tracker.py").read_text()).body
    node = next(n for n in nodes if isinstance(n, ast.FunctionDef) and n.name == "dashboard")
    ui_module = load_ui(fake)
    captured = []
    def overview(draft, **kwargs):
        captured.append(copy.deepcopy(draft))
        ui_module.calculated_overview(draft, **kwargs)
    values = {
        "pd": pd, "st": fake, "datetime": datetime, "ZoneInfo": ZoneInfo, "calendar": calendar,
        "REPORT_FILTERS_KEY": "_epa_report_filters", "score_rows": core.score_rows,
        "filter_rows": core.filter_rows, "epa_summary": core.epa_summary,
        "question_summary": core.question_summary, "monthly_summary": core.monthly_summary,
        "recognition_rows": lambda state: [], "pending_forms": lambda state: 0,
        "appd_tools": appd_module, "calculated_overview": overview,
        "table": lambda frame, **kwargs: fake.dataframe(frame), "public_frame": lambda frame: frame,
        "csv_bytes": lambda frame: b"fictional CSV", "utc_now": core.utc_now,
        "draw_chart": lambda *args: None, "report_context": lambda *args: {},
        "workbook_bytes": Mock(return_value=b"fictional workbook"),
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path or ROOT / "epa_tracker.py"), "exec"), values)
    return values["dashboard"], values, captured


def sample_ui(**kwargs):
    fake = FastUI(**kwargs)
    fake.session_state["_epa_report_filters"] = {
        "academic_year": 2026, "months": [7], "start_month": 7,
        "date_basis": "session_date", "residents": [], "form_ids": []}
    return fake


def test_dashboard_does_not_prepare_monthly_or_excel_reports_on_login():
    fake = sample_ui(selections={"Average summary": "Mapped EPA 1-12"})
    dashboard, namespace, captured = dashboard_function(fake)
    namespace["monthly_summary"] = Mock(side_effect=AssertionError("unrequested monthly report"))
    namespace["question_summary"] = Mock(side_effect=AssertionError("unrequested question report"))
    namespace["epa_summary"] = Mock(side_effect=AssertionError("unrequested form report"))
    result = dashboard(with_start())
    assert not result[0].empty and captured
    assert not namespace["workbook_bytes"].called
    assert not fake.errors


def test_dashboard_reuses_scored_rows_and_draft_on_repeat_interaction():
    state = with_start()
    fake = sample_ui(selections={"Average summary": "Mapped EPA 1-12"})
    dashboard, namespace, captured = dashboard_function(fake)
    namespace["score_rows"] = Mock(wraps=core.score_rows)
    with patch.object(appd, "dashboard_mapped_draft", wraps=appd.dashboard_mapped_draft) as draft:
        dashboard(state)
        dashboard(state)
        assert draft.call_count == 1
    assert namespace["score_rows"].call_count == 1
    assert captured[0] == captured[1]


def test_form_average_does_not_compute_questions_or_trends():
    fake = sample_ui(selections={"Average summary": "By evaluation form"})
    dashboard, namespace, _ = dashboard_function(fake)
    namespace["monthly_summary"] = Mock(side_effect=AssertionError("unrequested trends"))
    namespace["question_summary"] = Mock(side_effect=AssertionError("unrequested questions"))
    dashboard(with_start())
    assert not fake.errors
    assert any("average" in table for table in fake.tables)


def test_only_requested_trend_is_computed_and_reused():
    state = with_start()
    fake = sample_ui(selections={"Report": "Monthly progress"})
    dashboard, namespace, _ = dashboard_function(fake)
    namespace["monthly_summary"] = Mock(wraps=core.monthly_summary)
    dashboard(state)
    dashboard(state)
    assert namespace["monthly_summary"].call_count == 1
    assert namespace["monthly_summary"].call_args.kwargs == {}
    fake.selections["Progress view"] = "Cumulative from academic-year start"
    dashboard(state)
    assert namespace["monthly_summary"].call_count == 2
    assert namespace["monthly_summary"].call_args.kwargs == {"cumulative": True}


def test_filters_only_change_report_after_apply():
    state = with_start()
    fake = sample_ui(selections={"Average summary": "Mapped EPA 1-12", "Months": [8]})
    dashboard, _, _ = dashboard_function(fake)
    result = dashboard(state)
    assert not result[0].empty
    assert fake.session_state["_epa_report_filters"]["months"] == [7]
    fake.clicked = "Apply filters"
    assert dashboard(state) is None
    assert fake.session_state["_epa_report_filters"]["months"] == [8]
    assert any("No recognized evaluations" in message for message in fake.messages)


def test_excel_report_is_prepared_only_on_request_and_stays_available():
    state = with_start()
    fake = sample_ui(selections={"Report": "Download reports"})
    dashboard, namespace, _ = dashboard_function(fake)
    dashboard(state)
    assert not namespace["workbook_bytes"].called
    fake.clicked = "Prepare Excel report"
    dashboard(state)
    assert namespace["workbook_bytes"].call_count == 1
    fake.clicked = None
    fake.downloads.clear()
    dashboard(state)
    assert any(label == "Download Excel report" for label, _ in fake.downloads)
    assert namespace["workbook_bytes"].call_count == 1
    assert set(namespace["workbook_bytes"].call_args.args[0]) == {
        "EPA averages", "Question averages", "Monthly EPA", "Cumulative EPA",
        "Response audit", "Assessment PGYs", "Report notes"}


def test_export_edit_reuses_draft_until_archive_changes():
    state = with_start()
    fake = FastUI()
    module = load_simple(fake)
    with patch.object(appd, "build_draft", wraps=appd.build_draft) as draft:
        module.export_page(state, None, lambda *args: False)
        module.export_page(state, None, lambda *args: False)
        assert draft.call_count == 1
        state = copy.deepcopy(state)
        module.export_page(state, None, lambda *args: False)
        assert draft.call_count == 2
    assert not fake.errors


def test_only_one_admin_review_section_is_rendered():
    fake = FastUI(selections={"Review": "Saved evaluations"})
    node = next(n for n in ast.parse((ROOT / "epa_tracker.py").read_text()).body
                if isinstance(n, ast.FunctionDef) and n.name == "review_page")
    detail = Mock()
    values = {"st": fake, "pd": pd, "GitHubStore": object, "record_details": detail,
              "score_rows": Mock(side_effect=AssertionError("unrequested scoring review")),
              "recognition_rows": Mock(side_effect=AssertionError("unrequested matching review"))}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "review test", "exec"), values)
    values["review_page"](with_start(), None)
    assert detail.call_count == 1
    assert not fake.tab_names


@pytest.fixture(scope="module")
def encoded_password():
    return auth.hash_password("fictional test password")


def test_login_clears_password_block_before_any_archive_load_without_extra_rerun(encoded_password):
    fake = FastUI(clicked="Sign in", selections={"Password": "fictional test password"})
    fake.session_state["_epa_password_entry"] = "fictional test password"
    with patch.dict(sys.modules, {"streamlit": fake}), patch.object(auth, "_THROTTLE", auth._LoginThrottle()):
        auth.require_login({"password_hash": encoded_password})
        fake.events.append("archive load")
    assert fake.events.index("clear block") < fake.events.index("archive load")
    assert "_epa_password_entry" not in fake.session_state
    assert auth._AUTH_KEY in fake.session_state
    assert fake.reruns == 0


def test_wrong_password_does_not_allow_archive_loading(encoded_password):
    fake = FastUI(clicked="Sign in", selections={"Password": "wrong password"})
    with patch.dict(sys.modules, {"streamlit": fake}), patch.object(auth, "_THROTTLE", auth._LoginThrottle()):
        with pytest.raises(Stop):
            auth.require_login({"password_hash": encoded_password})
    assert auth._AUTH_KEY not in fake.session_state
    assert fake.errors == ["The password was not accepted."]


def test_timeout_clears_private_cached_results_and_requires_login(encoded_password):
    fake = FastUI()
    fake.session_state[auth._AUTH_KEY] = {
        "fingerprint": auth._fingerprint(encoded_password),
        "last_activity": time.monotonic() - 3600}
    perf.cached_result(fake.session_state, with_start(), "private", None, lambda: "private test value")
    fake.session_state["archive"] = with_start()
    with patch.dict(sys.modules, {"streamlit": fake}):
        with pytest.raises(Stop):
            auth.require_login({"password_hash": encoded_password, "session_minutes": 30})
    assert perf.CACHE_KEY not in fake.session_state
    assert "archive" not in fake.session_state


def test_logout_clears_archive_downloads_and_derived_results():
    fake = FastUI()
    state = with_start()
    fake.session_state["archive"] = state
    fake.session_state["upload_preview"] = {"private": "fictional data"}
    perf.cached_result(fake.session_state, state, "private", None, lambda: "private")
    with patch.dict(sys.modules, {"streamlit": fake}):
        auth.logout()
    assert not fake.session_state


def test_main_loads_archive_once_and_reload_invalidates_cache():
    fake = FastUI()
    state = with_start()
    fake.secrets = {"app": {}, "github": {}, "security": {"fernet_key": "test-only"}}
    class Store:
        loads = 0
        def __init__(self, *args): pass
        def load(self):
            Store.loads += 1
            return copy.deepcopy(state), "fictional-sha"
    node = next(n for n in ast.parse((ROOT / "epa_tracker.py").read_text()).body
                if isinstance(n, ast.FunctionDef) and n.name == "main")
    values = {
        "st": fake, "pd": pd, "apply_styles": lambda st: None, "require_login": lambda cfg: None,
        "GitHubStore": Store, "StorageError": ValueError, "validate_state": lambda state: None,
        "invalidate": perf.invalidate, "utc_now": core.utc_now,
        "apply_form_removal_session_cleanup": lambda: None,
        "NAVIGATION": ["Dashboard", "Upload", "APPD Export", "Admin"],
        "sidebar_brand": lambda st: None, "dashboard": lambda state: None,
        "render_sidebar_pdf": lambda *args: None,
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), "main test", "exec"), values)
    values["main"]()
    values["main"]()
    assert Store.loads == 1
    perf.cached_result(fake.session_state, fake.session_state["archive"], "private", None, lambda: "private")
    fake.clicked = "Reload saved data"
    with pytest.raises(Rerun):
        values["main"]()
    assert perf.CACHE_KEY not in fake.session_state and "archive" not in fake.session_state
    fake.clicked = None
    values["main"]()
    assert Store.loads == 2


def test_filter_year_choices_cover_both_session_and_completion_dates():
    state = with_start()
    next(iter(state["evaluations"].values()))["completed_at"] = "2027-08-01T10:00:00"
    fake = sample_ui(selections={"Report": "Assessment PGY"})
    dashboard, _, _ = dashboard_function(fake)
    with patch.object(fake, "selectbox", wraps=fake.selectbox) as select:
        dashboard(state)
    year_call = next(c for c in select.call_args_list if c.args[0] == "Academic year")
    assert 2026 in year_call.args[1] and 2027 in year_call.args[1]
