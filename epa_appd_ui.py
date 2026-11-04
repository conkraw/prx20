"""APPD Admin and Export screens. All saved changes use the existing encrypted store."""
from __future__ import annotations

import copy
import math
from datetime import date
from typing import Callable

import pandas as pd
import streamlit as st

import epa_appd as appd


def _notify(message: str) -> None:
    st.session_state["notice"] = message
    st.rerun()


def _config_save(state: dict, store, commit: Callable, proposal: dict) -> None:
    try:
        appd.validate_config(proposal)
        baseline = appd.digest(state["settings"].get("appd", {}))
        def apply(current):
            if appd.digest(current["settings"].get("appd", {})) != baseline:
                raise ValueError("APPD setup changed in another session. Reload saved data before saving.")
            current["settings"]["appd"] = copy.deepcopy(proposal)
            return current, "APPD setup saved."
        if commit(store, apply):
            _notify("APPD setup saved to the encrypted GitHub archive.")
    except (ValueError, TypeError, KeyError) as exc:
        st.error(str(exc))


def admin_page(state: dict, store, commit: Callable) -> None:
    st.header("Admin")
    st.caption("APPD setup is protected by your existing app sign-in. Changes are saved encrypted in GitHub.")
    section = st.radio("APPD setup section", ["Program setup", "Resident roster", "Question mapping", "Score conversion"],
                       horizontal=True, label_visibility="collapsed", key="appd_admin_section")
    if section == "Program setup":
        _program_setup(state, store, commit)
    elif section == "Resident roster":
        _roster(state, store, commit)
    elif section == "Question mapping":
        _question_mapping(state, store, commit)
    else:
        _conversion(state, store, commit)


def _program_setup(state: dict, store, commit: Callable) -> None:
    cfg = appd.config_for(state)
    st.subheader("Program and reporting period")
    with st.form("appd_program_setup"):
        a, b, c = st.columns([3, 1, 1])
        program = a.text_input("Program ID / program name", value=cfg["programid"],
                               help="Use the same program identifier or name that you use in APPD.")
        season = b.selectbox("Season", ["FALL", "SPRING"], index=["FALL", "SPRING"].index(cfg["season"]))
        year = c.number_input("Reporting year", min_value=2000, max_value=2100, value=int(cfg["year"]), step=1)
        st.selectbox("APPD mapping", ["GENERAL"], help="This is the template's mapping field, not the question mapping. This release supports the 12 general-pediatrics EPAs.")
        a, b, c = st.columns(3)
        start = a.date_input("Include evaluations from", value=appd.as_date(cfg["start_date"]))
        end = b.date_input("Through", value=appd.as_date(cfg["end_date"]))
        date_fields = {"completed_at": "Date Completed", "session_date": "Session Date"}
        basis = c.selectbox("Filter evaluations by", list(date_fields), index=list(date_fields).index(cfg["date_basis"]),
                            format_func=date_fields.get)
        modes = {"latest_completed": "Latest Date Completed for each resident", "latest_session": "Latest Session Date for each resident", "fixed": "One CCC assessment date for all residents"}
        a, b = st.columns([2, 1])
        mode = a.selectbox("Assessment date in the APPD file", list(modes), index=list(modes).index(cfg["assessment_date_mode"]), format_func=modes.get)
        fixed = b.date_input("CCC date, used only with the CCC-date option", value=appd.as_date(cfg["fixed_assessment_date"]))
        aggregation = "equal_evaluations"
        st.write("Averaging: linked questions within each evaluation, then equal weight for each completed evaluation.")
        st.caption("For a given EPA, average its scored questions on each form first. Then average those evaluation scores across preceptors and forms. A form with more answered questions gets no extra weight. Only the selected date range is used. The upload date is never used as the assessment date.")
        save = st.form_submit_button("Save APPD program setup", type="primary")
    if save:
        proposal = dict(cfg, programid=program.strip(), season=season, year=int(year), mapping="GENERAL",
                        start_date=start.isoformat(), end_date=end.isoformat(), date_basis=basis,
                        assessment_date_mode=mode, fixed_assessment_date=fixed.isoformat(), aggregation=aggregation)
        _config_save(state, store, commit, proposal)
    with st.expander("What the APPD fields mean"):
        st.write("mapping is GENERAL for this general-pediatrics file. Question mapping is configured separately for each form.")
        st.write("APPD describes assessmentdate as the date the EPA levels were collected or determined. Latest Date Completed is your starting rule. Select the CCC date when that is the date of the final committee determination.")
        st.write("The output has one sheet, the original information row, headers on row 2, and one row per included resident. Assessment dates are text in mm/dd/yyyy format.")
        st.link_button("APPD upload instructions", appd.APPD_GUIDE)


