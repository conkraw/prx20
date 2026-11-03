"""Print question averages without losing the unscored questions on a form.

This module is independent of Streamlit and has no storage side effects.
The caller supplies the same filtered response rows used by the dashboard.
"""

from __future__ import annotations

import io
import math
import threading
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

import pandas as pd
import reportlab
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    LongTable,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    TableStyle,
)


_FONT_LOCK = threading.Lock()
_REGULAR = "EPAReportVera"
_BOLD = "EPAReportVeraBold"
_NAVY = colors.HexColor("#183C50")
_TEAL = colors.HexColor("#176B72")
_MUTED = colors.HexColor("#526772")
_LINE = colors.HexColor("#D7E1E5")
_PALE = colors.HexColor("#F3F7F8")


def pdf_question_sections(rows: pd.DataFrame, forms: dict) -> list[dict]:
    """Build one section per form and resident, retaining every form question.

    Numeric fields are None when they are not eligible for display. Their
    corresponding text fields are literal empty strings, never zeros or N/A.
    """
    if rows.empty:
        return []
    required = {
        "form_id", "resident_key", "resident", "record_id", "question_id",
        "included", "status", "score",
    }
    if not required.issubset(rows.columns):
        raise ValueError("The PDF needs the dashboard's filtered response rows.")

    sections = []
    for (form_id, resident_key), group in rows.groupby(
        ["form_id", "resident_key"], sort=False, dropna=False
    ):
        form = forms.get(form_id)
        if form is None:
            continue
        evaluation_count = int(group["record_id"].dropna().nunique())
        if evaluation_count == 0:
            continue
        questions = []
        for number, question in enumerate(form.get("questions", []), start=1):
            question_id = str(question["id"])
            average = None
            count = None
            if form.get("scoring_confirmed") is True and question.get("include") is True:
                eligible = group.loc[
                    group["question_id"].astype(str).eq(question_id)
                    & group["included"].eq(True).fillna(False)
                    & group["status"].eq("scored")
                ]
                scores = pd.to_numeric(eligible["score"], errors="coerce")
                scores = scores.loc[scores.map(lambda value: pd.notna(value) and math.isfinite(float(value)))]
                if len(scores):
                    average = float(scores.mean())
                    count = int(len(scores))
            questions.append({
                "number": number,
                "question_id": question_id,
                "question": str(question.get("text") or f"Question {question_id}"),
                "average": average,
                "n": count,
                "average_text": "" if average is None else f"{average:.2f}",
                "n_text": "" if count is None else str(count),
            })
        sections.append({
            "form_id": form_id,
            "epa": str(form.get("name") or "EPA form"),
            "resident_key": str(resident_key),
            "resident": str(group["resident"].iloc[0]),
            "n_evaluations": evaluation_count,
            "questions": questions,
        })
    return sorted(sections, key=lambda section: (
        section["epa"].casefold(), str(section["form_id"]), section["resident_key"].casefold()
    ))


def _register_fonts() -> None:
    """Use fonts shipped with ReportLab, including on Streamlit Cloud."""
    with _FONT_LOCK:
        names = set(pdfmetrics.getRegisteredFontNames())
        directory = Path(reportlab.__file__).resolve().parent / "fonts"
        if _REGULAR not in names:
            pdfmetrics.registerFont(TTFont(_REGULAR, str(directory / "Vera.ttf")))
        if _BOLD not in names:
            pdfmetrics.registerFont(TTFont(_BOLD, str(directory / "VeraBd.ttf")))


def _paragraph_text(value: object) -> str:
    text = "" if value is None else str(value)
    # Leave source words intact while removing controls that are invalid XML.
    text = "".join(character for character in text if ord(character) >= 32 or character in "\n\t")
    text = text.translate(str.maketrans({"\u2011": "-", "\u2013": "-", "\u2014": "-"}))
    return escape(text).replace("\n", "<br/>")


def _generated_label(value: object) -> str:
    if not value:
        return ""
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if moment.tzinfo is not None:
            moment = moment.astimezone(ZoneInfo("America/New_York"))
        return moment.strftime("%b %d, %Y")
    except (ValueError, TypeError):
        return ""


