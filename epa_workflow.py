"""Numeric-first export review. No network access, UI, or patient data.

The displayed numeric averages are never passed off as valid Data Hub categories.
Only the program's explicitly confirmed conversion may create upload codes.
"""
from __future__ import annotations

import copy
import math
from typing import Any

import epa_appd as appd
from epa_core import clean_text, utc_now

REVIEW_NAMESPACE = "appd_numeric_reviews"
WORKFLOW_VERSION = "numeric-review-v1"


def numeric_rows(draft: dict) -> list[dict]:
    """Automatically carry full-precision calculated averages to the review table."""
    metrics = {(r["resident_key"], r["APPD EPA"]): r for r in draft["summary"]}
    result = []
    for source in draft["rows"]:
        row = {key: source.get(key) for key in (
            "resident_key", "Resident", "Include", "name_or_program_assigned_ID", "assessmentdate", "pgy")}
        for epa in appd.EPA_COLUMNS:
            row[epa] = metrics.get((row["resident_key"], epa), {}).get("Average")
        result.append(row)
    return result


def numeric_score(value: Any) -> float | None:
    if value is None or str(value).strip().lower() in {"", "nan", "none", "<na>", "nat"}:
        return None
    if isinstance(value, bool):
        raise ValueError("Enter a numeric score, not a checkbox value.")
    try:
        score = float(value)
    except (TypeError, ValueError):
        raise ValueError("Enter a numeric average. Local letter labels are no longer used.") from None
    if not math.isfinite(score):
        raise ValueError("Scores must be finite numbers.")
    return score


def normalize_rows(state: dict, rows: list[dict]) -> list[dict]:
    """Normalize metadata without rounding numeric scores or inferring zeros."""
    metadata = appd.apply_start_date_pgys(state, rows)
    result = []
    for source, row in zip(rows, metadata):
        for epa in appd.EPA_COLUMNS:
            try:
                row[epa] = numeric_score(source.get(epa))
            except ValueError as exc:
                raise ValueError(f"{row['Resident']}, {epa}: {exc}") from None
        result.append(row)
    return result


def saved_review(state: dict, config: dict) -> dict | None:
    saved = state.get("settings", {}).get(REVIEW_NAMESPACE, {}).get(appd.review_key(config))
    if (saved and saved.get("workflow_version") == WORKFLOW_VERSION
            and saved.get("input_fingerprint") == appd.input_fingerprint(state, config)):
        return copy.deepcopy(saved)
    return None


def prepare_export(state: dict, config: dict, rows: list[dict], *, draft: dict | None = None) -> dict:
    """Return validated upload codes plus actionable blockers, never invalid XLSX."""
    appd.validate_config(config)
    draft = draft or appd.build_draft(state, config)
    normalized = normalize_rows(state, rows)
    issues: list[str] = []
    expected = {row["resident_key"] for row in draft["rows"]}
    actual = [row["resident_key"] for row in normalized]
    if len(set(actual)) != len(actual) or set(actual) != expected:
        issues.append("Resident rows changed. Reload the current export review.")
    if not draft["coverage_ready"]:
        issues.append("Admin > Forms and EPA mapping: complete the scoring and mapping checks before export.")
    if state.get("conflicts"):
        issues.append("Admin > Data review: resolve pending duplicate or corrected evaluations before export.")
    if any("evaluations have no usable" in warning for warning in draft.get("warnings", [])):
        issues.append("Admin > Data review: evaluations with missing dates were excluded. Resolve those dates before export.")
    metrics = {(r["resident_key"], r["APPD EPA"]): r for r in draft["summary"]}
    codes, changes = [], []
    needs_conversion = False
    for row in normalized:
        output = copy.deepcopy(row)
        for epa in appd.EPA_COLUMNS:
            score = row[epa]
            evidence = metrics.get((row["resident_key"], epa), {})
            average = evidence.get("Average")
            if score is None:
                output[epa] = "0" if average is None and draft["coverage_ready"] else ""
                if row["Include"] and average is not None:
                    issues.append(f"{row['Resident']}, {epa}: a calculated average was cleared. Restore a numeric value or exclude this resident.")
            else:
                output[epa] = appd.suggested_level(score, config)
                if row["Include"] and not output[epa]:
                    if not (config.get("conversion_enabled") and config.get("conversion_confirmed")):
                        needs_conversion = True
                    else:
                        issues.append(f"{row['Resident']}, {epa}: {score:g} is outside the saved Data Hub conversion ranges.")
            if row["Include"] and score != average:
                changes.append({"Resident": row["Resident"], "EPA": epa,
                                "Calculated average": average, "Reviewed numeric value": score})
        codes.append(output)
    if needs_conversion:
        issues.insert(0, "Admin > Data Hub rules: confirm the program's numeric-to-supervision conversion once. Decimal averages alone are not documented Data Hub upload values.")
    # Use existing schema validation for identities, dates, PGYs, and code choices.
    for issue in appd.validate_review(codes, config):
        if ": choose a final level for " not in issue:
            issues.append(issue.replace("enter a PGY from 1 to 15", "set a July 1 start date under Admin > Residents, or enter PGY"))
    return {"numeric_rows": normalized, "upload_rows": codes,
            "issues": list(dict.fromkeys(issues)), "changes": changes,
            "not_observed": sum(r["Include"] and r[epa] == "0" for r in codes for epa in appd.EPA_COLUMNS)}


