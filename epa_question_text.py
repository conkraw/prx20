"""Preserve and restore question wording without guessing from answers.

Only explicit question text and matching question IDs are used. Comments,
response values, and rotation names are never treated as question wording.
Restoration changes no evaluation, question inclusion, score map, or EPA link.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import unicodedata
from decimal import Decimal
from typing import Any


def _text(value: Any) -> str:
    return "" if value is None else unicodedata.normalize("NFKC", str(value)).strip()


def _key(value: Any) -> str:
    return " ".join(_text(value).split()).casefold()


def _id(value: Any) -> str:
    text = _text(value)
    return str(int(Decimal(text))) if re.fullmatch(r"\d+(?:\.0+)?", text) else text


def is_placeholder_question_text(value: Any, question_id: Any = None) -> bool:
    """Recognize only empty text and known ID-only labels, not real prompts."""
    text = _key(value)
    if text in {"", "none", "nan", "question wording not available", "question text missing"}:
        return True
    ids = [re.escape(_id(question_id))] if question_id is not None else [r"[\w.-]+"]
    for qid in ids:
        if re.fullmatch(
            rf"question\s+(?:id\s*[:#]?\s*)?{qid}"
            r"(?:\s*\((?:verify wording from form definition|wording unavailable|text missing)\))?",
            text, flags=re.I,
        ):
            return True
    return False


def merge_question_metadata(previous: dict, incoming: dict) -> dict:
    """Keep real wording and saved scoring choices when an import is sparse."""
    merged = copy.deepcopy(previous)
    merged.update(copy.deepcopy(incoming))
    qid = _id(incoming.get("id", previous.get("id")))
    merged["id"] = qid
    old_text, new_text = previous.get("text", ""), incoming.get("text", "")
    old_real = not is_placeholder_question_text(old_text, qid)
    new_real = not is_placeholder_question_text(new_text, qid)
    if old_real and not new_real:
        merged["text"] = old_text
    if _text(previous.get("tags")) and not _text(incoming.get("tags")):
        merged["tags"] = previous["tags"]
    # A new definition supplies wording, not permission to reset admin choices.
    for field in ("include", "score_map"):
        if field in previous:
            merged[field] = copy.deepcopy(previous[field])
    history = []
    history_keys = set()
    candidates = list(previous.get("text_history", [])) + list(incoming.get("text_history", []))
    if old_real and new_real and _key(old_text) != _key(new_text):
        candidates.append(old_text)
    for text in candidates:
        key = _key(text)
        if not is_placeholder_question_text(text, qid) and key not in history_keys:
            history.append(_text(text))
            history_keys.add(key)
    if history:
        merged["text_history"] = history
    return merged


def merge_response_wording(previous: dict, incoming: dict, question_id: str) -> dict:
    """Retain added prompt metadata on duplicate evaluations, without rescoring."""
    result = copy.deepcopy(previous)
    old = previous.get("question_text", "")
    new = incoming.get("question_text", "")
    variants = list(previous.get("question_text_variants", [])) + list(incoming.get("question_text_variants", []))
    if not is_placeholder_question_text(new, question_id):
        if is_placeholder_question_text(old, question_id):
            result["question_text"] = new
        elif _key(old) != _key(new):
            variants.extend([old, new])
    seen = {}
    for value in variants:
        if not is_placeholder_question_text(value, question_id):
            seen.setdefault(_key(value), _text(value))
    if seen:
        result["question_text_variants"] = list(seen.values())
    return result


def saved_wording_candidates(state: dict, question_ids: list[str] | tuple[str, ...]) -> dict[str, list[str]]:
    """Index matching IDs. Conflicting versions stay visible, never guessed."""
    wanted = {_id(qid) for qid in question_ids}
    index: dict[str, dict[str, str]] = {qid: {} for qid in wanted}

    def add(qid, value):
        qid = _id(qid)
        if qid in index and not is_placeholder_question_text(value, qid):
            index[qid].setdefault(_key(value), _text(value))

    for form in state.get("forms", {}).values():
        for question in form.get("questions", []):
            qid = _id(question.get("id"))
            add(qid, question.get("text"))
            for text in question.get("text_history", []):
                add(qid, text)
    for record in state.get("evaluations", {}).values():
        for qid, answer in record.get("responses", {}).items():
            add(qid, answer.get("question_text"))
            for text in answer.get("question_text_variants", []):
                add(qid, text)
    return {qid: list(texts.values()) for qid, texts in index.items()}


def missing_wording_ids(form: dict) -> list[str]:
    return [_id(q["id"]) for q in form.get("questions", [])
            if is_placeholder_question_text(q.get("text"), q["id"])]


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def wording_repair_preview(state: dict, form_id: str, source_form: dict | None = None) -> dict:
    """Preview missing-text repair, scoped to the selected form's existing IDs.

    A source definition may contain extra questions or only part of the form.
    Neither circumstance changes form identity or deletes questions/mappings.
    """
    form = state.get("forms", {}).get(form_id)
    if form is None:
        raise ValueError("This form is no longer available. Reload saved data.")
    missing = missing_wording_ids(form)
    target_ids = {_id(q["id"]) for q in form["questions"]}
    source_ids: set[str] = set()
    if source_form is None:
        candidates = saved_wording_candidates(state, missing)
    else:
        source_questions = source_form.get("questions", [])
        source_ids = {_id(q["id"]) for q in source_questions}
        if not source_ids or len(source_ids) != len(source_questions):
            raise ValueError("The form definition has no questions or repeats a question ID.")
        if not source_ids.intersection(target_ids):
            raise ValueError("This file has no question IDs in common with the selected form. Choose its original form-definition TAB file.")
        candidates = {qid: [] for qid in missing}
        for q in source_questions:
            qid = _id(q["id"])
            if qid in candidates and not is_placeholder_question_text(q.get("text"), qid):
                candidates[qid] = [_text(q["text"]) ]
    changes, conflicting = [], []
    by_id = {_id(q["id"]): q for q in form["questions"]}
    for qid in missing:
        texts = candidates.get(qid, [])
        if len(texts) == 1:
            changes.append({"question_id": qid, "before": by_id[qid].get("text", ""), "after": texts[0]})
        elif len(texts) > 1:
            conflicting.append(qid)
    changed_ids = {item["question_id"] for item in changes}
    payload = {
        "form": form,
        "mapping": state.get("settings", {}).get("appd_question_maps", {}).get(form_id),
        "changes": changes,
        "conflicting_ids": conflicting,
        "source_form": source_form,
    }
    return {
        "form_id": form_id, "form_name": form.get("name", ""), "changes": changes,
        "missing_ids": missing, "remaining_ids": [qid for qid in missing if qid not in changed_ids],
        "conflicting_ids": conflicting, "source_only_ids": sorted(source_ids - target_ids),
        "source": "original form-definition file" if source_form is not None else "saved wording",
        "token": _digest(payload),
    }


def restore_question_wording(state: dict, form_id: str, *, expected_token: str,
                            source_form: dict | None = None) -> tuple[dict, dict]:
    """Apply a reviewed repair to a fresh archive, with stale-edit protection."""
    from epa_core import utc_now

    preview = wording_repair_preview(state, form_id, source_form)
    if not expected_token or preview["token"] != expected_token:
        raise ValueError("This form, its wording, or its EPA links changed in another session. Reload and review the repair again. Nothing was overwritten.")
    if not preview["changes"]:
        raise ValueError("No missing wording can be restored from this source. Choose the original form-definition TAB file.")
    updated = copy.deepcopy(state)
    form = updated["forms"][form_id]
    changes = {item["question_id"]: item["after"] for item in preview["changes"]}
    for question in form["questions"]:
        qid = _id(question["id"])
        if qid in changes:
            question["text"] = changes[qid]
    # Keep selections/scales/links, but require review of the restored wording.
    form["scoring_confirmed"] = False
    when = utc_now()
    updated["updated_at"] = when
    result = {"form_id": form_id, "restored": len(changes), "remaining": len(preview["remaining_ids"]),
              "source": preview["source"], "restored_at": when}
    updated.setdefault("question_wording_repairs", []).append(copy.deepcopy(result))
    return updated, result