def _roster(state: dict, store, commit: Callable) -> None:
    st.subheader("Resident start dates and PGY")
    names = appd.residents_in(state)
    if not names:
        st.info("Upload resident evaluations first. The resident list will appear here automatically.")
        return
    cfg = appd.config_for(state)
    stored = state["settings"].get("appd_roster", {})
    baseline = appd.digest(stored)
    version = baseline[:12]
    st.caption("Enter July 1 of each resident's PGY1 start year once. PGY advances every July 1 and is calculated at the assessment date, not today's date or the upload date. A start date takes priority over older manual PGY entries.")
    preview_day = st.date_input("Preview PGY on assessment date", value=appd.as_date(cfg["end_date"]) or date.today(),
                                key="appd_pgy_preview_date")
    data = [{"Resident": name, "APPD name or ID": stored.get(key, {}).get("export_id") or name,
             "Residency start date": appd.as_date(stored.get(key, {}).get("residency_start_date"))}
            for key, name in names.items()]
    frame = pd.DataFrame(data, index=list(names))
    frame["Residency start date"] = pd.Series([r["Residency start date"] for r in data], index=frame.index, dtype=object)
    edited = st.data_editor(frame, hide_index=True, width="stretch", key=f"appd_start_roster_{version}",
                            disabled=["_index", "Resident"],
                            column_config={"Resident": st.column_config.TextColumn(width="medium"),
                                "APPD name or ID": st.column_config.TextColumn(width="medium", required=True),
                                "Residency start date": st.column_config.DateColumn("PGY1 start date (July 1)", format="MM/DD/YYYY",
                                    help="For example, 07/01/2026. Leave blank only when the start year is not known.")})
    updates = {}
    for key, values in edited.iterrows():
        raw = values["APPD name or ID"]
        updates[key] = {"export_id": "" if pd.isna(raw) else str(raw).strip(),
                        "residency_start_date": values["Residency start date"]}
    if st.button("Save resident roster", type="primary"):
        try:
            # Validate the submitted date and preserve all existing roster metadata.
            appd.save_roster(state, updates, baseline)
            def apply(current):
                return appd.save_roster(current, updates, baseline), "Resident roster saved."
            if commit(store, apply):
                _notify("Start dates saved. Historical and future PGYs now use each assessment date.")
        except (ValueError, TypeError) as exc:
            st.error(str(exc))
    previews = []
    for key, values in updates.items():
        if appd.has_start_date(values):
            pgy, source = appd.pgy_from_start(values["residency_start_date"], preview_day)
        else:
            preview_state = {"evaluations": state.get("evaluations", {}),
                             "settings": {**state["settings"], "appd_roster": {**stored,
                                          key: {**stored.get(key, {}), "residency_start_date": ""}}}}
            pgy, source = appd.resolve_pgy(preview_state, key, preview_day)
        previews.append({"Resident": names[key], "Assessment date": preview_day,
                         "PGY": appd.pgy_label(pgy), "Source / issue": source})
    with st.expander("PGY preview for the dates entered above", expanded=True):
        st.caption("Preview includes unsaved edits. Click Save resident roster to apply them to reports.")
        st.dataframe(pd.DataFrame(previews), hide_index=True, width="stretch")
    with st.expander("Set the same start year for a group of residents"):
        st.caption("This saves only the selected residents. It does not change anyone else.")
        selected = st.multiselect("Residents in this starting class", list(names), format_func=names.get,
                                  key=f"appd_start_cohort_{version}")
        start_year = int(st.number_input("PGY1 start year", min_value=1900, max_value=2100, value=date.today().year, step=1))
        if st.button("Save July 1 start date for selected residents", disabled=not selected):
            updates_group = {key: {"export_id": stored.get(key, {}).get("export_id") or names[key],
                                   "residency_start_date": date(start_year, 7, 1).isoformat()} for key in selected}
            def apply(current):
                return appd.save_roster(current, updates_group, baseline), "Resident start dates saved."
            if commit(store, apply):
                _notify(f"Saved July 1, {start_year} for {len(selected)} residents.")
    with st.expander("Manual PGY fallback for residents without start dates"):
        st.caption("Existing entries are preserved. These values apply only when no start date is saved. A saved start date always controls PGY.")
        default_year = appd.academic_year(preview_day, state["settings"].get("academic_year_start_month", 7))
        year = int(st.number_input("Manual PGY academic year begins in", min_value=1900, max_value=2100,
                                   value=default_year, step=1, key="appd_manual_pgy_year"))
        fallback = pd.DataFrame([{"Resident": name,
                    "Manual PGY": appd.pgy_number(stored.get(key, {}).get("pgy_by_academic_year", {}).get(str(year)))}
                    for key, name in names.items()], index=list(names))
        fallback["Manual PGY"] = pd.array(fallback["Manual PGY"], dtype="Float64")
        manual = st.data_editor(fallback, hide_index=True, width="stretch", key=f"appd_manual_roster_{year}_{version}",
                                disabled=["_index", "Resident"], column_config={
                                    "Manual PGY": st.column_config.NumberColumn(min_value=1, max_value=15, step=1, format="%d")})
        if st.button("Save manual PGY fallback"):
            fallback_updates = {key: {"export_id": stored.get(key, {}).get("export_id") or names[key],
                                     "residency_start_date": stored.get(key, {}).get("residency_start_date"),
                                     "manual_year": year, "manual_pgy": row["Manual PGY"]} for key, row in manual.iterrows()}
            try:
                appd.save_roster(state, fallback_updates, baseline)
                def apply(current):
                    return appd.save_roster(current, fallback_updates, baseline), "Manual PGY fallback saved."
                if commit(store, apply):
                    _notify("Manual PGY fallback saved. Residents with a start date still use automatic PGY.")
            except (ValueError, TypeError) as exc:
                st.error(str(exc))


