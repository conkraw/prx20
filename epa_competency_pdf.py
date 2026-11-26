"""Print imported competency recommendations with original rotation feedback.

This renderer never calculates, converts, or updates a score. It takes the
reviewed source recommendations and the comment collector's read-only result.
"""
from __future__ import annotations

import io
import re
from collections import OrderedDict
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    BaseDocTemplate, CondPageBreak, Frame, PageTemplate, Paragraph, Spacer,
    Table, TableStyle,
)

from epa_pdf import _BOLD, _LINE, _MUTED, _NAVY, _PALE, _REGULAR, _TEAL, _register_fonts


_DOMAINS = OrderedDict([
    ("PC", "Patient Care"),
    ("MK", "Medical Knowledge"),
    ("SBP", "Systems-Based Practice"),
    ("PBLI", "Practice-Based Learning and Improvement"),
    ("PROF", "Professionalism"),
    ("ICS", "Interpersonal and Communication Skills"),
])


def _text(value: object) -> str:
    """Escape all source markup and keep source line breaks and punctuation."""
    value = "" if value is None else str(value)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = "".join(c for c in value if ord(c) >= 32 or c in "\n\t")
    return escape(value).replace("\t", "    ").replace("\n", "<br/>")


def _nonempty(value: object) -> bool:
    return value is not None and bool(str(value).strip())


def _styles() -> dict:
    return {
        "title": ParagraphStyle("CompetencyTitle", fontName=_BOLD, fontSize=17,
                                leading=21, textColor=_NAVY, spaceAfter=7),
        "resident": ParagraphStyle("CompetencyResident", fontName=_BOLD, fontSize=12,
                                   leading=16, textColor=_NAVY, spaceAfter=5),
        "section": ParagraphStyle("CompetencySection", fontName=_BOLD, fontSize=11,
                                  leading=15, textColor=_TEAL, spaceBefore=8,
                                  spaceAfter=5, keepWithNext=True),
        "rotation": ParagraphStyle("CommentRotation", fontName=_BOLD, fontSize=11,
                                   leading=15, textColor=_TEAL, spaceBefore=8,
                                   spaceAfter=5, keepWithNext=True),
        "evaluation": ParagraphStyle("CommentEvaluation", fontName=_BOLD, fontSize=9.3,
                                     leading=12.5, textColor=_NAVY, spaceBefore=4,
                                     spaceAfter=3, keepWithNext=True),
        "meta": ParagraphStyle("CompetencyMeta", fontName=_REGULAR, fontSize=8.2,
                               leading=11, textColor=_MUTED, spaceAfter=3,
                               splitLongWords=True),
        "eval_meta": ParagraphStyle("CommentMeta", fontName=_REGULAR, fontSize=8,
                                    leading=10.5, textColor=_MUTED, spaceAfter=4,
                                    splitLongWords=True, keepWithNext=True),
        "note": ParagraphStyle("CompetencyNote", fontName=_REGULAR, fontSize=8,
                               leading=10.5, textColor=_MUTED, spaceAfter=4,
                               splitLongWords=True),
        "prompt": ParagraphStyle("CommentPrompt", fontName=_BOLD, fontSize=8.7,
                                 leading=12, textColor=_NAVY, spaceBefore=4,
                                 spaceAfter=3, keepWithNext=True, splitLongWords=True),
        "body": ParagraphStyle("OriginalComment", fontName=_REGULAR, fontSize=9.2,
                               leading=13.2, textColor=_NAVY, spaceAfter=7,
                               splitLongWords=True, allowWidows=1, allowOrphans=1),
        "domain": ParagraphStyle("CompetencyDomain", fontName=_BOLD, fontSize=8,
                                 leading=10.5, textColor=_NAVY),
        "code": ParagraphStyle("CompetencyCode", fontName=_BOLD, fontSize=8,
                               leading=10, textColor=_MUTED, alignment=1),
        "score": ParagraphStyle("SourceCompetencyScore", fontName=_REGULAR,
                                fontSize=10.2, leading=14, textColor=_NAVY, alignment=1),
    }


