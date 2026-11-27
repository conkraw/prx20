"""One Admin workspace for uploaded competency scores and resident feedback."""
from __future__ import annotations

import copy
import hashlib
from datetime import date

import pandas as pd
import streamlit as st

from epa_appd import config_for, digest, residents_in
from epa_competency_reports import (
    linked_report, matching_residents, original_pdf, report_filename, report_id, report_label,
    resident_link_candidates, save_report_links, saved_reports, suggested_resident,
)
from epa_core import normalize_key
from epa_performance import cached_result
from epa_pgy import as_date
from epa_resident_comments import collect_resident_comments

UPLOAD = "Upload reports"
VIEW = "View saved reports"
SOURCE_NAME = "__competency_source_name__"
MAX_BATCH_FILES = 20
MAX_BATCH_BYTES = 30 * 1024 * 1024
DATE_LABELS = {"session_date": "Rotation / Session Date", "completed_at": "Date Completed"}


def _open_saved(report_key: str) -> None:
    st.session_state["_competency_open_saved"] = report_key
    st.rerun()


def _resident_selector(state: dict, report: dict, key: str, *, existing: bool = False) -> tuple[str, str]:
    names = residents_in(state)
    source_name = str(report.get("resident") or "").strip()
    candidates = resident_link_candidates(state, source_name)
    ambiguous = len(matching_residents(state, source_name)) > 1
    candidate_keys = [row["resident_key"] for row in candidates if row["resident_key"] in names]
    options = [SOURCE_NAME] + candidate_keys + [name for name in names if name not in candidate_keys]
    chosen = report.get("resident_key") if existing else suggested_resident(state, source_name)
    index = options.index(chosen) if chosen in options else 0
    def label(value):
        if value == SOURCE_NAME:
            return "Choose the matching resident" if ambiguous else f"{source_name} (name from PDF)"
        return names[value]
    selected = st.selectbox("Link comments for resident", options, index=index,
                            format_func=label, key=key)
    if selected == SOURCE_NAME:
        if ambiguous:
            st.warning("More than one saved resident matches the name or ID in this PDF. Choose the correct resident before saving.")
            return "", ""
        possible = [row for row in candidates if row.get("evaluations", 0)]
        if possible:
            st.info("Saved resident names that may match this PDF: " + ", ".join(row["resident"] for row in possible) + ". Choose the correct saved resident above to include those comments.")
        st.caption("Uses the name in this PDF. If evaluations use a different name or name order, choose that resident above.")
        return normalize_key(source_name), source_name
    if suggested_resident(state, source_name) != selected:
        st.caption(f"Check the link: the PDF says {source_name}, and comments will come from {names[selected]}.")
    return selected, names[selected]


def _date_choices(start, end, prefix: str):
    left, right = st.columns(2)
    start = left.date_input("Comments from", value=as_date(start) or date.today(), key=prefix + "_start")
    end = right.date_input("Through", value=as_date(end) or date.today(), key=prefix + "_end")
    return start, end


def _date_basis_choice(value: str, prefix: str) -> str:
    basis = st.selectbox("Match comments by", list(DATE_LABELS),
                         index=list(DATE_LABELS).index(value) if value in DATE_LABELS else 1,
                         format_func=DATE_LABELS.get, key=prefix + "_basis")
    st.caption("Rotation / Session Date keeps late-completed evaluations with their rotation. If the chosen date is missing, the other date is used and labeled.")
    return basis


def _score_table(report: dict) -> None:
    st.subheader("Competency scores")
    st.caption("APPD recommendations as reported. Ranges such as 2 - 2.5 stay as ranges. These are not final CCC decisions.")
    scores = report.get("scores", [])
    domains = list(dict.fromkeys(row.get("domain") or "Competencies" for row in scores))
    for offset in range(0, len(domains), 3):
        columns = st.columns(3)
        for column, domain in zip(columns, domains[offset:offset + 3]):
            with column:
                st.caption(domain)
                rows = [{"Competency": row["code"], "Score": row["score"]}
                        for row in scores if (row.get("domain") or "Competencies") == domain]
                st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                             column_config={"Score": st.column_config.TextColumn("Score")})


