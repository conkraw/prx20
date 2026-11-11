"""Execute removal UI with a Streamlit test double, not a live browser."""
import ast
import copy
import importlib.util
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

from epa_form_tools import form_removal_preview
from test_form_removal import two_forms


class Rerun(Exception):
    pass


class FakeStreamlit:
    def __init__(self, *, acknowledged=False, typed="", clicked=False):
        self.acknowledged = acknowledged
        self.typed = typed
        self.clicked = clicked
        self.session_state = {}
        self.errors = []
        self.messages = []
        self.controls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def expander(self, *args, **kwargs):
        return self

    def write(self, message):
        self.messages.append(message)

    caption = warning = write

    def error(self, message):
        self.errors.append(message)

    def checkbox(self, label, **kwargs):
        self.controls.append((label, kwargs))
        return self.acknowledged

    def text_input(self, label, **kwargs):
        self.controls.append((label, kwargs))
        return self.typed

    def button(self, label, **kwargs):
        self.controls.append((label, kwargs))
        # Deliberately ignore disabled to verify the server-side check too.
        return self.clicked

    def rerun(self):
        raise Rerun()


def load_ui(ui):
    with patch.dict(sys.modules, {"streamlit": ui}):
        path = Path(__file__).resolve().parents[1] / "epa_form_ui.py"
        spec = importlib.util.spec_from_file_location("_form_removal_ui_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


def render(state, fid, ui, *, latest=None, save_fails=False):
    writes = []
    ui.session_state["archive"] = copy.deepcopy(state)
    def commit(store, transform):
        if save_fails:
            ui.errors.append("Not saved. Synthetic storage failure.")
            return False
        try:
            saved, result = transform(copy.deepcopy(latest if latest is not None else state))
        except ValueError as exc:
            ui.error("Not saved. " + str(exc))
            return False
        writes.append(saved)
        ui.session_state["archive"] = saved
        ui.session_state["save_result"] = result
        return True
    module = load_ui(ui)
    try:
        module.render_form_removal(state, None, commit, fid)
    except Rerun:
        pass
    return writes, module


def test_opening_removal_panel_does_not_change_archive():
    state, fid, _ = two_forms()
    ui = FakeStreamlit()
    writes, _ = render(state, fid, ui)
    assert not writes
    assert ui.controls[-1][1]["disabled"] is True
    assert ui.session_state["archive"] == state
    assert any("Saved evaluations: 1. Residents affected: 1." in text for text in ui.messages)


@pytest.mark.parametrize("acknowledged,typed", [(False, ""), (True, ""), (False, "REMOVE"), (True, "remove"), (True, "wrong")])
def test_unconfirmed_removal_is_blocked_even_if_browser_submits(acknowledged, typed):
    state, fid, _ = two_forms()
    ui = FakeStreamlit(acknowledged=acknowledged, typed=typed, clicked=True)
    writes, _ = render(state, fid, ui)
    assert not writes
    assert ui.errors
    assert ui.session_state["archive"] == state
    assert "_removed_form_reset" not in ui.session_state


def test_confirmed_removal_is_saved_before_success_notice_and_rerun():
    state, fid, other = two_forms()
    ui = FakeStreamlit(acknowledged=True, typed="REMOVE", clicked=True)
    writes, _ = render(state, fid, ui)
    assert len(writes) == 1
    assert not ui.errors
    assert ui.controls[-1][1]["disabled"] is False
    assert fid not in writes[0]["forms"]
    assert other in writes[0]["forms"]
    assert "The change is saved" in ui.session_state["notice"]
    assert ui.session_state["_removed_form_reset"] == fid


def test_storage_failure_does_not_remove_any_data_or_show_success():
    state, fid, _ = two_forms()
    ui = FakeStreamlit(acknowledged=True, typed="REMOVE", clicked=True)
    writes, _ = render(state, fid, ui, save_fails=True)
    assert not writes
    assert ui.errors
    assert ui.session_state["archive"] == state
    assert "notice" not in ui.session_state
    assert "_removed_form_reset" not in ui.session_state


def test_concurrent_change_rejected_without_mutating_displayed_archive():
    state, fid, _ = two_forms()
    concurrent = copy.deepcopy(state)
    rid = form_removal_preview(concurrent, fid)["evaluation_ids"][0]
    concurrent["evaluations"][rid]["overall_comments"] = "Changed."
    ui = FakeStreamlit(acknowledged=True, typed="REMOVE", clicked=True)
    writes, _ = render(state, fid, ui, latest=concurrent)
    assert not writes
    assert any("changed in another session" in message for message in ui.errors)
    assert ui.session_state["archive"] == state
    assert "notice" not in ui.session_state


def test_confirmation_controls_are_unique_to_form_and_current_impact():
    state, fid, other = two_forms()
    keys = []
    for target in (fid, other):
        ui = FakeStreamlit()
        render(state, target, ui)
        keys.append({values["key"] for _, values in ui.controls})
    assert keys[0].isdisjoint(keys[1])
    updated = copy.deepcopy(state)
    rid = form_removal_preview(updated, fid)["evaluation_ids"][0]
    updated["evaluations"][rid]["overall_comments"] = "New comment."
    ui = FakeStreamlit()
    render(updated, fid, ui)
    assert keys[0].isdisjoint({values["key"] for _, values in ui.controls})


def test_cleanup_drops_stale_uploads_editors_and_filters_but_keeps_login_and_dates():
    state, fid, other = two_forms()
    ui = FakeStreamlit(acknowledged=True, typed="REMOVE", clicked=True)
    writes, module = render(state, fid, ui)
    ui.session_state.update({
        "_epa_report_filters": {"academic_year": 2026, "months": [7, 8],
                                "date_basis": "session_date", "form_ids": [fid, other],
                                "residents": ["example, resident", "deleted resident"]},
        "upload_preview": "stale uploaded files",
        "appd_work_stale": "old calculations",
        "appd_final_stale": "old export",
        "dashboard_epas": [fid], "dashboard_residents": ["deleted resident"],
        "form_to_edit": fid, "appd_mapping_form": fid,
        f"appd_map_v2_{fid}_old": "old mapping",
        f"form_{fid}_old_name": "old form name",
        "remove_form_old": True,
        "login_token": "keep login", "last_refreshed": "keep timestamp",
        f"form_{other}_old_name": "keep unrelated editor",
    })
    module.apply_form_removal_session_cleanup()
    preferences = ui.session_state["_epa_report_filters"]
    assert preferences["form_ids"] == [other]
    assert preferences["residents"] == ["example, resident"]
    assert preferences["months"] == [7, 8]
    assert preferences["academic_year"] == 2026
    assert ui.session_state["login_token"] == "keep login"
    assert ui.session_state[f"form_{other}_old_name"] == "keep unrelated editor"
    for key in ("_removed_form_reset", "upload_preview", "appd_work_stale", "appd_final_stale",
                "form_to_edit", "appd_mapping_form", "dashboard_epas", "remove_form_old"):
        assert key not in ui.session_state
    before = copy.deepcopy(ui.session_state)
    module.apply_form_removal_session_cleanup()
    assert ui.session_state == before


def test_main_app_wires_removal_to_forms_page_and_cleanup_before_rendering():
    path = Path(__file__).resolve().parents[1] / "epa_tracker.py"
    tree = ast.parse(path.read_text())
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    names = lambda function: [(node.func.id, node.lineno) for node in ast.walk(function)
                              if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)]
    assert "render_form_removal" in dict(names(functions["forms_page"]))
    main_calls = dict(names(functions["main"]))
    assert main_calls["apply_form_removal_session_cleanup"] < main_calls["appd_admin_page"]
