"""Parsing, identity, form matching, and reporting for EPA Tracker.

This module does not read secrets, contact GitHub, or write resident data.
Scores are used only after the form's question selection and score map are
explicitly confirmed. Unknown response codes are reported, never guessed.
"""

from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import math
import re
import unicodedata
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import PurePath
from typing import Any

import pandas as pd


SCHEMA_VERSION = 1
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
QUESTION_PATTERN = re.compile(
    r"^\[?\s*question\s+(\d+)\s+(id|tags|response|comment|drop\s*down\s*list)\s*\]?$",
    re.IGNORECASE,
)
SCORE_COLUMNS = [
    "record_id", "resident", "resident_key", "form_id", "epa", "question_id",
    "question", "question_order", "included", "raw_response", "score", "status",
    "session_date", "completed_at", "rotation",
]
QUESTION_SUMMARY_COLUMNS = [
    "resident", "resident_key", "form_id", "epa", "question_id", "question",
    "question_order", "average", "n", "n_missing", "n_excluded", "n_unmapped",
]
EPA_SUMMARY_COLUMNS = [
    "resident", "resident_key", "form_id", "epa", "average", "n_evaluations",
    "n_scored_evaluations", "n_ratings", "n_questions_scored", "n_questions_expected",
]


class ParseError(ValueError):
    """The uploaded file cannot be read without silently losing information."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return unicodedata.normalize("NFKC", str(value)).strip()


def normalize_key(value: Any) -> str:
    """Normalize formatting, without merging different people's names."""
    return " ".join(clean_text(value).split()).casefold()


def normalize_question_id(value: Any) -> str:
    text = clean_text(value)
    if re.fullmatch(r"\d+(?:\.0+)?", text):
        return str(int(Decimal(text)))
    return text


def normalize_code(value: Any) -> str:
    text = clean_text(value)
    if not text:
        return ""
    try:
        number = Decimal(text)
        if number.is_finite():
            if number == 0:
                return "0"
            return format(number.normalize(), "f")
    except InvalidOperation:
        pass
    return normalize_key(text)


def _digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def empty_state() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "forms": {},
        "evaluations": {},
        "conflicts": {},
        "settings": {"academic_year_start_month": 7},
        "updated_at": None,
    }


def _column_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", normalize_key(value))