def _evaluation_comments(evaluation: dict) -> None:
    day = evaluation.get("date") or "Date not available"
    form = evaluation.get("form_name") or "Form not identified"
    st.write(f"{day} | {form}")
    evaluator = evaluation.get("evaluator") or "Evaluator not listed"
    status = evaluation.get("evaluator_status")
    st.caption(evaluator + (f" | {status}" if status else ""))
    dates = []
    session = as_date(evaluation.get("session_date"))
    completed = as_date(evaluation.get("completed_at"))
    if session:
        dates.append("Session Date: " + session.isoformat())
    if completed:
        dates.append("Date Completed: " + completed.isoformat())
    if dates:
        st.caption(" | ".join(dates))
    if evaluation.get("date_fallback"):
        st.caption(DATE_LABELS.get(evaluation.get("date_basis"), "Other date") + " used because the selected date is missing or invalid.")
    for item in evaluation.get("feedback", []):
        prompt = item.get("question")
        if prompt:
            order = item.get("question_order")
            st.text((f"Question {order}: " if order else "") + prompt)
        elif item.get("kind") == "overall_comments":
            st.caption("Overall comments")
        st.text(item.get("text", ""))
    if evaluation.get("resident_comments"):
        st.caption("Resident comments")
        for item in evaluation["resident_comments"]:
            st.text(item.get("text", ""))
    sources = evaluation.get("source_files") or []
    if sources:
        st.caption("Source: " + ", ".join(sources))
    for note in evaluation.get("notes", []):
        st.caption(note)
    st.divider()


def _comments_view(comments: dict) -> None:
    st.subheader("Comments by rotation")
    basis = DATE_LABELS.get(comments.get("date_basis"), "Date Completed")
    st.caption(f"Original written feedback from every question, regardless of its position on the form. Matching by {basis}.")
    groups = comments.get("rotations", [])
    counts = comments.get("counts", {})
    diagnostics = comments.get("diagnostics", {})
    status = diagnostics.get("status")
    outside = counts.get("out_of_period_with_comments", 0)
    if not groups:
        if status == "no_resident_evaluations":
            st.info("No saved evaluations match this report's linked resident name. Check Link comments for resident above, including any middle name. If this resident's evaluations have not been imported, upload their New Innovations TAB files.")
        elif status == "no_saved_comments":
            st.info(f"Found {counts.get('resident_evaluations', 0)} saved evaluations for this resident, but their saved response and comment fields contain no written feedback.")
        elif status == "outside_period":
            st.info(f"The comments are saved. {outside} evaluation(s) with written feedback fall outside the selected dates when matched by {basis}. Adjust the dates or date choice above, then select Update comments.")
            if diagnostics.get("available_start"):
                st.caption(f"Saved comments span {diagnostics['available_start']} to {diagnostics['available_end']} using {basis}.")
        elif status == "undated_only":
            st.info("Written comments are saved, but their evaluations have no usable date. They appear separately below and in the PDF.")
        else:
            st.info("No saved narrative comments were found for this resident in these dates. Check the resident link and dates above.")
    else:
        st.caption(f"Showing {counts.get('evaluations_with_comments', 0)} evaluation(s) with comments. {outside} other evaluation(s) with comments are outside the selected dates.")
    alternate = diagnostics.get("alternate_additional_with_comments", 0)
    if alternate:
        alternate_label = DATE_LABELS.get(diagnostics.get("alternate_date_basis"), "the other date")
        st.info(f"{alternate} excluded evaluation(s) with comments fall inside this period using {alternate_label}. You can choose that date above and select Update comments.")
    rows = diagnostics.get("evaluations", [])
    if rows:
        with st.expander("Check included and excluded evaluations", expanded=not bool(groups)):
            labels = {"included": "Shown", "outside_period": "Outside selected dates",
                      "date_unavailable": "Shown separately without a date", "no_saved_comments": "No written feedback"}
            check = [{"Rotation": row.get("rotation") or "Rotation not recorded",
                      "Session Date": row.get("session_date") or "",
                      "Date Completed": row.get("completed_date") or "",
                      "Date used": row.get("selected_date") or "",
                      "Comment fields": row.get("comment_fields", 0),
                      "Status": labels.get(row.get("status"), row.get("status", ""))} for row in rows]
            st.dataframe(pd.DataFrame(check), hide_index=True, width="stretch")
    for group in groups:
        evaluations = group.get("evaluations", [])
        label = f"{group['rotation']} ({len(evaluations)} evaluation" + ("s)" if len(evaluations) != 1 else ")")
        with st.expander(label, expanded=len(groups) == 1):
            for evaluation in evaluations:
                _evaluation_comments(evaluation)
    undated = comments.get("undated", [])
    if undated:
        with st.expander(f"Date unavailable ({len(undated)} evaluations)", expanded=False):
            st.warning("These comments have no usable date. They are included separately at the end of the PDF and cannot be assigned to the selected period.")
            for evaluation in undated:
                st.caption(evaluation.get("rotation") or "Rotation not provided")
                _evaluation_comments(evaluation)
    if comments.get("notes"):
        with st.expander("Comment coverage details", expanded=False):
            for note in comments["notes"]:
                st.write(note)