def build_question_pdf(rows: pd.DataFrame, forms: dict, context: dict) -> bytes:
    """Render the filtered averages as a portrait PDF grouped by EPA form.

    Each resident/form pair starts on a new page. Context, table headings, and
    page numbers repeat when a form needs more than one page. Long question
    paragraphs can split across pages instead of being clipped or discarded.
    """
    sections = pdf_question_sections(rows, forms)
    if not sections:
        raise ValueError("No recognized evaluations match the selected report filters.")
    _register_fonts()
    page_width, page_height = letter
    margin = 0.55 * 72
    width = page_width - 2 * margin
    footer_top = 55
    styles = {
        "kicker": ParagraphStyle("EPAKicker", fontName=_BOLD, fontSize=8, leading=10, textColor=_TEAL),
        "title": ParagraphStyle("EPATitle", fontName=_BOLD, fontSize=16, leading=20, textColor=_NAVY),
        "resident": ParagraphStyle("EPAResident", fontName=_BOLD, fontSize=11, leading=14, textColor=_NAVY),
        "meta": ParagraphStyle("EPAMeta", fontName=_REGULAR, fontSize=8.4, leading=11, textColor=_MUTED),
        "note": ParagraphStyle("EPANote", fontName=_REGULAR, fontSize=8, leading=10.5, textColor=_MUTED),
        "body": ParagraphStyle("EPABody", fontName=_REGULAR, fontSize=9.2, leading=12.1, textColor=_NAVY,
                               splitLongWords=True, allowWidows=1, allowOrphans=1),
        "number": ParagraphStyle("EPANumber", fontName=_REGULAR, fontSize=9.2, leading=12.1, textColor=_MUTED),
        "score": ParagraphStyle("EPAScore", fontName=_REGULAR, fontSize=9.2, leading=12.1, textColor=_NAVY, alignment=TA_RIGHT),
        "table_header": ParagraphStyle("EPATableHeader", fontName=_BOLD, fontSize=8.5, leading=11, textColor=colors.white),
        "table_header_number": ParagraphStyle("EPATableHeaderNumber", fontName=_BOLD, fontSize=8.5, leading=11,
                                              textColor=colors.white, alignment=TA_RIGHT),
    }
    buffer = io.BytesIO()
    document = BaseDocTemplate(
        buffer, pagesize=letter, leftMargin=margin, rightMargin=margin,
        topMargin=margin, bottomMargin=footer_top, allowSplitting=1,
        title="EPA question averages", author="EPA Tracker",
        subject="Resident EPA question averages", pageCompression=1,
    )
    templates = []
    story = []
    generated = _generated_label(context.get("generated_at"))
    warnings = context.get("warnings") or []
    if isinstance(warnings, str):
        warnings = [warnings]

    for section_index, section in enumerate(sections):
        header = [
            (Paragraph("EPA QUESTION AVERAGES", styles["kicker"]), 5),
            (Paragraph(_paragraph_text(section["epa"]), styles["title"]), 4),
            (Paragraph("Resident: " + _paragraph_text(section["resident"]), styles["resident"]), 5),
        ]
        academic_year = str(context.get("academic_year_label") or "Selected period")
        basis = str(context.get("date_basis_label") or "Session month")
        header.append((Paragraph(_paragraph_text(f"Academic year: {academic_year}   |   {basis}"), styles["meta"]), 2))
        months = str(context.get("months_label") or "All months")
        header.append((Paragraph("Months: " + _paragraph_text(months), styles["meta"]), 2))
        details = f"Evaluations: {section['n_evaluations']}"
        if generated:
            details += f"   |   Generated: {generated}"
        header.append((Paragraph(_paragraph_text(details), styles["meta"]), 0))
        for warning in warnings:
            if warning:
                header.append((Paragraph(_paragraph_text(warning), styles["note"]), 2))
        heights = [paragraph.wrap(width, page_height)[1] + gap for paragraph, gap in header]
        header_height = sum(heights)
        frame_top = page_height - margin - header_height - 16
        frame_height = frame_top - footer_top
        if frame_height < 140:
            raise ValueError("The report heading is too long to fit. Shorten the EPA name or report notes.")
        frame = Frame(margin, footer_top, width, frame_height,
                      leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
                      id=f"epa_frame_{section_index}")

        def draw_page(canvas, doc, header=header, heights=heights, frame_top=frame_top):
            canvas.saveState()
            y = page_height - margin
            for (paragraph, gap), height in zip(header, heights):
                paragraph_height = height - gap
                y -= paragraph_height
                paragraph.drawOn(canvas, margin, y)
                y -= gap
            canvas.setStrokeColor(_LINE)
            canvas.setLineWidth(0.5)
            canvas.line(margin, frame_top + 8, page_width - margin, frame_top + 8)
            canvas.setFont(_REGULAR, 7.2)
            canvas.setFillColor(_MUTED)
            canvas.drawString(margin, 40, "Blank = not selected for scoring or no scored ratings. n = scored ratings.")
            canvas.setStrokeColor(_LINE)
            canvas.line(margin, 31, page_width - margin, 31)
            canvas.setFont(_REGULAR, 7)
            canvas.drawString(margin, 20, "EPA Tracker")
            canvas.drawRightString(page_width - margin, 20, f"Page {doc.page}")
            canvas.restoreState()

        template_name = f"epa_section_{section_index}"
        templates.append(PageTemplate(id=template_name, frames=[frame], onPage=draw_page))
        if section_index:
            story.extend([NextPageTemplate(template_name), PageBreak()])
        table_rows = [[
            Paragraph("#", styles["table_header"]),
            Paragraph("Question", styles["table_header"]),
            Paragraph("Average", styles["table_header_number"]),
            Paragraph("n", styles["table_header_number"]),
        ]]
        for question in section["questions"]:
            table_rows.append([
                Paragraph(str(question["number"]), styles["number"]),
                Paragraph(_paragraph_text(question["question"]), styles["body"]),
                Paragraph(question["average_text"], styles["score"]),
                Paragraph(question["n_text"], styles["score"]),
            ])
        table = LongTable(
            table_rows, colWidths=[36, width - 36 - 58 - 27, 58, 27],
            repeatRows=1, splitByRow=0, splitInRow=1, hAlign="LEFT",
        )
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), _NAVY),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _PALE]),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 7),
            ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ("TOPPADDING", (0, 0), (-1, 0), 7),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 7),
            ("TOPPADDING", (0, 1), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 1), (-1, -1), 5),
            ("LINEBELOW", (0, 0), (-1, 0), 0.5, _NAVY),
            ("LINEBELOW", (0, 1), (-1, -1), 0.35, _LINE),
        ]))
        story.append(table)
    document.addPageTemplates(templates)
    document.build(story)
    return buffer.getvalue()
