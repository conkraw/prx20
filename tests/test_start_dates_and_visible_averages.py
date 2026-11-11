"""Synthetic regression tests for historical PGY and visible EPA calculations."""
import copy
from datetime import date, datetime
import io
import json

import pandas as pd
import pytest
from cryptography.fernet import Fernet
from openpyxl import load_workbook

import epa_appd as appd
import epa_core as core
import epa_pgy as pgy
from test_appd import make_state


KEY = "example, resident"


def with_start(value="2026-07-01"):
    state = make_state()
    state["settings"]["appd_roster"][KEY]["residency_start_date"] = value
    return state


@pytest.mark.parametrize("assessed,expected", [
    ("2026-07-01", 1), ("2026-08-15", 1), ("2027-06-30", 1),
    ("2027-07-01", 2), ("2027-10-15", 2), ("2028-06-30", 2),
    ("2028-07-01", 3), ("2029-07-01", 4), ("2030-06-30", 4),
])
def test_july_promotions(assessed, expected):
    value, source = appd.resolve_pgy(with_start(), KEY, appd.as_date(assessed))
    assert value == expected
    assert "Calculated from July 1, 2026" in source


@pytest.mark.parametrize("value", [None, "", float("nan"), pd.NaT, pd.NA])
def test_missing_date_scalars_do_not_raise(value):
    assert appd.as_date(value) is None
    assert pgy.pgy_from_start("2026-07-01", value)[0] is None


@pytest.mark.parametrize("value", ["2026-07-02", "2026-06-01", "not a date"])
def test_invalid_start_dates_block_instead_of_using_old_manual_pgy(value):
    state = with_start(value)
    assert appd.resolve_pgy(state, KEY, date(2026, 10, 1))[0] is None
    with pytest.raises(ValueError):
        appd.save_roster(state, {KEY: {"export_id": "EXAMPLE-001", "residency_start_date": value}},
                         appd.digest(state["settings"]["appd_roster"]))


def test_before_start_blank_not_pgy_zero_or_manual_fallback():
    value, reason = appd.resolve_pgy(with_start(), KEY, date(2026, 6, 30))
    assert value is None
    assert "precedes" in reason


def test_start_overrides_old_year_and_ignores_dashboard_year_start():
    state = with_start()
    state["settings"]["academic_year_start_month"] = 1
    state["settings"]["appd_roster"][KEY]["pgy_by_academic_year"]["2027"] = 8
    assert appd.resolve_pgy(state, KEY, date(2027, 6, 30))[0] == 1
    assert appd.resolve_pgy(state, KEY, date(2027, 7, 1))[0] == 2


def test_old_manual_fallback_is_unchanged_without_start_date():
    state = make_state()
    assert appd.resolve_pgy(state, KEY, date(2026, 10, 1))[0] == 3


def test_missing_pgy_does_not_block_average():
    state = make_state()
    state["settings"]["appd_roster"] = {}
    draft = appd.build_draft(state)
    metric = next(x for x in draft["summary"] if x["APPD EPA"] == "EPA 2")
    assert draft["rows"][0]["pgy"] is None
    assert metric["Average"] == 3
    assert metric["Calculation status"] == "Calculated"


def test_save_preserves_evaluations_mappings_manual_history_and_other_residents():
    state = make_state()
    state["settings"]["appd_roster"]["other"] = {"resident": "Other", "residency_start_date": "2025-07-01", "extra": "retained"}
    before = copy.deepcopy(state)
    updated = appd.save_roster(state, {KEY: {"export_id": "0007", "residency_start_date": date(2026, 7, 1)}},
                              appd.digest(state["settings"]["appd_roster"]))
    assert state == before
    assert updated["evaluations"] == before["evaluations"]
    assert updated["forms"] == before["forms"]
    assert updated["settings"]["appd_question_maps"] == before["settings"]["appd_question_maps"]
    assert updated["settings"]["appd_roster"]["other"] == before["settings"]["appd_roster"]["other"]
    assert updated["settings"]["appd_roster"][KEY]["pgy_by_academic_year"] == {"2026": 3}
    assert updated["settings"]["appd_roster"][KEY]["export_id"] == "0007"
    cipher = Fernet(Fernet.generate_key())
    roundtrip = json.loads(cipher.decrypt(cipher.encrypt(json.dumps(updated).encode())))
    assert appd.resolve_pgy(roundtrip, KEY, date(2027, 10, 1))[0] == 2


