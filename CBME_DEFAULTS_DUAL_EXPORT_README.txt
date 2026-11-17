EPA TRACKER: RESIDENCY CBME DEFAULTS AND TWO EXCEL DOWNLOADS
Release: 2026-11-17
Based on EPA_Tracker_APPD_Export_Simplified_Full.zip.

INSTALL THE PATCH
Replace these four files in the same folder as epa_tracker.py:
  epa_appd.py
  epa_export_rules.py
  epa_workflow.py
  epa_simple_ui.py
Add this new file in that same folder:
  epa_dual_export.py
Commit all five together. Reboot Streamlit and refresh the browser.
Do not change your Secrets, encryption key, requirements, template, or archive.
No evaluation re-upload or question remapping is needed.
Use the full package only when you need all of the application files.

STAFF WORKFLOW
APPD Export -> review numeric averages -> Approve, save and prepare both files.
Then use the two download buttons:
  Download raw averages (.xlsx)
  Download CBME ratings (.xlsx)
One approval saves the numeric review, selected conversion rules, and accepted
review notes. Both files are checked before a successful archive save. Download
callbacks recheck the shared saved archive, so stale approvals cannot be used.
Changes to values or parameters require another approval.

EDITABLE DEFAULTS
Open APPD Export -> CBME conversion parameters to edit the minimum cutoffs.
The supplied residency defaults are:
  CBME 0: 0.00 to below 0.50
  CBME 1: 0.50 to below 1.50
  CBME 2a: 1.50 to below 2.50
  CBME 2b: 2.50 to below 3.00
  CBME 3a: 3.00 to below 3.50
  CBME 3b: 3.50 to below 4.00
  CBME 4: 4.00 to below 4.50
  CBME 5: 4.50 through 5.00 inclusive
These reproduce the supplied two-decimal table without gaps for averages with
additional decimal places. Conversion uses the unrounded average. For example,
3.495 stays in 3a, while exactly 3.50 is 3b. The initial table displays two decimal
places but its underlying numeric values retain full precision.
Examples: 3.36 -> 3a, 3.77 -> 3b, 4.00 -> 4, 4.50 -> 5.

Restore residency defaults loads these values into the editor. Approval saves
the changes. Your custom cutoffs persist across sessions and are not reset on
later launches. When upgrading from the previous app, the supplied defaults
replace the old example policy once. Previous conversion fields are retained
in settings.appd.previous_conversion_parameters. This does not rewrite source
evaluations. Old approvals must be reviewed again under the new rules.

TWO WORKBOOKS
Both use one worksheet and the same 18-column upload template layout:
programid, assessmentdate, name_or_program_assigned_ID, pgy, season, mapping,
and EPA 1 through EPA 12. Headers remain on row 2, data begin on row 3, assessment
dates are text in MM/DD/YYYY, and resident identifiers remain literal text.
Both include the same approved residents, identifiers, dates, and PGYs.

Raw file: Program-FALL-2026-RAW.xlsx
  Contains the approved numeric review values before category conversion.
  Explicit numeric review edits are included. Original evaluations are unchanged.
  Values such as 3.77 remain numbers, not strings or letter categories.
  Underlying floating-point precision is retained, with two-decimal display.
  Missing values stay blank. Observed numeric zero stays numeric zero.
  The categorical dropdown is removed, so it does not reject legitimate decimals.
  This is not an export of individual question responses.

Ratings file: Program-FALL-2026.xlsx
  Uses the requested CBME conversion parameters and the template dropdown codes.
  Missing evidence is exported as 0. A numeric value mapped to code 0 by the
  supplied policy is also exported as 0, with a separate review note.
  Valid categorical codes remain 0, 1, 2a, 2b, 3a, 3b, 4, and 5.
  The official filename convention is preserved for this file.

DATA HUB COMPATIBILITY
APPD's published upload instructions specify categorical supervision codes, not
decimal averages. Both files have the template layout, but the raw file is not
represented as a confirmed direct Data Hub import. Use the converted ratings
file for the documented import format. No live Data Hub import was performed.
APPD interprets code 0 as "not observed". Under the supplied residency policy,
numeric averages below 0.50 produce this code too. The raw file keeps those
numeric values distinguishable from truly missing evidence.
Source checked November 17, 2026:
https://www.appd.org/wp-content/uploads/2025/09/APPD-CBME-Data-Hub-User-Guide-1.pdf

UNCHANGED
Evaluation-first averaging, question-to-multiple-EPA mapping, PGY calculations,
EPA-grouped question PDFs, form-wording recovery, encrypted GitHub storage, and
previous interface/performance improvements remain in place.

VALIDATION
520 automated tests passed, 5 skipped, and 8 subtests passed.
New tests cover all supplied range boundaries, unrounded values, zero versus
missing evidence, default migration, editable persisted rules, paired workbook
structure, preserved identifiers/dates, encrypted save/reload, excluded residents,
concurrent updates, failed saves, and two-button UI behavior using a test double.
Older tests now explicitly configure their historical no-conversion fixtures
rather than assuming new installations have no default policy.
Both generated workbooks were independently imported and rendered using
artifact_tool. Numeric/letter cells, headers, dates, and worksheet counts checked.
The real Streamlit browser interface and an actual Data Hub import were not run.
