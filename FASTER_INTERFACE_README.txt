EPA TRACKER: FASTER INTERFACE UPDATE
Interface v2026.11

BASE VERSION
This update is based on EPA_Tracker_Grouped_Question_PDF_Full.zip.
It keeps the question tables grouped by EPA, including original question numbers,
source forms, question averages, counts, and the form checklist.
You do not need the earlier evaluation-by-evaluation audit update.

INSTALL THE UPDATE
Extract EPA_Tracker_Faster_Interface_Update.zip.
Upload all eight Python files into the same GitHub folder as epa_tracker.py.
Replace the six existing files and add epa_performance.py and epa_style.py.
Commit the files together, reboot the Streamlit app, and refresh the browser.
The sidebar displays Interface v2026.11 after the update is loaded.

Files:
  epa_tracker.py          Replace
  epa_auth.py             Replace
  epa_appd.py             Replace
  epa_simple_ui.py        Replace
  epa_workflow.py         Replace
  epa_form_ui.py          Replace
  epa_performance.py      Add
  epa_style.py            Add

Keep every other application file. Keep your existing Streamlit Secrets,
password hash, encryption key, requirements, template, and encrypted archive.
No data migration or re-upload is needed. Do not delete the saved archive.
The complete package is an alternative source for all application files, not
an instruction to replace your existing Secrets or archive.

WHAT STAFF WILL NOTICE
Sign-in: The password form is cleared after a successful password check, before
loading the archive. A separate loading message explains what is happening.
The previous extra rerun after successful login is removed.

Dashboard: Only the selected report is computed. Monthly and cumulative reports
are no longer calculated just to open the dashboard. Recent read-only results
are reused within the signed-in session.

Filters: Open Report filters, choose the changes, and click Apply filters once.
The table and PDF downloads use the last applied filters. The active selection
is shown above the results. APPD Export still uses its own saved reporting period.

Admin: The four main pages remain Dashboard, Upload, APPD Export, and Admin.
Data review loads only its selected section. Form discovery, observed response
codes, and removal previews can be reused until the underlying archive changes.
Destructive actions still validate the latest saved archive before writing.

Appearance: A cleaner login, teal navigation, clearer spacing, bordered summary
cards, and consistent controls. The design uses local styles and system fonts.
It adds no external scripts, font downloads, or new dependencies.

Exports: Excel processing starts when requested. A prepared Excel download stays
available for that archive and filter selection while it remains in the bounded
session cache. PDFs still build only when downloaded. The Data Hub workbook is
rechecked against the latest shared archive when downloaded.

Saved vs loaded: The sidebar shows the archive's saved time and the time your
session loaded it. Use Reload saved data to see changes another colleague saved.
Save unfinished page edits before reloading.

WHAT HAS NOT CHANGED
Numeric scores and the averaging method are unchanged: average linked, valid
questions within each evaluation, then give each completed evaluation equal
weight. Missing scores do not become zero.
Multiple EPAs per question, question wording, original numbering, form removal,
July 1 start-date PGY, the grouped PDF, and Data Hub approval checks are kept.
This update does not change CALCULATION_VERSION or force new approval by itself.
A source-data or configuration change still requires the usual new review.

CACHE AND SECURITY
Derived results are stored only in the current browser session's server memory.
They are not shared through a global cache or written to plaintext files.
The cache is invalidated after a successful save, archive load, or reload.
Signing out or expiring the session clears it with the rest of the session.
It is bounded to 16 entries and an estimated 64 MiB of derived cached values.
The active archive, current calculation, and download snapshots are separate
from that estimate. Oversized results are computed without retaining a cache.
Password hashing, throttling, inactivity checks, encryption, optimistic write
conflict checks, and live download validation have not been weakened.

VALIDATION AND LIMITS
See PERFORMANCE_TEST_RESULTS.txt for exact results and benchmark scope.
The Streamlit runtime and a browser engine were not available in this environment.
UI control flow was tested with a simulated Streamlit interface, not a live browser.
Your hosted app, private archive, and GitHub network connection were not accessed.
Server startup and archive network time can still cause a delay. The local timing
results are not a guarantee of your deployed app's end-to-end speed.