def _read_tab(data: bytes) -> tuple[list[str], list[dict], list[str]]:
    if not data:
        raise ParseError("The file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ParseError("This file is larger than 50 MB. Export a smaller date range.")
    warnings = []
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        encodings = ["utf-16"]
    elif b"\x00" in data[:200]:
        encodings = ["utf-16-le", "utf-16-be"]
    else:
        encodings = ["utf-8-sig", "cp1252"]
    text = None
    for encoding in encodings:
        try:
            candidate = data.decode(encoding)
            if "\t" in candidate:
                text = candidate
                break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ParseError("This is not a readable tab-delimited export. Upload the original .tab file.")
    try:
        csv.field_size_limit(10 * 1024 * 1024)
        reader = csv.reader(io.StringIO(text, newline=""), delimiter="\t", strict=True)
        raw_rows = []
        for row in reader:
            if not row or not any(clean_text(cell) for cell in row):
                continue
            raw_rows.append((reader.line_num, row))
    except csv.Error as exc:
        raise ParseError(f"The tab file has malformed quoting near line {reader.line_num}.") from exc
    if len(raw_rows) < 2:
        raise ParseError("The file needs a header and at least one data row.")
    headers = [clean_text(cell).lstrip("\ufeff") for cell in raw_rows[0][1]]
    if not all(headers) or len(set(_column_key(h) for h in headers)) != len(headers):
        raise ParseError("The file contains a blank or duplicate column heading.")
    records = []
    for line_num, row in raw_rows[1:]:
        if len(row) != len(headers):
            raise ParseError(
                f"Line {line_num} has {len(row)} columns, but the header has {len(headers)}. "
                "No rows from this file were imported. Re-export the original tab file."
            )
        record = dict(zip(headers, row))
        record["__line_number__"] = line_num
        records.append(record)
    return headers, records, warnings


def _propose_inclusion(text: str) -> bool:
    """A reviewable starting point, never an automatic scoring decision."""
    text = normalize_key(text)
    excluded = (
        "how much time", "time did you spend", "maintaining professionalism",
        "clinical performance on the rotation", "contents of this evaluation discussed",
        "what specific next steps", "overall comments", "additional comments",
        "strengths", "areas for improvement", "areas of improvement",
    )
    return bool(text) and not any(phrase in text for phrase in excluded)


def form_id_for_questions(question_ids: list[str]) -> str:
    return "form_" + _digest(sorted(set(normalize_question_id(q) for q in question_ids)))[:24]


def _parse_form(headers: list[str], records: list[dict], filename: str) -> dict:
    columns = {_column_key(h): h for h in headers}
    id_column = columns.get("questionid")
    if id_column is None:
        raise ParseError("The form export is missing QuestionID.")
    part_columns = sorted(
        (h for h in headers if re.fullmatch(r"questionpart\d+", _column_key(h))),
        key=lambda h: int(re.search(r"\d+$", _column_key(h)).group()),
    )
    if not part_columns:
        part_columns = [columns[key] for key in ("questiontext", "question") if key in columns]
    if not part_columns:
        raise ParseError("The form export has no question-text columns.")
    seen = set()
    questions = []
    for row in records:
        question_id = normalize_question_id(row[id_column])
        if not question_id:
            raise ParseError(f"Line {row['__line_number__']} has a blank question ID.")
        if question_id in seen:
            raise ParseError(f"Question ID {question_id} appears more than once in the form export.")
        seen.add(question_id)
        text = "\n".join(clean_text(row[h]) for h in part_columns if clean_text(row[h]))
        questions.append({
            "id": question_id,
            "text": text or f"Question {question_id}",
            "tags": clean_text(row.get(columns.get("tags", ""), "")),
            "include": _propose_inclusion(text),
        })
    name = re.sub(r"[_-]+", " ", PurePath(filename).stem)
    name = re.sub(r"\b(form|questions?)\b", "", name, flags=re.IGNORECASE)
    name = " ".join(name.split()) or "New EPA"
    return {
        "id": form_id_for_questions([q["id"] for q in questions]),
        "name": name,
        "questions": questions,
        "score_map": {"1": 1.0, "2": 2.0, "3": 3.0, "4": 4.0, "5": 5.0, "6": None},
        "scoring_confirmed": False,
    }


def parse_form(data: bytes, filename: str = "New EPA.tab") -> dict:
    headers, records, _ = _read_tab(data)
    return _parse_form(headers, records, filename)


def _parse_date(value: Any, *, with_time: bool) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    formats = (
        "%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %I:%M %p", "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %H:%M", "%m/%d/%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d",
        "%Y/%m/%d", "%m/%d/%y %I:%M:%S %p", "%m/%d/%y", "%m-%d-%Y",
    )
    parsed = None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        for fmt in formats:
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    if with_time:
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc)
        return parsed.isoformat(timespec="seconds")
    return parsed.date().isoformat()


def record_identity(record: dict) -> str:
    source_id = normalize_key(record.get("source_evaluation_id", ""))
    if source_id:
        return "eval_" + _digest({"source_evaluation_id": source_id})
    natural = {
        "resident": normalize_key(record.get("resident_key", record.get("resident", ""))),
        "evaluator": normalize_key(record.get("evaluator_key", record.get("evaluator", ""))),
        "rotation": normalize_key(record.get("rotation", "")),
        "session_date": record.get("session_date") or normalize_key(record.get("session_date_raw", "")),
        "completed_at": record.get("completed_at") or normalize_key(record.get("completed_at_raw", "")),
        "question_ids": sorted(record.get("responses", {})),
    }
    return "eval_" + _digest(natural)


