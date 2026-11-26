"""Read original narrative feedback without changing scores or saved records.

Comments do not require an EPA mapping or a confirmed scoring form. Records
without a usable date are returned separately so a dated report cannot imply
that they were assessed within its reporting period.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from epa_core import match_form, normalize_code, normalize_key
from epa_pgy import as_date
from epa_question_text import is_placeholder_question_text, saved_wording_candidates


MISSING_ROTATION = "Rotation not recorded"
_EMPTY_RESPONSES = {"", "n/a", "na", "not applicable", "not observed", "not assessed",
                    "unable to assess", "no opportunity to observe", "none", "nan", "null"}
_SCALE_LABELS = {
    "yes", "no", "always", "usually", "sometimes", "rarely", "never",
    "strongly agree", "agree", "neutral", "disagree", "strongly disagree",
    "below expectations", "meets expectations", "exceeds expectations",
    "does not meet expectations", "significantly exceeds expectations",
    "unsatisfactory", "satisfactory", "outstanding", "poor", "fair", "good",
    "very good", "excellent", "novice", "advanced beginner", "competent",
    "proficient", "expert", "independent", "direct supervision",
    "indirect supervision", "supervision at a distance",
}
_NARRATIVE_PROMPT = re.compile(
    r"\b(comment|comments|feedback|narrative|strength|strengths|improvement|improvements|"
    r"next steps|suggestions|recommendations)\b", re.I)
_RATING = re.compile(
    r"^(?:(?:level|score|rating)\s*[:=]?\s*)?[+-]?\d+(?:[.,]\d+)?"
    r"(?:[ab]|\s*(?:-|–|—|to|/)\s*[+-]?\d+(?:[.,]\d+)?|\s*%)?$", re.I)


def _text(value: Any) -> str:
    """Retain stored wording, Unicode, and line breaks exactly."""
    return "" if value is None else str(value)


def _nonblank(value: Any) -> bool:
    return bool(_text(value).strip())


def _date_bound(value: Any, label: str):
    if value is None or value == "":
        return None
    result = as_date(value)
    if result is None:
        raise ValueError(f"Enter a valid {label} for the comments report.")
    return result


def _numeric_or_rating(value: str) -> bool:
    stripped = value.strip()
    if _RATING.fullmatch(stripped):
        return True
    try:
        Decimal(stripped)
        return True
    except InvalidOperation:
        return False


def _written_response(answer: dict, question: dict, form: dict, prompt: str) -> str:
    """Separate written responses from numbers, scale codes, and dropdowns.