def _score_table(scores: list[dict], width: float, styles: dict) -> Table:
    if not scores:
        raise ValueError("No competency recommendations are available for this report.")
    seen = set()
    groups: OrderedDict[str, list[dict]] = OrderedDict((prefix, []) for prefix in _DOMAINS)
    extra_names = {}
    for row in scores:
        code = str(row.get("code") or row.get("label") or "").strip()
        score = row.get("score")
        if not code or not _nonempty(score):
            raise ValueError("Each competency needs a code and its source score or range.")
        if code in seen:
            raise ValueError(f"The report has duplicate recommendations for {code}.")
        seen.add(code)
        match = re.fullmatch(r"([A-Z]+)(\d+)", code)
        group = match.group(1) if match else "OTHER"
        if group not in groups:
            groups[group] = []
            extra_names[group] = str(row.get("domain") or "Other recommendations")
        groups[group].append({"code": code, "score": str(score)})
    rows = []
    # Six cells fit the largest domain in the current APPD report (SBP1-SBP6).
    count = 6
    domain_width = 146
    cell_width = (width - domain_width) / count
    for group, values in groups.items():
        if not values:
            continue
        values.sort(key=lambda item: int(re.search(r"\d+$", item["code"]).group())
                    if re.search(r"\d+$", item["code"]) else 0)
        for offset in range(0, len(values), count):
            domain = _DOMAINS.get(group, extra_names.get(group, group))
            if offset:
                domain += " (continued)"
            cells = [Paragraph(_text(domain), styles["domain"])]
            for value in values[offset:offset + count]:
                cells.append([
                    Paragraph(_text(value["code"]), styles["code"]),
                    Paragraph(_text(value["score"]), styles["score"]),
                ])
            cells.extend([""] * (count + 1 - len(cells)))
            rows.append(cells)
    table = Table(rows, colWidths=[domain_width] + [cell_width] * count,
                  hAlign="LEFT", splitByRow=True)
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [_PALE, colors.white]),
        ("LINEBELOW", (0, 0), (-1, -1), 0.45, _LINE),
        ("LEFTPADDING", (0, 0), (0, -1), 8),
        ("RIGHTPADDING", (0, 0), (0, -1), 7),
        ("LEFTPADDING", (1, 0), (-1, -1), 3),
        ("RIGHTPADDING", (1, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return table


def _entries(evaluation: dict, field: str) -> list[dict]:
    return [entry for entry in evaluation.get(field, [])
            if isinstance(entry, dict) and _nonempty(entry.get("text"))]


def _has_feedback(evaluation: dict) -> bool:
    return bool(_entries(evaluation, "feedback") or _entries(evaluation, "resident_comments"))


def _evaluation_story(evaluation: dict, styles: dict, *, undated: bool = False) -> list:
    feedback = _entries(evaluation, "feedback")
    resident_comments = _entries(evaluation, "resident_comments")
    if not feedback and not resident_comments:
        return []
    story = [CondPageBreak(68)]
    day = str(evaluation.get("date") or "Date unavailable")
    form = str(evaluation.get("form_name") or "Unrecognized form")
    story.append(Paragraph(_text(day + " | " + form), styles["evaluation"]))
    metadata = []
    evaluator = str(evaluation.get("evaluator") or "").strip()
    status = str(evaluation.get("evaluator_status") or "").strip()
    if evaluator:
        metadata.append("Evaluator: " + evaluator + (" (" + status + ")" if status else ""))
    elif status:
        metadata.append("Evaluator role: " + status)
    if undated:
        metadata.append("Rotation: " + str(evaluation.get("rotation") or "Rotation not provided"))
    if evaluation.get("date_basis") == "session_date":
        metadata.append("Session date used because completion date is unavailable")
    if metadata:
        story.append(Paragraph(_text(" | ".join(metadata)), styles["eval_meta"]))
    sources = [str(item) for item in evaluation.get("source_files", []) if _nonempty(item)]
    provenance = []
    if sources:
        provenance.append("Source: " + ", ".join(sources))
    record_id = str(evaluation.get("id") or "").strip()
    if record_id:
        provenance.append("Evaluation reference: " + record_id)
    if provenance:
        story.append(Paragraph(_text(" | ".join(provenance)), styles["eval_meta"]))
    # Keep provenance and review notes with their evaluation's first comment.
    # Putting them after a short final comment could leave a reference alone on
    # a new page. The comment itself remains a split-capable Paragraph.
    for note in evaluation.get("notes", []):
        if note:
            story.append(Paragraph(_text(note), styles["eval_meta"]))
    for entry in feedback:
        kind = entry.get("kind")
        if kind == "overall_comments":
            label = "Overall comments"
        else:
            question = str(entry.get("question") or "").strip()
            order = entry.get("question_order")
            qid = entry.get("question_id")
            prefix = f"Question {order}" if order is not None else (f"Question ID {qid}" if qid else "Question")
            label = prefix + (": " + question if question and question.casefold() != prefix.casefold() else "")
            if kind == "written_response":
                label += " (written response)"
        story.append(Paragraph(_text(label), styles["prompt"]))
        story.append(Paragraph(_text(entry["text"]), styles["body"]))
    for entry in resident_comments:
        story.append(Paragraph("Resident comments", styles["prompt"]))
        story.append(Paragraph(_text(entry["text"]), styles["body"]))
    story.append(Spacer(1, 4))
    return story


def build_competency_comments_pdf(report: dict, comments: dict) -> bytes:
    """Return a printable resident report without changing source data.

    Source recommendation strings, including ranges and zero, are printed
    unchanged. Dated comments follow by rotation. Comments with no usable date
    have their own final section and are never presented as inside the period.
    """
    resident = str(report.get("linked_resident") or report.get("resident") or "").strip()
    if not resident:
        raise ValueError("Select a resident before preparing the combined report.")
    if report.get("resident_key") and comments.get("resident_key") and report["resident_key"] != comments["resident_key"]:
        raise ValueError("The comments belong to a different resident. Select the matching resident before downloading.")
    for report_field, comment_field in (("comment_start", "start_date"), ("comment_end", "end_date")):
        if report.get(report_field) and comments.get(comment_field) and str(report[report_field]) != str(comments[comment_field]):
            raise ValueError("The comment dates changed. Refresh the combined report before downloading.")
    _register_fonts()
    styles = _styles()
    page_width, page_height = letter
    margin = 40
    width = page_width - 2 * margin
    header = Paragraph("EPA TRACKER | " + _text(resident), styles["meta"])
    header_height = header.wrap(width, page_height)[1]
    if header_height > 80:
        raise ValueError("The resident name is too long for a printable report heading.")
    frame_top = page_height - margin - header_height - 13
    footer_top = 43
    frame = Frame(margin, footer_top, width, frame_top - footer_top,
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)

    def draw_page(canvas, doc):
        canvas.saveState()
        header.drawOn(canvas, margin, page_height - margin - header_height)
        canvas.setStrokeColor(_LINE)
        canvas.setLineWidth(0.5)
        canvas.line(margin, frame_top + 7, page_width - margin, frame_top + 7)
        canvas.line(margin, 33, page_width - margin, 33)
        canvas.setFont(_REGULAR, 7)
        canvas.setFillColor(_MUTED)
        canvas.drawString(margin, 21, "EPA Tracker | Competency recommendations and original evaluation comments")
        canvas.drawRightString(page_width - margin, 21, f"Page {doc.page}")
        canvas.restoreState()

    output = io.BytesIO()
    document = BaseDocTemplate(output, pagesize=letter, leftMargin=margin,
                               rightMargin=margin, topMargin=margin,
                               bottomMargin=footer_top, allowSplitting=1,
                               title="Competency scores and rotation comments",
                               author="EPA Tracker", pageCompression=1)
    document.addPageTemplates([PageTemplate(id="competency_comments", frames=[frame], onPage=draw_page)])
    story = [
        Paragraph("Competency scores and rotation comments", styles["title"]),
        Paragraph(_text(resident), styles["resident"]),
    ]
    report_meta = [str(report.get("program") or "").strip()]
    season = str(report.get("season") or "").strip()
    pgy = report.get("pgy")
    if season:
        report_meta.append(season)
    if _nonempty(pgy):
        report_meta.append("PGY " + str(pgy))
    if any(report_meta):
        story.append(Paragraph(_text(" | ".join(x for x in report_meta if x)), styles["meta"]))
    source_resident = str(report.get("resident") or "").strip()
    if source_resident and source_resident != resident:
        story.append(Paragraph("Name on source report: " + _text(source_resident), styles["meta"]))
    if report.get("source_filename"):
        story.append(Paragraph("Source report: " + _text(report["source_filename"]), styles["meta"]))
    start = report.get("comment_start") or comments.get("start_date")
    end = report.get("comment_end") or comments.get("end_date")
    if start and end:
        period = f"Comment dates: {start} to {end}, inclusive"
    elif start:
        period = f"Comment dates: {start} onward"
    elif end:
        period = f"Comment dates: through {end}"
    else:
        period = "Comment dates: all available dates"
    story.append(Paragraph(_text(period), styles["meta"]))
    story.append(Paragraph("APPD recommendations as reported", styles["section"]))
    story.append(_score_table(report.get("scores", []), width, styles))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        "Scores and ranges are copied from the uploaded report. Ranges are not averaged or converted. "
        "Zeros remain as reported. These recommendations are not final CCC decisions.", styles["note"]))
    for note in report.get("warnings", []):
        if note:
            story.append(Paragraph(_text(note), styles["note"]))
    story.append(Paragraph("Comments by rotation", styles["section"]))
    story.append(Paragraph(
        "Original comments from saved evaluations. Completion date determines the selected period. "
        "A labeled session date is used when completion date is unavailable.", styles["note"]))
    rendered = 0
    for rotation in comments.get("rotations", []):
        evaluations = [evaluation for evaluation in rotation.get("evaluations", []) if _has_feedback(evaluation)]
        if not evaluations:
            continue
        story.extend([CondPageBreak(100), Paragraph(
            _text(rotation.get("rotation") or "Rotation not provided"), styles["rotation"])])
        for evaluation in evaluations:
            story.extend(_evaluation_story(evaluation, styles))
            rendered += 1
    if not rendered:
        story.append(Paragraph("No dated evaluation comments are available for this resident in the selected period.",
                               styles["body"]))
    undated = [evaluation for evaluation in comments.get("undated", []) if _has_feedback(evaluation)]
    if undated:
        story.extend([CondPageBreak(100), Paragraph("Comments with no usable date", styles["section"]),
                      Paragraph("These comments cannot be assigned to the selected period. They are shown separately for review.",
                                styles["note"])])
        for evaluation in undated:
            story.extend(_evaluation_story(evaluation, styles, undated=True))
    notes = [note for note in comments.get("notes", []) if note]
    if notes:
        story.append(Paragraph("Comment coverage", styles["section"]))
        for note in notes:
            story.append(Paragraph(_text(note), styles["note"]))
    document.build(story)
    return output.getvalue()
