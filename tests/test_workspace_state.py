"""Navigation/view-model checks; does not invoke a language model or clinical APIs."""
import unittest
from app.workspace_state import (
    WORKSPACE_VIEWS,
    select_review_case, status_display, escalation_display, reviews_for_scenario,
    review_history_reason, historical_review_warning,
    current_attempt_summary, saved_review_label,
    MODE_OFFLINE, MODE_NEBIUS, MODE_AWS, LIVE_FAILURE_MESSAGES,
    execution_mode_options, classify_live_failure, current_result_is_displayable,
    workflow_trace, next_action, observation_source_mode,
    ROUTINE_ACTION_LABEL, ROUTINE_ACTION_NOTE, ROUTINE_STATUS_LABEL,
    action_display_note, workflow_status_label,
)


class WorkspaceNavigationTests(unittest.TestCase):
    def test_three_workspace_sections(self):
        self.assertEqual(WORKSPACE_VIEWS, ('Patient & AI', 'Clinical Review', 'Outcomes & Audit'))

    def test_freshly_routed_review_takes_focus(self):
        self.assertEqual(select_review_case(['new', 'old'], preferred_id='new', prior_id='old'), 'new')

    def test_manual_selection_persists_when_still_saved(self):
        self.assertEqual(select_review_case(['new', 'old'], prior_id='old'), 'old')

    def test_deleted_review_falls_back(self):
        self.assertEqual(select_review_case(['valid'], prior_id='deleted'), 'valid')

    def test_no_reviews_is_handled(self):
        self.assertIsNone(select_review_case([]))

    def test_internal_completion_not_misrepresented_as_patient_care(self):
        friendly, description = status_display('COMPLETED_INTERNAL')
        self.assertEqual(friendly, 'Internal handoff recorded')
        self.assertIn('Not a delivered notification', description)

    def test_urgent_clinical_escalation_label(self):
        self.assertEqual(escalation_display('URGENT_MEDICAL'), 'Urgent medical escalation')

    def test_review_history_filters_other_scenario(self):
        reviews = [
            {'case_id': 'DEV04_UI_1', 'patient_id': 'SYN-DEV04'},
            {'case_id': 'DEV01_UI_2', 'patient_id': 'SYN-DEV01'},
        ]
        self.assertEqual([r['case_id'] for r in reviews_for_scenario(reviews, 'DEV01')],
                         ['DEV01_UI_2'])

    def test_review_history_does_not_guess_from_case_id(self):
        reviews = [{'case_id': 'DEV01_spoof', 'patient_id': 'SYN-DEV04'}]
        self.assertEqual(reviews_for_scenario(reviews, 'DEV01'), [])

    def test_review_history_preserves_rows_in_order(self):
        reviews = [
            {'case_id': 'new', 'patient_id': 'SYN-DEV04'},
            {'case_id': 'different', 'patient_id': 'SYN-DEV01'},
            {'case_id': 'old', 'patient_id': 'SYN-DEV04'},
        ]
        self.assertEqual([r['case_id'] for r in reviews_for_scenario(reviews, 'DEV04')],
                         ['new', 'old'])

    def test_historical_completed_review_never_looks_current(self):
        label, description = status_display('COMPLETED_INTERNAL', historical=True)
        self.assertIn('Historical review', label)
        self.assertIn('reassessment', label)
        self.assertIn('not use this prior review', description)

    def test_historical_pending_review_never_looks_current(self):
        label, description = status_display('REVIEW_PENDING', historical=True)
        self.assertIn('Historical review', label)
        self.assertNotEqual(label, 'Awaiting review')

    def test_unbound_recent_review_is_historical_even_after_profile_edit(self):
        profile = {'last_demo_edit_at': '2026-10-26T18:00:00+00:00'}
        newer_review = {'created_at': '2026-10-27T06:34:31+00:00'}
        reason = review_history_reason(profile, newer_review, 'unverified')
        self.assertEqual(reason, 'unverified')
        self.assertIn('verification unavailable', status_display('REVIEW_PENDING', historical=True, reason=reason)[0])
        self.assertIn('verified binding', historical_review_warning(reason))

    def test_unbound_older_review_retains_patient_context_changed_warning(self):
        profile = {'last_demo_edit_at': '2026-10-27T00:00:00+00:00'}
        older_review = {'created_at': '2026-10-26T19:00:00+00:00'}
        self.assertEqual(review_history_reason(profile, older_review, 'unverified'), 'changed')

    def test_verified_review_remains_current(self):
        profile = {'last_demo_edit_at': '2026-10-27T00:00:00+00:00'}
        review = {'created_at': '2026-10-27T06:00:00+00:00'}
        self.assertIsNone(review_history_reason(profile, review, 'current'))

    def test_unmodified_current_review_status_preserved(self):
        self.assertEqual(status_display('REVIEW_PENDING')[0], 'Awaiting review')
        self.assertEqual(status_display('COMPLETED_INTERNAL')[0], 'Internal handoff recorded')

    def test_unknown_status_is_not_assumed_complete(self):
        self.assertEqual(status_display('UNKNOWN')[0], 'Not available')

    def test_current_live_run_does_not_inherit_offline_handoff(self):
        label, explanation, status = current_attempt_summary(None, 'DEV04', 'Live Nebius')
        self.assertEqual(status, 'idle')
        self.assertNotIn('handoff recorded', label.lower())

    def test_attempt_isolated_by_scenario_and_mode(self):
        attempt = {'scenario_id': 'DEV04', 'execution_mode': 'Offline',
                   'outcome': 'routed', 'route': 'ESCALATE'}
        self.assertEqual(current_attempt_summary(attempt, 'DEV01', 'Offline')[2], 'idle')
        self.assertEqual(current_attempt_summary(attempt, 'DEV04', 'Live Nebius')[2], 'idle')

    def test_failed_live_run_is_a_safely_stopped_attempt(self):
        attempt = {'scenario_id': 'DEV04', 'execution_mode': 'Live Nebius', 'outcome': 'blocked'}
        label, explanation, status = current_attempt_summary(attempt, 'DEV04', 'Live Nebius')
        self.assertEqual(status, 'blocked')
        self.assertIn('safely stopped', label.lower())
        self.assertIn('before routing', explanation)

    def test_successful_run_is_routing_not_care_completion(self):
        attempt = {'scenario_id': 'DEV04', 'execution_mode': 'Live Nebius',
                   'outcome': 'routed', 'route': 'ESCALATE'}
        label, explanation, status = current_attempt_summary(attempt, 'DEV04', 'Live Nebius')
        self.assertEqual(status, 'routed')
        self.assertIn('ESCALATE', label)
        self.assertIn('not clinical care completion', explanation)

    def test_malformed_route_not_exposed_as_success(self):
        attempt = {'scenario_id': 'DEV04', 'execution_mode': 'Live Nebius',
                   'outcome': 'routed', 'route': 'MADE_UP'}
        self.assertEqual(current_attempt_summary(attempt, 'DEV04', 'Live Nebius')[2], 'idle')

    def test_saved_review_label_has_case_status_source_and_time(self):
        row = {'case_id': 'DEV04_UI_20261027T130000000000', 'status': 'COMPLETED_INTERNAL',
               'created_at': '2026-10-27T13:00:00+00:00', 'review_packet': {}}
        self.assertEqual(saved_review_label(row),
                         'DEV04 · Internal handoff recorded · OFFLINE/legacy · 2026-10-27 13:00 UTC')
        self.assertTrue(saved_review_label(row, historical=True).startswith(
            'Historical · DEV04 · Reassessment required'))
        live = dict(row, status='REVIEW_PENDING', created_at='2026-10-29T03:51:58.680477+00:00',
                    review_packet={'model_provenance': {'model': 'nvidia/Nemotron-3_5-Lightning'}})
        self.assertEqual(saved_review_label(live),
                         'DEV04 · Awaiting review · LIVE Nemotron · 2026-10-29 03:51 UTC')
        self.assertEqual(saved_review_label({}), 'Review · Not available · OFFLINE/legacy')


