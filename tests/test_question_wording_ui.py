"""Run the repair controls with a strict Streamlit double, not a browser."""
import copy
import importlib.util
from pathlib import Path
import sys
from unittest.mock import patch

import pytest
import epa_core as core
from test_core import configured_state, form_bytes, eval_bytes
from test_question_wording import placeholders
from test_start_date_ui import UI, Rerun

ROOT = Path(__file__).resolve().parents[1]


class Upload:
    def __init__(self, data, name='original_form.tab'):
        self.data, self.name = data, name
    def getvalue(self):
        return self.data


class RepairUI(UI):
    def __init__(self, uploaded=None, **kwargs):
        super().__init__(**kwargs)
        self.uploaded = uploaded
        self.uploaders = []
        self.button_labels = []
    def file_uploader(self, label, **kwargs):
        self.uploaders.append(label)
        return self.uploaded
    def button(self, label, **kwargs):
        self.button_labels.append(label)
        return super().button(label, **kwargs)


def load_ui(fake):
    with patch.dict(sys.modules, {'streamlit': fake}):
        spec = importlib.util.spec_from_file_location('_question_text_ui_test', ROOT / 'epa_question_text_ui.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


def commit_to(fake, state, writes):
    def commit(store, transform):
        updated, message = transform(copy.deepcopy(state))
        writes.append(updated)
        fake.session_state['archive'] = updated
        fake.session_state['save_result'] = message
        return True
    return commit


def test_no_extra_controls_for_forms_with_complete_wording():
    state = configured_state()
    fake = RepairUI()
    load_ui(fake).render_question_text_repair(state, None, lambda *a: False, next(iter(state['forms'])))
    assert not fake.messages and not fake.uploaders and not fake.button_labels


def test_missing_wording_shows_one_repair_location_with_original_file_input():
    state, fid = placeholders(configured_state())
    fake = RepairUI()
    load_ui(fake).render_question_text_repair(state, None, lambda *a: False, fid)
    assert fake.uploaders == ['Original form-definition TAB file']
    assert any('2 questions' in message for message in fake.messages)
    assert not fake.errors


def test_file_selection_previews_but_does_not_mutate_archive():
    state, fid = placeholders(configured_state())
    before = copy.deepcopy(state)
    fake = RepairUI(uploaded=Upload(form_bytes()))
    writes = []
    load_ui(fake).render_question_text_repair(state, None, commit_to(fake, state, writes), fid)
    assert not writes and state == before
    assert len(fake.tables) == 1
    assert fake.tables[0]['Wording to restore'].tolist() == ['Clinical skill 101', 'Clinical skill 102']
    assert 'Restore wording from file' in fake.button_labels


def test_file_restore_saves_and_reruns_with_preserved_selection():
    state, fid = placeholders(configured_state())
    fake = RepairUI(uploaded=Upload(form_bytes()), clicked='Restore wording from file')
    fake.session_state['form_to_edit'] = fid
    writes = []
    with pytest.raises(Rerun):
        load_ui(fake).render_question_text_repair(state, None, commit_to(fake, state, writes), fid)
    assert len(writes) == 1 and not fake.errors
    assert fake.session_state['form_to_edit'] == fid
    assert writes[0]['forms'][fid]['questions'][0]['text'] == 'Clinical skill 101'
    assert 'Existing selections, scales, and EPA links were kept' in fake.session_state['notice']


def test_saved_wording_restore_saves_and_reruns_without_an_upload():
    state, fid = placeholders(configured_state())
    state = core.register_form(state, core.parse_form(form_bytes(('101', '102', '999'))))
    fake = RepairUI(clicked='Restore saved wording')
    writes = []
    with pytest.raises(Rerun):
        load_ui(fake).render_question_text_repair(state, None, commit_to(fake, state, writes), fid)
    assert len(writes) == 1 and not fake.errors
    assert writes[0]['forms'][fid]['questions'][1]['text'] == 'Clinical skill 102'


@pytest.mark.parametrize('data, message', [(eval_bytes(), 'evaluation export'), (form_bytes(('999',)), 'no question IDs in common'),
    (b'garbled', 'readable tab-delimited')])
def test_bad_or_wrong_file_displays_actionable_error_without_saving(data, message):
    state, fid = placeholders(configured_state())
    fake = RepairUI(uploaded=Upload(data), clicked='Restore wording from file')
    writes = []
    load_ui(fake).render_question_text_repair(state, None, commit_to(fake, state, writes), fid)
    assert not writes
    assert any(message in error for error in fake.errors)


def test_failed_github_save_does_not_rerun_or_claim_success():
    state, fid = placeholders(configured_state())
    fake = RepairUI(uploaded=Upload(form_bytes()), clicked='Restore wording from file')
    load_ui(fake).render_question_text_repair(state, None, lambda *a: False, fid)
    assert not fake.reruns and 'notice' not in fake.session_state


def test_conflicting_saved_text_requires_explicit_definition():
    state, fid = placeholders(configured_state())
    state['forms'][fid]['questions'][0]['text_history'] = ['First version.', 'Second version.']
    fake = RepairUI()
    load_ui(fake).render_question_text_repair(state, None, lambda *a: False, fid)
    assert 'Restore saved wording' not in fake.button_labels
    assert any('Different saved wording' in message for message in fake.messages)


def test_integration_calls_repair_before_form_widgets():
    text = (ROOT / 'epa_tracker.py').read_text()
    body = text[text.index('def forms_page('):text.index('def record_details(')]
    assert body.index('render_question_text_repair(') < body.index('version = fingerprint(form)')
    assert 'st.session_state["form_to_edit"] = group["form_id"]' in body
