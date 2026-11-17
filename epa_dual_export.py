"""Produce a raw numeric companion to the validated categorical workbook.

This retains the supplied one-sheet, 18-column template layout. Raw decimals
are NOT claimed to satisfy the Data Hub category-code import contract. This
module is pure file formatting: approval and concurrent-save checks are owned
by epa_workflow. No source evaluation or saved mapping is changed here.
"""
from __future__ import annotations

import copy
import io
import math
import zipfile
from xml.etree import ElementTree as ET

S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
EPA_COLUMNS = tuple(f"EPA {number}" for number in range(1, 13))


def _inline_text(cell: ET.Element | None) -> str:
    if cell is None:
        return ""
    return "".join(node.text or "" for node in cell.iter(S + "t"))


def raw_from_ratings(ratings: bytes, numeric_rows: list[dict]) -> bytes:
    """Use the same approved rows, keeping full precision and true blank cells.

    An explicit numeric zero stays zero, never blank. Visible decimal formatting
    is two places, but the actual underlying float value is not rounded. Text
    identifiers/dates remain as safe literal strings from the validated writer.
    """
    with zipfile.ZipFile(io.BytesIO(ratings)) as source:
        parts = {name: source.read(name) for name in source.namelist()}
    sheet = ET.fromstring(parts["xl/worksheets/sheet1.xml"])
    data = sheet.find(S + "sheetData")
    if data is None:
        raise ValueError("The export template has no worksheet data.")
    rows = [row for row in data if int(row.get("r", "0")) >= 3]
    included = [row for row in numeric_rows if row.get("Include")]
    if len(rows) != len(included):
        raise ValueError("The raw and CBME exports have different resident counts. Review again.")

    # Reuse the template style system, adding only a numeric cell format.
    styles = ET.fromstring(parts["xl/styles.xml"])
    xfs = styles.find(S + "cellXfs")
    if xfs is None or not len(xfs):
        raise ValueError("The export template is missing its cell styles.")
    numeric_style = copy.deepcopy(xfs[0])
    numeric_style.set("numFmtId", "2")  # built-in 0.00, display only
    numeric_style.set("applyNumberFormat", "1")
    style_id = str(len(xfs))
    xfs.append(numeric_style)
    xfs.set("count", str(len(xfs)))

    for xml_row, source in zip(rows, included):
        number = xml_row.get("r")
        if _inline_text(xml_row.find(f"{S}c[@r='C{number}']")) != str(source.get("name_or_program_assigned_ID", "")):
            raise ValueError("The raw and CBME resident order differs. Review again.")
        for column, epa in enumerate(EPA_COLUMNS, start=6):
            ref = f"{chr(65 + column)}{number}"
            cell = xml_row.find(f"{S}c[@r='{ref}']")
            if cell is None:
                cell = ET.SubElement(xml_row, S + "c", {"r": ref})
            cell.clear()
            cell.attrib.update(r=ref, s=style_id)
            value = source.get(epa)
            if value is None:
                continue
            if isinstance(value, bool):
                raise ValueError(f"{epa}: a numeric average is required.")
            try:
                value = float(value)
            except (ValueError, TypeError):
                raise ValueError(f"{epa}: a numeric average is required.") from None
            if not math.isfinite(value):
                raise ValueError(f"{epa}: averages must be finite.")
            ET.SubElement(cell, S + "v").text = repr(value)

    # Row 1 is ignored by the importer. Replace the merged category legend with
    # an explicit raw-file notice, without moving headers or changing the layout.
    legend = data.find(f"{S}row[@r='1']/{S}c[@r='G1']")
    if legend is not None:
        old_style = legend.get("s", "0")
        legend.clear()
        legend.attrib.update(r="G1", s=old_style, t="inlineStr")
        inline = ET.SubElement(legend, S + "is")
        ET.SubElement(inline, S + "t").text = (
            "RAW AVERAGES (unconverted). Full numeric precision retained. "
            "Blank = no scored value. Decimal import acceptance is not confirmed. "
            "Use the separate CBME ratings file for Data Hub category-code upload."
        )

    # Categorical dropdowns would incorrectly flag raw values like 3.77.
    # Clear them in this companion only. The ratings workbook keeps its original
    # category dropdown and valid code checks.
    validations = sheet.find(S + "dataValidations")
    if validations is not None:
        sheet.remove(validations)
    ET.register_namespace("", S[1:-1])
    ET.register_namespace("r", "http://schemas.openxmlformats.org/officeDocument/2006/relationships")
    parts["xl/worksheets/sheet1.xml"] = ET.tostring(sheet, encoding="utf-8", xml_declaration=True)
    parts["xl/styles.xml"] = ET.tostring(styles, encoding="utf-8", xml_declaration=True)
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for name, content in parts.items():
            target.writestr(name, content)
    return result.getvalue()
