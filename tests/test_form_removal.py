"""Safe form deletion with synthetic data. No real archive or network access."""
import copy
import json

import pytest
from cryptography.fernet import Fernet

import epa_appd as appd
import epa_core as core
from epa_form_tools import form_removal_preview, remove_form
from epa_storage import GitHubStore
from test_appd import make_state, approved_state


def two_forms():
    state = make_state()
    fid = next(iter(state["forms"]))
    other = copy.deepcopy(state["forms"][fid])
    other["name"] = "Other form"
    for question in other["questions"]:
        question["id"] = "2" + question["id"][1:]
    other["id"] = core.form_id_for_questions([q["id"] for q in other["questions"]])
    state = core.register_form(state, other)
    record = copy.deepcopy(next(iter(state["evaluations"].values())))
    record["responses"] = {"2" + qid[1:]: answer for qid, answer in record["responses"].items()}
    state, stats = core.ingest_evaluations(state, [record])
    assert stats["added"] == 1
    state = appd.save_mapping(state, other["id"], {"201": ["EPA 2", "EPA 3"], "202": ["EPA 3"]}, True)
    return state, fid, other["id"]


def delete(state, fid):
    return remove_form(state, fid, expected_fingerprint=form_removal_preview(state, fid)["fingerprint"], confirmed=True)


def test_preview_counts_data_without_mutating_state():
    state, fid, _ = two_forms()
    before = copy.deepcopy(state)
    preview = form_removal_preview(state, fid)
    assert preview["evaluation_count"] == 1
    assert preview["resident_count"] == 1
    assert preview["has_mapping"]
    assert not preview["blocked"]
    assert len(preview["fingerprint"]) == 64
    assert state == before


def test_deletes_only_target_form_data_and_links_preserving_settings():
    state, fid, other = two_forms()
    before = copy.deepcopy(state)
    result, stats = delete(state, fid)
    assert state == before
    assert fid not in result["forms"]
    assert result["forms"][other] == before["forms"][other]
    assert fid not in result["settings"]["appd_question_maps"]
    assert result["settings"]["appd_question_maps"][other] == before["settings"]["appd_question_maps"][other]
    assert result["settings"]["appd_roster"] == before["settings"]["appd_roster"]
    assert result["settings"]["appd"] == before["settings"]["appd"]
    retained = {rid: record for rid, record in before["evaluations"].items()
                if core.match_form(record, before["forms"])["form_id"] == other}
    assert result["evaluations"] == retained
    assert stats["evaluations_removed"] == 1
    assert result["form_removal_history"][-1] == stats
    assert result["updated_at"]


def test_cannot_delete_without_explicit_confirmation_or_fresh_preview():
    state = make_state()
    fid = next(iter(state["forms"]))
    token = form_removal_preview(state, fid)["fingerprint"]
    for confirmation in (False, None, "yes", 1):
        with pytest.raises(ValueError, match="Confirm removal"):
            remove_form(state, fid, expected_fingerprint=token, confirmed=confirmation)
    for invalid_token in ("", "wrong"):
        with pytest.raises(ValueError, match="changed in another session"):
            remove_form(state, fid, expected_fingerprint=invalid_token, confirmed=True)
    assert len(state["forms"]) == len(state["evaluations"]) == 1


def test_unknown_or_already_removed_form_fails_without_changing_other_forms():
    state, fid, other = two_forms()
    with pytest.raises(ValueError, match="no longer exists"):
        form_removal_preview(state, "does-not-exist")
    updated, _ = delete(state, fid)
    with pytest.raises(ValueError, match="no longer exists"):
        delete(updated, fid)
    assert list(updated["forms"]) == [other]


def test_unused_form_can_be_removed_without_evaluation_changes():
    state, fid, other = two_forms()
    state["evaluations"] = {rid: record for rid, record in state["evaluations"].items()
                            if core.match_form(record, state["forms"])["form_id"] == other}
    updated, stats = delete(state, fid)
    assert stats["evaluations_removed"] == 0
    assert updated["evaluations"] == state["evaluations"]


def test_last_form_can_be_removed_and_empty_archive_survives_reload():
    state = make_state()
    updated, stats = delete(state, next(iter(state["forms"])))
    assert not updated["forms"] and not updated["evaluations"]
    reloaded = json.loads(json.dumps(updated))
    assert not reloaded["forms"]
    assert core.score_rows(reloaded).empty
    assert stats["evaluations_removed"] == 1


