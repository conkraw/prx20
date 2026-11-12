"""Confirmation UI for remembered-form removal."""
from __future__ import annotations

import copy
from typing import Callable

import streamlit as st

from epa_form_tools import form_removal_preview, remove_form


def apply_form_removal_session_cleanup() -> None:
    """Clear stale editor/upload selections before widgets are rendered again."""
    removed_id = st.session_state.pop("_removed_form_reset", None)
    if not removed_id:
        return
    state = st.session_state.get("archive", {})
    forms = state.get("forms", {})
    residents = {record.get("resident_key") for record in state.get("evaluations", {}).values()}
    preferences = copy.deepcopy(st.session_state.get("_epa_report_filters", {}))
    if preferences:
        preferences["form_ids"] = [fid for fid in preferences.get("form_ids", []) if fid in forms]
        preferences["residents"] = [key for key in preferences.get("residents", []) if key in residents]
        st.session_state["_epa_report_filters"] = preferences
    # Run on the next rerun, not while these widgets are being instantiated.
    exact = {"upload_preview", "dashboard_epas", "dashboard_residents", "form_to_edit", "appd_mapping_form"}
    prefixes = ("appd_work_", "appd_final_", "numeric_work_", "numeric_editor_", f"appd_map_v2_{removed_id}_",
                f"form_{removed_id}_", "remove_form_")
    for key in list(st.session_state.keys()):
        if key in exact or str(key).startswith(prefixes):
            del st.session_state[key]


def render_form_removal(state: dict, store, commit: Callable, form_id: str) -> None:
    """Show explicit impact and require both acknowledgement and typed intent."""
    from epa_performance import cached_result
    preview = cached_result(st.session_state, state, "form-removal-preview", form_id,
                            lambda: form_removal_preview(state, form_id))
    prefix = f"remove_form_{form_id}_{preview['fingerprint'][:16]}"
    with st.expander("Remove this form", expanded=False):
        st.write(f"Selected form: {preview['form_name']}")
        st.write(
            f"Saved evaluations: {preview['evaluation_count']}. "
            f"Residents affected: {preview['resident_count']}. Applies to all dates."
        )
        st.caption(
            f"{preview['conflict_count']} related pending duplicate/correction decisions. "
            "The form's question selections, scoring scales, and APPD question links will also be removed."
        )
        st.warning(
            "This removes the selected form AND its linked evaluations from the active saved archive, "
            "not just from the screen. Other forms and their evaluations are kept. "
            "Reports will recalculate and APPD export approval will need to be reviewed again."
        )
        st.caption(
            "There is no undo button. Keep the original TAB files to re-import anything you later need. "
            "Removing a form does not erase older encrypted GitHub versions."
        )
        if preview["ambiguous_evaluation_ids"]:
            st.error(
                f"Removal is blocked: {len(preview['ambiguous_evaluation_ids'])} evaluations "
                "could belong to this form or another form. Check Admin > Data review > Unrecognized forms "
                "and upload the correct form definitions before removing this form."
            )
        if preview["blocked_conflict_ids"]:
            st.error(
                f"Removal is blocked: {len(preview['blocked_conflict_ids'])} pending decisions "
                "also involve another or an unrecognized form. Resolve them under Admin > Data review first."
            )
        acknowledged = st.checkbox(
            "I understand that this form and its linked evaluations will be removed",
            key=prefix + "_acknowledge",
        )
        confirmation = st.text_input("Type REMOVE to confirm", key=prefix + "_confirmation")
        ready = acknowledged and confirmation.strip() == "REMOVE" and not preview["blocked"]
        if st.button("Remove form and linked evaluations", type="secondary", disabled=not ready,
                     key=prefix + "_submit"):
            # Validate on the server as well as disabling the browser control.
            if not ready:
                st.error("Confirm the form removal before continuing.")
                return
            def apply(current):
                return remove_form(current, form_id,
                                   expected_fingerprint=preview["fingerprint"], confirmed=True)
            if commit(store, apply):
                result = st.session_state.get("save_result", {})
                count = result.get("evaluations_removed", preview["evaluation_count"])
                st.session_state["notice"] = (
                    f"Removed {preview['form_name']} and {count} linked evaluations. "
                    "The change is saved. Other forms and evaluations were kept."
                )
                st.session_state["_removed_form_reset"] = form_id
                st.rerun()