def _question_mapping(state: dict, store, commit: Callable) -> None:
    st.subheader("Link each question to one or more EPAs")
    forms = state.get("forms", {})
    if not forms:
        st.info("Upload a question form first.")
        return
    fid = st.selectbox("Form to map", sorted(forms, key=lambda k: forms[k]["name"]),
                       format_func=lambda k: forms[k]["name"], key="appd_mapping_form")
    form = forms[fid]
    try:
        saved = appd.mapping_for(state, fid)
    except ValueError as exc:
        st.error(str(exc))
        return
    links = saved.get("questions", {})
    fresh = saved.get("form_signature") == appd.form_signature(form)
    prefix = f"appd_map_v2_{fid}_{appd.digest([form, saved])[:12]}"
    st.caption("Each question has its own multiple-selection box. Select every EPA that applies. Selecting a second EPA keeps the first. Leave unrelated questions empty. Changes are saved together for this form.")
    if saved and not fresh:
        st.warning("The wording on this form changed. Review and reconfirm its APPD links.")
    # Native multiselect widgets avoid dependence on an editable list column.
    # Explicit per-question keys keep labels and saved links together on reruns.
    proposed = {}
    with st.form(prefix + "_form"):
        for question in form["questions"]:
            qid = str(question["id"])
            left, right = st.columns([3, 2])
            with left:
                st.write(f"Question {qid}: {question['text']}")
                if not question.get("include"):
                    st.caption("Excluded from averages under Forms. Links can be saved but will not contribute yet.")
            with right:
                proposed[qid] = st.multiselect(
                    f"EPAs for question {qid}", options=appd.EPA_COLUMNS,
                    default=list(links.get(qid, [])), key=prefix + "_q_" + qid,
                    help="Select multiple EPAs in this box. The question counts once in each selected EPA.",
                )
        confirmed = st.checkbox("I reviewed these question-to-EPA links",
                                value=bool(saved.get("confirmed") and fresh), key=prefix + "_confirmed")
        save = st.form_submit_button("Save question mapping", type="primary")
    if save:
        try:
            baseline_form, baseline_map = appd.digest(form), appd.digest(saved)
            def apply(current):
                if appd.digest(current["forms"].get(fid)) != baseline_form or appd.digest(appd.mapping_for(current, fid)) != baseline_map:
                    raise ValueError("This form or its links changed in another session. Reload before saving.")
                return appd.save_mapping(current, fid, proposed, confirmed), "Question mapping saved."
            if commit(store, apply):
                _notify("Question mapping saved. Existing evaluations now use every selected EPA link.")
        except (ValueError, TypeError) as exc:
            st.error(str(exc))
    with st.expander("Mapping coverage across forms"):
        for form_id, definition in sorted(forms.items(), key=lambda item: item[1]["name"]):
            mapped = appd.mapping_for(state, form_id)
            valid = mapped.get("confirmed") and mapped.get("form_signature") == appd.form_signature(definition)
            targets = sorted({epa for values in mapped.get("questions", {}).values() for epa in values}, key=appd.EPA_COLUMNS.index)
            status = "Confirmed" if valid else "Needs review"
            target_text = ", ".join(targets) or "No EPA links"
            st.write(f"{definition['name']}: {len(mapped.get('questions', {}))} linked questions. {target_text}. {status}.")
    st.caption("Links are saved by question ID, not question position. A linked question also needs to be included and have a confirmed numeric scale under Forms.")


