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
from datetime import datetime, timezone
from typing import Any

from epa_appd import digest, residents_in
from epa_core import normalize_key, utc_now
from epa_pgy import as_date

NAMESPACE = "competency_reports"
LINKS_NAMESPACE = "competency_resident_links"
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


def _approved_alias_matches(state: dict, source_name: str, names: dict) -> list[str]:
    source = _name_key(source_name)
    matches = set()
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
                if isinstance(key, str) and key in names and alias and source == _name_key(str(alias)):
                    matches.add(key)
    return sorted(matches)


def _current_exact_matches(state: dict, source_name: str, names: dict) -> list[str]:
    """Match current names and explicit IDs without reviving stale name keys."""
    source = _name_key(source_name)
    roster = state.get("settings", {}).get("appd_roster", {})
    result = []
    for key, name in names.items():
        row = roster.get(key, {})
        aliases = [name, row.get("export_id", ""), row.get("name_or_program_assigned_ID", "")]
        if any(source == _name_key(str(alias)) for alias in aliases if alias):
            result.append(key)
    return sorted(result)


def _first_and_family_name(value: str) -> tuple[str, str] | None:
    """Conservative name parts shared by suggestions and validated matching."""
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

    This helper never changes a saved link or selects a target. It deliberately
    includes broader middle-name suggestions for manual review. The chooser
    applies stricter uniqueness and middle-name compatibility rules separately.
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
    for key in _approved_alias_matches(state, source_name, names):
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


def _remembered_links(state: dict) -> dict:
    links = state.get(LINKS_NAMESPACE, {})
    if not isinstance(links, dict):
        raise ValueError("Remembered resident links could not be read. Reload saved data before continuing.")
    return links


def resident_link_expectations(state: dict, source_names) -> dict[str, str]:
    """Snapshot alias records for the optional save_report_links guard."""
    if isinstance(source_names, str):
        source_names = [source_names]
    current = _remembered_links(state)
    keys = {_name_key(str(name or "")) for name in source_names}
    return {key: digest(current.get(key)) for key in sorted(keys) if key}


def _valid_remembered_link(entry, source: str, names: dict) -> bool:
    if not isinstance(entry, dict):
        return False
    key = entry.get("resident_key")
    if not isinstance(key, str) or key not in names:
        return False
    target = _name_key(names[key])
    return (
        entry.get("source_name_key") == source
        and _name_key(str(entry.get("source_name") or "")) == source
        and entry.get("resident_name_key") == target
        and _name_key(str(entry.get("resident_name") or "")) == target
    )


def _stable_report_link(report, source: str, names: dict) -> str | None:
    if not isinstance(report, dict) or _name_key(str(report.get("resident") or "")) != source:
        return None
    key = report.get("resident_key")
    if not isinstance(key, str) or key not in names:
        return None
    if _name_key(str(report.get("linked_resident") or "")) != _name_key(names[key]):
        return None
    return key


def _report_link_time(report: dict) -> datetime | None:
    value = report.get("updated_at") or report.get("saved_at")
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _middle_names(value: str) -> tuple[str, ...] | None:
    normalized = normalize_key(value)
    if _first_and_family_name(normalized) is None:
        return None
    given = normalized.split(",", 1)[1].split() if "," in normalized else normalized.split()[:-1]
    middle = tuple(part.rstrip(".") for part in given[1:])
    if any(part in {"jr", "sr", "ii", "iii", "iv"} for part in middle):
        return None
    if any(not part.isalpha() for part in middle):
        return None
    return middle


def _compatible_name(source_name: str, resident_name: str) -> bool:
    """Accept omitted middle names and matching initials, never contradictions."""
    source_parts = _first_and_family_name(source_name)
    if source_parts is None or source_parts != _first_and_family_name(resident_name):
        return False
    source_middle, target_middle = _middle_names(source_name), _middle_names(resident_name)
    if source_middle is None or target_middle is None:
        return False
    if not source_middle or not target_middle:
        return True

    def token_matches(left: str, right: str) -> bool:
        return left == right or (len(left) == 1 and right.startswith(left)) or (len(right) == 1 and left.startswith(right))

    # One form may omit a middle name. Retained middle names must still agree
    # in order, either as full words or as matching initials.
    shorter, longer = sorted((source_middle, target_middle), key=len)
    cursor = 0
    for token in shorter:
        while cursor < len(longer) and not token_matches(token, longer[cursor]):
            cursor += 1
        if cursor == len(longer):
            return False
        cursor += 1
    return True


