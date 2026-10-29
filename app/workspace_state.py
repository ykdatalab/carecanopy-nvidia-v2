"""UI-only case and status helpers; no changes to clinical routing or persistence."""
import tempfile
from pathlib import Path

from app.synthetic_profiles import review_predates_demo_context_edit

WORKSPACE_VIEWS = ("Patient & AI", "Clinical Review", "Outcomes & Audit")

STATUS_DISPLAY = {
    "REVIEW_PENDING": ("Awaiting review", "Human review required"),
    "ACTION_PENDING": ("Action pending", "Review decision recorded; internal handoff pending"),
    "AWAITING_INFORMATION": ("More information needed", "No internal handoff issued"),
    "COMPLETED_INTERNAL": ("Internal handoff recorded", "Not a delivered notification or completed clinical care"),
}

ESCALATION_DISPLAY = {
    "URGENT_MEDICAL": "Urgent medical escalation",
    "NON_URGENT_PROFESSIONAL": "Non-urgent clinical review",
    "SCOPE_BOUNDARY": "Outside delegated scope",
}


def select_review_case(saved_ids, preferred_id=None, prior_id=None):
    """Use the newly routed case first, or a user-selected saved review."""
    if preferred_id in saved_ids:
        return preferred_id
    if prior_id in saved_ids:
        return prior_id
    return saved_ids[0] if saved_ids else None


def reviews_for_scenario(saved_reviews, scenario_id):
    """Only show local review records for the active synthetic scenario.

    Patient linkage is explicit (SYN-{scenario_id}), not a substring match
    against arbitrary review IDs. Other scenarios remain in the SQLite store.
    """
    expected_patient_id = f"SYN-{scenario_id}"
    return [row for row in saved_reviews if row.get("patient_id") == expected_patient_id]


def review_history_reason(profile, review, context_state):
    """Backend guard is authoritative; timestamps only clarify legacy copy.

    Pre-patch reviews with no binding remain historical even if created
    AFTER the last demo profile edit. They must never look actionable.
    """
    if context_state == 'current':
        return None
    if context_state == 'changed' or (
        context_state == 'unverified' and review_predates_demo_context_edit(profile, review)
    ):
        return 'changed'
    return 'unverified'


def historical_review_warning(reason):
    if reason == 'changed':
        return ('Historical review — patient context changed. This saved review '
                'does not authorize actions for the updated synthetic profile. '
                'Run a new follow-up; the earlier record remains available for audit.')
    return ('Historical review — verification unavailable. This review has no '
            'verified binding to the current synthetic patient profile. '
            'Run a new follow-up before any review decision or internal handoff; '
            'the earlier record remains available for audit.')


def status_display(status, historical=False, reason='changed'):
    """Display stale reviews as historical, never as a current authorization.

    This is a display-layer distinction. The immutable record and audit
    events remain unchanged. Server-side authorisation must be handled
    separately for real-world clinical deployments.
    """
    if historical:
        if reason == 'unverified':
            return (
                "Historical review — verification unavailable",
                "This review cannot be verified against the current synthetic profile. "
                "A new follow-up is required before any further action.",
            )
        return (
            "Historical review — reassessment required",
            "Recorded before this synthetic patient's profile was edited. "
            "Do not use this prior review as authorization for the updated context.",
        )
    return STATUS_DISPLAY.get(status, ("Not available", "No confirmed workflow status"))


def escalation_display(value):
    return ESCALATION_DISPLAY.get(value, "Not assessed")


def current_attempt_summary(attempt, scenario_id, execution_mode):
    """Current *session* attempt only; NEVER infer success from saved reviews.

    In particular, archived OFFLINE handoffs are not a current LIVE result.
    The status is presentation-only and does not authorize any workflow action.
    """
    if (not isinstance(attempt, dict)
            or attempt.get('scenario_id') != scenario_id
            or attempt.get('execution_mode') != execution_mode):
        return ('Not run in this session', 'Select a synthetic case and analyze to create a current result.', 'idle')
    if attempt.get('outcome') == 'blocked':
        phase = attempt.get('phase')
        if phase in LIVE_FAILURE_MESSAGES and phase != 'stopped':
            title, note = LIVE_FAILURE_MESSAGES[phase]
            return (title, note, 'blocked')
        return ('Processing safely stopped',
                'The request or response was rejected before routing. No review or handoff was created by this attempt.',
                'blocked')
    if attempt.get('outcome') == 'routed':
        route = attempt.get('route')
        if route not in ('ROUTINE', 'CLARIFY', 'ESCALATE'):
            return ('Result unavailable', 'A verified current route was not supplied.', 'idle')
        return (f'{route} · routing recorded',
                'Routing is not clinical care completion or external notification.', 'routed')
    return ('Not run in this session', 'No verified result for this selection.', 'idle')