def _near_identity(record: dict) -> str:
    """Candidate grouping for edited timestamps and partial question exports."""
    return _digest({
        "resident": normalize_key(record.get("resident_key", record.get("resident", ""))),
        "evaluator": normalize_key(record.get("evaluator_key", record.get("evaluator", ""))),
        "rotation": normalize_key(record.get("rotation", "")),
        "session_date": record.get("session_date") or normalize_key(record.get("session_date_raw", "")),
    })


def _possibly_same_evaluation(first: dict, second: dict, forms: dict) -> bool:
    first_source, second_source = first.get("source_evaluation_id"), second.get("source_evaluation_id")
    if first_source and second_source and first_source != second_source:
        return False
    first_ids, second_ids = set(first.get("responses", {})), set(second.get("responses", {}))
    if first_ids == second_ids:
        return True
    if not first_ids or not second_ids or not (first_ids < second_ids or second_ids < first_ids):
        return False
    first_form, second_form = match_form(first, forms), match_form(second, forms)
    if first_form["form_id"] and second_form["form_id"] and first_form["form_id"] != second_form["form_id"]:
        return False
    # An incomplete export might be the same evaluation. Keep it out of the
    # scored archive until the administrator makes that distinction.
    return True


def record_content_hash(record: dict) -> str:
    canonical = {
        "resident": normalize_key(record.get("resident", "")),
        "evaluator": normalize_key(record.get("evaluator", "")),
        "evaluator_status": normalize_key(record.get("evaluator_status", "")),
        "rotation": normalize_key(record.get("rotation", "")),
        "session_date": record.get("session_date") or normalize_key(record.get("session_date_raw", "")),
        "completed_at": record.get("completed_at") or normalize_key(record.get("completed_at_raw", "")),
        "overall_comments": normalize_key(record.get("overall_comments", "")),
        "subject_comments": normalize_key(record.get("subject_comments", "")),
        "responses": {
            qid: {
                "response": normalize_code(answer.get("response", "")),
                "comment": normalize_key(answer.get("comment", "")),
                "dropdown": normalize_key(answer.get("dropdown", "")),
                "tags": normalize_key(answer.get("tags", "")),
            }
            for qid, answer in sorted(record.get("responses", {}).items())
        },
    }
    return _digest(canonical)


