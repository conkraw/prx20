EPA QUESTION TABLES GROUPED BY EPA
November 11, 2026

This replaces the proposed detailed audit report. Install directly on the
Question Wording Fixed app. The previous EPA Audit update is not required.

INSTALL
Replace epa_tracker.py and epa_pdf.py in your existing app directory.
Add epa_grouped_pdf.py in the same directory.
Commit all three files together, reboot Streamlit, and refresh your browser.

Keep your Secrets, encryption key, archive, dependencies, template, and all
other application files. No evaluations or mappings need to be re-entered.
This patch has no archive-writing code and does not change calculation rules.

USE
Use the existing Download PDF or Download PDF for all residents button.
The Dashboard date, resident, and form filters still control the report.
To inspect all saved forms in the selected dates, leave Forms at All forms.
The bulk download broadens only the resident filter, as before.

LAYOUT
Resident
  EPA 1
    Source form name and evaluation count
      Original question number | Question wording | Average | n
    Next source form, with its own original table
  EPA 2
    The same table structure
  Remaining mapped EPAs in numerical order, through EPA 12
  No EPA assigned
    Original tables for questions without saved EPA links
  Form checklist

The original question-average code supplies every value. Grouping does not
change averages, denominators, scoring selections, exclusions, or wording.
Question 10 remains Question 10, even if earlier questions are under another EPA.
A question linked to multiple EPAs appears once within each linked EPA.
Different source forms are not combined into a single question-average row.
The PDF does not list individual preceptors or an evaluation-by-evaluation ledger.

MAPPING AND FORM CHECKS
The report uses only saved links, never inferred links based on question wording.
Unconfirmed or outdated links remain visible for review but are labeled as not
currently contributing to mapped EPA totals. Question averages are unchanged.
Scoring that has not been confirmed leaves numeric cells blank as before.

The checklist includes every saved form definition for each reported resident:
Saved = all saved evaluations for that resident and form.
Period = evaluations in the selected dates, before applying the form filter.
In PDF = evaluations represented in the printed question tables.
It identifies excluded forms, missing links, unconfirmed scoring, selected but
unassigned questions, and linked but excluded questions.
Unrecognized/ambiguous evaluations in the selected dates and missing-date
records are flagged separately with their source filenames when available.
The checklist can only inspect the saved archive. It cannot identify a file
that has never been uploaded or prove that the original source export is complete.

CALCULATIONS
Nothing changes in the existing EPA calculation: average scored mapped questions
within each evaluation, then average the evaluation averages equally.
The printed question averages are not a substitute formula for the overall EPA
average. This PDF is a mapping and form-coverage review, not a different calculator.

PREVIEW
The separate preview PDF uses the question wording and aggregate values in the
user-supplied Inpatient/Pediatric Emergency Medicine report. EPA assignments are
explicitly illustrative, not recovered program mappings. The resident name is
omitted. No preview mapping or resident data is included in the app archive.

VALIDATION
354 tests passed, 5 skipped, and 8 subtests passed in this environment.
Includes 27 new grouping, numbering, coverage, PDF rendering, and snapshot tests.
The preview was rendered and visually inspected.
The live Streamlit interface and your saved encrypted archive were not tested.
