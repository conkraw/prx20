"""Question wording regressions. All residents and prompts here are synthetic."""
import copy
import csv
import io
import json

import pytest
from cryptography.fernet import Fernet

import epa_core as core
import epa_appd as appd
from epa_form_discovery import candidate_form, register_candidate, unregistered_groups
from epa_question_text import (is_placeholder_question_text, saved_wording_candidates,
    missing_wording_ids, wording_repair_preview, restore_question_wording)
from epa_storage import GitHubStore
from test_core import configured_state, form_bytes, eval_bytes, record, tab_bytes


def placeholders(state):
    result = copy.deepcopy(state)
    fid = next(iter(result['forms']))
    for q in result['forms'][fid]['questions']:
        q['text'] = f"Question {q['id']} (verify wording from form definition)"
    return result, fid


def mapped_state():
    state = configured_state([record()])
    fid = next(iter(state['forms']))
    state = appd.save_mapping(state, fid, {'101': ['EPA 1', 'EPA 2'], '102': ['EPA 2']}, True)
    state['forms'][fid]['questions'][0]['score_map'] = {'1': 1.0, '5': 4.5}
    state['settings']['appd_roster'] = {'example, resident': {'residency_start_date': '2026-07-01'}}
    return state, fid


@pytest.mark.parametrize('text', ['', None, 'Question 101', 'Question 101 (verify wording from form definition)',
    '  QUESTION 101  ', 'Question ID: 101', 'Question 101 (text missing)'])
def test_placeholder_recognition(text):
    assert is_placeholder_question_text(text, '101')


@pytest.mark.parametrize('text', ['Clinical skill 101', 'Question 101: explain the plan.',
    'Questions the initial diagnosis when new evidence appears.', 'Question 102'])
def test_real_prompts_are_not_treated_as_placeholders(text):
    assert not is_placeholder_question_text(text, '101')


@pytest.mark.parametrize('text', ['', 'Question 101', 'Question 101 (verify wording from form definition)'])
def test_sparse_reimport_cannot_erase_wording_or_configuration(text):
    state, fid = mapped_state()
    original = copy.deepcopy(state)
    source = core.parse_form(form_bytes())
    source['questions'][0]['text'] = text
    source['questions'][0]['include'] = False
    updated = core.register_form(state, source)
    assert updated['forms'][fid] == state['forms'][fid]
    assert updated['evaluations'] == state['evaluations']
    assert updated['settings'] == state['settings']
    assert state == original
    assert updated == state


def test_blank_tags_do_not_clear_existing_tags_or_confirmation():
    state, fid = mapped_state()
    state['forms'][fid]['questions'][0]['tags'] = 'Original tag'
    source = core.parse_form(form_bytes())
    updated = core.register_form(state, source)
    assert updated['forms'][fid]['questions'][0]['tags'] == 'Original tag'
    assert updated['forms'][fid]['scoring_confirmed']


def test_real_revised_prompt_keeps_previous_wording_and_needs_review():
    state, fid = mapped_state()
    source = core.parse_form(form_bytes())
    source['questions'][0]['text'] = 'Revised synthetic prompt.'
    updated = core.register_form(state, source)
    q = updated['forms'][fid]['questions'][0]
    assert q['text'] == 'Revised synthetic prompt.'
    assert q['text_history'] == ['Clinical skill 101']
    assert q['score_map'] == state['forms'][fid]['questions'][0]['score_map']
    assert not updated['forms'][fid]['scoring_confirmed']
    assert updated['settings'] == state['settings']
    repeated = core.register_form(updated, source)
    assert repeated == updated


def test_normalized_duplicate_question_ids_rejected():
    source = core.parse_form(form_bytes())
    source['questions'][1]['id'] = '00101.0'
    with pytest.raises(ValueError, match='repeat'):
        core.register_form(core.empty_state(), source)


def test_new_form_reuses_known_wording_for_shared_question_ids():
    state = configured_state()
    state, _ = core.ingest_evaluations(state, [record(ids=('101', '103'))])
    group = unregistered_groups(state)[0]
    updated = register_candidate(state, group, 'New synthetic form')
    new = updated['forms'][group['form_id']]
    questions = {q['id']: q for q in new['questions']}
    assert questions['101']['text'] == 'Clinical skill 101'
    assert is_placeholder_question_text(questions['103']['text'], '103')
    assert not new['scoring_confirmed']
    assert not any(q['include'] for q in new['questions'])
    assert updated['evaluations'] == state['evaluations']


def test_registration_never_invents_wording_from_answers_or_comments():
    rec = record(ids=('201', '202'))
    rec['responses']['201'].update(response='4', comment='NOT A PROMPT', dropdown='NOT A PROMPT', tags='NOT A PROMPT')
    state, _ = core.ingest_evaluations(core.empty_state(), [rec])
    group = unregistered_groups(state)[0]
    updated = register_candidate(state, group, 'Synthetic')
    assert len(missing_wording_ids(updated['forms'][group['form_id']])) == 2
    assert 'NOT A PROMPT' not in json.dumps(updated['forms'])