def _conversion(state: dict, store, commit: Callable) -> None:
    cfg = appd.config_for(state)
    st.subheader("Local display labels")
    st.write("The numeric average is preserved. A separate Local level column uses your display rule and leaves whole numbers unchanged.")
    local_prefix = "appd_local_" + appd.digest(cfg)[:12]
    with st.form(local_prefix + "_form"):
        orders = {
            "b_then_a": "b up to the cutoff, a above it (your requested rule)",
            "a_then_b": "a up to the cutoff, b above it",
        }
        order = st.radio("Fractional label order", list(orders),
                         index=list(orders).index(cfg.get("local_fraction_order", "b_then_a")),
                         format_func=orders.get, key=local_prefix + "_order")
        cutoff = st.number_input("Fractional cutoff", min_value=0.01, max_value=0.99,
                                 value=float(cfg.get("local_fraction_cutoff", 0.5)), step=0.05,
                                 format="%.2f", key=local_prefix + "_cutoff")
        st.caption("With the requested rule and a 0.50 cutoff: 3.00 becomes 3, 3.25 becomes 3b, 3.50 becomes 3b, 3.75 becomes 3a, and 4.00 becomes 4. Exactly at the cutoff stays in the lower band. Labels use the unrounded average.")
        save_local = st.form_submit_button("Save local display rule", type="primary")
    if save_local:
        _config_save(state, store, commit, dict(cfg, local_fraction_order=order, local_fraction_cutoff=float(cutoff)))
    st.warning("Local display labels are not APPD supervision levels. APPD defines 3a as close support and 3b as moderate support, with 3b the higher-independence category. Plain 2 and 3 are not valid APPD upload values. Your local labels are never copied automatically to the APPD file.")
    st.subheader("Optional APPD export suggestions")
    st.write("Keep your local labels separate from official upload categories. Enter final CCC levels in APPD Export, or define and confirm program-specific numeric thresholds below. Saved thresholds are preserved by this update.")
    prefix = "appd_conversion_" + appd.digest(cfg)[:12]
    enabled = st.checkbox("Suggest APPD levels from our local average thresholds", value=cfg.get("conversion_enabled", False), key=prefix + "_enable")
    minimum = st.number_input("Lowest average covered by the conversion", value=float(cfg.get("minimum_score", 1)), step=0.1, key=prefix + "_min")
    values = cfg.get("score_bands") or [{"upper": float(i), "code": ""} for i in range(1, 6)]
    data = pd.DataFrame([{"Maximum average (inclusive)": float(x["upper"]), "APPD level": x.get("code", "")} for x in values])
    edited = st.data_editor(data, hide_index=True, width="stretch", num_rows="dynamic", key=prefix + "_bands",
                           column_config={"Maximum average (inclusive)": st.column_config.NumberColumn(step=0.01, format="%.2f", required=True),
                                "APPD level": st.column_config.SelectboxColumn(options=[""] + list(appd.APPD_CODES[1:]))})
    st.caption("Enter increasing upper limits. Each row starts above the previous upper limit. The first row starts at the lowest covered average. Out-of-range averages remain blank. These are local suggestions, not an APPD-validated conversion.")
    confirmed = st.checkbox("I verified these local thresholds and that the mapped forms use a comparable numeric scale", value=bool(cfg.get("conversion_confirmed")), key=prefix + "_confirm")
    if st.button("Save score conversion", type="primary"):
        try:
            bands = []
            for _, row in edited.iterrows():
                raw_code = row["APPD level"]
                code = "" if pd.isna(raw_code) else str(raw_code).strip()
                upper = row["Maximum average (inclusive)"]
                if not code and not enabled:
                    continue
                if pd.isna(upper):
                    if code:
                        raise ValueError("Each conversion row needs a maximum average.")
                    continue
                bands.append({"upper": float(upper), "code": code})
            proposal = dict(cfg, conversion_enabled=bool(enabled), conversion_confirmed=bool(confirmed),
                            minimum_score=float(minimum), score_bands=bands)
            _config_save(state, store, commit, proposal)
        except (ValueError, TypeError) as exc:
            st.error(str(exc))
    with st.expander("APPD supervision scale"):
        st.dataframe(pd.DataFrame([{"Level": code, "Meaning": meaning} for code, meaning in appd.LEVEL_LABELS.items()]), hide_index=True, width="stretch")
        st.link_button("APPD guide and scale", appd.APPD_GUIDE)


