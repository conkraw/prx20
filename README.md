# EPA Tracker

See SIMPLE_WORKFLOW_README.md for the current update, staff workflow, and Data Hub format requirements.

EPA Tracker stores forms, evaluations, resident start dates, mappings, and reviewed exports in the existing encrypted GitHub archive. The app has four pages: Dashboard, Upload, APPD Export, and Admin. Local a/b display labels are no longer used. Numeric averages appear directly in the export review.

For an existing deployment, install only the six files listed in SIMPLE_WORKFLOW_README.md. Keep your Secrets and encryption key unchanged.

For a new deployment, upload every .py file in this folder, requirements.txt, seed_forms.json, appd_template.xlsx, and .streamlit/config.toml. Use epa_tracker.py as the main file. Never upload resident TAB exports or actual secrets into the code repository.

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

## Test suite

Run `python -m pytest tests -q` after installing the application requirements and development packages pytest and pypdf. Runtime-only tests skip when Streamlit is unavailable. Tests use synthetic records.

See SIMPLE_WORKFLOW_README.md for the tests actually run for this package. The live app, remote saved archive, and actual Data Hub import were not tested here.
