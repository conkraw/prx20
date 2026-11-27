"""Automatic defaults and remembered links, using invented residents only."""
from __future__ import annotations

import copy

import pytest

from epa_appd import digest
from epa_competency_import import parse_competency_pdf
from epa_competency_reports import (
    LINKS_NAMESPACE, NAMESPACE, choose_resident_link, linked_report,
    resident_link_expectations, save_report_links, suggested_resident,
)
from test_competency_import import pdf_bytes, report_text


A = "example, taylor jordan"
ANAME = "Example, Taylor Jordan"
B = "other, casey"
BNAME = "Other, Casey"
SOURCE = "Taylor Example"


def state_with(*residents):
    return {
        "evaluations": {}, "conflicts": {}, "forms": {},
        "settings": {"appd_roster": {key: {"resident": name} for key, name in residents}},
    }


def entry(source=SOURCE, target=A, name=ANAME, *, variant=0):
    text = report_text().replace("Taylor Example", source).replace("Taylor\nExample", source)
    data = pdf_bytes(text, extra_pages=variant)
    parsed = parse_competency_pdf(data, "invented_report.pdf")
    return linked_report(parsed, data, resident_key=target, resident_name=name,
                         start_date="2026-07-01", end_date="2026-11-27", date_basis="session_date")


def save(state, incoming, *, expected_links=None, when="2026-11-27T10:00:00+00:00"):
    incoming = incoming if isinstance(incoming, list) else [incoming]
    expected = {item["id"]: digest(state.get(NAMESPACE, {}).get(item["id"])) for item in incoming}
    return save_report_links(state, incoming, expected, expected_links=expected_links, saved_at=when)[0]


def report_link(source, key, name, **fields):
    return {"resident": source, "resident_key": key, "linked_resident": name, **fields}


def test_omitted_middle_name_chooses_unique_resident_without_changing_exact_helper():
    state = state_with((A, ANAME))
    before = copy.deepcopy(state)
    choice = choose_resident_link(state, SOURCE)
    assert choice["resident_key"] == A
    assert choice["method"] == "compatible_name"
    assert choice["resident"] == ANAME
    assert suggested_resident(state, SOURCE) is None
    assert state == before
    assert LINKS_NAMESPACE not in state


@pytest.mark.parametrize("source,target", [
    ("Taylor J. Example", "Example, Taylor Jordan"),
    ("Taylor Jordan Example", "Example, Taylor J."),
    ("Taylor Example", "Example, Taylor J."),
    ("Taylor Jordan Example", "Example, Taylor Jordan Lee"),
    ("Taylor Lee Example", "Example, Taylor Jordan Lee"),
])
def test_matching_middle_initials_and_omitted_middle_names_are_confident(source, target):
    state = state_with((A, target))
    choice = choose_resident_link(state, source)
    assert choice["resident_key"] == A
    assert choice["method"] == "compatible_name"


@pytest.mark.parametrize("source,target", [
    ("Taylor James Example", ANAME),
    ("Taylor J. Example", "Example, Taylor Alex"),
    ("Taylor Jordan Lee Example", "Example, Taylor Jordan Quinn"),
    ("T. Example", ANAME),
    ("T Example", ANAME),
    ("Tayler Example", ANAME),
    ("Taylor Exampleton", ANAME),
    ("Taylor Example", "Example, Taylor Jr."),
])
def test_conflicting_middle_names_initial_first_names_and_typos_are_not_confident(source, target):
    choice = choose_resident_link(state_with((A, target)), source)
    assert choice["resident_key"] is None
    assert choice["method"] == "none"


def test_explicit_roster_alias_can_confirm_otherwise_conflicting_name():
    state = state_with((A, ANAME))
    state["settings"]["appd_roster"][A]["export_id"] = "Taylor James Example"
    choice = choose_resident_link(state, "Taylor James Example")
    assert choice["resident_key"] == A
    assert choice["method"] == "exact"


def test_multiple_compatible_names_never_choose_by_order():
    rows = [(A, ANAME), ("example, taylor alex", "Example, Taylor Alex")]
    for ordered in [rows, list(reversed(rows))]:
        choice = choose_resident_link(state_with(*ordered), SOURCE)
        assert choice["resident_key"] is None
        assert choice["method"] == "ambiguous"
        assert len(choice["candidates"]) == 2


def add_review_alias(state, key, alias, *, approved=True, include=True, period="period"):
    state["settings"].setdefault("appd_numeric_reviews", {})[period] = {
        "approved": approved,
        "rows": [{"resident_key": key, "Include": include, "name_or_program_assigned_ID": alias}],
    }