def saved_review_label(row, *, historical=False):
    """Picker label: case · status · source · UTC time; raw immutable IDs are shown elsewhere."""
    case = str(row.get('case_id') or 'Review').split('_UI_')[0]
    # Avoid repeating 'Historical review' in the picker.
    status = ('Reassessment required / context unverified' if historical
              else status_display(row.get('status'))[0])
    source = 'LIVE Nemotron' if review_source_label(row.get('review_packet'))[1] == 'live' else 'OFFLINE/legacy'
    created = str(row.get('created_at') or '')
    when = f'{created[:10]} {created[11:16]} UTC' if len(created) >= 16 and created[10] == 'T' else ''
    parts = (['Historical'] if historical else []) + [case, status, source] + ([when] if when else [])
    return ' · '.join(parts)


# ---------------------------------------------------------------------------
# Execution mode and current-attempt presentation (Patient & AI tab).
# Display only: none of these helpers call a provider or authorize an action.
# ---------------------------------------------------------------------------

MODE_OFFLINE = 'Offline scripted replay (NO AI)'
MODE_NEBIUS = 'Live Nebius Nemotron (uses paid API credits)'
MODE_AWS = 'Live AWS Bedrock (v1)'

# Public demo: offline scripted replay only, per-visitor temporary SQLite.
PUBLIC_DEMO_ENV = 'CARECANOPY_PUBLIC_DEMO'
PUBLIC_DEMO_NOTE = ('PUBLIC DEMO · OFFLINE ONLY · NO AI — scripted synthetic replay; no Nebius or AWS '
                    'model call runs here; not evidence of model performance. Your records use temporary '
                    'per-session storage and are not kept for a new browser session or after a server '
                    'restart.')
PUBLIC_SIDEBAR_NOTE = ('Public demo: scripted fixtures + temporary per-session SQLite. '
                       'Live Nebius and AWS modes are disabled here.')
PUBLIC_FOOTER_NOTE = ('Public demo: synthetic records in temporary per-session SQLite. No AI model, '
                      'AWS service or external medical delivery runs here. Clinical decisions remain '
                      'with qualified people.')
_PUBLIC_ROOT_KEY = 'cc_v2_public_demo_data_root'


class PublicDemoBlocked(RuntimeError):
    """A live provider path was requested while the public demo flag is set."""


def public_demo_enabled(env):
    """Unset or '0' keeps the normal app; '1' (or any other value) fails closed to public demo."""
    return (env.get(PUBLIC_DEMO_ENV) or '').strip() not in ('', '0')


def assert_mode_allowed(env, *, offline_replay, nebius_live):
    """Execution-path guard, independent of the UI options."""
    if public_demo_enabled(env) and (nebius_live or not offline_replay):
        raise PublicDemoBlocked('Public demo allows offline scripted replay only; '
                                'no model or AWS request was made.')


def workspace_data_root(env, base_dir, session_state, *, mkdtemp=tempfile.mkdtemp):
    """Folder that holds `.carecanopy_local`. Normal app: the project folder (unchanged).

    Public demo: a temporary folder created once per Streamlit session, so visitors
    never share review records. Not durable across sessions or server restarts.
    """
    if not public_demo_enabled(env):
        return Path(base_dir)
    root = session_state.get(_PUBLIC_ROOT_KEY)
    if not root or not Path(root).is_dir():
        root = mkdtemp(prefix='carecanopy_public_')
        session_state[_PUBLIC_ROOT_KEY] = root
    return Path(root)


def execution_mode_options(env):
    """Billing/security gate: Nebius is offered only when fully configured.

    Returns (options, nebius_available). Never returns environment values.
    The public demo offers offline scripted replay only.
    """
    if public_demo_enabled(env):
        return [MODE_OFFLINE], False
    available = (
        env.get('CARECANOPY_ENABLE_NEBIUS') == '1'
        and bool((env.get('NEBIUS_API_KEY') or '').strip())
        and bool((env.get('NEBIUS_MODEL_ID') or '').strip())
    )
    options = [MODE_OFFLINE]
    if available:
        options.append(MODE_NEBIUS)
    options.append(MODE_AWS)
    return options, available


