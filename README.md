> Latest update: See MULTI_EPA_UPDATE_README.md for multiple-EPA selectors, evaluation-first weighting, local labels, and APPD export safeguards.

> APPD update: See APPD_UPDATE_README.md for Admin setup, per-form question mapping, PGY, and the reviewed all-resident Excel export.

# EPA Tracker for Streamlit

EPA Tracker recognizes evaluation forms by stable question IDs, remembers new forms, and reports each resident's EPA and question averages across an academic year. Forms, evaluations, scoring settings, and review decisions are encrypted before they are saved to GitHub.

The app is ready to configure and deploy. It has not been connected to your GitHub account or published from this package.

## What is included

| File | Purpose |
| --- | --- |
| `epa_tracker.py` | Streamlit app. Use this as the deployment entry point. |
| `epa_core.py` | File reading, form recognition, duplicate handling, and calculations. |
| `epa_storage.py` | Encrypted GitHub storage and protection against competing saves. |
| `epa_auth.py` | Password verification, sign-out, and session timeout. |
| `epa_pdf.py` | PDF question reports divided by EPA form and resident. |
| `seed_forms.json` | Acutes and Adolescent Medicine question definitions from the supplied forms. Contains no resident evaluations. |
| `requirements.txt` | Deployment dependency versions retained from the existing app. |
| `setup_secrets.html` | Opens in your browser and generates the Secrets settings without installing Python. |
| `setup_secrets.py` | Optional command-line version of the Secrets setup. |
| `START_HERE.md` | Short guide for deployment through the GitHub and Streamlit websites. |
| `secrets.toml.example` | Configuration template with placeholders only. |
| `.streamlit/config.toml` | Appearance and upload configuration. |
| `.gitignore` | Keeps local secrets and evaluation exports out of Git commits. |
| `tests/` | Automated checks using synthetic data. Tests against original files run only if those files are separately available. |

Upload the extracted files to your code repository together. Uploading just `epa_tracker.py` will not work because it imports the other modules and loads `seed_forms.json`.

## Set up GitHub and Streamlit

### 1. Create the repositories

Create a code repository, for example `epa-tracker`, and upload the contents of this folder. The main Python files, seed file, and `requirements.txt` should be at the repository root.

Create a separate private data repository, for example `epa-data`. Add a README when creating it so that the `main` branch exists. This repository will hold `data/epa_store.enc`, which the app creates on its first save. Keeping the data in its own repository separates routine archive changes from app deployment changes.

Use your institution's approved accounts and hosting arrangements for resident evaluations. The code repository may be private or public according to your deployment arrangement. Original evaluation exports and actual Secrets do not belong in the code repository.

### 2. Create a GitHub token

Create a fine-grained personal access token limited to the data repository. Give it Contents read and write permission. Retain any organization approval required for the repository. The app does not need access to unrelated repositories.

The token goes into Streamlit Secrets, along with the data repository's `owner/repository` name. Do not place it in a Python file.

### 3. Generate your password configuration

Open `setup_secrets.html` from the extracted folder in Chrome or Edge. It runs in your browser and does not require a local Python installation. Choose and confirm an app password of at least 12 characters. Enter the data repository and GitHub token, or fill those placeholders in later. Generate and copy the settings.

The helper creates the password hash and encryption key locally in browser memory. It does not send your entries to a server, write them into the HTML file, or store them in browser storage. Its output includes the GitHub token and encryption key, so paste it only into your private Streamlit Secrets settings and keep a secure backup. Remember the password you chose before the input fields clear.

This helper is for the first deployment. If you already have a saved archive, preserve its existing encryption key. Do not replace that key with a newly generated one.

The configuration has this structure:

```toml
[app]
password_hash = "GENERATED_PASSWORD_HASH"
session_minutes = 30

[github]
token = "YOUR_GITHUB_TOKEN"
repo = "YOUR_USERNAME/epa-data"
branch = "main"
path = "data/epa_store.enc"

[security]
fernet_key = "GENERATED_ENCRYPTION_KEY"
```

Keep the encryption key in a secure backup. Use the same key for the existing archive. Generating a different key later will make the saved archive unreadable until you restore the original key.

### 4. Deploy

In Streamlit Community Cloud, create an app from your code repository, choose its branch, and set the main file to `epa_tracker.py`. Choose Python 3.12 in Advanced settings and paste the completed configuration into Secrets. Deploy the app.

For an existing app, update Secrets through its settings. The app stays locked until it has a valid password configuration. It loads the GitHub archive only after successful login.

To run locally instead, put the completed configuration in `.streamlit/secrets.toml` and run:

```bash
python -m pip install -r requirements.txt
python -m streamlit run epa_tracker.py
```

Do not commit `.streamlit/secrets.toml` to GitHub.

If you prefer a terminal for first-time setup, `python setup_secrets.py` remains available after installing the requirements. It generates the same configuration format as the browser helper.

## First use with the supplied files