def _parse_evaluations(headers: list[str], rows: list[dict], filename: str) -> tuple[list[dict], list[str]]:
    columns = {_column_key(h): h for h in headers}
    if "subjectname" not in columns:
        raise ParseError("The evaluation export is missing Subject Name.")
    question_columns = {}
    for header in headers:
        match = QUESTION_PATTERN.match(header)
        if match:
            question_columns.setdefault(int(match.group(1)), {})[
                re.sub(r"\s+", "", match.group(2)).lower()
            ] = header
    if not any("id" in parts for parts in question_columns.values()):
        raise ParseError("The evaluation export has no [Question N ID] columns.")
    result = []
    warnings = []
    for row in rows:
        line_num = row["__line_number__"]
        get = lambda key: clean_text(row.get(columns.get(key, ""), ""))
        resident = get("subjectname")
        if not resident:
            raise ParseError(f"Line {line_num} has no resident name. No rows from this file were imported.")
        responses = {}
        for number, parts in sorted(question_columns.items()):
            question_id = normalize_question_id(row.get(parts.get("id", ""), ""))
            fields = {
                "response": clean_text(row.get(parts.get("response", ""), "")),
                "comment": clean_text(row.get(parts.get("comment", ""), "")),
                "dropdown": clean_text(row.get(parts.get("dropdownlist", ""), "")),
                "tags": clean_text(row.get(parts.get("tags", ""), "")),
            }
            if not question_id:
                if any(fields.values()):
                    raise ParseError(
                        f"Line {line_num}, question position {number}, contains an answer but no question ID. "
                        "No rows from this file were imported."
                    )
                continue
            if question_id in responses:
                raise ParseError(f"Line {line_num} repeats question ID {question_id}.")
            responses[question_id] = fields
        if not responses:
            raise ParseError(f"Line {line_num} has no question IDs. No rows from this file were imported.")
        session_raw, completed_raw = get("sessiondate"), get("datecompleted")
        session_date = _parse_date(session_raw, with_time=False)
        completed_at = _parse_date(completed_raw, with_time=True)
        if session_date is None:
            warnings.append(f"Line {line_num}: session date is missing or invalid. This evaluation needs a date for session-month reporting.")
        if completed_at is None:
            warnings.append(f"Line {line_num}: completion date is missing or invalid. This evaluation cannot be placed in a completion month.")
        source_id = next((get(key) for key in ("evaluationid", "evaluationrecordid", "evaluationresponseid") if get(key)), "")
        record = {
            "resident": resident,
            "resident_key": normalize_key(resident),
            "evaluator": get("evaluatorname"),
            "evaluator_key": normalize_key(get("evaluatorname")),
            "evaluator_status": get("evaluatorstatus"),
            "rotation": get("subjectrotation"),
            "session_date": session_date,
            "completed_at": completed_at,
            "session_date_raw": session_raw,
            "completed_at_raw": completed_raw,
            "responses": responses,
            "overall_comments": get("overallcomments"),
            "subject_comments": get("subjectcomments"),
            "source_files": [PurePath(filename).name],
        }
        # PGY is accepted only from an explicit resident/subject PGY column.
        # Never infer a numeric year from "Intern" or "Senior" rotation labels.
        pgy_aliases = ("subjectpgy", "residentpgy", "pgy", "postgraduateyear", "postgraduationyear",
                       "subjectpostgraduateyear", "subjecttrainingyear", "trainingyear", "residencyyear", "pgylevel")
        for pgy_alias in pgy_aliases:
            if pgy_alias in columns and get(pgy_alias):
                pgy_text = get(pgy_alias)
                record["pgy_raw"] = pgy_text
                record["pgy_source_field"] = columns[pgy_alias]
                pgy_match = re.fullmatch(r"(?:PGY\s*[-:]?\s*)?(\d{1,2})(?:\.0+)?", pgy_text, re.I)
                if pgy_match and 1 <= int(pgy_match.group(1)) <= 15:
                    record["resident_pgy"] = int(pgy_match.group(1))
                else:
                    warnings.append(f"Line {line_num}: PGY could not be read. Review the APPD resident roster.")
                break
        if source_id:
            record["source_evaluation_id"] = source_id
        record["id"] = record_identity(record)
        record["content_hash"] = record_content_hash(record)
        result.append(record)
    return result, warnings


def parse_upload(data: bytes, filename: str) -> dict:
    headers, rows, warnings = _read_tab(data)
    columns = {_column_key(h) for h in headers}
    if "questionid" in columns and "subjectname" not in columns:
        return {"kind": "form", "form": _parse_form(headers, rows, filename), "warnings": warnings}
    if "subjectname" in columns:
        evaluations, date_warnings = _parse_evaluations(headers, rows, filename)
        return {"kind": "evaluations", "evaluations": evaluations, "warnings": warnings + date_warnings}
    raise ParseError("This file is not a recognized question form or resident evaluation tab export.")


def match_form(record: dict, forms: dict) -> dict:
    ids = set(record.get("responses", {}))
    if not ids:
        return {"form_id": None, "status": "unknown", "candidates": []}
    question_sets = {fid: {q["id"] for q in form["questions"]} for fid, form in forms.items()}
    exact = sorted(fid for fid, qids in question_sets.items() if ids == qids)
    if len(exact) == 1:
        return {"form_id": exact[0], "status": "exact", "candidates": exact}
    if len(exact) > 1:
        return {"form_id": None, "status": "ambiguous", "candidates": exact}
    partial = sorted(fid for fid, qids in question_sets.items() if ids < qids)
    if len(partial) == 1:
        return {"form_id": partial[0], "status": "partial", "candidates": partial}
    return {"form_id": None, "status": "ambiguous" if partial else "unknown", "candidates": partial}