def _upload_reports(state: dict, store, commit) -> None:
    st.caption("Upload the original APPD General Pediatrics report PDFs. Each report supplies the competency table. Comments come from evaluation TAB files already saved in this app.")
    uploader_key = "competency_upload_" + str(st.session_state.get("_competency_upload_revision", 0))
    uploads = st.file_uploader("Competency report PDFs", type=["pdf"], accept_multiple_files=True, key=uploader_key)
    if not uploads:
        return
    if len(uploads) > MAX_BATCH_FILES:
        st.error("Upload up to 20 reports at a time.")
        return
    if sum(item.size for item in uploads) > MAX_BATCH_BYTES:
        st.error("This batch is larger than 30 MB. Upload a smaller set of reports.")
        return
    from epa_competency_import import parse_competency_pdf
    parsed = []
    seen = set()
    for upload in uploads:
        data = upload.getvalue()
        signature = hashlib.sha256(data).hexdigest()
        if signature in seen:
            st.caption(f"Duplicate file skipped: {upload.name}")
            continue
        seen.add(signature)
        try:
            report = cached_result(st.session_state, state, "competency-upload",
                                   [signature, upload.name], lambda: parse_competency_pdf(data, upload.name))
            parsed.append((report, data))
        except ValueError as exc:
            st.error(f"{upload.name}: {exc}")
    if not parsed:
        return
    cfg = config_for(state)
    batch_key = digest(sorted(seen))[:16]
    st.info("These PDFs do not identify a calendar year. Choose the dates for the comments you want attached. The dates below start from your current program setup.")
    start, end = _date_choices(cfg["start_date"], cfg["end_date"], "competency_batch_" + batch_key)
    date_basis = _date_basis_choice("session_date", "competency_batch_" + batch_key)
    st.caption("These comment dates apply to every report in this upload.")
    selections = []
    current = saved_reports(state)
    for report, data in parsed:
        rid = report_id(report["source_sha256"])
        with st.container(border=True):
            st.subheader(str(report.get("resident") or "Resident not recognized"))
            st.caption(f"{report.get('season', '')} | PGY{report.get('pgy', '')} | {report['source_filename']}")
            if rid in current:
                st.caption("This PDF is already saved. Saving again updates its resident link and comment dates without creating a duplicate.")
            choice_report = dict(report)
            if rid in current:
                choice_report.update(resident_key=current[rid].get("resident_key"))
            resident_key, resident_name = _resident_selector(state, choice_report, "competency_link_" + rid,
                                                            existing=rid in current)
            selections.append((report, data, resident_key, resident_name))
            with st.expander("Check extracted competency values", expanded=False):
                _score_table(report)
            for warning in report.get("warnings", []):
                st.caption(warning)
    readable = len(selections)
    if len(parsed) < len(seen):
        st.warning("Only the readable reports listed here will be saved. Files with errors remain unsaved.")
    if st.button(f"Save {readable} report" + ("s" if readable != 1 else "") + " and link comments", type="primary", width="stretch",
                 disabled=not all(key and name for _, _, key, name in selections)):
        try:
            entries = [linked_report(report, data, resident_key=key, resident_name=name,
                                     start_date=start, end_date=end, date_basis=date_basis) for report, data, key, name in selections]
            expected = {entry["id"]: digest(current.get(entry["id"])) for entry in entries}
            def apply(latest):
                return save_report_links(latest, entries, expected)
            if commit(store, apply):
                st.session_state["notice"] = "Reports saved. Your combined reports are ready to view and download."
                st.session_state["_competency_upload_revision"] = int(st.session_state.get("_competency_upload_revision", 0)) + 1
                _open_saved(entries[0]["id"])
        except ValueError as exc:
            st.error(str(exc))


