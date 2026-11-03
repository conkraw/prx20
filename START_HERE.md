# Put EPA Tracker on Streamlit Community Cloud

You can complete this setup through your browser. You do not need to install Python or use GitHub Desktop.

## 1. Extract the download

Right-click `EPA_Tracker_Streamlit.zip` and choose Extract All. Open the extracted `epa_tracker` folder.

## 2. Upload the app files to GitHub

Open the GitHub repository where you want the app code to live. Use Add file, then Upload files. Upload these files together:

| File to upload |
| --- |
| `epa_tracker.py` |
| `epa_core.py` |
| `epa_storage.py` |
| `epa_auth.py` |
| `epa_pdf.py` |
| `seed_forms.json` |
| `requirements.txt` |
| `README.md` |

The README is helpful for setup but not required to run the app. The seven other files are needed at runtime. Keep them at the repository root, then commit the upload. Additional files from the package can also be kept in the repository. The original resident evaluation exports stay on your computer until you upload them through the protected app.

## 3. Prepare the data repository

Create a separate private GitHub repository for the saved evaluations, for example `epa-data`. Select Add a README so it has a `main` branch.

Create or use a GitHub token with access to that data repository and Contents read and write permission. Keep the token for the next step. It does not go into a code file.

## 4. Generate the Streamlit settings

Double-click `setup_secrets.html` in the extracted folder. It should open in your browser. If needed, choose Open with, then Chrome or Edge.

Choose and confirm the password that will protect your EPA app. Enter your GitHub token and the data repository as `your-username/epa-data`. Generate the settings and copy the full block.

The helper works locally in the browser. It does not submit your entries anywhere. The generated block contains your token and encryption key. Keep it private and retain a secure copy of the encryption key. Use this helper for first-time deployment. An existing archive needs its existing key.

## 5. Deploy through Streamlit

Go to [Streamlit Community Cloud](https://share.streamlit.io/) and sign in. Choose Create app, then Yup, I have an app.

Fill in:

| Streamlit field | Value |
| --- | --- |
| Repository | The GitHub repository holding the app code |
| Branch | `main`, or the branch containing your uploaded files |
| Main file path | `epa_tracker.py` |
| Python version in Advanced settings | `3.12` |
| Secrets in Advanced settings | The full configuration block from the helper |

If you uploaded the entire folder instead of its contents, use `epa_tracker/epa_tracker.py` as the main file path and keep `requirements.txt` with that file.

Save Advanced settings and choose Deploy. Streamlit installs the dependencies from `requirements.txt` and runs the app on its servers. Your computer does not need to remain on.

## 6. Sign in and import evaluations

Use the app password you chose. Open Upload and add your evaluation .tab files, then save them. Open Forms to confirm the question selection and rating meanings before viewing the averages.

Acutes and Adolescent Medicine definitions are already included. Their score code 6 still needs confirmation from the original rating labels.

The sidebar's Download PDF button exports the Dashboard's selected residents, EPA forms, academic year, and months. Every form question is listed, with a blank average when it is unselected or has no scored ratings.

If Streamlit reports a setup error, copy the error message or capture the error screen. Keep the token, password, and encryption key out of screenshots you share.

## References

- [Streamlit deployment instructions](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy)
- [Streamlit Secrets](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management)
- [GitHub repository permissions](https://docs.github.com/en/rest/repos/contents)