1. Sign in with the password you chose.
2. Open Upload and select `acutes_evaluation.tab` and `adol_epa_evaluation.tab`. Choose Review uploads, then Save valid files to GitHub.
3. Open Forms and review the Acutes EPA and Adolescent Medicine EPA definitions. Their question IDs are already included in this package. Confirm which questions belong in the EPA average and map the response codes using the original rating labels.
4. Save each form's scoring settings, then open Dashboard. Choose the academic year, months, residents, and EPAs you want to report.

The supplied evaluation exports contain 20 evaluations across 17 normalized resident names. The app's proposed selections include nine Acutes EPA questions and seven Adolescent Medicine EPA questions. The time-with-resident question, separate administrative ratings, discussion confirmation, and narrative questions are initially excluded from the composite. You can review and change that selection.

### The response code 6 needs confirmation

The supplied .tab files contain numeric responses but do not include their rating labels. A response of 6 appears in both evaluation types. Its meaning cannot be established from these exports alone.

The starting configuration proposes scores 1 through 5 and excludes 6, but scoring is disabled until you explicitly confirm the setup. If 6 means unable to assess, leave it unchecked. If it is a valid score, check Count in average and enter 6, or the actual score associated with that response code. Confirm every other code against the original scale as well.

After confirmation, newly encountered unmapped codes are flagged and excluded until reviewed. They are never silently assumed to be valid numbers. A blank response always stays missing. If two questions use different rating systems, use A different scale for one question to save a question-specific mapping.

Scoring changes recalculate all stored evaluations. You do not need to upload the same evaluations again.

## Add another rotation or form

Upload that rotation's form-definition .tab file along with its evaluations. The definition export has one row per question, including QuestionID and question text. The evaluation export has resident rows with repeated Question ID and Response columns.

On the first upload, name the EPA and review its question selection and scoring. The app saves that definition in the encrypted archive. Future evaluation exports are recognized from their question IDs, regardless of their filename, upload order, or rotation label. One evaluation file may contain rows from several forms.

You can also upload evaluations before their form definition. The app saves them, lists them under Unrecognized forms, and includes them in reports once a matching definition is added and its scoring is confirmed.

Matching uses the actual question IDs such as `140828`, not the question's position on the screen. An exact set matches first. An incomplete set can match if it belongs to only one remembered form. Ambiguous or unknown sets remain unassigned. Forms whose question IDs change receive a separate remembered definition. The app does not guess that differently numbered questions are equivalent.

If wording or tags change while IDs stay the same, the updated definition retains the existing question selection and scale but requires scoring confirmation again. Reuploading an unchanged definition preserves its saved settings.

## Reports and calculation rules

### Each question

The question average equals the sum of mapped numeric scores divided by the number of scored responses for that resident, EPA, and question within the selected period.

For example, scores of 3, 4, and 5 produce an average of 4.00 with three scored ratings. A blank response or explicitly excluded code does not enter the numerator or denominator.

Each question row includes the average, scored-response count, blank count, excluded count, and count needing scoring review. Questions with no valid ratings remain blank.

### Overall EPA

The overall EPA average first averages selected scored questions within each completed evaluation, then averages those evaluation scores with equal evaluation weight. A form with more scored questions receives no extra weight. An evaluation with no valid scored answers contributes no average.

For example, a preceptor's scores of 3 and 4 give an evaluation average of 3.50. Another preceptor's scores of 3 and 3 give 3.00. The overall EPA average is 3.25. Question averages remain available for inspection but are not averaged together to produce the overall score. Review the rating and evaluation counts to judge completeness. The EPA average is descriptive and does not itself establish an entrustment decision.

The report does not combine different EPA forms into one score and does not rescale unlike forms into a common scale. Question-specific mappings should use comparable score meanings if their questions are included in one composite.

### Academic-year months

The default academic year runs July through June. Change the start month in Settings if needed. The academic-year label uses the starting year, for example 2026-2027.

Session month uses the Session Date in the export. This may represent a rotation or evaluation period's start, rather than the actual date an individual behavior was observed. It is the default because an evaluation completed in October may describe a July session. Completion month is available as a separate option.

Each month uses only evaluations assigned to that month. Cumulative from academic-year start recomputes the averages using all evaluations through that month. It does not average the monthly averages. Earlier months remain part of the cumulative calculation even when hidden by the month filter. Months without ratings are gaps, not zero scores.

An invalid or missing date stays visible for review and is excluded from reports that depend on that date. If its other date is valid, the evaluation can appear when that date basis is selected.

### Downloads

The Excel report contains EPA averages, question averages, monthly EPA results, cumulative EPA results, a response audit, and report notes describing the chosen filters and missing-data rules. Question and EPA averages are also available as CSV downloads. Question rows are displayed to two decimals, while calculations use the underlying precision.

Reports contain resident information. Source comments are available in the app's saved-evaluation and conflict-review views. The score workbook's response audit contains raw responses and scoring status, without narrative comments.

### Sidebar PDF report

Choose the academic year, months, residents, and EPA forms on Dashboard, then click Download PDF in the sidebar. The button is available on every main page and uses your most recent Dashboard selections. Those selections stay in your signed-in session when you move between pages.