def _view_reports(state: dict, store, commit) -> None:
    reports = saved_reports(state)
    if not reports:
        st.info("Choose Upload reports to add the competency PDFs.")
        return
    ids = sorted(reports, key=lambda key: (reports[key].get("comment_end", ""),
                  reports[key].get("updated_at", ""), reports[key].get("linked_resident", "")), reverse=True)
    if st.session_state.get("competency_selected_report") not in reports:
        st.session_state["competency_selected_report"] = ids[0]
    selected = st.selectbox("Resident report", ids, format_func=lambda key: report_label(reports[key]),
                            key="competency_selected_report")
    report = reports[selected]
    st.subheader(report.get("linked_resident") or report.get("resident") or "Resident")
    st.caption(f"{report.get('program', '')} | {report.get('season', '')} | Reported PGY{report.get('pgy', '')}")
    st.caption(f"Comments: {report['comment_start']} through {report['comment_end']}")
    _score_table(report)
    _edit_comment_selection(state, report, selected, store, commit)
    date_basis = report.get("comment_date_basis", "completed_at")
    comments = cached_result(st.session_state, state, "competency-comments-visibility-v2",
                             [report["resident_key"], report["comment_start"], report["comment_end"], date_basis],
                             lambda: collect_resident_comments(state, report["resident_key"], report["comment_start"], report["comment_end"], date_basis=date_basis))
    snapshot = {key: copy.deepcopy(value) for key, value in report.items() if key != "source_pdf_b64"}
    def download():
        from epa_competency_pdf import build_competency_comments_pdf
        return build_competency_comments_pdf(snapshot, comments)
    st.download_button("Download combined report (.pdf)", data=download,
                       file_name=report_filename(report), mime="application/pdf", type="primary",
                       on_click="ignore", width="stretch", key="competency_download_" + selected)
    st.caption("Includes the scores above followed by all available comments grouped by rotation. New saved evaluations in these dates appear when you reload saved data.")
    _comments_view(comments)
    with st.expander("Original competency PDF", expanded=False):
        st.caption("PDF resident: " + str(report.get("resident") or "Not listed"))
        st.caption("Source: " + str(report.get("source_filename") or "Uploaded PDF"))
        source_snapshot = copy.deepcopy(report)
        st.download_button("Download original uploaded PDF", data=lambda: original_pdf(source_snapshot),
                           file_name=str(report.get("source_filename") or "original_competency_report.pdf"),
                           mime="application/pdf", on_click="ignore", key="competency_original_" + selected)


def _edit_comment_selection(state: dict, report: dict, selected: str, store, commit) -> None:
    revision = digest({key: value for key, value in report.items() if key != "source_pdf_b64"})[:16]
    with st.form("competency_edit_" + revision):
        st.write("Resident and dates for comments")
        st.caption("PDF name: " + str(report.get("resident") or "Not listed"))
        rkey, name = _resident_selector(state, report, "competency_edit_resident_" + revision, existing=True)
        start, end = _date_choices(report["comment_start"], report["comment_end"], "competency_edit_" + revision)
        date_basis = _date_basis_choice(report.get("comment_date_basis", "completed_at"), "competency_edit_" + revision)
        submitted = st.form_submit_button("Update comments", type="primary")
    if submitted:
        try:
            entry = linked_report(report, original_pdf(report), resident_key=rkey, resident_name=name,
                                  start_date=start, end_date=end, date_basis=date_basis)
            expected = {selected: digest(report)}
            def apply(latest):
                return save_report_links(latest, [entry], expected)
            if commit(store, apply):
                st.session_state["notice"] = "Resident and date selection saved. Comments updated."
                _open_saved(selected)
        except ValueError as exc:
            st.error(str(exc))


def competency_reports_page(state: dict, store, commit) -> None:
    st.subheader("Competency reports and comments")
    st.caption("Keep the competency scores and rotation feedback together for CCC review.")
    try:
        reports = saved_reports(state)
        requested = st.session_state.pop("_competency_open_saved", None)
        if requested:
            st.session_state["competency_action"] = VIEW
            st.session_state["competency_selected_report"] = requested
        action = st.radio("Reports", [VIEW, UPLOAD], index=0 if reports else 1,
                          horizontal=True, key="competency_action")
        if action == UPLOAD:
            _upload_reports(state, store, commit)
        else:
            _view_reports(state, store, commit)
    except (ValueError, KeyError, TypeError) as exc:
        st.error(f"The competency report could not be opened. {exc}")