def expected_result_mode(execution_mode):
    if str(execution_mode).startswith('Offline'):
        return 'OFFLINE_SCRIPTED'
    if str(execution_mode).startswith('Live Nebius'):
        return 'NEBIUS_NEMOTRON_LIVE'
    return 'AWS_BEDROCK_V1'


def observation_source_mode(result_mode):
    """Persistence value only; displayed provenance keeps NEBIUS_NEMOTRON_LIVE."""
    return 'NEBIUS_NEMOTRON' if result_mode == 'NEBIUS_NEMOTRON_LIVE' else result_mode


# Fixed user-facing copy. Raw exception text (which may include provider
# diagnostics) is used only transiently to pick a phase and is never shown.
LIVE_FAILURE_MESSAGES = {
    'not_sent': ('Request not sent',
                 'A local configuration or synthetic-input check stopped the request before '
                 'it was sent. No routing, review, or observation was created.'),
    'request_rejected': ('Request rejected',
                         'The model provider rejected the request. No routing, review, or '
                         'observation was created. Independent Safety Gate: not evaluated.'),
    'request_failed': ('Request failed',
                       'The provider connection failed or timed out; usage may still be billed. '
                       'No routing was created. Independent Safety Gate: not evaluated.'),
    'validation_failed': ('Response validation failed',
                          'The model response did not pass structured validation and was '
                          'discarded. No routing was created. Independent Safety Gate: not evaluated.'),
    'stopped': ('Processing safely stopped',
                'The request or response was rejected before routing. No review or handoff '
                'was created by this attempt.'),
}

_NOT_SENT_MARKERS = (
    'no request made', 'mode is disabled', 'is not set', 'nebius_model_id',
    'not allowlisted', 'only syn-dev', 'missing synthetic', 'invalid synthetic',
    'invalid or oversized synthetic', 'invalid prior', 'history too long',
    'identifying information',
)


def classify_live_failure(message):
    """Map a NebiusRouteError message to a display phase; unknown text fails closed."""
    text = str(message or '').lower()
    if 'connection failed' in text or 'timed out' in text:
        return 'request_failed'
    if any(marker in text for marker in _NOT_SENT_MARKERS):
        return 'not_sent'
    if 'http' in text or 'rate limit' in text:
        return 'request_rejected'
    if 'no route recorded' in text or 'provider json' in text:
        return 'validation_failed'
    return 'stopped'


def current_result_is_displayable(attempt, result, scenario_id, execution_mode, workflow_case_id):
    """A stored result renders only as THIS attempt's verified, matching result."""
    if current_attempt_summary(attempt, scenario_id, execution_mode)[2] != 'routed':
        return False
    if not isinstance(result, dict) or not workflow_case_id:
        return False
    mode = result.get('mode')
    if mode != expected_result_mode(execution_mode) or attempt.get('mode') != mode:
        return False
    if (result.get('state') or {}).get('case_id') != workflow_case_id:
        return False
    decision = result.get('decision')
    route = getattr(getattr(decision, 'route', None), 'value', None)
    return route == attempt.get('route')


def _step(title, status, tone, note):
    return {'title': title, 'status': status, 'tone': tone, 'note': note}


def gate_effect(decision, proposal_route=None, proposal_type=None):
    """Display-only Safety Gate effect, claiming only what stored evidence shows.

    The original proposal is known only for offline scripted replay (the fixture label);
    LIVE and AWS records keep the post-gate decision, so no change is claimed for them.
    Returns (status, tone, note).
    """
    if not decision.safety_gate_triggered:
        return 'Evaluated · no override', 'ok', 'Deterministic boundary rules checked'
    route = decision.route.value
    etype = decision.escalation_type.value if decision.escalation_type else None
    if proposal_route is None:
        return ('Evaluated · hard boundary applied', 'caution',
                'Final route set by a deterministic boundary; original proposal not stored')
    if proposal_route != route:
        return ('Evaluated · route changed', 'caution',
                f'Deterministic boundary changed the route: {proposal_route} → {route}')
    if proposal_type != etype:
        return ('Evaluated · escalation type changed', 'caution',
                f'Route kept ({route}); escalation type {proposal_type or "none"} → {etype}')
    return ('Evaluated · boundary confirmed', 'ok',
            'Deterministic boundary matched the proposal; route and type unchanged')