def _decision(route, *, gate=False, escalation=None):
    from app.schemas import RoutingDecision, Route, EscalationType
    return RoutingDecision(
        route=Route(route), rationale='synthetic',
        escalation_type=EscalationType(escalation) if escalation else None,
        safety_gate_triggered=gate,
    )


def _result(mode, route, case_id='DEV04_UI_1', **kw):
    return {'decision': _decision(route, **kw), 'mode': mode,
            'state': {'case_id': case_id}, 'actions': {}}


class ExecutionModeGateTests(unittest.TestCase):
    FULL = {'CARECANOPY_ENABLE_NEBIUS': '1', 'NEBIUS_API_KEY': 'k', 'NEBIUS_MODEL_ID': 'm'}

    def test_unconfigured_has_no_nebius_option(self):
        options, available = execution_mode_options({})
        self.assertFalse(available)
        self.assertEqual(options, [MODE_OFFLINE, MODE_AWS])

    def test_configured_offers_nebius_between_offline_and_aws(self):
        options, available = execution_mode_options(self.FULL)
        self.assertTrue(available)
        self.assertEqual(options, [MODE_OFFLINE, MODE_NEBIUS, MODE_AWS])

    def test_partial_configuration_is_not_selectable(self):
        for missing in self.FULL:
            env = dict(self.FULL)
            env[missing] = '   ' if missing != 'CARECANOPY_ENABLE_NEBIUS' else '0'
            self.assertNotIn(MODE_NEBIUS, execution_mode_options(env)[0], missing)

    def test_options_never_echo_environment_values(self):
        env = {'CARECANOPY_ENABLE_NEBIUS': '1', 'NEBIUS_API_KEY': 'sk-secret-123',
               'NEBIUS_MODEL_ID': 'vendor/model-x'}
        joined = ' '.join(execution_mode_options(env)[0])
        self.assertNotIn('sk-secret-123', joined)
        self.assertNotIn('vendor/model-x', joined)


