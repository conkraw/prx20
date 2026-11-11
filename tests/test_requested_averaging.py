"""November 2026 requests, using invented residents, scores and faculty only."""
import copy
import math

import pytest

import epa_appd as appd
import epa_core as core
from test_appd import make_state, approved_state


def add_evaluation(state, answers=("5", ""), evaluator="Faculty, Second", session="2026-08-01"):
    record = copy.deepcopy(next(iter(state["evaluations"].values())))
    record.update(evaluator=evaluator, evaluator_key=core.normalize_key(evaluator),
                  session_date=session, completed_at=session + "T10:00:00")
    for qid, answer in zip(("101", "102"), answers):
        record["responses"][qid]["response"] = answer
    result, stats = core.ingest_evaluations(state, [record])
    assert stats["added"] == 1
    assert not result["conflicts"]
    return result


def metric(draft, epa):
    return next(row for row in draft["summary"] if row["APPD EPA"] == epa)


@pytest.mark.parametrize("value, expected", [
    (1, "1"), (2, "2"), (3, "3"), (4, "4"), (5, "5"),
    (3.01, "3b"), (3.25, "3b"), (3.4999, "3b"), (3.5, "3b"),
    (3.500001, "3a"), (3.75, "3a"), (3.9999, "3a"),
    (2.25, "2b"), (2.75, "2a"), (4.5, "4b"), (4.75, "4a"),
    (2.9999999999999996, "3"), (3.0000000000000004, "3"),
    (None, ""), (float("nan"), ""), (float("inf"), ""),
    (-1, ""), ("invalid", ""), (True, ""),
])
def test_requested_display_boundaries(value, expected):
    assert appd.local_display_level(value) == expected


def test_display_order_and_cutoff_are_saved_program_choices():
    cfg = dict(appd.defaults(), local_fraction_order="a_then_b", local_fraction_cutoff=0.25)
    assert appd.local_display_level(3.25, cfg) == "3a"
    assert appd.local_display_level(3.26, cfg) == "3b"
    assert appd.local_display_level(3, cfg) == "3"
    appd.validate_config(cfg)


@pytest.mark.parametrize("cutoff", [0, 1, -1, float("nan"), float("inf"), "bad"])
def test_obsolete_local_fraction_cutoff_no_longer_blocks_numeric_reports(cutoff):
    cfg = dict(appd.defaults(), local_fraction_cutoff=cutoff)
    appd.validate_config(cfg)


def test_multiple_epas_are_independent_and_evaluations_are_equal_weight():
    state = add_evaluation(make_state())
    fid = next(iter(state["forms"]))
    state = appd.save_mapping(state, fid, {
        "101": ["EPA 1", "EPA 2", "EPA 2"],
        "102": ["EPA 2", "EPA 3"],
    }, True)
    draft = appd.build_draft(state)
    assert metric(draft, "EPA 1")["Average"] == 4.5  # means 4 and 5
    assert metric(draft, "EPA 2")["Average"] == 4.0  # means (4+2)/2=3 and 5
    assert metric(draft, "EPA 3")["Average"] == 2.0  # only one scored evaluation
    assert metric(draft, "EPA 2")["Ratings"] == 3
    assert metric(draft, "EPA 2")["Evaluations"] == 2
    assert core.epa_summary(core.score_rows(state)).iloc[0]["average"] == 4.0
    scores = [row for row in draft["evaluation_scores"] if row["APPD EPA"] == "EPA 2"]
    assert sorted(row["Evaluation average"] for row in scores) == [3.0, 5.0]
    assert sorted(row["Scored questions"] for row in scores) == [1, 2]
    assert len({row["Evaluator"] for row in scores}) == 2
    assert all(row["Mapped questions"] == 2 for row in scores)
    assert appd.mapping_for(state, fid)["questions"]["101"] == ["EPA 1", "EPA 2"]


def test_empty_evaluation_is_not_zero_or_an_extra_denominator():
    state = add_evaluation(make_state(), answers=("6", ""))
    draft = appd.build_draft(state)
    assert metric(draft, "EPA 2")["Average"] == 3
    assert metric(draft, "EPA 2")["Evaluations"] == 1
    assert "Local level" not in metric(draft, "EPA 2")
    summary = core.epa_summary(core.score_rows(state)).iloc[0]
    assert summary["n_evaluations"] == 2
    assert summary["n_scored_evaluations"] == 1
    assert summary["average"] == 3


def test_deduplicated_upload_cannot_change_the_average():
    state = add_evaluation(make_state())
    before = appd.build_draft(state)
    state, stats = core.ingest_evaluations(state, list(state["evaluations"].values()))
    assert stats["duplicates"] == 2
    assert metric(appd.build_draft(state), "EPA 2") == metric(before, "EPA 2")


def test_each_distinct_evaluation_counts_even_for_same_preceptor():
    state = add_evaluation(make_state(), evaluator="Faculty, Example")
    assert metric(appd.build_draft(state), "EPA 2")["Average"] == 4
    assert metric(appd.build_draft(state), "EPA 2")["Evaluations"] == 2


def test_numeric_average_is_not_replaced_by_a_local_label():
    state = make_state()
    record = next(iter(state["evaluations"].values()))
    record["responses"]["101"]["response"] = "3"
    record["responses"]["102"]["response"] = "4"
    state = add_evaluation(state, answers=("3", "3"))
    row = metric(appd.build_draft(state), "EPA 2")
    assert row["Average"] == 3.25
    assert "Local level" not in row
    assert row["Suggested level"] == ""  # not automatically sent to APPD


