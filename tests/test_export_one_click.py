"""One-click APPD exports with fictional data and program-defined test rules."""
import copy
import io
import math
import zipfile
from xml.etree import ElementTree as ET
from unittest.mock import patch

import pytest

import epa_appd as appd
import epa_core as core
import epa_export_rules as rules
import epa_workflow as wf
from test_appd import make_state
from test_numeric_workflow import configured, review_rows, KEY, Store
from test_simple_ui import StaffUI, load_simple
from test_start_date_ui import Rerun, commit_function


def lower_state():
    state = configured()
    state["settings"]["appd"] = rules.example_parameters(appd.config_for(state))
    return state


def signoff(state, rows=None, proposed=None):
    cfg = appd.config_for(state)
    return wf.approve_save_prepare(state, proposed or cfg, rows if rows is not None else review_rows(state),
                                   appd.input_fingerprint(state, cfg),
                                   expected_saved=appd.digest(state["settings"].get(wf.REVIEW_NAMESPACE, {}).get(appd.review_key(cfg))))


@pytest.mark.parametrize("score,code", [
    (1, "1"), (1.749999, "1"), (1.75, "2a"), (2.0, "2a"),
    (2.249999, "2a"), (2.25, "2b"), (2.749999, "2b"),
    (2.75, "3a"), (3, "3a"), (3.249999, "3a"),
    (3.25, "3b"), (3.36, "3b"), (3.749999, "3b"),
    (3.75, "4"), (3.77, "4"), (4, "4"), (4.749999, "4"),
    (4.75, "5"), (5, "5"),
])
def test_example_lower_cutoffs_and_exact_boundaries(score, code):
    cfg = rules.example_parameters(appd.defaults())
    assert appd.suggested_level(score, cfg) == code


@pytest.mark.parametrize("score", [None, True, -1, 0, .999, 5.0000001, math.nan, math.inf, -math.inf, "3b"])
def test_bad_or_uncovered_scores_are_not_rounded_or_clipped(score):
    assert not appd.suggested_level(score, rules.example_parameters(appd.defaults()))


def test_example_is_not_installed_or_approved_on_page_load():
    state = make_state()
    original = copy.deepcopy(state)
    fake = StaffUI()
    load_simple(fake).export_page(state, None, lambda *args: pytest.fail("unexpected write"))
    assert state == original
    assert fake.editors[0][0].iloc[0]["EPA 2"] == 3
    assert fake.downloads[0][1]["disabled"]
    assert state["settings"]["appd"]["score_bands"] == []


def test_defaults_button_changes_only_session_draft():
    state = make_state()
    original = copy.deepcopy(state)
    fake = StaffUI(clicked="Restore residency defaults")
    with pytest.raises(Rerun):
        load_simple(fake).export_page(state, None, lambda *args: pytest.fail("unexpected write"))
    assert state == original
    draft = next(value for key, value in fake.session_state.items() if key.startswith("upload_parameter_draft_") and not key.endswith("_revision"))
    assert rules.convert_score(3.77, draft) == "3b"
    assert not state["settings"].get(wf.REVIEW_NAMESPACE)


def test_legacy_upper_limits_keep_their_boundary_behavior():
    cfg = appd.config_for(configured())
    for value, expected in [(1, "1"), (1.00001, "2a"), (2, "2a"), (2.00001, "2b"),
                            (3, "3a"), (3.00001, "3b"), (3.5, "3b"), (3.50001, "4"), (5, "5")]:
        assert rules.convert_score(value, cfg) == expected
    edited = rules.parameters_from_rows(cfg, rules.editor_rows(cfg), mode=rules.UPPER,
                                        minimum=cfg["minimum_score"])
    assert edited == cfg


def test_custom_parameters_respect_user_cutoffs_and_do_not_round_source():
    state = lower_state()
    cfg = appd.config_for(state)
    cfg["score_bands"][-2]["lower"] = 3.8
    assert rules.convert_score(3.77, cfg) == "3b"
    assert rules.convert_score(3.8, cfg) == "4"
    rows = review_rows(state)
    rows[0]["EPA 2"] = 3.771234
    outcome = wf.prepare_export(state, cfg, rows, accept_review_notes=True)
    assert outcome["numeric_rows"][0]["EPA 2"] == 3.771234
    assert rows[0]["EPA 2"] == 3.771234
    assert outcome["upload_rows"][0]["EPA 2"] == "3b"