An explicit narrative prompt permits short written feedback such as
"Excellent". Configured score codes and numeric/rating responses never become
comments, even when a form has not yet been confirmed for EPA scoring.
"""
    value = _text(answer.get("response"))
    normalized = normalize_key(value)
    if normalized in _EMPTY_RESPONSES or _numeric_or_rating(value):
        return ""
    codes = set()
    for mapping in (form.get("score_map"), question.get("score_map")):
        if isinstance(mapping, dict):
            codes.update(normalize_code(code) for code in mapping)
    if normalize_code(value) in codes:
        return ""
    dropdown = _text(answer.get("dropdown"))
    if dropdown.strip() and normalize_key(dropdown) == normalized:
        return ""
    narrative_prompt = bool(_NARRATIVE_PROMPT.search(prompt))
    if normalized in _SCALE_LABELS and not narrative_prompt:
        return ""
    # A scale descriptor often follows its numeric code in text-only exports.
    # Keep that descriptor out of narrative feedback unless a narrative prompt
    # explicitly identifies the answer as written feedback.
    if not narrative_prompt and re.match(r"^(?:level\s+)?\d+(?:\.\d+)?[ab]?\s*[:=)\-–—]\s*\S", value.strip(), re.I):
        return ""
    return value


def _question_prompt(qid: str, answer: dict, question: dict,
                     wording: dict[str, list[str]]) -> tuple[str, bool]:
    for value in (answer.get("question_text"), question.get("text")):
        if not is_placeholder_question_text(value, qid):
            return _text(value), False
    candidates = wording.get(qid, [])
    if len(candidates) == 1:
        return candidates[0], False
    return f"Question ID {qid}", True


def _evaluation(record_id: str, record: dict, forms: dict,
                wording: dict[str, list[str]]) -> dict:
    match = match_form(record, forms)
    form_id = match["form_id"]
    form = forms.get(form_id, {})
    questions = {str(q["id"]): q for q in form.get("questions", [])}
    order = {str(q["id"]): index for index, q in enumerate(form.get("questions", []), 1)}
    notes = []
    completed = as_date(record.get("completed_at")) or as_date(record.get("completed_at_raw"))
    session = as_date(record.get("session_date")) or as_date(record.get("session_date_raw"))
    day = completed or session
    basis = "completed_at" if completed else "session_date" if session else "unknown"
    if basis == "session_date":
        notes.append("Completion date is missing or invalid. Session date is used.")
    elif basis == "unknown":
        notes.append("No usable completion or session date. Reporting period is unknown.")
    rotation = _text(record.get("rotation"))
    if not rotation.strip():
        notes.append("Rotation is not recorded.")
    if not form_id:
        notes.append("Form is not uniquely identified. Saved narrative feedback is still included.")
    feedback = []
    resident_comments = []
    if _nonblank(record.get("overall_comments")):
        feedback.append({"kind": "overall_comments", "question_id": "", "question_order": None,
                         "question": "Overall comments", "text": _text(record["overall_comments"])})
    duplicated_fields = 0
    for qid, raw_answer in record.get("responses", {}).items():
        qid = str(qid)
        answer = raw_answer if isinstance(raw_answer, dict) else {}
        question = questions.get(qid, {})
        prompt, missing_wording = _question_prompt(qid, answer, question, wording)
        written = _written_response(answer, question, form, prompt)
        comment = _text(answer.get("comment"))
        values = []
        if written.strip():
            values.append(("written_response", written))
        if comment.strip():
            # Suppress only the exact same text exported into both fields of
            # this question. Matching words elsewhere are independent evidence.
            if written.strip() and written == comment:
                duplicated_fields += 1
            else:
                values.append(("question_comment", comment))
        for kind, text in values:
            feedback.append({"kind": kind, "question_id": qid, "question_order": order.get(qid),
                             "question": prompt, "question_wording_missing": missing_wording, "text": text})
    if _nonblank(record.get("subject_comments")):
        resident_comments.append({"kind": "subject_comments", "text": _text(record["subject_comments"])})
    if duplicated_fields:
        notes.append(f"{duplicated_fields} identical response/comment field pair(s) shown once within their question.")
    return {
        "id": record_id, "source_evaluation_id": _text(record.get("source_evaluation_id")),
        "resident": _text(record.get("resident")),
        "resident_key": normalize_key(record.get("resident_key") or record.get("resident")),
        "evaluator": _text(record.get("evaluator")), "evaluator_status": _text(record.get("evaluator_status")),
        "rotation": rotation, "date": day.isoformat() if day else None, "date_basis": basis,
        "completed_at": record.get("completed_at"), "session_date": record.get("session_date"),
        "form_id": form_id, "form_name": _text(form.get("name")) or "Form not identified",
        "form_match_status": match["status"],
        "source_files": list(record.get("source_files") or []),
        "feedback": feedback, "resident_comments": resident_comments,
        "duplicate_fields_suppressed": duplicated_fields, "notes": notes,
    }


def collect_resident_comments(state: dict, resident_key: str, start_date=None, end_date=None) -> dict:
    """Return dated comments grouped by rotation and undated comments separately.

    Date bounds are inclusive. Only active saved evaluations are considered,
    never pending conflict alternatives. Resident names must match their exact
    normalized key. No abbreviation, substring, or fuzzy-name matching occurs.
    This function is read-only and does not depend on scoring confirmation.
    """
    key = normalize_key(resident_key)
    if not key:
        raise ValueError("Choose a resident for the comments report.")
    start = _date_bound(start_date, "start date")
    end = _date_bound(end_date, "end date")
    if start and end and start > end:
        raise ValueError("The comments start date must not be after the end date.")
    records = [
        (str(record_id), record) for record_id, record in state.get("evaluations", {}).items()
        if normalize_key(record.get("resident_key") or record.get("resident")) == key
    ]
    forms = state.get("forms", {})
    ids = sorted({str(qid) for _, record in records for qid in record.get("responses", {})})
    wording = saved_wording_candidates(state, ids)
    grouped = {}
    undated = []
    included = []
    counts = {
        "resident_evaluations": len(records), "in_period_evaluations": 0,
        "out_of_period_evaluations": 0, "evaluations_with_comments": 0,
        "feedback_items": 0, "resident_comment_items": 0, "undated_evaluations": 0,
        "undated_feedback_items": 0, "undated_resident_comment_items": 0,
        "missing_rotation_evaluations": 0, "session_date_fallback_evaluations": 0,
        "duplicate_fields_suppressed": 0,
    }
    for record_id, record in records:
        evaluation = _evaluation(record_id, record, forms, wording)
        has_comments = bool(evaluation["feedback"] or evaluation["resident_comments"])
        if not evaluation["date"]:
            if has_comments:
                undated.append(evaluation)
            continue
        day = as_date(evaluation["date"])
        if (start and day < start) or (end and day > end):
            counts["out_of_period_evaluations"] += 1
            continue
        counts["in_period_evaluations"] += 1
        if not has_comments:
            continue
        included.append(evaluation)
        rotation = evaluation["rotation"]
        group_key = normalize_key(rotation)
        group = grouped.setdefault(group_key, {"rotation": rotation if rotation.strip() else MISSING_ROTATION,
                                              "rotation_missing": not bool(rotation.strip()), "evaluations": []})
        group["evaluations"].append(evaluation)
    rotations = sorted(grouped.values(), key=lambda group: (group["rotation_missing"], normalize_key(group["rotation"])))
    for group in rotations:
        group["evaluations"].sort(key=lambda value: (value["date"], value["id"]), reverse=True)
    undated.sort(key=lambda value: (normalize_key(value["rotation"]), value["id"]))
    counts.update({
        "evaluations_with_comments": len(included),
        "feedback_items": sum(len(e["feedback"]) for e in included),
        "resident_comment_items": sum(len(e["resident_comments"]) for e in included),
        "undated_evaluations": len(undated),
        "undated_feedback_items": sum(len(e["feedback"]) for e in undated),
        "undated_resident_comment_items": sum(len(e["resident_comments"]) for e in undated),
        "missing_rotation_evaluations": sum(not e["rotation"].strip() for e in included),
        "session_date_fallback_evaluations": sum(e["date_basis"] == "session_date" for e in included),
        "duplicate_fields_suppressed": sum(e["duplicate_fields_suppressed"] for e in included + undated),
    })
    notes = []
    if undated:
        notes.append(f"{len(undated)} evaluation(s) with comments have no usable date and are shown separately. Their reporting period cannot be confirmed.")
    if counts["session_date_fallback_evaluations"]:
        notes.append(f"{counts['session_date_fallback_evaluations']} evaluation(s) use session date because completion date is unavailable.")
    if counts["missing_rotation_evaluations"]:
        notes.append(f"{counts['missing_rotation_evaluations']} evaluation(s) have no saved rotation.")
    return {
        "resident_key": key, "start_date": start.isoformat() if start else None,
        "end_date": end.isoformat() if end else None, "rotations": rotations,
        "undated": undated, "counts": counts, "notes": notes,
    }
