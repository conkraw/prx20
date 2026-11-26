"""Staff workflow: review numeric averages, approve/save/prepare, download."""
from __future__ import annotations

import copy
from typing import Callable

import pandas as pd
import streamlit as st

import epa_appd as appd
import epa_export_rules as rules
import epa_workflow as workflow
from epa_appd_ui import _program_setup, _roster, _csv

APPROVE_LABEL = "Approve, save and prepare both files"
DOWNLOAD_LABEL = "Download CBME ratings (.xlsx)"
RAW_DOWNLOAD_LABEL = "Download raw averages (.xlsx)"


def admin_page(state: dict, store, commit: Callable, *, forms_page: Callable,
               review_page: Callable, settings_page: Callable) -> None:
    st.header("Admin")
    st.caption("Set up residents, forms, dates, and combined competency reports here. Upload conversion parameters are on APPD Export.")
    sections = ["Residents", "Forms and EPA mapping", "Program and dates", "Competency reports and comments", "Data review", "Advanced"]
    if st.session_state.get("simple_admin_section") == "Data Hub rules":
        st.session_state["simple_admin_section"] = "Program and dates"
    section = st.selectbox("Setup", sections, key="simple_admin_section")
    if section == "Residents":
        _roster(state, store, commit)
    elif section == "Forms and EPA mapping":
        forms_page(state, store)
    elif section == "Program and dates":
        _program_setup(state, store, commit)
    elif section == "Competency reports and comments":
        from epa_competency_ui import competency_reports_page
        competency_reports_page(state, store, commit)
    elif section == "Data review":
        review_page(state, store)
    else:
        settings_page(state, store)


def _parameters(cfg: dict, signature: str) -> tuple[dict | None, str]:
    """Edit preloaded residency defaults, without saving on page load."""
    seed_key = "upload_parameter_draft_" + signature
    seed = copy.deepcopy(st.session_state.get(seed_key, cfg))
    configured = bool(cfg.get("conversion_enabled") and cfg.get("conversion_confirmed") and cfg.get("score_bands"))
    with st.expander("CBME conversion parameters", expanded=not configured):
        st.caption("Your residency defaults are preloaded. Change the cutoffs here as needed. Changes are saved when you approve. Raw averages never change.")
        if st.button("Restore residency defaults", key="parameter_defaults_" + signature[:12],
                     help="Reload the supplied CBME table. Your saved rules are not changed until approval."):
            st.session_state[seed_key] = rules.default_parameters(cfg)
            revision_key = seed_key + "_revision"
            st.session_state[revision_key] = int(st.session_state.get(revision_key, 0)) + 1
            st.rerun()
        mode = seed.get("score_band_mode", rules.UPPER) if seed.get("score_bands") else rules.LOWER
        prefix = "upload_parameters_" + appd.digest([seed, st.session_state.get(seed_key + "_revision", 0)])[:16]
        data = pd.DataFrame(rules.editor_rows(seed))
        boundary = "Minimum average" if mode == rules.LOWER else "Maximum average (inclusive)"
        data[boundary] = pd.array(data[boundary], dtype="Float64")
        if mode == rules.LOWER:
            st.caption("Each minimum is included. The next minimum ends the previous range. Defaults: 3.77 becomes 3b, 4.00 becomes 4, and 4.50 becomes 5. Conversion uses the unrounded average.")
        else:
            st.caption("Your existing maximum-inclusive cutoffs are preserved. Each range begins above the previous maximum. Restore residency defaults switches to minimum-inclusive cutoffs.")
        edited = st.data_editor(data, hide_index=True, width="stretch", num_rows="dynamic", key=prefix + "_table",
            column_config={
                "Upload code": st.column_config.SelectboxColumn(options=list(rules.CODES), required=True, width="small"),
                boundary: st.column_config.NumberColumn(format="%.4f", step=0.01, required=True),
            })
        if mode == rules.LOWER:
            minimum = 1.0
            maximum = st.number_input("Highest average covered", value=float(seed.get("maximum_score", 5)),
                                      step=0.1, key=prefix + "_max")
        else:
            minimum = st.number_input("Lowest average covered", value=float(seed.get("minimum_score", 1)),
                                      step=0.1, key=prefix + "_min")
            maximum = 5.0
        test_score = st.number_input("Test a score", value=3.77, step=0.01, format="%.4f", key="upload_parameter_test_" + signature[:12])
        try:
            candidate = rules.parameters_from_rows(seed, edited.to_dict("records"), mode=mode,
                                                   minimum=minimum, maximum=maximum)
            appd.validate_config(candidate)
            error = ""
        except (ValueError, TypeError, KeyError) as exc:
            candidate, error = None, str(exc)
        code = rules.convert_score(test_score, candidate) if candidate else ""
        st.write(f"{test_score:g} will upload as {code}." if code else "Complete the cutoffs to preview this score. Scores outside the covered range cannot be exported.")
        st.caption("Codes: 0, 1, 2a, 2b, 3a, 3b, 4, 5. These numeric cutoffs are your residency policy. Data Hub interprets code 0 as not observed, including numeric averages below 0.50 under the defaults.")
        if seed.get("conversion_rule_origin"):
            st.caption(seed["conversion_rule_origin"])
    return candidate, error


