"""End-to-end competency report checks using invented residents and PDFs only."""
from __future__ import annotations

import copy
from io import BytesIO
from unittest.mock import Mock

import pytest
from cryptography.fernet import Fernet
from openpyxl import load_workbook
from pypdf import PdfReader

import epa_appd as appd
import epa_core as core
import epa_export_rules as rules
import epa_workflow as workflow
from epa_competency_import import parse_competency_pdf
from epa_competency_pdf import build_competency_comments_pdf
from epa_competency_reports import (
    NAMESPACE, linked_report, original_pdf, save_report_links, saved_reports,
    suggested_resident,
)
from epa_resident_comments import collect_resident_comments
from epa_storage import ConflictError, GitHubStore
from test_appd import make_state
from test_competency_import import pdf_bytes, report_text


KEY = "example, taylor"
NAME = "Example, Taylor"
START = "2026-07-01"
END = "2026-11-03"


def source():
    data = pdf_bytes(report_text())
    return parse_competency_pdf(data, "synthetic_competencies.pdf"), data


def linked(parsed=None, data=None, **changes):
    if parsed is None:
        parsed, data = source()
    choices = dict(resident_key=KEY, resident_name=NAME, start_date=START, end_date=END)
    choices.update(changes)
    return linked_report(parsed, data, **choices)


def example_state():
    state = make_state()
    state["settings"]["appd"] = rules.default_parameters(state["settings"]["appd"])
    roster = state["settings"]["appd_roster"].pop("example, resident")
    roster["resident"] = NAME
    state["settings"]["appd_roster"][KEY] = roster
    evaluation = next(iter(state["evaluations"].values()))
    evaluation.update(resident=NAME, resident_key=KEY, rotation="Inpatient Pediatrics",
                      overall_comments="Explains the daily plan clearly.",
                      subject_comments="My next goal is a shorter presentation.")
    evaluation["responses"]["101"]["comment"] = "Reassessed the patient.\nUpdated the team."
    return state


def save(state, entry, **kwargs):
    return save_report_links(state, [entry], {entry["id"]: appd.digest(saved_reports(state).get(entry["id"]))}, **kwargs)[0]


def archive_store():
    store = GitHubStore({"token": "synthetic_token", "repo": "example/epa"}, Fernet.generate_key())
    store._session.request = Mock(side_effect=AssertionError("No network in synthetic workflow tests"))
    return store


def collected(state, report):
    return collect_resident_comments(state, report["resident_key"], report["comment_start"], report["comment_end"])


def workbook_values(data):
    book = load_workbook(BytesIO(data), data_only=True)
    return {sheet.title: list(sheet.values) for sheet in book.worksheets}


def test_parse_link_encrypt_reload_and_print_with_original_comments():
    state = example_state()
    parsed, data = source()
    assert suggested_resident(state, parsed["resident"]) == KEY
    entry = linked(parsed, data)
    updated = save(state, entry)
    store = archive_store()
    encrypted = store.encode(updated)
    assert b"Taylor Example" not in encrypted
    assert b"Explains the daily plan" not in encrypted
    reloaded = store.decode(encrypted)
    report = saved_reports(reloaded)[entry["id"]]
    assert original_pdf(report) == data
    assert report["scores"] == parsed["scores"]
    comments = collected(reloaded, report)
    output = build_competency_comments_pdf(report, comments)
    pages = PdfReader(BytesIO(output)).pages
    first = pages[0].extract_text()
    text = "\n".join(page.extract_text() for page in pages)
    assert first.count("2 - 2.5") == 22
    assert "2.25" not in text
    assert text.index("APPD recommendations as reported") < text.index("Comments by rotation")
    for expected in (NAME, "Taylor Example", "Inpatient Pediatrics", "Example skill A",
                     "Explains the daily plan clearly.", "Reassessed the patient.",
                     "Updated the team.", "Resident comments", "My next goal is a shorter presentation."):
        assert expected in text


def test_same_pdf_is_idempotent_and_does_not_change_saved_timestamp():
    state = example_state()
    entry = linked()
    once = save(state, entry, saved_at="2026-11-25T12:00:00+00:00")
    twice, message = save_report_links(once, [entry, copy.deepcopy(entry)],
                                      {entry["id"]: appd.digest(once[NAMESPACE][entry["id"]])},
                                      saved_at="2026-11-26T12:00:00+00:00")
    assert twice == once
    assert len(twice[NAMESPACE]) == 1
    assert "already saved" in message


