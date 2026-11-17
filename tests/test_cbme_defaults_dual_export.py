"""User-supplied Nov 17 CBME policy and paired exports. Fictional residents only."""
import copy
import io
import math
import zipfile
from datetime import date
from xml.etree import ElementTree as ET
from unittest.mock import patch

import pytest

import epa_appd as appd
import epa_export_rules as rules
import epa_workflow as workflow
import epa_dual_export as dual
from test_appd import make_state
from test_numeric_workflow import Store, review_rows
from test_simple_ui import StaffUI, load_simple
from test_start_date_ui import Rerun, commit_function


def default_state():
    state = make_state()
    state['settings']['appd'] = rules.default_parameters(state['settings']['appd'])
    return state


def approve(state, rows=None, config=None):
    baseline = appd.config_for(state)
    return workflow.approve_save_prepare(
        state, config or baseline, rows if rows is not None else review_rows(state),
        appd.input_fingerprint(state, baseline),
        expected_saved=appd.digest(state['settings'].get(workflow.REVIEW_NAMESPACE, {}).get(appd.review_key(baseline))))


def workbook(blob):
    with zipfile.ZipFile(io.BytesIO(blob)) as file:
        parts = {name: file.read(name) for name in file.namelist()}
    root = ET.fromstring(parts['xl/worksheets/sheet1.xml'])
    shared = ET.fromstring(parts['xl/sharedStrings.xml'])
    strings = [''.join(item.itertext()) for item in shared]
    values = {}
    cells = {}
    for row in root.find(appd.S+'sheetData'):
        for cell in row:
            cells[cell.get('r')] = cell
            if cell.get('t') == 'inlineStr':
                value = ''.join(node.text or '' for node in cell.iter(appd.S+'t'))
            elif cell.get('t') == 's':
                value = strings[int(cell.find(appd.S+'v').text)]
            else:
                value_node = cell.find(appd.S+'v')
                value = None if value_node is None else float(value_node.text)
            values[cell.get('r')] = value
    return values, cells, root, parts


@pytest.mark.parametrize('value,expected', [
    (0, '0'), (0.49, '0'), (0.499999999, '0'), (0.5, '1'),
    (1, '1'), (1.49, '1'), (1.499999999, '1'), (1.5, '2a'),
    (2, '2a'), (2.49, '2a'), (2.499999999, '2a'), (2.5, '2b'),
    (2.99, '2b'), (2.999999999, '2b'), (3, '3a'), (3.36, '3a'),
    (3.49, '3a'), (3.495, '3a'), (3.499999999, '3a'), (3.5, '3b'),
    (3.77, '3b'), (3.8, '3b'), (3.99, '3b'), (3.999999999, '3b'),
    (4, '4'), (4.49, '4'), (4.499999999, '4'), (4.5, '5'), (5, '5'),
])
def test_supplied_ranges_and_full_precision_boundaries(value, expected):
    config = appd.defaults(date(2026, 11, 17))
    assert appd.suggested_level(value, config) == expected


@pytest.mark.parametrize('value', [-0.000001, 5.000001, None, True, False, math.inf, -math.inf, math.nan, '3b'])
def test_invalid_or_outside_range_is_never_clipped(value):
    assert rules.convert_score(value, appd.defaults()) == ''


def test_defaults_exactly_match_user_table_and_are_not_shared_mutable_objects():
    first = appd.defaults()
    second = appd.defaults()
    assert [(b['code'], b['lower']) for b in first['score_bands']] == [
        ('0', 0.0), ('1', 0.5), ('2a', 1.5), ('2b', 2.5),
        ('3a', 3.0), ('3b', 3.5), ('4', 4.0), ('5', 4.5)]
    assert first['minimum_score'] == 0 and first['maximum_score'] == 5
    assert first['conversion_enabled'] and first['conversion_confirmed']
    first['score_bands'][6]['lower'] = 3.75
    assert second['score_bands'][6]['lower'] == 4