def _csv(data: list[dict]) -> bytes:
    frame = pd.DataFrame(data)
    def protect(value):
        return "'" + value if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")) else value
    return frame.map(protect).to_csv(index=False).encode("utf-8-sig")


def calculated_overview(draft: dict, *, key_prefix: str = "appd") -> None:
    """Visible read-only averages and local labels, separate from final APPD codes."""
    st.subheader("Calculated EPA averages")
    st.caption("Each cell shows the numeric average and your local label, for example 3.25 (3b). These results calculate automatically from saved mappings and confirmed scores. Final APPD levels are reviewed separately.")
    metrics = {(r["resident_key"], r["APPD EPA"]): r for r in draft["summary"]}
    rows = []
    for row in draft["rows"]:
        display = {"Resident": row["Resident"], "Assessment date": appd.as_date(row["assessmentdate"]),
                   "PGY": appd.pgy_label(row["pgy"])}
        for epa in appd.EPA_COLUMNS:
            metric = metrics.get((row["resident_key"], epa), {})
            avg = metric.get("Average")
            display[epa] = f"{avg:.2f} ({metric['Local level']})" if avg is not None else ""
        rows.append(display)
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                 column_config={"Resident": st.column_config.TextColumn(pinned=True),
                                "Assessment date": st.column_config.DateColumn(format="MM/DD/YYYY")})
    if draft.get("summary") and all(r["Average"] is None for r in draft["summary"]):
        st.warning("No mapped EPA averages are available for this selection. Check the reasons below. Missing results are not zero.")
    with st.expander("Why is an average blank? / Form readiness", expanded=bool(draft.get("warnings"))):
        st.dataframe(pd.DataFrame(draft.get("form_diagnostics", [])), hide_index=True, width="stretch")
        missing = [{"Resident": r["Resident"], "EPA": r["APPD EPA"], "Reason": r["Calculation status"]}
                   for r in draft["summary"] if r["Average"] is None]
        if missing:
            st.dataframe(pd.DataFrame(missing), hide_index=True, width="stretch")
        st.caption("Check the selected dates, saved question-to-EPA links, included questions, and confirmed scoring scale. Blank final-upload cells are separate from blank calculated averages.")
    if draft["summary"]:
        st.download_button("Download calculated averages CSV", data=_csv(draft["summary"]),
                           file_name="EPA_calculated_averages.csv", mime="text/csv", on_click="ignore", key=key_prefix + "_averages_csv")


