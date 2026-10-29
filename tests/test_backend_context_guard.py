"""Direct backend safety regression tests: synthetic demo only, no network."""
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from app.review_integration import review_store, register_review, scripted_synthetic_replay
from app.review_workflow import ReviewWorkflow, ReviewConflict
from app.synthetic_profiles import SyntheticPatientStore, case_with_demo_profile


class BackendContextGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name)
        self.profiles = SyntheticPatientStore(self.project/'.carecanopy_local'/'synthetic_patients.sqlite3')
        self.fixture = {'id': 'DEV04', 'expected_route': 'ESCALATE',
                        'expected_escalation_type': 'NON_URGENT_PROFESSIONAL',
                        'current_report': 'New shoulder pain affecting daily life',
                        'patient_context': {'age': 69, 'weeks_post_stroke': 11,
                                            'baseline': 'Uses quad cane',
                                            'approved_plan': 'Walking and balance program'}}
        self.profile = self.profiles.seed_from_fixture(self.fixture)
        self.service = review_store(self.project)

    def tearDown(self):
        self.tmp.cleanup()

    def make_case(self, suffix):
        case = case_with_demo_profile(self.fixture, self.profiles.get('SYN-DEV04'))
        case.update({'id': f'DEV04_UI_{suffix}', 'source_case_id': 'DEV04'})
        return case

    def register(self, suffix):
        case = self.make_case(suffix)
        review = register_review(scripted_synthetic_replay(case), case, self.service)
        self.assertEqual(review['profile_version'], self.profiles.get('SYN-DEV04')['context_version'])
        return case

    def change_profile(self, age=70):
        return self.profiles.update_demo_context('SYN-DEV04', age=age,
                    weeks_post_stroke=12, baseline='Uses quad cane')

    def test_backend_decide_direct_call_rejected_after_profile_change(self):
        case = self.register('beforeedit')
        self.change_profile()
        with self.assertRaisesRegex(ReviewConflict, 'context changed'):
            self.service.decide(case['id'], 'demo', 'APPROVE_FOLLOWUP', 'Test', 'd1')
        self.assertEqual(self.service.get(case['id'])['status'], 'REVIEW_PENDING')
        self.assertEqual([e['event_type'] for e in self.service.audit_events(case['id'])], ['REVIEW_CREATED'])

    def test_backend_publish_direct_call_rejected_after_edit_after_approval(self):
        case = self.register('approvedprior')
        self.service.decide(case['id'], 'demo', 'APPROVE_FOLLOWUP', 'Within plan', 'd1')
        self.change_profile()
        with self.assertRaisesRegex(ReviewConflict, 'context changed'):
            self.service.publish_internal_handoff(case['id'], 'p1')
        self.assertEqual(self.service.get(case['id'])['status'], 'ACTION_PENDING')
        self.assertIsNone(self.service.internal_handoff(case['id']))
        self.assertEqual(len(self.service.audit_events(case['id'])), 2)

    def test_assessment_started_before_edit_cannot_register_review_later(self):
        case = self.make_case('delayed')
        result = scripted_synthetic_replay(case)
        self.change_profile()
        with self.assertRaisesRegex(ReviewConflict, 'changed during assessment'):
            register_review(result, case, self.service)
        self.assertIsNone(self.service.get(case['id']))

    def test_updated_profile_new_review_succeeds_and_retries_idempotent(self):
        self.change_profile()
        case = self.register('newcontext')
        self.service.decide(case['id'], 'demo', 'APPROVE_FOLLOWUP', 'Within plan', 'd1')
        self.service.decide(case['id'], 'demo', 'APPROVE_FOLLOWUP', 'Within plan', 'd1')
        self.service.publish_internal_handoff(case['id'], 'p1')
        self.service.publish_internal_handoff(case['id'], 'p1')
        self.assertEqual(self.service.get(case['id'])['status'], 'COMPLETED_INTERNAL')
        self.assertEqual(len(self.service.audit_events(case['id'])), 3)
        self.assertEqual(self.service.internal_handoff(case['id'])['external_delivery_status'], 'NOT_CONFIGURED')

    def test_reverting_to_old_values_does_not_revive_old_review(self):
        case = self.register('reverted')
        self.change_profile()
        self.profiles.update_demo_context('SYN-DEV04', age=69, weeks_post_stroke=11, baseline='Uses quad cane')
        self.assertEqual(self.profiles.get('SYN-DEV04')['context_version'], 3)
        with self.assertRaisesRegex(ReviewConflict, 'context changed'):
            self.service.decide(case['id'], 'demo', 'APPROVE_FOLLOWUP', 'Within plan', 'd1')

    def test_noop_context_save_keeps_binding_valid(self):
        case = self.register('noop')
        self.profiles.update_demo_context('SYN-DEV04', age=69, weeks_post_stroke=11, baseline='Uses quad cane')
        self.assertEqual(self.profiles.get('SYN-DEV04')['context_version'], 1)
        self.service.decide(case['id'], 'demo', 'APPROVE_FOLLOWUP', 'Within plan', 'd1')
        self.assertEqual(self.service.get(case['id'])['status'], 'ACTION_PENDING')

    def test_source_patient_snapshot_must_match_saved_profile(self):
        case = self.make_case('tampered')
        case['patient_context']['age'] = 71  # not actually saved
        result = scripted_synthetic_replay(case)
        with self.assertRaisesRegex(ReviewConflict, 'snapshot does not match'):
            register_review(result, case, self.service)
        self.assertIsNone(self.service.get(case['id']))

    def test_review_creation_rejects_patient_case_mismatch(self):
        case = self.make_case('mismatch')
        case['id'] = 'DEV01_UI_wrongpatient'
        result = scripted_synthetic_replay(case)
        # This is a direct call bypassing the UI registration checks.
        with self.assertRaisesRegex(ReviewConflict, 'does not belong'):
            self.service.create_review(case['id'], 'SYN-DEV04', {
                'case_id': case['id'], 'escalation_type': 'NON_URGENT_PROFESSIONAL'},
                expected_context_version=1, expected_patient_context=case['patient_context'])
        self.assertIsNone(self.service.get(case['id']))

    def test_read_only_context_state_matches_backend_authorization(self):
        case = self.register('display')
        self.assertEqual(self.service.review_context_state(case['id']), 'current')
        self.assertEqual(self.service.get(case['id'])['status'], 'REVIEW_PENDING')
        self.change_profile()
        self.assertEqual(self.service.review_context_state(case['id']), 'changed')
        self.assertEqual(self.service.get(case['id'])['status'], 'REVIEW_PENDING')
        self.assertEqual(len(self.service.audit_events(case['id'])), 1)

    def test_read_only_context_state_blocks_unbound_prepatch_record(self):
        case = self.register('unbound_display')
        with closing(sqlite3.connect(self.service.db_path)) as con:
            con.execute('UPDATE reviews SET profile_version=NULL, profile_digest=NULL WHERE case_id=?', (case['id'],))
            con.commit()
        self.assertEqual(self.service.review_context_state(case['id']), 'unverified')
        self.assertEqual(self.service.get(case['id'])['status'], 'REVIEW_PENDING')
        self.assertEqual(len(self.service.audit_events(case['id'])), 1)

    def test_existing_prepatch_review_has_no_verified_binding_and_fails_closed(self):
        case = self.register('legacy')
        # Simulate an existing prepatch review migrated into the new schema.
        with closing(sqlite3.connect(self.service.db_path)) as con:
            con.execute('UPDATE reviews SET profile_version=NULL, profile_digest=NULL WHERE case_id=?', (case['id'],))
            con.commit()
        with self.assertRaisesRegex(ReviewConflict, 'lacks verified context binding'):
            self.service.decide(case['id'], 'demo', 'APPROVE_FOLLOWUP', 'Within plan', 'd1')
        self.assertEqual(len(self.service.audit_events(case['id'])), 1)

    def test_existing_prepatch_approved_handoff_cannot_be_republished(self):
        case = self.register('legacypublish')
        self.service.decide(case['id'], 'demo', 'APPROVE_FOLLOWUP', 'Within plan', 'd1')
        with closing(sqlite3.connect(self.service.db_path)) as con:
            con.execute('UPDATE reviews SET profile_version=NULL, profile_digest=NULL WHERE case_id=?', (case['id'],))
            con.commit()
        with self.assertRaisesRegex(ReviewConflict, 'lacks verified context binding'):
            self.service.publish_internal_handoff(case['id'], 'p1')
        self.assertIsNone(self.service.internal_handoff(case['id']))

    def test_profile_database_write_locked_during_review_transaction(self):
        # Proof that a profile update cannot sneak between context validation
        # and decision persistence in a distinct SQLite file.
        with self.service._transaction():
            with closing(sqlite3.connect(str(self.profiles.db_path), timeout=.05)) as second:
                with self.assertRaises(sqlite3.OperationalError):
                    second.execute('BEGIN IMMEDIATE')

    def test_old_handoff_remains_readable_after_subsequent_profile_edit(self):
        case = self.register('history')
        self.service.decide(case['id'], 'demo', 'APPROVE_FOLLOWUP', 'Within plan', 'd1')
        self.service.publish_internal_handoff(case['id'], 'p1')
        self.change_profile()
        self.assertEqual(self.service.get(case['id'])['status'], 'COMPLETED_INTERNAL')
        self.assertEqual(len(self.service.audit_events(case['id'])), 3)
        with self.assertRaisesRegex(ReviewConflict, 'context changed'):
            self.service.publish_internal_handoff(case['id'], 'p1')

if __name__ == '__main__':
    unittest.main()
