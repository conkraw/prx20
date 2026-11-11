"""Numeric review and Data Hub compatibility, all data invented."""
import copy
import io
import zipfile
from datetime import date
from xml.etree import ElementTree as ET

import pytest

import epa_appd as appd
import epa_workflow as wf
import epa_core as core
from test_appd import make_state

KEY = "example, resident"


def configured():
    state = make_state()
    state["settings"]["appd"].update(conversion_enabled=True, conversion_confirmed=True,
        # Synthetic test thresholds only, not a proposed program policy.
        score_bands=[{"upper": 1, "code": "1"}, {"upper": 2, "code": "2a"},
                     {"upper": 2.5, "code": "2b"}, {"upper": 3, "code": "3a"},
                     {"upper": 3.5, "code": "3b"}, {"upper": 4.9, "code": "4"},
                     {"upper": 5, "code": "5"}])
    state["settings"]["appd_roster"][KEY]["residency_start_date"] = "2026-07-01"
    return state


def review_rows(state):
    return wf.numeric_rows(appd.build_draft(state))


def approve(state, rows=None):
    cfg = appd.config_for(state)
    return wf.save_numeric_review(state, cfg, rows or review_rows(state), appd.input_fingerprint(state, cfg),
                                  approved=True, reviewer="TEST", expected_saved=appd.digest(None))


def test_numeric_review_is_prepopulated_without_conversion():
    state = make_state()
    rows = review_rows(state)
    assert rows[0]["EPA 2"] == 3.0
    assert rows[0]["EPA 1"] is None
    assert all("Local level" not in row for row in appd.build_draft(state)["summary"])
    assert "Data Hub rules" in wf.prepare_export(state, appd.config_for(state), rows)["issues"][0]


@pytest.mark.parametrize("value", [3, 3.25, 3.5, 3.75, 4.0, 4.999999, 5.0])
def test_numeric_values_remain_numeric_and_full_precision(value):
    state = configured()
    rows = review_rows(state)
    rows[0]["EPA 2"] = value
    out = wf.prepare_export(state, appd.config_for(state), rows)
    assert out["numeric_rows"][0]["EPA 2"] == value
    assert isinstance(out["numeric_rows"][0]["EPA 2"], float)
    assert out["upload_rows"][0]["EPA 2"] in appd.APPD_CODES
    assert not out["issues"]


@pytest.mark.parametrize("value", ["3b", "3a", True, float("inf"), float("-inf")])
def test_non_numeric_or_nonfinite_input_rejected(value):
    state = configured()
    rows = review_rows(state)
    rows[0]["EPA 2"] = value
    with pytest.raises(ValueError):
        wf.prepare_export(state, appd.config_for(state), rows)


@pytest.mark.parametrize("value", [None, "", float("nan")])
def test_clearing_existing_average_is_not_classified_not_observed(value):
    state = configured()
    rows = review_rows(state)
    rows[0]["EPA 2"] = value
    out = wf.prepare_export(state, appd.config_for(state), rows)
    assert any("cleared" in issue for issue in out["issues"])
    assert out["upload_rows"][0]["EPA 2"] == ""


def test_no_scored_evidence_stays_none_in_review_and_zero_only_in_upload():
    state = configured()
    out = wf.prepare_export(state, appd.config_for(state), review_rows(state))
    assert out["numeric_rows"][0]["EPA 1"] is None
    assert out["upload_rows"][0]["EPA 1"] == "0"
    assert out["not_observed"] == 11


@pytest.mark.parametrize("change", ["mapping", "scoring", "excluded", "unknown_code", "conflict", "no_links"])
def test_incomplete_evidence_blocks_ready_file(change):
    state = configured()
    fid = next(iter(state["forms"]))
    if change == "mapping":
        state["settings"]["appd_question_maps"][fid]["confirmed"] = False
    elif change == "scoring":
        state["forms"][fid]["scoring_confirmed"] = False
    elif change == "excluded":
        state["forms"][fid]["questions"][0]["include"] = False
    elif change == "unknown_code":
        next(iter(state["evaluations"].values()))["responses"]["101"]["response"] = "999"
    elif change == "no_links":
        state = appd.save_mapping(state, fid, {}, True)
    else:
        state["conflicts"] = {"synthetic": {}}
    cfg = appd.config_for(state)
    out = wf.prepare_export(state, cfg, review_rows(state))
    assert out["issues"]
    with pytest.raises(ValueError):
        approve(state)


@pytest.mark.parametrize("value", [0, 0.99, 5.00001, 6])
def test_out_of_range_scores_not_rounded_to_valid_codes(value):
    state = configured()
    rows = review_rows(state)
    rows[0]["EPA 2"] = value
    assert any("outside" in issue for issue in wf.prepare_export(state, appd.config_for(state), rows)["issues"])


def test_rounding_display_never_changes_conversion_boundary():
    state = configured()
    rows = review_rows(state)
    rows[0]["EPA 2"] = 3.50000001
    out = wf.prepare_export(state, appd.config_for(state), rows)
    assert out["numeric_rows"][0]["EPA 2"] == 3.50000001
    assert out["upload_rows"][0]["EPA 2"] == "4"  # synthetic threshold above 3.5


def test_edited_assessment_date_recomputes_pgy():
    state = configured()
    rows = review_rows(state)
    rows[0]["assessmentdate"] = "2027-10-01"
    rows[0]["pgy"] = 9
    normalized = wf.normalize_rows(state, rows)
    assert normalized[0]["pgy"] == 2
    assert normalized[0]["EPA 2"] == 3


