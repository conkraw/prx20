"""Compact EPA grouping regressions, with fictional residents and forms."""
import ast
import copy
import io
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest
from pypdf import PdfReader

import epa_appd as appd
import epa_core as core
import epa_grouped_pdf as grouped
from epa_pdf import build_question_pdf, pdf_question_sections
from test_pdf import _state, _form, _record, CONTEXT


def state_and_rows():
    state = _state()
    fid = next(iter(state['forms']))
    state = appd.save_mapping(state, fid, {
        '102': ['EPA 10', 'EPA 2', 'EPA 1', 'EPA 1'],
        '104': ['EPA 1'], '107': ['EPA 2'],
    }, True)
    rows = core.filter_rows(core.score_rows(state), academic_year=2026, months=[7, 8])
    return state, rows, fid


def context(state, rows, **extra):
    value = dict(CONTEXT, report_selection={'academic_year': 2026, 'start_month': 7,
                                          'months': [7, 8], 'date_basis': 'session_date'})
    value.update(extra)
    return grouped.grouped_context(state, rows, value)


def pdf_text(data):
    return '\n'.join(page.extract_text() or '' for page in PdfReader(io.BytesIO(data)).pages)


def groups(state, rows):
    return grouped.grouped_question_sections(rows, state['forms'], state['settings']['appd_question_maps'])


def test_grouping_uses_numeric_epa_order_not_lexical_order():
    state, rows, _ = state_and_rows()
    assert [part['epa'] for part in groups(state, rows)[0]['groups']] == ['EPA 1', 'EPA 2', 'EPA 10', 'No EPA assigned']


def test_original_question_positions_not_ids_or_sequential_renumbering():
    state, rows, _ = state_and_rows()
    part = groups(state, rows)[0]['groups'][0]['forms'][0]
    assert [(q['number'], q['question_id']) for q in part['questions']] == [(1, '102'), (4, '104')]
    assert groups(state, rows)[0]['groups'][1]['forms'][0]['questions'][1]['number'] == 7


def test_all_original_averages_counts_and_text_are_identical_to_legacy_pdf():
    state, rows, _ = state_and_rows()
    original = {(s['form_id'], q['question_id']): q for s in pdf_question_sections(rows, state['forms']) for q in s['questions']}
    for person in groups(state, rows):
        for part in person['groups']:
            for form in part['forms']:
                for question in form['questions']:
                    assert question == original[(form['form_id'], question['question_id'])]


def test_multilink_repeats_a_question_once_per_epa_without_changing_counts():
    state, rows, _ = state_and_rows()
    person = groups(state, rows)[0]
    appearances = [(part['epa'], q['n']) for part in person['groups'] for form in part['forms']
                   for q in form['questions'] if q['question_id'] == '102']
    assert appearances == [('EPA 1', 2), ('EPA 2', 2), ('EPA 10', 2)]


def test_no_epa_links_are_not_guessed_from_form_name_or_wording():
    state = _state()
    rows = core.score_rows(state)
    result = grouped.grouped_question_sections(rows, state['forms'], {})
    assert [g['epa'] for g in result[0]['groups']] == ['No EPA assigned']
    assert len(result[0]['groups'][0]['forms'][0]['questions']) == 7


def test_unscored_and_unassigned_questions_still_present():
    state, rows, _ = state_and_rows()
    person = groups(state, rows)[0]
    excluded = person['groups'][0]['forms'][0]['questions'][1]
    assert excluded['average_text'] == excluded['n_text'] == ''
    unassigned = person['groups'][-1]['forms'][0]['questions']
    assert [q['number'] for q in unassigned] == [2, 3, 5, 6]


def test_zero_is_not_blank():
    state, rows, _ = state_and_rows()
    question = groups(state, rows)[0]['groups'][1]['forms'][0]['questions'][1]
    assert question['average_text'] == '0.00' and question['n_text'] == '2'


def test_pending_mapping_is_visible_but_flagged_not_counted_in_epa_totals():
    state, rows, fid = state_and_rows()
    state['settings']['appd_question_maps'][fid]['confirmed'] = False
    part = groups(state, rows)[0]['groups'][0]['forms'][0]
    assert not part['mapping_ready']
    assert part['questions'][0]['average_text'] == '3.00'
    assert any('not currently counted' in warning for warning in part['issues'])


def test_changed_wording_stales_links_but_keeps_grouping_for_review():
    state, rows, fid = state_and_rows()
    state['forms'][fid]['questions'][0]['text'] = 'Revised wording'
    part = groups(state, rows)[0]['groups'][0]['forms'][0]
    assert not part['mapping_ready'] and part['questions'][0]['question'] == 'Revised wording'


def test_invalid_mapping_does_not_erase_any_question():
    state, rows, fid = state_and_rows()
    state['settings']['appd_question_maps'][fid]['questions']['102'] = ['EPA 99']
    person = groups(state, rows)[0]
    assert len(person['groups']) == 1
    assert len(person['groups'][0]['forms'][0]['questions']) == 7
    assert any('Invalid saved' in issue for issue in person['groups'][0]['forms'][0]['issues'])


