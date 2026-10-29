"""Clinical Review and Outcomes & Audit panels for the CareCanopy synthetic prototype.

This UI does not verify clinical identity, deliver messages, or dispatch services.
The underlying ReviewWorkflow, its state transitions and persisted data are unchanged:
decisions and handoffs go through ReviewWorkflow.decide / publish_internal_handoff only.
"""
from __future__ import annotations

from html import escape

import streamlit as st
from app.review_workflow import ReviewError, ReviewConflict, ReviewWorkflow
from app.workspace_state import (
    DECISION_DISPLAY, DECISION_PLACEHOLDER, allowed_decisions, can_record_handoff, decision_ready,
    decision_saved, escalation_display, historical_review_warning, review_source_label,
    review_state_badge, timeline_rows,
)

_ESCALATION_TONE = {'URGENT_MEDICAL': 'critical', 'NON_URGENT_PROFESSIONAL': 'caution',
                    'SCOPE_BOUNDARY': 'caution'}


def _remember_widget(source: str, target: str):
    st.session_state[target] = st.session_state.get(source, "")


def _open_outcomes():
    st.session_state['cc_v2_workspace_view'] = 'Outcomes & Audit'


def _chip(text: str, tone: str) -> str:
    return '<span class="cc-chip tone-' + escape(tone) + '">' + escape(text) + '</span>'


def _section(number: str, title: str, subtitle: str) -> None:
    st.html('<div class="cc-step-head"><span class="cc-step-num">' + escape(number) +
            '</span><div><div class="cc-step-title">' + escape(title) +
            '</div><div class="cc-step-sub">' + escape(subtitle) + '</div></div></div>')


def _kv(pairs) -> str:
    return '<div class="cc-kv">' + ''.join(
        '<div><div class="cc-kv-label">' + escape(str(label)) + '</div><div class="cc-kv-value">' +
        escape(str(value)) + '</div></div>' for label, value in pairs) + '</div>'


def _page_header(title: str, subtitle: str, chips) -> None:
    st.html('<div class="cc-page-head"><div><div class="cc-page-title">' + escape(title) +
            '</div><div class="cc-step-sub">' + escape(subtitle) + '</div></div>'
            '<div class="cc-route-meta">' + ''.join(_chip(t, tone) for t, tone in chips) + '</div></div>')


# ---------------------------------------------------------------------------
# Tab 2 · Clinical Review
# ---------------------------------------------------------------------------

