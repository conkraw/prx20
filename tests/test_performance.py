"""Read-path caching and calculation regressions. All residents are fictional."""
import copy
from unittest.mock import patch

import pandas as pd
import pytest

import epa_appd as appd
import epa_core as core
import epa_performance as perf
import epa_workflow as workflow
from test_appd import make_state
from test_numeric_workflow import configured, approve, review_rows


def test_repeat_request_runs_factory_only_once_and_returns_copies():
    state, session, calls = make_state(), {}, []
    def work():
        calls.append(1)
        return {"rows": [{"score": 3.36}]}
    first = perf.cached_result(session, state, "test", {"year": 2026}, work)
    first["rows"][0]["score"] = 99
    second = perf.cached_result(session, state, "test", {"year": 2026}, work)
    assert second == {"rows": [{"score": 3.36}]}
    assert calls == [1]


def test_dataframes_are_defensively_copied():
    state, session = make_state(), {}
    factory = lambda: pd.DataFrame({"average": [3.36]})
    first = perf.cached_result(session, state, "frame", None, factory)
    first.loc[0, "average"] = 99
    assert perf.cached_result(session, state, "frame", None, factory).iloc[0]["average"] == 3.36


def test_new_snapshot_invalidates_even_when_timestamp_is_identical():
    state, session = make_state(), {}
    factory = lambda: core.epa_summary(core.score_rows(state)).iloc[0]["average"]
    assert perf.cached_result(session, state, "scores", None, factory) == 3
    state = copy.deepcopy(state)
    next(iter(state["evaluations"].values()))["responses"]["101"]["response"] = "5"
    assert perf.cached_result(session, state, "scores", None, factory) == 3.5


def test_explicit_invalidation_covers_intentional_in_place_edits():
    state, session = make_state(), {}
    factory = lambda: state["settings"].get("local_test_value", 1)
    assert perf.cached_result(session, state, "test", None, factory) == 1
    state["settings"]["local_test_value"] = 2
    perf.invalidate(session)
    assert perf.cached_result(session, state, "test", None, factory) == 2


def test_revision_timestamp_change_refreshes_results():
    state, session, calls = make_state(), {}, []
    def work():
        calls.append(1)
        return len(calls)
    perf.cached_result(session, state, "test", None, work)
    state["updated_at"] = "2099-01-01T00:00:00Z"
    assert perf.cached_result(session, state, "test", None, work) == 2


def test_distinct_users_do_not_share_cached_values():
    state, first, second, calls = make_state(), {}, {}, []
    def work():
        calls.append(1)
        return len(calls)
    assert perf.cached_result(first, state, "test", None, work) == 1
    assert perf.cached_result(second, state, "test", None, work) == 2
    assert first[perf.CACHE_KEY] is not second[perf.CACHE_KEY]


def test_filters_are_part_of_the_cache_key():
    state, session = make_state(), {}
    assert perf.cached_result(session, state, "test", {"month": 7}, lambda: 7) == 7
    assert perf.cached_result(session, state, "test", {"month": 8}, lambda: 8) == 8
    assert perf.cached_result(session, state, "test", {"month": 7}, lambda: 999) == 7


def test_parameter_dictionary_order_is_irrelevant():
    state, session = make_state(), {}
    perf.cached_result(session, state, "test", {"a": 1, "b": 2}, lambda: 3)
    assert perf.cached_result(session, state, "test", {"b": 2, "a": 1}, lambda: 99) == 3


def test_reading_an_unprepared_download_does_not_compute_anything():
    state, session = make_state(), {}
    assert perf.cached_value(session, state, "workbook", {"month": 7}) is None
    data = perf.cached_result(session, state, "workbook", {"month": 7}, lambda: b"synthetic")
    assert perf.cached_value(session, state, "workbook", {"month": 7}) == data
    assert perf.cached_value(session, state, "workbook", {"month": 8}) is None
    assert perf.cached_value(session, copy.deepcopy(state), "workbook", {"month": 7}) is None


def test_cache_is_bounded_and_evicts_the_least_recent_item():
    state, session = make_state(), {}
    for i in range(perf.MAX_ENTRIES + 1):
        perf.cached_result(session, state, "test", i, lambda i=i: i)
    assert len(session[perf.CACHE_KEY]["items"]) == perf.MAX_ENTRIES
    assert perf.cached_value(session, state, "test", 0) is None
    assert perf.cached_value(session, state, "test", perf.MAX_ENTRIES) == perf.MAX_ENTRIES


