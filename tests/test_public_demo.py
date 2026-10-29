"""Public demo mode: offline-only execution and per-visitor temporary SQLite (offline tests)."""
import hashlib
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from app.review_integration import register_review, review_store, scripted_synthetic_replay
from app.synthetic_profiles import SyntheticPatientStore, case_with_demo_profile
from app.workspace_state import (MODE_AWS, MODE_NEBIUS, MODE_OFFLINE, PublicDemoBlocked,
                                 assert_mode_allowed, execution_mode_options, public_demo_enabled,
                                 workspace_data_root)

REPO = Path(__file__).resolve().parents[1]
FULL_NEBIUS = {'CARECANOPY_ENABLE_NEBIUS': '1', 'NEBIUS_API_KEY': 'unit-test-placeholder-only',
               'NEBIUS_MODEL_ID': 'nvidia/Nemotron-3_5-Lightning'}
FIXTURE = {'id': 'DEV04', 'expected_route': 'ESCALATE',
           'expected_escalation_type': 'NON_URGENT_PROFESSIONAL',
           'current_report': 'New shoulder pain affecting daily life',
           'patient_context': {'age': 69, 'weeks_post_stroke': 11, 'baseline': 'Uses quad cane',
                               'approved_plan': 'Walking and balance program'}}


def original_db_hashes():
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((REPO / '.carecanopy_local').glob('*.sqlite3'))}


class PublicDemoFlagTests(unittest.TestCase):
    def test_flag_values(self):
        for value in (None, '', '0', ' 0 '):
            env = {} if value is None else {'CARECANOPY_PUBLIC_DEMO': value}
            self.assertFalse(public_demo_enabled(env), value)
        for value in ('1', ' 1 ', 'true', 'yes'):   # anything else fails closed to public
            self.assertTrue(public_demo_enabled({'CARECANOPY_PUBLIC_DEMO': value}), value)

    def test_public_demo_offers_offline_only_even_when_nebius_configured(self):
        options, available = execution_mode_options({**FULL_NEBIUS, 'CARECANOPY_PUBLIC_DEMO': '1'})
        self.assertEqual(options, [MODE_OFFLINE])
        self.assertFalse(available)

    def test_default_mode_options_unchanged(self):
        self.assertEqual(execution_mode_options(FULL_NEBIUS), ([MODE_OFFLINE, MODE_NEBIUS, MODE_AWS], True))
        self.assertEqual(execution_mode_options({}), ([MODE_OFFLINE, MODE_AWS], False))

    def test_execution_path_blocks_live_providers_in_public_demo(self):
        env = {'CARECANOPY_PUBLIC_DEMO': '1'}
        with self.assertRaises(PublicDemoBlocked):
            assert_mode_allowed(env, offline_replay=False, nebius_live=True)    # Nebius
        with self.assertRaises(PublicDemoBlocked):
            assert_mode_allowed(env, offline_replay=False, nebius_live=False)   # AWS v1
        assert_mode_allowed(env, offline_replay=True, nebius_live=False)

    def test_default_mode_execution_path_unchanged(self):
        for offline, live in ((True, False), (False, True), (False, False)):
            assert_mode_allowed({}, offline_replay=offline, nebius_live=live)