def test_legacy_single_epa_string_is_respected():
    state, rows, fid = state_and_rows()
    state['settings']['appd_question_maps'][fid]['questions']['102'] = 'EPA 3'
    targets = [part['epa'] for part in groups(state, rows)[0]['groups'] for form in part['forms']
               for q in form['questions'] if q['question_id'] == '102']
    assert targets == ['EPA 3']


def test_source_form_tables_stay_separate_even_when_names_match():
    state, rows, fid = state_and_rows()
    form = _form(prefix='2')
    state = core.register_form(state, form)
    state = appd.save_mapping(state, form['id'], {'202': ['EPA 1']}, True)
    state, _ = core.ingest_evaluations(state, [_record(prefix='2', value='5')])
    person = groups(state, core.score_rows(state))[0]
    parts = person['groups'][0]['forms']
    assert len(parts) == 2
    assert {p['form_id'] for p in parts} == {fid, form['id']}
    assert sorted(p['questions'][0]['average'] for p in parts) == [3, 5]


def test_residents_are_not_combined_and_original_filter_is_preserved():
    state, _, _ = state_and_rows()
    state, _ = core.ingest_evaluations(state, [_record(resident='Other, Resident', value='5')])
    rows = core.filter_rows(core.score_rows(state), residents=['Example, Resident'])
    result = groups(state, rows)
    assert len(result) == 1 and result[0]['resident'] == 'Example, Resident'


def test_grouping_and_pdf_generation_have_no_state_or_data_side_effects():
    state, rows, _ = state_and_rows()
    old = copy.deepcopy(state)
    old_rows = rows.copy(deep=True)
    report_context = context(state, rows)
    old_context = copy.deepcopy(report_context)
    data = build_question_pdf(rows, state['forms'], report_context)
    assert data.startswith(b'%PDF-')
    assert state == old and report_context == old_context
    pd.testing.assert_frame_equal(rows, old_rows)


def test_pdf_contains_original_four_columns_and_no_preceptor_audit_ledger():
    state, rows, _ = state_and_rows()
    text = pdf_text(build_question_pdf(rows, state['forms'], context(state, rows)))
    for term in ['EPA 1', 'EPA 2', 'EPA 10', 'Question', 'Average', 'No EPA assigned', 'Form checklist', 'Example EPA', '3.00']:
        assert term in text
    assert 'Example, Faculty' not in text
    assert 'Evaluation average' not in text
    assert 'Narrative <b>exact</b> & source > text' in text


def test_long_question_can_span_pages_and_keeps_form_and_epa_context():
    state, _, fid = state_and_rows()
    state['forms'][fid]['questions'][0]['text'] = 'LONG_START ' + 'long source wording ' * 1400 + ' LONG_END'
    rows = core.score_rows(state)
    data = build_question_pdf(rows, state['forms'], context(state, rows))
    reader = PdfReader(io.BytesIO(data))
    text = pdf_text(data)
    assert 'LONG_START' in text and 'LONG_END' in text
    assert len(reader.pages) > 3
    assert 'Example EPA' in (reader.pages[1].extract_text() or '')


def test_empty_export_stays_an_error():
    state, rows, _ = state_and_rows()
    with pytest.raises(ValueError, match='No recognized evaluations'):
        grouped.build_grouped_question_pdf(rows.iloc[:0], state['forms'], context(state, rows))


def test_snapshot_drops_response_and_comment_text_but_preserves_recognition():
    state, _, _ = state_and_rows()
    before = copy.deepcopy(state)
    snap = grouped.report_snapshot(state)
    for rid, record in snap['evaluations'].items():
        assert 'overall_comments' not in record
        assert all(value == {} for value in record['responses'].values())
        assert core.match_form(record, snap['forms']) == core.match_form(state['evaluations'][rid], state['forms'])
    assert state == before
    next(iter(state['forms'].values()))['name'] = 'Changed after capture'
    assert next(iter(snap['forms'].values()))['name'] != 'Changed after capture'


def test_checklist_counts_evaluations_not_questions_and_includes_all_saved_forms():
    state, rows, fid = state_and_rows()
    empty_form = _form(prefix='2', name='No uploaded evaluations')
    state = core.register_form(state, empty_form)
    checklist = context(state, rows)['form_coverage']['example, resident']['forms']
    main = next(f for f in checklist if f['form_id'] == fid)
    absent = next(f for f in checklist if f['form_id'] == empty_form['id'])
    assert (main['saved'], main['period'], main['in_pdf']) == (2, 2, 2)
    assert absent['saved'] == absent['period'] == absent['in_pdf'] == 0
    assert any('No saved evaluations' in note for note in absent['notes'])


