"""Password-protected Streamlit EPA tracker with an encrypted GitHub archive."""

from __future__ import annotations

import calendar
import copy
import hashlib
import io
import json
import math
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import altair as alt
import pandas as pd
import streamlit as st
from openpyxl.styles import Alignment, Font, PatternFill

from epa_auth import logout, require_login
from epa_core import (
    empty_state, epa_summary, filter_rows, ingest_evaluations, match_form,
    monthly_summary, normalize_code, parse_upload, question_summary,
    register_form, resolve_conflict, score_rows, utc_now,
)
from epa_storage import GitHubStore, StorageError
from epa_pdf import build_question_pdf
from epa_appd_ui import admin_page as appd_admin_page, export_page as appd_export_page


ROOT = Path(__file__).resolve().parent
NAVIGATION = ["Dashboard", "Upload", "Forms", "Review", "APPD Export", "Admin", "Settings"]
REPORT_FILTERS_KEY = "_epa_report_filters"
LABELS = {
    "resident": "Resident", "epa": "EPA", "question_id": "Question ID",
    "question": "Question", "average": "Average", "n": "Scored ratings",
    "n_missing": "Blank", "n_excluded": "Excluded by scale",
    "n_unmapped": "Needs scoring review", "n_evaluations": "Evaluations",
    "n_scored_evaluations": "Scored evaluations", "n_ratings": "Scored ratings",
    "n_questions_scored": "Questions with scores",
    "n_questions_expected": "Selected questions", "period": "Month",
    "academic_month": "Academic month", "academic_year": "Academic year start",
    "raw_response": "Original response", "score": "Mapped score",
    "status": "Scoring status", "session_date": "Session date",
    "completed_at": "Date completed", "rotation": "Rotation",
    "record_id": "Archive record ID", "included": "Included EPA question",
}


def fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def seed_state() -> dict:
    state = empty_state()
    with (ROOT / "seed_forms.json").open(encoding="utf-8") as file:
        forms = json.load(file)
    for form in forms:
        state = register_form(state, form)
    state["updated_at"] = None
    return state


def validate_state(state: dict) -> None:
    if not isinstance(state, dict) or state.get("schema_version") != 1:
        raise ValueError("This archive uses an unsupported format. It has not been changed.")
    for key in ("forms", "evaluations", "conflicts", "settings"):
        if not isinstance(state.get(key), dict):
            raise ValueError("The saved archive is incomplete. It has not been changed.")
    start = state["settings"].get("academic_year_start_month", 7)
    if not isinstance(start, int) or not 1 <= start <= 12:
        raise ValueError("The saved academic-year setting is invalid. It has not been changed.")


def commit(store: GitHubStore, transform) -> bool:
    """Reapply an intent to a freshly loaded archive, preserving concurrent uploads."""
    def guarded(current):
        current = seed_state() if current is None else current
        validate_state(current)
        before = copy.deepcopy(current)
        updated, result = transform(copy.deepcopy(current))
        validate_state(updated)
        if updated != before:
            updated["updated_at"] = utc_now()
        return updated, result

    try:
        with st.spinner("Saving..."):
            saved, result = store.transaction(guarded)
        st.session_state["archive"] = saved
        st.session_state["last_refreshed"] = utc_now()
        st.session_state["save_result"] = result
        st.session_state.pop("save_error", None)
        return True
    except (StorageError, ValueError) as exc:
        st.session_state["save_error"] = str(exc)
        st.error(f"Not saved. {exc}")
        return False


def public_frame(frame: pd.DataFrame, columns: list[str] | None = None) -> pd.DataFrame:
    if columns is None:
        columns = [c for c in frame.columns if c not in {"resident_key", "form_id", "question_order"}]
    return frame.reindex(columns=columns).rename(columns=LABELS)


def table(frame: pd.DataFrame, *, height="auto") -> None:
    st.dataframe(
        frame, hide_index=True, width="stretch", height=height,
        column_config={
            "Average": st.column_config.NumberColumn(format="%.2f"),
            "Mapped score": st.column_config.NumberColumn(format="%.2f"),
            "Question": st.column_config.TextColumn(width="large"),
        },
    )


def safe_export(frame: pd.DataFrame) -> pd.DataFrame:
    """Keep resident/source text from becoming a spreadsheet formula."""
    def protect(value):
        if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
            return "'" + value
        return value
    return frame.map(protect)


def csv_bytes(frame: pd.DataFrame) -> bytes:
    return safe_export(frame).to_csv(index=False).encode("utf-8-sig")


def workbook_bytes(sheets: dict[str, pd.DataFrame]) -> bytes:
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for name, frame in sheets.items():
            safe_export(frame).to_excel(writer, sheet_name=name[:31], index=False)
            sheet = writer.sheets[name[:31]]
            sheet.freeze_panes = "A2"
            sheet.auto_filter.ref = sheet.dimensions
            for cell in sheet[1]:
                cell.fill = PatternFill("solid", fgColor="183C50")
                cell.font = Font(color="FFFFFF", bold=True)
                cell.alignment = Alignment(wrap_text=True, vertical="top")
            sheet.row_dimensions[1].height = 32
            for column in sheet.columns:
                letter = column[0].column_letter
                header = str(column[0].value or "")
                width = 65 if header == "Question" else min(42, max(14, len(header) + 3))
                if header in {"Resident", "EPA"}:
                    width = 32
                sheet.column_dimensions[letter].width = width
                for cell in column[1:]:
                    cell.alignment = Alignment(vertical="top", wrap_text=True)
                    if isinstance(cell.value, float):
                        cell.number_format = "0.00"
            sheet.sheet_properties.pageSetUpPr.fitToPage = True
            sheet.page_setup.orientation = "landscape"
            sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
            sheet.page_setup.fitToWidth = 1
            sheet.page_setup.fitToHeight = 0
    return buffer.getvalue()


