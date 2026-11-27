EPA TRACKER: TODAY'S DATE AND REMEMBERED RESIDENT LINKS
Updated November 26, 2026

INSTALL

This small update is for the EPA Tracker that already has
Admin > Competency reports and comments, including the missing-comments fix.

Replace these two files in the repository used by your Streamlit app:
  epa_competency_reports.py
  epa_competency_ui.py

Let Streamlit restart after the files are updated. For a local installation,
replace them in the same folder as epa_tracker.py and restart Streamlit.
Keep your existing secrets and saved archive. No new setup is required.

WHAT CHANGED

Through starts at today's date when you upload or open a report. Today uses
America/New_York so the date does not advance early at UTC midnight. You can
still choose a different cutoff. That choice stays in place while you work,
including after saving. Untouched defaults advance when the local day changes.

The app automatically selects a clear best resident match. It accepts an
omitted middle name or matching middle initial when the match is unique.
You can choose a different resident from the same dropdown.

Your usual Save reports and link comments or Update comments action remembers
the selected resident for later PDFs with that source name. The link survives
app restarts. The app checks again if the source name changes, the linked
resident's actual name changes, or the linked resident is no longer available.
Formatting differences in case, spacing, or name order keep the same link.
If two residents match equally, choose the correct one once and save.

Opening a saved report prefills the editor with today's date and the current
resident choice. Select Update comments to apply them to the saved report and
its PDF. Until then, the visible comments, report heading, and PDF use the
previously saved selection. A cutoff you enter yourself is retained for the
rest of your current session.

Only the two Python files above are needed for this patch. The readme, test
results, checksums, and tests/ folder are provided for reference.

The cumulative EPA_Tracker_Competency_Comments_Update.zip also includes this
change, along with the competency import and previous comment visibility fix.
Both packages contain synthetic test data only.
