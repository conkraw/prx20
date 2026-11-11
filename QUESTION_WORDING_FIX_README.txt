EPA Tracker: preserve and restore question wording
Version: 2026-11-11

INSTALL THIS PATCH

In the same GitHub directory as your existing epa_tracker.py, replace:
  epa_tracker.py
  epa_core.py
  epa_form_discovery.py

Add these two new files in that same directory:
  epa_question_text.py
  epa_question_text_ui.py

Commit all five Python files together. Reboot the Streamlit app and refresh the
browser. Keep the existing Secrets, encryption key, requirements.txt,
appd_template.xlsx, seed_forms.json, and encrypted archive unchanged.
Do not replace an existing archive with a new empty file.

RESTORE THE RADIOLOGY QUESTIONS

Open Admin > Forms and EPA mapping and select Radiology.
A Restore question wording panel appears when the selected form has missing
wording or ID-only placeholders.

When matching wording is still available in saved data, review the preview and
click Restore saved wording.

Otherwise, upload the original Radiology form-definition TAB file in this
panel, review the matching question IDs and wording, and click Restore wording
from file. The form-definition export has QuestionID and Question Part/Text
columns. A resident evaluation file containing only question IDs and responses
cannot reconstruct the missing wording.

The repair fills missing wording only. It keeps the selected form ID, question
order, included/excluded choices, scoring maps, EPA assignments, resident start
dates, and saved evaluations. Extra questions in a definition file are not
added by the repair. Unmatched questions stay in the selected form.

After restoration, review the displayed questions, check the scoring and
question-to-EPA confirmations, and click Save form setup. Wording changes
require this review before the form contributes confirmed ratings again.
Existing EPA assignments remain available rather than being cleared.

Do not delete or recreate Radiology. Do not re-upload all resident evaluations.
Save any unfinished mapping or scoring edits before using the repair panel.

WHAT WAS FIXED

The earlier unrecognized-form registration generated placeholders for every
question without looking for wording already saved under the same question ID.
Registration now reuses unambiguous saved wording when available. New forms
still require their own inclusion, scoring, and EPA-mapping confirmation.

Re-importing the same definition no longer replaces real wording with blank or
ID-only text. Existing question selections, scoring overrides, and EPA mappings
are kept. Genuine wording changes retain prior wording in the encrypted form
record and require review.

Evaluation exports with explicit question-text or question-part columns retain
that metadata. Adding this metadata to an otherwise duplicate evaluation does
not create another evaluation or change its answer content hash. Comments,
response values, dropdown labels, and rotation names are never used as prompts.

Repairs are previewed and saved through the existing encrypted GitHub
transaction. Concurrent changes to the affected form, its EPA links, or the
proposed wording block a stale repair rather than overwriting another user's
work. Conflicting saved wording is never selected automatically.

LIMITATIONS

The patch cannot recreate wording that is absent from the current archive and
all available source definitions. It does not automatically access historical
GitHub commits. Use the original form-definition TAB file when the saved-wording
option cannot recover a question.

This package has not been deployed to your repository or connected to your live
archive. Install it as described above. The live Streamlit browser interface and
an actual APPD Data Hub upload were not tested.

VALIDATION

327 automated tests passed, with 5 skipped and 8 subtests passed.
49 added tests cover wording preservation, registration, explicit prompt parsing,
wrong-file rejection, partial/superset definition recovery, concurrent edits,
encrypted save/reload, EPA-link preservation, and simulated repair controls.

Four test modules were skipped because the Streamlit runtime is unavailable.
One existing test was skipped because supplied resident exports are intentionally
excluded from the application package. No resident export or live archive is
included in either distribution.

Run the tests from the application directory:
  PYTHONPATH=.:tests pytest -q -ra