def pending_forms(state: dict) -> int:
    return sum(not form.get("scoring_confirmed") for form in state["forms"].values())


def recognition_rows(state: dict) -> list[dict]:
    rows = []
    for record in state["evaluations"].values():
        match = match_form(record, state["forms"])
        if match["status"] not in {"exact", "partial"}:
            rows.append({
                "Resident": record["resident"], "Session date": record.get("session_date"),
                "Rotation": record.get("rotation", ""), "Status": match["status"],
                "Question IDs": ", ".join(sorted(record["responses"])),
            })
    return rows


def report_context(state: dict, year: int, start_month: int, months: list[int], basis: str,
                   rows: pd.DataFrame) -> dict:
    ordered_months = [(start_month + i - 1) % 12 + 1 for i in range(12)]
    month_labels = [f"{calendar.month_abbr[m]} {year if m >= start_month else year + 1}"
                    for m in ordered_months if m in months]
    warnings = []
    unmapped = int(rows["status"].eq("unmapped").sum()) if not rows.empty else 0
    if unmapped:
        warnings.append(f"{unmapped} responses need scoring review and are not included in averages.")
    return {
        "academic_year_label": str(year) if start_month == 1 else f"{year}-{year + 1}",
        "months_label": ", ".join(month_labels),
        "date_basis_label": "Session month" if basis == "session_date" else "Completion month",
        "generated_at": utc_now(),
        "warnings": warnings,
    }


def current_report_view(state: dict, *, all_residents: bool = False) -> tuple[pd.DataFrame, dict]:
    """Rebuild the Dashboard selection, optionally including every resident."""
    rows = score_rows(state)
    start_month = state["settings"].get("academic_year_start_month", 7)
    now = datetime.now(ZoneInfo("America/New_York"))
    current_year = now.year - (now.month < start_month)
    preferences = st.session_state.get(REPORT_FILTERS_KEY, {})
    same_year_start = preferences.get("start_month", start_month) == start_month
    year = int(preferences.get("academic_year", current_year)) if same_year_start else current_year
    months = preferences.get("months", list(range(1, 13))) if same_year_start else list(range(1, 13))
    basis = preferences.get("date_basis", "session_date")
    selected = filter_rows(
        rows, academic_year=year, start_month=start_month, months=months, date_basis=basis,
        residents=None if all_residents else preferences.get("residents") or None,
        form_ids=preferences.get("form_ids") or None,
    )
    return selected, report_context(state, year, start_month, months, basis, selected)


def pdf_download_button(state: dict, rows: pd.DataFrame, context: dict, *, label: str,
                        key: str, filename: str, help_text: str) -> None:
    """Bind one deferred download to an immutable, session-private snapshot."""
    if rows.empty:
        st.download_button(label, data=b"", file_name=filename, mime="application/pdf",
                           disabled=True, width="stretch", key=key, help=help_text)
        return
    rows_snapshot = rows.copy(deep=True)
    used_forms = set(rows["form_id"].unique())
    forms_snapshot = copy.deepcopy({key: value for key, value in state["forms"].items()
                                    if key in used_forms})
    context_snapshot = copy.deepcopy(context)

    def make_pdf() -> bytes:
        return build_question_pdf(rows_snapshot, forms_snapshot, context_snapshot)

    st.download_button(label, data=make_pdf, file_name=filename, mime="application/pdf",
                       on_click="ignore", width="stretch", key=key, help=help_text)


def render_sidebar_pdf(state: dict, view: tuple[pd.DataFrame, dict] | None = None) -> None:
    """Offer selected-resident and all-resident PDFs for the same period and EPAs."""
    rows, context = current_report_view(state) if view is None else view
    preferences = st.session_state.get(REPORT_FILTERS_KEY, {})
    # With no resident filter the two selections already contain the same rows.
    all_rows, all_context = current_report_view(state, all_residents=True) if preferences.get("residents") else (rows, context)
    with st.sidebar:
        st.divider()
        pdf_download_button(
            state, rows, context, label="Download PDF", key="epa_pdf_download",
            filename=f"epa_question_averages_{context['academic_year_label']}.pdf",
            help_text="Every question is listed by EPA form and resident. Unselected or unscored averages stay blank.",
        )
        st.caption("Uses your Dashboard resident, EPA, and date selections.")
        pdf_download_button(
            state, all_rows, all_context, label="Download PDF for all residents",
            key="epa_pdf_all_residents_download",
            filename=f"epa_all_residents_{all_context['academic_year_label']}.pdf",
            help_text="One combined PDF for every resident with evaluations in the selected academic year, months, and EPA forms. Your resident selection stays unchanged.",
        )
        if all_rows.empty:
            st.caption("Choose a Dashboard period with saved evaluations.")
        else:
            count = all_rows["resident_key"].nunique()
            st.caption(f"Includes {count} {'resident' if count == 1 else 'residents'} in the selected period and EPA forms.")


