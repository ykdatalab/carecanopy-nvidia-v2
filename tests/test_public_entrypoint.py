"""Hosted public entry point: forces offline-only mode even if the flag is unset or '0' (offline)."""
import hashlib
import os
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from app.workspace_state import MODE_OFFLINE

REPO = Path(__file__).resolve().parents[1]
ENTRY = REPO / 'streamlit_public_app.py'
# A fully configured Nebius environment and a flag explicitly set to '0' must not matter.
HOSTILE_ENV = {'CARECANOPY_PUBLIC_DEMO': '0', 'CARECANOPY_ENABLE_NEBIUS': '1',
               'NEBIUS_API_KEY': 'unit-test-placeholder-only',
               'NEBIUS_MODEL_ID': 'nvidia/Nemotron-3_5-Lightning'}


def original_db_hashes():
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((REPO / '.carecanopy_local').glob('*.sqlite3'))}


class PublicEntryPointSourceTests(unittest.TestCase):
    def test_flag_is_forced_before_the_app_runs(self):
        src = ENTRY.read_text(encoding='utf-8')
        self.assertNotIn('from app', src)
        self.assertNotIn('import app', src)
        self.assertLess(src.index("os.environ['CARECANOPY_PUBLIC_DEMO'] = '1'"), src.index('runpy.run_path('))


class PublicEntryPointRunTests(unittest.TestCase):
    """Runs the real entry point headlessly and clicks Analyze; provider calls must never happen."""
    def test_entry_point_is_offline_only_and_never_calls_providers(self):
        from streamlit.testing.v1 import AppTest
        before = original_db_hashes()
        flag_before = os.environ.get('CARECANOPY_PUBLIC_DEMO')
        fail = AssertionError('provider path must not run in the public demo')
        with patch.dict(os.environ, HOSTILE_ENV), \
                patch('app.nebius_agent.route_case_nebius', side_effect=fail) as nebius, \
                patch('app.agent.route_case', side_effect=fail) as aws, \
                patch('app.dynamo_state.DynamoWorkflowStateStore', side_effect=fail) as dynamo:
            at = AppTest.from_file(str(ENTRY), default_timeout=60).run()
            self.assertFalse(at.exception, [e.value for e in at.exception])
            radios = [r for r in at.sidebar.radio if r.label == 'Execution mode']
            self.assertEqual(len(radios), 1)
            self.assertEqual(list(radios[0].options), [MODE_OFFLINE])
            self.assertEqual(sum('PUBLIC DEMO' in i.value for i in at.info), 1)
            analyze = [b for b in at.button if b.label == 'Analyze & route follow-up']
            self.assertEqual(len(analyze), 1)
            analyze[0].click().run()
            self.assertFalse(at.exception, [e.value for e in at.exception])
            result = at.session_state['carecanopy_result']
            self.assertEqual(result['mode'], 'OFFLINE_SCRIPTED')
            self.assertIsNotNone(result['decision'])   # a review exists only for escalated cases
            root = Path(at.session_state['cc_v2_public_demo_data_root'])
        self.addCleanup(shutil.rmtree, root, True)
        for mock in (nebius, aws, dynamo):
            mock.assert_not_called()
        self.assertTrue((root / '.carecanopy_local' / 'review.sqlite3').is_file())
        self.assertNotIn(REPO.resolve(), root.resolve().parents)
        self.assertEqual(os.environ.get('CARECANOPY_PUBLIC_DEMO'), flag_before)   # no leak into other tests
        self.assertEqual(original_db_hashes(), before)


if __name__ == '__main__':
    unittest.main()
