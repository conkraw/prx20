"""Original synthetic PDF fixtures for the competency report importer."""
from hashlib import sha256
from io import BytesIO

import pytest
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas

import epa_competency_import as importer
from epa_competency_import import EXPECTED_CODES, parse_competency_pdf


def report_text():
    rows = [
        ["PC 1", "PC 2", "PC 3", "PC 4", "PC 5", "MK 1"],
        ["MK 2", "SBP 1", "SBP 2", "SBP 3", "SBP 4", "SBP 5"],
        ["SBP 6", "PBLI 1", "PBLI 2", "PROF 1", "PROF 2", "PROF 3"],
        ["PROF 4", "ICS 1", "ICS 2", "ICS 3"],
    ]
    header = (
        "General Pediatrics EP As and Milestones Crosswalk\n"
        "Based on the EPAs reported by Example Residency, these are the recommended General Pediatrics\n"
        "milestone levels for\n"
        "Taylor Example (F ALL PGY 2)\n"
        "Taylor Example\n"
        "1 2 3 4 5\n"
        "(PC1) History\n"
        "Milestone level recommendation\n"
        "(Here are these recommendations in table form)\n"
    )
    return header + "\n".join(
        "Learner " + " ".join(row) + "\nTaylor\nExample\n" + " ".join(["2 - 2.5"] * len(row))
        for row in rows
    ) + "\n1 of 3\n"


def pdf_bytes(text=None, extra_pages=0):
    result = BytesIO()
    document = canvas.Canvas(result, pagesize=(612, 792), invariant=True)
    if text is not None:
        document.setFont("Helvetica", 8)
        y = 770
        for line in text.splitlines():
            document.drawString(30, y, line)
            y -= 13
    document.showPage()
    for _ in range(extra_pages):
        document.setFont("Helvetica", 9)
        document.drawString(30, 750, "EPAs Reported by Program")
        document.drawString(30, 730, "4 3a 2b 5 4 4 4 4 4 4 4 4")
        document.showPage()
    document.save()
    return result.getvalue()


def test_imports_all_22_recommendations_from_pdf_and_ignores_epa_pages():
    data = pdf_bytes(report_text(), extra_pages=2)
    parsed = parse_competency_pdf(data, "source.pdf")
    assert parsed["resident"] == "Taylor Example"
    assert parsed["program"] == "Example Residency"
    assert parsed["season"] == "FALL"
    assert parsed["pgy"] == 2
    assert parsed["source_sha256"] == sha256(data).hexdigest()
    assert parsed["source_filename"] == "source.pdf"
    assert tuple(row["code"] for row in parsed["scores"]) == EXPECTED_CODES
    assert len(parsed["scores"]) == 22
    assert all(row["score"] == "2 - 2.5" for row in parsed["scores"])
    assert all(row["label"] and row["domain"] for row in parsed["scores"])


def test_preserves_zero_decimals_and_range_endpoints_without_conversion():
    text = report_text().replace(
        "2 - 2.5 2 - 2.5 2 - 2.5 2 - 2.5 2 - 2.5 2 - 2.5",
        "0 1.5 2.0 2 - 3.5 4.5 - 5 5", 1,
    )
    scores = parse_competency_pdf(pdf_bytes(text))["scores"]
    assert [row["score"] for row in scores[:6]] == ["0", "1.5", "2.0", "2 - 3.5", "4.5 - 5", "5"]


def test_whitespace_in_labels_and_table_names_is_supported():
    text = report_text().replace("PC 1 PC 2", "PC1  PC 2").replace("Taylor\nExample", "Taylor   Example")
    parsed = parse_competency_pdf(pdf_bytes(text))
    assert parsed["scores"][0]["code"] == "PC1"
    assert parsed["resident"] == "Taylor Example"


def test_dash_normalization_preserves_range_text(monkeypatch):
    original = PdfReader

    class ReaderWithDash:
        def __init__(self, stream):
            self.real = original(stream)
            self.is_encrypted = False
            raw = self.real.pages[0].extract_text()

            class Page:
                def extract_text(self):
                    return raw.replace("2 - 2.5", "2\u20132.5")

            self.pages = [Page()]

    monkeypatch.setattr("pypdf.PdfReader", ReaderWithDash)
    parsed = parse_competency_pdf(pdf_bytes(report_text()))
    assert all(row["score"] == "2 - 2.5" for row in parsed["scores"])


def test_header_identity_overrides_misleading_filename_and_drops_path():
    parsed = parse_competency_pdf(pdf_bytes(report_text()), "C:\\upload\\Other Person-Spring-PGY4.pdf")
    assert parsed["resident"] == "Taylor Example"
    assert parsed["season"] == "FALL"
    assert parsed["pgy"] == 2
    assert parsed["source_filename"] == "Other Person-Spring-PGY4.pdf"
    assert any("does not infer an academic year" in note for note in parsed["warnings"])


@pytest.mark.parametrize("payload", [b"", None, "not bytes", b"not a PDF"])
def test_rejects_empty_or_non_pdf_input(payload):
    with pytest.raises(ValueError, match="PDF"):
        parse_competency_pdf(payload)


