"""Editable residency CBME defaults and full-precision score conversion.

The November 17, 2026 residency table is the default policy, not an APPD model.
Changing these parameters affects converted downloads only. CCC approval is a
separate action. Missing evidence and observed scores converting to zero remain
distinguishable in the numeric review, raw export, and approval notes.
"""
from __future__ import annotations

import copy
import math
from decimal import Decimal, InvalidOperation
from typing import Any

CODES = ("0", "1", "2a", "2b", "3a", "3b", "4", "5")
DEFAULTS_VERSION = "residency-cbme-2026-11-17"
DEFAULT_MINIMUMS = (0.0, 0.5, 1.5, 2.5, 3.0, 3.5, 4.0, 4.5)
CONVERSION_KEYS = ("conversion_enabled", "conversion_confirmed", "score_band_mode",
                   "minimum_score", "maximum_score", "score_bands", "conversion_rule_origin")
UPPER = "upper_inclusive"
LOWER = "lower_inclusive"


def default_parameters(config: dict, *, preserve_previous: bool = False) -> dict:
    """Install the user-supplied defaults without changing input data or approval.

    The marker prevents subsequent launches from overwriting edited cutoffs.
    A one-time upgrade retains the old conversion fields for reference.
    """
    result = copy.deepcopy(config)
    if preserve_previous and result.get("score_bands") and "previous_conversion_parameters" not in result:
        result["previous_conversion_parameters"] = {
            key: copy.deepcopy(result[key]) for key in CONVERSION_KEYS if key in result
        }
    result.update(
        conversion_enabled=True, conversion_confirmed=True,
        score_band_mode=LOWER, minimum_score=0.0, maximum_score=5.0,
        score_bands=[{"lower": lower, "code": code} for code, lower in zip(CODES, DEFAULT_MINIMUMS)],
        cbme_defaults_version=DEFAULTS_VERSION,
        conversion_rule_origin="Residency CBME defaults supplied November 17, 2026 (editable)",
    )
    return result


def example_parameters(config: dict) -> dict:
    """Return a draft example. The caller must obtain approval before saving."""
    result = copy.deepcopy(config)
    result.update(
        conversion_enabled=True, conversion_confirmed=True,
        score_band_mode=LOWER, minimum_score=1.0, maximum_score=5.0,
        score_bands=[{"lower": lower, "code": code} for code, lower in zip(
            CODES[1:], (1.0, 1.75, 2.25, 2.75, 3.25, 3.75, 4.75))],
        conversion_rule_origin="User-selected example cutoffs; review before approval",
    )
    return result


def decimal_value(value: Any, label: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"Enter a number for {label}.")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f"Enter a number for {label}.") from None
    if not number.is_finite():
        raise ValueError(f"Enter a finite number for {label}.")
    return number


def validate_parameters(config: dict) -> None:
    """Validate explicitly selected rules, including the residency zero range."""
    mode = config.get("score_band_mode", UPPER)
    if mode not in {UPPER, LOWER}:
        raise ValueError("Choose minimum-inclusive or maximum-inclusive conversion ranges.")
    bands = config.get("score_bands", [])
    if not bands:
        raise ValueError("Set the upload conversion parameters before preparing the file.")
    minimum = decimal_value(config.get("minimum_score", 1), "the lowest average")
    previous = None
    codes = []
    field = "lower" if mode == LOWER else "upper"
    for band in bands:
        code = band.get("code")
        if code not in CODES:
            raise ValueError("Use CBME codes 0, 1, 2a, 2b, 3a, 3b, 4, or 5.")
        boundary = decimal_value(band.get(field), f"the {code} cutoff")
        if boundary < minimum or (previous is not None and boundary <= previous):
            raise ValueError("Score cutoffs must increase with no duplicates and cannot fall below the lowest average.")
        previous = boundary
        codes.append(code)
    if mode == LOWER:
        maximum = decimal_value(config.get("maximum_score", 5), "the highest average")
        if maximum < previous:
            raise ValueError("The highest average must be at least the last cutoff.")
        if decimal_value(bands[0][field], "the first cutoff") != minimum:
            raise ValueError("The first cutoff must equal the lowest average covered.")
        if len(set(codes)) != len(codes) or codes != sorted(codes, key=CODES.index):
            raise ValueError("Use codes in order: 0, 1, 2a, 2b, 3a, 3b, 4, 5. A precedes B on the APPD scale.")


def convert_score(value: Any, config: dict) -> str:
    """Use full precision and explicit boundaries. Never round the source score."""
    if value is None or not (config.get("conversion_enabled") and config.get("conversion_confirmed")):
        return ""
    try:
        score = decimal_value(value, "the average")
        validate_parameters(config)
        if score < decimal_value(config.get("minimum_score", 1), "the lowest average"):
            return ""
        if config.get("score_band_mode", UPPER) == LOWER:
            if score > decimal_value(config.get("maximum_score", 5), "the highest average"):
                return ""
            result = ""
            for band in config["score_bands"]:
                if score >= decimal_value(band["lower"], "the cutoff"):
                    result = band["code"]
                else:
                    break
            return result
        for band in config["score_bands"]:
            if score <= decimal_value(band["upper"], "the cutoff"):
                return band["code"]
    except (ValueError, TypeError, KeyError):
        return ""
    return ""


def editor_rows(config: dict) -> list[dict]:
    mode = config.get("score_band_mode", UPPER) if config.get("score_bands") else LOWER
    field = "lower" if mode == LOWER else "upper"
    label = "Minimum average" if mode == LOWER else "Maximum average (inclusive)"
    bands = config.get("score_bands") or [{"code": code, field: None} for code in CODES]
    return [{"Upload code": item.get("code", ""), label: item.get(field)} for item in bands]


def parameters_from_rows(config: dict, rows: list[dict], *, mode: str,
                         minimum: Any = 1, maximum: Any = 5) -> dict:
    """Create a candidate rule. Saving/approving is the user's explicit action."""
    field = "lower" if mode == LOWER else "upper"
    label = "Minimum average" if mode == LOWER else "Maximum average (inclusive)"
    bands = []
    for row in rows:
        code = str(row.get("Upload code", "")).strip().lower()
        value = row.get(label)
        if not code and (value is None or str(value).lower() in {"", "nan", "none", "<na>"}):
            continue
        bands.append({"code": code, field: float(decimal_value(value, f"the {code or 'upload'} cutoff"))})
    result = copy.deepcopy(config)
    result.update(conversion_enabled=True, conversion_confirmed=True, score_bands=bands)
    if mode == LOWER:
        result.update(score_band_mode=LOWER, maximum_score=float(decimal_value(maximum, "the highest average")),
                      minimum_score=bands[0]["lower"] if bands else float(decimal_value(minimum, "the lowest average")))
    else:
        # Do not add a mode field to legacy rules. This preserves old approval fingerprints.
        if "score_band_mode" in result:
            result["score_band_mode"] = UPPER
        result["minimum_score"] = float(decimal_value(minimum, "the lowest average"))
    validate_parameters(result)
    # Equivalent integer/float boundaries must not invalidate a saved approval.
    if result == config:
        return copy.deepcopy(config)
    if result.get("cbme_defaults_version") == DEFAULTS_VERSION:
        result["conversion_rule_origin"] = "Custom CBME cutoffs (based on the supplied residency defaults)"
    return result