def register_form(state: dict, form: dict, name: str | None = None) -> dict:
    new_state = copy.deepcopy(state)
    incoming = copy.deepcopy(form)
    incoming["id"] = form_id_for_questions([q["id"] for q in incoming["questions"]])
    if not incoming["questions"]:
        raise ValueError("A form needs at least one question.")
    if len({q["id"] for q in incoming["questions"]}) != len(incoming["questions"]):
        raise ValueError("A form cannot repeat question IDs.")
    previous = new_state["forms"].get(incoming["id"])
    if previous:
        old_questions = {q["id"]: q for q in previous["questions"]}
        wording_changed = any(
            normalize_key(question.get(field, ""))
            != normalize_key(old_questions.get(question["id"], {}).get(field, ""))
            for question in incoming["questions"] for field in ("text", "tags")
        )
        for question in incoming["questions"]:
            if question["id"] in old_questions:
                for field in ("include", "score_map"):
                    if field in old_questions[question["id"]]:
                        question[field] = copy.deepcopy(old_questions[question["id"]][field])
        incoming["name"] = name if name is not None else previous["name"]
        incoming["score_map"] = copy.deepcopy(previous["score_map"])
        incoming["scoring_confirmed"] = previous.get("scoring_confirmed", False) and not wording_changed
    elif name is not None:
        incoming["name"] = clean_text(name)
    if not clean_text(incoming.get("name")):
        raise ValueError("Enter an EPA name.")
    new_state["forms"][incoming["id"]] = incoming
    if new_state != state:
        new_state["updated_at"] = utc_now()
    return new_state


def ingest_evaluations(state: dict, records: list[dict]) -> tuple[dict, dict]:
    new_state = copy.deepcopy(state)
    stats = {"added": 0, "duplicates": 0, "conflicts": 0}
    candidate_index = {}
    for stored_id, stored_record in new_state["evaluations"].items():
        candidate_index.setdefault(_near_identity(stored_record), set()).add(stored_id)
    for source in records:
        incoming = copy.deepcopy(source)
        incoming["id"] = record_identity(incoming)
        incoming["content_hash"] = record_content_hash(incoming)
        proposed_identity = incoming["id"]
        identity = new_state.get("record_aliases", {}).get(proposed_identity, proposed_identity)
        incoming["id"] = identity
        previous = new_state["evaluations"].get(identity)
        kind = "correction"
        candidates = []
        if previous is None:
            candidates = sorted(
                old_id for old_id in candidate_index.get(_near_identity(incoming), set())
                if _possibly_same_evaluation(new_state["evaluations"][old_id], incoming, new_state["forms"])
            )
            if candidates:
                identity = candidates[0]
                previous = new_state["evaluations"][identity]
                kind = "possible_duplicate"
            else:
                new_state["evaluations"][identity] = incoming
                candidate_index.setdefault(_near_identity(incoming), set()).add(identity)
                stats["added"] += 1
                continue
        old_hash = previous.get("content_hash") or record_content_hash(previous)
        if old_hash == incoming["content_hash"]:
            previous["source_files"] = sorted(set(previous.get("source_files", []) + incoming.get("source_files", [])))
            # An enriched export must not create another evaluation. Fill missing
            # PGY metadata, but surface conflicting PGYs for manual resolution.
            new_pgy, old_pgy = incoming.get("resident_pgy"), previous.get("resident_pgy")
            if new_pgy is not None and old_pgy is not None and new_pgy != old_pgy:
                values = set(previous.get("pgy_metadata_conflicts", [])) | {old_pgy, new_pgy}
                previous["pgy_metadata_conflicts"] = sorted(values)
            elif new_pgy is not None and old_pgy is None:
                for field in ("resident_pgy", "pgy_raw", "pgy_source_field"):
                    if field in incoming:
                        previous[field] = copy.deepcopy(incoming[field])
            stats["duplicates"] += 1
            continue
        conflict_id = "conflict_" + _digest([identity, old_hash, incoming["content_hash"]])
        history = new_state.get("conflict_history", [])
        already_resolved = any(
            entry.get("conflict_id") == conflict_id and entry.get("action") == "keep"
            for entry in history
        )
        superseded = any(
            entry.get("record_id") == identity and entry.get("action") == "replace"
            and entry.get("previous_content_hash") == incoming["content_hash"]
            for entry in history
        )
        if already_resolved or superseded:
            stats["duplicates"] += 1
            continue
        new_state["conflicts"].setdefault(conflict_id, {
            "id": conflict_id, "existing_id": identity, "incoming": incoming,
            "existing_content_hash": old_hash, "detected_at": utc_now(),
            "kind": kind, "candidate_ids": candidates or [identity],
            "candidate_content_hashes": {
                candidate_id: new_state["evaluations"][candidate_id].get("content_hash")
                or record_content_hash(new_state["evaluations"][candidate_id])
                for candidate_id in (candidates or [identity])
            },
        })
        stats["conflicts"] += 1
    if new_state != state:
        new_state["updated_at"] = utc_now()
    return new_state, stats