@pytest.mark.parametrize("mutation", [
    lambda c: c["score_bands"][2].update(lower=1.75),
    lambda c: c["score_bands"][2].update(lower=0),
    lambda c: c["score_bands"][2].update(lower=None),
    lambda c: c["score_bands"][2].update(lower=math.inf),
    lambda c: c["score_bands"][2].update(code="3b"),
    lambda c: c["score_bands"][2].update(code="0"),
    lambda c: c["score_bands"][2].update(code="3"),
    lambda c: c.update(maximum_score=3),
    lambda c: c.update(minimum_score=.5),
    lambda c: c.update(score_band_mode="guess"),
])
def test_invalid_rule_is_rejected(mutation):
    cfg = rules.example_parameters(appd.defaults())
    mutation(cfg)
    with pytest.raises(ValueError):
        rules.validate_parameters(cfg)


def test_one_button_prepares_both_snapshots_without_named_reviewer():
    state = lower_state()
    source = copy.deepcopy(state)
    rows = review_rows(state)
    rows[0]["EPA 2"] = 3.77
    saved, blob = signoff(state, rows)
    cfg = appd.config_for(saved)
    snapshot = wf.ready_review(saved, cfg)
    assert blob.startswith(b"PK")
    assert snapshot["rows"][0]["EPA 2"] == 3.77
    assert snapshot["reviewer"] == ""
    assert snapshot["approval_action"] == "approve_save_prepare"
    assert snapshot["review_notes_accepted"]
    assert snapshot["saved_at"]
    assert appd.approved_review(saved, cfg)["rows"][0]["EPA 2"] == "4"
    assert state == source
    for key in ["forms", "evaluations", "conflicts"]:
        assert saved[key] == source[key]
    assert saved["settings"]["appd_roster"] == source["settings"]["appd_roster"]
    assert saved["settings"]["appd_question_maps"] == source["settings"]["appd_question_maps"]


@pytest.mark.parametrize("kind", ["mapping", "scoring", "conflict", "missing_date", "unknown_form", "excluded_question", "unmapped_code"])
def test_review_notes_are_nonblocking_only_when_explicitly_approved(kind):
    state = lower_state()
    fid = next(iter(state["forms"]))
    record = next(iter(state["evaluations"].values()))
    if kind == "mapping":
        state["settings"]["appd_question_maps"][fid]["confirmed"] = False
    elif kind == "scoring":
        state["forms"][fid]["scoring_confirmed"] = False
    elif kind == "conflict":
        state["conflicts"]["test-pending"] = {"note": "fictional correction held for review"}
    elif kind == "missing_date":
        missing = copy.deepcopy(record)
        missing["completed_at"] = ""
        state["evaluations"]["missing-date-record"] = missing
    elif kind == "unknown_form":
        unknown = copy.deepcopy(record)
        unknown["responses"] = {"unrecognized": {"response": "4"}}
        state["evaluations"]["unrecognized-record"] = unknown
    elif kind == "excluded_question":
        state["forms"][fid]["questions"][0]["include"] = False
    else:
        record["responses"]["101"]["response"] = "NOT-MAPPED"
    cfg = appd.config_for(state)
    rows = review_rows(state)
    strict = wf.prepare_export(state, cfg, rows)
    relaxed = wf.prepare_export(state, cfg, rows, accept_review_notes=True)
    assert strict["issues"]
    assert relaxed["review_notes"]
    assert not relaxed["issues"]
    saved, blob = signoff(state, rows)
    assert wf.ready_review(saved, cfg)
    assert blob.startswith(b"PK")
    assert wf.saved_review(saved, cfg)["review_notes"] == relaxed["review_notes"]


@pytest.mark.parametrize("kind", ["pgy", "date", "identity", "out_of_range", "nothing_selected", "injected_row"])
def test_true_upload_errors_still_block_even_when_review_notes_accepted(kind):
    state = lower_state()
    rows = review_rows(state)
    if kind == "pgy":
        state["settings"]["appd_roster"] = {}
        rows[0]["pgy"] = None
    elif kind == "date":
        rows[0]["assessmentdate"] = ""
    elif kind == "identity":
        rows[0]["name_or_program_assigned_ID"] = ""
    elif kind == "out_of_range":
        rows[0]["EPA 2"] = 5.001
    elif kind == "nothing_selected":
        rows[0]["Include"] = False
    elif kind == "injected_row":
        rows.append(dict(rows[0], resident_key="injected"))
    with pytest.raises(ValueError):
        signoff(state, rows)
    assert not state["settings"].get(wf.REVIEW_NAMESPACE)


