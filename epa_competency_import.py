"""Read the recommendations table in an APPD General Pediatrics report.

The values are imported as printed text. A range is never rounded, averaged,
or converted into an EPA rating. Source PDFs are parsed locally with pypdf.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from hashlib import sha256
from io import BytesIO
from pathlib import PurePath
import re
import unicodedata


MAX_PDF_BYTES = 15 * 1024 * 1024
MAX_PDF_PAGES = 20
MAX_FIRST_PAGE_TEXT = 250_000

_COMPETENCIES = (
    ("PC1", "History", "Patient Care"),
    ("PC2", "Physical examination", "Patient Care"),
    ("PC3", "Organization and prioritization", "Patient Care"),
    ("PC4", "Clinical reasoning", "Patient Care"),
    ("PC5", "Patient management", "Patient Care"),
    ("MK1", "Clinical knowledge", "Medical Knowledge"),
    ("MK2", "Diagnostic evaluation", "Medical Knowledge"),
    ("SBP1", "Patient safety", "Systems-Based Practice"),
    ("SBP2", "Quality improvement", "Systems-Based Practice"),
    ("SBP3", "Coordination of care", "Systems-Based Practice"),
    ("SBP4", "Transitions in care", "Systems-Based Practice"),
    ("SBP5", "Population and community health", "Systems-Based Practice"),
    ("SBP6", "Role in the health system", "Systems-Based Practice"),
    ("PBLI1", "Evidence-based practice", "Practice-Based Learning and Improvement"),
    ("PBLI2", "Reflective practice", "Practice-Based Learning and Improvement"),
    ("PROF1", "Professional behavior", "Professionalism"),
    ("PROF2", "Ethical principles", "Professionalism"),
    ("PROF3", "Accountability", "Professionalism"),
    ("PROF4", "Well-being", "Professionalism"),
    ("ICS1", "Patient and family communication", "Interpersonal and Communication Skills"),
    ("ICS2", "Interprofessional and team communication", "Interpersonal and Communication Skills"),
    ("ICS3", "Communication within health systems", "Interpersonal and Communication Skills"),
)
COMPETENCY_CODES = tuple(item[0] for item in _COMPETENCIES)
EXPECTED_CODES = COMPETENCY_CODES

_TABLE_ANCHOR = re.compile(
    r"\(\s*Here\s+are\s+these\s+recommendations\s+in\s+table\s+form\s*\)",
    re.IGNORECASE,
)
_CODE = re.compile(r"(PBLI|PROF|SBP|ICS|PC|MK)\s*(\d+)\b", re.IGNORECASE)
_SCORE = re.compile(r"(\d+(?:\.\d+)?)(?:\s*-\s*(\d+(?:\.\d+)?))?")


def _normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).replace("\u00ad", "")
    return text.translate(str.maketrans({
        "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-",
        "\u2014": "-", "\u2212": "-", "\r": "\n",
    }))


def _compact(text: str) -> str:
    return " ".join(text.split())


def _metadata(text: str) -> tuple[dict, list[str]]:
    """Use the printed header, never a filename, to identify the learner."""
    matches = list(re.finditer(
        r"milestone\s+levels\s+for\s+(.{1,240}?)"
        r"\(\s*([^()]{1,40}?)\s+PGY\s*(\d{1,2})\s*\)",
        text, re.IGNORECASE | re.DOTALL,
    ))
    if len(matches) != 1:
        raise ValueError(
            "The PDF must identify one resident with a reporting season and PGY "
            "in its milestone report header. Upload the original individual "
            "General Pediatrics crosswalk PDF. The filename is not used to guess the resident."
        )
    match = matches[0]
    resident = _compact(match[1])
    if not any(character.isalpha() for character in resident):
        raise ValueError("The resident name in the PDF header could not be read. Upload the original downloadable PDF.")
    pgy = int(match[3])
    if not 1 <= pgy <= 20:
        raise ValueError("The PGY in the PDF header is invalid. Check the original individual report.")
    # Some PDF text extractors put a space inside FALL, for example 'F ALL'.
    raw_season = _compact(match[2])
    season = re.sub(r"\s+", "", raw_season).upper()
    warnings = []
    if season not in {"FALL", "SPRING", "SUMMER", "WINTER"}:
        season = raw_season
        warnings.append("The reporting season needs review because it is not a recognized season name.")
    program_match = re.search(
        r"Based\s+on\s+the\s+EP\s*As\s+reported\s+by\s+(.{1,240}?),\s*these\s+are",
        text[:match.start()], re.IGNORECASE | re.DOTALL,
    )
    program = _compact(program_match[1]) if program_match else ""
    if not program:
        warnings.append("The program name could not be read from the PDF header.")
    warnings.append(
        "This import does not infer an academic year or comment period from the resident's PGY. "
        "Choose the comment period when linking the report."
    )
    return {"resident": resident, "program": program, "season": season, "pgy": pgy}, warnings


def _scores_from_row(row: str, expected_count: int, first_code: str) -> list[str]:
    values = []
    pos = 0
    row = row.strip()
    while pos < len(row):
        match = _SCORE.match(row, pos)
        if match is None:
            raise ValueError(
                f"The recommendation row beginning with {first_code} contains an unreadable "
                "or invalid score. Upload the original downloadable PDF and check its table."
            )
        try:
            low = Decimal(match[1])
            high = Decimal(match[2]) if match[2] is not None else low
        except InvalidOperation as exc:
            raise ValueError(f"The recommendation for {first_code} is not a valid number.") from exc
        if not (Decimal(0) <= low <= high <= Decimal(5)):
            raise ValueError(
                f"The recommendation row beginning with {first_code} contains a score outside "
                "0 to 5 or a range with its endpoints reversed. Check the source report."
            )
        values.append(match[1] + (" - " + match[2] if match[2] is not None else ""))
        pos = match.end()
        if pos < len(row) and not row[pos].isspace():
            raise ValueError(f"The recommendation row beginning with {first_code} has ambiguous score spacing.")
        while pos < len(row) and row[pos].isspace():
            pos += 1
    if len(values) != expected_count:
        raise ValueError(
            f"The recommendation row beginning with {first_code} has {expected_count} labels "
            f"but {len(values)} readable scores. No scores were shifted or filled in. "
            "Upload the original downloadable PDF and check its table."
        )
    return values


def _parse_recommendations(text: str, resident: str) -> list[dict]:
    anchors = list(_TABLE_ANCHOR.finditer(text))
    if len(anchors) != 1:
        raise ValueError(
            "The page-one milestone recommendations table could not be identified uniquely. "
            "Upload the original General Pediatrics crosswalk PDF, including its first page."
        )
    table = text[anchors[0].end():]
    table = re.sub(r"\s*\b\d+\s+of\s+\d+\s*$", "", table, flags=re.IGNORECASE)
    blocks = re.split(r"(?mi)^\s*Learner\b\s*", table)
    if len(blocks) < 2 or blocks[0].strip():
        raise ValueError("The milestone table layout is not recognized. Upload the original downloadable PDF.")
    found = {}
    resident_pattern = re.compile(r"\s+".join(re.escape(word) for word in resident.split()) + r"(?=\s|$)", re.IGNORECASE)
    for block in blocks[1:]:
        remaining = block.lstrip()
        codes = []
        while match := _CODE.match(remaining):
            code = match[1].upper() + str(int(match[2]))
            if code not in COMPETENCY_CODES:
                raise ValueError(f"The milestone table contains an unsupported competency label: {code}.")
            if code in found or code in codes:
                raise ValueError(f"The milestone table repeats {code}. Upload one individual report with one set of recommendations.")
            codes.append(code)
            remaining = remaining[match.end():].lstrip()
        if not codes:
            raise ValueError("One milestone table header has no readable competency labels. Check the source PDF.")
        name_match = resident_pattern.match(remaining)
        if name_match is None:
            raise ValueError(
                f"The resident printed in the table beginning with {codes[0]} does not match "
                "the report header, or the row could not be read. No resident match was guessed."
            )
        values = _scores_from_row(remaining[name_match.end():], len(codes), codes[0])
        found.update(zip(codes, values))
    missing = [code for code in COMPETENCY_CODES if code not in found]
    if missing:
        raise ValueError(
            "The PDF is missing readable recommendations for " + ", ".join(missing) + ". "
            "Upload the complete original report. Missing scores are not treated as zero."
        )
    return [
        {"code": code, "label": label, "score": found[code], "domain": domain}
        for code, label, domain in _COMPETENCIES
    ]


def parse_competency_pdf(data: bytes, filename: str = "") -> dict:
    """Return the printed page-one recommendations with source identity.

    Requires one original, text-based General Pediatrics crosswalk report.
    Files with incomplete or ambiguous tables raise ValueError. Page-three EPA
    inputs are deliberately excluded. No filename or PGY is used to infer dates.
    """
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise ValueError("Choose a non-empty PDF report to upload.")
    if len(data) > MAX_PDF_BYTES:
        raise ValueError("This PDF is larger than 15 MB. Upload one resident's original report at a time.")
    data = bytes(data)
    if not data.lstrip().startswith(b"%PDF-"):
        raise ValueError("This file is not a readable PDF. Upload the original downloadable report.")
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ValueError("PDF import is unavailable until pypdf is installed from the app's updated requirements.txt.") from exc
    try:
        reader = PdfReader(BytesIO(data))
        encrypted = reader.is_encrypted
    except Exception as exc:
        raise ValueError("The PDF could not be read. Download the original report again and upload that copy.") from exc
    if encrypted:
        raise ValueError("This PDF is password protected. Upload an unlocked copy of the individual report.")
    try:
        page_count = len(reader.pages)
    except Exception as exc:
        raise ValueError("The PDF could not be read. Download the original report again and upload that copy.") from exc
    if not 1 <= page_count <= MAX_PDF_PAGES:
        raise ValueError("Upload one individual report with between 1 and 20 pages.")
    try:
        text = reader.pages[0].extract_text() or ""
    except Exception as exc:
        raise ValueError("The PDF text could not be read. Download the original report again and upload that copy.") from exc
    if len(text) > MAX_FIRST_PAGE_TEXT:
        raise ValueError("The PDF's first page is too large to read safely. Upload the original individual report.")
    if not text.strip():
        raise ValueError(
            "This PDF has no readable text on its first page. It may be a scan or photo. "
            "Upload the original downloadable PDF instead of a scanned printout."
        )
    text = _normalize_text(text)
    if not re.search(r"General\s+Pediatrics\s+EP\s*As\s+and\s+Milestones\s+Crosswalk", text, re.IGNORECASE):
        raise ValueError(
            "This PDF is not a recognized General Pediatrics EPA and Milestones crosswalk report. "
            "Upload the original individual crosswalk report."
        )
    metadata, warnings = _metadata(text)
    # Strip both path conventions so a client-supplied name is only a label.
    source_filename = PurePath(str(filename).replace("\\", "/")).name if filename else "report.pdf"
    return {
        "source_sha256": sha256(data).hexdigest(),
        "source_filename": source_filename,
        **metadata,
        "scores": _parse_recommendations(text, metadata["resident"]),
        "warnings": warnings,
    }