def test_unique_approved_export_alias_is_selected_after_exact_names():
    state = state_with((A, ANAME))
    add_review_alias(state, A, "EXPORT-001")
    choice = choose_resident_link(state, "EXPORT-001")
    assert choice["resident_key"] == A
    assert choice["method"] == "approved_alias"
    assert suggested_resident(state, "EXPORT-001") is None


def test_unapproved_and_excluded_export_aliases_are_ignored():
    state = state_with((A, ANAME))
    add_review_alias(state, A, "EXPORT-001", approved=False)
    add_review_alias(state, A, "EXPORT-001", include=False, period="excluded")
    assert choose_resident_link(state, "EXPORT-001")["resident_key"] is None


def test_approved_alias_collision_remains_ambiguous():
    state = state_with((A, ANAME), (B, BNAME))
    add_review_alias(state, A, "EXPORT-001", period="old")
    add_review_alias(state, B, "EXPORT-001", period="new")
    choice = choose_resident_link(state, "EXPORT-001")
    assert choice["resident_key"] is None
    assert choice["method"] == "ambiguous"


def test_exact_name_has_priority_over_approved_alias_and_middle_name_match():
    key = "example, taylor"
    state = state_with((key, "Example, Taylor"), (A, ANAME), (B, BNAME))
    add_review_alias(state, B, SOURCE)
    choice = choose_resident_link(state, SOURCE)
    assert choice["resident_key"] == key
    assert choice["method"] == "exact"


def test_exact_name_tie_does_not_fall_through_to_single_export_alias():
    state = state_with(("first", "Taylor Example"), ("second", "Example, Taylor"))
    add_review_alias(state, "first", SOURCE)
    choice = choose_resident_link(state, SOURCE)
    assert choice["resident_key"] is None
    assert choice["method"] == "ambiguous"


def test_existing_saved_choice_has_priority_over_inferred_exact_name():
    state = state_with((A, "Example, Taylor"), (B, BNAME))
    report = report_link(SOURCE, B, BNAME)
    choice = choose_resident_link(state, SOURCE, existing_report=report)
    assert choice["resident_key"] == B
    assert choice["method"] == "existing_report"


def test_save_records_name_snapshots_and_keeps_appd_and_evaluation_state_unchanged():
    state = state_with((A, ANAME))
    state["evaluations"]["active"] = {"resident_key": A, "resident": ANAME, "responses": {}}
    state["settings"]["appd_reviews"] = {"old": {"approved": True, "rows": []}}
    before = copy.deepcopy(state)
    saved = save(state, entry(), expected_links=resident_link_expectations(state, [SOURCE]))
    memory = saved[LINKS_NAMESPACE]["taylor example"]
    assert memory == {
        "source_name": SOURCE, "source_name_key": "taylor example", "resident_key": A,
        "resident_name": ANAME, "resident_name_key": "taylor jordan example",
        "saved_at": "2026-11-27T10:00:00+00:00", "updated_at": "2026-11-27T10:00:00+00:00",
    }
    assert saved["settings"] == before["settings"]
    assert saved["evaluations"] == before["evaluations"]
    assert saved["conflicts"] == before["conflicts"]
    assert state == before


def test_remembered_manual_choice_has_priority_over_existing_report_and_inference():
    state = state_with((A, ANAME), (B, BNAME))
    saved = save(state, entry(target=B, name=BNAME))
    report = report_link(SOURCE, A, ANAME)
    before = copy.deepcopy(saved)
    choice = choose_resident_link(saved, SOURCE, existing_report=report)
    assert choice["resident_key"] == B
    assert choice["method"] == "remembered"
    assert saved == before


def test_case_spacing_and_comma_order_changes_keep_memory_valid():
    saved = save(state_with((A, ANAME)), entry())
    saved["settings"]["appd_roster"][A]["resident"] = "  TAYLOR  JORDAN EXAMPLE "
    choice = choose_resident_link(saved, " EXAMPLE,  TAYLOR ")
    assert choice["resident_key"] == A
    assert choice["method"] == "remembered"
    assert choice["remembered_invalidated"] is False


def test_actual_target_name_change_invalidates_memory_and_old_report_snapshot():
    source = "Taylor Jordan Example"
    saved = save(state_with((A, ANAME)), entry(source=source))
    old = next(iter(saved[NAMESPACE].values()))
    saved["settings"]["appd_roster"][A]["resident"] = "Example, Taylor Alex"
    choice = choose_resident_link(saved, source, existing_report=old)
    assert choice["resident_key"] is None
    assert choice["method"] == "none"
    assert choice["remembered_invalidated"] is True


