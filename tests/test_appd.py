"""APPD regression tests with invented data only. No network or Streamlit required."""
import copy
import csv
import io
import zipfile
from datetime import date
from xml.etree import ElementTree as ET

import pytest

import epa_core as core
import epa_appd as appd


def make_state():
    form = {"id": core.form_id_for_questions(["101", "102"]), "name": "Example form",
            "questions": [{"id": "101", "text": "Example skill A", "include": True, "tags": ""},
                          {"id": "102", "text": "Example skill B", "include": True, "tags": ""}],
            "score_map": {"1": 1., "2": 2., "3": 3., "4": 4., "5": 5., "6": None},
            "scoring_confirmed": True}
    state = core.register_form(core.empty_state(), form)
    record = {"resident": "Example, Resident", "resident_key": "example, resident", "evaluator": "Faculty, Example",
              "evaluator_key": "faculty, example", "evaluator_status": "Faculty", "rotation": "Example",
              "session_date": "2026-07-01", "completed_at": "2026-07-31T10:00:00",
              "responses": {"101": {"response": "4", "comment": "", "dropdown": "", "tags": ""},
                            "102": {"response": "2", "comment": "", "dropdown": "", "tags": ""}},
              "source_files": ["example.tab"], "overall_comments": "", "subject_comments": ""}
    state, _ = core.ingest_evaluations(state, [record])
    cfg = appd.defaults(date(2026, 11, 3))
    cfg.update(programid="Example Program", start_date="2026-07-01", end_date="2026-11-03")
    state["settings"]["appd"] = cfg
    state["settings"]["appd_roster"] = {"example, resident": {
        "resident": "Example, Resident", "export_id": "EXAMPLE-001",
        "pgy_by_academic_year": {"2026": 3}}}
    state = appd.save_mapping(state, form["id"], {"101": ["EPA 2"], "102": ["EPA 2"]}, True)
    return state


def approved_state():
    state = make_state()
    draft = appd.build_draft(state)
    rows = copy.deepcopy(draft["rows"])
    for row in rows:
        row.update({epa: "0" for epa in appd.EPA_COLUMNS})
        row["EPA 2"] = "3a"
    return appd.save_review(state, state["settings"]["appd"], rows, draft["fingerprint"], approved=True, reviewer="TEST")


def test_latest_completion_date_and_roster_pgy():
    state = make_state()
    draft = appd.build_draft(state)
    row = draft["rows"][0]
    assert row["assessmentdate"] == "2026-07-31"
    assert row["pgy"] == 3
    assert row["name_or_program_assigned_ID"] == "EXAMPLE-001"
    assert all(row[epa] == "" for epa in appd.EPA_COLUMNS)
    assert draft["coverage_ready"]
    metric = next(x for x in draft["summary"] if x["APPD EPA"] == "EPA 2")
    assert metric["Average"] == 3
    assert metric["Ratings"] == 2


def test_date_range_and_ccc_override():
    state = make_state()
    cfg = state["settings"]["appd"]
    cfg.update(assessment_date_mode="fixed", fixed_assessment_date="2026-11-01")
    assert appd.build_draft(state)["rows"][0]["assessmentdate"] == "2026-11-01"
    cfg["start_date"] = "2026-08-01"
    assert appd.build_draft(state)["rows"] == []


def test_one_question_can_link_to_multiple_epas_without_duplicate_weight():
    state = make_state()
    fid = next(iter(state["forms"]))
    state = appd.save_mapping(state, fid, {"101": ["EPA 1", "EPA 2", "EPA 2"]}, True)
    summary = appd.build_draft(state)["summary"]
    for epa in ["EPA 1", "EPA 2"]:
        row = next(x for x in summary if x["APPD EPA"] == epa)
        assert row["Average"] == 4
        assert row["Ratings"] == 1


def test_legacy_pooling_setting_migrates_to_equal_evaluation_weight():
    state = make_state()
    record = copy.deepcopy(next(iter(state["evaluations"].values())))
    record["evaluator"] = "Another, Faculty"
    record["evaluator_key"] = "another, faculty"
    record["completed_at"] = "2026-08-15T10:00:00"
    record["responses"]["101"]["response"] = "2"
    record["responses"]["102"]["response"] = ""
    state, _ = core.ingest_evaluations(state, [record])
    a = next(x for x in appd.build_draft(state)["summary"] if x["APPD EPA"] == "EPA 2")
    assert a["Average"] == 2.5
    state["settings"]["appd"]["aggregation"] = "pooled_ratings"
    b = next(x for x in appd.build_draft(state)["summary"] if x["APPD EPA"] == "EPA 2")
    assert b["Average"] == pytest.approx(2.5)
    assert appd.config_for(state)["aggregation"] == "equal_evaluations"
    assert appd.build_draft(state)["rows"][0]["assessmentdate"] == "2026-08-15"