class LiveFailurePresentationTests(unittest.TestCase):
    def test_http_400_is_request_rejected(self):
        msg = ('Nebius rejected the request (HTTP 400); request_profile=full; '
               'json_format_requested=yes; error_type=x; no route recorded; no automatic retry.')
        self.assertEqual(classify_live_failure(msg), 'request_rejected')
        self.assertEqual(classify_live_failure('Nebius access/billing blocked (HTTP 402); no route recorded'),
                         'request_rejected')
        self.assertEqual(classify_live_failure('Nebius rate limit reached; do not retry automatically'),
                         'request_rejected')

    def test_preflight_errors_are_not_sent(self):
        for msg in ('NEBIUS_API_KEY is not set; no live request made',
                    'Nebius API endpoint is not allowlisted',
                    'Possible identifying information in synthetic text; no request made',
                    'Live Nebius mode is disabled (CARECANOPY_ENABLE_NEBIUS != 1)'):
            self.assertEqual(classify_live_failure(msg), 'not_sent', msg)

    def test_timeout_is_request_failed(self):
        self.assertEqual(classify_live_failure(
            'Nebius connection failed or timed out; usage may be billed; do not retry blindly'),
            'request_failed')

    def test_response_errors_are_validation_failed(self):
        for msg in ('Nemotron output is not valid standalone JSON; no route recorded',
                    'Routing schema validation failed; no route recorded',
                    'Provider JSON was not an object',
                    'Provider returned unexpected model ID; no route recorded'):
            self.assertEqual(classify_live_failure(msg), 'validation_failed', msg)

    def test_unknown_text_fails_closed(self):
        self.assertEqual(classify_live_failure('something unexpected'), 'stopped')
        self.assertEqual(classify_live_failure(None), 'stopped')

    def test_fixed_messages_contain_no_diagnostics(self):
        for phase, (title, note) in LIVE_FAILURE_MESSAGES.items():
            text = (title + ' ' + note).lower()
            for leak in ('bearer', 'request_profile', 'api_key', 'http 400;', 'json_format'):
                self.assertNotIn(leak, text, phase)

    def test_phase_specific_attempt_titles(self):
        for phase in ('not_sent', 'request_rejected', 'request_failed', 'validation_failed'):
            attempt = {'scenario_id': 'DEV04', 'execution_mode': 'Live Nebius',
                       'outcome': 'blocked', 'phase': phase}
            title, note, state = current_attempt_summary(attempt, 'DEV04', 'Live Nebius')
            self.assertEqual(state, 'blocked')
            self.assertEqual(title, LIVE_FAILURE_MESSAGES[phase][0])