To export everyone at once, click Download PDF for all residents. This creates one combined PDF for all residents with matching evaluations in the selected academic year, months, date basis, and EPA forms. It includes residents outside the current Residents selection and leaves that selection as you set it. The sidebar shows how many residents the combined report will include. Residents without evaluations matching the chosen period and EPA forms do not receive an empty section.

The PDF is divided by EPA form, then resident. Each form and resident section starts on a new page and lists every question in the form's original order. Selected, confirmed questions with valid ratings show their average to two decimal places and their scored-response count. Unselected questions, unconfirmed scoring, and questions without valid scored ratings have empty Average and n cells. Narrative and administrative questions remain listed with blank cells unless you later explicitly include and map them for scoring.

Selecting a question under Forms and saving its scoring settings makes it eligible for future PDFs using the stored evaluations. Generating a PDF does not change your question selections, scores, or saved data. The file is generated only when you click Download PDF.

For an existing deployment, replace `epa_tracker.py` and `requirements.txt`, and add `epa_pdf.py` beside the other modules. Keep your current Streamlit Secrets and saved archive. The next deployment installs ReportLab automatically from the updated requirements.

If you already installed the sidebar PDF update, adding the all-residents button requires replacing only `epa_tracker.py`.

## Duplicate and correction handling

Exact repeated evaluations are skipped even when the file is renamed, the encoding changes, or question columns are rearranged. Duplicate detection does not depend on the upload filename.

When an export provides a unique evaluation ID, the app uses it. The supplied files do not have such an ID, so their stable identity uses the resident, evaluator, rotation, session date, completion timestamp, and question-ID set. Names are normalized for capitalization, spacing, and Unicode formatting.

An incoming version with changed responses under the same identity is held as a correction. A changed completion timestamp or partial export that resembles an existing evaluation is held as a possible duplicate. The incoming version does not count in the averages while waiting for review.

In Review, compare the saved and incoming versions. Keep the saved data or replace it with the incoming version. For a possible duplicate, you can explicitly confirm that they are separate evaluations and count both. If more than one saved evaluation could match, select the exact saved evaluation before replacing it. The app remembers dismissed and superseded versions so they do not repeatedly return on the same upload.

There is an unavoidable limit when the source supplies no unique evaluation ID: changing a resident name, evaluator, rotation, or session date can make an edited evaluation look like a new one. Two truly separate evaluations with identical identity fields and identical responses also cannot be distinguished. Use source evaluation IDs when the exporter offers them and review source corrections carefully.

Residents with different spellings remain separate. Identical resident names are treated as the same resident in this version. This app does not infer identity from approximate name matches.

## Persistence and access

The GitHub file contains the full encrypted archive, including form definitions, scoring settings, raw evaluation responses and comments, and duplicate decisions. Uploads stay in server memory until saved. The app does not write a plaintext evaluation cache to the repository.

The shared app password is stored as a salted PBKDF2-SHA256 hash with 600,000 iterations. The GitHub token and Fernet key are separate values in Streamlit Secrets. Password throttling is shared across sessions in one running app process. The default inactivity timeout is 30 minutes and is checked on the next app interaction. Signing out clears session data. The shared password gives every authenticated user the same access, rather than providing individual user accounts or roles.

Each save reads the current archive and checks the GitHub file version. If another session saves first, the app reloads and reapplies the import. Form edits and review decisions require a fresh comparison before replacing changed settings or records. A failed load or decryption never becomes an empty archive, and a failed save is explicitly shown as not saved.

The sidebar shows the last successful archive save. Use Reload saved data to see changes made in another session. Stored data survives app restarts because GitHub holds the archive.

Preserve the encryption key and use GitHub history or your approved backup process to retain the encrypted archive. The app does not erase historical Git commits. This version supports archives up to 25 MB encrypted and 100 MB decompressed JSON, and uploads up to 50 MB per file. Split large exports if needed.

To change only the app password, run:

```bash
python setup_secrets.py --password-only
```

Replace only `app.password_hash` in Secrets. Keep the existing `security.fernet_key`. Password changes invalidate existing sessions on their next interaction.

## Verification

See MULTI_EPA_UPDATE_README.md for this release's actual verification results. Current arithmetic checks use invented evaluations. Original resident files are deliberately not included in the distribution. Software arithmetic does not validate the clinical meaning of response codes.

The supplied test suite includes parsing, identity, scoring, persistence, report, authentication, and Streamlit tests. This release ran the core, APPD, acceptance, storage, requested-calculation, mapping-widget test-double, and PDF checks. The live Streamlit and authentication UI suites were not run here because Streamlit was unavailable. GitHub storage responses were mocked. No connection to your live repository or APPD account was made.

To run the automated checks:

```bash
python -m pip install pytest pypdf
python -m pytest tests -q
```

## Official setup references

- [Streamlit deployment](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app)
- [Streamlit Cloud Secrets](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management)
- [GitHub repository Contents API and permissions](https://docs.github.com/en/rest/repos/contents)
- [Fernet encryption and key handling](https://cryptography.io/en/latest/fernet/)
