EPA TRACKER: SIMPLER APPD EXPORT
Release: November 12, 2026
Based on: EPA_Tracker_Faster_Interface_Full.zip

INSTALL
Upload these four Python files into the same GitHub directory as epa_tracker.py:
    epa_appd.py          (replace)
    epa_workflow.py      (replace)
    epa_simple_ui.py     (replace)
    epa_export_rules.py  (new)
Commit them together, reboot Streamlit, and refresh the browser.
Do not replace your Secrets, encryption key, requirements, template, or archive.
No evaluation re-upload or mapping rebuild is needed.

DAILY USE
Open APPD Export. Review the original numeric averages and included residents.
Click "Approve, save and prepare Data Hub file" and then "Download Data Hub file (.xlsx)".
No separate Save draft button, reviewer-name field, or CCC checkbox is required.
The approval button itself records explicit submission approval and its time.
This shared-password app does not identify an individual reviewer. The archive
records that limitation rather than inventing a reviewer's name.

PARAMETERS IN ONE LOCATION
Open "Upload conversion parameters" on APPD Export.
Existing saved conversion rules are preserved, including their original
maximum-inclusive boundaries. They are not replaced with the example.
New rules can use minimum-inclusive cutoffs for 1, 2a, 2b, 3a, 3b, 4 and 5.
For example, a minimum of 3.75 for code 4 converts 3.77 to 4 in the upload.
Numeric 4 is the same level as 4.0. The worksheet retains the template's code format.
The original review value remains 3.77.

Use "Test a score" to check the rule before approving. "Preview converted upload
values" shows the actual upload categories without changing the first table.
Rules are saved together with the review when the approval button is clicked.
A rule change invalidates any old approved download until approved again.

OPTIONAL EXAMPLE, NOT A RESIDENCY POLICY OR APPD RECOMMENDATION
"Use example cutoffs (3.77 to 4)" loads an editable, unsaved example:
    Minimum average    Upload code
    1.00               1
    1.75               2a
    2.25               2b
    2.75               3a
    3.25               3b
    3.75               4
    4.75               5
    Highest covered: 5.00
Each minimum is inclusive. The next minimum ends the preceding range.
Change these to your residency's approved parameters before approval. The complete
residency policy was not supplied. Opening the page does not activate this example.
Use full-precision averages for conversion, not the rounded value shown for display.
The official scale places a before b. Only 2 and 3 have a/b subdivisions in this
Data Hub template. Do not use the old, reversed local a/b display convention.

REVIEW NOTES VERSUS INVALID FILES
Missing or unconfirmed mapping/scoring, excluded questions, unknown form IDs,
pending upload conflicts, and missing source dates remain visible as review notes.
They do not automatically prevent approval. Only confirmed valid available scores
are counted. Accepting a note does not fix the underlying issue or add missing data.
Blank EPA cells upload as not observed (0). They never become zeros in an average.
Clearing a calculated numeric score explicitly creates a review note and an audit
entry before it can be submitted as not observed. Raw evaluation scores remain intact.

Missing resident ID, assessment date or PGY, duplicate identities, no selected
residents, invalid/uncovered conversion values, or an unavailable template still
prevent an upload-ready file. These cannot be safely guessed. Enter the missing
field, correct the parameter, or uncheck the affected resident.

SAVING AND DOWNLOADS
Approval saves numeric values, converted categories, parameter choices, accepted
review notes, and the approval time in the existing encrypted archive transaction.
The workbook is validated/generated before approval is saved. A failed save does
not unlock a download. Downloads recheck the latest shared archive and approval.
Changing a score, rule, mapping, reporting period, or resident setup requires another
approval. Other users' saved changes are not overwritten silently.
No network request is added merely to open the export page.

UNCHANGED
Question-to-EPA mappings, evaluation-first averaging, historical start-date PGY,
original source evaluations, question wording recovery, grouped question PDFs,
existing performance improvements, archive encryption, and other app pages.

TESTING
462 automated tests passed, five skipped, with eight additional subtests passing.
Tests use fictional data. They cover exact boundaries, 3.77 to 4, official a/b codes,
legacy rule preservation, nonblocking review notes, required upload fields,
concurrent changes, encrypted save/reload, failed saves, and stale-download rejection.
A generated workbook was parsed independently and visually rendered. It retains
one sheet, the supplied first row, row-2 headers, text assessment dates, numeric PGY,
and only valid upload codes. No formula errors were found.
The live Streamlit browser and an actual APPD Data Hub import were not tested.

REFERENCE
User-supplied APPD upload template (September 2025).
APPD CBME Data Hub User Guide, September 23, 2025:
https://www.appd.org/wp-content/uploads/2025/09/APPD-CBME-Data-Hub-User-Guide-1.pdf
APPD specifies CCC-determined supervision levels. The cutoff table above is only a
configurable local example, not a validated equivalence supplied by APPD.