def workflow_trace(run_state, *, phase=None, result=None, scripted_label=None, scripted_escalation=None):
    """Observation → Proposal → Independent Safety Gate → Final route.

    Pass `result` only when current_result_is_displayable() is True. Steps that
    were not executed are never shown as evaluated, passed, or overridden.
    """
    if run_state == 'blocked':
        title, _ = LIVE_FAILURE_MESSAGES.get(phase, LIVE_FAILURE_MESSAGES['stopped'])
        return [
            _step('Frontline observation', 'Captured', 'neutral', 'Not saved by this attempt'),
            _step('Model proposal (Nemotron)', title, 'caution', 'No usable model output'),
            _step('Independent Safety Gate', 'Not evaluated', 'skipped', 'Stopped before this step'),
            _step('Final route', 'Not evaluated', 'skipped', 'No route recorded'),
        ]
    if run_state != 'routed' or not isinstance(result, dict):
        return [
            _step('Frontline observation', 'Pending', 'pending', "Today's notes"),
            _step('Proposal', 'Pending', 'pending', 'Suggested next step'),
            _step('Independent Safety Gate', 'Pending', 'pending', 'Deterministic rule check'),
            _step('Final route', 'Pending', 'pending', 'Verified route'),
        ]
    decision = result['decision']
    mode = result.get('mode')
    if mode == 'OFFLINE_SCRIPTED':
        proposal = _step('Scripted proposal (NO AI)', scripted_label or 'Scripted label', 'neutral',
                         'Fixture label · no AI model called · no live response to validate')
    elif mode == 'NEBIUS_NEMOTRON_LIVE':
        proposal = _step('Model proposal (Nemotron)', 'Response validation passed', 'ok',
                         'Structured response accepted; original proposal not stored separately')
    else:
        proposal = _step('AWS v1 route', 'Received', 'neutral', 'Nebius response validation not applicable')
    known = mode == 'OFFLINE_SCRIPTED' and scripted_label
    gate = _step('Independent Safety Gate', *gate_effect(
        decision, scripted_label if known else None, scripted_escalation if known else None))
    route = decision.route.value
    route_tone = {'ROUTINE': 'ok', 'CLARIFY': 'caution', 'ESCALATE': 'critical'}.get(route, 'pending')
    escalation = decision.escalation_type.value if decision.escalation_type else None
    return [
        _step('Frontline observation', 'Recorded', 'ok', 'Saved as local synthetic observation'),
        proposal,
        gate,
        _step('Final route', route, route_tone,
              escalation_display(escalation) if escalation else 'No escalation'),
    ]


# Display-only copy for routine routing. Backend action names, statuses and
# descriptions are unchanged; the UI must never imply care was delivered.
ROUTINE_ACTION_LABEL = 'Routine routing recorded locally'
ROUTINE_ACTION_NOTE = 'Routine routing recorded locally. No clinical follow-up delivered.'
# Short enough for st.metric, which truncates long values; the action card
# beside it carries the full "No clinical follow-up delivered." note.
ROUTINE_STATUS_LABEL = 'Routine routing recorded'
_ROUTINE_STATUSES = ('ROUTINE_COMPLETED', 'ROUTINE_RECOMMENDED_INTERNAL')


def action_display_note(action_name, description):
    if action_name == 'COMPLETE_ROUTINE_FOLLOWUP':
        return ROUTINE_ACTION_NOTE
    return description


def workflow_status_label(status, labels):
    if status in _ROUTINE_STATUSES:
        return ROUTINE_STATUS_LABEL
    return labels.get(status, status)


def sidebar_nebius_note(nebius_available, is_live, run_state):
    """Sidebar status from THIS session's attempt only; saved records never count."""
    if not nebius_available:
        return 'Nebius Nemotron: live option not configured.'
    if is_live and run_state == 'routed':
        return 'Nebius Nemotron: LIVE route verified in this session.'
    if is_live and run_state == 'blocked':
        return 'Nebius Nemotron: this session\'s LIVE attempt stopped safely; no route recorded.'
    return 'Nebius Nemotron: live option enabled; no verified LIVE result in this session yet.'


# ---------------------------------------------------------------------------
# Clinical Review / Outcomes & Audit presentation (display only; backend unchanged)
# ---------------------------------------------------------------------------

DECISION_DISPLAY = {
    'APPROVE_FOLLOWUP': 'Approve follow-up within the existing plan',
    'REQUEST_MORE_INFO': 'Request more information',
    'REFER_URGENT': 'Refer for urgent assessment',
}

