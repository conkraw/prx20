"""Preview and remove one remembered form without touching unrelated data.

Deletion is based on the same exact/unique-partial question-ID matching used by
reports. Ambiguous matches and cross-form duplicate decisions block removal.
All writes go through the existing encrypted, optimistic GitHub transaction.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from epa_core import match_form, utc_now


def _fingerprint(value: Any) -> str:
    data = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def form_removal_preview(state: dict, form_id: str) -> dict:
    """Return impact and a token to bind confirmation to exactly this preview.

    An upload for an unrelated form does not invalidate this preview. Changes
    to affected data, the target form, or its mapping require new confirmation.
    The input is not mutated.
    """
    forms = state.get("forms", {})
    if form_id not in forms:
        raise ValueError("This form no longer exists. Reload saved data before continuing.")
    records = state.get("evaluations", {})
    evaluation_ids: list[str] = []
    ambiguous_ids: list[str] = []
    for rid, record in records.items():
        matched = match_form(record, forms)
        if matched["form_id"] == form_id:
            evaluation_ids.append(rid)
        elif form_id in matched["candidates"]:
            ambiguous_ids.append(rid)
    evaluation_ids.sort()
    ambiguous_ids.sort()
    removed_ids = set(evaluation_ids)

    conflict_ids: list[str] = []
    blocked_conflicts: list[str] = []
    affected_conflicts = {}
    for cid, conflict in state.get("conflicts", {}).items():
        incoming = match_form(conflict.get("incoming", {}), forms)
        candidates = set(conflict.get("candidate_ids", []))
        if conflict.get("existing_id"):
            candidates.add(conflict["existing_id"])
        touches_target = bool(candidates & removed_ids)
        incoming_target = incoming["form_id"] == form_id
        incoming_ambiguous = form_id in incoming["candidates"] and not incoming_target
        if not (touches_target or incoming_target or incoming_ambiguous):
            continue
        affected_conflicts[cid] = conflict
        # Never discard an incoming evaluation for a different/uncertain form,
        # or a decision that also references another form's saved evaluation.
        if incoming_target and candidates.issubset(removed_ids):
            conflict_ids.append(cid)
        else:
            blocked_conflicts.append(cid)
    conflict_ids.sort()
    blocked_conflicts.sort()
    conflict_set = set(conflict_ids)
    aliases = {
        key: target for key, target in state.get("record_aliases", {}).items()
        if key in removed_ids or target in removed_ids
    }
    removed_history = [
        item for item in state.get("conflict_history", [])
        if item.get("record_id") in removed_ids or item.get("conflict_id") in conflict_set
    ]
    maps = state.get("settings", {}).get("appd_question_maps", {})
    mapping = maps.get(form_id)
    resident_keys = {
        records[rid].get("resident_key") or records[rid].get("resident", "")
        for rid in evaluation_ids
    }
    residents = sorted({records[rid].get("resident", "") for rid in evaluation_ids})
    payload = {
        "form": forms[form_id],
        "evaluations": {rid: records[rid] for rid in evaluation_ids},
        "ambiguous_evaluations": {rid: records[rid] for rid in ambiguous_ids},
        "conflicts": affected_conflicts,
        "removed_conflict_ids": conflict_ids,
        "blocked_conflict_ids": blocked_conflicts,
        "aliases": aliases,
        "conflict_history": removed_history,
        "has_mapping": form_id in maps,
        "mapping": mapping,
    }
    return {
        "form_id": form_id,
        "form_name": forms[form_id]["name"],
        "evaluation_ids": evaluation_ids,
        "evaluation_count": len(evaluation_ids),
        "resident_count": len(resident_keys),
        "residents": residents,
        "conflict_ids": conflict_ids,
        "conflict_count": len(conflict_ids),
        "ambiguous_evaluation_ids": ambiguous_ids,
        "blocked_conflict_ids": blocked_conflicts,
        "blocked": bool(ambiguous_ids or blocked_conflicts),
        "has_mapping": form_id in maps,
        "alias_count": len(aliases),
        "history_count": len(removed_history),
        "fingerprint": _fingerprint(payload),
    }


def remove_form(state: dict, form_id: str, *, expected_fingerprint: str,
                confirmed: bool = False) -> tuple[dict, dict]:
    """Remove a confirmed form and only its uniquely matched saved evaluations.

    Returns a new archive and summary. The source archive is unchanged on both
    success and failure. The caller must persist the result transactionally.
    """
    if confirmed is not True:
        raise ValueError("Confirm removal of the form and its linked evaluations first.")
    preview = form_removal_preview(state, form_id)
    if not expected_fingerprint or preview["fingerprint"] != expected_fingerprint:
        raise ValueError(
            "This form, its evaluations, or its EPA mapping changed in another session. "
            "Reload saved data and confirm the updated removal counts. Nothing was removed."
        )
    if preview["ambiguous_evaluation_ids"]:
        raise ValueError(
            "Some saved evaluations could belong to this form or another form. "
            "Removal is blocked to avoid deleting or reassigning uncertain evaluations. "
            "Review their question IDs and upload the correct form definitions first."
        )
    if preview["blocked_conflict_ids"]:
        raise ValueError(
            "A pending duplicate or correction also involves another or an unrecognized form. "
            "Resolve that decision under Review before removing this form."
        )
    updated = copy.deepcopy(state)
    removed_ids = set(preview["evaluation_ids"])
    conflict_ids = set(preview["conflict_ids"])
    del updated["forms"][form_id]
    for rid in removed_ids:
        del updated["evaluations"][rid]
    for cid in conflict_ids:
        del updated["conflicts"][cid]
    if "record_aliases" in updated:
        updated["record_aliases"] = {
            key: target for key, target in updated["record_aliases"].items()
            if key not in removed_ids and target not in removed_ids
        }
    if "conflict_history" in updated:
        updated["conflict_history"] = [
            item for item in updated["conflict_history"]
            if item.get("record_id") not in removed_ids and item.get("conflict_id") not in conflict_ids
        ]
    settings = updated.setdefault("settings", {})
    if "appd_question_maps" in settings:
        settings["appd_question_maps"].pop(form_id, None)
    when = utc_now()
    # Keep historical review rows, but never reuse approval after evidence is
    # deleted, even if the original form is subsequently uploaded again.
    for review in settings.get("appd_reviews", {}).values():
        review["approved"] = False
        review["invalidated_at"] = when
        review["invalidated_reason"] = "A form and its linked evaluations were removed."
    result = {
        "form_id": form_id,
        "form_name": preview["form_name"],
        "evaluations_removed": preview["evaluation_count"],
        "residents_affected": preview["resident_count"],
        "pending_reviews_removed": preview["conflict_count"],
        "aliases_removed": preview["alias_count"],
        "mapping_removed": preview["has_mapping"],
        "removed_at": when,
    }
    updated.setdefault("form_removal_history", []).append(copy.deepcopy(result))
    updated["updated_at"] = when
    return updated, result