def test_conflicting_roster_edits_do_not_overwrite():
    state = with_start()
    with pytest.raises(ValueError, match="changed in another session"):
        appd.save_roster(state, {KEY: {"export_id": "ID", "residency_start_date": "2025-07-01"}}, "old hash")


def test_clearing_a_start_restores_legacy_fallback():
    state = with_start()
    updated = appd.save_roster(state, {KEY: {"export_id": "ID", "residency_start_date": pd.NaT}},
                              appd.digest(state["settings"]["appd_roster"]))
    assert appd.resolve_pgy(updated, KEY, date(2026, 10, 1))[0] == 3


def test_editing_review_date_recalculates_before_approval_and_excel_export():
    state = with_start()
    cfg = appd.config_for(state)
    draft = appd.build_draft(state)
    rows = copy.deepcopy(draft["rows"])
    rows[0].update(assessmentdate="2027-10-01", pgy=9)
    rows[0].update({epa: "0" for epa in appd.EPA_COLUMNS})
    rows[0]["EPA 2"] = "3a"
    saved = appd.save_review(state, cfg, rows, draft["fingerprint"], approved=True, reviewer="TEST")
    approved = appd.approved_review(saved, cfg)
    assert approved["rows"][0]["pgy"] == 2
    workbook = load_workbook(io.BytesIO(appd.workbook_bytes(saved, cfg)))
    assert workbook.active["D3"].value == 2
    assert workbook.active["B3"].value == "10/01/2027"
    assert workbook.active["H3"].value == "3a"


def test_invalid_edited_assessment_cannot_be_approved_with_stale_pgy():
    state = with_start()
    draft = appd.build_draft(state)
    rows = copy.deepcopy(draft["rows"])
    rows[0].update(assessmentdate="2025-10-01", pgy=3)
    rows[0].update({epa: "0" for epa in appd.EPA_COLUMNS})
    with pytest.raises(ValueError, match="PGY"):
        appd.save_review(state, appd.config_for(state), rows, draft["fingerprint"], approved=True, reviewer="TEST")


def test_start_date_change_invalidates_old_approval():
    state = with_start()
    draft = appd.build_draft(state)
    rows = copy.deepcopy(draft["rows"])
    rows[0].update({epa: "0" for epa in appd.EPA_COLUMNS})
    approved = appd.save_review(state, appd.config_for(state), rows, draft["fingerprint"], approved=True, reviewer="TEST")
    approved["settings"]["appd_roster"][KEY]["residency_start_date"] = "2025-07-01"
    assert appd.approved_review(approved, appd.config_for(approved)) is None


def test_latest_fixed_and_individual_dates_have_distinct_correct_pgys():
    state = with_start()
    state["settings"]["appd"].update(assessment_date_mode="fixed", fixed_assessment_date="2027-10-01")
    draft = appd.build_draft(state)
    assert draft["rows"][0]["pgy"] == 2
    assert draft["evaluation_scores"][0]["PGY at assessment"] == "PGY1"
    assert draft["summary"][0]["PGY"] == "PGY2"
    individual = appd.assessment_pgy_rows(state)
    assert individual[0]["PGY"] == "PGY1"
    assert individual[0]["Assessment date"] == "2026-07-31"


def test_no_completed_date_does_not_invent_an_individual_pgy():
    state = with_start()
    next(iter(state["evaluations"].values()))["completed_at"] = None
    assert appd.assessment_pgy_rows(state)[0]["PGY"] == ""
    assert "assessment date" in appd.assessment_pgy_rows(state)[0]["PGY source / issue"]


def test_dashboard_mapping_uses_selected_ids_not_separate_appd_date_window():
    state = with_start()
    state["settings"]["appd"].update(start_date="2025-07-01", end_date="2025-12-31")
    assert appd.build_draft(state)["rows"] == []
    selected = core.filter_rows(core.score_rows(state), academic_year=2026, months=[7])
    draft = appd.dashboard_mapped_draft(state, selected)
    metric = next(x for x in draft["summary"] if x["APPD EPA"] == "EPA 2")
    assert metric["Average"] == 3
    assert draft["rows"][0]["pgy"] == 1
    assert state["settings"]["appd"]["start_date"] == "2025-07-01"