def test_form_removal_survives_real_encrypted_archive_roundtrip():
    state, fid, other = two_forms()
    updated, _ = delete(state, fid)
    store = GitHubStore({"token": "synthetic-token", "repo": "example/epa"}, Fernet.generate_key())
    encoded = store.encode(updated)
    reloaded = store.decode(encoded)
    assert reloaded == updated
    assert fid not in reloaded["forms"]
    assert other in reloaded["forms"]
    assert b"Example, Resident" not in encoded


def test_target_corrections_removed_unrelated_corrections_retained():
    state, fid, other = two_forms()
    for record in list(state["evaluations"].values()):
        incoming = copy.deepcopy(record)
        next(iter(incoming["responses"].values()))["response"] = "5"
        state, stats = core.ingest_evaluations(state, [incoming])
        assert stats["conflicts"] == 1
    assert len(state["conflicts"]) == 2
    retained = {cid: row for cid, row in state["conflicts"].items()
                if core.match_form(row["incoming"], state["forms"])["form_id"] == other}
    updated, stats = delete(state, fid)
    assert stats["pending_reviews_removed"] == 1
    assert updated["conflicts"] == retained
    for row in updated["conflicts"].values():
        assert row["existing_id"] in updated["evaluations"]
        assert all(rid in updated["evaluations"] for rid in row["candidate_ids"])


def test_aliases_and_related_decision_history_cleaned_only_for_removed_records():
    state, fid, other = two_forms()
    rid = form_removal_preview(state, fid)["evaluation_ids"][0]
    other_rid = form_removal_preview(state, other)["evaluation_ids"][0]
    state["record_aliases"] = {"former-identity": rid, "other-identity": other_rid, rid: "target-identity"}
    state["conflict_history"] = [
        {"conflict_id": "target-conflict", "record_id": rid, "action": "keep"},
        {"conflict_id": "other-conflict", "record_id": other_rid, "action": "keep"},
    ]
    updated, stats = delete(state, fid)
    assert updated["record_aliases"] == {"other-identity": other_rid}
    assert updated["conflict_history"] == [state["conflict_history"][1]]
    assert stats["aliases_removed"] == 2


def test_removing_form_invalidates_existing_appd_approval():
    state = approved_state()
    cfg = appd.config_for(state)
    assert appd.approved_review(state, cfg) is not None
    previous = copy.deepcopy(state["settings"]["appd_reviews"])
    updated, _ = delete(state, next(iter(state["forms"])))
    assert appd.approved_review(updated, cfg) is None
    for key, review in updated["settings"]["appd_reviews"].items():
        assert review["approved"] is False
        assert review["rows"] == previous[key]["rows"]
        assert review["invalidated_at"]


def test_old_approval_is_not_revived_by_reuploading_identical_source():
    state = approved_state()
    fid = next(iter(state["forms"]))
    updated, _ = delete(state, fid)
    updated = core.register_form(updated, state["forms"][fid])
    updated, _ = core.ingest_evaluations(updated, list(state["evaluations"].values()))
    updated = appd.save_mapping(updated, fid, {"101": ["EPA 2"], "102": ["EPA 2"]}, True)
    assert appd.approved_review(updated, appd.config_for(updated)) is None


def test_explicit_reupload_restores_form_and_counts_evaluation_once():
    state = make_state()
    fid = next(iter(state["forms"]))
    updated, _ = delete(state, fid)
    updated = core.register_form(updated, state["forms"][fid])
    records = list(state["evaluations"].values())
    updated, first = core.ingest_evaluations(updated, records)
    updated, second = core.ingest_evaluations(updated, records)
    assert first["added"] == 1
    assert second["duplicates"] == 1
    assert len(updated["evaluations"]) == 1
    assert fid not in updated["settings"]["appd_question_maps"]


def test_unique_partial_evaluation_is_included_in_removal():
    state = make_state()
    record = copy.deepcopy(next(iter(state["evaluations"].values())))
    record["evaluator"] = "Second Faculty"
    record["evaluator_key"] = "second faculty"
    record["responses"].pop("102")
    state, _ = core.ingest_evaluations(state, [record])
    fid = next(iter(state["forms"]))
    assert form_removal_preview(state, fid)["evaluation_count"] == 2
    updated, _ = delete(state, fid)
    assert not updated["evaluations"]