def render_review_panel(service: ReviewWorkflow, selected_id: str, *, saved_record: bool = False):
    """03 decision form (REVIEW_PENDING only) and 04 save & continue. Backend calls unchanged."""
    row = service.get(selected_id)
    if row is None:
        st.error("This review record is no longer available.")
        return
    with st.container(border=True):
        _section('03', 'Human review decision',
                 'Simulated reviewer · allowed decisions only · rationale required')
        if row['status'] == 'REVIEW_PENDING':
            st.caption('DEMO ONLY — reviewer identity and clinical authorization are not verified.')
            reviewer_key = f'cc_v2_reviewer_{selected_id}'
            reviewer_draft_key = f'cc_draft_reviewer_{selected_id}'
            reviewer = st.text_input(
                'Simulated reviewer label',
                value=st.session_state.get(reviewer_draft_key, 'synthetic-reviewer'),
                key=reviewer_key, on_change=_remember_widget, args=(reviewer_key, reviewer_draft_key),
            )
            options = allowed_decisions(row['escalation_type'])
            if row['escalation_type'] == 'URGENT_MEDICAL':
                st.error('Urgent medical escalation can only be referred urgently; it cannot be '
                         'cleared by a routine approval.')
            # No preselected decision: the simulated reviewer must choose explicitly.
            decision = st.selectbox('Reviewer decision', options, index=None,
                                    placeholder=DECISION_PLACEHOLDER,
                                    key=f'cc_v2_decision_{selected_id}',
                                    format_func=lambda code: DECISION_DISPLAY.get(code, code))
            if decision == 'REQUEST_MORE_INFO':
                st.caption('Request more information pauses the case until new information arrives. '
                           'It does not re-run the AI model.')
            note_key = f'cc_v2_review_note_{selected_id}'
            note_draft_key = f'cc_draft_review_note_{selected_id}'
            note = st.text_area(
                'Reason for decision (required)',
                value=st.session_state.get(note_draft_key, ''),
                # Matches ReviewWorkflow's 250-character limit so saving never fails on length.
                max_chars=250,
                key=note_key, on_change=_remember_widget, args=(note_key, note_draft_key),
            )
            if st.button('Save simulated review decision', type='primary', key='cc_v2_review_save'):
                if decision is None:
                    st.warning('Please select a reviewer decision.')
                elif not decision_ready(decision, note, options):
                    st.warning('Please provide a reason for your decision.')
                else:
                    try:
                        service.decide(selected_id, reviewer, decision, note,
                                       f'integrated:decision:{selected_id}')
                        st.session_state.pop(note_draft_key, None)
                        st.rerun()
                    except (ReviewError, ReviewConflict) as exc:
                        st.error(f'Unable to save the review: {exc}')
        else:
            st.html(_kv((
                ('Saved decision', DECISION_DISPLAY.get(row.get('decision'), row.get('decision') or '—')),
                ('Simulated reviewer', row.get('reviewer_label') or '—'),
                ('Saved at', (row.get('updated_at') or '')[:19].replace('T', ' ') + ' UTC'),
            )))
            if row.get('review_note'):
                st.html('<div class="cc-why"><div class="cc-why-label">Reviewer rationale</div>'
                        '<div class="cc-why-text">' + escape(row['review_note']) + '</div></div>')

    with st.container(border=True):
        _section('04', 'Save & continue',
                 'Saved decision → eligible internal handoff → Outcomes & Audit')
        status = row['status']
        if status == 'REVIEW_PENDING':
            st.caption('Save a decision first. The internal handoff becomes available only after a '
                       'decision that allows follow-up action is saved.')
        elif can_record_handoff(status):
            st.info('Decision saved. The next step records an INTERNAL work-queue item only; '
                    'nothing is sent externally.')
            if st.button('Record internal handoff', type='primary', key='cc_v2_publish'):
                try:
                    service.publish_internal_handoff(selected_id, f'integrated:publish:{selected_id}')
                    st.rerun()
                except (ReviewError, ReviewConflict) as exc:
                    st.error(f'Unable to record the internal handoff: {exc}')
        elif status == 'AWAITING_INFORMATION':
            st.info('Awaiting more information. No internal handoff is offered or recorded.')
        elif status == 'COMPLETED_INTERNAL':
            st.success('Internal handoff recorded in the local work queue. No external delivery verified.')
        saved = decision_saved(status)
        st.button('Open Outcomes & Audit', key='cc_v2_go_outcomes', on_click=_open_outcomes,
                  disabled=not saved)
        if not saved:
            st.caption('Outcomes & Audit opens after a simulated decision is saved.')
        st.caption('An internal handoff record is not proof of a delivered message or completed clinical care.')


