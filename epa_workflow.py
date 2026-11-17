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
WORKFLOW_VERSION = "numeric-review-dual-export-v2"


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


def prepare_export(state: dict, config: dict, rows: list[dict], *, draft: dict | None = None,
                   accept_review_notes: bool = False) -> dict:
    """Separate incomplete-evidence notes from structurally invalid upload values.

    The new one-button workflow can accept review notes explicitly. Legacy
    callers retain the stricter default. Neither mode guesses missing PGYs,
    identities, dates, or numeric-to-category rules.
    """
    appd.validate_config(config)
    draft = draft or appd.build_draft(state, config)
    normalized = normalize_rows(state, rows)
    issues: list[str] = []
    notes: list[str] = list(draft.get("warnings", []))
    expected = {row["resident_key"] for row in draft["rows"]}
    actual = [row["resident_key"] for row in normalized]
    if len(set(actual)) != len(actual) or set(actual) != expected:
        issues.append("Resident rows changed. Reload the current export review.")
    review_issues = []
    if not draft["coverage_ready"]:
        review_issues.append("Some scoring or question-to-EPA mapping needs review. Only confirmed, valid scores are included. Blank EPA cells will upload as not observed (0).")
    if state.get("conflicts"):
        review_issues.append("Pending duplicate or corrected evaluations remain in Admin > Data review. The saved version is counted, not the pending alternative.")
    if any("evaluations have no usable" in warning for warning in draft.get("warnings", [])):
        review_issues.append("Evaluations with missing dates were excluded from this reporting period.")
    notes.extend(review_issues)
    if not accept_review_notes:
        issues.extend(review_issues)
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
                output[epa] = "0" if accept_review_notes or (average is None and draft["coverage_ready"]) else ""
                if row["Include"] and average is not None:
                    if accept_review_notes:
                        notes.append(f"{row['Resident']}, {epa}: the calculated average {average:g} was cleared and will upload as not observed (0).")
                    else:
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
        issues.insert(0, "APPD Export > Upload conversion parameters: set the program's score cutoffs. Data Hub rules must use supervision codes, not decimal averages.")
    # Use existing schema validation for identities, dates, PGYs, and code choices.
    for issue in appd.validate_review(codes, config):
        if ": choose a final level for " not in issue:
            issues.append(issue.replace("enter a PGY from 1 to 15", "set a July 1 start date under Admin > Residents, or enter PGY"))
    converted_zero = sum(
        bool(numeric["Include"]) and numeric[epa] is not None and converted[epa] == "0"
        for numeric, converted in zip(normalized, codes) for epa in appd.EPA_COLUMNS
    )
    if converted_zero:
        notes.append(
            f"{converted_zero} numeric EPA values convert to CBME 0 under the selected ranges. "
            "Data Hub interprets 0 as not observed. The raw file keeps the numeric values."
        )
    return {"numeric_rows": normalized, "upload_rows": codes,
            "issues": list(dict.fromkeys(issues)), "review_notes": list(dict.fromkeys(notes)), "changes": changes,
            "not_observed": sum(bool(r["Include"]) and r[epa] is None for r in normalized for epa in appd.EPA_COLUMNS),
            "converted_zero": converted_zero}


def save_numeric_review(state: dict, config: dict, rows: list[dict], expected_fingerprint: str,
                        *, approved: bool, reviewer: str = "", expected_saved: str | None = None,
                        approval_action: str = "", accept_review_notes: bool = False) -> dict:
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
    if accept_review_notes and approval_action != "approve_save_prepare":
        raise ValueError("Review notes can be accepted only through the explicit approval action.")
    outcome = prepare_export(state, config, normalized, draft=draft, accept_review_notes=accept_review_notes)
    if approved:
        if outcome["issues"]:
            raise ValueError(outcome["issues"][0])
        if not clean_text(reviewer) and approval_action != "approve_save_prepare":
            raise ValueError("Enter the reviewer name or initials.")
    updated = copy.deepcopy(state)
    # Retain the proven APPD writer and its approval checks, but never feed it decimals.
    updated = appd.save_review(updated, config, outcome["upload_rows"], expected_fingerprint,
                               approved=approved, reviewer=reviewer, approval_action=approval_action)
    updated.setdefault("settings", {}).setdefault(REVIEW_NAMESPACE, {})[appd.review_key(config)] = {
        "workflow_version": WORKFLOW_VERSION, "input_fingerprint": expected_fingerprint,
        "rows": normalized, "approved": bool(approved), "reviewer": clean_text(reviewer),
        "saved_at": utc_now(), "config": copy.deepcopy(config), "overrides": outcome["changes"],
        "approval_action": approval_action, "review_notes_accepted": bool(accept_review_notes and approved),
        "review_notes": outcome["review_notes"],
    }
    return updated