class CurrentResultDisplayTests(unittest.TestCase):
    def _attempt(self, mode='OFFLINE_SCRIPTED', route='ESCALATE', execution='Offline'):
        return {'scenario_id': 'DEV04', 'execution_mode': execution,
                'outcome': 'routed', 'route': route, 'mode': mode}

    def test_matching_routed_attempt_is_displayable(self):
        self.assertTrue(current_result_is_displayable(
            self._attempt(), _result('OFFLINE_SCRIPTED', 'ESCALATE'),
            'DEV04', 'Offline', 'DEV04_UI_1'))

    def test_offline_result_never_shown_as_live(self):
        attempt = self._attempt(execution='Live Nebius')
        self.assertFalse(current_result_is_displayable(
            attempt, _result('OFFLINE_SCRIPTED', 'ESCALATE'), 'DEV04', 'Live Nebius', 'DEV04_UI_1'))

    def test_other_scenario_blocked_or_missing_attempt_hidden(self):
        result = _result('OFFLINE_SCRIPTED', 'ESCALATE')
        self.assertFalse(current_result_is_displayable(
            self._attempt(), result, 'DEV01', 'Offline', 'DEV04_UI_1'))
        blocked = dict(self._attempt(), outcome='blocked')
        self.assertFalse(current_result_is_displayable(blocked, result, 'DEV04', 'Offline', 'DEV04_UI_1'))
        self.assertFalse(current_result_is_displayable(None, result, 'DEV04', 'Offline', 'DEV04_UI_1'))

    def test_mismatched_case_id_or_route_hidden(self):
        result = _result('OFFLINE_SCRIPTED', 'ESCALATE')
        self.assertFalse(current_result_is_displayable(
            self._attempt(), result, 'DEV04', 'Offline', 'DEV04_UI_OLD'))
        self.assertFalse(current_result_is_displayable(
            self._attempt(route='ROUTINE'), result, 'DEV04', 'Offline', 'DEV04_UI_1'))
        legacy = self._attempt()
        legacy.pop('mode')
        self.assertFalse(current_result_is_displayable(legacy, result, 'DEV04', 'Offline', 'DEV04_UI_1'))


class WorkflowTraceTests(unittest.TestCase):
    def test_not_run_is_all_pending_without_routes(self):
        steps = workflow_trace('idle')
        self.assertEqual([s['status'] for s in steps], ['Pending'] * 4)
        text = ' '.join(s['title'] + s['status'] + s['note'] for s in steps)
        for word in ('ROUTINE', 'CLARIFY', 'ESCALATE', 'override', 'approved'):
            self.assertNotIn(word, text)

    def test_offline_is_scripted_no_ai(self):
        steps = workflow_trace('routed', result=_result('OFFLINE_SCRIPTED', 'ROUTINE'),
                               scripted_label='ROUTINE')
        self.assertIn('NO AI', steps[1]['title'])
        self.assertNotIn('Nemotron', ' '.join(s['title'] + s['note'] for s in steps))
        self.assertEqual(steps[2]['status'], 'Evaluated · no override')
        self.assertEqual(steps[3]['status'], 'ROUTINE')

    def test_live_success_does_not_invent_original_proposal(self):
        steps = workflow_trace('routed', result=_result(
            'NEBIUS_NEMOTRON_LIVE', 'ESCALATE', gate=True, escalation='URGENT_MEDICAL'))
        self.assertEqual(steps[1]['status'], 'Response validation passed')
        self.assertIn('not stored separately', steps[1]['note'])
        for route in ('ROUTINE', 'CLARIFY', 'ESCALATE'):
            self.assertNotIn(route, steps[1]['status'] + steps[1]['note'])
        # The LIVE proposal is not stored, so the gate never claims it changed the route.
        self.assertEqual(steps[2]['status'], 'Evaluated · hard boundary applied')
        self.assertNotIn('changed', steps[2]['note'])
        self.assertEqual(steps[3]['tone'], 'critical')

    def test_failed_live_never_shows_gate_evaluated(self):
        for phase in LIVE_FAILURE_MESSAGES:
            steps = workflow_trace('blocked', phase=phase)
            self.assertEqual(steps[1]['status'], LIVE_FAILURE_MESSAGES[phase][0])
            self.assertEqual([steps[2]['status'], steps[3]['status']], ['Not evaluated'] * 2)
            text = ' '.join(s['status'] + s['note'] for s in steps).lower()
            for word in ('override', 'triggered', 'routine', 'escalate', 'passed'):
                self.assertNotIn(word, text, phase)

    def test_aws_validation_not_applicable(self):
        steps = workflow_trace('routed', result=_result('AWS_BEDROCK_V1', 'CLARIFY'))
        self.assertIn('not applicable', steps[1]['note'])


