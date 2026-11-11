"""Main Streamlit flows using a memory store and invented evaluation data."""

import pytest
pytest.importorskip("streamlit", reason="Streamlit runtime is not installed in this test environment")

import copy
import io
from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

import epa_core as core
from epa_auth import hash_password


APP = Path(__file__).resolve().parents[1] / "epa_tracker.py"
PASSWORD = "synthetic ui test passphrase"


@pytest.fixture(scope="module")
def password_hash():
    return hash_password(PASSWORD)


def _example_state(*, confirmed=True):
    form = {
        "id": core.form_id_for_questions(["101", "102"]),
        "name": "Example EPA",
        "questions": [
            {"id": "101", "text": "Performs a focused assessment.", "tags": "", "include": True},
            {"id": "102", "text": "Explains the care plan.", "tags": "", "include": True},
        ],
        "score_map": {"1": 1.0, "2": 2.0, "3": 3.0, "4": 4.0, "5": 5.0, "6": None},
        "scoring_confirmed": confirmed,
    }
    state = core.register_form(core.empty_state(), form)
    record = {
        "resident": "Example, Resident",
        "resident_key": "example, resident",
        "evaluator": "Example, Faculty",
        "evaluator_key": "example, faculty",
        "evaluator_status": "Faculty",
        "rotation": "Example rotation",
        "session_date": "2026-07-01",
        "completed_at": "2026-08-01T10:00:00",
        "responses": {
            "101": {"response": "4", "comment": "", "dropdown": "", "tags": ""},
            "102": {"response": "2", "comment": "", "dropdown": "", "tags": ""},
        },
        "overall_comments": "Synthetic example only.",
        "subject_comments": "",
        "source_files": ["example.tab"],
    }
    return core.ingest_evaluations(state, [record])[0]


class MemoryStore:
    saved = None
    loads = 0
    writes = 0

    def __init__(self, config, key):
        pass

    def load(self):
        type(self).loads += 1
        return copy.deepcopy(type(self).saved), "a" * 40

    def transaction(self, transform, attempts=3):
        updated, result = transform(copy.deepcopy(type(self).saved))
        type(self).saved = copy.deepcopy(updated)
        type(self).writes += 1
        return updated, result


def _app(password_hash, state):
    MemoryStore.saved = copy.deepcopy(state)
    MemoryStore.loads = MemoryStore.writes = 0
    app = AppTest.from_file(APP, default_timeout=15)
    app.secrets.update({
        "app": {"password_hash": password_hash, "session_minutes": 30},
        "github": {"token": "synthetic-token", "repo": "example/epa", "branch": "main"},
        "security": {"fernet_key": "synthetic-key-unused-by-memory-store"},
    })
    return app


def _button(app, label):
    return next(button for button in app.button if button.label == label)


def _sign_in(app):
    app.run()
    assert not app.exception
    app.text_input(key="_epa_password_entry").set_value(PASSWORD)
    _button(app, "Sign in").click().run()
    assert not app.exception


def test_seeded_forms_do_not_claim_an_unsaved_archive_has_been_saved():
    from epa_tracker import seed_state
    state = seed_state()
    assert state["updated_at"] is None
    assert len(state["forms"]) == 2
    assert not state["evaluations"]


def test_main_gate_does_not_load_resident_archive_before_correct_password(password_hash):
    with patch("epa_storage.GitHubStore", MemoryStore):
        app = _app(password_hash, _example_state())
        app.run()
        assert not app.exception
        assert MemoryStore.loads == 0
        app.text_input(key="_epa_password_entry").set_value("wrong example password")
        _button(app, "Sign in").click().run()
        assert MemoryStore.loads == 0
        assert not app.dataframe
        app.text_input(key="_epa_password_entry").set_value(PASSWORD)
        _button(app, "Sign in").click().run()
        assert not app.exception
        assert MemoryStore.loads == 1
        assert len(app.dataframe) >= 1


def test_all_main_pages_render_and_excel_prepares(password_hash):
    with patch("epa_storage.GitHubStore", MemoryStore):
        app = _app(password_hash, _example_state())
        _sign_in(app)
        for page in ["Admin", "APPD Export", "Upload", "Dashboard"]:
            app.sidebar.radio[0].set_value(page).run()
            assert not app.exception, page
        app.selectbox(key="dashboard_report_type").set_value("Download reports").run()
        _button(app, "Prepare Excel report").click().run()
        assert not app.exception
        assert not app.error
        assert len(app.get("download_button")) >= 3


def test_form_confirmation_is_persisted_and_reloaded(password_hash):
    with patch("epa_storage.GitHubStore", MemoryStore):
        app = _app(password_hash, _example_state(confirmed=False))
        _sign_in(app)
        app.sidebar.radio[0].set_value("Admin").run()
        app.selectbox(key="simple_admin_section").set_value("Forms and EPA mapping").run()
        confirmation = next(box for box in app.checkbox if box.label.startswith("I checked"))
        assert confirmation.value is False
        confirmation.check().run()
        _button(app, "Save form setup").click().run()
        assert not app.exception
        assert not app.error
        assert MemoryStore.writes == 1
        form = next(iter(MemoryStore.saved["forms"].values()))
        assert form["scoring_confirmed"] is True
        _button(app, "Reload saved data").click().run()
        assert not app.exception
        assert next(box for box in app.checkbox if box.label.startswith("I checked")).value is True