def test_clearing_numeric_value_is_review_note_and_explicit_zero_not_silent():
    state = lower_state()
    rows = review_rows(state)
    rows[0]["EPA 2"] = None
    out = wf.prepare_export(state, appd.config_for(state), rows, accept_review_notes=True)
    assert out["upload_rows"][0]["EPA 2"] == "0"
    assert any("was cleared" in note for note in out["review_notes"])
    assert out["changes"][0]["Calculated average"] == 3
    saved, _ = signoff(state, rows)
    assert next(row for row in appd.build_draft(saved)["summary"] if row["APPD EPA"] == "EPA 2")["Average"] == 3


def test_bad_conversion_cannot_mark_scores_not_observed():
    state = lower_state()
    rows = review_rows(state)
    rows[0]["EPA 2"] = 9
    out = wf.prepare_export(state, appd.config_for(state), rows, accept_review_notes=True)
    assert out["issues"]
    assert out["upload_rows"][0]["EPA 2"] == ""


def test_changes_to_parameters_invalidate_download_and_keep_numeric_scores():
    state = lower_state()
    rows = review_rows(state)
    rows[0]["EPA 2"] = 3.77
    saved, _ = signoff(state, rows)
    cfg = appd.config_for(saved)
    assert wf.ready_review(saved, cfg)
    saved["settings"]["appd"]["score_bands"][-2]["lower"] = 3.8
    assert wf.ready_review(saved, appd.config_for(saved)) is None
    assert saved["settings"][wf.REVIEW_NAMESPACE][appd.review_key(cfg)]["rows"][0]["EPA 2"] == 3.77


def test_parameter_edits_and_numeric_edits_are_saved_together():
    state = make_state()
    proposed = rules.example_parameters(appd.config_for(state))
    rows = review_rows(state)
    rows[0]["EPA 2"] = 3.77
    saved, _ = signoff(state, rows, proposed)
    assert saved["settings"]["appd"] == proposed
    assert wf.ready_review(saved, proposed)["rows"][0]["EPA 2"] == 3.77


def test_failed_template_preparation_never_returns_an_approval():
    state = lower_state()
    source = copy.deepcopy(state)
    with patch.object(appd, "workbook_bytes", side_effect=ValueError("missing template")):
        with pytest.raises(ValueError, match="missing template"):
            signoff(state)
    assert state == source


def test_concurrent_rule_or_source_change_cannot_be_overwritten():
    state = lower_state()
    cfg = appd.config_for(state)
    signature = appd.input_fingerprint(state, cfg)
    rows = review_rows(state)
    state["settings"]["appd"]["score_bands"][-2]["lower"] = 3.8
    with pytest.raises(ValueError, match="changed"):
        wf.approve_save_prepare(state, cfg, rows, signature, expected_saved=appd.digest(None))


def test_concurrent_review_change_cannot_be_overwritten():
    state, _ = signoff(lower_state())
    cfg = appd.config_for(state)
    with pytest.raises(ValueError, match="Another user"):
        wf.approve_save_prepare(state, cfg, review_rows(state), appd.input_fingerprint(state, cfg), expected_saved=appd.digest(None))


def test_explicit_approval_flag_is_required_without_named_reviewer():
    state = lower_state()
    cfg = appd.config_for(state)
    with pytest.raises(ValueError, match="reviewer"):
        wf.save_numeric_review(state, cfg, review_rows(state), appd.input_fingerprint(state, cfg), approved=True)
    with pytest.raises(ValueError, match="explicit approval"):
        wf.save_numeric_review(state, cfg, review_rows(state), appd.input_fingerprint(state, cfg),
                                approved=True, reviewer="TEST", accept_review_notes=True)


def test_download_rechecks_latest_archive_and_saved_approval():
    saved, blob = signoff(lower_state())
    cfg = appd.config_for(saved)
    sig = appd.input_fingerprint(saved, cfg)
    approval = wf.ready_review(saved, cfg)
    assert wf.download_bytes(Store(saved), sig, appd.digest(approval)).startswith(b"PK")
    saved["settings"]["appd"]["score_bands"][-2]["lower"] = 3.8
    with pytest.raises(ValueError, match="changed"):
        wf.download_bytes(Store(saved), sig, appd.digest(approval))


