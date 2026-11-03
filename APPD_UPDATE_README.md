> Superseded details: MULTI_EPA_UPDATE_README.md describes the newest patch. It replaces pooled-rating choices with evaluation-first weighting and adds separate local display labels.

# APPD export update

This update adds Admin setup and APPD Excel export to the existing EPA Tracker. It does not change the app's entry point, password, GitHub credentials, encryption key, archive path, or existing PDF workflow.

## Install into your existing repository

Extract EPA_Tracker_APPD_Update.zip. Upload these five files into the same repository folder that contains your current epa_tracker.py:

1. epa_tracker.py: replaces the existing file.
2. epa_core.py: replaces the existing file.
3. epa_appd.py: new file.
4. epa_appd_ui.py: new file.
5. appd_template.xlsx: new file, required for the Excel download.

APPD_UPDATE_README.md is this instruction file and can also be uploaded. The tests folder is optional for deployment.

Keep the remaining app files, requirements.txt, Streamlit Secrets, and encrypted archive unchanged. Commit the files and restart or reboot the existing Streamlit app. The entry point remains epa_tracker.py. Do not upload the ZIP itself as the app code.

The separate EPA_Tracker_Streamlit_APPD.zip contains the complete updated application. Its epa_tracker folder contains the same five updated files and the existing supporting files.

## First-time setup

Open Admin, then Program setup. Confirm your program ID or name, reporting year, FALL or SPRING, and evaluation date range. This release supports the supplied GENERAL mapping and its 12 general-pediatrics EPAs. The template's mapping field is different from the question-to-EPA links configured below.

Assessment date defaults to the latest Date Completed for each resident among evaluations in the selected date range. It is not the upload date. You can instead select the latest Session Date or one common CCC assessment date. APPD describes this field as the date the EPA levels were collected or determined. Use the CCC date when it better represents the final determination. Individual dates can also be corrected in the export review.

Open Resident roster. All residents with stored evaluations appear automatically. The two supplied TAB exports do not have a PGY column. Enter a PGY override for each resident for the applicable academic year. Enter a program-assigned ID instead of a name when needed. Saved PGY overrides do not automatically advance the following year. A future TAB export with a supported explicit PGY field can populate Detected PGY. Ambiguous values require review, and rotation labels such as Intern or Senior are not used to infer PGY.

Open Question mapping. Select a form and assign one or more APPD EPAs to each relevant question. Leave unrelated questions unmapped. Confirm and save each form's links. Links use question IDs, not their order. Several forms can contribute to the same EPA, and one question can contribute to several EPAs. These links are not preassigned because the program needs to review their educational meaning. Existing records use saved links without reuploading.

The existing Forms section still controls which questions count toward averages and the response-to-numeric-score mapping. A question must be included and its scale confirmed before it contributes. Changes to form wording require the APPD mapping to be reconfirmed.

## Averages and final APPD levels

The app averages linked questions within each evaluation, then gives each evaluation equal weight when combining forms for an EPA. The latest patch standardizes this rule and no longer offers pooled-rating weighting. Missing, excluded, unmapped, and unconfirmed responses do not become zeros in averages. Counts and source responses are available in Averages and evidence.

APPD expects the categorical supervision levels 0, 1, 2a, 2b, 3a, 3b, 4, and 5. A faculty-rating average is not automatically a CCC-determined APPD level. Enter final levels directly or use the optional Score conversion section to configure your program's own average thresholds. Threshold suggestions are disabled by default and require confirmation. They are local suggestions, not an APPD-validated mathematical conversion. Final CCC review remains required.

## Export all residents

Open APPD Export. Review averages and evidence, then edit Final APPD levels. All residents with evaluations in the configured period are included in the draft. Clear Include for residents who should not be exported.

Confirm each included resident's identifier, assessment date, PGY, and all 12 EPA levels. Use 0 only for an EPA that was not observed. A separate button can mark unrated EPAs as 0 after mappings and scoring are confirmed. It does not fill cells with scored evidence or unresolved scoring issues.

Save an unfinished draft at any time. To enable the download, enter the CCC reviewer name or initials, confirm committee review, and save CCC approval. The download then creates one Excel file for all included residents. Changes to source data, mappings, roster, or setup invalidate earlier approval and require a new review. The download rechecks the saved archive so an older browser session cannot export an outdated approval.

The file uses the supplied template's exact headers on row 2, one worksheet, text dates in mm/dd/yyyy format, a numeric PGY, and the filename programid-season-year.xlsx. It contains the final reviewed levels, not evaluation comments or the supporting evidence tables. The source workbook contained populated resident rows. Those rows were removed from the bundled blank template.

## Storage and access

Program settings, question links, resident roster, drafts, and approvals are stored inside the existing encrypted GitHub archive. The existing shared app sign-in protects Admin and APPD Export. This update does not create a separate administrator role or additional password. Anyone authorized to use the app under its current shared-password model has access to those screens.

Existing evaluation identities are unchanged. Reimporting a record to add explicit PGY metadata does not create an extra evaluation. Conflicting PGY metadata is flagged for review. No real evaluation exports or populated resident spreadsheets are included in this update package.

## Validation performed

See MULTI_EPA_UPDATE_README.md for the latest test results and limitations. The current patch does not change requirements or Secrets.
