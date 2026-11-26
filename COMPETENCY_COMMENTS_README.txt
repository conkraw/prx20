EPA TRACKER: COMPETENCY REPORTS AND ROTATION COMMENTS
Updated November 26, 2026

WHAT THIS UPDATE ADDS

Open Admin > Competency reports and comments.

Upload one or several original APPD General Pediatrics EPA-to-Milestones
crosswalk PDFs. The app reads the resident, program, season, PGY, and all 22
milestone recommendations from page 1. It preserves each printed value,
including ranges such as 2 - 2.5 and explicit zero values.

After you select the resident and comment dates, the app shows those competency
recommendations at the top and the original written evaluation feedback below,
grouped by rotation. Download combined report (.pdf) makes a printable copy.

The PDFs supplied for this update contain competency recommendations but no
evaluation comments or calendar year. Written feedback comes from the New
Innovations evaluation TAB files already saved in your EPA Tracker. If comments
are missing, upload the appropriate evaluation TAB files on the Upload page,
then check the resident link and date range.

INSTALL INTO YOUR EXISTING APP

Extract this ZIP. Copy its contents into the same application folder that
already contains epa_tracker.py, allowing the three existing application files
to be replaced. Keep the directory structure of tests/ if you copy the tests.

Required new files:
  epa_competency_import.py
  epa_competency_reports.py
  epa_competency_ui.py
  epa_competency_pdf.py
  epa_resident_comments.py

Required updated files:
  epa_simple_ui.py
  epa_tracker.py
  requirements.txt

Upload or commit all eight required files to the repository used by your
Streamlit app. Streamlit needs the updated requirements.txt because PDF import
uses pypdf==6.21.4. Let the app rebuild and restart. On a local installation,
run python3 -m pip install -r requirements.txt, then restart Streamlit.

The usual entry point is still epa_tracker.py. Keep your existing Streamlit
Secrets and encrypted archive. No new password, API key, service, database,
or data migration is required.

This is an update package for the EPA Tracker CBME Defaults / Two Exports
version supplied in this conversation. It is not a standalone application.

USING THE NEW SECTION

1. Open Admin, then select Competency reports and comments.
2. Choose Upload reports and select the original APPD PDF files.
3. Check the resident link and choose Comments from and Through. The initial
   dates come from your current Program and dates settings. PGY does not infer
   a calendar year. Expand Check extracted competency values if you want to
   compare the imported values with the source.
4. Select Save reports and link comments. The button includes the number of
   readable reports. The app opens the saved report view.
5. Choose a resident report, review the scores and rotation comments, and use
   Download combined report (.pdf).

Resident link, dates, and original PDF lets you correct a link or date range
and download the original uploaded document. New evaluations within the saved
comment dates appear after they are saved or after Reload saved data.

DETAILS THAT MATTER

- Competency recommendations remain source text. A range such as 2 - 2.5 is
  not averaged or rounded. These are APPD recommendations for CCC review.
- Page-three EPA input ratings are not imported into the app's EPA averages.
- Resident matching accepts a unique exact name, Last, First name order, or
  saved APPD export ID. Similar names and initials are not guessed. When more
  than one saved resident matches, the admin must choose the correct one.
- A report may be saved before evaluation comments exist. If later imports use
  a different name, correct the resident link in the saved report.
- Comment dates are inclusive. Date Completed is used first. Session Date is
  used and labeled when Date Completed is missing or invalid. Comments with
  neither date appear separately at the end and are not assigned to the period.
- Narrative feedback is included from saved evaluations even when their forms
  have not been confirmed for scoring or linked to EPAs. Missing rotation names
  appear under Rotation not recorded. Missing question wording is identified
  by Question ID.
- Original overall comments, written responses, and question comments remain
  attributable to their evaluation, question, evaluator, date, and source file.
  Resident comments are labeled separately. Numeric responses and scale labels
  are excluded from narrative feedback. Identical response/comment fields in
  one question are shown once. Repeated observations across evaluations remain.
- Pending incoming corrections remain in Data review. The active saved version
  supplies comments until you resolve that correction.
- Re-uploading the same PDF updates its link and dates without adding a second
  copy. Different source PDFs remain separate saved reports. Concurrent changes
  to a report require reloading before saving over that report.
- Reports, original PDFs, and links are saved in the existing encrypted archive.
  Comments and PDFs are parsed locally by the app. This feature uses no AI API.
- Upload original downloadable text-based PDFs. Scanned images, incomplete
  tables, and unsupported layouts receive a clear message instead of guessed
  values. Maximums are 15 MB and 20 pages per PDF, 20 files and 30 MB per batch.
  The existing total archive limit still applies.

VALIDATION

All three supplied resident PDFs were read successfully with 22 values each
and checked against their visible page-one tables. Synthetic tests cover
round-trip encrypted persistence, resident identity, ambiguous names, dates,
repeat uploads, concurrent saves, missing forms, and long comments. Streamlit
AppTest exercises upload, save, view, edit, and the real PDF download callback.

The new workflow also checks that saving a competency report preserves existing
EPA averages and both previously approved Excel export values. See the included
COMPETENCY_COMMENTS_TEST_RESULTS.txt for the final run and its environment.

The two existing PDF navigation test files were updated to use the already
shipped Apply filters button and current Admin navigation. Their six failures
were reproduced on the original unmodified app before these test corrections.
All original assertions were retained.

The included example PDF contains invented names and comments. No uploaded
resident reports, live evaluation data, passwords, or encrypted archives are
included in this update package.