def test_ambiguous_match_is_not_deleted_or_reassigned_to_another_form():
    state = make_state()
    fid = next(iter(state["forms"]))
    other = copy.deepcopy(state["forms"][fid])
    other["questions"][1]["id"] = "103"
    state = core.register_form(state, other)
    record = copy.deepcopy(next(iter(state["evaluations"].values())))
    record["evaluator"] = "Second Faculty"
    record["evaluator_key"] = "second faculty"
    record["responses"].pop("102")
    state, _ = core.ingest_evaluations(state, [record])
    before = copy.deepcopy(state)
    assert form_removal_preview(state, fid)["blocked"]
    with pytest.raises(ValueError, match="could belong"):
        delete(state, fid)
    assert state == before


def test_cross_form_duplicate_decision_blocks_deletion():
    state, fid, other = two_forms()
    rid = form_removal_preview(state, fid)["evaluation_ids"][0]
    other_rid = form_removal_preview(state, other)["evaluation_ids"][0]
    state["conflicts"]["cross"] = {
        "existing_id": rid, "candidate_ids": [rid, other_rid],
        "incoming": copy.deepcopy(state["evaluations"][other_rid]),
    }
    assert form_removal_preview(state, fid)["blocked_conflict_ids"] == ["cross"]
    with pytest.raises(ValueError, match="pending duplicate"):
        delete(state, fid)
    assert len(state["evaluations"]) == 2


@pytest.mark.parametrize("change", ["evaluation", "new_evaluation", "form", "mapping", "conflict", "alias"])
def test_concurrent_target_changes_require_new_confirmation(change):
    state, fid, other = two_forms()
    token = form_removal_preview(state, fid)["fingerprint"]
    rid = form_removal_preview(state, fid)["evaluation_ids"][0]
    if change == "evaluation":
        state["evaluations"][rid]["overall_comments"] = "Changed evidence."
    elif change == "new_evaluation":
        incoming = copy.deepcopy(state["evaluations"][rid])
        incoming["evaluator_key"] = "another faculty"
        incoming["evaluator"] = "Another Faculty"
        state, _ = core.ingest_evaluations(state, [incoming])
    elif change == "form":
        state["forms"][fid]["name"] = "Changed name"
    elif change == "mapping":
        state = appd.save_mapping(state, fid, {"101": ["EPA 12"]}, True)
    elif change == "conflict":
        incoming = copy.deepcopy(state["evaluations"][rid])
        incoming["responses"]["101"]["response"] = "5"
        state, _ = core.ingest_evaluations(state, [incoming])
    else:
        state["record_aliases"] = {"new-alias": rid}
    before = copy.deepcopy(state)
    with pytest.raises(ValueError, match="changed in another session"):
        remove_form(state, fid, expected_fingerprint=token, confirmed=True)
    assert state == before


def test_concurrent_unrelated_upload_is_preserved_not_rejected():
    state, fid, other = two_forms()
    token = form_removal_preview(state, fid)["fingerprint"]
    rid = form_removal_preview(state, other)["evaluation_ids"][0]
    incoming = copy.deepcopy(state["evaluations"][rid])
    incoming["evaluator_key"] = "another faculty"
    incoming["evaluator"] = "Another Faculty"
    state, _ = core.ingest_evaluations(state, [incoming])
    updated, stats = remove_form(state, fid, expected_fingerprint=token, confirmed=True)
    assert len(updated["evaluations"]) == 2
    assert stats["evaluations_removed"] == 1
    assert all(core.match_form(record, updated["forms"])["form_id"] == other
               for record in updated["evaluations"].values())


def test_same_form_names_do_not_cause_wrong_form_to_be_removed():
    state, fid, other = two_forms()
    state["forms"][other]["name"] = state["forms"][fid]["name"]
    updated, _ = delete(state, fid)
    assert other in updated["forms"]
    assert len(updated["evaluations"]) == 1


def test_deleted_data_no_longer_contribute_to_form_or_appd_averages():
    state, fid, other = two_forms()
    updated, _ = delete(state, fid)
    summary = core.epa_summary(core.score_rows(updated))
    assert set(summary["form_id"]) == {other}
    draft = appd.build_draft(updated)
    assert all(row["Form"] == "Other form" for row in draft["evidence"])