def choose_resident_link(state: dict, source_name: str, *, existing_report=None) -> dict:
    """Choose a unique best dropdown default without writing state or links.

    Saved choices take precedence over name inference. Ambiguity at a stronger
    evidence level never falls through to a weaker match. Existing reports and
    remembered choices are reused only while their resident-name snapshot is
    still current. The UI must save an explicit selection to change report data.
    """
    source = _name_key(source_name)
    names = residents_in(state)
    candidates = resident_link_candidates(state, source_name)
    memory = _remembered_links(state).get(source) if source else None
    memory_valid = _valid_remembered_link(memory, source, names)
    invalidated = memory is not None and not memory_valid

    def result(key, method, reason):
        return {"resident_key": key, "resident": names.get(key, ""), "reason": reason,
                "method": method, "candidates": candidates, "remembered_invalidated": invalidated}

    if not source:
        return result(None, "none", "The report does not have a resident name to match.")
    if memory_valid:
        return result(memory["resident_key"], "remembered", "Uses your saved resident choice for this report name.")
    # Invalidated memory was the latest saved choice. Reusing older reports
    # here could revive a link that the administrator already corrected.
    # Historical fallback is only for reports saved before memory existed.
    key = _stable_report_link(existing_report, source, names) if not invalidated else None
    if key:
        return result(key, "existing_report", "Keeps the resident already saved for this report.")

    previous = ([(report, key) for report in saved_reports(state).values()
                 if (key := _stable_report_link(report, source, names)) is not None]
                if not invalidated else [])
    targets = {key for _, key in previous}
    if len(targets) == 1:
        return result(next(iter(targets)), "previous_report", "Uses the resident saved with an earlier report under this name.")
    if len(targets) > 1:
        dated = [(_report_link_time(report), key) for report, key in previous]
        if all(moment is not None for moment, _ in dated):
            latest = max(moment for moment, _ in dated)
            latest_targets = {key for moment, key in dated if moment == latest}
            if len(latest_targets) == 1:
                return result(next(iter(latest_targets)), "previous_report", "Uses the most recently saved resident choice for this report name.")
        return result(None, "ambiguous", "Earlier reports link this name to different residents. Choose the correct resident.")

    exact = _current_exact_matches(state, source_name, names)
    if len(exact) == 1:
        return result(exact[0], "exact", "Matches the saved resident name or APPD ID.")
    if len(exact) > 1:
        return result(None, "ambiguous", "More than one resident matches this saved name or ID. Choose the correct resident.")

    aliases = _approved_alias_matches(state, source_name, names)
    if len(aliases) == 1:
        return result(aliases[0], "approved_alias", "Matches the name or ID in a saved approved APPD review.")
    if len(aliases) > 1:
        return result(None, "ambiguous", "Saved APPD reviews use this name or ID for different residents. Choose the correct resident.")

    compatible = [key for key, name in names.items() if _compatible_name(source_name, name)]
    if len(compatible) == 1:
        return result(compatible[0], "compatible_name", "Matches the first and last name, allowing omitted middle names or matching middle initials.")
    if len(compatible) > 1:
        return result(None, "ambiguous", "More than one resident has a compatible name. Choose the correct resident.")
    return result(None, "none", "No confident resident match was found. Choose a resident or keep the report without linked evaluations.")


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
                      *, saved_at: str | None = None,
                      expected_links: dict[str, str] | None = None) -> tuple[dict, str]:
    """Atomically add/relink reports without replacing a colleague's changes.

The same PDF has one record. Changed PDFs are retained as separate reports.
Call via the app's existing commit function so unrelated concurrent data survives.
Pass resident_link_expectations from the displayed state as expected_links to
guard remembered choices shared by different PDF records.
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
    names = residents_in(state)
    current_links = _remembered_links(state)
    proposed_links = {}
    for entry in proposed.values():
        key = entry["resident_key"]
        source = _name_key(str(entry.get("resident") or ""))
        # A source-only placeholder is a valid saved report, but it must never
        # become a remembered association to an invented resident record.
        if key not in names or not source:
            continue
        if _name_key(str(entry.get("linked_resident") or "")) != _name_key(names[key]):
            raise ValueError("The resident name changed while this report was open. Reload saved data and check the resident link.")
        remembered = {
            "source_name": str(entry["resident"]).strip(), "source_name_key": source,
            "resident_key": key, "resident_name": names[key], "resident_name_key": _name_key(names[key]),
        }
        if source in proposed_links and proposed_links[source]["resident_key"] != key:
            raise ValueError("Reports with the same source name have different resident choices. Choose one consistent resident for that name before saving.")
        proposed_links[source] = remembered
    link_changes = {}
    for source, remembered in proposed_links.items():
        old = current_links.get(source)
        if _valid_remembered_link(old, source, names) and old["resident_key"] == remembered["resident_key"]:
            continue
        if expected_links is not None and (source not in expected_links or digest(old) != expected_links[source]):
            raise ValueError("The remembered resident choice changed in another session. Reload saved data and review the resident link before saving.")
        remembered["saved_at"] = old.get("saved_at", timestamp) if isinstance(old, dict) else timestamp
        remembered["updated_at"] = timestamp
        link_changes[source] = remembered
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
    if link_changes:
        updated.setdefault(LINKS_NAMESPACE, {}).update(link_changes)
    message = (f"Saved {changed} competency report" + ("s." if changed != 1 else ".")) if changed else "These reports are already saved with the same resident and dates."
    if not changed and link_changes:
        message = "Resident choice remembered. This report already has the same resident and dates."
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