def resolve_conflict(state: dict, conflict_id: str, action: str = "keep", target_id: str | None = None) -> dict:
    if action not in {"keep", "replace", "add"}:
        raise ValueError("Conflict action must be 'keep', 'replace', or 'add'.")
    new_state = copy.deepcopy(state)
    conflict = new_state["conflicts"].get(conflict_id)
    if conflict is None:
        raise ValueError("This conflict was already resolved. Reload the saved data.")
    if action == "add" and conflict.get("kind") != "possible_duplicate":
        raise ValueError("Only a possible duplicate with a different evaluation identity can be added separately.")
    candidates = conflict.get("candidate_ids", [conflict["existing_id"]])
    if target_id is not None and target_id not in candidates:
        raise ValueError("The selected evaluation is not a candidate for this conflict.")
    if action == "replace" and len(candidates) > 1 and target_id is None:
        raise ValueError("Several existing evaluations match. Select the exact evaluation to replace.")
    existing_id = target_id if action == "replace" and target_id else conflict["existing_id"]
    existing = new_state["evaluations"].get(existing_id)
    if existing is None:
        raise ValueError("The original evaluation no longer exists.")
    baseline_hash = conflict.get("candidate_content_hashes", {}).get(existing_id, conflict["existing_content_hash"])
    if action == "replace" and (existing.get("content_hash") or record_content_hash(existing)) != baseline_hash:
        raise ValueError("The original evaluation changed after this conflict was detected. Reload and review the latest version.")
    if action == "replace":
        replacement = copy.deepcopy(conflict["incoming"])
        replacement["source_files"] = sorted(set(existing.get("source_files", []) + replacement.get("source_files", [])))
        incoming_identity = record_identity(replacement)
        if incoming_identity != existing_id:
            new_state.setdefault("record_aliases", {})[incoming_identity] = existing_id
        replacement["id"] = existing_id
        new_state["evaluations"][existing_id] = replacement
        # Other pending alternatives now compare with the version just saved.
        # The UI renders their current existing_id record on the next refresh.
        for other_id, other in new_state["conflicts"].items():
            if other_id == conflict_id:
                continue
            if other["existing_id"] == existing_id:
                other["existing_content_hash"] = replacement["content_hash"]
            if existing_id in other.get("candidate_ids", []):
                other.setdefault("candidate_content_hashes", {})[existing_id] = replacement["content_hash"]
    elif action == "add":
        addition = copy.deepcopy(conflict["incoming"])
        addition["id"] = record_identity(addition)
        if addition["id"] in new_state["evaluations"]:
            raise ValueError("This evaluation is already stored. Reload the saved data.")
        new_state["evaluations"][addition["id"]] = addition
    new_state.setdefault("conflict_history", []).append({
        "conflict_id": conflict_id, "record_id": existing_id,
        "action": action, "resolved_at": utc_now(),
        "previous_content_hash": baseline_hash,
        "incoming_content_hash": conflict["incoming"]["content_hash"],
    })
    del new_state["conflicts"][conflict_id]
    new_state["updated_at"] = utc_now()
    return new_state