def test_review_replacement_changes_one_saved_evaluation(password_hash):
    state = _example_state()
    correction = copy.deepcopy(next(iter(state["evaluations"].values())))
    correction["responses"]["101"]["response"] = "5"
    state, _ = core.ingest_evaluations(state, [correction])
    with patch("epa_storage.GitHubStore", MemoryStore):
        app = _app(password_hash, state)
        _sign_in(app)
        app.sidebar.radio[0].set_value("Admin").run()
        app.selectbox(key="simple_admin_section").set_value("Data review").run()
        assert not app.exception
        decision = next(radio for radio in app.radio if radio.label == "Decision")
        decision.set_value(next(option for option in decision.options if option.startswith("Replace"))).run()
        _button(app, "Save decision").click().run()
        assert not app.exception
        assert not app.error
        assert len(MemoryStore.saved["evaluations"]) == 1
        assert not MemoryStore.saved["conflicts"]
        assert core.epa_summary(core.score_rows(MemoryStore.saved)).iloc[0]["average"] == 3.5


def test_academic_year_setting_persists_without_changing_evaluations(password_hash):
    with patch("epa_storage.GitHubStore", MemoryStore):
        state = _example_state()
        app = _app(password_hash, state)
        _sign_in(app)
        app.sidebar.radio[0].set_value("Admin").run()
        app.selectbox(key="simple_admin_section").set_value("Advanced").run()
        month = next(box for box in app.selectbox if box.label == "Academic year starts in")
        month.set_value(1).run()
        _button(app, "Save academic-year setting").click().run()
        assert not app.exception
        assert not app.error
        assert MemoryStore.saved["settings"]["academic_year_start_month"] == 1
        assert MemoryStore.saved["evaluations"] == state["evaluations"]


def test_duplicate_new_form_upload_and_blank_name_are_reviewable(password_hash):
    first = io.BytesIO(b"QuestionID\tTags\t[Question Part 1]\n301\t\tPerforms an example assessment.\n")
    first.name = "new_form.tab"
    second = io.BytesIO(first.getvalue())
    second.name = "second_copy.tab"
    with patch("epa_storage.GitHubStore", MemoryStore), patch("streamlit.file_uploader", return_value=[first, second]):
        app = _app(password_hash, _example_state())
        _sign_in(app)
        app.sidebar.radio[0].set_value("Upload").run()
        _button(app, "Review uploads").click().run()
        assert not app.exception
        names = [box for box in app.text_input if box.label.startswith("Form name for")]
        assert len(names) == 1
        names[0].set_value("").run()
        assert not app.exception
        assert _button(app, "Save valid files to GitHub").disabled
        next(box for box in app.text_input if box.label.startswith("Form name for")).set_value("New example EPA").run()
        _button(app, "Save valid files to GitHub").click().run()
        assert not app.exception
        assert not app.error
        assert len(MemoryStore.saved["forms"]) == 2
        assert len(MemoryStore.saved["evaluations"]) == 1


def test_chart_keeps_identically_named_form_versions_separate(password_hash):
    state = _example_state()
    form = copy.deepcopy(next(iter(state["forms"].values())))
    form["questions"][0]["id"] = "201"
    form["questions"][1]["id"] = "202"
    state = core.register_form(state, form)
    second = copy.deepcopy(next(iter(state["evaluations"].values())))
    second["responses"] = {"201": second["responses"]["101"], "202": second["responses"]["102"]}
    state, _ = core.ingest_evaluations(state, [second])
    with patch("epa_storage.GitHubStore", MemoryStore), patch("streamlit.altair_chart") as chart:
        app = _app(password_hash, state)
        _sign_in(app)
        assert not app.exception
        app.selectbox(key="dashboard_report_type").set_value("Monthly progress").run()
        assert chart.call_count >= 1
        assert chart.call_args.args[0].data["Series"].nunique() == 2


def test_multiple_duplicate_candidates_require_selected_replacement_target(password_hash):
    state = _example_state()
    later = copy.deepcopy(next(iter(state["evaluations"].values())))
    later["completed_at"] = "2026-08-02T10:00:00"
    state, _ = core.ingest_evaluations(state, [later])
    state = core.resolve_conflict(state, next(iter(state["conflicts"])), "add")
    newest = copy.deepcopy(later)
    newest["completed_at"] = "2026-08-03T10:00:00"
    newest["responses"]["101"]["response"] = "5"
    state, _ = core.ingest_evaluations(state, [newest])
    target_id = core.record_identity(later)
    with patch("epa_storage.GitHubStore", MemoryStore):
        app = _app(password_hash, state)
        _sign_in(app)
        app.sidebar.radio[0].set_value("Admin").run()
        app.selectbox(key="simple_admin_section").set_value("Data review").run()
        assert not app.exception
        decision = next(radio for radio in app.radio if radio.label == "Decision")
        assert not any(option.startswith("Replace") for option in decision.options)
        targets = next(box for box in app.selectbox if box.label == "Saved evaluation to compare")
        targets.set_value(target_id).run()
        assert not app.exception
        decision = next(radio for radio in app.radio if radio.label == "Decision")
        decision.set_value(next(option for option in decision.options if option.startswith("Replace"))).run()
        _button(app, "Save decision").click().run()
        assert not app.exception
        assert not app.error
        assert len(MemoryStore.saved["evaluations"]) == 2
        assert MemoryStore.saved["evaluations"][target_id]["responses"]["101"]["response"] == "5"

