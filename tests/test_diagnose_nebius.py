"""The Nebius dry run must stay offline and must not leak secrets or content.

Any attempt to reach the network fails the test, including with a complete,
real-looking Nebius environment present.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch
import urllib.request

import diagnose_nebius as dn
from app import nebius_agent as na

ROOT = Path(__file__).resolve().parents[1]
CANARY_KEY = "CANARY-KEY-DO-NOT-PRINT-7b21"
CANARY_MODEL = "nvidia/canary-nemotron-model-7b21"
FULL_ENV = {
    "CARECANOPY_ENABLE_NEBIUS": "1",
    "NEBIUS_API_KEY": CANARY_KEY,
    "NEBIUS_MODEL_ID": CANARY_MODEL,
    "NEBIUS_BASE_URL": "https://api.tokenfactory.eu-north1.nebius.com/v1",
}


class _NetworkTouched(AssertionError):
    pass


def _fail(*_args, **_kwargs):
    raise _NetworkTouched("dry run attempted network access")


@contextlib.contextmanager
def tripwires():
    """Outer tripwires; the tool's own blocking sits inside these."""
    with patch.object(urllib.request, "urlopen", _fail), patch.object(na, "urlopen", _fail), \
            patch.object(socket.socket, "connect", _fail), patch.object(socket, "create_connection", _fail):
        yield


def run_main(env):
    out = io.StringIO()
    with tripwires(), patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(out):
        code = dn.main()
    return code, out.getvalue()


class DryRunOfflineTests(unittest.TestCase):
    def test_no_network_even_with_full_real_looking_env(self):
        code, _ = run_main(FULL_ENV)
        self.assertEqual(code, 0)

    def test_tool_blocks_network_itself(self):
        with dn.network_blocked():
            with self.assertRaises(dn.NetworkBlocked):
                urllib.request.urlopen("https://api.tokenfactory.nebius.com/v1/models")
            with self.assertRaises(dn.NetworkBlocked):
                socket.create_connection(("127.0.0.1", 9))
            with self.assertRaises(dn.NetworkBlocked):
                na.urlopen("https://api.tokenfactory.nebius.com/v1/models")

    def test_output_never_contains_secrets_or_content(self):
        _, output = run_main(FULL_ENV)
        case = json.loads((ROOT / "data" / "dev_cases.json").read_text(encoding="utf-8"))
        dev04 = next(c for c in case if c["id"] == "DEV04")
        forbidden = [CANARY_KEY, CANARY_MODEL, "Bearer", na._SYSTEM[:40], "CareCanopy, a community",
                     dev04["current_report"][:30], dev04["patient_context"]["baseline"],
                     dev04["patient_context"]["approved_plan"]]
        for text in forbidden:
            self.assertNotIn(text, output)

    def test_launcher_profile_is_labelled_reconstruction_with_unknowns(self):
        _, output = run_main({})
        self.assertIn("RECONSTRUCTION, not historical runtime evidence", output)
        launcher = output.split("[2]", 1)[1]
        self.assertIn("model_id: unknown", launcher)
        self.assertIn("api_key_present: unknown", launcher)

    def test_reports_effective_payload_profiles(self):
        default = dn.describe(dn.launcher_env({}), model_known=False)
        self.assertEqual(default["payload_profile"], "standard_json")
        self.assertEqual(default["endpoint"], "global")
        self.assertEqual(default["response_format"], "present")
        minimal_env = {"CARECANOPY_NEBIUS_JSON_MODE": "0", "CARECANOPY_NEBIUS_MINIMAL_REQUEST": "1"}
        minimal = dn.describe(dn.launcher_env(minimal_env), model_known=False)
        self.assertEqual(minimal["payload_profile"], "minimal")
        self.assertEqual(minimal["payload_keys"], "max_tokens, messages, model")
        eu = dn.describe({"NEBIUS_BASE_URL": FULL_ENV["NEBIUS_BASE_URL"]})
        self.assertEqual(eu["endpoint"], "eu-north1")
        opt_in = dict(minimal_env, CARECANOPY_NEBIUS_MINIMAL_MAX_TOKENS="2048")
        big = dn.describe(dn.launcher_env(opt_in), model_known=False)
        self.assertEqual(big["payload_profile"], "minimal")
        self.assertEqual(big["max_tokens"], 2048)
        self.assertEqual(big["payload_keys"], "max_tokens, messages, model")
        self.assertIn("CARECANOPY_NEBIUS_MINIMAL_MAX_TOKENS", dn.launcher_env(opt_in))
        self.assertEqual(big["chat_template_kwargs"], "absent")
        no_think_env = dict(opt_in, CARECANOPY_NEBIUS_DISABLE_THINKING="1")
        no_think = dn.describe(dn.launcher_env(no_think_env), model_known=False)
        self.assertEqual(no_think["payload_profile"], "minimal_no_thinking")
        self.assertEqual(no_think["payload_keys"], "chat_template_kwargs, max_tokens, messages, model")
        self.assertEqual(no_think["chat_template_kwargs"], "enable_thinking=false")

    def test_model_match_status(self):
        self.assertEqual(dn.describe({"NEBIUS_MODEL_ID": dn.PLAYGROUND_MODEL})["model_id"], "match")
        self.assertEqual(dn.describe({"NEBIUS_MODEL_ID": CANARY_MODEL})["model_id"],
                         "differs from Playground ID")
        self.assertEqual(dn.describe({})["model_id"], "not set")

    def test_rejected_endpoint_stops_before_request(self):
        result = dn.describe({"NEBIUS_BASE_URL": "https://evil.example/v1"})
        self.assertIn("not allowlisted", result["stopped_before_request"])
        self.assertNotIn("payload_profile", result)

    def test_writes_no_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            before = sorted(Path(tmp).rglob("*"))
            cwd = os.getcwd()
            try:
                os.chdir(tmp)
                run_main(FULL_ENV)
            finally:
                os.chdir(cwd)
            self.assertEqual(sorted(Path(tmp).rglob("*")), before)


if __name__ == "__main__":
    unittest.main()