def _normalized_score_map(mapping: dict) -> dict:
    result = {}
    for code, value in mapping.items():
        key = normalize_code(code)
        if key in result and result[key] != value:
            raise ValueError(f"Scoring map has conflicting entries for response code {key}.")
        if value is not None:
            if isinstance(value, bool):
                raise ValueError("Scoring values must be numbers or excluded.")
            value = float(value)
            if not math.isfinite(value):
                raise ValueError("Scoring values must be finite numbers.")
        result[key] = value
    return result


def score_rows(state: dict) -> pd.DataFrame:
    result = []
    for record_id, record in state.get("evaluations", {}).items():
        match = match_form(record, state.get("forms", {}))
        if match["form_id"] is None:
            continue
        form = state["forms"][match["form_id"]]
        confirmed = form.get("scoring_confirmed", False)
        default_map = _normalized_score_map(form.get("score_map", {}))
        for order, question in enumerate(form["questions"], start=1):
            raw_response = record["responses"].get(question["id"], {}).get("response", "")
            code = normalize_code(raw_response)
            included = bool(question.get("include", False))
            score = None
            if not included:
                status = "not_epa"
            elif not code:
                status = "blank"
            elif not confirmed:
                status = "unconfirmed"
            else:
                mapping = _normalized_score_map(question["score_map"]) if question.get("score_map") is not None else default_map
                if code not in mapping:
                    status = "unmapped"
                elif mapping[code] is None:
                    status = "excluded"
                else:
                    score = mapping[code]
                    status = "scored"
            result.append({
                "record_id": record_id, "resident": record["resident"],
                "resident_key": record["resident_key"], "form_id": form["id"],
                "epa": form["name"], "question_id": question["id"],
                "question": question["text"], "question_order": order,
                "included": included, "raw_response": raw_response, "score": score,
                "status": status, "session_date": record.get("session_date"),
                "completed_at": record.get("completed_at"), "rotation": record.get("rotation", ""),
            })
    frame = pd.DataFrame(result, columns=SCORE_COLUMNS)
    frame["score"] = pd.to_numeric(frame["score"], errors="coerce")
    return frame


def filter_rows(
    rows: pd.DataFrame, academic_year: int | None = None, months: list[int] | None = None,
    start_month: int = 7, date_basis: str = "session_date", residents: list[str] | None = None,
    form_ids: list[str] | None = None,
) -> pd.DataFrame:
    if start_month not in range(1, 13):
        raise ValueError("The academic year start month must be 1–12.")
    if date_basis not in {"session_date", "completed_at"}:
        raise ValueError("Choose session_date or completed_at for the date basis.")
    if months is not None and any(month not in range(1, 13) for month in months):
        raise ValueError("Month filters must use calendar month numbers 1–12.")
    frame = rows.copy()
    dates = pd.to_datetime(frame[date_basis], errors="coerce", format="mixed", utc=True)
    frame["period"] = dates.dt.strftime("%Y-%m")
    frame["academic_month"] = ((dates.dt.month - start_month) % 12 + 1).astype("Int64")
    frame["academic_year"] = (dates.dt.year - (dates.dt.month < start_month).astype(int)).astype("Int64")
    if academic_year is not None:
        frame = frame.loc[frame["academic_year"].eq(int(academic_year)).fillna(False)]
    if months is not None:
        frame = frame.loc[dates.loc[frame.index].dt.month.isin(months)]
    if residents is not None:
        resident_keys = {normalize_key(value) for value in residents}
        frame = frame.loc[frame["resident_key"].isin(resident_keys)]
    if form_ids is not None:
        frame = frame.loc[frame["form_id"].isin(form_ids)]
    return frame.copy()