def test_no_start_date_blocks_export_not_averaging():
    state = configured()
    state["settings"]["appd_roster"][KEY] = {"resident": "Example, Resident", "export_id": "ID"}
    rows = review_rows(state)
    assert rows[0]["EPA 2"] == 3
    assert any("start date" in issue for issue in wf.prepare_export(state, appd.config_for(state), rows)["issues"])


def test_review_save_does_not_modify_source_scores_or_mappings():
    state = configured()
    before = copy.deepcopy(state)
    rows = review_rows(state)
    rows[0]["EPA 2"] = 3.25
    updated = approve(state, rows)
    cfg = appd.config_for(updated)
    saved = wf.ready_review(updated, cfg)
    assert saved["rows"][0]["EPA 2"] == 3.25
    assert saved["overrides"][0]["Calculated average"] == 3
    assert updated["evaluations"] == before["evaluations"]
    assert updated["forms"] == before["forms"]
    assert updated["settings"]["appd_question_maps"] == before["settings"]["appd_question_maps"]
    assert state == before


def test_draft_save_revokes_prior_approval():
    state = approve(configured())
    cfg = appd.config_for(state)
    rows = review_rows(state)
    rows[0]["EPA 2"] = 3.1
    changed = wf.save_numeric_review(state, cfg, rows, appd.input_fingerprint(state, cfg), approved=False)
    assert wf.ready_review(changed, cfg) is None
    with pytest.raises(ValueError):
        appd.workbook_bytes(changed, cfg)


def test_numeric_draft_can_be_saved_before_conversion_is_set():
    state = make_state()
    cfg = appd.config_for(state)
    state = wf.save_numeric_review(state, cfg, review_rows(state), appd.input_fingerprint(state, cfg), approved=False)
    assert wf.saved_review(state, cfg)["rows"][0]["EPA 2"] == 3
    assert wf.ready_review(state, cfg) is None


def test_concurrent_numeric_review_cannot_be_overwritten_unnoticed():
    state = approve(configured())
    cfg = appd.config_for(state)
    with pytest.raises(ValueError, match="Another user"):
        wf.save_numeric_review(state, cfg, review_rows(state), appd.input_fingerprint(state, cfg),
                               approved=True, reviewer="OTHER", expected_saved=appd.digest(None))


def test_missing_or_injected_resident_row_rejected():
    state = configured()
    rows = review_rows(state)
    for altered in [[], rows + rows, [dict(rows[0], resident_key="invented")]]:
        with pytest.raises(ValueError, match="resident rows"):
            wf.save_numeric_review(state, appd.config_for(state), altered,
                                   appd.input_fingerprint(state, appd.config_for(state)), approved=False)


@pytest.mark.parametrize("change", ["date", "score", "roster", "rule", "mapping"])
def test_saved_approval_expires_when_inputs_change(change):
    state = approve(configured())
    if change == "date": state["settings"]["appd"]["end_date"] = "2026-11-04"
    elif change == "score": next(iter(state["evaluations"].values()))["responses"]["101"]["response"] = "5"
    elif change == "roster": state["settings"]["appd_roster"][KEY]["residency_start_date"] = "2025-07-01"
    elif change == "rule": state["settings"]["appd"]["score_bands"][0]["upper"] = 1.1
    else: next(iter(state["settings"]["appd_question_maps"].values()))["questions"]["101"] = ["EPA 1", "EPA 2"]
    assert wf.saved_review(state, appd.config_for(state)) is None
    assert wf.ready_review(state, appd.config_for(state)) is None


def test_unsaved_editor_change_disables_existing_download():
    state = approve(configured())
    rows = review_rows(state)
    rows[0]["EPA 2"] = 3.2
    assert wf.ready_review(state, appd.config_for(state), rows) is None


class Store:
    def __init__(self, state): self.state = state
    def load(self): return self.state, "sha"


def test_download_rechecks_current_archive():
    state = approve(configured())
    cfg = appd.config_for(state)
    sig = appd.input_fingerprint(state, cfg)
    snapshot = appd.digest(wf.ready_review(state, cfg))
    assert wf.download_bytes(Store(state), sig, snapshot).startswith(b"PK")
    state["settings"]["appd"]["end_date"] = "2026-11-04"
    with pytest.raises(ValueError, match="changed"):
        wf.download_bytes(Store(state), sig, snapshot)


def test_excel_upload_is_template_shaped_with_no_raw_decimal_scores():
    state = approve(configured())
    cfg = appd.config_for(state)
    workbook = appd.workbook_bytes(state, cfg)
    with zipfile.ZipFile(io.BytesIO(workbook)) as archive:
        ns = {"s": appd.SHEET_NS}
        root = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        data = root.find("s:sheetData", ns)
        assert [int(row.get("r")) for row in data] == [1, 2, 3]
        row = data[-1]
        values = {}
        for cell in row:
            node = cell.find("s:is/s:t", ns) if cell.get("t") == "inlineStr" else cell.find("s:v", ns)
            values[cell.get("r")] = node.text
        assert values["B3"] == "07/31/2026"
        assert values["C3"] == "EXAMPLE-001"
        assert values["D3"] == "1"
        assert values["F3"] == "GENERAL"
        assert values["H3"] == "3a"
        assert all(values[f"{chr(71+i)}3"] in appd.APPD_CODES for i in range(12))
        assert not root.findall(".//s:f", ns)
        book = ET.fromstring(archive.read("xl/workbook.xml"))
        assert len(book.find("s:sheets", ns)) == 1
