"""Execute mapping widget/save logic with a small Streamlit test double.

This is not a browser test. It verifies independent multi-select controls and
saved selections without needing a running Streamlit server or GitHub access.
"""
import copy
import importlib.util
from pathlib import Path
import sys
from unittest.mock import patch

import epa_appd as appd
from test_appd import make_state


class FakeStreamlit:
    def __init__(self, selections=None, save=True):
        self.selections = selections or {}
        self.save = save
        self.controls = []
        self.errors = []
        self.session_state = {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def subheader(self, *args, **kwargs):
        pass

    def caption(self, *args, **kwargs):
        pass

    def write(self, *args, **kwargs):
        pass

    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, text):
        self.errors.append(text)

    def selectbox(self, label, options, **kwargs):
        return options[0]

    def form(self, *args, **kwargs):
        return self

    def expander(self, *args, **kwargs):
        return self

    def columns(self, weights):
        return [self for _ in weights]

    def multiselect(self, label, options, default, key, **kwargs):
        assert all(option in options for option in default)
        self.controls.append({"label": label, "default": default, "key": key})
        return self.selections.get(label, default)

    def checkbox(self, *args, **kwargs):
        return True

    def form_submit_button(self, *args, **kwargs):
        return self.save

    def rerun(self):
        pass


def run_mapping(state, ui, concurrent_state=None):
    writes = []
    def commit(store, transform):
        latest = copy.deepcopy(concurrent_state if concurrent_state is not None else state)
        updated, message = transform(latest)
        writes.append(updated)
        return True
    with patch.dict(sys.modules, {"streamlit": ui}):
        spec = importlib.util.spec_from_file_location("_mapping_ui_test", Path(__file__).resolve().parents[1] / "epa_appd_ui.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module._question_mapping(copy.deepcopy(state), None, commit)
    return writes


def test_one_control_per_question_saves_multiple_targets_and_reloads_them():
    state = make_state()
    ui = FakeStreamlit({"EPAs for question 101": ["EPA 1", "EPA 2", "EPA 12"],
                       "EPAs for question 102": ["EPA 2", "EPA 3"]})
    writes = run_mapping(state, ui)
    assert not ui.errors
    assert len(ui.controls) == 2
    assert len({control["key"] for control in ui.controls}) == 2
    saved = writes[0]
    fid = next(iter(saved["forms"]))
    assert appd.mapping_for(saved, fid)["questions"] == {
        "101": ["EPA 1", "EPA 2", "EPA 12"], "102": ["EPA 2", "EPA 3"]}
    reloaded_ui = FakeStreamlit(save=False)
    assert not run_mapping(saved, reloaded_ui)
    assert reloaded_ui.controls[0]["default"] == ["EPA 1", "EPA 2", "EPA 12"]
    assert reloaded_ui.controls[1]["default"] == ["EPA 2", "EPA 3"]


def test_clearing_one_question_does_not_erase_the_other_links():
    ui = FakeStreamlit({"EPAs for question 101": []})
    saved = run_mapping(make_state(), ui)[0]
    assert appd.mapping_for(saved, next(iter(saved["forms"])))["questions"] == {"102": ["EPA 2"]}


def test_conflicting_mapping_from_another_session_is_not_overwritten():
    state = make_state()
    fid = next(iter(state["forms"]))
    concurrent = appd.save_mapping(state, fid, {"101": ["EPA 12"]}, True)
    ui = FakeStreamlit({"EPAs for question 101": ["EPA 1", "EPA 2"]})
    assert not run_mapping(state, ui, concurrent)
    assert any("changed in another session" in text for text in ui.errors)