def test_generated_workbook_has_numeric_four_and_real_letter_codes():
    state = lower_state()
    rows = review_rows(state)
    for epa, value in zip(appd.EPA_COLUMNS, [3.77, 2, 2.5, 3, 3.5, 4.9, None, 1, 3.7499, 3.75, 4.7499, 4.75]):
        rows[0][epa] = value
    saved, data = signoff(state, rows)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        root = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
        cells = root.find(appd.S + "sheetData")[-1]
        def val(cell):
            if cell.get("t") == "inlineStr":
                return cell.find(appd.S + "is/" + appd.S + "t").text
            return cell.find(appd.S + "v").text
        values = {cell.get("r"): val(cell) for cell in cells}
        assert values["G3"] == "4"
        assert values["H3"] == "2a"
        assert values["I3"] == "2b"
        assert values["J3"] == "3a"
        assert values["K3"] == "3b"
        assert values["M3"] == "0"
        assert values["P3"] == "4"
        assert not root.findall(".//" + appd.S + "f")
        assert len(ET.fromstring(z.read("xl/workbook.xml")).find(appd.S + "sheets")) == 1
        for row in root.find(appd.S + "sheetData"):
            if row.get("r") == "3":
                assert row.find(appd.S + "c[@r='B3']").get("t") == "inlineStr"
    assert wf.ready_review(saved, appd.config_for(saved))["rows"][0]["EPA 1"] == 3.77


def test_ui_approval_needs_no_name_checkbox_or_save_draft():
    fake = StaffUI(clicked="Approve, save and prepare both files")
    state = lower_state()
    writes = []
    with pytest.raises(Rerun):
        load_simple(fake).export_page(state, None, commit_function(state, writes))
    assert len(writes) == 1
    assert wf.ready_review(writes[0], appd.config_for(writes[0]))
    assert not any(label == "Save draft" for label, _ in fake.buttons)


def test_ui_warnings_allow_approval_with_saved_good_scores():
    state = lower_state()
    state["conflicts"]["test"] = {"note": "fictional"}
    fake = StaffUI(clicked="Approve, save and prepare both files")
    writes = []
    with pytest.raises(Rerun):
        load_simple(fake).export_page(state, None, commit_function(state, writes))
    assert len(writes) == 1
    assert wf.ready_review(writes[0], appd.config_for(writes[0]))
    assert any("Pending duplicate" in message for message in fake.messages)


def test_ui_failed_save_never_shows_ready_download_or_success():
    fake = StaffUI(clicked="Approve, save and prepare both files")
    state = lower_state()
    load_simple(fake).export_page(state, None, lambda *args: False)
    assert fake.downloads[0][1]["disabled"]
    assert "notice" not in fake.session_state


def test_ui_unsaved_parameter_edit_disables_previous_download():
    state, _ = signoff(lower_state())
    fake = StaffUI(edits={"upload_parameters_": [(5, "Minimum average", 3.8)]})
    load_simple(fake).export_page(state, None, lambda *args: False)
    assert fake.downloads[0][1]["disabled"]
    assert state["settings"]["appd"]["score_bands"][5]["lower"] == 3.75


def test_ui_parameters_are_not_duplicated_in_admin():
    import inspect
    fake = StaffUI()
    module = load_simple(fake)
    assert '"Data Hub rules",' not in inspect.getsource(module.admin_page)


def test_ui_shows_current_numeric_average_when_rules_incomplete():
    fake = StaffUI()
    load_simple(fake).export_page(make_state(), None, lambda *args: False)
    assert fake.editors[0][0].iloc[0]["EPA 2"] == 3
    assert fake.downloads[0][1]["disabled"]
    assert not fake.errors


def test_one_click_parameters_notes_and_approval_survive_encrypted_roundtrip():
    from cryptography.fernet import Fernet
    from epa_storage import GitHubStore
    state = lower_state()
    state['conflicts']['example-note'] = {'note': 'pending fictional correction'}
    saved, data = signoff(state)
    store = GitHubStore({'token': 'synthetic-token', 'repo': 'example/epa'}, Fernet.generate_key())
    encrypted = store.encode(saved)
    assert b'Example, Resident' not in encrypted
    assert b'example-note' not in encrypted
    loaded = store.decode(encrypted)
    cfg = appd.config_for(loaded)
    assert cfg == appd.config_for(saved)
    assert wf.ready_review(loaded, cfg)['review_notes_accepted']
    assert appd.workbook_bytes(loaded, cfg).startswith(b'PK')


def test_download_rejects_a_different_approval_with_same_numeric_inputs():
    saved, _ = signoff(lower_state())
    cfg = appd.config_for(saved)
    sig = appd.input_fingerprint(saved, cfg)
    old = wf.ready_review(saved, cfg)
    saved['settings'][wf.REVIEW_NAMESPACE][appd.review_key(cfg)]['saved_at'] = '2099-01-01T01:00:00+00:00'
    with pytest.raises(ValueError, match='review changed'):
        wf.download_bytes(Store(saved), sig, appd.digest(old))