def _render_clinical_review(service, row, *, historical_context, historical_reason, saved_record,
                            attempt_title, profile, prior_reports, live_session=False):
    selected_id = row['case_id']
    packet = row.get('review_packet') or {}
    source_text, source_kind = review_source_label(packet)
    badge, badge_tone = review_state_badge(row['status'], historical=historical_context)
    _page_header('Clinical Review', 'Why Review · Human Decision · Next Action', (
        (row.get('patient_id') or 'Synthetic patient', 'neutral'),
        (source_text, 'ok' if source_kind == 'live' else 'neutral'),
        (badge, badge_tone),
    ))
    st.caption(('Saved record — not the result of this session\'s attempt.' if saved_record
                else 'Review created by this session\'s analysis.') +
               (f'  Current attempt: {attempt_title}.' if attempt_title else ''))
    if source_kind == 'live' and not live_session:
        st.caption('Saved LIVE records are viewable here, but new LIVE inference is not available '
                   'in the current session.')
    if historical_context:
        st.warning(historical_review_warning(historical_reason or 'unverified'))

    escalation = row.get('escalation_type')
    with st.container(border=True):
        _section('01', 'Why this case needs review', 'Final route · escalation reason · observation evidence')
        st.html('<div class="cc-route-meta">' + _chip('Final route · ESCALATE', 'critical'
                                                      if escalation == 'URGENT_MEDICAL' else 'caution') +
                _chip(escalation_display(escalation), _ESCALATION_TONE.get(escalation, 'neutral')) + '</div>')
        why = ('<div class="cc-why"><div class="cc-why-label">Routing rationale (recorded)</div>'
               '<div class="cc-why-text">' + escape(str(packet.get('rationale') or 'No rationale recorded.')) +
               '</div>')
        if packet.get('evidence'):
            why += ('<div class="cc-why-label">Observation evidence</div><ul>' +
                    ''.join('<li>' + escape(str(e)) + '</li>' for e in packet['evidence']) + '</ul>')
        st.html(why + '</div>')

    with st.container(border=True):
        _section('02', 'Review evidence', 'Proposal source · Independent Safety Gate · supporting information')
        prov = packet.get('model_provenance') if source_kind == 'live' else None
        if prov:
            st.html(_kv((
                ('Proposal source', 'Model proposal (Nemotron) · response validated'),
                ('Request profile', prov.get('request_profile', '—')),
                ('Tokens (prompt / completion)', f"{prov.get('prompt_tokens', '—')} / {prov.get('completion_tokens', '—')}"),
                ('Latency', f"{prov.get('latency_seconds', '—')} s"),
            )))
            st.caption('The original model proposal is not stored separately; this record holds the '
                       'post-Safety-Gate result and sanitized provenance.')
        else:
            st.html(_kv((('Proposal source', 'Scripted proposal (NO AI) or legacy record'),
                         ('Model provenance', 'None recorded'))))
        # The packet holds only the post-gate result, so do not claim the route was changed.
        gate = ('Hard boundary applied · ' + str(packet.get('safety_gate_reason') or '')
                if packet.get('safety_gate_triggered') else 'Evaluated · no override')
        st.html(_kv((('Independent Safety Gate', gate),
                     ('Protocol triggers', ', '.join(packet.get('protocol_triggers') or []) or '—'))))
        with st.expander('Supporting information', expanded=False):
            tabs = st.tabs(['Patient baseline & plan', 'Previous observations', 'Safety Gate rules',
                            'Technical review packet'])
            with tabs[0]:
                if profile:
                    st.html(_kv((('Age', profile.get('age', '—')),
                                 ('Weeks since stroke', profile.get('weeks_post_stroke', '—')),
                                 ('Baseline', profile.get('baseline', '—')),
                                 ('Specialist-approved plan (locked)', profile.get('approved_plan', '—')))))
                else:
                    st.caption('Synthetic profile unavailable.')
            with tabs[1]:
                if prior_reports:
                    for item in prior_reports:
                        st.caption(f"{item.get('created_at', '')[:19]} · {item.get('source_mode', '')}")
                        st.write(item.get('report', ''))
                else:
                    st.caption('No earlier observations saved for this synthetic patient.')
            with tabs[2]:
                st.caption('Deterministic hard boundaries override the proposal: urgent medical signs '
                           '(U1–U4) and scope boundaries such as treatment changes, medication, '
                           'diagnosis, discharge, review bypass or record falsification (N1–N6). '
                           'Non-urgent professional review has no deterministic rule; it relies on '
                           'the proposal and this human review.')
            with tabs[3]:
                st.json(packet, expanded=False)

    if historical_context:
        st.info('This review is retained for audit only. New decisions and internal handoffs are '
                'disabled; start a new follow-up in Patient & AI.')
    else:
        render_review_panel(service, selected_id, saved_record=saved_record)


# ---------------------------------------------------------------------------
# Tab 3 · Outcomes & Audit
# ---------------------------------------------------------------------------

