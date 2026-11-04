# Remove an unwanted EPA form

## Update your existing app

Extract EPA_Tracker_Remove_Form_Update.zip. In the same GitHub folder as your current epa_tracker.py:

- Replace epa_tracker.py.
- Add epa_form_tools.py and epa_form_ui.py.

Commit the three Python files together, then reboot the Streamlit app and refresh your browser. Upload the extracted files, not the ZIP itself. The README and tests folder are optional for deployment.

Keep all your other app files, requirements.txt, Streamlit Secrets, APPD template, and encrypted archive unchanged. This patch is based on the latest Multiple-EPA update. It preserves that update's question multiselects, evaluation-first averages, local score labels, APPD configuration, roster, and exports.

EPA_Tracker_Streamlit_Remove_Form.zip contains the complete updated application as an alternative. Do not replace your own Secrets or encrypted data with examples from any package.

## Use the new control

Open Forms, select the unwanted form, and expand Remove this form directly below the selector.

The panel shows the number of linked saved evaluations, residents affected, and related pending duplicate/correction decisions. The count covers all stored dates, not just the Dashboard date filter.

Check the acknowledgement, type REMOVE in capitals, and click Remove form and linked evaluations.

Nothing is removed merely by opening the panel. The action button stays disabled until you confirm. The change is saved through the same encrypted GitHub transaction as your other edits. A success notice appears only after saving succeeds.

## What removal does

It removes the selected form's definition, question selections, scoring scales, APPD question links, uniquely matched evaluations, and related pending duplicate/correction decisions from the active archive. Other forms and their evaluations are preserved. The app matches by question IDs, not the displayed form name.

It also cleans duplicate-identity references and decision history associated with the removed records. This prevents dangling references and allows an intentional re-import later.

Resident roster entries, program settings, PGY assignments, conversion settings, and other forms' mappings are retained. APPD reviews are retained for history, but their approvals are invalidated because the evidence changed. Review and approve a fresh export before downloading an APPD upload.

Dashboard/PDF calculations use the remaining active evaluations. Stale upload previews, selections, and unsaved APPD export drafts are cleared after removal so an old preview is not accidentally reused.

An unused form can be removed. The final form can also be removed. An empty saved archive is not treated as a new installation, so deleted seed forms are not silently recreated on reload.

## Safeguards and recovery

If an evaluation could belong to more than one form, removal is blocked rather than guessing which data to delete or silently reassigning the record. Review the question IDs under Review > Unrecognized forms and upload the correct definition first. Cross-form or unrecognized duplicate/correction decisions must also be resolved before deletion.

The save operation reloads the latest archive. Changes to the selected form or affected records require a new confirmation. Unrelated concurrent uploads are preserved.

There is no undo button. Keep your original TAB files. You can deliberately re-upload a form and its evaluations later, then reconfirm its scoring and question mappings. Removal is not an upload exclusion rule, and re-uploading can add deleted data back.

This removes data from the current active archive, not from older encrypted GitHub versions or files already downloaded to another computer. No live archive was accessed or modified while preparing this update.

## Validation

167 automated tests passed, with 8 passing subtests. One pre-existing test was skipped because resident source exports are intentionally not distributed with the application.

The checks cover deletion scope, confirmation, concurrent changes, empty archives, real Fernet encryption/decryption, save failure behavior, approval invalidation, re-import, calculation and export regressions, and UI control logic using a Streamlit test double. The existing calculation, storage, template, and PDF modules are unchanged.

The live Streamlit browser interface and your GitHub deployment were not tested. Streamlit was not available in this execution environment, and installing it was not possible because outbound package access was unavailable.

To run these non-browser tests in a development environment with the existing application dependencies plus pytest:

    python -m pytest tests/test_form_removal.py tests/test_form_removal_ui.py tests/test_core.py tests/test_appd.py tests/test_acceptance.py tests/test_storage.py tests/test_requested_averaging.py tests/test_mapping_widgets.py -q
