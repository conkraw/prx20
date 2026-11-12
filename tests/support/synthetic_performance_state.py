"""Reproducible fictional benchmark data. No uploaded resident records."""
import random
from datetime import date

import epa_appd as appd
import epa_core as core


def make_synthetic_state(evaluations=600, residents=40, forms=8, questions=10):
    state = core.empty_state()
    rng = random.Random(614)
    form_ids = []
    for fi in range(forms):
        qids = [str(10000 + fi * 100 + qi) for qi in range(questions)]
        fid = core.form_id_for_questions(qids)
        form = {"id": fid, "name": f"Fictional form {fi + 1}",
                "questions": [{"id": qid, "text": f"Fictional assessment question {qi + 1}.",
                               "include": qi < questions - 2, "tags": ""} for qi, qid in enumerate(qids)],
                "score_map": {"1": 1., "2": 2., "3": 3., "4": 4., "5": 5., "6": None},
                "scoring_confirmed": True}
        state = core.register_form(state, form)
        links = {qid: [f"EPA {(qi % 6) + 1}", "EPA 8"] for qi, qid in enumerate(qids[:-2])}
        state = appd.save_mapping(state, fid, links, True)
        form_ids.append(fid)
    for ri in range(residents):
        key = f"fictional resident {ri:03}"
        state["settings"].setdefault("appd_roster", {})[key] = {
            "resident": f"Fictional resident {ri:03}", "export_id": f"TEST-{ri:03}",
            "residency_start_date": f"{2026 - ri % 4}-07-01"}
    for ei in range(evaluations):
        ri = ei % residents
        fi = (ei // residents + ri) % forms
        fid = form_ids[fi]
        month = 7 + (ei // (residents * 2)) % 5
        day = 1 + (ei // residents) % 27
        record = {"resident": f"Fictional resident {ri:03}", "resident_key": f"fictional resident {ri:03}",
                  "evaluator": f"Fictional faculty {ei % 23:02}", "evaluator_key": f"fictional faculty {ei % 23:02}",
                  "evaluator_status": "Faculty", "rotation": state["forms"][fid]["name"],
                  "session_date": f"2026-{month:02}-01", "completed_at": f"2026-{month:02}-{day:02}T12:00:00",
                  "responses": {q["id"]: {"response": rng.choice(["2", "3", "3", "4", "4", "5", "6", ""]),
                                            "comment": "", "dropdown": "", "tags": ""}
                                for q in state["forms"][fid]["questions"]},
                  "source_files": [f"fictional_{fi}.tab"], "overall_comments": "", "subject_comments": ""}
        state["evaluations"][f"fictional-evaluation-{ei:05}"] = record
    config = appd.defaults(date(2026, 11, 12))
    config.update(programid="Fictional benchmark", start_date="2026-07-01", end_date="2026-11-30")
    state["settings"]["appd"] = config
    state["updated_at"] = "2026-11-12T12:00:00Z"
    return state