def test_legacy_upgrade_applies_defaults_once_retains_old_rules_and_does_not_mutate_source():
    state = make_state()
    state['settings']['appd'] = rules.example_parameters(state['settings']['appd'])
    state['settings']['appd'].pop('cbme_defaults_version')
    original = copy.deepcopy(state)
    cfg = appd.config_for(state)
    assert rules.convert_score(3.77, cfg) == '3b'
    assert cfg['previous_conversion_parameters']['score_bands'][-2]['lower'] == 3.75
    assert cfg['programid'] == original['settings']['appd']['programid']
    assert cfg == appd.config_for(state)
    assert state == original
    saved, _ = approve(state)
    assert saved['settings']['appd']['cbme_defaults_version'] == rules.DEFAULTS_VERSION
    assert saved['evaluations'] == original['evaluations']
    assert saved['forms'] == original['forms']
    assert saved['settings']['appd_question_maps'] == original['settings']['appd_question_maps']


def test_custom_rules_survive_reload_and_defaults_can_be_restored():
    state = default_state()
    config = appd.config_for(state)
    edits = rules.editor_rows(config)
    edits[6]['Minimum average'] = 3.75
    custom = rules.parameters_from_rows(config, edits, mode=rules.LOWER)
    rows = review_rows(state)
    rows[0]['EPA 2'] = 3.77
    saved, _ = approve(state, rows, custom)
    assert rules.convert_score(3.77, appd.config_for(saved)) == '4'
    assert rules.convert_score(3.77, rules.default_parameters(appd.config_for(saved))) == '3b'
    assert workflow.ready_review(saved, appd.config_for(saved))
    assert workflow.ready_review(saved, appd.config_for(saved))['rows'][0]['EPA 2'] == 3.77


def test_zero_conversion_has_distinct_note_and_does_not_conflate_blank_or_numeric_zero():
    state = default_state()
    rows = review_rows(state)
    rows[0]['EPA 1'] = 0.0
    rows[0]['EPA 2'] = 0.49
    result = workflow.prepare_export(state, appd.config_for(state), rows, accept_review_notes=True)
    assert result['converted_zero'] == 2
    assert result['not_observed'] == 10
    assert not result['issues']
    assert result['numeric_rows'][0]['EPA 1'] == 0
    assert result['numeric_rows'][0]['EPA 3'] is None
    assert any('2 numeric EPA values' in note for note in result['review_notes'])
    saved, ratings = approve(state, rows)
    raw = workflow.raw_workbook_bytes(saved, appd.config_for(saved))
    rated_values, _, _, _ = workbook(ratings)
    raw_values, _, _, _ = workbook(raw)
    assert [rated_values[c] for c in ['G3','H3','I3']] == [0,0,0]
    assert [raw_values[c] for c in ['G3','H3','I3']] == [0,0.49,None]


def test_both_workbooks_same_shape_ids_dates_and_pgy_but_different_scores():
    state = default_state()
    rows = review_rows(state)
    values = [0, .49, .5, 1.49, 1.5, 2.5, 3, 3.77, 4, 4.5, 3.495123456789, None]
    rows[0].update(zip(appd.EPA_COLUMNS, values))
    rows[0]['name_or_program_assigned_ID'] = '0000123'
    saved, ratings = approve(state, rows)
    raw = workflow.raw_workbook_bytes(saved, appd.config_for(saved))
    rv, rc, rr, rp = workbook(raw)
    cv, cc, cr, cp = workbook(ratings)
    for column in 'ABCDEF':
        assert rv[column+'3'] == cv[column+'3']
    assert rv['C3'] == '0000123'
    assert rv['B3'] == '07/31/2026' and rc['B3'].get('t') == 'inlineStr'
    assert rv['D3'] == 3
    assert rv['N3'] == 3.77 and cv['N3'] == '3b'
    assert rv['Q3'] == values[-2] and cv['Q3'] == '3a'
    assert rv['R3'] is None and cv['R3'] == 0
    assert all(rc[c+'3'].get('t') != 'inlineStr' for c in 'GHIJKLMNOPQR')
    assert cc['K3'].get('t') == 'inlineStr'  # 2a must stay literal text
    assert rr.find(appd.S+'dataValidations') is None
    assert cr.find(appd.S+'dataValidations') is not None
    assert [row.get('r') for row in rr.find(appd.S+'sheetData')] == ['1','2','3']
    assert [row.get('r') for row in cr.find(appd.S+'sheetData')] == ['1','2','3']
    for contents in [rp,cp]:
        assert len(ET.fromstring(contents['xl/workbook.xml']).find(appd.S+'sheets')) == 1
    assert not rr.findall('.//'+appd.S+'f') and not cr.findall('.//'+appd.S+'f')
    assert [rv[chr(65+i)+'2'] for i in range(18)] == appd.HEADERS
    assert [cv[chr(65+i)+'2'] for i in range(18)] == appd.HEADERS
    assert state['evaluations'] == saved['evaluations']