def test_cross_form_aggregation():
    state = make_state()
    form = copy.deepcopy(next(iter(state["forms"].values())))
    for q in form["questions"]:
        q["id"] = str(int(q["id"]) + 100)
    form["name"] = "Other form"
    form["id"] = core.form_id_for_questions(["201", "202"])
    state = core.register_form(state, form)
    record = copy.deepcopy(next(iter(state["evaluations"].values())))
    record["responses"] = {"201": {"response": "5"}, "202": {"response": "5"}}
    record["rotation"] = "Other rotation"
    state, _ = core.ingest_evaluations(state, [record])
    state = appd.save_mapping(state, form["id"], {"201": ["EPA 2"], "202": ["EPA 2"]}, True)
    metric = next(x for x in appd.build_draft(state)["summary"] if x["APPD EPA"] == "EPA 2")
    assert metric["Average"] == 4
    assert metric["Evaluations"] == 2
    assert metric["Ratings"] == 4


def test_excluded_missing_and_unknown_scores_not_zero():
    state = make_state()
    record = next(iter(state["evaluations"].values()))
    record["responses"]["101"]["response"] = "6"
    record["responses"]["102"]["response"] = ""
    draft = appd.build_draft(state)
    metric = next(x for x in draft["summary"] if x["APPD EPA"] == "EPA 2")
    assert metric["Average"] is None
    assert metric["Ratings"] == 0
    record["responses"]["101"]["response"] = "UNKNOWN"
    draft = appd.build_draft(state)
    assert not draft["coverage_ready"]
    assert next(x for x in draft["summary"] if x["APPD EPA"] == "EPA 2")["Needs scoring review"] == 1


def test_unconfirmed_mapping_and_changed_wording_do_not_score():
    state = make_state()
    fid = next(iter(state["forms"]))
    state["forms"][fid]["questions"][0]["text"] = "New wording"
    draft = appd.build_draft(state)
    assert not draft["coverage_ready"]
    assert all(x["Average"] is None for x in draft["summary"])
    assert draft["warnings"]


def test_explicit_pgy_detection_and_annual_override():
    state = make_state()
    state["settings"].pop("appd_roster")
    record = next(iter(state["evaluations"].values()))
    record["resident_pgy"] = 4
    record["pgy_source_field"] = "Subject PGY"
    assert appd.resolve_pgy(state, record["resident_key"], date(2026, 11, 3))[0] == 4
    assert appd.resolve_pgy(state, record["resident_key"], date(2027, 7, 1))[0] is None
    assert appd.resolve_pgy(state, record["resident_key"], date(2027, 6, 30))[0] == 4


def test_pgy_never_guessed_from_rotation():
    state = make_state()
    state["settings"].pop("appd_roster")
    record = next(iter(state["evaluations"].values()))
    record["rotation"] = "PEDS-ACUTES Senior"
    assert appd.build_draft(state)["rows"][0]["pgy"] is None
    record["rotation"] = "PEDS-ACUTES Intern"
    assert appd.build_draft(state)["rows"][0]["pgy"] is None


def test_optional_thresholds_are_explicit_and_boundaries_work():
    state = make_state()
    cfg = state["settings"]["appd"]
    cfg.update(conversion_enabled=True, conversion_confirmed=True, minimum_score=1.,
               score_bands=[{"upper": 2., "code": "2b"}, {"upper": 4., "code": "3b"}, {"upper": 5., "code": "5"}])
    assert appd.suggested_level(2, cfg) == "2b"
    assert appd.suggested_level(2.001, cfg) == "3b"
    assert appd.suggested_level(5.1, cfg) == ""
    assert appd.suggested_level(0, cfg) == ""
    assert appd.build_draft(state)["rows"][0]["EPA 2"] == "3b"
    cfg["conversion_confirmed"] = False
    with pytest.raises(ValueError, match="Confirm"):
        appd.build_draft(state)