def export_page(state: dict, store, commit: Callable) -> None:
    from epa_performance import cached_result
    st.header("APPD Export")
    st.caption("Review numeric averages, approve once, then download raw averages and converted CBME ratings.")
    cfg = appd.config_for(state)
    try:
        draft = cached_result(st.session_state, state, "export-draft", cfg,
                              lambda: appd.build_draft(state, cfg))
    except (ValueError, TypeError, KeyError) as exc:
        st.error(f"Program setup needs a correction: {exc}")
        st.caption("Open Admin > Program and dates to correct the reporting setup.")
        return
    st.caption(f"{cfg['programid']} | {cfg['season']} {cfg['year']} | {cfg['start_date']} to {cfg['end_date']}")
    if not draft["rows"]:
        st.info("No evaluations in this period. Check Admin > Program and dates, or upload evaluations.")
        return
    signature = draft["fingerprint"]
    saved = workflow.saved_review(state, cfg)
    raw_saved = state.get("settings", {}).get(workflow.REVIEW_NAMESPACE, {}).get(appd.review_key(cfg))
    expected_saved = appd.digest(raw_saved)
    if raw_saved and not saved:
        st.info("Saved data or setup changed. These are the current averages. Review again before preparing a new file.")
    st.subheader("Review averages")
    st.caption("Adjust values or uncheck a resident as needed. Both files use this approved numeric table. Only the CBME ratings file applies conversion. Original evaluations stay unchanged.")
    work_key = "numeric_work_" + signature
    source = st.session_state.get(work_key) or (saved["rows"] if saved else workflow.numeric_rows(draft))
    source = workflow.normalize_rows(state, source)
    rows = copy.deepcopy(source)
    for row in rows:
        row["assessmentdate"] = appd.as_date(row["assessmentdate"])
    frame = pd.DataFrame(rows).set_index("resident_key")
    frame["pgy"] = pd.array(frame["pgy"], dtype="Float64")
    for epa in appd.EPA_COLUMNS:
        frame[epa] = pd.array(frame[epa], dtype="Float64")
    columns = {
        "Resident": st.column_config.TextColumn(width="medium", pinned=True),
        "Include": st.column_config.CheckboxColumn(width="small"),
        "name_or_program_assigned_ID": st.column_config.TextColumn("APPD name / ID", width="medium", required=True),
        "assessmentdate": st.column_config.DateColumn("Assessment date", format="MM/DD/YYYY"),
        "pgy": st.column_config.NumberColumn("PGY", min_value=1, max_value=15, step=1, format="%d"),
    }
    columns.update({epa: st.column_config.NumberColumn(epa, format="%.2f", step=0.01, width="small") for epa in appd.EPA_COLUMNS})
    edited = st.data_editor(frame, hide_index=True, width="stretch", height=min(530, 80 + 35 * len(frame)),
                            disabled=["_index", "Resident"], column_config=columns,
                            column_order=["Resident", "Include", "assessmentdate", "pgy"] + appd.EPA_COLUMNS + ["name_or_program_assigned_ID"],
                            key=f"numeric_editor_{signature[:12]}_{appd.digest(source)[:12]}")
    try:
        submitted = edited.reset_index().to_dict("records")
        reviewed = workflow.normalize_rows(state, submitted)
        if [appd.pgy_number(r.get("pgy")) for r in submitted] != [r["pgy"] for r in reviewed]:
            st.session_state[work_key] = reviewed
            st.rerun()
    except (ValueError, TypeError, KeyError) as exc:
        st.error(str(exc))
        return
    st.caption("PGY follows the assessment date and saved July 1 start date. Blanks are not included as zeros in your averages.")
    proposed, parameter_error = _parameters(cfg, signature)
    # Calculated averages do not depend on conversion. Reuse the cached source
    # evidence even while previewing different rules instead of rescoring it.
    outcome_cfg = proposed or copy.deepcopy(cfg)
    if proposed is None:
        outcome_cfg.update(conversion_enabled=False, conversion_confirmed=False)
    outcome = workflow.prepare_export(state, outcome_cfg, reviewed, draft=draft, accept_review_notes=True)
    issues = list(outcome["issues"])
    if parameter_error:
        issues = ["CBME conversion parameters: " + parameter_error] + [
            message for message in issues if "Data Hub rules" not in message]
    included = sum(bool(row["Include"]) for row in reviewed)
    st.caption(f"{included} residents selected. {outcome['not_observed']} blank EPA values stay blank in the raw file and become not observed (0) in the CBME file.")
    notes = outcome["review_notes"]
    if notes:
        with st.expander(f"Review notes ({len(notes)}) - you decide whether to proceed", expanded=False):
            st.caption("These notes do not block approval. Only the available, confirmed scores are included. Approving accepts these notes for this export.")
            for message in notes:
                st.write(message)
    if issues:
        with st.expander(f"Required for a valid upload ({len(issues)})", expanded=True):
            st.caption("Correct these fields or exclude the affected resident. They cannot be guessed or left invalid in a Data Hub file.")
            for message in issues:
                st.write(message)
    with st.expander("Preview converted upload values", expanded=False):
        included_rows = [r for r in outcome["upload_rows"] if r["Include"]]
        upload = pd.DataFrame(included_rows).drop(columns=["resident_key", "Include", "name_or_program_assigned_ID"], errors="ignore")
        st.dataframe(upload, hide_index=True, width="stretch")
        st.caption("Only these categories go into the upload. The first table retains the original numeric averages and any explicit review edits.")
    st.caption("Clicking Approve saves the parameters and your reviewed values, accepts the review notes, and records approval for this submission. Only proceed with values approved for submission by your CCC.")
    approve = st.button(APPROVE_LABEL, type="primary", width="stretch", disabled=bool(issues))
    if approve and not issues and proposed is not None:
        def apply(current):
            updated, _ = workflow.approve_save_prepare(current, proposed, reviewed, signature,
                                                       expected_saved=expected_saved)
            return updated, "Both Excel files approved, saved and prepared."
        if commit(store, apply):
            st.session_state.pop(work_key, None)
            st.session_state.pop("upload_parameter_draft_" + signature, None)
            st.session_state["notice"] = "Approved and saved. Both Excel downloads are ready."
            st.rerun()
    same_parameters = proposed is not None and appd.digest(proposed) == appd.digest(cfg)
    ready = workflow.ready_review(state, cfg, reviewed, draft=draft) if same_parameters and not issues else None
    raw_column, ratings_column = st.columns(2)
    downloads = [
        (raw_column, RAW_DOWNLOAD_LABEL, "raw", workflow.raw_export_filename(cfg)),
        (ratings_column, DOWNLOAD_LABEL, "ratings", appd.export_filename(cfg)),
    ]
    snapshot = appd.digest(ready) if ready else ""
    for column, label, variant, filename in downloads:
        if ready:
            column.download_button(label,
                data=lambda variant=variant: workflow.download_bytes(store, signature, snapshot, variant=variant),
                file_name=filename, mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                width="stretch", on_click="ignore", key=f"dual_download_{variant}_" + signature[:12])
        else:
            column.download_button(label, data=b"", disabled=True, file_name=filename,
                width="stretch", key=f"dual_download_{variant}_disabled")
    raw_column.caption("Unconverted approved averages in the upload layout. Missing evidence stays blank.")
    ratings_column.caption("Converted CBME categories for Data Hub EPA Import. Missing evidence becomes 0.")
    st.caption("APPD documents category-code uploads, not decimal-average uploads. The raw file has the same layout, but decimal acceptance by Data Hub is not confirmed. Upload the CBME ratings file for the documented format.")
    if ready:
        st.caption("Approved and saved. Both files use the same residents, dates and PGYs. Trainee names or IDs must already exist in Data Hub.")
    with st.expander("Calculation details", expanded=False):
        detail = st.selectbox("Show", ["Numeric review", "Per-evaluation averages", "Question averages", "Form readiness", "Numeric overrides"], key="numeric_detail_view")
        choices = {"Numeric review": reviewed, "Per-evaluation averages": draft["evaluation_scores"],
                   "Question averages": draft["questions"], "Form readiness": draft["form_diagnostics"],
                   "Numeric overrides": outcome["changes"]}
        displayed = pd.DataFrame(choices[detail]).drop(columns=["resident_key"], errors="ignore")
        st.dataframe(displayed, hide_index=True, width="stretch")
        st.download_button("Download this check as CSV (not a Data Hub upload)", data=_csv(displayed.to_dict("records")),
                           file_name="EPA_review_check.csv", mime="text/csv", on_click="ignore", key="numeric_check_csv")
        st.caption("Questions are averaged within each completed evaluation, then evaluations receive equal weight. Full precision is retained. Conversion and review edits never overwrite the original evaluations.")
        st.link_button("APPD upload instructions", appd.APPD_GUIDE)
