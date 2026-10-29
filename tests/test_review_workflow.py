"""Offline regression tests. No AWS, patient records, or network required."""
import tempfile
from pathlib import Path
import unittest
from app.review_workflow import ReviewWorkflow, ReviewError, ReviewConflict


class ReviewWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "review.sqlite3"
        self.svc = ReviewWorkflow(self.db)
        self.packet = {"case_id": "P001-001", "escalation_type": "NON_URGENT_PROFESSIONAL", "rationale": "Synthetic follow-up requires review"}
        self.svc.create_review("P001-001", "P001", self.packet)

    def tearDown(self):
        self.tmp.cleanup()

    def test_create_pending(self):
        self.assertEqual(self.svc.get("P001-001")["status"], "REVIEW_PENDING")

    def test_create_idempotent(self):
        self.svc.create_review("P001-001", "P001", self.packet)
        self.assertEqual(len(self.svc.audit_events("P001-001")), 1)

    def test_create_conflict(self):
        with self.assertRaises(ReviewConflict):
            self.svc.create_review("P001-001", "P002", self.packet)

    def test_approve_publish_internal_only(self):
        self.svc.decide("P001-001", "demo-reviewer", "APPROVE_FOLLOWUP", "Within approved plan", "review1")
        out = self.svc.publish_internal_handoff("P001-001", "publish1")
        self.assertEqual(out["status"], "COMPLETED_INTERNAL")
        handoff = self.svc.internal_handoff("P001-001")
        self.assertEqual(handoff["delivery_scope"], "IN_APP_ONLY")
        self.assertEqual(handoff["external_delivery_status"], "NOT_CONFIGURED")

    def test_decision_duplicate_command(self):
        self.svc.decide("P001-001", "demo-reviewer", "APPROVE_FOLLOWUP", "Within approved plan", "review1")
        self.svc.decide("P001-001", "demo-reviewer", "APPROVE_FOLLOWUP", "Within approved plan", "review1")
        self.assertEqual(len(self.svc.audit_events("P001-001")), 2)

    def test_same_command_changed_decision_rejected(self):
        self.svc.decide("P001-001", "demo-reviewer", "APPROVE_FOLLOWUP", "Within approved plan", "review1")
        with self.assertRaises(ReviewConflict):
            self.svc.decide("P001-001", "other-reviewer", "REFER_URGENT", "Changed answer", "review1")

    def test_decision_second_command_blocked(self):
        self.svc.decide("P001-001", "demo-reviewer", "APPROVE_FOLLOWUP", "Within approved plan", "review1")
        with self.assertRaises(ReviewConflict):
            self.svc.decide("P001-001", "other-reviewer", "REFER_URGENT", "Different disposition", "review2")

    def test_restart_persistence(self):
        self.svc.decide("P001-001", "demo-reviewer", "APPROVE_FOLLOWUP", "Within approved plan", "review1")
        after_restart = ReviewWorkflow(self.db)
        self.assertEqual(after_restart.get("P001-001")["status"], "ACTION_PENDING")
        after_restart.publish_internal_handoff("P001-001", "publish1")
        self.assertEqual(self.svc.get("P001-001")["status"], "COMPLETED_INTERNAL")

    def test_publish_twice_no_duplicate(self):
        self.svc.decide("P001-001", "demo-reviewer", "APPROVE_FOLLOWUP", "Within approved plan", "review1")
        self.svc.publish_internal_handoff("P001-001", "publish1")
        self.svc.publish_internal_handoff("P001-001", "publish1")
        self.assertEqual(len(self.svc.audit_events("P001-001")), 3)
        with self.assertRaises(ReviewConflict):
            self.svc.publish_internal_handoff("P001-001", "publish2")

    def test_request_info_no_publish(self):
        self.svc.decide("P001-001", "demo-reviewer", "REQUEST_MORE_INFO", "Need relevant observation", "review1")
        self.assertEqual(self.svc.get("P001-001")["status"], "AWAITING_INFORMATION")
        with self.assertRaises(ReviewConflict):
            self.svc.publish_internal_handoff("P001-001", "publish1")

    def test_urgent_cannot_be_cleared(self):
        self.svc.create_review("U001", "P002", {"case_id": "U001", "escalation_type": "URGENT_MEDICAL"})
        with self.assertRaises(ReviewError):
            self.svc.decide("U001", "demo-reviewer", "APPROVE_FOLLOWUP", "No action", "u-review1")
        self.assertEqual(self.svc.get("U001")["status"], "REVIEW_PENDING")
        self.svc.decide("U001", "demo-reviewer", "REFER_URGENT", "Emergency pathway remains active", "u-review2")
        self.svc.publish_internal_handoff("U001", "u-publish1")
        self.assertEqual(self.svc.internal_handoff("U001")["handoff_kind"], "URGENT_HANDOFF_RECORD")

    def test_command_id_unique_across_cases(self):
        self.svc.create_review("P003-001", "P003", {"case_id": "P003-001", "escalation_type": "SCOPE_BOUNDARY"})
        self.svc.decide("P001-001", "demo-reviewer", "APPROVE_FOLLOWUP", "OK", "same-id")
        with self.assertRaises(ReviewConflict):
            self.svc.decide("P003-001", "demo-reviewer", "APPROVE_FOLLOWUP", "OK", "same-id")

    def test_reusing_decision_command_for_publish_rejected(self):
        self.svc.decide("P001-001", "demo-reviewer", "APPROVE_FOLLOWUP", "Within approved plan", "review1")
        with self.assertRaises(ReviewConflict):
            self.svc.publish_internal_handoff("P001-001", "review1")

    def test_invalid_status_cannot_publish(self):
        with self.assertRaises(ReviewConflict):
            self.svc.publish_internal_handoff("P001-001", "publish1")


if __name__ == "__main__":
    unittest.main()
