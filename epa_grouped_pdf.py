"""The original question-average table, organized by saved EPA links.

Read-only presentation. No new scoring, mapping, or archive-writing behavior.
The original report's question-average builder is the only numerical source.
"""
from __future__ import annotations

import copy
import io
from collections import defaultdict

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    BaseDocTemplate, CondPageBreak, Frame, LongTable, NextPageTemplate,
    PageBreak, PageTemplate, Paragraph, Spacer, TableStyle,
)

from epa_appd import EPA_COLUMNS, form_signature, mapping_for
from epa_core import match_form
from epa_pdf import (
    _BOLD, _LINE, _MUTED, _NAVY, _PALE, _REGULAR, _TEAL,
    _generated_label, _paragraph_text, _register_fonts, pdf_question_sections,
)


def mapping_details(forms: dict, saved_maps: dict) -> dict:
    """Read the real saved links. Never infer an EPA from wording or form name."""
    result = {}
    state = {"forms": forms, "settings": {"appd_question_maps": saved_maps}}
    for fid, form in forms.items():
        try:
            mapping = mapping_for(state, fid)
            links = mapping.get("questions", {})
            known = {str(q["id"]) for q in form.get("questions", [])}
            unknown = set(links) - known
            valid = bool(mapping.get("confirmed")) and mapping.get("form_signature") == form_signature(form)
            issues = []
            if unknown:
                issues.append("Saved links include question IDs not on this form. Review mapping.")
                valid = False
            if links and not valid:
                issues.append("EPA links need confirmation. This form is not currently counted in mapped EPA totals.")
            if not links:
                issues.append("No EPA links saved.")
            result[fid] = {
                "links": {qid: list(targets) for qid, targets in links.items() if qid in known},
                "ready": valid and not unknown,
                "issues": issues,
            }
        except (ValueError, TypeError, KeyError) as exc:
            result[fid] = {"links": {}, "ready": False,
                           "issues": ["Invalid saved EPA links. Review mapping: " + str(exc)]}
    return result


def regroup_question_sections(sections: list[dict], forms: dict, saved_maps: dict) -> list[dict]:
    """Reorder existing question summaries without recalculating any values.

    Each resident has EPA 1..12 groups, then an unassigned group. Each group
    retains separate source-form tables and their original question numbering.
    """
    details = mapping_details(forms, saved_maps)
    people = {}
    for section in sections:
        key = section["resident_key"]
        person = people.setdefault(key, {"resident_key": key, "resident": section["resident"],
                                         "groups": {}, "empty_epas": []})
        fid = section["form_id"]
        info = details[fid]
        buckets = defaultdict(list)
        for question in section["questions"]:
            targets = info["links"].get(str(question["question_id"]), [])
            for target in targets or ["No EPA assigned"]:
                buckets[target].append(copy.deepcopy(question))
        for target, questions in buckets.items():
            part = {key: copy.deepcopy(value) for key, value in section.items() if key != "questions"}
            part.update(questions=questions, mapping_ready=info["ready"],
                        issues=list(info["issues"]), scoring_confirmed=bool(forms[fid].get("scoring_confirmed")))
            person["groups"].setdefault(target, []).append(part)
    output = []
    for person in sorted(people.values(), key=lambda p: (p["resident"].casefold(), p["resident_key"])):
        groups = person["groups"]
        person["empty_epas"] = [epa for epa in EPA_COLUMNS if epa not in groups]
        person["groups"] = [
            {"epa": epa, "forms": sorted(groups[epa], key=lambda f: (f["epa"].casefold(), str(f["form_id"])))}
            for epa in EPA_COLUMNS + ["No EPA assigned"] if epa in groups
        ]
        output.append(person)
    return output


def grouped_question_sections(rows: pd.DataFrame, forms: dict, saved_maps: dict) -> list[dict]:
    return regroup_question_sections(pdf_question_sections(rows, forms), forms, saved_maps)


