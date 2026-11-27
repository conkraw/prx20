"""Saved competency PDFs and explicit links to existing resident evaluations.

Imports are separate from EPA scores, mappings, roster, and APPD approvals.
The enclosing archive supplies encryption and transactional persistence.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import re
from collections import Counter
from typing import Any

from epa_appd import digest, residents_in
from epa_core import normalize_key, utc_now
from epa_pgy import as_date

NAMESPACE = "competency_reports"
REPORT_VERSION = 1


def saved_reports(state: dict) -> dict[str, dict]:
    value = state.get(NAMESPACE, {})
    if not isinstance(value, dict):
        raise ValueError("Saved competency reports could not be read. Reload saved data before continuing.")
    return value


def report_id(source_sha256: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{64}", str(source_sha256)):
        raise ValueError("The report's source fingerprint is invalid. Upload the PDF again.")
    return "competency_" + source_sha256


def _name_key(value: str) -> str:
    """Handle an explicit Last, First form, but do not guess initials or aliases."""
    value = normalize_key(value)
    if value.count(",") == 1:
        last, first = (part.strip() for part in value.split(",", 1))
        if last and first:
            return first + " " + last
    return value


def matching_residents(state: dict, source_name: str) -> list[str]:
    """List exact name/APPD-ID matches without choosing between aliases."""
    source = _name_key(source_name)
    if not source:
        return []
    matches = set()
    roster = state.get("settings", {}).get("appd_roster", {})
    for key, name in residents_in(state).items():
        row = roster.get(key, {})
        aliases = [key, name, row.get("resident", ""), row.get("export_id", ""),
                   row.get("name_or_program_assigned_ID", "")]
        if any(source == _name_key(str(alias)) for alias in aliases if alias):
            matches.add(key)
    return sorted(matches)


def suggested_resident(state: dict, source_name: str) -> str | None:
    """Return a unique exact name/APPD-ID match, including Last, First order."""
    matches = matching_residents(state, source_name)
    return matches[0] if len(matches) == 1 else None


def _first_and_family_name(value: str) -> tuple[str, str] | None:
    """Conservative name parts for manual suggestions, never for linking."""
    value = normalize_key(value)
    if value.count(",") == 1:
        family, given = (part.strip() for part in value.split(",", 1))
        given_parts = given.split()
    elif "," not in value:
        parts = value.split()
        if len(parts) < 2:
            return None
        given_parts, family = parts[:-1], parts[-1]
    else:
        return None
    if not given_parts or not family:
        return None
    first = given_parts[0]
    # Initials are not expanded into a full given name. Punctuation such as
    # apostrophes and hyphens remains significant in both comparison fields.
    if "." in first or sum(character.isalpha() for character in first) < 2:
        return None
    if sum(character.isalpha() for character in family) < 2:
        return None
    if any(character.isdigit() for character in first + family):
        return None
    return first, family


def resident_link_candidates(state: dict, source_name: str) -> list[dict]:
    """Suggest saved residents for an admin to inspect and choose explicitly.

    This helper never changes a saved link and is not used by exact automatic
    matching. Same first/family names with differing middle names are suggestions
    only, including when there is just one candidate. Approved-review aliases
    are also manual evidence because an old export name might have been reused.
    """
    source = _name_key(source_name)
    if not source:
        return []
    names = residents_in(state)
    counts = Counter(
        normalize_key(record.get("resident_key") or record.get("resident"))
        for record in state.get("evaluations", {}).values()
    )
    candidates = {}

    def add(key: str, reason: str, priority: int) -> None:
        if key not in names:
            return
        previous = candidates.get(key)
        if previous is None or priority < previous["priority"]:
            candidates[key] = {
                "resident_key": key, "resident": names[key], "reason": reason,
                "evaluations": counts[normalize_key(key)], "priority": priority,
            }

    for key in matching_residents(state, source_name):
        add(key, "Exact saved name or APPD ID", 0)

    settings = state.get("settings", {})
    for namespace in ("appd_numeric_reviews", "appd_reviews"):
        reviews = settings.get(namespace, {})
        if not isinstance(reviews, dict):
            continue
        for review in reviews.values():
            if not isinstance(review, dict) or review.get("approved") is not True:
                continue
            rows = review.get("rows", [])
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict) or row.get("Include") is not True:
                    continue
                key = row.get("resident_key")
                alias = row.get("name_or_program_assigned_ID")
                if isinstance(key, str) and alias and source == _name_key(str(alias)):
                    add(key, "Exact name or ID in a saved approved APPD review", 1)

    parts = _first_and_family_name(source_name)
    if parts:
        roster = settings.get("appd_roster", {})
        for key, name in names.items():
            row = roster.get(key, {})
            saved_names = [name, row.get("resident", "")]
            if any(_first_and_family_name(str(value)) == parts for value in saved_names if value):
                add(key, "Same first and last name, with a different or omitted middle name", 2)

    ordered = sorted(candidates.values(), key=lambda row: (
        row["priority"], normalize_key(row["resident"]), row["resident_key"],
    ))
    return [{key: value for key, value in row.items() if key != "priority"} for row in ordered]


def linked_report(parsed: dict, pdf_bytes: bytes, *, resident_key: str,
                  resident_name: str, start_date: Any, end_date: Any,
                  date_basis: str = "completed_at") -> dict:
    """Build a reviewable saved entry from parsed source data and explicit choices."""
    if not resident_key or not str(resident_name).strip():
        raise ValueError("Choose the resident for this report.")
    start, end = as_date(start_date), as_date(end_date)
    if start is None or end is None or start > end:
        raise ValueError("Choose a valid comment date range, with the start on or before the end.")
    if date_basis not in ("completed_at", "session_date"):
        raise ValueError("Choose Session Date or Date Completed for the comment period.")
    fingerprint = hashlib.sha256(pdf_bytes).hexdigest()
    if fingerprint != parsed.get("source_sha256"):
        raise ValueError("The PDF changed after it was read. Upload it again.")
    from epa_competency_import import EXPECTED_CODES
    scores = parsed.get("scores", [])
    if [row.get("code") for row in scores] != list(EXPECTED_CODES):
        raise ValueError("The competency table is incomplete. Upload a supported original APPD report.")
    if any(not isinstance(row.get("score"), str) or not row["score"].strip() for row in scores):
        raise ValueError("A competency value is missing. Upload the original report again.")
    entry = {key: copy.deepcopy(parsed.get(key)) for key in (
        "source_filename", "source_sha256", "resident", "program", "season", "pgy", "scores", "warnings")}
    entry.update(
        id=report_id(fingerprint), format_version=REPORT_VERSION,
        source_pdf_b64=base64.b64encode(pdf_bytes).decode("ascii"),
        resident_key=str(resident_key), linked_resident=str(resident_name).strip(),
        comment_start=start.isoformat(), comment_end=end.isoformat(),
        comment_date_basis=date_basis,
    )
    return entry


def save_report_links(state: dict, entries: list[dict], expected: dict[str, str],
                      *, saved_at: str | None = None) -> tuple[dict, str]:
    """Atomically add/relink reports without replacing a colleague's changes.