@pytest.mark.parametrize('header', ['[Question 1 Text]', '[Question 1 Question Text]', '[Question 1 Wording]', '[Question 1 Prompt]'])
def test_evaluation_export_explicit_text_is_retained_and_deduplicated(header):
    data = list(csv.reader(io.StringIO(eval_bytes(encoding='utf-8').decode()), delimiter='\t'))
    data[0].append(header)
    data[1].append('Explicit synthetic question wording.')
    enriched = core.parse_upload(tab_bytes(data[0], data[1:]), 'enriched.tab')['evaluations'][0]
    base = record()
    assert enriched['id'] == base['id']
    assert enriched['content_hash'] == base['content_hash']
    state, stats = core.ingest_evaluations(core.empty_state(), [base, enriched])
    assert stats['added'] == 1 and stats['duplicates'] == 1
    assert next(iter(state['evaluations'].values()))['responses']['101']['question_text'] == 'Explicit synthetic question wording.'
    group = unregistered_groups(state)[0]
    registered = register_candidate(state, group, 'Synthetic')
    assert registered['forms'][group['form_id']]['questions'][0]['text'] == 'Explicit synthetic question wording.'


def test_question_text_parts_join_in_numeric_order():
    headers = ['Subject Name', '[Question 1 ID]', '[Question 1 Response]', '[Question 1 Part 2]', '[Question 1 Part 1]']
    data = tab_bytes(headers, [['Example, Resident', '101', '4', 'Second part.', 'First part.']])
    rec = core.parse_upload(data, 'parts.tab')['evaluations'][0]
    assert rec['responses']['101']['question_text'] == 'First part.\nSecond part.'


def test_conflicting_explicit_prompt_metadata_is_not_silently_chosen():
    first = record()
    second = copy.deepcopy(first)
    first['responses']['101']['question_text'] = 'First synthetic wording.'
    second['responses']['101']['question_text'] = 'Different synthetic wording.'
    state, stats = core.ingest_evaluations(core.empty_state(), [first, second])
    assert stats['duplicates'] == 1
    options = saved_wording_candidates(state, ['101'])['101']
    assert len(options) == 2
    group = unregistered_groups(state)[0]
    form = candidate_form(group, 'Synthetic', state)
    assert is_placeholder_question_text(form['questions'][0]['text'], '101')


def test_repair_from_original_file_keeps_form_id_order_evaluations_links_and_roster():
    state, fid = mapped_state()
    state, _ = placeholders(state)
    old = copy.deepcopy(state)
    source = core.parse_form(form_bytes(('102', '101', '999')))
    preview = wording_repair_preview(state, fid, source)
    assert preview['source_only_ids'] == ['999']
    updated, result = restore_question_wording(state, fid, expected_token=preview['token'], source_form=source)
    assert result['restored'] == 2 and result['remaining'] == 0
    assert len(updated['forms']) == len(state['forms'])
    assert updated['forms'][fid]['id'] == fid
    assert [q['id'] for q in updated['forms'][fid]['questions']] == ['101', '102']
    assert [q['text'] for q in updated['forms'][fid]['questions']] == ['Clinical skill 101', 'Clinical skill 102']
    for a, b in zip(state['forms'][fid]['questions'], updated['forms'][fid]['questions']):
        assert {k: v for k, v in a.items() if k != 'text'} == {k: v for k, v in b.items() if k != 'text'}
    assert updated['evaluations'] == state['evaluations']
    assert updated['settings'] == state['settings']
    assert updated['forms'][fid]['score_map'] == state['forms'][fid]['score_map']
    assert not updated['forms'][fid]['scoring_confirmed']
    assert state == old


def test_repair_can_restore_from_another_saved_definition_by_id():
    state, fid = placeholders(configured_state())
    state = core.register_form(state, core.parse_form(form_bytes(('101', '102', '999'))), 'Complete definition')
    plan = wording_repair_preview(state, fid)
    assert len(plan['changes']) == 2
    restored, _ = restore_question_wording(state, fid, expected_token=plan['token'])
    assert not missing_wording_ids(restored['forms'][fid])


def test_repair_can_recover_unique_history_after_legacy_text_loss():
    state, fid = placeholders(configured_state())
    state['forms'][fid]['questions'][0]['text_history'] = ['Original synthetic wording.']
    plan = wording_repair_preview(state, fid)
    assert plan['changes'][0]['after'] == 'Original synthetic wording.'
    assert plan['remaining_ids'] == ['102']