def save_numeric_review(state: dict, config: dict, rows: list[dict], expected_fingerprint: str,
                        *, approved: bool, reviewer: str = "", expected_saved: str | None = None) -> dict:
    """Save numeric and upload snapshots atomically in the encrypted archive."""
    if appd.input_fingerprint(state, appd.config_for(state)) != expected_fingerprint:
        raise ValueError("Evaluations or setup changed. Reload the export review before saving.")
    current = state.get("settings", {}).get(REVIEW_NAMESPACE, {}).get(appd.review_key(config))
    if expected_saved is not None and appd.digest(current) != expected_saved:
        raise ValueError("Another user saved this review. Reload before saving your changes.")
    normalized = normalize_rows(state, rows)
    draft = appd.build_draft(state, config)
    keys = [r["resident_key"] for r in normalized]
    if len(set(keys)) != len(keys) or set(keys) != {r["resident_key"] for r in draft["rows"]}:
        raise ValueError("The resident rows changed. Reload the current export review.")
    outcome = prepare_export(state, config, normalized, draft=draft)
    if approved:
        if outcome["issues"]:
            raise ValueError(outcome["issues"][0])
        if not clean_text(reviewer):
            raise ValueError("Enter the reviewer name or initials.")
    updated = copy.deepcopy(state)
    # Retain the proven APPD writer and its approval checks, but never feed it decimals.
    updated = appd.save_review(updated, config, outcome["upload_rows"], expected_fingerprint,
                               approved=approved, reviewer=reviewer)
    updated.setdefault("settings", {}).setdefault(REVIEW_NAMESPACE, {})[appd.review_key(config)] = {
        "workflow_version": WORKFLOW_VERSION, "input_fingerprint": expected_fingerprint,
        "rows": normalized, "approved": bool(approved), "reviewer": clean_text(reviewer),
        "saved_at": utc_now(), "config": copy.deepcopy(config), "overrides": outcome["changes"],
    }
    return updated


def ready_review(state: dict, config: dict, rows: list[dict] | None = None) -> dict | None:
    saved = saved_review(state, config)
    official = appd.approved_review(state, config)
    if not saved or not saved.get("approved") or not official:
        return None
    if rows is not None and appd.digest(normalize_rows(state, rows)) != appd.digest(saved["rows"]):
        return None
    outcome = prepare_export(state, config, saved["rows"])
    if outcome["issues"] or appd.digest(appd.normalize_review_rows(outcome["upload_rows"])) != appd.digest(official["rows"]):
        return None
    return saved


def download_bytes(store, signature: str, expected_review: str) -> bytes:
    """Recheck the shared archive at download time, not only when the page opened."""
    latest, _ = store.load()
    if latest is None:
        raise ValueError("Saved data is unavailable. Reload the app before downloading.")
    config = appd.config_for(latest)
    if appd.input_fingerprint(latest, config) != signature:
        raise ValueError("Saved data changed. Reload and review before downloading.")
    saved = ready_review(latest, config)
    if saved is None or appd.digest(saved) != expected_review:
        raise ValueError("The shared review changed. Reload before downloading.")
    return appd.workbook_bytes(latest, config)
