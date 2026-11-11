"""A single, form-scoped repair panel for missing question wording."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from epa_core import parse_upload
from epa_question_text import missing_wording_ids, wording_repair_preview, restore_question_wording


def render_question_text_repair(state: dict, store, commit, form_id: str) -> None:
    missing = missing_wording_ids(state["forms"][form_id])
    if not missing:
        return
    st.warning(f"Question wording is missing for {len(missing)} questions. The question IDs are still saved. Restore the wording below before reviewing the EPA links.")
    with st.expander("Restore question wording", expanded=True):
        st.caption("This fills missing wording only. It keeps your evaluations, question selections, scoring settings, and EPA links. Save any unfinished setup changes before restoring wording.")
        saved = wording_repair_preview(state, form_id)
        if saved["changes"]:
            st.caption(f"Found saved wording for {len(saved['changes'])} questions with matching IDs.")
            _preview_table(saved)
            if st.button("Restore saved wording", key=f"restore_saved_wording_{form_id}", type="primary"):
                _save_repair(state, store, commit, form_id, saved)
        if saved["conflicting_ids"]:
            st.info("Different saved wording exists for question IDs " + ", ".join(saved["conflicting_ids"]) + ". Use the original form-definition file to select the correct wording.")
        if not saved["changes"]:
            st.info("No single saved wording could be recovered automatically. Upload the original form-definition TAB file below. Do not delete the form or re-upload resident evaluations.")
        source = st.file_uploader("Original form-definition TAB file", type=["tab", "tsv", "txt"],
                                  key=f"wording_definition_{form_id}",
                                  help="Use the file with QuestionID and Question Part/Text columns, not just evaluation responses.")
        if source is None:
            return
        try:
            parsed = parse_upload(source.getvalue(), source.name)
            if parsed["kind"] != "form":
                raise ValueError("This is an evaluation export. Choose the form-definition TAB file containing QuestionID and the question wording. Your saved evaluations have not been changed.")
            plan = wording_repair_preview(state, form_id, parsed["form"])
            if not plan["changes"]:
                st.info("This file has no new wording for the missing question IDs. Check that it is the matching form definition.")
                return
            st.caption(f"Ready to restore {len(plan['changes'])} questions in {plan['form_name']} by matching question ID.")
            _preview_table(plan)
            if plan["remaining_ids"]:
                st.warning("This file will not restore every missing question. Still missing: " + ", ".join(plan["remaining_ids"]))
            if plan["source_only_ids"]:
                st.caption("Extra questions in the file are not added by this repair. This repair keeps the selected form and its existing IDs unchanged.")
            if st.button("Restore wording from file", key=f"restore_file_wording_{form_id}", type="primary"):
                _save_repair(state, store, commit, form_id, plan, parsed["form"])
        except ValueError as exc:
            st.error(str(exc))


def _preview_table(plan: dict) -> None:
    frame = pd.DataFrame([{"Question ID": item["question_id"], "Wording to restore": item["after"]}
                          for item in plan["changes"]])
    st.dataframe(frame, hide_index=True, width="stretch")


def _save_repair(state, store, commit, form_id, plan, source_form=None) -> None:
    def apply(current):
        updated, result = restore_question_wording(current, form_id,
            expected_token=plan["token"], source_form=source_form)
        message = f"Restored wording for {result['restored']} questions. Existing selections, scales, and EPA links were kept. Review the restored wording and click Save form setup."
        if result["remaining"]:
            message += f" Wording is still missing for {result['remaining']} questions."
        return updated, message
    try:
        if commit(store, apply):
            st.session_state["notice"] = st.session_state.get("save_result", "Question wording restored. Review and save form setup.")
            st.rerun()
    except ValueError as exc:
        st.error(str(exc))
