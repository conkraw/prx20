"""Explicit registration of unmatched evaluation question sets for Admin mapping."""
from __future__ import annotations
from collections import defaultdict
from epa_core import form_id_for_questions, match_form, register_form, normalize_question_id
from epa_question_text import saved_wording_candidates


def unregistered_groups(state: dict) -> list[dict]:
    groups = defaultdict(list)
    for record in state.get('evaluations', {}).values():
        if match_form(record, state.get('forms', {}))['form_id'] is not None:
            continue
        ids = tuple(sorted(normalize_question_id(q) for q in record.get('responses', {}) if str(q).strip()))
        if ids:
            groups[ids].append(record)
    return [{'ids': ids, 'count': len(records),
             'rotations': sorted({str(r.get('rotation') or 'Unspecified') for r in records}),
             'residents': len({str(r.get('resident_key') or r.get('resident')) for r in records}),
             'form_id': form_id_for_questions(list(ids))}
            for ids, records in sorted(groups.items(), key=lambda pair: -len(pair[1]))]


def candidate_form(group: dict, name: str, state: dict | None = None) -> dict:
    if not name.strip():
        raise ValueError('Enter a form name.')
    known = saved_wording_candidates(state or {}, group['ids'])
    return {'id': group['form_id'], 'name': name.strip(),
            'registration_source': 'evaluation_question_ids',
            'questions': [{'id': qid, 'text': (known[qid][0] if len(known.get(qid, [])) == 1
                                              else f'Question {qid} (verify wording from form definition)'),
                           'tags': '', 'include': False} for qid in group['ids']],
            'score_map': {'1': 1.0, '2': 2.0, '3': 3.0, '4': 4.0, '5': 5.0, '6': None},
            'scoring_confirmed': False}


def register_candidate(state: dict, group: dict, name: str) -> dict:
    if group['form_id'] in state['forms']:
        raise ValueError('This form was registered in another session. Reload saved data.')
    latest = {g['form_id']: g for g in unregistered_groups(state)}
    if group['form_id'] not in latest or tuple(group['ids']) != tuple(latest[group['form_id']]['ids']):
        raise ValueError('The unmatched evaluations changed. Reload before registering.')
    return register_form(state, candidate_form(group, name, state))