def test_source_name_change_does_not_reuse_old_alias_memory():
    saved = save(state_with((A, ANAME)), entry(source="Taylor Jordan Example"))
    choice = choose_resident_link(saved, "Taylor Alex Example")
    assert choice["resident_key"] is None
    assert choice["method"] == "none"


def test_missing_target_does_not_reuse_memory_or_previous_report():
    saved = save(state_with((A, ANAME)), entry())
    del saved["settings"]["appd_roster"][A]
    choice = choose_resident_link(saved, SOURCE)
    assert choice["resident_key"] is None
    assert choice["remembered_invalidated"] is True


def test_legacy_report_choice_is_reused_without_mutating_it_or_creating_memory():
    state = state_with((A, ANAME), (B, BNAME))
    state[NAMESPACE] = {"legacy": report_link(SOURCE, B, BNAME)}
    before = copy.deepcopy(state)
    choice = choose_resident_link(state, SOURCE)
    assert choice["resident_key"] == B
    assert choice["method"] == "previous_report"
    assert state == before
    assert LINKS_NAMESPACE not in state


def test_latest_consistent_legacy_choice_wins_when_timestamps_are_known():
    state = state_with((A, ANAME), (B, BNAME))
    state[NAMESPACE] = {
        "older": report_link(SOURCE, A, ANAME, updated_at="2026-11-26T12:00:00+00:00"),
        "newer": report_link(SOURCE, B, BNAME, updated_at="2026-11-27T12:00:00+00:00"),
        "same_latest": report_link(SOURCE, B, BNAME, updated_at="2026-11-27T07:00:00-05:00"),
    }
    choice = choose_resident_link(state, SOURCE)
    assert choice["resident_key"] == B
    assert choice["method"] == "previous_report"


@pytest.mark.parametrize("time1,time2", [(None, None), (None, "2026-11-27T12:00:00Z"),
                                        ("2026-11-27T12:00:00Z", "2026-11-27T12:00:00+00:00")])
def test_conflicting_legacy_links_without_a_unique_latest_target_remain_ambiguous(time1, time2):
    state = state_with((A, "Example, Taylor"), (B, BNAME))
    state[NAMESPACE] = {
        "first": report_link(SOURCE, A, "Example, Taylor", updated_at=time1),
        "second": report_link(SOURCE, B, BNAME, updated_at=time2),
    }
    choice = choose_resident_link(state, SOURCE)
    assert choice["resident_key"] is None
    assert choice["method"] == "ambiguous"


def test_source_only_placeholder_is_saved_without_remembering_an_invented_resident():
    state = state_with()
    incoming = entry(target="taylor example", name=SOURCE)
    saved = save(state, incoming, expected_links=resident_link_expectations(state, [SOURCE]))
    assert saved[NAMESPACE][incoming["id"]]["resident_key"] == "taylor example"
    assert LINKS_NAMESPACE not in saved
    assert choose_resident_link(saved, SOURCE)["resident_key"] is None


def test_identical_save_and_cross_report_replay_do_not_touch_memory_timestamps():
    state = state_with((A, ANAME))
    incoming = entry()
    once = save(state, incoming)
    twice = save(once, incoming, when="2026-11-27T11:00:00+00:00")
    assert twice == once
    another = entry(variant=1)
    third = save(twice, another, when="2026-11-27T12:00:00+00:00")
    assert third[LINKS_NAMESPACE] == once[LINKS_NAMESPACE]
    assert len(third[NAMESPACE]) == 2


def test_guard_helper_uses_canonical_source_names_and_current_digests():
    saved = save(state_with((A, ANAME)), entry())
    expected = resident_link_expectations(saved, [SOURCE, "Example, Taylor", " TAYLOR   EXAMPLE ", "New Resident", ""])
    assert expected == {
        "taylor example": digest(saved[LINKS_NAMESPACE]["taylor example"]),
        "new resident": digest(None),
    }


def test_concurrent_remap_from_another_pdf_is_rejected_atomically():
    initial = save(state_with((A, ANAME), (B, BNAME)), entry())
    stale = resident_link_expectations(initial, [SOURCE])
    remap = entry(target=B, name=BNAME, variant=1)
    newer = save(initial, remap, expected_links=stale, when="2026-11-27T11:00:00+00:00")
    different_pdf = entry(variant=2)
    before = copy.deepcopy(newer)
    with pytest.raises(ValueError, match="remembered resident choice changed"):
        save_report_links(newer, [different_pdf], {different_pdf["id"]: digest(None)}, expected_links=stale)
    assert newer == before
    assert different_pdf["id"] not in newer[NAMESPACE]