The same PDF has one record. Changed PDFs are retained as separate reports.
Call via the app's existing commit function so unrelated concurrent data survives.
"""
    if not entries:
        raise ValueError("Upload at least one readable report.")
    current = saved_reports(state)
    proposed: dict[str, dict] = {}
    timestamp = saved_at or utc_now()
    for entry in entries:
        rid = report_id(entry.get("source_sha256", ""))
        if entry.get("id") != rid or entry.get("format_version") != REPORT_VERSION:
            raise ValueError("This competency report has an unsupported format. Upload the PDF again.")
        if rid in proposed:
            if proposed[rid] != entry:
                raise ValueError("The same PDF has different resident or date choices. Keep one copy in the upload.")
            continue
        proposed[rid] = copy.deepcopy(entry)
        if rid not in expected or digest(current.get(rid)) != expected[rid]:
            raise ValueError("This report changed in another session. Reload saved data and review its resident and dates.")
        start, end = as_date(entry.get("comment_start")), as_date(entry.get("comment_end"))
        if start is None or end is None or start > end or not entry.get("resident_key"):
            raise ValueError("Choose a resident and valid comment dates before saving.")
        if entry.get("comment_date_basis", "completed_at") not in ("completed_at", "session_date"):
            raise ValueError("Choose Session Date or Date Completed for the comment period.")
        original_pdf(entry)
    updated = copy.deepcopy(state)
    target = updated.setdefault(NAMESPACE, {})
    changed = 0
    for rid, entry in proposed.items():
        old = current.get(rid, {})
        comparable = {key: value for key, value in old.items() if key not in {"saved_at", "updated_at"}}
        candidate = {key: value for key, value in entry.items() if key not in {"saved_at", "updated_at"}}
        if comparable == candidate:
            continue
        entry["saved_at"] = old.get("saved_at", timestamp)
        entry["updated_at"] = timestamp
        target[rid] = entry
        changed += 1
    message = (f"Saved {changed} competency report" + ("s." if changed != 1 else ".")) if changed else "These reports are already saved with the same resident and dates."
    return updated, message


def original_pdf(report: dict) -> bytes:
    try:
        data = base64.b64decode(report.get("source_pdf_b64", ""), validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("The original PDF could not be read. Upload the source report again.") from exc
    if not data or hashlib.sha256(data).hexdigest() != report.get("source_sha256"):
        raise ValueError("The saved PDF does not match its source fingerprint. Upload the source report again.")
    return data


def report_filename(report: dict) -> str:
    name = re.sub(r"[^\w.-]+", "_", str(report.get("linked_resident") or report.get("resident") or "Resident"), flags=re.UNICODE).strip("._")
    return f"{name[:100] or 'Resident'}_competency_comments_{report.get('comment_start', '')}_to_{report.get('comment_end', '')}.pdf"


def report_label(report: dict) -> str:
    name = report.get("linked_resident") or report.get("resident") or "Resident"
    season = report.get("season") or "Season not listed"
    pgy = f"PGY{report['pgy']}" if report.get("pgy") else "PGY not listed"
    return f"{name} | {season} {pgy} | {report.get('comment_start', '')} to {report.get('comment_end', '')} | {str(report.get('source_sha256', ''))[:6]}"
