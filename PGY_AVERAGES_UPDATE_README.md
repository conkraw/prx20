# EPA Tracker: visible averages and assessment-date PGY

## Install over the Remove Form release

Extract EPA_Tracker_PGY_Averages_Update.zip. In the same GitHub folder as your running epa_tracker.py, replace epa_tracker.py, epa_appd.py, and epa_appd_ui.py. Add the new epa_pgy.py file. Commit all four Python files together and reboot the Streamlit app.

Keep your existing encrypted archive, Streamlit Secrets, requirements, APPD template, and other Python modules. Do not replace the archive or re-upload evaluations. This update retains multi-EPA mapping, evaluation-first averaging, local display labels, and form removal.

## Why averages can look blank

The previous APPD Export page opened on final APPD categories, not calculated averages. Those categories intentionally remain blank when a program-defined APPD conversion is disabled and final levels have not been entered. Numeric evidence was on a separate tab or behind an expandable section.

The new APPD Export page opens on Calculated EPA averages. Each populated cell shows the numeric average and local label, such as 3.25 (3b). PGY appears next to the assessment date. No CCC approval is needed to view calculated averages.

An actual missing average still stays blank. The new readiness tables explain missing or unconfirmed question-to-EPA mapping, unconfirmed scoring scales, excluded questions, and responses that are blank or excluded. Scores are never fabricated, and missing PGY does not prevent averaging.

The Dashboard now offers two views under EPA averages:

- By evaluation form retains the existing form-level summary.
- Mapped EPA 1-12 combines each question's saved EPA links across the selected forms and evaluations. It uses the Dashboard filters, not the separate APPD export date range.

The default is the mapped view when confirmed EPA links exist. Final APPD codes remain separate from local display labels.

## Save residency start dates once

Open Admin > Resident roster. Enter July 1 of each resident's PGY1 start year in the PGY1 start date column, then click Save resident roster. Start dates use the existing encrypted GitHub archive.

A group-entry section can assign the same July 1 start date to several selected residents. The preview date is for checking PGYs only. It does not change assessment dates or report filters.

For a July 1, 2026 start:

| Assessment date | PGY |
| --- | --- |
| August 15, 2026 | PGY1 |
| June 30, 2027 | PGY1 |
| July 1, 2027 | PGY2 |
| October 15, 2027 | PGY2 |
| July 1, 2028 | PGY3 |
| July 1, 2029 | PGY4 |

The calculation is the assessment's July-to-June academic-year start year minus the resident's start year plus one. It never uses today's date or upload time for historical PGY.

A saved start date takes priority over older manual yearly PGYs. Old manual entries are preserved as a fallback only when no start date is saved. Explicit PGY fields in TAB files remain another fallback. A start date is not inferred from Intern or Senior rotation names.

Invalid dates, starts other than July 1, assessments before residency begins, and calculated PGYs outside 1 to 15 are flagged rather than guessed. Automatic progression follows the requested July 1 assumption and does not adjust for leave, transfers, or off-cycle progression. For a resident who cannot follow this rule, clear the start date and use the existing manual fallback for the affected academic year.

## Which assessment date controls PGY?

For each individual evaluation, the Assessment PGY tab uses Date Completed. A missing Date Completed leaves that evaluation's PGY blank and explains why. It does not silently substitute the upload date or rotation start.

APPD Export uses the final assessmentdate for that resident. This follows your Admin setting for latest completion, latest session, or a common CCC date. Editing assessmentdate in the final-level grid automatically recalculates PGY when a start date is present. All other edits are retained. The saved review and workbook export recheck the calculation.

Dashboard form summaries show all PGYs represented by the selected evaluations. A summary spanning a July boundary can show PGY1 / PGY2. Mapped EPA summaries label the latest completed assessment in the selection. The Assessment PGY tab provides the individual dates.

Changing start dates or assessment dates does not change the EPA score formula. The average is still the mean of valid mapped question scores within each evaluation, followed by the mean of those evaluation averages. Each evaluation has equal weight.

## APPD approval

Saved evaluations, question mappings, and final-code conversion settings are retained. Previous APPD approvals must be reviewed again after this update. A changed start date also invalidates any approval based on the old roster. This prevents export of an approved but stale PGY.

## Testing and limits

225 automated tests passed, one existing test was skipped, and eight subtests passed. Coverage includes July rollover, historical dates, clearing dates, invalid dates, encrypted save/reload, preservation of other residents, multiple EPA links, equal-evaluation weighting, date edits before approval, and PGY values in the generated APPD workbook.

The Admin, Dashboard, and APPD Export logic was exercised with a Streamlit test double. This is not a live browser test. Streamlit was unavailable in this environment, so four existing Streamlit-dependent test files were not run. The live deployed app and an actual APPD Data Hub upload were not tested. The user's live archive was not available, so the exact reason for any particular blank resident cell could not be confirmed.