def test_same_pdf_conflicting_links_in_one_batch_fail_atomically():
    state = example_state()
    before = copy.deepcopy(state)
    first = linked()
    second = copy.deepcopy(first)
    second["comment_start"] = "2026-08-01"
    with pytest.raises(ValueError, match="different resident or date choices"):
        save_report_links(state, [first, second], {first["id"]: appd.digest(None)})
    assert state == before


def test_changed_pdf_same_resident_and_season_is_separate_version():
    first = linked()
    state = save(example_state(), first)
    changed_data = pdf_bytes(report_text().replace("2 - 2.5", "3 - 3.5", 1))
    changed_parse = parse_competency_pdf(changed_data, "synthetic_competencies.pdf")
    second = linked(changed_parse, changed_data)
    updated = save(state, second)
    assert first["id"] != second["id"]
    assert set(updated[NAMESPACE]) == {first["id"], second["id"]}
    assert updated[NAMESPACE][first["id"]] == state[NAMESPACE][first["id"]]
    assert updated[NAMESPACE][second["id"]]["scores"][0]["score"] == "3 - 3.5"


def test_transaction_retry_keeps_unrelated_concurrent_evaluation():
    initial = example_state()
    latest = copy.deepcopy(initial)
    latest["evaluations"]["new_concurrent_evaluation"] = copy.deepcopy(next(iter(initial["evaluations"].values())))
    latest["evaluations"]["new_concurrent_evaluation"]["overall_comments"] = "A colleague saved this during the report upload."
    entry = linked()
    expected = {entry["id"]: appd.digest(None)}
    store = archive_store()
    store.load = Mock(side_effect=[(initial, "old-sha"), (latest, "new-sha")])
    store.save = Mock(side_effect=[ConflictError("Concurrent upload"), "saved-sha"])
    updated, _ = store.transaction(lambda current: save_report_links(current, [entry], expected))
    assert store.load.call_count == 2
    assert store.save.call_count == 2
    assert updated["evaluations"] == latest["evaluations"]
    assert updated[NAMESPACE][entry["id"]]["resident_key"] == KEY
    assert store.save.call_args.kwargs["expected_sha"] == "new-sha"
    assert NAMESPACE not in initial


def test_concurrent_edit_to_same_report_is_rejected_without_overwrite():
    original = linked()
    state = save(example_state(), original)
    expected = {original["id"]: appd.digest(state[NAMESPACE][original["id"]])}
    colleague = copy.deepcopy(original)
    colleague["comment_end"] = "2026-10-01"
    latest = save(state, colleague)
    desired = copy.deepcopy(original)
    desired["comment_start"] = "2026-08-01"
    before = copy.deepcopy(latest)
    with pytest.raises(ValueError, match="changed in another session"):
        save_report_links(latest, [desired], expected)
    assert latest == before


def test_unrelated_concurrent_report_is_preserved():
    first = linked()
    alternate_data = pdf_bytes(report_text().replace("Taylor Example", "Casey Test").replace("Taylor\nExample", "Casey\nTest"))
    parsed = parse_competency_pdf(alternate_data, "another_report.pdf")
    second = linked(parsed, alternate_data, resident_key="test, casey", resident_name="Test, Casey")
    state = save(example_state(), second)
    updated, _ = save_report_links(state, [first], {first["id"]: appd.digest(None)})
    assert updated[NAMESPACE][second["id"]] == state[NAMESPACE][second["id"]]
    assert set(updated[NAMESPACE]) == {first["id"], second["id"]}


def test_matching_suggests_exact_last_first_but_never_initials_or_substrings():
    state = example_state()
    assert suggested_resident(state, "Taylor Example") == KEY
    assert suggested_resident(state, "Example, Taylor") == KEY
    assert suggested_resident(state, "  TAYLOR   EXAMPLE ") == KEY
    for name in ("Taylor", "Example", "Taylor E.", "Taylor Example Jr", "T. Example"):
        assert suggested_resident(state, name) is None


def test_matching_uses_actual_saved_export_id_and_rejects_ambiguous_alias():
    state = example_state()
    assert suggested_resident(state, "EXAMPLE-001") == KEY
    state["settings"]["appd_roster"]["different, resident"] = {
        "resident": "Different, Resident", "export_id": "EXAMPLE-001",
    }
    assert suggested_resident(state, "EXAMPLE-001") is None


def test_two_keys_with_same_full_name_are_not_silently_merged():
    state = example_state()
    state["settings"]["appd_roster"]["different-id"] = {"resident": "Taylor Example", "export_id": "OTHER-002"}
    assert suggested_resident(state, "Taylor Example") is None