def test_multi_epa_and_equal_evaluation_averaging_remain_correct():
    state = with_start()
    fid = next(iter(state["forms"]))
    state = appd.save_mapping(state, fid, {"101": ["EPA 1", "EPA 2"], "102": ["EPA 1", "EPA 2"]}, True)
    r = next(iter(state["evaluations"].values()))
    r["responses"]["101"]["response"] = "3"
    r["responses"]["102"]["response"] = "4"
    second = copy.deepcopy(r)
    second.update(evaluator="Other Faculty", evaluator_key="other faculty", completed_at="2026-08-15T10:00:00")
    second["responses"]["101"]["response"] = "3"
    second["responses"]["102"]["response"] = ""
    state, _ = core.ingest_evaluations(state, [second])
    draft = appd.build_draft(state)
    for epa in ("EPA 1", "EPA 2"):
        metric = next(x for x in draft["summary"] if x["APPD EPA"] == epa)
        assert metric["Average"] == 3.25
        assert "Local level" not in metric
        assert metric["Evaluations"] == 2
    assert all(draft["rows"][0][epa] == "" for epa in appd.EPA_COLUMNS)


def test_unconfirmed_scoring_has_actionable_blank_reason():
    state = with_start()
    next(iter(state["forms"].values()))["scoring_confirmed"] = False
    draft = appd.build_draft(state)
    metric = next(x for x in draft["summary"] if x["APPD EPA"] == "EPA 2")
    assert metric["Average"] is None
    assert "scoring review" in metric["Calculation status"]
    assert "confirm" in draft["form_diagnostics"][0]["Action"]
    diagnostic = appd.scoring_diagnostics(state, core.score_rows(state))[0]
    assert diagnostic["Needs scoring review"] == 2
    assert "scoring scale" in diagnostic["Action"]


def test_unconfirmed_mapping_has_actionable_blank_reason():
    state = with_start()
    fid = next(iter(state["forms"]))
    state["settings"]["appd_question_maps"][fid]["confirmed"] = False
    draft = appd.build_draft(state)
    assert all(x["Average"] is None for x in draft["summary"])
    assert "mapping" in draft["summary"][0]["Calculation status"]
    assert not draft["form_diagnostics"][0]["Mapping ready"]


def test_zero_and_excluded_are_not_mistaken_for_missing():
    state = with_start()
    form = next(iter(state["forms"].values()))
    form["score_map"]["0"] = 0.0
    record = next(iter(state["evaluations"].values()))
    record["responses"]["101"]["response"] = "0"
    record["responses"]["102"]["response"] = "6"
    metric = next(x for x in appd.build_draft(state)["summary"] if x["APPD EPA"] == "EPA 2")
    assert metric["Average"] == 0
    assert "Local level" not in metric
    assert metric["Calculation status"] == "Calculated"


def test_summary_shows_both_pgys_across_july_boundary():
    state = with_start()
    record = copy.deepcopy(next(iter(state["evaluations"].values())))
    record.update(evaluator="Other Faculty", evaluator_key="other faculty", completed_at="2027-07-01T10:00:00", session_date="2027-07-01")
    state, _ = core.ingest_evaluations(state, [record])
    source = core.filter_rows(core.score_rows(state), date_basis="completed_at")
    report = appd.annotate_summary_pgys(state, core.epa_summary(source), source)
    assert report.iloc[0]["assessment_pgys"] == "PGY1 / PGY2"
    monthly = appd.annotate_summary_pgys(state, core.monthly_summary(source), source)
    assert list(monthly["assessment_pgys"]) == ["PGY1", "PGY2"]


def test_summary_does_not_disguise_missing_pgy():
    state = make_state()
    state["settings"]["appd_roster"] = {}
    source = core.score_rows(state)
    report = appd.annotate_summary_pgys(state, core.epa_summary(source), source)
    assert report.iloc[0]["assessment_pgys"] == "PGY not set"
