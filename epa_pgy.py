"""Historical PGY calculations for July 1 residency starts.

Pure functions only. No dates are inferred from names, rotations, or upload time.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any


def as_date(value: Any) -> date | None:
    """Parse saved ISO dates and TAB dates, including empty pandas/NumPy scalars."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "nat", "none", "<na>"}:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
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
    match = re.fullmatch(r"(?:PGY\s*[-:]?\s*)?(\d{1,2})(?:\.0+)?", str(value).strip(), re.I)
    if match and 1 <= int(match.group(1)) <= 15:
        return int(match.group(1))
    return None


def has_start_date(roster_row: dict) -> bool:
    value = roster_row.get("residency_start_date")
    return value is not None and str(value).strip().lower() not in {"", "none", "nan", "nat", "<na>"}


def validated_start_date(value: Any) -> date | None:
    """Allow clearing a start date, but never silently change an entered date."""
    if not has_start_date({"residency_start_date": value}):
        return None
    day = as_date(value)
    if day is None:
        raise ValueError("Enter a valid residency start date.")
    if (day.month, day.day) != (7, 1):
        raise ValueError("Residency start dates must be July 1. Enter July 1 of the PGY1 start year.")
    if not 1900 <= day.year <= 2100:
        raise ValueError("Enter a residency start year between 1900 and 2100.")
    return day


def pgy_from_start(start_date: Any, assessment_date: Any) -> tuple[int | None, str]:
    """PGY increments at the July 1 anniversary, independently of today's date."""
    try:
        start = validated_start_date(start_date)
    except ValueError as exc:
        return None, str(exc)
    if start is None:
        return None, "Add the July 1 residency start date in Admin > Resident roster."
    assessed = as_date(assessment_date)
    if assessed is None:
        return None, "A valid assessment date is required to determine PGY."
    if assessed < start:
        return None, f"Assessment precedes the residency start date ({start.isoformat()})."
    year = assessed.year - int(assessed.month < 7)
    pgy = year - start.year + 1
    if not 1 <= pgy <= 15:
        return None, f"Calculated PGY{pgy} is outside the supported range 1 to 15. Review the start date."
    return pgy, f"Calculated from July 1, {start.year} start at {assessed.isoformat()}"


def pgy_label(value: Any) -> str:
    number = pgy_number(value)
    return f"PGY{number}" if number is not None else ""