def export_page(state: dict, store, commit: Callable) -> None:
    st.header("APPD Export")
    if not state.get("settings", {}).get("appd"):
        st.info("Open Admin, save Program setup, and review the resident roster and question mappings first.")
        return
    cfg = appd.config_for(state)
    try:
        draft = appd.build_draft(state, cfg)
    except (ValueError, TypeError, KeyError) as exc:
        st.error(f"Review Admin setup: {exc}")
        return
    st.caption(f"{cfg['programid']} | {cfg['season']} {cfg['year']} | {cfg['start_date']} through {cfg['end_date']}")
    st.info("Calculated averages and Local level labels are evidence for review. The upload file contains your saved, CCC-approved APPD levels, not raw averages or local display labels.")
    if not draft["rows"]:
        st.warning("No residents have evaluations in the saved date range. Adjust Program setup in Admin.")
        return
    if draft["warnings"]:
        with st.expander(f"Review {len(draft['warnings'])} data or mapping notices", expanded=True):
            for warning in draft["warnings"]:
                st.write(warning)
    signature = draft["fingerprint"]
    saved = state["settings"].get("appd_reviews", {}).get(appd.review_key(cfg), {})
    same_input = saved.get("input_fingerprint") == signature
    if saved and not same_input:
        st.warning("The data or setup changed since the saved review. Prior approval no longer applies. Review the refreshed draft and approve it again.")
    tab_averages, tab_final, tab_evidence = st.tabs(["Calculated EPA averages", "Final APPD levels", "Averages and evidence"])
    with tab_averages:
        calculated_overview(draft)
    with tab_evidence:
        _evidence(draft)
    with tab_final:
        st.caption("The calculated averages are on the first tab. This table contains final APPD categories only. Without a configured APPD conversion, final levels stay blank until reviewed. Local a/b labels are not copied into the upload automatically.")
        work_key = "appd_work_" + signature
        source_rows = st.session_state.get(work_key) or (saved.get("rows") if same_input else None) or draft["rows"]
        source_rows = appd.apply_start_date_pgys(state, source_rows)
        version = appd.digest(source_rows)[:12]
        rows = copy.deepcopy(source_rows)
        for row in rows:
            row["assessmentdate"] = appd.as_date(row["assessmentdate"])
        frame = pd.DataFrame(rows).set_index("resident_key")
        frame["pgy"] = pd.array(frame["pgy"], dtype="Float64")
        columns = {"Resident": st.column_config.TextColumn(width="medium", pinned=True),
                   "Include": st.column_config.CheckboxColumn(width="small"),
                   "name_or_program_assigned_ID": st.column_config.TextColumn("APPD name / ID", width="medium", required=True),
                   "assessmentdate": st.column_config.DateColumn("Assessment date", format="MM/DD/YYYY"),
                   "pgy": st.column_config.NumberColumn("PGY", min_value=1, max_value=15, step=1, format="%d")}
        columns.update({epa: st.column_config.SelectboxColumn(epa, options=[""] + list(appd.APPD_CODES), width="small") for epa in appd.EPA_COLUMNS})
        edited = st.data_editor(frame, hide_index=True, width="stretch", height=min(650, 80 + 35 * len(frame)),
                               disabled=["_index", "Resident"], column_config=columns,
                               column_order=["Resident", "Include", "name_or_program_assigned_ID", "assessmentdate", "pgy"] + appd.EPA_COLUMNS,
                               key=f"appd_final_{signature[:12]}_{version}")
        raw_rows = appd.normalize_review_rows(edited.reset_index().to_dict("records"))
        normalized = appd.apply_start_date_pgys(state, raw_rows)
        if appd.digest(raw_rows) != appd.digest(normalized):
            # Persist all edits before rebuilding the widget with the recalculated PGY.
            st.session_state[work_key] = normalized
            st.rerun()
        st.caption("Choose a final APPD level for all 12 EPAs for each included resident. Use 0 only when not observed. PGY recalculates when you edit the assessment date for a resident with a saved start date. Manual PGY edits apply only to residents without start dates. Edits here affect this reporting period only.")
        a, b = st.columns(2)
        if a.button("Mark unrated EPAs as not observed (0)", width="stretch", disabled=not draft.get("coverage_ready", False),
                    help="Requires confirmed mappings and scoring for the included forms. Fills only blank levels with no scored evidence. Review these classifications before approval."):
            by_key = {(r["resident_key"], r["APPD EPA"]): r for r in draft["summary"]}
            count = 0
            for row in normalized:
                for epa in appd.EPA_COLUMNS:
                    metric = by_key[(row["resident_key"], epa)]
                    if row["Include"] and not row[epa] and metric["Ratings"] == 0 and metric["Needs scoring review"] == 0:
                        row[epa] = "0"
                        count += 1
            st.session_state[work_key] = normalized
            _notify(f"Marked {count} unrated EPA cells as not observed in the draft. Save the draft or approve after review.")
        if b.button("Save draft", width="stretch"):
            def apply(current):
                return appd.save_review(current, cfg, normalized, signature, approved=False), "APPD draft saved."
            if commit(store, apply):
                st.session_state.pop(work_key, None)
                _notify("APPD draft saved. It is not approved for upload yet.")
        errors = appd.validate_review(normalized, cfg)
        if errors:
            with st.expander(f"Complete {len(errors)} items before approval", expanded=False):
                for error in errors:
                    st.write(error)
        reviewer = st.text_input("CCC reviewer name or initials", value=saved.get("reviewer", "") if same_input else "", key=f"appd_reviewer_{signature[:12]}")
        confirmed = st.checkbox("The CCC reviewed these final levels, dates, PGYs, and not-observed entries", key=f"appd_approval_{signature[:12]}_{appd.digest(normalized)[:12]}")
        if st.button("Save CCC approval", type="primary", disabled=bool(errors) or not confirmed or not reviewer.strip()):
            def apply(current):
                return appd.save_review(current, cfg, normalized, signature, approved=True, reviewer=reviewer), "CCC approval saved."
            if commit(store, apply):
                st.session_state.pop(work_key, None)
                _notify("CCC approval saved. The APPD Excel download is ready.")
        approved = appd.approved_review(state, cfg)
        matches = approved is not None and appd.digest(appd.normalize_review_rows(approved["rows"])) == appd.digest(normalized)
        if matches:
            snapshot_rows = copy.deepcopy(approved["rows"])
            def download():
                # Recheck persisted data at download time, as done when saving.
                latest, _ = store.load()
                if latest is None or appd.input_fingerprint(latest, appd.config_for(latest)) != signature:
                    raise ValueError("Saved data changed. Reload and review the current data before downloading.")
                latest_review = appd.approved_review(latest, cfg)
                if latest_review is None or appd.digest(latest_review["rows"]) != appd.digest(snapshot_rows):
                    raise ValueError("The saved CCC review changed. Reload before downloading.")
                return appd.workbook_bytes(latest, cfg)
            st.download_button("Download APPD Excel for all included residents", data=download,
                               file_name=appd.export_filename(cfg), mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               width="stretch", key="appd_download_" + signature[:12], on_click="ignore")
            st.caption(f"Approved by {approved['reviewer']} | Saved {approved['saved_at']}. Only included residents are exported.")
        else:
            st.download_button("Download APPD Excel for all included residents", data=b"", disabled=True,
                               file_name=appd.export_filename(cfg), width="stretch", key="appd_disabled_download")
            if approved:
                st.caption("The table has unsaved edits. Save CCC approval again to export the edited values.")