def report_snapshot(state: dict) -> dict:
    """Keep only data needed for the deferred PDF, without comments or secrets."""
    evaluations = {}
    for rid, record in state.get("evaluations", {}).items():
        entry = {key: copy.deepcopy(record.get(key)) for key in (
            "resident", "resident_key", "session_date", "completed_at", "rotation", "source_files")}
        # Form recognition uses IDs, not private free-text responses/comments.
        entry["responses"] = {str(qid): {} for qid in record.get("responses", {})}
        evaluations[rid] = entry
    return {
        "forms": copy.deepcopy(state.get("forms", {})),
        "evaluations": evaluations,
        "settings": {"appd_question_maps": copy.deepcopy(state.get("settings", {}).get("appd_question_maps", {}))},
        "conflicts": {key: {"incoming": {"resident_key": conflict.get("incoming", {}).get("resident_key")}}
                      for key, conflict in state.get("conflicts", {}).items()},
    }


def _in_period(record: dict, selection: dict) -> bool | None:
    """Same UTC interpretation and July-year rule as the Dashboard filter."""
    basis = selection.get("date_basis", "session_date")
    value = pd.to_datetime(record.get(basis), errors="coerce", utc=True)
    if pd.isna(value):
        return None
    start = int(selection.get("start_month", 7))
    year = value.year - int(value.month < start)
    required_year = selection.get("academic_year")
    months = selection.get("months")
    return (required_year is None or year == int(required_year)) and (months is None or value.month in months)


def form_coverage(state: dict, rows: pd.DataFrame, context: dict) -> dict:
    """A small per-resident checklist, including unmatched saved evaluations.

    Counts are evaluations, not response rows. 'In PDF' uses the exact rows
    passed to the original PDF. Period ignores the form filter on purpose so
    excluded forms can be distinguished from absent evaluations.
    """
    if rows.empty:
        return {}
    forms = state.get("forms", {})
    details = mapping_details(forms, state.get("settings", {}).get("appd_question_maps", {}))
    selection = context.get("report_selection", {})
    selected_forms = set(context.get("selected_form_ids") or [])
    resident_keys = set(rows["resident_key"].astype(str))
    counts = defaultdict(lambda: {"saved": 0, "period": 0, "undated": 0})
    unmatched = defaultdict(lambda: defaultdict(lambda: {"n": 0, "sources": set()}))
    for _, record in state.get("evaluations", {}).items():
        rkey = str(record.get("resident_key"))
        if rkey not in resident_keys:
            continue
        inside = _in_period(record, selection)
        match = match_form(record, forms)
        fid = match.get("form_id")
        if fid is None:
            if inside is True or inside is None:
                reason = "Missing/invalid date" if inside is None else str(match.get("status") or "unrecognized")
                entry = unmatched[rkey][reason]
                entry["n"] += 1
                entry["sources"].update(record.get("source_files") or [])
            continue
        entry = counts[(rkey, fid)]
        entry["saved"] += 1
        entry["period"] += int(inside is True)
        entry["undated"] += int(inside is None)
    result = {}
    for rkey in sorted(resident_keys):
        selected = rows.loc[rows["resident_key"].astype(str).eq(rkey)]
        checklist = []
        for fid, form in sorted(forms.items(), key=lambda item: (str(item[1].get("name", "")).casefold(), str(item[0]))):
            counter = counts[(rkey, fid)]
            form_rows = selected.loc[selected["form_id"].eq(fid)]
            in_pdf = int(form_rows["record_id"].nunique())
            info = details[fid]
            epas = [epa for epa in EPA_COLUMNS if any(epa in targets for targets in info["links"].values())]
            notes = []
            if selected_forms and fid not in selected_forms:
                notes.append("Excluded by form filter")
            elif counter["saved"] == 0:
                notes.append("No saved evaluations for this resident")
            elif counter["period"] == 0:
                notes.append("No evaluations in selected dates")
            elif in_pdf == 0:
                notes.append("Not represented in PDF rows")
            if counter["undated"]:
                notes.append(f"{counter['undated']} missing/invalid date")
            if counter["period"]:
                if not info["links"]:
                    notes.append("No EPA links")
                elif not info["ready"]:
                    notes.append("EPA mapping needs review")
                if not form.get("scoring_confirmed"):
                    notes.append("Scoring not confirmed")
                linked = set(info["links"])
                excluded = [str(i) for i, q in enumerate(form.get("questions", []), 1)
                            if str(q["id"]) in linked and not q.get("include")]
                if excluded:
                    notes.append("Linked but excluded: Q" + ", Q".join(excluded))
                not_linked = [str(i) for i, q in enumerate(form.get("questions", []), 1)
                              if q.get("include") and str(q["id"]) not in linked]
                if not_linked:
                    notes.append("Selected but unassigned: Q" + ", Q".join(not_linked))
                if in_pdf and info["links"] and not form_rows.loc[
                    form_rows["question_id"].astype(str).isin(linked) & form_rows["status"].eq("scored")
                ].empty:
                    pass
                elif in_pdf and info["links"] and form.get("scoring_confirmed"):
                    notes.append("No scored ratings on linked questions")
            if not notes:
                notes.append("Included")
            checklist.append({"form_id": fid, "form": str(form.get("name") or fid),
                              **counter, "in_pdf": in_pdf, "epas": epas, "notes": notes})
        unknown = [{"reason": reason, "n": entry["n"], "sources": sorted(entry["sources"])}
                   for reason, entry in sorted(unmatched[rkey].items())]
        pending = sum(conflict.get("incoming", {}).get("resident_key") == rkey
                      for conflict in state.get("conflicts", {}).values())
        result[rkey] = {"forms": checklist, "unmatched": unknown, "pending_conflicts": pending}
    return result