def test_raw_export_keeps_literal_formula_like_identifier_safe():
    state = default_state()
    rows = review_rows(state)
    rows[0]['name_or_program_assigned_ID'] = '=SUM(1,2)'
    saved, _ = approve(state, rows)
    values, _, root, _ = workbook(workflow.raw_workbook_bytes(saved, appd.config_for(saved)))
    assert values['C3'] == '=SUM(1,2)'
    assert not root.findall('.//'+appd.S+'f')


def test_raw_filename_distinct_and_ratings_filename_retains_import_convention():
    cfg = appd.config_for(default_state())
    assert appd.export_filename(cfg) == 'Example Program-FALL-2026.xlsx'
    assert workflow.raw_export_filename(cfg) == 'Example Program-FALL-2026-RAW.xlsx'


@pytest.mark.parametrize('variant', ['raw','ratings'])
def test_each_download_rechecks_latest_archive_and_rejects_changed_rules(variant):
    saved, _ = approve(default_state())
    cfg = appd.config_for(saved)
    sig = appd.input_fingerprint(saved, cfg)
    snapshot = appd.digest(workflow.ready_review(saved, cfg))
    assert workflow.download_bytes(Store(saved), sig, snapshot, variant=variant).startswith(b'PK')
    saved['settings']['appd']['score_bands'][6]['lower'] = 3.75
    with pytest.raises(ValueError, match='changed'):
        workflow.download_bytes(Store(saved), sig, snapshot, variant=variant)


@pytest.mark.parametrize('variant', ['raw','ratings'])
def test_each_download_rejects_replaced_review(variant):
    saved, _ = approve(default_state())
    cfg = appd.config_for(saved)
    sig = appd.input_fingerprint(saved, cfg)
    snapshot = appd.digest(workflow.ready_review(saved, cfg))
    saved['settings'][workflow.REVIEW_NAMESPACE][appd.review_key(cfg)]['saved_at'] = '2099-01-01'
    with pytest.raises(ValueError, match='review changed'):
        workflow.download_bytes(Store(saved), sig, snapshot, variant=variant)


def test_raw_failure_does_not_save_half_of_an_approval():
    state = default_state()
    original = copy.deepcopy(state)
    with patch.object(dual, 'raw_from_ratings', side_effect=ValueError('raw writer failed')):
        with pytest.raises(ValueError, match='raw writer failed'):
            approve(state)
    assert state == original
    assert not state['settings'].get(workflow.REVIEW_NAMESPACE)


def test_unapproved_raw_download_is_not_available():
    state = default_state()
    with pytest.raises(ValueError, match='Approve'):
        workflow.raw_workbook_bytes(state, appd.config_for(state))


def test_defaults_and_both_downloads_survive_encrypted_round_trip():
    from epa_storage import GitHubStore
    from cryptography.fernet import Fernet
    state, _ = approve(default_state())
    store = GitHubStore({'token':'fictional', 'repo':'example/epa'}, Fernet.generate_key())
    encoded = store.encode(state)
    assert b'Example, Resident' not in encoded
    loaded = store.decode(encoded)
    cfg = appd.config_for(loaded)
    assert cfg['score_bands'] == state['settings']['appd']['score_bands']
    assert appd.workbook_bytes(loaded, cfg).startswith(b'PK')
    assert workflow.raw_workbook_bytes(loaded, cfg).startswith(b'PK')