_EVENT_DISPLAY = {
    'REVIEW_CREATED': ('Review task created',
                       'A simulated review task was created; no external message was sent.'),
    'REVIEW_DECIDED': ('Simulated review decision saved',
                       'Decision and rationale saved by a simulated reviewer (not a verified clinician).'),
    'INTERNAL_HANDOFF_PUBLISHED': ('Internal handoff recorded',
                                   'Internal work-queue record saved. No external delivery verified.'),
}


def review_source_label(packet):
    """Provenance of the review packet: verified LIVE response, or no LIVE provenance."""
    if isinstance(packet, dict) and isinstance(packet.get('model_provenance'), dict):
        return 'LIVE Nemotron · verified response', 'live'
    return 'OFFLINE scripted / legacy · no LIVE provenance', 'offline'


def review_state_badge(status, historical=False):
    """(label, tone) for the review state; historical records are always read-only."""
    if historical:
        return 'Historical · Read only', 'neutral'
    return {
        'REVIEW_PENDING': ('Review pending', 'caution'),
        'ACTION_PENDING': ('Decision saved · handoff available', 'ok'),
        'AWAITING_INFORMATION': ('Awaiting information', 'caution'),
        'COMPLETED_INTERNAL': ('Internal handoff recorded', 'ok'),
    }.get(status, ('Status unavailable', 'neutral'))


def allowed_decisions(escalation_type):
    """Mirrors ReviewWorkflow.decide(): urgent medical cases may only be referred urgently."""
    if escalation_type == 'URGENT_MEDICAL':
        return ['REFER_URGENT']
    return ['APPROVE_FOLLOWUP', 'REQUEST_MORE_INFO', 'REFER_URGENT']


DECISION_PLACEHOLDER = 'Select a reviewer decision'


def decision_ready(decision, rationale, allowed):
    """Save is allowed only after an explicit allowed decision AND a non-empty rationale."""
    return decision in allowed and bool(str(rationale or '').strip())


def decision_saved(status):
    """True once a simulated decision has been persisted (any post-decision state)."""
    return status in ('ACTION_PENDING', 'AWAITING_INFORMATION', 'COMPLETED_INTERNAL')


def can_record_handoff(status, historical=False):
    """The separate internal handoff is only offered after a saved decision (ACTION_PENDING)."""
    return status == 'ACTION_PENDING' and not historical


def format_utc(timestamp):
    text = str(timestamp or '')
    if len(text) >= 19 and text[10] == 'T':
        return text[:10] + ' · ' + text[11:19] + ' UTC'
    return text or 'time unavailable'


def timeline_rows(events):
    """Workflow Timeline rows built ONLY from stored SQLite events (no fabricated steps)."""
    rows = []
    for event in events or []:
        kind = event.get('event_type', '')
        title, note = _EVENT_DISPLAY.get(kind, (kind or 'Unknown event', 'Recorded event.'))
        rows.append({'event_id': event.get('event_id'), 'title': title, 'event_type': kind,
                     'at': format_utc(event.get('created_at')), 'note': note})
    return rows


def next_action(route, *, has_review, clarification_round, is_offline, is_live=False):
    """One next permissible step; never claims delivery or completed care."""
    if route == 'CLARIFY' and is_live:
        # LIVE clarification re-routing is deliberately disabled in this pilot.
        return ('clarify', 'Missing information is listed below. Re-routing is not available in LIVE '
                           'mode; a qualified person must obtain the information and decide. No review, '
                           'handoff or external action has been recorded.')
    if route == 'ESCALATE':
        if has_review:
            return ('open_review', 'Open the simulated clinical review. A qualified person decides; '
                                   'nothing has been sent externally.')
        return ('human', 'Hand this case to a qualified person through your local process; '
                         'no review record was created.')
    if route == 'CLARIFY':
        if clarification_round == 0:
            if is_offline:
                return ('clarify', 'Missing information is listed below. Offline replay cannot '
                                   'interpret clarification text; use a real model to re-route.')
            return ('clarify', 'Add the missing information below and re-route once.')
        return ('human', 'The one clarification round is used; unresolved uncertainty goes to a '
                         'qualified person.')
    if route == 'ROUTINE':
        return ('routine', 'Continue within the specialist-approved plan. Recorded locally; '
                           'no clinical care was completed by CareCanopy.')
    return ('human', 'No verified route; a qualified person must decide.')