@pytest.mark.parametrize("start,end", [(None, END), (START, None), ("not a date", END), (END, START)])
def test_calendar_dates_must_be_explicit_and_valid(start, end):
    parsed, data = source()
    assert "year" not in parsed
    with pytest.raises(ValueError, match="valid comment date range"):
        linked(parsed, data, start_date=start, end_date=end)


def test_report_save_does_not_change_epa_values_or_invalidate_approved_exports():
    state = example_state()
    cfg = appd.config_for(state)
    rows = workflow.numeric_rows(appd.build_draft(state, cfg))
    approved, _ = workflow.approve_save_prepare(state, cfg, rows,
                                               appd.input_fingerprint(state, cfg),
                                               expected_saved=appd.digest(None))
    before = copy.deepcopy(approved)
    before_draft = appd.build_draft(approved, cfg)
    before_review = copy.deepcopy(workflow.ready_review(approved, cfg))
    before_raw = workbook_values(workflow.raw_workbook_bytes(approved, cfg))
    before_ratings = workbook_values(appd.workbook_bytes(approved, cfg))
    assert before_review is not None
    assert before_review["rows"][0]["EPA 2"] == 3.0
    entry = linked()
    updated = save(approved, entry)
    assert approved == before
    for section in ("forms", "evaluations", "conflicts", "settings"):
        assert updated[section] == before[section]
    assert appd.input_fingerprint(updated, cfg) == appd.input_fingerprint(before, cfg)
    assert appd.build_draft(updated, cfg) == before_draft
    assert workflow.ready_review(updated, cfg) == before_review
    assert workbook_values(workflow.raw_workbook_bytes(updated, cfg)) == before_raw
    assert workbook_values(appd.workbook_bytes(updated, cfg)) == before_ratings
    assert updated[NAMESPACE][entry["id"]]["scores"][0]["score"] == "2 - 2.5"


def test_combined_report_uses_selected_dates_and_keeps_undated_feedback_separate():
    state = example_state()
    original = next(iter(state["evaluations"].values()))
    outside = copy.deepcopy(original)
    outside.update(completed_at="2025-01-01T12:00:00", overall_comments="Outside the chosen dates.", subject_comments="")
    unknown = copy.deepcopy(original)
    unknown.update(completed_at=None, session_date=None, overall_comments="Undated observation.", subject_comments="")
    state["evaluations"].update(outside=outside, unknown=unknown)
    entry = linked()
    comments = collected(state, entry)
    assert comments["counts"]["out_of_period_evaluations"] == 1
    assert comments["counts"]["undated_evaluations"] == 1
    text = "\n".join(p.extract_text() for p in PdfReader(BytesIO(build_competency_comments_pdf(entry, comments))).pages)
    assert "Outside the chosen dates." not in text
    assert "Undated observation." in text
    assert text.index("Explains the daily plan clearly.") < text.index("Undated observation.")
    assert START in text and END in text


def test_new_evaluations_appear_without_reuploading_or_changing_source_report():
    entry = linked()
    saved = save(example_state(), entry)
    report = saved[NAMESPACE][entry["id"]]
    before = collected(saved, report)
    newer = copy.deepcopy(next(iter(saved["evaluations"].values())))
    newer.update(rotation="Ambulatory Pediatrics", completed_at="2026-09-20T10:00:00",
                 overall_comments="New saved ambulatory feedback.")
    refreshed = copy.deepcopy(saved)
    refreshed["evaluations"]["additional"] = newer
    after = collected(refreshed, refreshed[NAMESPACE][entry["id"]])
    assert refreshed[NAMESPACE] == saved[NAMESPACE]
    assert len(before["rotations"]) == 1
    assert len(after["rotations"]) == 2
    assert after["counts"]["evaluations_with_comments"] == 2


def test_source_fingerprint_prevents_pdf_substitution_and_corrupt_saved_bytes():
    parsed, data = source()
    other = pdf_bytes(report_text().replace("2 - 2.5", "3", 1))
    with pytest.raises(ValueError, match="PDF changed"):
        linked(parsed, other)
    entry = linked(parsed, data)
    entry["source_pdf_b64"] = "not valid base64"
    with pytest.raises(ValueError, match="original PDF could not be read"):
        save(example_state(), entry)


def test_collect_and_render_do_not_modify_saved_archive():
    entry = linked()
    state = save(example_state(), entry)
    before = copy.deepcopy(state)
    report = state[NAMESPACE][entry["id"]]
    build_competency_comments_pdf(report, collected(state, report))
    assert state == before