def test_consistent_concurrent_first_save_is_allowed_without_rewriting_memory():
    initial = state_with((A, ANAME))
    stale_empty = resident_link_expectations(initial, [SOURCE])
    once = save(initial, entry(), expected_links=stale_empty)
    another = entry(variant=1)
    twice = save(once, another, expected_links=stale_empty, when="2026-11-27T11:00:00+00:00")
    assert twice[LINKS_NAMESPACE] == once[LINKS_NAMESPACE]
    assert len(twice[NAMESPACE]) == 2


def test_same_pdf_optimistic_concurrency_guard_is_still_enforced():
    incoming = entry()
    once = save(state_with((A, ANAME)), incoming)
    with pytest.raises(ValueError, match="This report changed in another session"):
        save_report_links(once, [incoming], {incoming["id"]: digest(None)},
                          expected_links=resident_link_expectations(once, [SOURCE]))


def test_batch_with_same_source_name_and_different_targets_is_rejected():
    state = state_with((A, ANAME), (B, BNAME))
    before = copy.deepcopy(state)
    entries = [entry(), entry(target=B, name=BNAME, variant=1)]
    with pytest.raises(ValueError, match="same source name have different resident choices"):
        save(state, entries, expected_links=resident_link_expectations(state, [SOURCE]))
    assert state == before


def test_known_target_name_changed_after_render_requires_reloading_before_save():
    state = state_with((A, "Example, Taylor Alex"))
    before = copy.deepcopy(state)
    with pytest.raises(ValueError, match="resident name changed while this report was open"):
        save(state, entry())
    assert state == before


def test_remembered_alias_with_inconsistent_source_signature_is_not_reused():
    saved = save(state_with((A, ANAME), (B, BNAME)), entry(target=B, name=BNAME))
    saved[LINKS_NAMESPACE]["taylor example"]["source_name"] = "Different Source"
    # Remove reports to isolate the deliberately damaged remembered record.
    saved[NAMESPACE] = {}
    choice = choose_resident_link(saved, SOURCE)
    assert choice["resident_key"] == A
    assert choice["method"] == "compatible_name"
    assert choice["remembered_invalidated"] is True


def test_invalidated_newest_memory_does_not_revive_superseded_report_choice():
    state = state_with((A, ANAME), (B, BNAME))
    older = entry(target=B, name=BNAME)
    once = save(state, older, when="2026-11-25T10:00:00+00:00")
    corrected = entry(variant=1)
    newer = save(once, corrected, expected_links=resident_link_expectations(once, [SOURCE]),
                 when="2026-11-26T10:00:00+00:00")
    newer["settings"]["appd_roster"][A]["resident"] = "Renamed, Alex Jordan"
    exact_key = "example, taylor"
    newer["settings"]["appd_roster"][exact_key] = {"resident": "Example, Taylor"}
    before = copy.deepcopy(newer)
    for existing in (None, newer[NAMESPACE][older["id"]], newer[NAMESPACE][corrected["id"]]):
        choice = choose_resident_link(newer, SOURCE, existing_report=existing)
        assert choice["resident_key"] == exact_key
        assert choice["method"] == "exact"
        assert choice["remembered_invalidated"] is True
    assert newer == before


def test_invalidated_memory_without_current_match_requires_new_choice_not_old_history():
    state = state_with((A, ANAME), (B, BNAME))
    older = entry(target=B, name=BNAME)
    once = save(state, older, when="2026-11-25T10:00:00+00:00")
    newer = save(once, entry(variant=1), expected_links=resident_link_expectations(once, [SOURCE]),
                 when="2026-11-26T10:00:00+00:00")
    newer["settings"]["appd_roster"][A]["resident"] = "Renamed, Alex Jordan"
    choice = choose_resident_link(newer, SOURCE, existing_report=newer[NAMESPACE][older["id"]])
    assert choice["resident_key"] is None
    assert choice["method"] == "none"
    assert choice["remembered_invalidated"] is True


def test_remembered_choices_round_trip_through_the_existing_encrypted_archive():
    from test_competency_workflow import archive_store, example_state

    original = example_state()
    incoming = entry(target="example, taylor", name="Example, Taylor")
    saved = save(original, incoming)
    store = archive_store()
    ciphertext = store.encode(saved)
    assert b"Taylor Example" not in ciphertext
    assert b"competency_resident_links" not in ciphertext
    restored = store.decode(ciphertext)
    assert restored[LINKS_NAMESPACE] == saved[LINKS_NAMESPACE]
    assert choose_resident_link(restored, SOURCE)["method"] == "remembered"
    assert choose_resident_link(restored, SOURCE)["resident_key"] == "example, taylor"
