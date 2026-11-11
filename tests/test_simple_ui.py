"""UI contract tests use a strict fake, not a browser or live Streamlit server."""
import copy
import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

import epa_appd as appd
import epa_workflow as workflow
from test_start_date_ui import UI, Rerun, commit_function
from test_numeric_workflow import configured, approve, review_rows
from test_appd import make_state

ROOT = Path(__file__).resolve().parents[1]


class StaffUI(UI):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.downloads = []
        self.buttons = []
    def download_button(self, label, *args, **kwargs):
        self.downloads.append((label, kwargs))
    def link_button(self, *args, **kwargs): pass
    def button(self, label, **kwargs):
        self.buttons.append((label, kwargs))
        return super().button(label, **kwargs)


def load_simple(fake):
    def load(name, file):
        spec = importlib.util.spec_from_file_location(name, ROOT / file)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    with patch.dict(sys.modules, {"streamlit": fake}):
        base = load("_base_for_simple_test", "epa_appd_ui.py")
        with patch.dict(sys.modules, {"epa_appd_ui": base}):
            return load("_simple_test", "epa_simple_ui.py")


def test_export_has_one_editor_prepopulated_with_numeric_averages():
    fake = StaffUI()
    load_simple(fake).export_page(configured(), None, lambda *args: False)
    assert not fake.errors
    assert not fake.tab_names
    assert len(fake.editors) == 1
    frame, options = fake.editors[0]
    assert frame.iloc[0]["EPA 2"] == 3
    assert options["column_config"]["EPA 2"]["kind"] == "NumberColumn"
    assert all("Local level" not in table.columns for table in fake.tables)
    assert fake.downloads[0][1]["disabled"] is True


def test_calculated_scores_are_visible_even_before_conversion_setup():
    fake = StaffUI()
    load_simple(fake).export_page(make_state(), None, lambda *args: False)
    assert fake.editors[0][0].iloc[0]["EPA 2"] == 3
    assert any("Data Hub rules" in message for message in fake.messages)
    assert next(kwargs for label, kwargs in fake.buttons if label == "Approve and prepare Data Hub file")["disabled"]


def test_save_numeric_draft_calls_encrypted_commit_without_rewriting_source():
    state = configured()
    fake = StaffUI(clicked="Save draft", edits={"numeric_editor": [("example, resident", "EPA 2", 3.25)]})
    writes = []
    with pytest.raises(Rerun):
        load_simple(fake).export_page(state, None, commit_function(state, writes))
    assert len(writes) == 1
    saved = workflow.saved_review(writes[0], appd.config_for(writes[0]))
    assert saved["rows"][0]["EPA 2"] == 3.25
    assert writes[0]["evaluations"] == state["evaluations"]
    assert not saved["approved"]


def test_approval_creates_both_numeric_and_upload_snapshots():
    state = configured()
    fake = StaffUI(clicked="Approve and prepare Data Hub file", selections={
        "Reviewer name or initials": "TEST",
        "The CCC reviewed these values and the not-observed entries": True,
    })
    writes = []
    with pytest.raises(Rerun):
        load_simple(fake).export_page(state, None, commit_function(state, writes))
    assert workflow.ready_review(writes[0], appd.config_for(writes[0]))
    assert appd.approved_review(writes[0], appd.config_for(writes[0]))


def test_approved_table_exposes_download_without_initial_network_call():
    state = approve(configured())
    fake = StaffUI()
    load_simple(fake).export_page(state, None, lambda *args: False)
    label, kwargs = fake.downloads[0]
    assert label == "Download Data Hub upload (.xlsx)"
    assert callable(kwargs["data"])
    assert not kwargs.get("disabled")


def test_unsaved_numeric_change_disables_prior_approved_download():
    fake = StaffUI(edits={"numeric_editor": [("example, resident", "EPA 2", 3.4)]})
    state = approve(configured())
    load_simple(fake).export_page(state, None, lambda *args: False)
    assert fake.downloads[0][1]["disabled"] is True


@pytest.mark.parametrize("section,handler", [("Forms and EPA mapping", "forms"), ("Data review", "review"), ("Advanced", "settings")])
def test_technical_pages_are_routed_through_one_admin_location(section, handler):
    fake = StaffUI(selections={"Setup": section})
    called = []
    load_simple(fake).admin_page(configured(), None, lambda *args: False,
        forms_page=lambda *args: called.append("forms"),
        review_page=lambda *args: called.append("review"),
        settings_page=lambda *args: called.append("settings"))
    assert called == [handler]


def test_unified_form_save_keeps_multiple_epa_links_and_source_scores():
    import ast
    import pandas as pd
    state = configured()
    fid = next(iter(state["forms"]))
    fake = StaffUI(clicked="Save form setup", selections={
        "EPAs for question 101": ["EPA 1", "EPA 2"],
        "EPAs for question 102": ["EPA 2"],
        "I reviewed these question-to-EPA links": True,
    })
    writes = []
    def commit(store, transform):
        updated, notice = transform(copy.deepcopy(state))
        writes.append(updated)
        fake.session_state["save_result"] = notice
        return True
    tree = ast.parse((ROOT / "epa_tracker.py").read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "forms_page")
    namespace = {"st": fake, "pd": pd, "copy": copy, "GitHubStore": object,
                 "commit": commit, "fingerprint": appd.digest, "appd_tools": appd,
                 "render_form_removal": lambda *args: None,
                 "render_question_text_repair": lambda *args: None,
                 "observed_codes": lambda *args: {"1", "2", "3", "4", "5", "6"},
                 "scale_editor": lambda mapping, *args: copy.deepcopy(mapping),
                 "read_scale": lambda mapping: mapping}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "unified_form", "exec"), namespace)
    with pytest.raises(Rerun):
        namespace["forms_page"](state, None)
    assert writes[0]["settings"]["appd_question_maps"][fid]["questions"]["101"] == ["EPA 1", "EPA 2"]
    assert writes[0]["evaluations"] == state["evaluations"]
    assert writes[0]["forms"][fid]["score_map"] == state["forms"][fid]["score_map"]
    assert not fake.errors