class PublicDemoStorageTests(unittest.TestCase):
    def test_default_mode_uses_project_folder(self):
        state = {}
        self.assertEqual(workspace_data_root({}, REPO, state), REPO)
        self.assertEqual(state, {})

    def test_public_root_is_per_session_and_stable_within_session(self):
        env = {'CARECANOPY_PUBLIC_DEMO': '1'}
        a, b = {}, {}
        root_a = workspace_data_root(env, REPO, a)
        root_b = workspace_data_root(env, REPO, b)
        self.addCleanup(lambda: [p.rmdir() for p in (root_a, root_b) if p.is_dir() and not any(p.iterdir())])
        self.assertNotEqual(root_a, root_b)
        self.assertEqual(workspace_data_root(env, REPO, a), root_a)
        for root in (root_a, root_b):
            self.assertNotEqual(root.resolve(), REPO.resolve())
            self.assertNotIn(REPO.resolve(), root.resolve().parents)

    def test_public_root_recreated_if_removed(self):
        env, state = {'CARECANOPY_PUBLIC_DEMO': '1'}, {}
        first = workspace_data_root(env, REPO, state)
        first.rmdir()
        second = workspace_data_root(env, REPO, state)
        self.addCleanup(second.rmdir)
        self.assertTrue(second.is_dir())

    def test_two_visitors_never_share_reviews_and_flow_links_within_session(self):
        before = original_db_hashes()
        env = {'CARECANOPY_PUBLIC_DEMO': '1'}
        roots = []
        with tempfile.TemporaryDirectory() as base:
            make = lambda prefix='carecanopy_public_': tempfile.mkdtemp(prefix=prefix, dir=base)
            stores = []
            for state in ({}, {}):
                root = workspace_data_root(env, REPO, state, mkdtemp=make)
                roots.append(root)
                profiles = SyntheticPatientStore(root / '.carecanopy_local' / 'synthetic_patients.sqlite3')
                profiles.seed_from_fixture(FIXTURE)
                stores.append((profiles, review_store(root)))
            (profiles_a, svc_a), (_, svc_b) = stores
            case = case_with_demo_profile(FIXTURE, profiles_a.get('SYN-DEV04'))
            case.update({'id': 'DEV04_UI_public_a', 'source_case_id': 'DEV04'})
            review = register_review(scripted_synthetic_replay(case), case, svc_a)
            # Patient & AI -> Clinical Review -> Outcomes & Audit inside visitor A's session.
            self.assertEqual(svc_a.get(review['case_id'])['status'], 'REVIEW_PENDING')
            svc_a.decide(review['case_id'], 'Synthetic reviewer', 'APPROVE_FOLLOWUP',
                         'Keep the existing approved plan.', 'cmd-public-a-1')
            svc_a.publish_internal_handoff(review['case_id'], 'cmd-public-a-2')
            self.assertEqual(svc_a.get(review['case_id'])['status'], 'COMPLETED_INTERNAL')
            self.assertEqual(len(svc_a.list_reviews()), 1)
            # Visitor B sees nothing from visitor A.
            self.assertEqual(svc_b.list_reviews(), [])
            self.assertIsNone(svc_b.get(review['case_id']))
            self.assertNotEqual(roots[0], roots[1])
        self.assertEqual(original_db_hashes(), before)


class PublicDemoAppSourceTests(unittest.TestCase):
    """Static checks that the Streamlit app routes storage and execution through the guards."""
    def setUp(self):
        self.src = (REPO / 'streamlit_app.py').read_text(encoding='utf-8')

    def test_storage_uses_data_root_only(self):
        self.assertNotIn("review_store(BASE_DIR)", self.src)
        self.assertNotIn("BASE_DIR / '.carecanopy_local'", self.src)
        self.assertEqual(self.src.count('review_store(DATA_ROOT)'), 2)

    def test_run_pipeline_guard_precedes_any_provider_call(self):
        body = self.src.split('def run_pipeline(', 1)[1]
        guard = body.index('assert_mode_allowed(os.environ')
        for call in ('route_case_nebius(case)', 'route_case(case)', 'DynamoWorkflowStateStore()'):
            self.assertLess(guard, body.index(call), call)


class PublicDemoAppRunTests(unittest.TestCase):
    """Runs the real Streamlit script headlessly in public mode; no provider call is possible."""
    def test_public_app_renders_offline_only_with_separate_storage(self):
        from streamlit.testing.v1 import AppTest
        before = original_db_hashes()
        env = {'CARECANOPY_PUBLIC_DEMO': '1', **FULL_NEBIUS}
        with patch.dict(os.environ, env):
            runs = []
            for _ in range(2):
                at = AppTest.from_file(str(REPO / 'streamlit_app.py'), default_timeout=60).run()
                self.assertFalse(at.exception, [e.value for e in at.exception])
                runs.append(at)
        for at in runs:
            radios = [r for r in at.sidebar.radio if r.label == 'Execution mode']
            self.assertEqual(len(radios), 1)
            self.assertEqual(list(radios[0].options), [MODE_OFFLINE])
            self.assertEqual(sum('PUBLIC DEMO' in i.value for i in at.info), 1)
            captions = ' '.join(c.value for c in list(at.caption) + list(at.sidebar.caption))
            for stale in ('AWS v1', 'DynamoDB', 'OFFLINE REPLAY · NO AI', 'live option not configured',
                          'runtime is not active'):
                self.assertNotIn(stale, captions)
        roots = [Path(at.session_state['cc_v2_public_demo_data_root']) for at in runs]
        for root in roots:
            self.addCleanup(shutil.rmtree, root, True)
        self.assertNotEqual(roots[0], roots[1])
        for root in roots:
            self.assertNotIn(REPO.resolve(), root.resolve().parents)
        self.assertEqual(original_db_hashes(), before)


if __name__ == '__main__':
    unittest.main()