def render_outcome_panel(service: ReviewWorkflow, selected_id: str,
                         historical_context: bool = False, saved_record: bool = False,
                         attempt_title: str | None = None, public_demo: bool = False):
    row = service.get(selected_id)
    packet = row.get('review_packet') or {}
    handoff = service.internal_handoff(selected_id)
    events = service.audit_events(selected_id)
    source_text, source_kind = review_source_label(packet)
    badge, badge_tone = review_state_badge(row['status'], historical=historical_context)
    _page_header('Outcomes & Audit', 'State changes · Workflow timeline · Audit evidence',
                 ((badge, badge_tone),))
    st.html('<div class="cc-attempt"><span class="cc-attempt-label">CURRENT ATTEMPT</span><strong>' +
            escape(attempt_title or 'Not run in this session') +
            '</strong><span class="cc-attempt-label" style="margin-left:auto">SELECTED RECORD</span>'
            '<strong>' + escape(selected_id.split('_UI_')[0]) + '</strong><span class="cc-attempt-note">' +
            escape(source_text + (' · saved record' if saved_record else ' · this session')) + '</span></div>')
    if historical_context:
        st.warning('Historical record · read only. It cannot authorize current follow-up or care.')

    escalation = row.get('escalation_type')
    decision = row.get('decision')
    st.html('<div class="cc-outcome-cards">' + ''.join(
        '<div class="cc-outcome-card"><div class="cc-panel-kicker">' + escape(k) + '</div>'
        '<div class="cc-outcome-value tone-' + escape(tone) + '">' + escape(v) + '</div>'
        '<div class="cc-route-note">' + escape(n) + '</div></div>'
        for k, v, n, tone in (
            ('Final routing · Safety Gate', 'ESCALATE', escalation_display(escalation),
             'critical' if escalation == 'URGENT_MEDICAL' else 'caution'),
            ('Simulated reviewer decision', DECISION_DISPLAY.get(decision, 'Pending') if decision else 'Pending',
             'Simulated reviewer · not a new model decision', 'ok' if decision else 'neutral'),
            ('Recorded local action', 'Internal handoff saved' if handoff else 'None recorded',
             'In-app queue only · no external delivery', 'ok' if handoff else 'neutral'),
        )) + '</div>')

    left, right = st.columns([1.35, 1], gap='medium')
    with left:
        with st.container(border=True):
            _section('01', 'Workflow Timeline', 'Saved SQLite events for the selected record · chronological · UTC')
            rows = timeline_rows(events)
            if rows:
                st.html('<div class="cc-timeline">' + ''.join(
                    '<div class="cc-tl-item"><span class="cc-tl-num">' + escape(str(r['event_id'])) + '</span>'
                    '<div class="cc-tl-body"><div class="cc-tl-top"><strong>' + escape(r['title']) +
                    '</strong>' + _chip('Recorded', 'ok') + '</div><div class="cc-tl-meta">' +
                    escape(r['event_type']) + ' · ' + escape(r['at']) + '</div><div class="cc-route-note">' +
                    escape(r['note']) + '</div></div></div>' for r in rows) + '</div>')
            else:
                st.caption('No audit events recorded for this review.')
    with right:
        with st.container(border=True):
            _section('02', 'Saved outcome', 'What changed locally, and what did not happen')
            if handoff:
                st.success('Internal handoff recorded' + (' (earlier saved record).' if saved_record else '.'))
            elif row['status'] == 'AWAITING_INFORMATION':
                st.info('Awaiting more information. No internal handoff recorded.')
            elif row['status'] == 'ACTION_PENDING':
                st.info('Decision saved; internal handoff not yet recorded.')
            else:
                st.info('Review pending. No decision or handoff recorded yet.')
            st.html(_kv((('Clinical routing → simulated decision',
                          'ESCALATE → ' + (DECISION_DISPLAY.get(decision, decision) if decision else 'pending')),)))
            st.warning('External delivery: NOT CONFIGURED. No email, SMS, emergency dispatch, or '
                       'completed clinical care was verified.')
            st.caption('Evidence scope: local SQLite workflow and audit events only. A stored handoff is '
                       'not proof of external notification or autonomous agent resume.')

    with st.expander('Audit evidence & supporting records', expanded=False):
        tabs = st.tabs(['Audit events', 'Review packet', 'Internal handoff record'])
        with tabs[0]:
            if events:
                st.dataframe([{k: e.get(k) for k in ('event_id', 'event_type', 'created_at')} for e in events],
                             use_container_width=True, hide_index=True)
            else:
                st.caption('No audit events recorded.')
        with tabs[1]:
            st.json(packet, expanded=False)
        with tabs[2]:
            if handoff:
                st.json(handoff, expanded=False)
            else:
                st.caption('No internal handoff has been recorded for this case.')
    st.caption('These records are stored in temporary per-session SQLite for this public demo only.'
               if public_demo else
               'These records are stored in local SQLite; they are not synchronized with DynamoDB.')


def render_review_center(service: ReviewWorkflow, selected_id: str | None = None,
                         view: str = 'review', historical_context: bool = False,
                         historical_reason: str | None = None,
                         saved_record: bool = False, *, attempt_title: str | None = None,
                         profile: dict | None = None, prior_reports: list | None = None,
                         live_session: bool = False, public_demo: bool = False):
    """Render Clinical Review or Outcomes & Audit under the stable top-level navigation."""
    if view not in ('review', 'outcome'):
        raise ValueError('Unknown review-center view')
    if not selected_id:
        st.subheader('Clinical Review' if view == 'review' else 'Outcomes & Audit')
        st.info('No review selected. Run an escalated synthetic case in Patient & AI, '
                'or select a previously saved review under Review history.')
        return
    row = service.get(selected_id)
    if row is None:
        st.error('The selected case is not in the local review store.')
        return
    if view == 'review':
        _render_clinical_review(service, row, historical_context=historical_context,
                                historical_reason=historical_reason, saved_record=saved_record,
                                attempt_title=attempt_title, profile=profile, prior_reports=prior_reports,
                                live_session=live_session)
    else:
        render_outcome_panel(service, selected_id, historical_context=historical_context,
                             saved_record=saved_record, attempt_title=attempt_title,
                             public_demo=public_demo)