def test_ambiguous_saved_wording_requires_original_definition():
    state, fid = placeholders(configured_state())
    state['forms'][fid]['questions'][0]['text_history'] = ['First version.', 'Second version.']
    plan = wording_repair_preview(state, fid)
    assert not plan['changes']
    assert plan['conflicting_ids'] == ['101']
    with pytest.raises(ValueError, match='No missing wording'):
        restore_question_wording(state, fid, expected_token=plan['token'])
    source = core.parse_form(form_bytes(('101',)))
    explicit = wording_repair_preview(state, fid, source)
    updated, _ = restore_question_wording(state, fid, expected_token=explicit['token'], source_form=source)
    assert updated['forms'][fid]['questions'][0]['text'] == 'Clinical skill 101'


def test_valid_existing_wording_is_never_replaced_by_repair_upload():
    state = configured_state()
    fid = next(iter(state['forms']))
    state['forms'][fid]['questions'][1]['text'] = 'Question 102'
    source = core.parse_form(form_bytes())
    source['questions'][0]['text'] = 'Different wording that should not replace real text.'
    plan = wording_repair_preview(state, fid, source)
    assert [c['question_id'] for c in plan['changes']] == ['102']
    updated, _ = restore_question_wording(state, fid, expected_token=plan['token'], source_form=source)
    assert updated['forms'][fid]['questions'][0]['text'] == 'Clinical skill 101'


def test_wrong_definition_is_rejected_without_mutation():
    state, fid = placeholders(configured_state())
    before = copy.deepcopy(state)
    with pytest.raises(ValueError, match='no question IDs in common'):
        wording_repair_preview(state, fid, core.parse_form(form_bytes(('999',))))
    assert state == before


def test_partial_definition_reports_missing_questions_and_does_not_delete_them():
    state, fid = placeholders(configured_state())
    source = core.parse_form(form_bytes(('101',)))
    plan = wording_repair_preview(state, fid, source)
    assert plan['remaining_ids'] == ['102']
    updated, result = restore_question_wording(state, fid, expected_token=plan['token'], source_form=source)
    assert result['remaining'] == 1
    assert len(updated['forms'][fid]['questions']) == 2
    assert updated['forms'][fid]['questions'][1] == state['forms'][fid]['questions'][1]


@pytest.mark.parametrize('change', ['form', 'mapping', 'wording_source'])
def test_concurrent_changes_block_stale_repair(change):
    state, fid = placeholders(configured_state())
    state = core.register_form(state, core.parse_form(form_bytes(('101', '102', '999'))))
    plan = wording_repair_preview(state, fid)
    current = copy.deepcopy(state)
    if change == 'form':
        current['forms'][fid]['questions'][0]['include'] = False
    elif change == 'mapping':
        current = appd.save_mapping(current, fid, {'101': ['EPA 12']}, True)
    else:
        other = next(key for key in current['forms'] if key != fid)
        current['forms'][other]['questions'][0]['text'] = 'New source wording.'
    with pytest.raises(ValueError, match='another session'):
        restore_question_wording(current, fid, expected_token=plan['token'])


def test_unrelated_concurrent_evaluation_is_preserved():
    state, fid = placeholders(configured_state())
    source = core.parse_form(form_bytes())
    plan = wording_repair_preview(state, fid, source)
    current, _ = core.ingest_evaluations(state, [record(name='Another, Synthetic Resident')])
    updated, _ = restore_question_wording(current, fid, expected_token=plan['token'], source_form=source)
    assert updated['evaluations'] == current['evaluations']


def test_missing_selected_form_does_not_get_recreated_by_stale_repair():
    state, fid = placeholders(configured_state())
    source = core.parse_form(form_bytes())
    plan = wording_repair_preview(state, fid, source)
    state['forms'].pop(fid)
    with pytest.raises(ValueError, match='no longer available'):
        restore_question_wording(state, fid, expected_token=plan['token'], source_form=source)


def test_restore_encrypted_save_reload_and_reconfirm_keeps_original_averages():
    state, fid = mapped_state()
    original_average = core.epa_summary(core.score_rows(state)).iloc[0]['average']
    damaged, _ = placeholders(state)
    source = core.parse_form(form_bytes())
    plan = wording_repair_preview(damaged, fid, source)
    repaired, _ = restore_question_wording(damaged, fid, expected_token=plan['token'], source_form=source)
    store = GitHubStore({'repo': 'example/epa', 'token': 'synthetic-token'}, Fernet.generate_key())
    encrypted = store.encode(repaired)
    assert b'Clinical skill 101' not in encrypted
    reloaded = store.decode(encrypted)
    assert reloaded == repaired
    reloaded['forms'][fid]['scoring_confirmed'] = True
    assert core.epa_summary(core.score_rows(reloaded)).iloc[0]['average'] == original_average
    assert reloaded['settings']['appd_question_maps'][fid]['questions']['101'] == ['EPA 1', 'EPA 2']