def test_scanned_or_blank_pdf_requests_original_text_pdf():
    with pytest.raises(ValueError, match="scan or photo"):
        parse_competency_pdf(pdf_bytes())


def test_corrupt_pdf_has_actionable_error():
    with pytest.raises(ValueError, match="PDF could not be read"):
        parse_competency_pdf(b"%PDF-1.7\ncorrupt content\n%%EOF")


def test_pdf_library_value_error_is_translated_to_useful_message(monkeypatch):
    def malformed_reader(_stream):
        raise ValueError("unexpected internal object")

    monkeypatch.setattr("pypdf.PdfReader", malformed_reader)
    with pytest.raises(ValueError, match="Download the original report again"):
        parse_competency_pdf(pdf_bytes(report_text()))


def test_encrypted_pdf_requests_unlocked_report():
    output = BytesIO()
    writer = PdfWriter()
    writer.append(PdfReader(BytesIO(pdf_bytes(report_text()))))
    writer.encrypt("example-password")
    writer.write(output)
    with pytest.raises(ValueError, match="password protected"):
        parse_competency_pdf(output.getvalue())


def test_rejects_oversize_upload(monkeypatch):
    data = pdf_bytes(report_text())
    monkeypatch.setattr(importer, "MAX_PDF_BYTES", len(data) - 1)
    with pytest.raises(ValueError, match="larger than 15 MB"):
        parse_competency_pdf(data)


def test_rejects_too_many_pages():
    with pytest.raises(ValueError, match="between 1 and 20"):
        parse_competency_pdf(pdf_bytes(report_text(), extra_pages=20))


def test_rejects_excessive_extracted_text(monkeypatch):
    monkeypatch.setattr(importer, "MAX_FIRST_PAGE_TEXT", 20)
    with pytest.raises(ValueError, match="first page is too large"):
        parse_competency_pdf(pdf_bytes(report_text()))


@pytest.mark.parametrize("bad_value", ["N/A", "6", "-1", "2 - 1.5", "4 - 5.5", "2b", "NaN", "2--2.5"])
def test_rejects_invalid_scores_without_shifting_rows(bad_value):
    text = report_text().replace("2 - 2.5", bad_value, 1)
    with pytest.raises(ValueError, match="recommendation row"):
        parse_competency_pdf(pdf_bytes(text))


def test_rejects_missing_cell_without_using_zero_or_next_row():
    text = report_text().replace("2 - 2.5 ", "", 1)
    with pytest.raises(ValueError, match="6 labels but 5 readable scores"):
        parse_competency_pdf(pdf_bytes(text))


def test_rejects_duplicate_competency_label():
    text = report_text().replace("PC 4 PC 5", "PC 4 PC 4")
    with pytest.raises(ValueError, match="repeats PC4"):
        parse_competency_pdf(pdf_bytes(text))


def test_rejects_unsupported_competency_label():
    text = report_text().replace("SBP 6", "SBP 7")
    with pytest.raises(ValueError, match="unsupported competency label: SBP7"):
        parse_competency_pdf(pdf_bytes(text))


def test_rejects_incomplete_report():
    text = report_text().split("Learner PROF 4")[0]
    with pytest.raises(ValueError, match="missing readable recommendations for PROF4, ICS1, ICS2, ICS3"):
        parse_competency_pdf(pdf_bytes(text))


def test_rejects_table_name_that_does_not_match_header():
    text = report_text().replace("Taylor\nExample", "Different\nResident", 1)
    with pytest.raises(ValueError, match="does not match the report header"):
        parse_competency_pdf(pdf_bytes(text))


def test_rejects_missing_resident_metadata_instead_of_using_filename():
    text = report_text().replace("Taylor Example (F ALL PGY 2)", "")
    with pytest.raises(ValueError, match="filename is not used to guess"):
        parse_competency_pdf(pdf_bytes(text), "Taylor Example FALL PGY2.pdf")


def test_rejects_unrecognized_document_type():
    text = report_text().replace("General Pediatrics EP As and Milestones Crosswalk", "Another assessment report")
    with pytest.raises(ValueError, match="not a recognized General Pediatrics"):
        parse_competency_pdf(pdf_bytes(text))


def test_rejects_multiple_recommendation_tables():
    text = report_text() + report_text()
    with pytest.raises(ValueError, match="one resident"):
        parse_competency_pdf(pdf_bytes(text))


def test_missing_program_is_visible_warning_but_does_not_invent_program():
    text = report_text().replace("Based on the EPAs reported by Example Residency, these are the recommended General Pediatrics", "Recommended General Pediatrics")
    parsed = parse_competency_pdf(pdf_bytes(text))
    assert parsed["program"] == ""
    assert any("program name could not be read" in note for note in parsed["warnings"])


def test_single_score_layout_has_22_scores_not_just_ranges():
    text = report_text().replace("2 - 2.5", "1")
    parsed = parse_competency_pdf(pdf_bytes(text))
    assert len(parsed["scores"]) == 22
    assert all(row["score"] == "1" for row in parsed["scores"])


def test_input_bytes_are_unchanged():
    data = bytearray(pdf_bytes(report_text()))
    previous = bytes(data)
    parse_competency_pdf(data)
    assert data == previous