class NextActionTests(unittest.TestCase):
    def test_one_action_per_route(self):
        self.assertEqual(next_action('ESCALATE', has_review=True, clarification_round=0,
                                     is_offline=True)[0], 'open_review')
        self.assertEqual(next_action('ESCALATE', has_review=False, clarification_round=0,
                                     is_offline=True)[0], 'human')
        self.assertEqual(next_action('CLARIFY', has_review=False, clarification_round=0,
                                     is_offline=False)[0], 'clarify')
        self.assertEqual(next_action('CLARIFY', has_review=False, clarification_round=1,
                                     is_offline=False)[0], 'human')
        kind, text = next_action('ROUTINE', has_review=False, clarification_round=0, is_offline=True)
        self.assertEqual(kind, 'routine')
        self.assertIn('no clinical care was completed', text)

    def test_offline_clarify_explains_no_reinterpretation(self):
        self.assertIn('cannot interpret', next_action(
            'CLARIFY', has_review=False, clarification_round=0, is_offline=True)[1])

    def test_live_clarify_never_offers_reroute_or_claims_review(self):
        for round_ in (0, 1):
            kind, text = next_action('CLARIFY', has_review=False, clarification_round=round_,
                                     is_offline=False, is_live=True)
            self.assertEqual(kind, 'clarify')
            lowered = text.lower()
            self.assertIn('not available in live mode', lowered)
            self.assertNotIn('re-route once', lowered)
            self.assertIn('no review, handoff or external action has been recorded', lowered)
        # Non-LIVE behaviour is unchanged.
        self.assertIn('re-route once', next_action('CLARIFY', has_review=False, clarification_round=0,
                                                   is_offline=False)[1])
        self.assertEqual(next_action('ESCALATE', has_review=True, clarification_round=0,
                                     is_offline=False, is_live=True)[0], 'open_review')


