"""Display-only Safety Gate wording and public-demo notices (offline, synthetic fixtures)."""
import json
from pathlib import Path
import unittest

from app.review_integration import scripted_synthetic_replay
from app.schemas import RoutingDecision
from app.workspace_state import (PUBLIC_DEMO_NOTE, PUBLIC_FOOTER_NOTE, PUBLIC_SIDEBAR_NOTE,
                                 gate_effect, workflow_trace)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = {c['id']: c for c in json.loads((ROOT / 'data' / 'dev_cases.json').read_text(encoding='utf-8'))}


def decision(route, etype=None, triggered=False):
    return RoutingDecision(route=route, escalation_type=etype, rationale='x',
                           safety_gate_triggered=triggered,
                           safety_gate_reason='Deterministic safety boundary triggered: N1.' if triggered else None)


class GateEffectTests(unittest.TestCase):
    def test_not_triggered_is_no_override(self):
        self.assertEqual(gate_effect(decision('ROUTINE'), 'ROUTINE', None)[0], 'Evaluated · no override')

    def test_route_change_only_when_proposal_route_differs(self):
        status, tone, note = gate_effect(decision('ESCALATE', 'URGENT_MEDICAL', True), 'ROUTINE', None)
        self.assertEqual(status, 'Evaluated · route changed')
        self.assertIn('ROUTINE → ESCALATE', note)

    def test_escalation_type_change_keeps_route(self):
        status, _, note = gate_effect(decision('ESCALATE', 'URGENT_MEDICAL', True),
                                      'ESCALATE', 'SCOPE_BOUNDARY')
        self.assertEqual(status, 'Evaluated · escalation type changed')
        self.assertIn('Route kept (ESCALATE)', note)
        self.assertNotIn('changed the route', note)

    def test_boundary_confirmed_when_nothing_changed(self):
        status, tone, note = gate_effect(decision('ESCALATE', 'SCOPE_BOUNDARY', True),
                                         'ESCALATE', 'SCOPE_BOUNDARY')
        self.assertEqual((status, tone), ('Evaluated · boundary confirmed', 'ok'))
        self.assertNotRegex(note, r'\bchanged\b')   # 'unchanged' is fine

    def test_unknown_proposal_never_claims_a_change(self):
        status, _, note = gate_effect(decision('ESCALATE', 'URGENT_MEDICAL', True))
        self.assertEqual(status, 'Evaluated · hard boundary applied')
        self.assertIn('original proposal not stored', note)
        self.assertNotRegex(note, r'\bchanged\b')


class FixtureTraceTests(unittest.TestCase):
    """DEV04/DEV05 (and every dev fixture) through the real scripted replay + Safety Gate."""
    def trace(self, fixture_id):
        fixture = FIXTURES[fixture_id]
        result = scripted_synthetic_replay(dict(fixture))
        steps = workflow_trace('routed', result=result, scripted_label=fixture.get('expected_route'),
                               scripted_escalation=fixture.get('expected_escalation_type'))
        return result['decision'], steps[2]

    def test_dev04_non_urgent_no_override(self):
        final, gate = self.trace('DEV04')
        self.assertEqual((final.route.value, final.escalation_type.value), ('ESCALATE', 'NON_URGENT_PROFESSIONAL'))
        self.assertEqual(gate['status'], 'Evaluated · no override')

    def test_dev05_scope_boundary_confirmed_not_changed(self):
        final, gate = self.trace('DEV05')
        self.assertTrue(final.safety_gate_triggered)
        self.assertEqual((final.route.value, final.escalation_type.value), ('ESCALATE', 'SCOPE_BOUNDARY'))
        self.assertEqual(gate['status'], 'Evaluated · boundary confirmed')
        self.assertNotRegex(gate['note'], r'\bchanged\b')

    def test_no_fixture_claims_a_route_change_it_did_not_make(self):
        for fixture_id, fixture in FIXTURES.items():
            with self.subTest(fixture_id):
                final, gate = self.trace(fixture_id)
                if 'changed the route' in gate['note']:
                    self.assertNotEqual(fixture['expected_route'], final.route.value)


class PublicNoticeTests(unittest.TestCase):
    def test_public_notices_drop_unselectable_modes(self):
        for note in (PUBLIC_SIDEBAR_NOTE, PUBLIC_FOOTER_NOTE):
            for stale in ('AWS v1', 'DynamoDB', 'not configured', 'not active', 'opt-in'):
                self.assertNotIn(stale, note)
        self.assertIn('not evidence of model performance', PUBLIC_DEMO_NOTE)

    def test_default_mode_notices_kept_in_source(self):
        src = (ROOT / 'streamlit_app.py').read_text(encoding='utf-8')
        for kept in ("'AWS v1 (optional legacy/comparison): Bedrock / DynamoDB / EventBridge. '",
                     "'Synthetic records use local SQLite. Optional AWS v1 can use AWS services. '",
                     "st.caption('**OFFLINE REPLAY · NO AI** — Scripted synthetic labels; local SQLite only. "
                     "Not evidence of model performance.')"):
            self.assertIn(kept, src)
        ui = (ROOT / 'app' / 'review_ui.py').read_text(encoding='utf-8')
        self.assertIn("'These records are stored in local SQLite; they are not synchronized with DynamoDB.'", ui)
        self.assertNotIn('Override applied', ui)


if __name__ == '__main__':
    unittest.main()