def test_exceptions_are_not_cached():
    state, session = make_state(), {}
    def fail():
        raise ValueError("synthetic failure")
    with pytest.raises(ValueError):
        perf.cached_result(session, state, "test", None, fail)
    assert perf.cached_result(session, state, "test", None, lambda: 4) == 4


def test_cached_and_uncached_draft_have_identical_values():
    state, session = make_state(), {}
    original = appd.build_draft(state)
    cached = perf.cached_result(session, state, "draft", appd.config_for(state),
                                lambda: appd.build_draft(state))
    assert original == cached
    assert original == appd.build_draft(state, scored_rows=core.score_rows(state))


def test_precomputed_scored_rows_are_reused_without_rescoring():
    state = make_state()
    rows = core.score_rows(state)
    expected = appd.build_draft(state)
    with patch.object(appd, "score_rows", side_effect=AssertionError("should not rescore")):
        assert appd.build_draft(state, scored_rows=rows) == expected


def test_matching_each_question_set_once_preserves_same_result():
    state = make_state()
    sample = next(iter(state["evaluations"].values()))
    for i in range(20):
        record = copy.deepcopy(sample)
        record["evaluator"] = f"Fictional faculty {i}"
        record["evaluator_key"] = f"fictional faculty {i}"
        state["evaluations"][f"fictional-{i}"] = record
    with patch.object(appd, "match_form", wraps=appd.match_form) as match:
        draft = appd.build_draft(state)
    assert match.call_count == 1
    assert next(r for r in draft["summary"] if r["APPD EPA"] == "EPA 2")["Average"] == 3


def test_dashboard_subset_rows_still_mean_selected_evaluations_not_dropped_questions():
    # Preserve the prior public API: rows choose evaluations. Only a separate
    # explicitly supplied complete scoring table can bypass rescoring.
    state = make_state()
    rows = core.score_rows(state)
    only_first_question = rows.loc[rows["question_id"].eq("101")]
    result = appd.dashboard_mapped_draft(state, only_first_question)
    assert next(r for r in result["summary"] if r["APPD EPA"] == "EPA 2")["Average"] == 3


def test_ready_review_reuses_matching_draft_but_rejects_stale_draft():
    state = approve(configured())
    cfg = appd.config_for(state)
    draft = appd.build_draft(state, cfg)
    expected = workflow.ready_review(state, cfg)
    with patch.object(appd, "build_draft", side_effect=AssertionError("duplicate draft")):
        assert workflow.ready_review(state, cfg, draft=draft) == expected
    stale = copy.deepcopy(draft)
    stale["fingerprint"] = "outdated"
    assert workflow.ready_review(state, cfg, draft=stale) is None


def test_new_mapping_and_roster_changes_do_not_reuse_old_results():
    state, session = make_state(), {}
    before = perf.cached_result(session, state, "draft", None, lambda: appd.build_draft(state))
    fid = next(iter(state["forms"]))
    state = appd.save_mapping(state, fid, {"101": ["EPA 2"]}, True)
    state["settings"]["appd_roster"]["example, resident"]["residency_start_date"] = "2026-07-01"
    after = perf.cached_result(session, state, "draft", None, lambda: appd.build_draft(state))
    assert next(r for r in before["summary"] if r["APPD EPA"] == "EPA 2")["Average"] == 3
    assert next(r for r in after["summary"] if r["APPD EPA"] == "EPA 2")["Average"] == 4
    assert after["rows"][0]["pgy"] == 1


def test_memory_budget_skips_an_oversized_result_without_losing_small_cache():
    state, session, calls = make_state(), {}, []
    with patch.object(perf, "MAX_BYTES", 500):
        perf.cached_result(session, state, "small", None, lambda: b"small")
        def large():
            calls.append(1)
            return b"x" * 1000
        assert len(perf.cached_result(session, state, "large", None, large)) == 1000
        perf.cached_result(session, state, "large", None, large)
        assert calls == [1, 1]
        assert perf.cached_value(session, state, "small", None) == b"small"
        assert session[perf.CACHE_KEY]["bytes"] <= 500


def test_total_memory_budget_evicts_old_entries():
    state, session = make_state(), {}
    with patch.object(perf, "MAX_BYTES", 500):
        for i in range(12):
            perf.cached_result(session, state, "test", i, lambda: b"x" * 150)
        assert session[perf.CACHE_KEY]["bytes"] <= 500
        assert len(session[perf.CACHE_KEY]["items"]) < 12