class RoutineWordingTests(unittest.TestCase):
    """Display copy must never imply patient care was delivered."""

    def _routine_actions(self):
        from app.actions import execute_actions
        case = {'id': 'DEV01_UI_TEST'}
        return execute_actions(case, _decision('ROUTINE'))

    def test_backend_routine_record_is_unchanged(self):
        action = self._routine_actions()
        self.assertEqual(action['status'], 'ROUTINE_COMPLETED')
        self.assertEqual(action['actions'][0]['action'], 'COMPLETE_ROUTINE_FOLLOWUP')
        self.assertIn('completed', action['actions'][0]['description'])

    def test_routine_action_display_is_precise(self):
        action = self._routine_actions()['actions'][0]
        note = action_display_note(action['action'], action['description'])
        self.assertEqual(note, ROUTINE_ACTION_NOTE)
        self.assertIn('Routine routing recorded locally', note)
        self.assertIn('No clinical follow-up delivered', note)
        for text in (note, ROUTINE_ACTION_LABEL, ROUTINE_STATUS_LABEL):
            self.assertNotIn('complet', text.lower())

    def test_routine_statuses_never_display_completed(self):
        legacy_labels = {'ROUTINE_COMPLETED': 'Completed'}
        for status in ('ROUTINE_COMPLETED', 'ROUTINE_RECOMMENDED_INTERNAL'):
            label = workflow_status_label(status, legacy_labels)
            self.assertEqual(label, ROUTINE_STATUS_LABEL)
            self.assertNotIn('complet', label.lower())

    def test_other_actions_and_statuses_pass_through(self):
        self.assertEqual(action_display_note('CREATE_REVIEW_PACKET', 'packet text'), 'packet text')
        self.assertEqual(workflow_status_label('ESCALATED', {'ESCALATED': 'Escalated'}), 'Escalated')
        self.assertEqual(workflow_status_label('UNKNOWN', {}), 'UNKNOWN')

    def test_app_source_has_no_routine_completion_copy(self):
        from pathlib import Path
        source = (Path(__file__).resolve().parents[1] / 'streamlit_app.py').read_text(encoding='utf-8')
        for phrase in ('Complete routine follow-up', 'Routine follow-up completed',
                       'Routine follow-up can be completed', '"Completed"'):
            self.assertNotIn(phrase, source)


class ReviewOutcomePresentationTests(unittest.TestCase):
    def test_sidebar_note_uses_current_session_only(self):
        from app.workspace_state import sidebar_nebius_note
        self.assertIn('not configured', sidebar_nebius_note(False, True, 'routed'))
        self.assertIn('verified in this session', sidebar_nebius_note(True, True, 'routed'))
        self.assertIn('no verified LIVE result', sidebar_nebius_note(True, True, 'idle'))
        self.assertIn('stopped safely', sidebar_nebius_note(True, True, 'blocked'))
        # A routed OFFLINE attempt never claims LIVE verification.
        self.assertIn('no verified LIVE result', sidebar_nebius_note(True, False, 'routed'))
        self.assertNotIn('not yet verified', sidebar_nebius_note(True, True, 'routed'))

    def test_review_source_label(self):
        from app.workspace_state import review_source_label
        self.assertEqual(review_source_label({'model_provenance': {'model': 'm'}})[1], 'live')
        self.assertEqual(review_source_label({'rationale': 'x'})[1], 'offline')
        self.assertEqual(review_source_label(None)[1], 'offline')
        self.assertNotIn('LIVE Nemotron', review_source_label({})[0])

    def test_state_badge_and_handoff_eligibility(self):
        from app.workspace_state import review_state_badge, can_record_handoff
        self.assertEqual(review_state_badge('REVIEW_PENDING')[0], 'Review pending')
        self.assertEqual(review_state_badge('COMPLETED_INTERNAL', historical=True)[0],
                         'Historical · Read only')
        self.assertEqual(review_state_badge('MADE_UP')[0], 'Status unavailable')
        self.assertTrue(can_record_handoff('ACTION_PENDING'))
        for status in ('REVIEW_PENDING', 'AWAITING_INFORMATION', 'COMPLETED_INTERNAL', None):
            self.assertFalse(can_record_handoff(status))
        self.assertFalse(can_record_handoff('ACTION_PENDING', historical=True))

    def test_allowed_decisions_match_backend(self):
        from app.workspace_state import allowed_decisions, DECISION_DISPLAY
        from app.review_workflow import ReviewWorkflow
        self.assertEqual(allowed_decisions('URGENT_MEDICAL'), ['REFER_URGENT'])
        self.assertEqual(set(allowed_decisions('NON_URGENT_PROFESSIONAL')), ReviewWorkflow.VALID_DECISIONS)
        self.assertEqual(set(DECISION_DISPLAY), ReviewWorkflow.VALID_DECISIONS)

    def test_decision_requires_explicit_choice_and_rationale(self):
        from app.workspace_state import decision_ready, allowed_decisions, DECISION_PLACEHOLDER
        allowed = allowed_decisions('NON_URGENT_PROFESSIONAL')
        self.assertFalse(decision_ready(None, 'reason', allowed))          # nothing selected (default)
        self.assertFalse(decision_ready('APPROVE_FOLLOWUP', '   ', allowed))
        self.assertFalse(decision_ready('MADE_UP', 'reason', allowed))
        self.assertFalse(decision_ready('APPROVE_FOLLOWUP', 'reason', allowed_decisions('URGENT_MEDICAL')))
        self.assertTrue(decision_ready('REQUEST_MORE_INFO', 'Need timing details', allowed))
        self.assertEqual(DECISION_PLACEHOLDER, 'Select a reviewer decision')
        self.assertNotIn(DECISION_PLACEHOLDER, allowed)

    def test_review_ui_decision_has_no_default(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / 'app' / 'review_ui.py').read_text(encoding='utf-8')
        self.assertIn('index=None', src)
        self.assertIn('placeholder=DECISION_PLACEHOLDER', src)
        self.assertIn('disabled=not saved', src)
        # The rationale field enforces the backend's 250-character limit.
        self.assertIn('max_chars=250', src)
        from app import review_workflow
        with self.assertRaises(review_workflow.ReviewError):
            review_workflow._required('x' * 251, 'note')
        self.assertEqual(review_workflow._required('x' * 250, 'note'), 'x' * 250)

    def test_outcomes_link_only_after_saved_decision(self):
        from app.workspace_state import decision_saved
        self.assertFalse(decision_saved('REVIEW_PENDING'))
        self.assertFalse(decision_saved(None))
        for status in ('ACTION_PENDING', 'AWAITING_INFORMATION', 'COMPLETED_INTERNAL'):
            self.assertTrue(decision_saved(status))

    def test_timeline_built_from_stored_events_only(self):
        from app.workspace_state import timeline_rows
        self.assertEqual(timeline_rows([]), [])
        rows = timeline_rows([
            {'event_id': 21, 'event_type': 'REVIEW_CREATED', 'created_at': '2026-10-29T03:51:58.681479+00:00'},
            {'event_id': 22, 'event_type': 'REVIEW_DECIDED', 'created_at': '2026-10-29T04:10:00+00:00'},
            {'event_id': 23, 'event_type': 'INTERNAL_HANDOFF_PUBLISHED', 'created_at': '2026-10-29T04:11:00+00:00'},
        ])
        self.assertEqual([r['event_id'] for r in rows], [21, 22, 23])
        self.assertEqual(rows[0]['at'], '2026-10-29 · 03:51:58 UTC')
        self.assertIn('No external delivery verified', rows[2]['note'])
        self.assertIn('not a verified clinician', rows[1]['note'])
        self.assertEqual(timeline_rows([{'event_id': 1, 'event_type': 'OTHER'}])[0]['title'], 'OTHER')


