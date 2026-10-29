"""Offline checks for synthetic-only longitudinal context; NOT clinical evaluation."""
import copy
import tempfile
import unittest
from pathlib import Path

from app.synthetic_profiles import (
    SyntheticPatientStore, SyntheticProfileError, SyntheticProfileConflict,
    case_with_demo_profile, review_predates_demo_context_edit,
)


FIXTURE = {
    "id": "DEV04",
    "patient_context": {
        "age": 69, "weeks_post_stroke": 11, "baseline": "Walks with a quad cane",
        "approved_plan": "Walking and balance program",
    },
    "current_report": "New shoulder pain",
}


class SyntheticProfileTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "synthetic.sqlite3"
        self.store = SyntheticPatientStore(self.path)
        self.profile = self.store.seed_from_fixture(FIXTURE)

    def test_seed_has_explicit_synthetic_id_and_fixture_context(self):
        self.assertEqual(self.profile["patient_id"], "SYN-DEV04")
        self.assertEqual(self.profile["age"], 69)
        self.assertEqual(self.profile["approved_plan"], "Walking and balance program")

    def test_seed_is_idempotent(self):
        self.store.seed_from_fixture(FIXTURE)
        self.assertEqual(len(self.store.audit("SYN-DEV04")), 1)

    def test_profile_update_is_persistent_across_restart(self):
        self.store.update_demo_context("SYN-DEV04", age=70, weeks_post_stroke=12,
                                       baseline="Uses a quad cane")
        reopened = SyntheticPatientStore(self.path)
        self.assertEqual(reopened.get("SYN-DEV04")["baseline"], "Uses a quad cane")
        self.assertEqual(reopened.get("SYN-DEV04")["age"], 70)

    def test_old_review_is_flagged_after_context_change(self):
        self.assertFalse(review_predates_demo_context_edit(self.profile, {"created_at": "2026-10-01T00:00:00+00:00"}))
        self.store.update_demo_context("SYN-DEV04", age=70, weeks_post_stroke=12,
                                       baseline="Uses a quad cane")
        changed = self.store.get("SYN-DEV04")
        self.assertTrue(review_predates_demo_context_edit(changed, {"created_at": "2026-10-01T00:00:00+00:00"}))
        self.assertFalse(review_predates_demo_context_edit(changed, {"created_at": "2026-12-01T00:00:00+00:00"}))

    def test_seed_after_update_does_not_reset_user_changes(self):
        self.store.update_demo_context("SYN-DEV04", age=70, weeks_post_stroke=12,
                                       baseline="Uses a quad cane")
        self.assertEqual(self.store.seed_from_fixture(FIXTURE)["age"], 70)

    def test_approved_plan_cannot_be_changed_via_update(self):
        with self.assertRaises(TypeError):
            self.store.update_demo_context("SYN-DEV04", age=70, weeks_post_stroke=12,
                baseline="Uses a quad cane", approved_plan="Double exercise intensity")
        self.assertEqual(self.store.get("SYN-DEV04")["approved_plan"], FIXTURE["patient_context"]["approved_plan"])

    def test_fixture_plan_mismatch_rejected(self):
        different = copy.deepcopy(FIXTURE)
        different["patient_context"]["approved_plan"] = "Unverified new plan"
        with self.assertRaises(SyntheticProfileConflict):
            self.store.seed_from_fixture(different)

    def test_invalid_age_and_baseline_rejected(self):
        for invalid_age in (True, -1, 121, "69"):
            with self.subTest(invalid_age=invalid_age), self.assertRaises(SyntheticProfileError):
                self.store.update_demo_context("SYN-DEV04", age=invalid_age,
                    weeks_post_stroke=11, baseline="Uses a cane")
        with self.assertRaises(SyntheticProfileError):
            self.store.update_demo_context("SYN-DEV04", age=69,
                weeks_post_stroke=11, baseline=" ")

    def test_only_dev_synthetic_cases_accepted(self):
        fixture = copy.deepcopy(FIXTURE)
        fixture["id"] = "PATIENT-REAL"
        with self.assertRaises(SyntheticProfileError):
            self.store.seed_from_fixture(fixture)

    def test_observation_is_saved_and_survives_restart(self):
        self.store.save_observation("SYN-DEV04", case_id="DEV04_UI_1",
            report="Pain started yesterday", source_mode="OFFLINE_SCRIPTED")
        after_restart = SyntheticPatientStore(self.path)
        self.assertEqual(after_restart.observations("SYN-DEV04")[0]["report"], "Pain started yesterday")

    def test_observation_retry_is_idempotent(self):
        kwargs = dict(case_id="DEV04_UI_1", report="Pain", source_mode="OFFLINE_SCRIPTED")
        a = self.store.save_observation("SYN-DEV04", **kwargs)
        b = self.store.save_observation("SYN-DEV04", **kwargs)
        self.assertEqual(a["observation_id"], b["observation_id"])
        self.assertEqual(len(self.store.observations("SYN-DEV04")), 1)

    def test_observation_conflicting_reuse_rejected(self):
        self.store.save_observation("SYN-DEV04", case_id="DEV04_UI_1",
            report="Pain", source_mode="OFFLINE_SCRIPTED")
        with self.assertRaises(SyntheticProfileConflict):
            self.store.save_observation("SYN-DEV04", case_id="DEV04_UI_1",
                report="Altered", source_mode="OFFLINE_SCRIPTED")

    def test_cross_patient_observation_rejected(self):
        with self.assertRaises(SyntheticProfileConflict):
            self.store.save_observation("SYN-DEV04", case_id="DEV01_UI_1",
                report="Wrong patient", source_mode="OFFLINE_SCRIPTED")

    def test_cross_patient_histories_are_isolated(self):
        other = copy.deepcopy(FIXTURE)
        other["id"] = "DEV01"
        self.store.seed_from_fixture(other)
        self.store.save_observation("SYN-DEV04", case_id="DEV04_UI_1",
            report="Pain", source_mode="OFFLINE_SCRIPTED")
        self.assertEqual(self.store.observations("SYN-DEV01"), [])

    def test_profile_audit_does_not_duplicate_same_content(self):
        self.store.update_demo_context("SYN-DEV04", age=69, weeks_post_stroke=11,
                                       baseline="Walks with a quad cane")
        self.assertEqual(len(self.store.audit("SYN-DEV04")), 1)
        self.store.update_demo_context("SYN-DEV04", age=70, weeks_post_stroke=11,
                                       baseline="Walks with a quad cane")
        self.assertEqual(len(self.store.audit("SYN-DEV04")), 2)

    def test_routing_case_uses_saved_profile_without_mutating_fixture(self):
        self.store.update_demo_context("SYN-DEV04", age=70, weeks_post_stroke=12,
                                       baseline="Uses a quad cane")
        updated = case_with_demo_profile(FIXTURE, self.store.get("SYN-DEV04"))
        self.assertEqual(updated["patient_context"]["baseline"], "Uses a quad cane")
        self.assertEqual(updated["synthetic_patient_id"], "SYN-DEV04")
        self.assertEqual(FIXTURE["patient_context"]["age"], 69)

    def test_prior_observations_are_explicitly_unverified(self):
        saved = self.store.save_observation('SYN-DEV04', case_id='DEV04_UI_1',
            report='Previous shoulder pain', source_mode='OFFLINE_SCRIPTED')
        case = case_with_demo_profile(FIXTURE, self.profile, [saved])
        history = case['patient_context']['previous_unverified_reports']
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]['report'], 'Previous shoulder pain')
        self.assertNotIn('clinical_decision', history[0])

    def test_other_patient_observation_cannot_enter_context(self):
        wrong = {'patient_id': 'SYN-DEV01', 'report': 'Wrong patient', 'created_at': ''}
        with self.assertRaises(SyntheticProfileConflict):
            case_with_demo_profile(FIXTURE, self.profile, [wrong])

    def test_empty_history_keeps_fixture_context_unchanged(self):
        case = case_with_demo_profile(FIXTURE, self.profile, [])
        self.assertNotIn('previous_unverified_reports', case['patient_context'])

    def test_routing_case_mismatch_rejected(self):
        with self.assertRaises(SyntheticProfileConflict):
            case_with_demo_profile({**FIXTURE, "id": "DEV01"}, self.profile)

    def test_report_excludes_external_delivery_or_clinical_claim(self):
        event = self.store.save_observation("SYN-DEV04", case_id="DEV04_UI_1",
            report="Demo test", source_mode="OFFLINE_SCRIPTED")
        self.assertEqual(event["source_mode"], "OFFLINE_SCRIPTED")
        self.assertNotIn("delivered", event)
        self.assertNotIn("clinically_approved", event)


if __name__ == "__main__":
    unittest.main()