def ready_review(state: dict, config: dict, rows: list[dict] | None = None, *, draft: dict | None = None) -> dict | None:
    saved = saved_review(state, config)
    official = appd.approved_review(state, config)
    if not saved or not saved.get("approved") or not official:
        return None
    if rows is not None and appd.digest(normalize_rows(state, rows)) != appd.digest(saved["rows"]):
        return None
    if draft is not None and draft.get("fingerprint") != appd.input_fingerprint(state, config):
        return None
    outcome = prepare_export(state, config, saved["rows"], draft=draft,
                             accept_review_notes=bool(saved.get("review_notes_accepted")))
    if outcome["issues"] or appd.digest(appd.normalize_review_rows(outcome["upload_rows"])) != appd.digest(official["rows"]):
        return None
    return saved


def download_bytes(store, signature: str, expected_review: str, *, variant: str = "ratings") -> bytes:
    """Recheck the shared archive at download time, not only when the page opened."""
    if variant not in {"ratings", "raw"}:
        raise ValueError("Choose the raw averages or converted CBME ratings file.")
    latest, _ = store.load()
    if latest is None:
        raise ValueError("Saved data is unavailable. Reload the app before downloading.")
    config = appd.config_for(latest)
    if appd.input_fingerprint(latest, config) != signature:
        raise ValueError("Saved data changed. Reload and review before downloading.")
    saved = ready_review(latest, config)
    if saved is None or appd.digest(saved) != expected_review:
        raise ValueError("The shared review changed. Reload before downloading.")
    ratings = appd.workbook_bytes(latest, config)
    if variant == "raw":
        from epa_dual_export import raw_from_ratings
        return raw_from_ratings(ratings, saved["rows"])
    return ratings


def raw_workbook_bytes(state: dict, config: dict) -> bytes:
    """Export the approved numeric table without category conversion.

    This shares the upload template's structure, not its categorical contract.
    Missing values stay blank. Explicit review edits are included, and the
    untouched evaluation evidence remains in the saved source archive.
    """
    saved = ready_review(state, config)
    if saved is None:
        raise ValueError("Approve and save the current review before downloading.")
    from epa_dual_export import raw_from_ratings
    return raw_from_ratings(appd.workbook_bytes(state, config), saved["rows"])


def raw_export_filename(config: dict) -> str:
    return appd.export_filename(config)[:-5] + "-RAW.xlsx"


def approve_save_prepare(state: dict, config: dict, rows: list[dict], expected_fingerprint: str,
                         *, expected_saved: str | None = None) -> tuple[dict, bytes]:
    """Validate, generate, and atomically save one explicit approval.

    The returned workbook was prepared before the caller commits the archive.
    A failed validation/template load therefore cannot leave a false approval.
    The caller must not expose a download until its encrypted save succeeds.
    Raw evaluations and calculated numeric values are never overwritten.
    """
    baseline = appd.config_for(state)
    if appd.input_fingerprint(state, baseline) != expected_fingerprint:
        raise ValueError("The saved evaluations or setup changed. Reload and review before approving.")
    if appd.review_key(config) != appd.review_key(baseline):
        raise ValueError("The reporting period changed. Save the program settings and reopen APPD Export.")
    current = state.get("settings", {}).get(REVIEW_NAMESPACE, {}).get(appd.review_key(baseline))
    if expected_saved is not None and appd.digest(current) != expected_saved:
        raise ValueError("Another user saved this review. Reload before approving.")
    appd.validate_config(config)
    updated = copy.deepcopy(state)
    updated.setdefault("settings", {})["appd"] = copy.deepcopy(config)
    signature = appd.input_fingerprint(updated, config)
    updated = save_numeric_review(updated, config, rows, signature, approved=True,
                                  approval_action="approve_save_prepare", accept_review_notes=True,
                                  expected_saved=expected_saved)
    data = appd.workbook_bytes(updated, config)
    # Prepare both before the caller commits. Neither download becomes ready
    # after a failed validation, workbook build, or encrypted archive save.
    from epa_dual_export import raw_from_ratings
    review = updated["settings"][REVIEW_NAMESPACE][appd.review_key(config)]
    raw_from_ratings(data, review["rows"])
    return updated, data
