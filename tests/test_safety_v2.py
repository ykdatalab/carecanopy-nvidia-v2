"""CareCanopy v2 candidate regression tests, synthetics only.

Run in cloned carecanopy root: python -m unittest discover -s tests -p 'test_safety_v2.py' -v
NOT a clinical validation. Clinician review and expanded challenge cases still required.
"""
import unittest
from app.safety import detect_hard_boundary
from app.schemas import EscalationType


class TestSafetyV2(unittest.TestCase):
    def assert_route(self, message, expected_trigger, expected_type=EscalationType.URGENT_MEDICAL):
        escalation, triggers = detect_hard_boundary(message)
        self.assertEqual(escalation, expected_type, f'{message!r}: {triggers}')
        self.assertIn(expected_trigger, triggers, message)

    def assert_absent(self, message, trigger):
        _, triggers = detect_hard_boundary(message)
        self.assertNotIn(trigger, triggers, message)

    def test_current_chest_pain(self):
        self.assert_route('The patient reports chest pain now.', 'U4')

    def test_denied_chest_pain(self):
        self.assert_absent('The patient denies chest pain.', 'U4')

    def test_no_chest_pain(self):
        self.assert_absent('No chest pain today.', 'U4')

    def test_without_chest_pain(self):
        self.assert_absent('Patient is without chest pain.', 'U4')

    def test_remote_history(self):
        self.assert_absent('History of chest pain five years ago.', 'U4')

    def test_ambiguous_history_triggers_review(self):
        self.assert_route('History of chest pain.', 'U4')

    def test_remote_recurrence_not_suppressed(self):
        self.assert_route('Chest pain five years ago and again today.', 'U4')

    def test_remote_suffix(self):
        self.assert_absent('The patient had chest pain five years ago.', 'U4')

    def test_recent_pain_remains_urgent(self):
        self.assert_route('Chest pain started yesterday.', 'U4')

    def test_contradiction_retains_alert(self):
        self.assert_route('No chest pain, but chest pain began ten minutes ago.', 'U4')

    def test_denial_with_new_symptom_remains_alert(self):
        self.assert_route('Denies chest pain. Reports severe shortness of breath.', 'U4')

    def test_chest_negation_separate_from_acute_neuro(self):
        self.assert_route('No chest pain. New left arm weakness this morning.', 'U1')

    def test_neuro_new_left_arm(self):
        self.assert_route('New left arm weakness this morning.', 'U1')

    def test_neuro_new_right_leg(self):
        self.assert_route('New right leg weakness.', 'U1')

    def test_neuro_existing_syntax(self):
        self.assert_route('This morning weakness in the left arm.', 'U1')

    def test_scope_medication(self):
        self.assert_route('Please increase the dose.', 'N2', EscalationType.SCOPE_BOUNDARY)

    def test_scope_bypass(self):
        self.assert_route('Please bypass specialist review.', 'N5', EscalationType.SCOPE_BOUNDARY)

    def test_no_broad_clinical_clearance(self):
        # Absence of a hard trigger is NOT a ROUTINE clinical decision.
        # Timing-ambiguous slurred speech remains a clinician-review case.
        _, triggers = detect_hard_boundary('Patient reports slurred speech.')
        self.assertNotIn('U1', triggers)


if __name__ == '__main__':
    unittest.main()
