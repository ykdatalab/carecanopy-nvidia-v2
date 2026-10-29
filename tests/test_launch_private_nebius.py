"""Launcher applies the verified LIVE profile, fails safely on conflicts, never sends requests.

Offline only: Streamlit is never started (subprocess.call is mocked) and any
network attempt fails the test.
"""
import contextlib
import io
import os
import socket
import unittest
from unittest.mock import patch
import urllib.request

import launch_private_nebius as lp
from app import nebius_agent as na

CANARY_KEY = "CANARY-LAUNCHER-KEY-5d1e"
NEBIUS_VARS = list(lp.VERIFIED_PROFILE) + ["CARECANOPY_ENABLE_NEBIUS", "NEBIUS_MODEL_ID", "NEBIUS_API_KEY"]


def _no_network(*_a, **_k):
    raise AssertionError("launcher attempted network access")


def run_launcher(env=None, answers=("ENABLE", ""), key=CANARY_KEY):
    base = {k: v for k, v in os.environ.items() if k not in NEBIUS_VARS}
    base.update(env or {})
    replies = iter(answers)
    out = io.StringIO()
    with patch.dict(os.environ, base, clear=True), \
            patch("builtins.input", lambda _p="": next(replies)), \
            patch.object(lp, "getpass", lambda _p="": key), \
            patch.object(lp.subprocess, "call", return_value=0) as launch, \
            patch.object(urllib.request, "urlopen", _no_network), patch.object(na, "urlopen", _no_network), \
            patch.object(socket.socket, "connect", _no_network), \
            contextlib.redirect_stdout(out):
        code = lp.main()
    return code, out.getvalue(), launch


class LauncherProfileTests(unittest.TestCase):
    def test_applies_verified_profile_and_launches_locally(self):
        code, text, launch = run_launcher()
        self.assertEqual(code, 0)
        launch.assert_called_once()
        args, kwargs = launch.call_args
        env = kwargs["env"]
        for name, value in lp.VERIFIED_PROFILE.items():
            self.assertEqual(env[name], value)
        self.assertEqual(env["NEBIUS_MODEL_ID"], lp.VERIFIED_MODEL)
        self.assertEqual(env["STREAMLIT_SERVER_ADDRESS"], "127.0.0.1")
        self.assertIn("127.0.0.1", args[0])
        self.assertIn("endpoint: eu-north1 | profile: minimal_no_thinking | max_tokens: 2048 | "
                      "thinking: enable_thinking=false", text)
        self.assertNotIn(CANARY_KEY, text)

    def test_key_removed_from_env_after_run(self):
        _, _, launch = run_launcher()
        self.assertNotIn("NEBIUS_API_KEY", launch.call_args.kwargs["env"])

    def test_conflicting_shell_value_stops_before_prompts(self):
        code, text, launch = run_launcher(env={"CARECANOPY_NEBIUS_DISABLE_THINKING": "0"}, answers=())
        self.assertEqual(code, 3)
        launch.assert_not_called()
        self.assertIn("CARECANOPY_NEBIUS_DISABLE_THINKING", text)
        self.assertIn("Nothing launched", text)

    def test_conflicting_endpoint_stops(self):
        code, _, launch = run_launcher(env={"NEBIUS_BASE_URL": "https://api.tokenfactory.nebius.com/v1"},
                                       answers=())
        self.assertEqual(code, 3)
        launch.assert_not_called()

    def test_matching_inherited_values_are_accepted(self):
        code, _, launch = run_launcher(env={"NEBIUS_BASE_URL": lp.VERIFIED_PROFILE["NEBIUS_BASE_URL"] + "/",
                                            "CARECANOPY_NEBIUS_MINIMAL_REQUEST": "1"})
        self.assertEqual(code, 0)
        launch.assert_called_once()

    def test_existing_safeguards_preserved(self):
        code, _, launch = run_launcher(answers=("no",))
        self.assertEqual(code, 2)
        launch.assert_not_called()
        code, _, launch = run_launcher(answers=("ENABLE", "meta/llama-3"))
        self.assertEqual(code, 2)
        launch.assert_not_called()
        code, _, launch = run_launcher(key="")
        self.assertEqual(code, 2)
        launch.assert_not_called()

    def test_non_verified_model_warns_but_keeps_profile(self):
        code, text, launch = run_launcher(answers=("ENABLE", "nvidia/other-nemotron"))
        self.assertEqual(code, 0)
        self.assertIn("WARNING: model differs", text)
        self.assertEqual(launch.call_args.kwargs["env"]["CARECANOPY_NEBIUS_DISABLE_THINKING"], "1")


if __name__ == "__main__":
    unittest.main()