def test_approval_is_required_and_invalidated_by_new_data():
    state = make_state()
    cfg = state["settings"]["appd"]
    with pytest.raises(ValueError, match="approval"):
        appd.workbook_bytes(state, cfg)
    state = approved_state()
    assert appd.approved_review(state, cfg) is not None
    next(iter(state["evaluations"].values()))["responses"]["101"]["response"] = "5"
    assert appd.approved_review(state, cfg) is None
    with pytest.raises(ValueError, match="approval"):
        appd.workbook_bytes(state, cfg)


def test_unsaved_inputs_or_invalid_codes_cannot_be_approved():
    state = make_state()
    draft = appd.build_draft(state)
    with pytest.raises(ValueError, match="choose a final level"):
        appd.save_review(state, draft["config"], draft["rows"], draft["fingerprint"], approved=True, reviewer="TEST")
    next(iter(state["evaluations"].values()))["rotation"] = "changed"
    with pytest.raises(ValueError, match="changed"):
        appd.save_review(state, draft["config"], draft["rows"], draft["fingerprint"], approved=False)


def test_drafts_allow_incomplete_fields_and_are_not_exportable():
    state = make_state()
    draft = appd.build_draft(state)
    state = appd.save_review(state, draft["config"], draft["rows"], draft["fingerprint"], approved=False)
    assert not appd.approved_review(state, draft["config"])
    assert len(state["settings"]["appd_reviews"]) == 1


def test_workbook_has_exact_headers_text_dates_and_valid_categories():
    state = approved_state()
    data = appd.workbook_bytes(state, state["settings"]["appd"])
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        root = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
        strings = ET.fromstring(z.read("xl/sharedStrings.xml"))
        texts = ["".join(x.itertext()) for x in strings]
        def val(c):
            if c.get("t") == "inlineStr":
                return "".join(c.find(appd.S + "is").itertext())
            v = c.find(appd.S + "v")
            if c.get("t") == "s":
                return texts[int(v.text)]
            return v.text if v is not None else None
        rows = root.find(appd.S + "sheetData")
        assert len(rows) == 3
        assert [val(c) for c in rows[1]] == appd.HEADERS
        assert [val(c) for c in rows[2]][:6] == ["Example Program", "07/31/2026", "EXAMPLE-001", "3", "FALL", "GENERAL"]
        assert rows[2][1].get("t") == "inlineStr"
        assert [val(c) for c in rows[2]][7] == "3a"
        assert len(ET.fromstring(z.read("xl/workbook.xml")).find(appd.S + "sheets")) == 1
        assert not any(b"Leslie Anderson" in z.read(n) or b"Kelly Chodos" in z.read(n) for n in z.namelist())


def test_formula_like_export_identifier_stays_literal():
    state = make_state()
    state["settings"]["appd_roster"]["example, resident"]["export_id"] = '=HYPERLINK("bad")'
    draft = appd.build_draft(state)
    rows = draft["rows"]
    for row in rows:
        row.update({epa: "0" for epa in appd.EPA_COLUMNS})
    state = appd.save_review(state, draft["config"], rows, draft["fingerprint"], approved=True, reviewer="TEST")
    with zipfile.ZipFile(io.BytesIO(appd.workbook_bytes(state, draft["config"]))) as z:
        root = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
        cell = root.find(f"{appd.S}sheetData/{appd.S}row[@r='3']/{appd.S}c[@r='C3']")
        assert cell.get("t") == "inlineStr"
        assert not root.findall('.//' + appd.S + 'f')


def test_duplicate_pgy_enrichment_keeps_one_evaluation_and_surfaces_conflicts():
    state = make_state()
    original = copy.deepcopy(next(iter(state["evaluations"].values())))
    original.update(resident_pgy=3, pgy_raw="PGY-3", pgy_source_field="Subject PGY")
    state, stats = core.ingest_evaluations(state, [original])
    assert stats["duplicates"] == 1
    assert len(state["evaluations"]) == 1
    assert next(iter(state["evaluations"].values()))["resident_pgy"] == 3
    original["resident_pgy"] = 4
    state, stats = core.ingest_evaluations(state, [original])
    assert stats["duplicates"] == 1
    assert appd.detected_pgy(state, "example, resident", 2026)[0] is None
    assert appd.resolve_pgy(state, "example, resident", date(2026, 11, 3))[0] == 3