def _evidence(draft: dict) -> None:
    st.caption("First average valid mapped questions within each evaluation for an EPA. Then average the evaluation averages, with equal weight for each evaluation across preceptors and forms. Missing, excluded, unconfirmed, and unmapped responses do not become zeros. An evaluation with no valid scores contributes no average.")
    st.caption("Local level uses the Admin display rule. It is not an official APPD category. No rounding is applied before averaging or assigning a local label.")
    names = [r["Resident"] for r in draft["rows"]]
    choice = st.selectbox("Resident evidence", ["All residents"] + names, key="appd_evidence_resident")
    def filtered(rows):
        return [r for r in rows if choice == "All residents" or r["Resident"] == choice]
    metrics = pd.DataFrame(filtered(draft["summary"])).drop(columns=["resident_key"], errors="ignore")
    st.dataframe(metrics, hide_index=True, width="stretch", column_config={"Average": st.column_config.NumberColumn(format="%.4f")})
    with st.expander("Step 1: EPA average within each completed evaluation", expanded=False):
        st.dataframe(pd.DataFrame(filtered(draft["evaluation_scores"])), hide_index=True, width="stretch",
                     column_config={"Evaluation average": st.column_config.NumberColumn(format="%.4f")})
    with st.expander("Step 2: Evaluation averages grouped by form", expanded=False):
        st.caption("These form summaries are for checking your results. The final EPA average above weights each evaluation equally, not each form equally.")
        st.dataframe(pd.DataFrame(filtered(draft["form_summaries"])), hide_index=True, width="stretch",
                     column_config={"Average": st.column_config.NumberColumn(format="%.4f")})
    with st.expander("Question averages contributing to each APPD EPA"):
        st.caption("Question averages are descriptive. They are not averaged together to calculate the overall EPA average.")
        st.dataframe(pd.DataFrame(filtered(draft["questions"])), hide_index=True, width="stretch")
    with st.expander("Individual source responses"):
        st.dataframe(pd.DataFrame(filtered(draft["evidence"])), hide_index=True, width="stretch")
    if draft["summary"]:
        st.download_button("Download EPA averages and local labels CSV", data=_csv(filtered(draft["summary"])),
                           file_name="APPD_EPA_averages_local_labels.csv", mime="text/csv", on_click="ignore")
    if draft["evaluation_scores"]:
        st.download_button("Download per-evaluation EPA averages CSV", data=_csv(filtered(draft["evaluation_scores"])),
                           file_name="APPD_EPA_evaluation_averages.csv", mime="text/csv", on_click="ignore")
    if draft["evidence"]:
        st.download_button("Download mapped evidence CSV", data=_csv(filtered(draft["evidence"])),
                           file_name="APPD_mapped_evidence.csv", mime="text/csv", on_click="ignore")
