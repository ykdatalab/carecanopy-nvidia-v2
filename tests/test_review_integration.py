"""Offline integration tests. No AWS, Nebius, Streamlit or network access."""
from __future__ import annotations
import tempfile
from pathlib import Path
import unittest
from app.review_workflow import ReviewWorkflow, ReviewError
from app.review_integration import scripted_synthetic_replay, register_review, review_store


class IntegratedReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.svc = review_store(self.tmp.name)
        from app.synthetic_profiles import SyntheticPatientStore
        store = SyntheticPatientStore(Path(self.tmp.name)/'.carecanopy_local'/'synthetic_patients.sqlite3')
        store.seed_from_fixture({
            'id': 'DEV04', 'patient_context': {
                'age': 69, 'weeks_post_stroke': 11,
                'baseline': 'Uses quad cane',
                'approved_plan': 'Walking and balance programme',
            },
        })

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def a_case(route='ESCALATE', escalation='NON_URGENT_PROFESSIONAL', report='New shoulder pain'):
        return {
            'id':'DEV04_UI_123', 'source_case_id':'DEV04', 'synthetic_patient_id':'SYN-DEV04',
            'expected_route':route, 'expected_escalation_type':escalation,
            'patient_context': {'age': 69, 'weeks_post_stroke':11, 'baseline':'Uses quad cane',
                                'approved_plan':'Walking and balance programme'},
            'current_report':report,
            'synthetic_context_version':1,
        }

    def test_scripted_result_is_clearly_not_ai(self):
        result=scripted_synthetic_replay(self.a_case())
        self.assertEqual(result['mode'],'OFFLINE_SCRIPTED')
        self.assertEqual(result['actions']['route'],'ESCALATE')

    def test_automatic_review_created_from_action_packet(self):
        case=self.a_case()
        result=scripted_synthetic_replay(case)
        review=register_review(result,case,self.svc)
        self.assertEqual(review['status'],'REVIEW_PENDING')
        self.assertEqual(review['case_id'],case['id'])
        self.assertEqual(review['patient_id'],'SYN-DEV04')

    def test_repeated_registration_is_idempotent(self):
        case=self.a_case()
        result=scripted_synthetic_replay(case)
        register_review(result,case,self.svc)
        register_review(result,case,self.svc)
        self.assertEqual(len(self.svc.audit_events(case['id'])),1)

    def test_approval_then_internal_handoff(self):
        case=self.a_case()
        register_review(scripted_synthetic_replay(case),case,self.svc)
        self.svc.decide(case['id'],'demo-reviewer','APPROVE_FOLLOWUP',
                        'Follow up inside approved plan','integrated:decision:'+case['id'])
        self.svc.publish_internal_handoff(case['id'],'integrated:publish:'+case['id'])
        self.assertEqual(self.svc.get(case['id'])['status'],'COMPLETED_INTERNAL')
        self.assertEqual(self.svc.internal_handoff(case['id'])['external_delivery_status'],'NOT_CONFIGURED')
        self.assertEqual(len(self.svc.audit_events(case['id'])),3)

    def test_restarting_review_store_preserves_completion(self):
        case=self.a_case()
        register_review(scripted_synthetic_replay(case),case,self.svc)
        self.svc.decide(case['id'],'demo','APPROVE_FOLLOWUP','Recorded', 'decision:'+case['id'])
        other=ReviewWorkflow(Path(self.tmp.name)/'.carecanopy_local'/'review.sqlite3')
        other.publish_internal_handoff(case['id'],'publish:'+case['id'])
        self.assertEqual(self.svc.get(case['id'])['status'],'COMPLETED_INTERNAL')

    def test_urgent_cannot_be_approved(self):
        case=self.a_case(escalation='URGENT_MEDICAL',report='Suddenly developed new weakness in the left arm')
        res=scripted_synthetic_replay(case)
        register_review(res,case,self.svc)
        with self.assertRaises(ReviewError):
            self.svc.decide(case['id'],'demo','APPROVE_FOLLOWUP','Looks fine','u1')

    def test_clarify_does_not_create_review(self):
        case=self.a_case(route='CLARIFY',escalation=None, report='Walking seems off this week')
        result=scripted_synthetic_replay(case)
        self.assertIsNone(register_review(result,case,self.svc))
        self.assertEqual(len(self.svc.list_reviews()),0)

    def test_routine_does_not_create_review(self):
        case=self.a_case(route='ROUTINE',escalation=None,report='Completed walking sessions; no new issues')
        result=scripted_synthetic_replay(case)
        self.assertIsNone(register_review(result,case,self.svc))

    def test_refuses_missing_review_packet(self):
        case=self.a_case()
        result=scripted_synthetic_replay(case)
        result['actions']['review_packet']=None
        with self.assertRaisesRegex(ReviewError,'no review packet'):
            register_review(result,case,self.svc)

    def test_refuses_case_id_mismatch(self):
        case=self.a_case()
        result=scripted_synthetic_replay(case)
        result['actions']['case_id']='OTHER'
        with self.assertRaisesRegex(ReviewError,'Case ID mismatch'):
            register_review(result,case,self.svc)


if __name__=='__main__':
    unittest.main()