def test_cross_form_totals_weight_evaluations_not_forms():
    state = add_evaluation(make_state())  # form A has averages 3 and 5
    other = copy.deepcopy(next(iter(state["forms"].values())))
    other["name"] = "Other example form"
    for question in other["questions"]:
        question["id"] = str(int(question["id"]) + 100)
    other["id"] = core.form_id_for_questions(["201", "202"])
    state = core.register_form(state, other)
    record = copy.deepcopy(next(iter(state["evaluations"].values())))
    record["responses"] = {"201": {"response": "1"}, "202": {"response": "1"}}
    record["rotation"] = "Different example rotation"
    state, stats = core.ingest_evaluations(state, [record])
    assert stats["added"] == 1
    state = appd.save_mapping(state, other["id"], {"201": ["EPA 2"], "202": ["EPA 2"]}, True)
    draft = appd.build_draft(state)
    assert metric(draft, "EPA 2")["Average"] == 3  # (3+5+1)/3, not (4+1)/2
    assert sorted(row["Average"] for row in draft["form_summaries"]) == [1, 4]
    assert sorted(row["Evaluations"] for row in draft["form_summaries"]) == [1, 2]


def test_monthly_and_cumulative_use_individual_evaluations():
    state = add_evaluation(make_state(), answers=("5", ""))
    state = add_evaluation(state, answers=("2", ""), evaluator="Faculty, Third", session="2026-08-15")
    rows = core.filter_rows(core.score_rows(state), academic_year=2026)
    monthly = core.monthly_summary(rows).set_index("period")
    cumulative = core.monthly_summary(rows, cumulative=True).set_index("period")
    assert monthly.loc["2026-07", "average"] == 3
    assert monthly.loc["2026-08", "average"] == 3.5
    assert cumulative.loc["2026-08", "average"] == pytest.approx(10 / 3)
    assert metric(appd.build_draft(state), "EPA 2")["Average"] == pytest.approx(10 / 3)


def test_whole_number_local_labels_cannot_be_exported_as_appd_categories():
    state = make_state()
    draft = appd.build_draft(state)
    assert "Local level" not in metric(draft, "EPA 2")
    assert draft["rows"][0]["EPA 2"] == ""
    rows = copy.deepcopy(draft["rows"])
    rows[0].update({epa: "0" for epa in appd.EPA_COLUMNS})
    rows[0]["EPA 2"] = "3"
    with pytest.raises(ValueError, match="choose a final level"):
        appd.save_review(state, draft["config"], rows, draft["fingerprint"], approved=True, reviewer="TEST")


def test_program_appd_thresholds_are_preserved_and_independent_of_local_labels():
    state = make_state()
    state["settings"]["appd"].update(conversion_enabled=True, conversion_confirmed=True,
        score_bands=[{"upper": 3, "code": "3a"}, {"upper": 5, "code": "4"}])
    before = copy.deepcopy(state)
    row = metric(appd.build_draft(state), "EPA 2")
    assert row["Average"] == 3
    assert "Local level" not in row
    assert row["Suggested level"] == "3a"
    assert state == before


def test_legacy_mapping_and_settings_read_without_mutating_the_archive():
    state = make_state()
    fid = next(iter(state["forms"]))
    state["settings"]["appd"].pop("local_fraction_order", None)
    state["settings"]["appd"].pop("local_fraction_cutoff", None)
    state["settings"]["appd"]["aggregation"] = "pooled_ratings"
    state["settings"]["appd_question_maps"][fid]["questions"]["101"] = "EPA 2"
    state["settings"]["appd_question_maps"][fid]["questions"]["102"] = ["EPA 2", "EPA 2"]
    before = copy.deepcopy(state)
    draft = appd.build_draft(state)
    assert metric(draft, "EPA 2")["Average"] == 3
    assert metric(draft, "EPA 2")["Ratings"] == 2
    assert "Local level" not in metric(draft, "EPA 2")
    assert draft["config"]["aggregation"] == "equal_evaluations"
    assert state == before


def test_calculation_version_invalidates_old_approvals():
    state = approved_state()
    cfg = appd.config_for(state)
    settings = state["settings"]
    previous_version_fingerprint = appd.digest({
        "config": cfg, "forms": state.get("forms", {}),
        "evaluations": state.get("evaluations", {}), "conflicts": state.get("conflicts", {}),
        "maps": settings.get("appd_question_maps", {}), "roster": settings.get("appd_roster", {}),
        "academic_year_start_month": settings.get("academic_year_start_month", 7),
    })
    saved = settings["appd_reviews"][appd.review_key(cfg)]
    saved["input_fingerprint"] = previous_version_fingerprint
    assert appd.approved_review(state, cfg) is None
    assert saved["rows"]  # history is preserved, not deleted


def test_mapping_changes_never_change_responses_or_the_form_score_map():
    state = make_state()
    fid = next(iter(state["forms"]))
    changed = appd.save_mapping(state, fid, {"101": ["EPA 1", "EPA 2", "EPA 12"], "102": ["EPA 3"]}, True)
    assert changed["evaluations"] == state["evaluations"]
    assert changed["forms"] == state["forms"]
    assert changed["settings"]["appd_roster"] == state["settings"]["appd_roster"]