def dashboard(state: dict) -> tuple[pd.DataFrame, dict] | None:
    st.header("Resident progress")
    if not state["evaluations"]:
        st.info("Upload your evaluation files to begin. Acutes and Adolescent Medicine forms are already available under Forms.")
        return
    rows = score_rows(state)
    unrecognized = recognition_rows(state)
    if unrecognized:
        st.warning(f"{len(unrecognized)} evaluations need a matching form. Upload the form definition to include them in reports.")
    if state["conflicts"]:
        st.warning(f"{len(state['conflicts'])} possible duplicates or corrections are waiting in Review. Incoming versions are not counted until resolved.")
    if pending_forms(state):
        st.info(f"{pending_forms(state)} forms need scoring confirmation in Forms. Their ratings remain unscored until you save the scale.")
    if rows.empty:
        st.info("Your evaluations are saved. Add their form definitions under Upload to recognize the question IDs.")
        return

    start_month = state["settings"].get("academic_year_start_month", 7)
    preferences = st.session_state.get(REPORT_FILTERS_KEY, {})
    date_label = st.radio("Track by", ["Session month", "Completion month"], horizontal=True,
                          index=1 if preferences.get("date_basis") == "completed_at" else 0,
                          key="dashboard_date_basis",
                          help="Session month follows the export's Session Date, which may be the rotation start. Completion month follows Date Completed.")
    basis = "session_date" if date_label == "Session month" else "completed_at"
    all_periods = filter_rows(rows, start_month=start_month, date_basis=basis)
    now = datetime.now(ZoneInfo("America/New_York"))
    current_year = now.year - (now.month < start_month)
    years = sorted({current_year, *all_periods["academic_year"].dropna().astype(int).tolist()}, reverse=True)
    year_label = lambda y: str(y) if start_month == 1 else f"{y}-{y + 1}"
    left, right = st.columns([1, 3])
    same_year_start = preferences.get("start_month", start_month) == start_month
    preferred_year = preferences.get("academic_year", current_year) if same_year_start else current_year
    with left:
        year = st.selectbox("Academic year", years, format_func=year_label,
                            index=years.index(preferred_year) if preferred_year in years else 0,
                            key=f"dashboard_year_{start_month}")
    ordered_months = [(start_month + i - 1) % 12 + 1 for i in range(12)]
    with right:
        preferred_months = preferences.get("months", ordered_months) if same_year_start else ordered_months
        months = st.multiselect("Months", ordered_months, default=preferred_months,
                                format_func=lambda m: calendar.month_abbr[m],
                                key=f"months_{start_month}")
    col1, col2 = st.columns(2)
    resident_names = rows.drop_duplicates("resident_key").set_index("resident_key")["resident"].to_dict()
    with col1:
        resident_keys = st.multiselect("Residents", sorted(resident_names), format_func=lambda k: resident_names[k],
                                       default=[key for key in preferences.get("residents", []) if key in resident_names],
                                       key="dashboard_residents", placeholder="All residents")
    forms = state["forms"]
    with col2:
        form_ids = st.multiselect("EPAs", sorted(forms, key=lambda k: forms[k]["name"]),
                                  format_func=lambda k: forms[k]["name"],
                                  default=[key for key in preferences.get("form_ids", []) if key in forms],
                                  key="dashboard_epas", placeholder="All EPAs")
    st.session_state[REPORT_FILTERS_KEY] = {
        "academic_year": year, "start_month": start_month, "months": list(months),
        "date_basis": basis, "residents": list(resident_keys), "form_ids": list(form_ids),
    }
    year_rows = filter_rows(rows, academic_year=year, start_month=start_month, date_basis=basis,
                            residents=resident_keys or None, form_ids=form_ids or None)
    selected = filter_rows(year_rows, months=months, start_month=start_month, date_basis=basis)
    undated = int(rows.loc[pd.to_datetime(rows[basis], errors="coerce", format="mixed").isna(), "record_id"].nunique())
    if undated:
        st.warning(f"{undated} evaluations have no valid {date_label.lower().replace(' month', ' date')} and are excluded from dated reports. See Review.")
    if not months:
        st.info("Select at least one month.")
        return
    if selected.empty:
        st.info("No recognized evaluations match these filters.")
        return
    summary = epa_summary(selected)
    questions = question_summary(selected)
    monthly = monthly_summary(selected)
    cumulative = monthly_summary(year_rows, cumulative=True)
    if not cumulative.empty:
        cumulative = cumulative.loc[cumulative["period"].str[-2:].astype(int).isin(months)]
    unmapped = selected.loc[selected["status"].eq("unmapped")]
    if not unmapped.empty:
        st.warning(f"{len(unmapped)} responses use codes missing from the saved scale. They are excluded from the averages until you map them in Forms.")
    counters = st.columns(4)
    counters[0].metric("Residents", selected["resident_key"].nunique())
    counters[1].metric("EPAs", selected["form_id"].nunique())
    counters[2].metric("Evaluations", selected["record_id"].nunique())
    counters[3].metric("Scored ratings", int(selected["status"].eq("scored").sum()))
    summary_tab, questions_tab, trends_tab, export_tab = st.tabs(["EPA averages", "Each question", "Monthly progress", "Download"])
    with summary_tab:
        st.caption("Average of the selected question averages. Each question has equal weight. Blank, excluded, and unmapped responses do not count as zero.")
        table(public_frame(summary))
        if not summary.empty and (summary["n_questions_scored"] < summary["n_questions_expected"]).any():
            st.caption("An EPA average can be based on only part of the form. Compare Questions with scores with Selected questions before interpreting it.")
    with questions_tab:
        st.caption("Final average for each question over the selected months, with its own scored-response count.")
        table(public_frame(questions), height=500)
    with trends_tab:
        trend_kind = st.radio("Progress view", ["Each month", "Cumulative from academic-year start"], horizontal=True)
        trend_data = monthly if trend_kind == "Each month" else cumulative
        st.caption("Cumulative results use all earlier months of this academic year, including earlier months hidden by the filter. Empty months are not zero." if trend_kind != "Each month" else "Each point uses only that month's evaluations. Months without ratings remain blank.")
        if selected["resident_key"].nunique() > 8:
            st.info("Select up to eight residents above to show the chart. The table and downloads include everyone selected.")
        elif not trend_data.empty and trend_data["average"].notna().any():
            chosen_question = st.selectbox("Chart", ["Overall EPA", "Individual question"])
            if chosen_question == "Individual question":
                question_options = selected.loc[selected["included"]].drop_duplicates(["form_id", "question_id"])
                opts = list(zip(question_options["form_id"], question_options["question_id"]))
                labels = {(r.form_id, r.question_id): f"{r.epa} | {r.question_id}: {r.question}" for r in question_options.itertuples()}
                chosen = st.selectbox("Question", opts, format_func=lambda k: labels[k])
                chart_parts = []
                source = year_rows if trend_kind != "Each month" else selected
                source = source.loc[source["form_id"].eq(chosen[0]) & source["question_id"].eq(chosen[1])]
                for period in sorted(source["period"].dropna().unique()):
                    if int(period[-2:]) not in months:
                        continue
                    sample = source.loc[source["period"].le(period) if trend_kind != "Each month" else source["period"].eq(period)]
                    part = epa_summary(sample)
                    part["period"] = period
                    chart_parts.append(part)
                chart_data = pd.concat(chart_parts, ignore_index=True) if chart_parts else pd.DataFrame()
            else:
                chart_data = trend_data
            draw_chart(chart_data, year, start_month, months)
        else:
            st.info("Confirm the scoring scales under Forms to show progress.")
        table(public_frame(trend_data), height=350)
    with export_tab:
        month_names = ", ".join(calendar.month_abbr[m] for m in ordered_months if m in months)
        notes = pd.DataFrame([
            {"Setting": "Academic year", "Value": year_label(year)},
            {"Setting": "Academic year starts", "Value": calendar.month_name[start_month]},
            {"Setting": "Months selected", "Value": month_names},
            {"Setting": "Date basis", "Value": date_label},
            {"Setting": "Question average", "Value": "Sum of mapped scores / number of scored responses for that question."},
            {"Setting": "Overall EPA average", "Value": "Mean of question averages, with equal weight for each question with a score. Not an official entrustment decision."},
            {"Setting": "Cumulative", "Value": "Recalculated from all evaluations from academic-year start through each displayed month, even if earlier months are hidden."},
            {"Setting": "Missing values", "Value": "Blank, excluded, unconfirmed, and unmapped values are excluded from the score denominator. Missing is not zero."},
            {"Setting": "Pending conflicts", "Value": str(len(state["conflicts"]))},
            {"Setting": "Unrecognized forms", "Value": str(len(unrecognized))},
            {"Setting": "Unconfirmed forms", "Value": str(pending_forms(state))},
            {"Setting": "Evaluations without valid chosen date", "Value": str(undated)},
            {"Setting": "Generated", "Value": utc_now()},
        ])
        sheets = {
            "EPA averages": public_frame(summary), "Question averages": public_frame(questions),
            "Monthly EPA": public_frame(monthly), "Cumulative EPA": public_frame(cumulative),
            "Response audit": public_frame(selected), "Report notes": notes,
        }
        if st.button("Prepare Excel report", type="primary"):
            try:
                data = workbook_bytes(sheets)
                st.download_button("Download Excel report", data, file_name=f"epa_report_{year_label(year)}.xlsx",
                                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            except Exception:
                st.error("The Excel report could not be prepared. You can download the CSV reports below.")
        st.download_button("Question averages CSV", csv_bytes(public_frame(questions)), file_name=f"epa_questions_{year_label(year)}.csv", mime="text/csv")
        st.download_button("EPA averages CSV", csv_bytes(public_frame(summary)), file_name=f"epa_averages_{year_label(year)}.csv", mime="text/csv")
    return selected, report_context(state, year, start_month, months, basis, selected)


def draw_chart(frame: pd.DataFrame, year: int, start_month: int, months: list[int]) -> None:
    if frame.empty:
        return
    start = pd.Timestamp(year=year, month=start_month, day=1)
    periods = [period.strftime("%Y-%m") for period in pd.date_range(start, periods=12, freq="MS") if period.month in months]
    data = frame.copy()
    data["Series"] = data["resident"] + " | " + data["epa"]
    collisions = data.drop_duplicates(["resident_key", "form_id"]).groupby("Series").size()
    colliding = set(collisions[collisions.gt(1)].index)
    data.loc[data["Series"].isin(colliding), "Series"] += " | " + data.loc[data["Series"].isin(colliding), "form_id"].str[-6:]
    pieces = []
    for _, group in data.groupby(["resident_key", "form_id"], sort=False):
        label = group["Series"].iloc[0]
        group = group.set_index("period").reindex(periods).reset_index().rename(columns={"index": "period"})
        group["Series"] = label
        skipped = pd.to_datetime(group["period"]).diff().dt.days.gt(35)
        group["segment"] = label + " / " + (group["average"].isna() | skipped).cumsum().astype(str)
        pieces.append(group)
    plot = pd.concat(pieces, ignore_index=True)
    chart = alt.Chart(plot).mark_line(point=True).encode(
        x=alt.X("period:N", sort=periods, title="Month", axis=alt.Axis(labelAngle=-45)),
        y=alt.Y("average:Q", title="Average EPA score", scale=alt.Scale(zero=False)),
        color=alt.Color("Series:N", title="Resident / EPA"),
        detail="segment:N",
        tooltip=[alt.Tooltip("resident:N", title="Resident"), alt.Tooltip("epa:N", title="EPA"),
                 alt.Tooltip("period:N", title="Month"), alt.Tooltip("average:Q", title="Average", format=".2f"),
                 alt.Tooltip("n_evaluations:Q", title="Evaluations"), alt.Tooltip("n_ratings:Q", title="Scored ratings")],
    ).properties(height=340)
    st.altair_chart(chart, width="stretch")


def upload_page(state: dict, store: GitHubStore) -> None:
    st.header("Upload files")
    st.caption("Upload one or more original .tab files. Forms and evaluations are recognized automatically.")
    files = st.file_uploader("Forms or evaluations", type=["tab", "tsv", "txt"], accept_multiple_files=True)
    if not files:
        return
    signature = fingerprint([(f.name, hashlib.sha256(f.getvalue()).hexdigest()) for f in files])
    if st.button("Review uploads", type="primary"):
        parsed, errors = [], []
        for file in files:
            try:
                item = parse_upload(file.getvalue(), file.name)
                item["filename"] = file.name
                parsed.append(item)
            except ValueError as exc:
                errors.append(f"{file.name}: {exc}")
        st.session_state["upload_preview"] = {"signature": signature, "items": parsed, "errors": errors}
    preview = st.session_state.get("upload_preview")
    if not preview or preview["signature"] != signature:
        return
    for error in preview["errors"]:
        st.error(error)
    items = preview["items"]
    if not items:
        return
    preview_rows, warnings = [], []
    proposed = copy.deepcopy(state)
    names = {}
    for item in items:
        if item["kind"] == "form":
            form = item["form"]
            known = form["id"] in state["forms"]
            if not known and form["id"] not in names:
                names[form["id"]] = st.text_input(f"EPA name for {item['filename']}", value=form["name"], key=f"new_name_{form['id']}")
            proposed_name = names.get(form["id"])
            proposed = register_form(proposed, form, proposed_name if proposed_name and proposed_name.strip() else None)
            preview_rows.append({"File": item["filename"], "Type": "Form definition", "Rows": len(form["questions"]), "Result": "Already remembered" if known else "New form"})
        warnings.extend(item.get("warnings", []))
    for item in items:
        if item["kind"] == "evaluations":
            records = item["evaluations"]
            matched = sum(match_form(record, proposed["forms"])["form_id"] is not None for record in records)
            preview_rows.append({"File": item["filename"], "Type": "Evaluations", "Rows": len(records), "Result": f"{matched} recognized, {len(records) - matched} need a form"})
    table(pd.DataFrame(preview_rows))
    if warnings:
        with st.expander(f"Import notes ({len(warnings)})"):
            for warning in warnings:
                st.write(warning)
    records = [record for item in items if item["kind"] == "evaluations" for record in item["evaluations"]]
    _, estimate = ingest_evaluations(proposed, records)
    st.caption(f"Preview: {estimate['added']} new evaluations, {estimate['duplicates']} already stored, {estimate['conflicts']} need review.")
    if preview["errors"]:
        st.caption("Files with errors will not be imported. The valid files shown above can be saved.")
    if st.button("Save valid files to GitHub", disabled=any(not name.strip() for name in names.values())):
        def apply(current):
            for item in items:
                if item["kind"] == "form":
                    current = register_form(current, item["form"], names.get(item["form"]["id"]))
            return ingest_evaluations(current, records)
        if commit(store, apply):
            result = st.session_state["save_result"]
            st.session_state["notice"] = f"Saved. {result['added']} new evaluations, {result['duplicates']} already stored, {result['conflicts']} need review."
            st.session_state.pop("upload_preview", None)
            st.rerun()


def observed_codes(state: dict, form_id: str, questions: list[dict]) -> set[str]:
    ids = {q["id"] for q in questions if q.get("include")}
    values = set()
    for record in state["evaluations"].values():
        if match_form(record, state["forms"])["form_id"] == form_id:
            for qid in ids:
                value = normalize_code(record["responses"].get(qid, {}).get("response", ""))
                if value:
                    values.add(value)
    return values


def scale_editor(mapping: dict, observed: set[str], key: str) -> pd.DataFrame:
    codes = set(mapping) | observed
    def ordering(code):
        try:
            return (0, float(code))
        except ValueError:
            return (1, str(code))
    rows = [{"Response code": code, "Count in average": mapping.get(code) is not None,
             "Score": mapping.get(code)} for code in sorted(codes, key=ordering)]
    frame = pd.DataFrame(rows, columns=["Response code", "Count in average", "Score"])
    frame["Score"] = pd.to_numeric(frame["Score"], errors="coerce")
    return st.data_editor(frame, hide_index=True, num_rows="dynamic", width="stretch", key=key,
                          column_config={"Response code": st.column_config.TextColumn(required=True),
                                         "Count in average": st.column_config.CheckboxColumn(default=False),
                                         "Score": st.column_config.NumberColumn(format="%.2f")})


def read_scale(frame: pd.DataFrame) -> dict:
    result = {}
    for _, row in frame.iterrows():
        if pd.isna(row["Response code"]) or not str(row["Response code"]).strip():
            continue
        code = normalize_code(row["Response code"])
        if code in result:
            raise ValueError(f"Response code {code} appears more than once in the scoring table.")
        if bool(row["Count in average"]):
            value = row["Score"]
            if pd.isna(value) or not math.isfinite(float(value)):
                raise ValueError(f"Enter a numeric score for response code {code}, or uncheck Count in average.")
            result[code] = float(value)
        else:
            result[code] = None
    if not result:
        raise ValueError("Add at least one response code to the scoring table.")
    return result


def forms_page(state: dict, store: GitHubStore) -> None:
    st.header("Remembered forms")
    st.caption("The app identifies forms by their question IDs. Review the scoring once for each new form.")
    if not state["forms"]:
        st.info("Upload a form definition first.")
        return
    form_id = st.selectbox("Form", sorted(state["forms"], key=lambda k: state["forms"][k]["name"]),
                           format_func=lambda k: state["forms"][k]["name"])
    form = state["forms"][form_id]
    version = fingerprint(form)
    prefix = f"form_{form_id}_{version[:10]}"
    if not form.get("scoring_confirmed"):
        st.warning("Scoring is not confirmed. The starting scale proposes scores 1-5 and excludes code 6. Check the actual response labels before saving. No ratings from this form are averaged yet.")
    name = st.text_input("EPA name", form["name"], key=f"{prefix}_name")
    qframe = pd.DataFrame([{"Include": bool(q.get("include")), "Question ID": q["id"], "Question": q["text"]} for q in form["questions"]])
    st.subheader("Questions to average")
    edited = st.data_editor(qframe, hide_index=True, disabled=["Question ID", "Question"], width="stretch", key=f"{prefix}_questions",
                            column_config={"Include": st.column_config.CheckboxColumn(), "Question": st.column_config.TextColumn(width="large")})
    proposed_questions = copy.deepcopy(form["questions"])
    for question, include in zip(proposed_questions, edited["Include"]):
        question["include"] = bool(include)
    st.subheader("Response codes")
    st.caption("Use the labels on your original form to decide which codes count. Unchecked codes are retained but excluded. Blank responses are always excluded.")
    values = observed_codes(state, form_id, proposed_questions)
    scale = scale_editor(form.get("score_map", {}), values, f"{prefix}_scale")
    override_id = None
    override = None
    with st.expander("A different scale for one question"):
        options = [None] + [q["id"] for q in form["questions"]]
        labels = {q["id"]: f"{q['id']}: {q['text']}" for q in form["questions"]}
        override_id = st.selectbox("Question to customize", options,
                                   format_func=lambda k: "Choose a question" if k is None else labels[k], key=f"{prefix}_override")
        if override_id:
            selected_question = next(q for q in form["questions"] if q["id"] == override_id)
            use_override = st.checkbox("Use a separate scale for this question", value=selected_question.get("score_map") is not None,
                                       key=f"{prefix}_override_on_{override_id}")
            if use_override:
                question_codes = observed_codes(state, form_id, [{"id": override_id, "include": True}])
                override = scale_editor(selected_question.get("score_map") or form["score_map"], question_codes, f"{prefix}_override_scale_{override_id}")
    confirmed = st.checkbox("I checked the selected questions and the meaning of the response codes", value=bool(form.get("scoring_confirmed")), key=f"{prefix}_confirmed")
    if st.button("Save form and scoring", type="primary"):
        try:
            if not name.strip():
                raise ValueError("Enter an EPA name.")
            if not any(q["include"] for q in proposed_questions):
                raise ValueError("Select at least one question to average.")
            new_form = copy.deepcopy(form)
            new_form.update(name=name.strip(), questions=proposed_questions, score_map=read_scale(scale), scoring_confirmed=confirmed)
            if override_id:
                question = next(q for q in new_form["questions"] if q["id"] == override_id)
                question["score_map"] = read_scale(override) if override is not None else None
            if confirmed:
                for question in new_form["questions"]:
                    mapping = question.get("score_map") if question.get("score_map") is not None else new_form["score_map"]
                    if question["include"] and not any(value is not None for value in mapping.values()):
                        raise ValueError(f"Question {question['id']} needs at least one code that counts as a score.")
            def apply(current):
                if fingerprint(current["forms"].get(form_id)) != version:
                    raise ValueError("This form changed in another session. Reload saved data and review the new settings before saving.")
                current["forms"][form_id] = copy.deepcopy(new_form)
                return current, "Form saved. Existing evaluations have been recalculated."
            if commit(store, apply):
                st.session_state["notice"] = st.session_state["save_result"]
                st.rerun()
        except ValueError as exc:
            st.error(str(exc))


def record_details(record: dict, forms: dict) -> None:
    st.write(f"Resident: {record['resident']}")
    st.write(f"Evaluator: {record.get('evaluator', '')}")
    st.write(f"Session: {record.get('session_date') or 'Missing'} | Completed: {record.get('completed_at') or 'Missing'}")
    st.write(f"Rotation: {record.get('rotation', '')}")
    match = match_form(record, forms)
    form = forms.get(match["form_id"], {})
    question_text = {q["id"]: q["text"] for q in form.get("questions", [])}
    table(pd.DataFrame([{"Question ID": qid, "Question": question_text.get(qid, ""),
                         "Response": response.get("response", ""), "Comment": response.get("comment", "")}
                        for qid, response in record["responses"].items()]), height=350)
    for field, label in [("overall_comments", "Overall comments"), ("subject_comments", "Resident comments")]:
        if record.get(field):
            st.text(f"{label}: {record[field]}")


def review_page(state: dict, store: GitHubStore) -> None:
    st.header("Review")
    conflict_tab, unmatched_tab, scoring_tab, records_tab = st.tabs(["Duplicates and corrections", "Unrecognized forms", "Scoring and dates", "Saved evaluations"])
    with conflict_tab:
        if not state["conflicts"]:
            st.info("No duplicate or correction decisions are waiting.")
        else:
            conflict_id = st.selectbox("Evaluation to review", list(state["conflicts"]),
                                       format_func=lambda k: f"{state['conflicts'][k]['incoming']['resident']} | {state['conflicts'][k]['incoming'].get('session_date') or 'Undated'} | {k[:8]}")
            conflict = state["conflicts"][conflict_id]
            candidate_ids = conflict.get("candidate_ids", [conflict["existing_id"]])
            target_id = conflict["existing_id"]
            if len(candidate_ids) > 1:
                st.warning("Several saved evaluations could match. Select the saved evaluation to compare before replacing anything.")
                target_id = st.selectbox("Saved evaluation to compare", candidate_ids,
                                         format_func=lambda k: f"{state['evaluations'][k].get('completed_at') or 'No completion date'} | {state['evaluations'][k].get('evaluator', '')} | {k[:8]}",
                                         index=None, placeholder="Select an evaluation", key=f"target_{conflict_id}")
            existing = state["evaluations"].get(target_id)
            st.caption("The saved version is counted. The incoming version is held here until you choose what to do.")
            left, right = st.columns(2)
            with left:
                st.subheader("Saved version")
                if existing:
                    record_details(existing, state["forms"])
            with right:
                st.subheader("Incoming version")
                record_details(conflict["incoming"], state["forms"])
            choices = {"Dismiss incoming version and keep saved data": "keep"}
            if target_id is not None:
                choices["Replace the selected saved version with incoming"] = "replace"
            if conflict.get("kind") == "possible_duplicate":
                choices["These are separate evaluations. Count both."] = "add"
            choice = st.radio("Decision", list(choices), key=f"decision_{conflict_id}")
            if st.button("Save decision", type="primary"):
                expected = fingerprint(conflict)
                existing_version = fingerprint(existing)
                def apply(current):
                    if fingerprint(current["conflicts"].get(conflict_id)) != expected or fingerprint(current["evaluations"].get(target_id)) != existing_version:
                        raise ValueError("This evaluation changed in another session. Reload saved data before deciding.")
                    return resolve_conflict(current, conflict_id, choices[choice], target_id=target_id if choices[choice] == "replace" else None), "Review decision saved."
                if commit(store, apply):
                    st.session_state["notice"] = st.session_state["save_result"]
                    st.rerun()
    with unmatched_tab:
        unrecognized = recognition_rows(state)
        if unrecognized:
            st.caption("Upload the corresponding form-definition .tab file. Saved evaluations will match automatically once their question IDs identify one form. Ambiguous matches are never guessed.")
            table(pd.DataFrame(unrecognized))
        else:
            st.info("All saved evaluations match a remembered form.")
    with scoring_tab:
        rows = score_rows(state)
        attention = rows.loc[rows["status"].isin(["unmapped", "unconfirmed"])]
        if not attention.empty:
            st.caption("Edit the form's response codes in Forms. Unmapped and unconfirmed ratings are not counted in averages.")
            table(public_frame(attention, ["resident", "epa", "question_id", "question", "raw_response", "status"]))
        else:
            st.info("No unmatched response codes or unconfirmed ratings.")
        dates = [{"Resident": r["resident"], "Session date": r.get("session_date"), "Date completed": r.get("completed_at"),
                  "Rotation": r.get("rotation", "")} for r in state["evaluations"].values() if not r.get("session_date") or not r.get("completed_at")]
        if dates:
            st.subheader("Incomplete dates")
            st.caption("Correct the date in the source system and re-export. Until then, a record can only appear in reports based on its valid date field.")
            table(pd.DataFrame(dates))
    with records_tab:
        records = state["evaluations"]
        if not records:
            st.info("No evaluations have been saved yet.")
        else:
            search = st.text_input("Find a resident or rotation")
            ids = [key for key, record in records.items() if search.casefold() in f"{record['resident']} {record.get('rotation', '')}".casefold()]
            if ids:
                record_id = st.selectbox("Saved evaluation", ids,
                                         format_func=lambda k: f"{records[k]['resident']} | {records[k].get('session_date') or 'Undated'} | {records[k].get('rotation', '')} | {k[:8]}")
                record_details(records[record_id], state["forms"])
            else:
                st.info("No evaluations match that search.")


def settings_page(state: dict, store: GitHubStore) -> None:
    st.header("Settings")
    current_start = state["settings"].get("academic_year_start_month", 7)
    start = st.selectbox("Academic year starts in", list(range(1, 13)), index=current_start - 1, format_func=lambda m: calendar.month_name[m])
    if st.button("Save academic-year setting"):
        def apply(current):
            if current["settings"].get("academic_year_start_month", 7) != current_start:
                raise ValueError("The academic-year setting changed elsewhere. Reload saved data before saving.")
            current["settings"]["academic_year_start_month"] = start
            return current, "Academic-year setting saved."
        if commit(store, apply):
            st.session_state["notice"] = st.session_state["save_result"]
            st.rerun()
    with st.expander("How averages and saved data work"):
        st.write("Each question average uses all valid mapped ratings for that resident and EPA in the selected date range. The overall EPA average gives each question average equal weight. It is a descriptive summary, not a separate entrustment determination.")
        st.write("Responses are preserved as exported. Changing a form's scale recalculates reports without re-uploading. Unexpected codes are flagged instead of treated as numeric scores.")
        st.write("The archive contains forms, evaluations, and review decisions, encrypted before it is saved to GitHub. Reopening the app restores the archive after login.")
        st.write("Exact re-uploads are skipped. Because these exports do not contain a unique evaluation ID, similar records with changed completion dates are held for review.")
        st.write("Residents are matched by their exported name with spacing and capitalization normalized. Different spellings remain separate. People who share an identical name cannot be separated without an additional source identifier.")
        st.write("The shared password controls app access. Use a private data repository and keep the password, GitHub token, and encryption key in Streamlit Secrets. GitHub history retains prior encrypted versions.")


def main() -> None:
    st.set_page_config(page_title="EPA Tracker", layout="wide", initial_sidebar_state="expanded")
    try:
        app_config = dict(st.secrets.get("app", {}))
    except (FileNotFoundError, KeyError):
        st.title("EPA Tracker")
        st.error("Add your app configuration in Streamlit Secrets. See the included README and secrets.toml.example.")
        st.stop()
    require_login(app_config)
    try:
        store = GitHubStore(dict(st.secrets["github"]), st.secrets["security"]["fernet_key"])
    except (KeyError, StorageError, ValueError):
        st.error("The GitHub or encryption settings are missing or invalid. Check Streamlit Secrets against secrets.toml.example.")
        st.stop()
    if "archive" not in st.session_state:
        try:
            with st.spinner("Loading saved data..."):
                saved, _ = store.load()
            saved = seed_state() if saved is None else saved
            validate_state(saved)
            st.session_state["archive"] = saved
            st.session_state["last_refreshed"] = utc_now()
        except (StorageError, ValueError) as exc:
            st.error(f"Saved data could not be loaded. {exc}")
            if st.button("Try loading again"):
                st.rerun()
            if st.button("Sign out"):
                logout()
                st.rerun()
            st.stop()
    state = st.session_state["archive"]
    with st.sidebar:
        st.title("EPA Tracker")
        page = st.radio("Navigate", NAVIGATION, label_visibility="collapsed")
        st.divider()
        st.caption(f"{len(state['evaluations'])} evaluations | {len(state['forms'])} forms")
        if state.get("updated_at"):
            when = pd.Timestamp(state["updated_at"]).tz_convert("America/New_York")
            st.caption(f"Saved: {when.strftime('%b %d, %Y %I:%M %p %Z')}")
        else:
            st.caption("Ready for first save")
        if st.session_state.get("save_error"):
            st.error("Last change was not saved.")
        if st.button("Reload saved data", width="stretch"):
            st.session_state.pop("archive", None)
            st.session_state.pop("save_error", None)
            st.session_state.pop("upload_preview", None)
            st.rerun()
        if st.button("Sign out", width="stretch"):
            logout()
            st.rerun()
    if notice := st.session_state.pop("notice", None):
        st.info(notice)
    report_view = None
    if page == "Dashboard":
        report_view = dashboard(state)
    elif page == "Upload":
        upload_page(state, store)
    elif page == "Forms":
        forms_page(state, store)
    elif page == "Review":
        review_page(state, store)
    elif page == "APPD Export":
        appd_export_page(state, store, commit)
    elif page == "Admin":
        appd_admin_page(state, store, commit)
    else:
        settings_page(state, store)
    render_sidebar_pdf(state, report_view)


if __name__ == "__main__":
    main()
