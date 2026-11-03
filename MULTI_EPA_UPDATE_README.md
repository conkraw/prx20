# Multiple-EPA mapping and evaluation-first averaging

## Install into your existing app

Extract EPA_Tracker_Multi_EPA_Update.zip. Replace these four files in the same GitHub folder as your current epa_tracker.py:

    epa_tracker.py
    epa_core.py
    epa_appd.py
    epa_appd_ui.py

Commit the files together, then reboot the Streamlit app and refresh the browser. Keep the existing appd_template.xlsx, remaining app files, requirements.txt, Secrets, and encrypted archive. Do not upload the ZIP itself as an app file.

No evaluation re-upload or manual archive migration is required. Existing mappings, raw responses, question selections, response-code scales, resident IDs, PGYs, program settings, and score-conversion thresholds are retained. The update does not change code 6 or any other saved response mapping.

The tests folder and this README are optional for deployment. This ZIP is a patch for an app that already has the APPD update, not a standalone application.

## Link a question to several EPAs

Open Admin > Question mapping and select a form. Every question now has a separate multiple-selection box. Select every EPA that applies, confirm the links, and click Save question mapping. For example, the same question can have EPA 1, EPA 2, and EPA 7 selected together.

The saved links are identified by form and question ID. They apply to existing and future matching evaluations. Selecting a second EPA does not remove the first. A response contributes once to each linked EPA, never twice to the same EPA. Existing one-EPA mappings load as one selected value.

A linked question must still be included and have a confirmed numeric scoring scale under Forms. Unselected questions do not begin contributing merely because they have an EPA link.

## Calculation rule

For each resident and each EPA:

1. Within each completed evaluation, average the valid scored questions linked to that EPA.
2. Average those evaluation averages. Each completed evaluation with at least one valid score gets equal weight.

For example, one evaluation has scores 3 and 4 for an EPA, giving 3.50. A second evaluation has scores 3 and 3, giving 3.00. The resident's EPA average is (3.50 + 3.00) / 2 = 3.25.

The rule also applies when the number of scored questions differs between evaluations. A form with more answered questions receives no extra weight. Missing, excluded, unmapped, and unconfirmed answers are not zeros. An evaluation without valid scored answers for an EPA is omitted from its denominator.

When several forms map to one EPA, every individual evaluation is still weighted equally. The app does not average form averages with equal form weights. Two distinct evaluations from the same preceptor count as two evaluations. Duplicate uploads remain deduplicated.

Dashboard form-level summaries and monthly/cumulative reports now also use evaluation-first weighting. Cumulative results use the underlying evaluations rather than averaging monthly means. Individual question averages and question-level PDFs retain their existing calculation.

A legacy pooled-ratings setting no longer controls calculations. The effective setting is evaluation-first. Its saved value is updated the next time program settings are saved. Source evaluations are not rewritten.

## Requested local labels

A Local level column is shown beside numeric averages. The default convention is:

| Numeric average | Local level |
| --- | --- |
| 3.00 | 3 |
| 3.25 | 3b |
| 3.50 | 3b |
| 3.75 | 3a |
| 4.00 | 4 |

More generally, exact whole numbers stay whole. A fractional part above zero through 0.50 gets b. A fractional part above 0.50 gets a. The whole-number portion stays the same. The label is calculated from the unrounded overall average, not by averaging labels. Only floating-point precision noise at an integer or cutoff is tolerated.

Admin > Score conversion lets you change the fractional order or cutoff and save that rule in the encrypted archive. Labels appear automatically with the default rule for archives that do not yet have these settings.

## Local labels and APPD upload levels are different

APPD accepts 0, 1, 2a, 2b, 3a, 3b, 4, and 5. Its scale defines 3a as close support and 3b as moderate support. Thus 3b represents greater independence than 3a. Plain 2 and 3 are not accepted upload categories. Codes such as 1b or 4a are also local labels only.

Your requested display convention is therefore NOT an automatic APPD conversion. The app never copies the Local level column to the APPD workbook. Existing, separately confirmed APPD threshold suggestions remain available and are preserved. Otherwise, enter the official final levels in APPD Export and save CCC approval.

Neither the local label convention nor numeric thresholds constitute an APPD-validated conversion from faculty-rating averages. APPD requests CCC-determined supervision levels.

Official sources:
https://www.appd.org/cbmedatahub/
https://www.appd.org/wp-content/uploads/2025/09/APPD-CBME-Data-Hub-User-Guide-1.pdf
See the Instructions screenshot on PDF page 17 for the upload scale.

## Check the calculation

Open APPD Export > Averages and evidence. The summary shows the numeric average, Local level, rating count, and number of scored evaluations. Expand Step 1 to see each evaluator's within-evaluation EPA average and the number of scored questions. Step 2 groups these evaluation averages by form for comparison.

The new CSV downloads include EPA averages with local labels and the intermediate per-evaluation averages. These are review files, not APPD upload templates. The existing APPD Excel download still uses the approved official categories and original template format.

Old CCC approvals are invalidated by this calculation-version change. Their saved snapshots are not deleted, but the updated results require another review and approval before export.

## Verification

140 tests passed, 1 test was skipped, and 8 additional subtests passed with invented data. Checks include calculation boundaries, independent multi-EPA links, mapping save/reload logic, preservation of source data, unequal question counts, empty evaluations, duplicate uploads, cross-form weighting, date filters, PGY behavior, stale approval protection, encrypted-storage behavior, workbook structure, and PDF regressions. All app modules compile.

Mapping widget logic was tested with a Streamlit test double. The real browser interface and an actual APPD upload were not tested in this environment. Streamlit is not installed here, and network restrictions prevented installing it. Existing deployment dependencies were not changed.

Reproduce the exercised checks after installing your app's dependencies plus pytest and pypdf:

    python -m pytest -q tests/test_core.py tests/test_appd.py tests/test_acceptance.py tests/test_storage.py tests/test_requested_averaging.py tests/test_mapping_widgets.py tests/test_pdf.py