def grouped_context(state: dict, rows: pd.DataFrame, context: dict) -> dict:
    result = copy.deepcopy(context)
    result["group_by_mapped_epa"] = True
    result["question_maps"] = copy.deepcopy(state.get("settings", {}).get("appd_question_maps", {}))
    result["form_coverage"] = form_coverage(state, rows, result)
    return result


def build_grouped_question_pdf(rows: pd.DataFrame, forms: dict, context: dict) -> bytes:
    people = grouped_question_sections(rows, forms, context.get("question_maps", {}))
    return render_grouped_question_pdf(people, context)


def render_grouped_question_pdf(people: list[dict], context: dict) -> bytes:
    """Render the compact original four-column layout, not an audit ledger."""
    if not people:
        raise ValueError("No recognized evaluations match the selected report filters.")
    _register_fonts()
    page_width, page_height = letter
    margin = 0.55 * 72
    width = page_width - 2 * margin
    footer_top = 60
    styles = {
        "kicker": ParagraphStyle("GroupedKicker", fontName=_BOLD, fontSize=8, leading=10, textColor=_TEAL),
        "resident": ParagraphStyle("GroupedResident", fontName=_BOLD, fontSize=13, leading=17, textColor=_NAVY),
        "meta": ParagraphStyle("GroupedMeta", fontName=_REGULAR, fontSize=8.4, leading=11, textColor=_MUTED),
        "note": ParagraphStyle("GroupedNote", fontName=_REGULAR, fontSize=8, leading=10.5, textColor=_MUTED),
        "epa": ParagraphStyle("GroupedEPA", fontName=_BOLD, fontSize=15, leading=19, textColor=_TEAL,
                              spaceBefore=6, spaceAfter=7, keepWithNext=True),
        "form": ParagraphStyle("GroupedForm", fontName=_BOLD, fontSize=10.5, leading=14, textColor=_NAVY),
        "body": ParagraphStyle("GroupedBody", fontName=_REGULAR, fontSize=9.2, leading=12.1, textColor=_NAVY,
                               splitLongWords=True, allowWidows=1, allowOrphans=1),
        "number": ParagraphStyle("GroupedNumber", fontName=_REGULAR, fontSize=9.2, leading=12.1, textColor=_MUTED),
        "score": ParagraphStyle("GroupedScore", fontName=_REGULAR, fontSize=9.2, leading=12.1, textColor=_NAVY, alignment=TA_RIGHT),
        "thead": ParagraphStyle("GroupedHeader", fontName=_BOLD, fontSize=8.5, leading=11, textColor=colors.white),
        "theadr": ParagraphStyle("GroupedHeaderRight", fontName=_BOLD, fontSize=8.5, leading=11, textColor=colors.white, alignment=TA_RIGHT),
        "small": ParagraphStyle("GroupedSmall", fontName=_REGULAR, fontSize=8, leading=10.5, textColor=_NAVY),
    }
    def p(value, style="body"):
        return Paragraph(_paragraph_text(value), styles[style])

    buffer = io.BytesIO()
    document = BaseDocTemplate(buffer, pagesize=letter, leftMargin=margin, rightMargin=margin,
                               topMargin=margin, bottomMargin=footer_top, allowSplitting=1,
                               title="EPA question averages - grouped by EPA", author="EPA Tracker",
                               subject="Original question tables grouped by saved EPA links", pageCompression=1)
    templates, story = [], []
    generated = _generated_label(context.get("generated_at"))
    for person_index, person in enumerate(people):
        header = [(p("EPA QUESTION AVERAGES", "kicker"), 5),
                  (p("Resident: " + person["resident"], "resident"), 5),
                  (p(f"Academic year: {context.get('academic_year_label') or 'Selected period'}   |   "
                     f"{context.get('date_basis_label') or 'Session month'}", "meta"), 2),
                  (p("Months: " + str(context.get("months_label") or "All months"), "meta"), 2)]
        if generated:
            header.append((p("Generated: " + generated, "meta"), 2))
        if context.get("preview_note"):
            header.append((p(context["preview_note"], "note"), 3))
        heights = [para.wrap(width, page_height)[1] + gap for para, gap in header]
        top = page_height - margin - sum(heights) - 16
        if top - footer_top < 140:
            raise ValueError("The report heading is too long. Shorten the report notes.")
        frame = Frame(margin, footer_top, width, top - footer_top, leftPadding=0, rightPadding=0,
                      topPadding=0, bottomPadding=0, id=f"grouped_frame_{person_index}")
        def draw_page(canvas, doc, header=header, heights=heights, top=top):
            canvas.saveState()
            y = page_height - margin
            for (para, gap), height in zip(header, heights):
                y -= height - gap
                para.drawOn(canvas, margin, y)
                y -= gap
            canvas.setStrokeColor(_LINE)
            canvas.setLineWidth(0.5)
            canvas.line(margin, top + 8, page_width - margin, top + 8)
            canvas.setFillColor(_MUTED)
            canvas.setFont(_REGULAR, 7.1)
            canvas.drawString(margin, 46, "Original question numbers retained. A question may appear under more than one saved EPA link.")
            canvas.drawString(margin, 36, "Blank = not selected for scoring or no scored ratings. n = scored ratings.")
            canvas.line(margin, 29, page_width - margin, 29)
            canvas.setFont(_REGULAR, 7)
            canvas.drawString(margin, 18, "EPA Tracker")
            canvas.drawRightString(page_width - margin, 18, f"Page {doc.page}")
            canvas.restoreState()
        template = f"grouped_resident_{person_index}"
        templates.append(PageTemplate(id=template, frames=[frame], onPage=draw_page))
        if person_index:
            story.extend([NextPageTemplate(template), PageBreak()])
        for group in person["groups"]:
            target = group["epa"]
            story.extend([CondPageBreak(115), p(target, "epa")])
            if target == "No EPA assigned":
                story.append(p("These questions have no saved EPA link. They are not included in mapped EPA totals.", "note"))
                story.append(Spacer(1, 5))
            for form in group["forms"]:
                issues = [issue for issue in form.get("issues", []) if issue != "No EPA links saved."]
                if not form.get("scoring_confirmed", True):
                    issues.append("Scoring not confirmed. Question averages remain blank.")
                form_heading = f"{form['epa']}   |   {target}   |   Evaluations: {form['n_evaluations']}"
                title_cell = [p(form_heading, "form")]
                title_cell += [p(issue, "note") for issue in issues]
                data = [[title_cell, "", "", ""],
                        [p("#", "thead"), p("Question", "thead"), p("Average", "theadr"), p("n", "theadr")]]
                for question in form["questions"]:
                    data.append([p(question["number"], "number"), p(question["question"]),
                                 p(question["average_text"], "score"), p(question["n_text"], "score")])
                # Ordinary questions move intact to the next page. Only enable
                # within-row splitting for source wording taller than a page.
                needs_row_split = any(
                    p(question["question"]).wrap(width - 135, 1000000)[1] + 10 > top - footer_top - 100
                    for question in form["questions"]
                )
                table = LongTable(data, colWidths=[36, width - 121, 58, 27], repeatRows=2,
                                  splitByRow=1, splitInRow=int(needs_row_split), hAlign="LEFT")
                table.setStyle(TableStyle([
                    ("SPAN", (0, 0), (-1, 0)), ("BACKGROUND", (0, 0), (-1, 0), colors.white),
                    ("BACKGROUND", (0, 1), (-1, 1), _NAVY),
                    ("ROWBACKGROUNDS", (0, 2), (-1, -1), [colors.white, _PALE]),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                    ("LEFTPADDING", (0, 0), (-1, 0), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 1), (-1, 1), 7), ("BOTTOMPADDING", (0, 1), (-1, 1), 7),
                    ("LINEBELOW", (0, 2), (-1, -1), 0.35, _LINE),
                ]))
                story.extend([table, Spacer(1, 9)])
        coverage = context.get("form_coverage", {}).get(person["resident_key"])
        if coverage:
            story.extend([PageBreak(), p("Form checklist", "epa")])
            story.append(p("For this resident: Saved = all saved evaluations. Period = evaluations in the selected dates before the form filter. "
                           "In PDF = evaluations represented in these question tables.", "note"))
            story.append(Spacer(1, 8))
            data = [[p(label, "thead") for label in ("Form", "Saved", "Period", "In PDF", "EPA links", "Check")]]
            for form in coverage["forms"]:
                data.append([p(form["form"], "small"), p(form["saved"], "small"), p(form["period"], "small"),
                             p(form["in_pdf"], "small"),
                             p(", ".join(epa.replace("EPA ", "") for epa in form["epas"]) or "None", "small"),
                             p(". ".join(form["notes"]), "small")])
            table = LongTable(data, colWidths=[123, 45, 48, 48, 65, width - 329], repeatRows=1,
                              splitByRow=1, splitInRow=1, hAlign="LEFT")
            table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), _NAVY),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _PALE]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LINEBELOW", (0, 1), (-1, -1), 0.35, _LINE),
            ]))
            story.extend([table, Spacer(1, 9)])
            for item in coverage.get("unmatched", []):
                sources = ", ".join(item["sources"]) or "Source filename unavailable"
                story.append(p(f"Not in the question tables: {item['n']} saved evaluation(s), {item['reason']}. Files: {sources}.", "note"))
                story.append(Spacer(1, 5))
            if coverage.get("pending_conflicts"):
                story.append(p(f"{coverage['pending_conflicts']} pending upload conflict(s) for this resident. Incoming alternatives are not counted. Review under Admin.", "note"))
            story.append(p("This checks the saved archive only. A file that has never been uploaded cannot be detected here.", "note"))
        if person["empty_epas"]:
            story.extend([Spacer(1, 8), p("No mapped questions in this PDF: " + ", ".join(person["empty_epas"]) + ".", "note")])
        story.extend([Spacer(1, 6), p("Question averages are unchanged. The overall EPA calculation still averages within each evaluation first, then across evaluations. "
                                    "It is not necessarily the average of the question averages printed here.", "note")])
    document.addPageTemplates(templates)
    document.build(story)
    return buffer.getvalue()
