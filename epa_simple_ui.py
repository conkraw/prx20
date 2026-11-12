"""Small staff workflow with one numeric export review and one Admin area."""
from __future__ import annotations

import copy
from typing import Callable

import pandas as pd
import streamlit as st

import epa_appd as appd
import epa_workflow as workflow
from epa_appd_ui import _program_setup, _roster, _conversion, _csv


def admin_page(state: dict, store, commit: Callable, *, forms_page: Callable,
               review_page: Callable, settings_page: Callable) -> None:
    st.header("Admin")
    st.caption("Set up here once. Staff can use Upload, Dashboard, and APPD Export for routine work.")
    sections = ["Residents", "Forms and EPA mapping", "Program and dates", "Data Hub rules", "Data review", "Advanced"]
    section = st.selectbox("Setup", sections, key="simple_admin_section")
    if section == "Residents":
        _roster(state, store, commit)
    elif section == "Forms and EPA mapping":
        forms_page(state, store)
    elif section == "Program and dates":
        _program_setup(state, store, commit)
    elif section == "Data Hub rules":
        _conversion(state, store, commit)
    elif section == "Data review":
        review_page(state, store)
    else:
        settings_page(state, store)


def export_page(state: dict, store, commit: Callable) -> None:
    from epa_performance import cached_result
    st.header("APPD Export")
    cfg = appd.config_for(state)
    try:
        draft = cached_result(st.session_state, state, "export-draft", cfg,
                              lambda: appd.build_draft(state, cfg))
    except (ValueError, TypeError, KeyError) as exc:
        st.error(f"Admin > Program and dates: {exc}")
        return
    st.caption(f"{cfg['programid']} | {cfg['season']} {cfg['year']} | {cfg['start_date']} to {cfg['end_date']}")
    if not state.get("settings", {}).get("appd"):
        st.info("Save the program and reporting period under Admin > Program and dates before approving an export.")
    if not draft["rows"]:
        st.info("No evaluations in this reporting period. Check Admin > Program and dates, or upload evaluations.")
        return
    st.caption("Numeric averages are filled in automatically. Review here, make any CCC-approved changes, then prepare the file. No copying between tabs.")
    signature = draft["fingerprint"]
    saved = workflow.saved_review(state, cfg)
    raw_saved = state.get("settings", {}).get(workflow.REVIEW_NAMESPACE, {}).get(appd.review_key(cfg))
    expected_saved = appd.digest(raw_saved)
    if raw_saved and not saved:
        st.info("Data or setup changed. The table now uses current averages. Earlier overrides and approval are not reused.")
    key = "numeric_work_" + signature
    source = st.session_state.get(key) or (saved["rows"] if saved else workflow.numeric_rows(draft))
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
    edited = st.data_editor(frame, hide_index=True, width="stretch", height=min(600, 80 + 35 * len(frame)),
                            disabled=["_index", "Resident"], column_config=columns,
                            column_order=["Resident", "Include", "assessmentdate", "pgy"] + appd.EPA_COLUMNS + ["name_or_program_assigned_ID"],
                            key=f"numeric_editor_{signature[:12]}_{appd.digest(source)[:12]}")
    try:
        submitted = edited.reset_index().to_dict("records")
        reviewed = workflow.normalize_rows(state, submitted)
        # Keep all edits if an edited date requires PGY to be redrawn.
        old_pgys = [appd.pgy_number(row.get("pgy")) for row in submitted]
        if old_pgys != [r["pgy"] for r in reviewed]:
            st.session_state[key] = reviewed
            st.rerun()
        outcome = workflow.prepare_export(state, cfg, reviewed, draft=draft)
    except (ValueError, TypeError, KeyError) as exc:
        st.error(str(exc))
        return
    st.caption("Blank cells mean no scored evidence. They never count as zero in an average. PGY recalculates from the saved July 1 start date.")
    issues = list(outcome["issues"])
    if not state.get("settings", {}).get("appd"):
        issues.insert(0, "Save Admin > Program and dates before export.")
    if issues:
        st.warning("Averages are ready to review. Complete the setup items below before downloading a Data Hub file.")
        with st.expander(f"Needs attention ({len(issues)})", expanded=False):
            for message in issues:
                st.write(message)
    included = sum(bool(row["Include"]) for row in reviewed)
    st.caption(f"{included} residents selected. {len(outcome['changes'])} numeric overrides. {outcome['not_observed']} cells will be marked not observed in the Data Hub file.")
    # Keep the external schema distinction visible, not a silent translation.
    st.caption("The Data Hub file uses the supervision codes approved in Admin > Data Hub rules. Your numeric averages stay unchanged in this table and in numeric reports.")
    reviewer = st.text_input("Reviewer name or initials", value=saved.get("reviewer", "") if saved else "", key="numeric_reviewer_" + signature[:12])
    confirmed = st.checkbox("The CCC reviewed these values and the not-observed entries", key="numeric_approve_" + appd.digest(reviewed)[:12] + signature[:8])
    left, right = st.columns([1, 2])
    save_draft = left.button("Save draft", width="stretch")
    approve = right.button("Approve and prepare Data Hub file", type="primary", width="stretch",
                            disabled=bool(issues) or not confirmed or not reviewer.strip())
    if save_draft or approve:
        def apply(current):
            updated = workflow.save_numeric_review(current, cfg, reviewed, signature, approved=approve,
                                                     reviewer=reviewer, expected_saved=expected_saved)
            return updated, "Export review saved."
        if commit(store, apply):
            st.session_state.pop(key, None)
            st.session_state["notice"] = "Approved. Download your Data Hub file below." if approve else "Draft saved."
            st.rerun()
    ready = workflow.ready_review(state, cfg, reviewed, draft=draft)
    if ready:
        snapshot = appd.digest(ready)
        st.download_button("Download Data Hub upload (.xlsx)",
                           data=lambda: workflow.download_bytes(store, signature, snapshot),
                           file_name=appd.export_filename(cfg),
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           width="stretch", on_click="ignore", key="numeric_download_" + signature[:12])
        st.caption("Upload this workbook under EPA Import in Data Hub. Trainees must already be registered there with matching names or IDs.")
    else:
        st.download_button("Download Data Hub upload (.xlsx)", data=b"", disabled=True,
                           file_name=appd.export_filename(cfg), width="stretch", key="numeric_download_disabled")
    with st.expander("Details and checks", expanded=False):
        detail = st.selectbox("Show", ["Numeric review", "Actual Data Hub values", "Per-evaluation averages", "Question averages", "Form readiness", "Numeric overrides"], key="numeric_detail_view")
        choices = {
            "Numeric review": reviewed,
            "Actual Data Hub values": outcome["upload_rows"],
            "Per-evaluation averages": draft["evaluation_scores"],
            "Question averages": draft["questions"],
            "Form readiness": draft["form_diagnostics"],
            "Numeric overrides": outcome["changes"],
        }
        displayed = pd.DataFrame(choices[detail]).drop(columns=["resident_key"], errors="ignore")
        st.dataframe(displayed, hide_index=True, width="stretch")
        st.download_button("Download this check as CSV (not a Data Hub upload)", data=_csv(displayed.to_dict("records")),
                           file_name="EPA_review_check.csv", mime="text/csv", on_click="ignore", key="numeric_check_csv")
        if draft["warnings"]:
            st.caption("Source-data notes")
            for message in draft["warnings"]:
                st.write(message)
        st.caption("Averages first combine valid linked questions within each evaluation, then weight every completed evaluation equally. Full precision is retained. Saving a numeric override does not change the source evaluations.")
        st.link_button("APPD upload instructions", appd.APPD_GUIDE)
        reset = st.checkbox("Discard this period's numeric edits and return to the calculated averages", key="numeric_reset_confirm_" + signature[:12])
        if st.button("Reset to calculated averages", disabled=not reset):
            st.session_state[key] = workflow.numeric_rows(draft)
            st.rerun()