def test_new_defaults_page_needs_no_parameter_setup_or_auto_save():
    state = default_state()
    original = copy.deepcopy(state)
    fake = StaffUI()
    load_simple(fake).export_page(state, None, lambda *a: pytest.fail('must not save on open'))
    assert not fake.errors
    assert fake.editors[0][0].iloc[0]['EPA 2'] == 3.0
    assert fake.editors[1][0]['Upload code'].tolist() == list(rules.CODES)
    assert fake.editors[1][0]['Minimum average'].tolist() == list(rules.DEFAULT_MINIMUMS)
    approval = next(kwargs for label, kwargs in fake.buttons if label == 'Approve, save and prepare both files')
    assert not approval['disabled']
    assert all(kwargs.get('disabled') for label,kwargs in fake.downloads[:2])
    assert state == original


def test_one_button_saves_and_enables_both_later_downloads_without_name_or_checkbox():
    state = default_state()
    fake = StaffUI(clicked='Approve, save and prepare both files')
    writes=[]
    with pytest.raises(Rerun):
        load_simple(fake).export_page(state, None, commit_function(state,writes))
    assert len(writes)==1
    assert workflow.ready_review(writes[0], appd.config_for(writes[0]))
    next_ui = StaffUI()
    load_simple(next_ui).export_page(writes[0], Store(writes[0]), lambda *a: False)
    assert [label for label,kw in next_ui.downloads[:2]] == ['Download raw averages (.xlsx)', 'Download CBME ratings (.xlsx)']
    assert all(callable(kw['data']) and not kw.get('disabled') for label,kw in next_ui.downloads[:2])
    raw_data = next_ui.downloads[0][1]['data']()
    converted_data = next_ui.downloads[1][1]['data']()
    assert workbook(raw_data)[0]['H3'] == 3
    assert workbook(converted_data)[0]['H3'] == '3a'


def test_unsaved_numeric_or_cutoff_edits_disable_both_downloads():
    saved, _ = approve(default_state())
    for edits in [
        {'numeric_editor': [('example, resident','EPA 2',3.77)]},
        {'upload_parameters_': [(6,'Minimum average',3.75)]},
    ]:
        fake=StaffUI(edits=edits)
        load_simple(fake).export_page(saved,None,lambda *a:False)
        assert all(kw.get('disabled') for label,kw in fake.downloads[:2])


def test_missing_required_metadata_still_blocks_both_but_warnings_do_not():
    state=default_state()
    state['conflicts']['fictional']={'reason':'test only'}
    rows=review_rows(state)
    saved,_=approve(state,rows)
    assert workflow.ready_review(saved,appd.config_for(saved))
    rows[0]['name_or_program_assigned_ID']=''
    with pytest.raises(ValueError,match='ID'):
        approve(state,rows)


def test_numeric_raw_export_respects_excluded_residents():
    import epa_core as core
    state=default_state()
    record=copy.deepcopy(next(iter(state['evaluations'].values())))
    record.update(resident='Second, Fictional',resident_key='second, fictional')
    for field in ['id','identity','content_hash','base_key','record_id']:
        record.pop(field,None)
    state,_=core.ingest_evaluations(state,[record])
    rows=review_rows(state)
    assert len(rows)==2
    excluded=next(row for row in rows if row['resident_key']=='second, fictional')
    excluded['Include']=False
    saved,converted=approve(state,rows)
    raw=workflow.raw_workbook_bytes(saved,appd.config_for(saved))
    assert [r.get('r') for r in workbook(raw)[2].find(appd.S+'sheetData')] == ['1','2','3']
    assert [r.get('r') for r in workbook(converted)[2].find(appd.S+'sheetData')] == ['1','2','3']


def test_raw_transform_rejects_order_mismatch():
    state=default_state()
    rows=review_rows(state)
    _,converted=approve(state,rows)
    rows[0]['name_or_program_assigned_ID']='incorrect'
    with pytest.raises(ValueError,match='order'):
        dual.raw_from_ratings(converted,rows)
