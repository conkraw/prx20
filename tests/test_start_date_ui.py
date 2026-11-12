"""Execute new UI paths with a Streamlit double, not a live browser."""
import ast
import calendar
import copy
from datetime import date, datetime
import importlib.util
from pathlib import Path
import sys
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import epa_appd as appd
import epa_core as core
from test_start_dates_and_visible_averages import with_start, KEY

ROOT = Path(__file__).resolve().parents[1]


class Rerun(Exception):
    pass


class Config:
    def __getattr__(self, name):
        return lambda *args, **kwargs: {"kind": name, **kwargs}


class UI:
    def __init__(self, edits=None, clicked=None, selections=None):
        self.edits = edits or {}
        self.clicked = clicked
        self.selections = selections or {}
        self.column_config = Config()
        self.session_state = {}
        self.tables = []
        self.tab_names = []
        self.errors = []
        self.messages = []
        self.editors = []
        self.reruns = 0

    def __enter__(self): return self
    def __exit__(self, *args): return False
    def columns(self, values): return [self] * (values if isinstance(values, int) else len(values))
    def tabs(self, values):
        self.tab_names.append(values)
        return [self] * len(values)
    def expander(self, *args, **kwargs): return self
    def form(self, *args, **kwargs): return self
    def dataframe(self, frame, **kwargs): self.tables.append(frame.copy())
    def data_editor(self, frame, **kwargs):
        self.editors.append((frame.copy(), kwargs))
        result = frame.copy()
        key = kwargs.get("key", "")
        for prefix, edits in self.edits.items():
            if key.startswith(prefix):
                for row, column, value in edits:
                    result.at[row, column] = value
        return result
    def button(self, label, **kwargs): return label == self.clicked and not kwargs.get("disabled", False)
    def form_submit_button(self, label, **kwargs): return self.button(label, **kwargs)
    def checkbox(self, label, **kwargs): return self.selections.get(label, kwargs.get("value", False))
    def date_input(self, label, value, **kwargs): return self.selections.get(label, value)
    def number_input(self, label, value, **kwargs): return self.selections.get(label, value)
    def multiselect(self, label, options, default=None, **kwargs): return self.selections.get(label, default or [])
    def radio(self, label, options, index=0, **kwargs): return self.selections.get(label, options[index])
    def selectbox(self, label, options, index=0, **kwargs): return self.selections.get(label, options[index])
    def text_input(self, label, value="", **kwargs): return self.selections.get(label, value)
    def write(self, text, *args, **kwargs): self.messages.append(str(text))
    caption = info = warning = header = subheader = write
    def error(self, text): self.errors.append(str(text))
    def metric(self, *args, **kwargs): pass
    def download_button(self, *args, **kwargs): pass
    def rerun(self):
        self.reruns += 1
        raise Rerun()