class ObservationSourceModeTests(unittest.TestCase):
    def test_mapping_only_changes_live_persistence_value(self):
        self.assertEqual(observation_source_mode('NEBIUS_NEMOTRON_LIVE'), 'NEBIUS_NEMOTRON')
        self.assertEqual(observation_source_mode('OFFLINE_SCRIPTED'), 'OFFLINE_SCRIPTED')
        self.assertEqual(observation_source_mode('AWS_BEDROCK_V1'), 'AWS_BEDROCK_V1')

    def test_mapped_live_mode_is_accepted_by_store(self):
        import json, tempfile
        from pathlib import Path
        from app.synthetic_profiles import SyntheticPatientStore
        root = Path(__file__).resolve().parents[1]
        case = json.loads((root / 'data' / 'dev_cases.json').read_text(encoding='utf-8'))[0]
        with tempfile.TemporaryDirectory() as tmp:
            store = SyntheticPatientStore(Path(tmp) / 'synthetic_patients.sqlite3')
            profile = store.seed_from_fixture(case)
            saved = store.save_observation(
                profile['patient_id'], case_id=case['id'] + '_UI_TEST', report='Synthetic note.',
                source_mode=observation_source_mode('NEBIUS_NEMOTRON_LIVE'))
            self.assertEqual(saved['source_mode'], 'NEBIUS_NEMOTRON')


if __name__ == '__main__':
    unittest.main()