def _included(rows: pd.DataFrame) -> pd.DataFrame:
    if rows.empty:
        return rows.copy()
    return rows.loc[rows["included"].astype(bool)].copy()


def question_summary(rows: pd.DataFrame) -> pd.DataFrame:
    frame = _included(rows)
    if frame.empty:
        return pd.DataFrame(columns=QUESTION_SUMMARY_COLUMNS)
    result = []
    for (resident_key, form_id, question_id), group in frame.groupby(
        ["resident_key", "form_id", "question_id"], sort=False, dropna=False
    ):
        first = group.iloc[0]
        scored = group.loc[group["status"].eq("scored"), "score"].dropna()
        result.append({
            "resident": first["resident"], "resident_key": resident_key,
            "form_id": form_id, "epa": first["epa"], "question_id": question_id,
            "question": first["question"], "question_order": first["question_order"],
            "average": scored.mean() if len(scored) else float("nan"),
            "n": len(scored), "n_missing": int(group["status"].eq("blank").sum()),
            "n_excluded": int(group["status"].eq("excluded").sum()),
            "n_unmapped": int(group["status"].isin(["unmapped", "unconfirmed"]).sum()),
        })
    return pd.DataFrame(result, columns=QUESTION_SUMMARY_COLUMNS).sort_values(
        ["resident_key", "epa", "question_order"], kind="stable"
    ).reset_index(drop=True)


def epa_summary(rows: pd.DataFrame) -> pd.DataFrame:
    frame = _included(rows)
    if frame.empty:
        return pd.DataFrame(columns=EPA_SUMMARY_COLUMNS)
    result = []
    for (resident_key, form_id), group in frame.groupby(["resident_key", "form_id"], sort=False, dropna=False):
        questions = question_summary(group)
        first = group.iloc[0]
        scored = group.loc[group["status"].eq("scored") & group["score"].notna()]
        result.append({
            "resident": first["resident"], "resident_key": resident_key,
            "form_id": form_id, "epa": first["epa"],
            "average": questions["average"].mean(),
            "n_evaluations": int(group["record_id"].nunique()),
            "n_scored_evaluations": int(scored["record_id"].nunique()),
            "n_ratings": len(scored),
            "n_questions_scored": int(questions["average"].notna().sum()),
            "n_questions_expected": int(group["question_id"].nunique()),
        })
    return pd.DataFrame(result, columns=EPA_SUMMARY_COLUMNS).sort_values(
        ["resident_key", "epa"], kind="stable"
    ).reset_index(drop=True)


def monthly_summary(rows: pd.DataFrame, cumulative: bool = False) -> pd.DataFrame:
    columns = EPA_SUMMARY_COLUMNS + ["period", "academic_month", "academic_year"]
    if rows.empty:
        return pd.DataFrame(columns=columns)
    if not {"period", "academic_month", "academic_year"}.issubset(rows.columns):
        raise ValueError("Call filter_rows before monthly_summary to establish academic-year periods.")
    frame = rows.loc[rows["period"].notna() & rows["academic_year"].notna()]
    results = []
    for (_, _, _), group in frame.groupby(["resident_key", "form_id", "academic_year"], sort=False):
        for period in sorted(group["period"].unique()):
            current = group.loc[group["period"].eq(period)]
            selected = group.loc[group["period"].le(period)] if cumulative else current
            summary = epa_summary(selected)
            if summary.empty:
                continue
            summary["period"] = period
            summary["academic_month"] = int(current["academic_month"].iloc[0])
            summary["academic_year"] = int(current["academic_year"].iloc[0])
            results.append(summary)
    if not results:
        return pd.DataFrame(columns=columns)
    return pd.concat(results, ignore_index=True)[columns].sort_values(
        ["resident_key", "epa", "period"], kind="stable"
    ).reset_index(drop=True)
