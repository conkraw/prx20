# EPA Tracker: simplified numeric workflow

## Install this update in your existing app

Extract EPA_Tracker_Simple_Workflow_Update.zip. Upload all six Python files to the same GitHub folder as your current epa_tracker.py, then commit them together:

- epa_tracker.py: replace
- epa_appd.py: replace
- epa_appd_ui.py: replace
- epa_form_ui.py: replace
- epa_workflow.py: add
- epa_simple_ui.py: add

Reboot your Streamlit app and refresh the browser. Keep the other app files, appd_template.xlsx, requirements.txt, Streamlit Secrets, encryption key, and saved archive unchanged.

Do not upload the ZIP itself to replace the Python files. This is a code update, not a data import. You do not need to re-upload evaluations, re-enter start dates, or rebuild existing question links.

## What staff see

The sidebar now has Dashboard, Upload, APPD Export, and Admin.

Upload accepts the existing TAB files. Dashboard shows numeric reports and offers PDF downloads. Detailed report choices use one selector, and the analysis filters are collapsed until needed.

APPD Export opens directly on one numeric review table. A calculated 3.25 appears as 3.25, without a local letter label. The former separate averages and empty final-level tabs are gone. Edit only approved changes, save a draft when needed, then approve and download the Data Hub file.

Numeric edits affect this reporting-period review only. Original evaluations and calculated dashboard averages are not overwritten. Dates edited during review recalculate PGY from the saved residency start date.

## One Admin area

Residents: save each resident's July 1 PGY1 start date and Data Hub name or ID. A group-entry control is available for an entire starting class. PGY uses the assessment date and advances each July 1.

Forms and EPA mapping: choose a form, include the appropriate questions, assign one or more EPAs to each question, and confirm its response-code scale. One Save form setup button saves the settings and mappings together. Remove this form remains available here with its existing confirmation safeguards.

Program and dates: program identifier, season, year, and reporting-period dates. The technical GENERAL mapping is fixed automatically.

Data Hub rules: confirm the program's conversion from numeric scores to the Data Hub supervision categories once. Previously saved conversion limits are preserved.

Data review: resolve duplicate/correction decisions and unrecognized data.

Advanced: less frequently changed academic-year and storage information. The shared sign-in remains unchanged. This update does not add separate staff or administrator permissions.

## Important Data Hub format distinction

The APPD guide documents EPA upload values 0, 1, 2a, 2b, 3a, 3b, 4, and 5. It does not document decimal averages such as 3.25, or plain 2 and 3, as valid upload categories.

The app removes the old LOCAL letter labels from numeric reports. It does not remove official categories from the external file format. Reviewed numeric averages are converted only by the saved, confirmed program rule at export. No new cutoffs were invented in this update. Where no confirmed rule exists, numeric averages are visible and drafts can be saved, but the Data Hub download stays disabled until program leadership sets the rule.

Changing display formatting does not validate a clinical supervision determination. APPD expects CCC-determined levels. Each export still requires a reviewer and CCC-review confirmation. Routine staff do not need to choose an individual letter category in every cell.

Blank averages never count as zero. When scoring and mappings are complete, cells without scored evidence are represented as not observed (0) in the upload, subject to the CCC review confirmation. A cleared cell with existing scored evidence is not silently changed to not observed. Missing mappings, unconfirmed scales, unknown response codes, missing PGYs, and unresolved corrections block approval rather than generating a misleading ready file.

The numeric CSV under Details and checks is labeled as a report, not a Data Hub upload.

## Workbook checks

The existing APPD template is retained. The upload keeps one sheet, the information row, headers on row 2, text assessment dates in mm/dd/yyyy format, numeric PGY, GENERAL mapping, and one row per included resident. Only documented EPA codes are written to the upload cells. The filename uses program-season-year.xlsx.

Before EPA Import, trainees must already be registered in Data Hub with matching names or program-assigned IDs. This app does not connect to or upload directly into your Data Hub account.

Official guide, including upload instructions on page 17:
https://www.appd.org/wp-content/uploads/2025/09/APPD-CBME-Data-Hub-User-Guide-1.pdf

APPD purpose and CCC expectations:
https://www.appd.org/cbmedatahub/

## Preserved calculations and storage

A question can contribute once to each selected EPA. For each EPA, valid mapped question scores are averaged within a completed evaluation. The overall result is the equal-weight mean of those evaluation averages across preceptors and forms. No valid scores means that evaluation contributes no average. Missing, excluded, unconfirmed, and unmapped answers are not zero. Question-level reports remain numeric.

Changes continue through the existing encrypted GitHub save transaction. New numeric review snapshots and override records are part of that encrypted archive. Concurrent changes are checked before saves and before downloading an approved file. Old approvals are not silently reused after this update or after inputs change.

## Verification

278 automated tests passed, plus 8 subtests. Five items were skipped: four modules needing the unavailable Streamlit runtime and one existing source-file-dependent test. UI-contract tests used a simulated interface. Source compilation and generated workbook structure were checked.

The deployed browser interface, your live GitHub archive, and a real Data Hub import were not tested here. No live data or cloud configuration was changed while preparing this update.
