"""Bridge CareCanopy routing results to local durable synthetic review workflow.

Local proof of concept only: NOT DynamoDB review persistence, real clinical
reviewer verification, external notification, or clinical action completion.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.actions import execute_actions
from app.review_workflow import ReviewWorkflow, ReviewError
from app.safety import apply_safety_gate
from app.schemas import EscalationType, Route, RoutingDecision


def review_store(project_dir: str | Path) -> ReviewWorkflow:
    # Open/create the synthetic profile schema before the review engine.
    # The guard is enabled for every review created through the main app.
    from app.synthetic_profiles import SyntheticPatientStore
    local = Path(project_dir) / '.carecanopy_local'
    profile_path = local / 'synthetic_patients.sqlite3'
    SyntheticPatientStore(profile_path)
    return ReviewWorkflow(local / 'review.sqlite3', profile_db_path=profile_path)


def register_review(result: dict, case: dict, service: ReviewWorkflow) -> dict | None:
    """Create an idempotent local review from an ACTUAL post-gate action packet.

    Never fabricate a positive review packet for a routine/clarification case.
    The same case id and same packet can be replayed without duplicate events.
    """
    action = result.get('actions') or {}
    if action.get('route') != 'ESCALATE':
        return None
    packet = action.get('review_packet')
    if not isinstance(packet, dict):
        raise ReviewError('Escalated case has no review packet; cannot create review task')
    case_id = action.get('case_id')
    if not case_id or str(case_id) != str(case.get('id')):
        raise ReviewError('Case ID mismatch between routing and review task')
    if packet.get('case_id') != case_id:
        raise ReviewError('Review packet case ID mismatch')
    patient_id = case.get('synthetic_patient_id') or case.get('source_case_id')
    if not patient_id:
        raise ReviewError('Synthetic patient ID required for review traceability')
    return service.create_review(
        str(case_id), str(patient_id), packet,
        expected_context_version=case.get('synthetic_context_version'),
        expected_patient_context=case.get('patient_context'),
    )


def scripted_synthetic_replay(case: dict) -> dict:
    """Offline UX-only test. EXPECTED labels are not model predictions.

    Forces the selected synthetic fixture's expected route as a scripted
    proposal, then runs the project's actual deterministic safety gate and
    action conversion. It does not call AI, AWS, or Nebius.
    """
    expected = case.get('expected_route')
    if expected not in {'ROUTINE','CLARIFY','ESCALATE'}:
        raise ValueError('Synthetic fixture requires an expected_route for scripted replay')
    esc_text = case.get('expected_escalation_type')
    escalation = EscalationType(esc_text) if esc_text else None
    if expected == 'ESCALATE' and escalation is None:
        raise ValueError('Scripted escalation requires expected_escalation_type')
    proposal = RoutingDecision(
        route=Route(expected),
        escalation_type=escalation,
        rationale='Scripted synthetic fixture label for UI testing only; not an AI inference.',
        evidence=[str(case.get('current_report',''))],
        missing_information=(['Please clarify timing and change from baseline.'] if expected == 'CLARIFY' else []),
        immediate_medical_guidance=esc_text == 'URGENT_MEDICAL',
        specialist_notification=expected == 'ESCALATE',
        create_review_packet=expected == 'ESCALATE',
    )
    decision = apply_safety_gate(case, proposal)
    action = execute_actions(case, decision)
    names = [entry.get('action') for entry in action.get('actions', [])]
    state = {
        'case_id': case['id'],
        'status': action['status'],
        'routine_followup_completed': 'COMPLETE_ROUTINE_FOLLOWUP' in names,
        'clarification_requested': 'REQUEST_CLARIFICATION' in names,
        'review_required': 'CREATE_REVIEW_PACKET' in names,
        'clinical_team_notified': 'NOTIFY_CLINICAL_TEAM' in names,
        'audit_log': [{'timestamp': action['executed_at'], 'action': name,
                       'source': 'OFFLINE SCRIPTED REPLAY - NOT EXECUTED EXTERNALLY'}
                      for name in names],
    }
    return {'decision': decision, 'actions': action, 'state': state, 'mode': 'OFFLINE_SCRIPTED'}