def test_parser_reads_pgy_column_without_changing_identity():
    headers = ["Subject Name", "Subject Rotation", "Session Date", "Date Completed", "Evaluator Name", "[Question 1 ID]", "[Question 1 Response]"]
    values = ["Example, Resident", "Example", "7/1/2026", "7/31/2026", "Example, Faculty", "101", "4"]
    def raw(with_pgy):
        out = io.StringIO()
        writer = csv.writer(out, delimiter='\t')
        writer.writerow(headers + (["Subject PGY"] if with_pgy else []))
        writer.writerow(values + (["PGY-4"] if with_pgy else []))
        return out.getvalue().encode()
    first = core.parse_upload(raw(False), "a.tab")["evaluations"][0]
    second = core.parse_upload(raw(True), "b.tab")["evaluations"][0]
    assert first["id"] == second["id"]
    assert first["content_hash"] == second["content_hash"]
    assert second["resident_pgy"] == 4


def test_config_rejects_unrecognized_mapping_and_overlapping_thresholds():
    cfg = appd.defaults(date(2026, 11, 3))
    cfg['mapping'] = 'OTHER'
    with pytest.raises(ValueError, match="GENERAL"):
        appd.validate_config(cfg)
    cfg.update(mapping='GENERAL', conversion_enabled=True, conversion_confirmed=True,
               score_bands=[{"upper": 3, "code": "3a"}, {"upper": 2, "code": "2a"}])
    with pytest.raises(ValueError, match="increase"):
        appd.validate_config(cfg)


def test_question_order_change_keeps_id_based_links_valid():
    state = make_state()
    form = next(iter(state["forms"].values()))
    form["questions"].reverse()
    assert appd.build_draft(state)["coverage_ready"]
    assert next(x for x in appd.build_draft(state)["summary"] if x["APPD EPA"] == "EPA 2")["Average"] == 3


def test_all_residents_included_and_each_has_own_date_and_pgy():
    state = make_state()
    record = copy.deepcopy(next(iter(state["evaluations"].values())))
    record.update(resident="Example, Second", resident_key="example, second", completed_at="2026-09-30T10:00:00", resident_pgy=2)
    state, _ = core.ingest_evaluations(state, [record])
    draft = appd.build_draft(state)
    assert len(draft["rows"]) == 2
    assert {r["assessmentdate"] for r in draft["rows"]} == {"2026-07-31", "2026-09-30"}
    assert {r["pgy"] for r in draft["rows"]} == {2, 3}
    for row in draft["rows"]:
        row.update({epa: "0" for epa in appd.EPA_COLUMNS})
    state = appd.save_review(state, draft["config"], draft["rows"], draft["fingerprint"], approved=True, reviewer="TEST")
    with zipfile.ZipFile(io.BytesIO(appd.workbook_bytes(state, draft["config"]))) as z:
        root = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
        assert len(root.find(appd.S + "sheetData")) == 4


def test_appd_setup_roster_mapping_and_approval_round_trip_encrypted():
    from cryptography.fernet import Fernet
    from epa_storage import GitHubStore
    state = approved_state()
    store = GitHubStore({"repo": "example/test", "token": "test-token"}, Fernet.generate_key())
    data = store.encode(state)
    assert b"EXAMPLE-001" not in data
    restored = store.decode(data)
    assert restored == state
    assert appd.approved_review(restored, restored["settings"]["appd"])


def test_cleared_export_id_and_fractional_pgy_block_approval():
    state = make_state()
    rows = appd.build_draft(state)["rows"]
    rows[0]["name_or_program_assigned_ID"] = float('nan')
    rows[0]["pgy"] = 2.5
    errors = appd.validate_review(rows, state["settings"]["appd"])
    assert any("APPD name" in e for e in errors)
    assert any("PGY" in e for e in errors)


def test_duplicate_export_identifiers_are_blocked():
    state = make_state()
    rows = appd.build_draft(state)["rows"]
    for row in rows:
        row.update({epa: "0" for epa in appd.EPA_COLUMNS})
    other = copy.deepcopy(rows[0])
    other["resident_key"] = "second"
    other["Resident"] = "Second resident"
    errors = appd.validate_review(rows + [other], state["settings"]["appd"])
    assert any("duplicates" in e for e in errors)