def load_ui(fake):
    with patch.dict(sys.modules, {"streamlit": fake}):
        spec = importlib.util.spec_from_file_location("_start_ui_test", ROOT / "epa_appd_ui.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


def commit_function(state, writes, latest=None):
    def commit(store, transform):
        updated, _ = transform(copy.deepcopy(latest if latest is not None else state))
        writes.append(updated)
        return True
    return commit


def test_roster_saves_calendar_date_as_iso_and_preserves_scores():
    state = with_start()
    fake = UI(edits={"appd_start_roster": [(KEY, "Residency start date", date(2025, 7, 1))]}, clicked="Save resident roster")
    module = load_ui(fake)
    writes = []
    with pytest.raises(Rerun):
        module._roster(state, None, commit_function(state, writes))
    assert not fake.errors
    assert writes[0]["settings"]["appd_roster"][KEY]["residency_start_date"] == "2025-07-01"
    assert writes[0]["evaluations"] == state["evaluations"]


def test_roster_rejects_non_july_first_and_shows_reason():
    state = with_start()
    fake = UI(edits={"appd_start_roster": [(KEY, "Residency start date", date(2025, 8, 1))]}, clicked="Save resident roster")
    writes = []
    load_ui(fake)._roster(state, None, commit_function(state, writes))
    assert not writes
    assert "July 1" in fake.errors[0]
    assert any("July 1" in str(table) for table in fake.tables)


def test_roster_date_preview_uses_entered_date_without_saving():
    state = with_start()
    fake = UI(edits={"appd_start_roster": [(KEY, "Residency start date", date(2026, 7, 1))]},
              selections={"Preview PGY on assessment date": date(2027, 10, 1)})
    writes = []
    load_ui(fake)._roster(state, None, commit_function(state, writes))
    assert not writes
    preview = next(table for table in fake.tables if "PGY" in table.columns)
    assert preview.iloc[0]["PGY"] == "PGY2"


def test_roster_group_assignment_preserves_other_residents():
    state = with_start()
    state["settings"]["appd_roster"]["other"] = {"resident": "Other", "export_id": "OTHER", "residency_start_date": "2024-07-01"}
    fake = UI(clicked="Save July 1 start date for selected residents",
              selections={"Residents in this starting class": [KEY], "PGY1 start year": 2025})
    writes = []
    with pytest.raises(Rerun):
        load_ui(fake)._roster(state, None, commit_function(state, writes))
    assert writes[0]["settings"]["appd_roster"][KEY]["residency_start_date"] == "2025-07-01"
    assert writes[0]["settings"]["appd_roster"]["other"]["residency_start_date"] == "2024-07-01"


def test_export_opens_with_visible_numeric_averages_without_local_labels():
    from test_simple_ui import StaffUI, load_simple
    state = with_start()
    fake = StaffUI()
    load_simple(fake).export_page(state, None, lambda *args: False)
    assert not fake.errors
    assert not fake.tab_names
    final = next(frame for frame, kwargs in fake.editors if "assessmentdate" in frame.columns)
    assert final.iloc[0]["EPA 2"] == 3.0
    assert final.iloc[0]["pgy"] == 1


def test_edit_assessment_date_updates_working_pgy_and_retains_other_edits():
    state = with_start()
    from test_simple_ui import StaffUI, load_simple
    fake = StaffUI(edits={"numeric_editor": [(KEY, "assessmentdate", date(2027, 10, 1)),
                                  (KEY, "name_or_program_assigned_ID", "00123"),
                                  (KEY, "EPA 2", 3.25)]})
    module = load_simple(fake)
    with pytest.raises(Rerun):
        module.export_page(state, None, lambda *args: False)
    rows = next(value for key, value in fake.session_state.items() if key.startswith("numeric_work_"))
    assert rows[0]["pgy"] == 2
    assert rows[0]["assessmentdate"] == "2027-10-01"
    assert rows[0]["name_or_program_assigned_ID"] == "00123"
    assert rows[0]["EPA 2"] == 3.25
    # The updated source is stable on the following rerun.
    fake.edits = {}
    module.export_page(state, None, lambda *args: False)
    assert fake.reruns == 1


def test_diagnostic_table_explains_unscored_evaluations():
    state = with_start()
    next(iter(state["forms"].values()))["scoring_confirmed"] = False
    fake = UI()
    load_ui(fake).calculated_overview(appd.build_draft(state))
    assert any("No mapped averages" in message for message in fake.messages)
    diagnostic = next(frame for frame in fake.tables if "Reason" in frame.columns)
    assert diagnostic["Reason"].str.contains("scoring review").any()


def test_averages_table_does_not_merge_residents_with_same_display_name():
    draft = appd.build_draft(with_start())
    other = copy.deepcopy(draft["rows"][0])
    other["resident_key"] = "second unique key"
    other["pgy"] = 2
    draft["rows"].append(other)
    metric = copy.deepcopy(next(x for x in draft["summary"] if x["APPD EPA"] == "EPA 2"))
    metric.update(resident_key="second unique key", Average=4, **{"Local level": "4"})
    draft["summary"].append(metric)
    fake = UI()
    load_ui(fake).calculated_overview(draft)
    assert len(fake.tables[0]) == 2
    assert fake.tables[0]["EPA 2"].tolist() == [3.0, 4.0]


@pytest.mark.parametrize("view", ["Mapped EPA 1-12", "By evaluation form"])
def test_dashboard_paths_render_and_show_assessment_pgys(view):
    fake = UI(selections={"Average summary": view})
    fake.session_state["_epa_report_filters"] = {"academic_year": 2026, "months": [7]}
    module = load_ui(fake)
    tree = ast.parse((ROOT / "epa_tracker.py").read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "dashboard")
    code = compile(ast.Module(body=[node], type_ignores=[]), "epa_tracker_dashboard", "exec")
    globals_ = {
        "pd": pd, "st": fake, "datetime": datetime, "ZoneInfo": ZoneInfo, "calendar": calendar,
        "REPORT_FILTERS_KEY": "_epa_report_filters", "score_rows": core.score_rows,
        "filter_rows": core.filter_rows, "epa_summary": core.epa_summary,
        "question_summary": core.question_summary, "monthly_summary": core.monthly_summary,
        "recognition_rows": lambda state: [], "pending_forms": lambda state: 0,
        "appd_config_for": appd.config_for, "local_display_level": appd.local_display_level,
        "appd_tools": appd, "calculated_overview": module.calculated_overview,
        "table": lambda frame, **kwargs: fake.dataframe(frame), "public_frame": lambda frame: frame,
        "csv_bytes": lambda frame: b"synthetic", "utc_now": core.utc_now,
        "draw_chart": lambda *args: None,
        "report_context": lambda *args: {},
    }
    exec(code, globals_)
    result = globals_["dashboard"](with_start())
    assert not fake.errors
    assert not result[0].empty
    assert not fake.tab_names
    fake.tables.clear()
    fake.selections["Report"] = "Assessment PGY"
    globals_["dashboard"](with_start())
    pgy_table = next(frame for frame in fake.tables if "PGY source / issue" in frame.columns)
    assert pgy_table.iloc[0]["PGY"] == "PGY1"
