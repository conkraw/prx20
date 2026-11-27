EPA TRACKER: SAVED COMMENT VISIBILITY FIX
November 26, 2026

INSTALL

This small fix applies to the Competency reports and comments update.
Replace these four files in the same repository folder as epa_tracker.py:

  epa_resident_comments.py
  epa_competency_reports.py
  epa_competency_ui.py
  epa_competency_pdf.py

Restart the Streamlit app after all four files are updated. This fix uses the
same installed dependencies, saved evaluations, and saved competency PDFs.

SHOW THE SAVED COMMENTS

Open Admin > Competency reports and comments and select the resident report.
The Resident and dates for comments form is now visible above the comments.
Choose the correct saved resident name, including the middle name if present.
Choose the comment date range and Match comments by, then select Update comments.

For feedback organized by the rotation, choose Rotation / Session Date.
For example, a September 21 rotation completed by the evaluator on November 18
belongs to September when matched by its session date. The same evaluation
belongs to November when matched by its completion date.

New PDF imports default to Rotation / Session Date. Existing saved reports
retain their previous completion-date rule until you choose and save a change.
The selected basis and both source dates are shown in the report.

WHAT WAS FIXED

The previous comments view used one generic empty message for several different
conditions. That could look like missing comments even when written feedback
was already saved but excluded by the resident link or reporting dates.

The updated view distinguishes:
  - No saved evaluations matching the currently linked resident name.
  - Saved evaluations with no written feedback in their fields.
  - Saved comments that fall outside the selected dates.
  - Saved comments with no usable date, shown separately.

Check included and excluded evaluations shows the rotation, Session Date,
Date Completed, date used, comment-field count, and inclusion status. It also
shows when the other date basis would include additional saved feedback.

Shortened names in competency PDFs can differ from full names in saved
evaluations. The app now shows possible saved-name matches, including omitted
middle names and approved APPD export aliases. These are suggestions for the
admin to choose, not automatic resident merges.

All question comments are read, including comments in the middle of a form
with scored questions after them. Question position, scoring confirmation,
and EPA mapping do not determine whether saved narrative comments are shown.

VERIFICATION

The tests reproduce a form containing two comment questions followed by
numeric questions, a September session completed in November, and a shortened
PDF name differing from the saved resident's full name. The UI tests exercise
changing the resident and date basis, saving, and the actual PDF callback.
Existing EPA averaging and both APPD Excel outputs are covered by regression
tests. See COMMENT_VISIBILITY_TEST_RESULTS.txt for the final run.

The screenshots supplied with this issue do not show the selected report name
and date range. They establish that the comments are saved, but do not identify
which filter excluded them. The new diagnostics make that visible in the app.

Tests use invented names and narrative feedback. This package contains no
resident evaluation records, uploaded reports, passwords, or live archives.
