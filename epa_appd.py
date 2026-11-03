"""APPD general-pediatrics export, mapping, roster, and approval logic.

No network calls or secrets. Existing numeric EPA averages are evidence only.
A program-defined conversion can suggest categories, but export requires a saved
CCC approval. Missing scores are never silently treated as observed competence.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import math
import re
import zipfile
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from statistics import mean
from typing import Any
from xml.etree import ElementTree as ET

from epa_core import clean_text, match_form, normalize_code, normalize_key, score_rows, utc_now

# Bump when calculation behavior changes so old CCC approvals cannot be reused.
CALCULATION_VERSION = "evaluation-first-multiepa-2026-11-03-v2"
EPA_COLUMNS = [f"EPA {n}" for n in range(1, 13)]
APPD_CODES = ("0", "1", "2a", "2b", "3a", "3b", "4", "5")
HEADERS = ["programid", "assessmentdate", "name_or_program_assigned_ID", "pgy", "season", "mapping"] + EPA_COLUMNS
LEVEL_LABELS = {
    "0": "Not observed",
    "1": "Observation only / performed by supervisor",
    "2a": "Co-activity with the supervisor",
    "2b": "Supervisor in the room and ready to step in",
    "3a": "Close support and oversight",
    "3b": "Moderate support and oversight",
    "4": "Limited support and oversight",
    "5": "Without supervision",
}
APPD_GUIDE = "https://www.appd.org/wp-content/uploads/2025/09/APPD-CBME-Data-Hub-User-Guide-1.pdf"
SHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
S = "{" + SHEET_NS + "}"


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str,
                                     allow_nan=False).encode("utf-8")).hexdigest()


def as_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text or text.lower() in {"nan", "nat", "none", "<na>"}:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except (ValueError, TypeError):
        pass
    for fmt in ("%m/%d/%Y", "%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def pgy_number(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    text = clean_text(value)
    m = re.fullmatch(r"(?:PGY\s*[-:]?\s*)?(\d{1,2})(?:\.0+)?", text, re.I)
    if m and 1 <= int(m.group(1)) <= 15:
        return int(m.group(1))
    return None


def academic_year(day: date, start_month: int = 7) -> int:
    return day.year - int(day.month < start_month)


def defaults(today: date | None = None) -> dict:
    today = today or date.today()
    season = "FALL" if today.month >= 7 else "SPRING"
    return {
        "programid": "Penn State Pediatric Residency", "season": season,
        "year": today.year, "mapping": "GENERAL",
        "start_date": date(today.year, 7 if season == "FALL" else 1, 1).isoformat(),
        "end_date": today.isoformat(), "date_basis": "completed_at",
        "assessment_date_mode": "latest_completed", "fixed_assessment_date": today.isoformat(),
        "aggregation": "equal_evaluations", "conversion_enabled": False,
        "conversion_confirmed": False, "minimum_score": 1.0, "score_bands": [],
        "local_fraction_order": "b_then_a", "local_fraction_cutoff": 0.5,
    }


def config_for(state: dict, today: date | None = None) -> dict:
    result = defaults(today)
    result.update(copy.deepcopy(state.get("settings", {}).get("appd", {})))
    # This update deliberately standardizes both the dashboard and APPD evidence.
    # A legacy pooling choice is left in the archive until the next settings save,
    # but is not used for calculations. No source scores or mappings are changed.
    result["aggregation"] = "equal_evaluations"
    return result


def validate_config(config: dict) -> None:
    if not clean_text(config.get("programid")):
        raise ValueError("Enter the program ID or program name used in APPD.")
    if config.get("season") not in {"FALL", "SPRING"}:
        raise ValueError("Choose FALL or SPRING.")
    if not isinstance(config.get("year"), int) or not 2000 <= config["year"] <= 2100:
        raise ValueError("Enter a reporting year between 2000 and 2100.")
    if config.get("mapping") != "GENERAL":
        raise ValueError("This 12-EPA template supports the GENERAL mapping only.")
    start, end = as_date(config.get("start_date")), as_date(config.get("end_date"))
    if start is None or end is None or start > end:
        raise ValueError("Choose a valid evaluation date range.")
    if config.get("date_basis") not in {"completed_at", "session_date"}:
        raise ValueError("Choose a valid evaluation date field.")
    if config.get("assessment_date_mode") not in {"latest_completed", "latest_session", "fixed"}:
        raise ValueError("Choose a valid assessment-date rule.")
    if config["assessment_date_mode"] == "fixed" and as_date(config.get("fixed_assessment_date")) is None:
        raise ValueError("Enter the CCC assessment date.")
    if config.get("aggregation") != "equal_evaluations":
        raise ValueError("Use evaluation-first averaging: linked questions within each evaluation, then equal evaluation weights.")
    if config.get("local_fraction_order", "b_then_a") not in {"b_then_a", "a_then_b"}:
        raise ValueError("Choose a valid local a/b display order.")
    try:
        cutoff = float(config.get("local_fraction_cutoff", 0.5))
    except (ValueError, TypeError):
        raise ValueError("The local fractional cutoff must be between 0 and 1.") from None
    if not math.isfinite(cutoff) or not 0 < cutoff < 1:
        raise ValueError("The local fractional cutoff must be between 0 and 1.")
    if config.get("conversion_enabled"):
        if not config.get("conversion_confirmed"):
            raise ValueError("Confirm the local score conversion before enabling suggestions.")
        minimum = float(config.get("minimum_score", 1))
        if not math.isfinite(minimum):
            raise ValueError("The minimum average must be a finite number.")
        previous = None
        bands = config.get("score_bands", [])
        if not bands:
            raise ValueError("Add at least one average-to-level conversion row.")
        for band in bands:
            upper = float(band["upper"])
            if not math.isfinite(upper) or upper < minimum or (previous is not None and upper <= previous):
                raise ValueError("Maximum averages must increase, without repeats, and cannot be below the minimum.")
            if band.get("code") not in APPD_CODES[1:]:
                raise ValueError("Each conversion row needs an observed APPD level. Use 0 only for not observed.")
            previous = upper


def suggested_level(average: float | None, config: dict) -> str:
    """Suggest an APPD code only from the program's separate confirmed code bands.

    Local display labels are deliberately never used here. For example, a local
    label of '3' is not an accepted APPD supervision category.
    """
    if average is None or not config.get("conversion_enabled") or not config.get("conversion_confirmed"):
        return ""
    try:
        average = float(average)
    except (ValueError, TypeError):
        return ""
    if not math.isfinite(average) or average < float(config.get("minimum_score", 1)):
        return ""
    for band in config.get("score_bands", []):
        if average <= float(band["upper"]):
            return str(band["code"])
    return ""


def local_display_level(average: Any, config: dict | None = None) -> str:
    """A local display convention, not a validated APPD score conversion.

    Default requested rule: integers remain integers, fractions through .50 get
    b, and fractions above .50 get a. Calculate from the unrounded numeric mean.
    Tiny floating-point error at an integer or cutoff is not a new category.
    """
    if average is None or isinstance(average, bool):
        return ""
    try:
        value = float(average)
    except (ValueError, TypeError):
        return ""
    if not math.isfinite(value) or value < 0:
        return ""
    config = config or {}
    order = config.get("local_fraction_order", "b_then_a")
    cutoff = float(config.get("local_fraction_cutoff", 0.5))
    if order not in {"b_then_a", "a_then_b"} or not math.isfinite(cutoff) or not 0 < cutoff < 1:
        raise ValueError("Invalid local display settings. Review Admin > Score conversion.")
    nearest_integer = round(value)
    if math.isclose(value, nearest_integer, rel_tol=0, abs_tol=1e-12):
        return str(nearest_integer)
    whole = math.floor(value)
    fraction = value - whole
    lower, upper = ("b", "a") if order == "b_then_a" else ("a", "b")
    in_lower_half = fraction < cutoff or math.isclose(fraction, cutoff, rel_tol=0, abs_tol=1e-12)
    return f"{whole}{lower if in_lower_half else upper}"


def form_signature(form: dict) -> str:
    """A wording change requires review, but renaming the form does not lose links."""
    return digest([{k: q.get(k, "") for k in ("id", "text", "tags")}
                   for q in sorted(form.get("questions", []), key=lambda q: str(q["id"]))])


def mapping_for(state: dict, form_id: str) -> dict:
    saved = copy.deepcopy(state.get("settings", {}).get("appd_question_maps", {}).get(form_id, {}))
    # Read legacy single-target entries as one-element lists without erasing them.
    # Deduplicate targets so a response can contribute only once to each EPA.
    links = {}
    for qid, values in saved.get("questions", {}).items():
        if isinstance(values, str):
            values = [values] if values else []
        if not isinstance(values, (list, tuple)):
            raise ValueError(f"Question {qid} has an invalid saved EPA mapping. Review its mapping.")
        targets = list(dict.fromkeys(str(value) for value in values))
        if any(target not in EPA_COLUMNS for target in targets):
            raise ValueError(f"Question {qid} has an unknown saved EPA target. Use EPA 1 through EPA 12.")
        if targets:
            links[str(qid)] = sorted(targets, key=EPA_COLUMNS.index)
    if saved:
        saved["questions"] = links
    return saved


def save_mapping(state: dict, form_id: str, links: dict, confirmed: bool) -> dict:
    form = state.get("forms", {}).get(form_id)
    if not form:
        raise ValueError("This form no longer exists. Reload saved data.")
    known = {str(q["id"]) for q in form["questions"]}
    normalized = {}
    for qid, values in links.items():
        if str(qid) not in known:
            raise ValueError(f"Question {qid} is not part of this form.")
        if not isinstance(values, (list, tuple)):
            raise ValueError("Each question mapping must be a list of EPAs.")
        epas = sorted(set(str(value) for value in values), key=lambda x: EPA_COLUMNS.index(x) if x in EPA_COLUMNS else 99)
        if any(epa not in EPA_COLUMNS for epa in epas):
            raise ValueError("Map questions only to EPA 1 through EPA 12.")
        if epas:
            normalized[str(qid)] = epas
    updated = copy.deepcopy(state)
    updated.setdefault("settings", {}).setdefault("appd_question_maps", {})[form_id] = {
        "questions": normalized, "confirmed": bool(confirmed),
        "form_signature": form_signature(form), "saved_at": utc_now(),
    }
    return updated


def residents_in(state: dict) -> dict[str, str]:
    names = {}
    for record in state.get("evaluations", {}).values():
        names[record["resident_key"]] = record["resident"]
    for key, row in state.get("settings", {}).get("appd_roster", {}).items():
        names.setdefault(key, row.get("resident", key))
    return dict(sorted(names.items(), key=lambda kv: kv[1].casefold()))


def detected_pgy(state: dict, resident_key: str, year: int) -> tuple[int | None, str]:
    start_month = state.get("settings", {}).get("academic_year_start_month", 7)
    matches, sources = set(), set()
    conflict = False
    for record in state.get("evaluations", {}).values():
        if record.get("resident_key") != resident_key:
            continue
        day = as_date(record.get("session_date")) or as_date(record.get("completed_at"))
        if day is None or academic_year(day, start_month) != year:
            continue
        value = pgy_number(record.get("resident_pgy"))
        if value is not None:
            matches.add(value)
            sources.add(record.get("pgy_source_field", "TAB PGY field"))
        if record.get("pgy_metadata_conflicts"):
            conflict = True
    if conflict or len(matches) > 1:
        return None, "Conflicting TAB PGY values. Enter a roster override."
    if len(matches) == 1:
        return next(iter(matches)), "TAB: " + ", ".join(sorted(sources))
    return None, "No PGY field found for this academic year."


def resolve_pgy(state: dict, resident_key: str, assessment_date: date | None) -> tuple[int | None, str]:
    if assessment_date is None:
        return None, "An assessment date is required to determine PGY."
    year = academic_year(assessment_date, state.get("settings", {}).get("academic_year_start_month", 7))
    roster = state.get("settings", {}).get("appd_roster", {}).get(resident_key, {})
    override = pgy_number(roster.get("pgy_by_academic_year", {}).get(str(year)))
    if override is not None:
        return override, f"Saved roster for academic year {year}-{year + 1}"
    return detected_pgy(state, resident_key, year)


def input_fingerprint(state: dict, config: dict) -> str:
    settings = state.get("settings", {})
    return digest({
        "calculation_version": CALCULATION_VERSION,
        "config": config, "forms": state.get("forms", {}),
        "evaluations": state.get("evaluations", {}), "conflicts": state.get("conflicts", {}),
        "maps": settings.get("appd_question_maps", {}), "roster": settings.get("appd_roster", {}),
        "academic_year_start_month": settings.get("academic_year_start_month", 7),
    })


def review_key(config: dict) -> str:
    return digest([config["programid"], config["mapping"], config["season"], config["year"]])[:24]


def build_draft(state: dict, config: dict | None = None) -> dict:
    config = copy.deepcopy(config or config_for(state))
    validate_config(config)
    start, end = as_date(config["start_date"]), as_date(config["end_date"])
    eligible, missing_dates = {}, 0
    for rid, record in state.get("evaluations", {}).items():
        day = as_date(record.get(config["date_basis"]))
        if day is None:
            missing_dates += 1
        elif start <= day <= end:
            eligible[rid] = record
    people = defaultdict(list)
    for rid, record in eligible.items():
        people[record["resident_key"]].append((rid, record))
    names = residents_in(state)
    warnings = []
    if missing_dates:
        warnings.append(f"{missing_dates} evaluations have no usable {config['date_basis']} date and were excluded.")
    if state.get("conflicts"):
        warnings.append(f"{len(state['conflicts'])} upload conflicts remain in Review. Pending alternatives are not used.")
    good_links = {}
    coverage_ready = True
    for fid, form in state.get("forms", {}).items():
        mapping = mapping_for(state, fid)
        used = any(match_form(record, state["forms"]).get("form_id") == fid for record in eligible.values())
        valid = mapping.get("confirmed") and mapping.get("form_signature") == form_signature(form)
        if valid:
            good_links[fid] = mapping.get("questions", {})
            excluded_links = [q["id"] for q in form["questions"] if q.get("id") in good_links[fid] and not q.get("include")]
            if used and excluded_links:
                coverage_ready = False
                warnings.append(f"{form['name']}: linked questions {', '.join(excluded_links)} are excluded from scoring in Forms.")
        elif used:
            coverage_ready = False
            warnings.append(f"{form['name']}: APPD question mapping is missing, unconfirmed, or needs review after wording changed.")
        if used and not form.get("scoring_confirmed"):
            coverage_ready = False
            warnings.append(f"{form['name']}: numeric scoring has not been confirmed in Forms.")
    unknown = sum(match_form(r, state.get("forms", {}))["form_id"] is None for r in eligible.values())
    if unknown:
        coverage_ready = False
        warnings.append(f"{unknown} included evaluations have unrecognized forms and contribute no mapped scores.")

    evidence, per_eval, blocked, question_points = [], defaultdict(list), defaultdict(int), defaultdict(list)
    for item in score_rows(state).to_dict("records"):
        if item["record_id"] not in eligible:
            continue
        for epa in good_links.get(item["form_id"], {}).get(item["question_id"], []):
            key = (item["resident_key"], epa)
            score = item.get("score")
            valid = item["status"] == "scored" and score is not None and math.isfinite(float(score))
            if valid:
                per_eval[(item["resident_key"], epa, item["record_id"])].append(float(score))
                question_points[(item["resident_key"], epa, item["form_id"], item["question_id"], item["question"])].append(float(score))
            elif item["status"] in {"unmapped", "unconfirmed", "not_epa"}:
                blocked[key] += 1
            evidence.append({
                "Resident": item["resident"], "APPD EPA": epa, "Form": item["epa"],
                "Evaluator": eligible[item["record_id"]].get("evaluator", ""),
                "Question ID": item["question_id"], "Question": item["question"],
                "Original response": item["raw_response"], "Numeric score": float(score) if valid else None,
                "Status": item["status"], "Session date": item.get("session_date"),
                "Date completed": item.get("completed_at"), "Evaluation ID": item["record_id"],
            })
    group_values, eval_means = defaultdict(list), defaultdict(list)
    evaluation_scores, form_values = [], defaultdict(list)
    for (key, epa, rid), values in sorted(per_eval.items()):
        evaluation_average = mean(values)
        group_values[(key, epa)].extend(values)
        eval_means[(key, epa)].append(evaluation_average)
        record = eligible[rid]
        fid = match_form(record, state.get("forms", {}))["form_id"]
        form_values[(key, epa, fid)].append(evaluation_average)
        mapped_count = sum(epa in targets for targets in good_links.get(fid, {}).values())
        evaluation_scores.append({
            "Resident": names.get(key, key), "APPD EPA": epa,
            "Form": state["forms"][fid]["name"], "Evaluator": record.get("evaluator", ""),
            "Evaluation average": evaluation_average,
            "Scored questions": len(values), "Mapped questions": mapped_count,
            "Session date": record.get("session_date"), "Date completed": record.get("completed_at"),
            "Evaluation ID": rid,
        })
    form_summaries = [
        {"Resident": names.get(key, key), "APPD EPA": epa,
         "Form": state["forms"][fid]["name"], "Average": mean(values),
         "Evaluations": len(values), "Local level": local_display_level(mean(values), config)}
        for (key, epa, fid), values in sorted(form_values.items())
    ]
    rows, summaries = [], []
    for key in sorted(people, key=lambda x: names.get(x, x).casefold()):
        records = [r for _, r in people[key]]
        mode = config["assessment_date_mode"]
        if mode == "fixed":
            day = as_date(config["fixed_assessment_date"])
        else:
            field = "completed_at" if mode == "latest_completed" else "session_date"
            days = [as_date(r.get(field)) for r in records]
            day = max((d for d in days if d is not None), default=None)
        pgy, source = resolve_pgy(state, key, day)
        roster = state.get("settings", {}).get("appd_roster", {}).get(key, {})
        row = {"resident_key": key, "Resident": names.get(key, key), "Include": True,
               "name_or_program_assigned_ID": roster.get("export_id") or names.get(key, key),
               "assessmentdate": day.isoformat() if day else "", "pgy": pgy,
               "PGY source": source}
        if day and day.year != config["year"]:
            warnings.append(f"{names.get(key, key)}: latest assessment date is outside the reporting year. Review the date or select a CCC date.")
        for epa in EPA_COLUMNS:
            values, evaluations = group_values[(key, epa)], eval_means[(key, epa)]
            avg = mean(evaluations) if evaluations else None
            row[epa] = suggested_level(avg, config)
            summaries.append({"resident_key": key, "Resident": row["Resident"], "APPD EPA": epa,
                              "Average": avg, "Local level": local_display_level(avg, config),
                              "Ratings": len(values), "Evaluations": len(evaluations),
                              "Needs scoring review": blocked[(key, epa)], "Suggested level": row[epa]})
        rows.append(row)
    questions = []
    for (key, epa, fid, qid, text), values in sorted(question_points.items()):
        questions.append({"Resident": names.get(key, key), "APPD EPA": epa,
                          "Form": state["forms"][fid]["name"], "Question ID": qid, "Question": text,
                          "Average": mean(values), "Ratings": len(values)})
    return {"rows": rows, "summary": summaries, "questions": questions, "evidence": evidence,
            "evaluation_scores": evaluation_scores, "form_summaries": form_summaries,
            "warnings": list(dict.fromkeys(warnings)), "fingerprint": input_fingerprint(state, config),
            "config": config, "coverage_ready": coverage_ready and not any(blocked.values())}


def normalize_review_rows(rows: list[dict]) -> list[dict]:
    def text(value):
        if value is None or (isinstance(value, float) and not math.isfinite(value)):
            return ""
        cleaned = clean_text(value)
        return "" if cleaned in {"<NA>", "NaT"} else cleaned
    result = []
    for source in rows:
        row = {"resident_key": str(source.get("resident_key", "")),
               "Resident": str(source.get("Resident", "")), "Include": bool(source.get("Include", True)),
               "name_or_program_assigned_ID": text(source.get("name_or_program_assigned_ID", "")),
               "assessmentdate": as_date(source.get("assessmentdate")), "pgy": pgy_number(source.get("pgy"))}
        row["assessmentdate"] = row["assessmentdate"].isoformat() if row["assessmentdate"] else ""
        for epa in EPA_COLUMNS:
            value = source.get(epa)
            row[epa] = "" if value is None or str(value).lower() in {"nan", "none", "<na>"} else normalize_code(value)
        result.append(row)
    return result


def validate_review(rows: list[dict], config: dict) -> list[str]:
    errors, names, keys = [], set(), set()
    included = [r for r in normalize_review_rows(rows) if r["Include"]]
    if not included:
        return ["Select at least one resident for export."]
    for row in included:
        name = row["Resident"] or row["resident_key"] or "Unnamed resident"
        identifier = row["name_or_program_assigned_ID"]
        if not row["resident_key"] or row["resident_key"] in keys:
            errors.append(f"{name}: duplicate or missing resident identity.")
        keys.add(row["resident_key"])
        if not identifier:
            errors.append(f"{name}: enter an APPD name or program-assigned ID.")
        elif normalize_key(identifier) in names:
            errors.append(f"{name}: APPD name/ID duplicates another selected resident.")
        names.add(normalize_key(identifier))
        if not as_date(row["assessmentdate"]):
            errors.append(f"{name}: enter an assessment date.")
        if row["pgy"] is None:
            errors.append(f"{name}: enter a PGY from 1 to 15.")
        missing = [epa for epa in EPA_COLUMNS if row[epa] not in APPD_CODES]
        if missing:
            errors.append(f"{name}: choose a final level for {', '.join(missing)}. Use 0 only when not observed.")
    return errors


def save_review(state: dict, config: dict, rows: list[dict], expected_fingerprint: str,
                *, approved: bool, reviewer: str = "") -> dict:
    if input_fingerprint(state, config_for(state)) != expected_fingerprint:
        raise ValueError("The evaluations or APPD setup changed. Reload and review the current scores before saving.")
    normalized = normalize_review_rows(rows)
    current_keys = {row["resident_key"] for row in build_draft(state, config)["rows"]}
    if len({r["resident_key"] for r in normalized}) != len(normalized) or {r["resident_key"] for r in normalized} != current_keys:
        raise ValueError("The resident rows changed. Reload the current export review.")
    if approved:
        errors = validate_review(normalized, config)
        if errors:
            raise ValueError(errors[0])
        if not clean_text(reviewer):
            raise ValueError("Enter the reviewer name or initials.")
    updated = copy.deepcopy(state)
    snapshot = {"input_fingerprint": expected_fingerprint, "rows": normalized, "approved": bool(approved),
                "reviewer": clean_text(reviewer), "saved_at": utc_now(), "config": copy.deepcopy(config)}
    updated.setdefault("settings", {}).setdefault("appd_reviews", {})[review_key(config)] = snapshot
    return updated


def approved_review(state: dict, config: dict) -> dict | None:
    saved = state.get("settings", {}).get("appd_reviews", {}).get(review_key(config))
    if not saved or not saved.get("approved"):
        return None
    if saved.get("input_fingerprint") != input_fingerprint(state, config):
        return None
    if validate_review(saved.get("rows", []), config):
        return None
    return saved


def export_filename(config: dict) -> str:
    program = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", config["programid"]).strip(" .")[:110] or "Program"
    return f"{program}-{config['season']}-{config['year']}.xlsx"


def workbook_bytes(state: dict, config: dict, template_path: Path | None = None) -> bytes:
    """Fill a scrubbed copy of the supplied template using native OOXML.

    Keeping the original first two rows avoids pandas' usual row-1 header export.
    Inline strings are never executable formulas and preserve IDs with zeros.
    """
    review = approved_review(state, config)
    if review is None:
        raise ValueError("Save CCC approval for the current data before downloading the APPD workbook.")
    template_path = template_path or Path(__file__).with_name("appd_template.xlsx")
    if not template_path.is_file():
        raise ValueError("appd_template.xlsx is missing. Upload every file from the update package.")
    with zipfile.ZipFile(template_path) as original:
        parts = {n: original.read(n) for n in original.namelist()}
    root = ET.fromstring(parts["xl/worksheets/sheet1.xml"])
    data = root.find(S + "sheetData")
    if data is None:
        raise ValueError("The bundled APPD template has no worksheet data.")
    for row in list(data):
        if int(row.attrib.get("r", "0")) >= 3:
            data.remove(row)
    header_date = data.find(f"{S}row[@r='2']/{S}c[@r='B2']")
    text_style = header_date.get("s", "0") if header_date is not None else "0"
    included = [r for r in review["rows"] if r["Include"]]
    for number, source in enumerate(included, start=3):
        values = [config["programid"], as_date(source["assessmentdate"]).strftime("%m/%d/%Y"),
                  source["name_or_program_assigned_ID"], source["pgy"], config["season"], config["mapping"]]
        values += [int(source[k]) if source[k] in {"0", "1", "4", "5"} else source[k] for k in EPA_COLUMNS]
        row = ET.SubElement(data, S + "row", {"r": str(number), "spans": "1:18"})
        for column, value in enumerate(values):
            attrs = {"r": f"{chr(65 + column)}{number}"}
            if column in (1, 2):
                attrs["s"] = text_style
            if isinstance(value, int):
                cell = ET.SubElement(row, S + "c", attrs)
                ET.SubElement(cell, S + "v").text = str(value)
            else:
                attrs["t"] = "inlineStr"
                cell = ET.SubElement(row, S + "c", attrs)
                inline = ET.SubElement(cell, S + "is")
                ET.SubElement(inline, S + "t", {"{http://www.w3.org/XML/1998/namespace}space": "preserve"}).text = str(value)
    dimension = root.find(S + "dimension")
    if dimension is not None:
        dimension.set("ref", f"A1:R{len(included) + 2}")
    # Ensure the supplied EPA dropdown is available on every exported data row.
    validations = root.find(S + "dataValidations")
    if validations is not None:
        rules = list(validations)
        for rule in rules[1:]:
            validations.remove(rule)
        if rules:
            rules[0].set("sqref", "G3:R1048576")
        validations.set("count", str(len(validations)))
    ET.register_namespace("", SHEET_NS)
    ET.register_namespace("r", "http://schemas.openxmlformats.org/officeDocument/2006/relationships")
    parts["xl/worksheets/sheet1.xml"] = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as final:
        for name, content in parts.items():
            final.writestr(name, content)
    return output.getvalue()