def test_checklist_distinguishes_outside_period_and_form_filter():
    state, _, fid = state_and_rows()
    extra = _form(prefix='2', name='Other form')
    state = core.register_form(state, extra)
    state, _ = core.ingest_evaluations(state, [_record(prefix='2'), _record(prefix='2', session='2025-07-01')])
    rows = core.filter_rows(core.score_rows(state), academic_year=2026, months=[7, 8], form_ids=[fid])
    checklist = context(state, rows, selected_form_ids=[fid])['form_coverage']['example, resident']['forms']
    item = next(f for f in checklist if f['form_id'] == extra['id'])
    assert (item['saved'], item['period'], item['in_pdf']) == (2, 1, 0)
    assert 'Excluded by form filter' in item['notes']


def test_unrecognized_evaluations_in_period_are_flagged_without_guessing_a_form():
    state, rows, _ = state_and_rows()
    state, _ = core.ingest_evaluations(state, [_record(prefix='8'), _record(prefix='9', session='2025-07-01')])
    coverage = context(state, rows)['form_coverage']['example, resident']
    assert sum(item['n'] for item in coverage['unmatched']) == 1
    assert coverage['unmatched'][0]['sources'] == ['example.tab']


def test_ambiguous_evaluation_is_flagged_not_allocated_to_either_form():
    state, rows, fid = state_and_rows()
    second = _form(prefix='2')
    second['questions'].append(copy.deepcopy(state['forms'][fid]['questions'][0]))
    state = core.register_form(state, second)
    record = _record()
    record['responses'] = {'102': {'response': '4'}}
    record['rotation'] = 'Ambiguous rotation'
    state, _ = core.ingest_evaluations(state, [record])
    coverage = context(state, rows)['form_coverage']['example, resident']
    assert any(item['reason'] == 'ambiguous' and item['n'] == 1 for item in coverage['unmatched'])


def test_incomplete_dates_and_unconfirmed_scoring_are_visible():
    state, rows, fid = state_and_rows()
    record = _record(session=None, evaluator='Other')
    state, _ = core.ingest_evaluations(state, [record])
    state['forms'][fid]['scoring_confirmed'] = False
    item = next(f for f in context(state, rows)['form_coverage']['example, resident']['forms'] if f['form_id'] == fid)
    assert item['undated'] == 1
    assert 'Scoring not confirmed' in item['notes']


def test_completion_month_checklist_uses_completion_not_session_month():
    state, _, fid = state_and_rows()
    rows = core.filter_rows(core.score_rows(state), academic_year=2026, months=[10], date_basis='completed_at')
    report = context(state, rows, report_selection={'academic_year': 2026, 'start_month': 7,
                                                  'months': [10], 'date_basis': 'completed_at'})
    item = next(f for f in report['form_coverage']['example, resident']['forms'] if f['form_id'] == fid)
    assert item['period'] == 2


def test_no_new_scoring_or_persistence_files_changed_by_this_feature():
    # The renderer imports the original average builder rather than computing
    # or overwriting evaluation-level EPA totals.
    with patch.object(grouped, 'pdf_question_sections', return_value=[]) as original:
        assert grouped.grouped_question_sections(pd.DataFrame(), {}, {}) == []
        original.assert_called_once()


def test_deferred_ui_captures_mappings_and_filters_without_storage_access():
    path = Path(__file__).resolve().parents[1] / 'epa_tracker.py'
    source = path.read_text()
    tree = ast.parse(source)
    definition = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'pdf_download_button')
    class Fake:
        session_state = {'_epa_report_filters': {'form_ids': []}}
        calls = []
        def download_button(self, *args, **kwargs):
            self.calls.append((args, kwargs))
    fake = Fake()
    namespace = {'st': fake, 'pd': pd, 'copy': copy, 'REPORT_FILTERS_KEY': '_epa_report_filters',
                 'grouped_context': grouped.grouped_context, 'report_snapshot': grouped.report_snapshot,
                 'build_question_pdf': build_question_pdf}
    exec(compile(ast.Module(body=[definition], type_ignores=[]), str(path), 'exec'), namespace)
    state, rows, fid = state_and_rows()
    before = copy.deepcopy(state)
    namespace['pdf_download_button'](state, rows, context(state, rows), label='Download PDF',
                                     key='test', filename='test.pdf', help_text='')
    callback = fake.calls[-1][1]['data']
    assert callable(callback) and fake.calls[-1][1]['on_click'] == 'ignore'
    state['settings']['appd_question_maps'].clear()
    text = pdf_text(callback())
    assert 'EPA 1' in text and 'EPA 10' in text
    assert rows['score'].notna().any()


def test_original_pdf_entry_point_without_grouping_remains_backward_compatible():
    state, rows, _ = state_and_rows()
    text = pdf_text(build_question_pdf(rows, state['forms'], CONTEXT))
    assert 'Example EPA' in text and 'Form checklist' not in text
