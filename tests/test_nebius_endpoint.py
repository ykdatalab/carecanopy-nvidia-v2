"""Exact Nebius endpoint allowlist and sanitized endpoint labels.

Offline only: every HTTP interaction below is a fake; nothing reaches Nebius.
"""
import io
import json
import os
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from app import nebius_agent as na

GLOBAL = "https://api.tokenfactory.nebius.com/v1"
EU_NORTH1 = "https://api.tokenfactory.eu-north1.nebius.com/v1"
CANARY_KEY = "CANARY-KEY-DO-NOT-PRINT-9f3c"
MODEL = "nvidia/Nemotron-3_5-Lightning"


class EndpointAllowlistTests(unittest.TestCase):
    def test_global_and_eu_north1_are_accepted(self):
        for url, expected in ((GLOBAL, GLOBAL), (GLOBAL + "/", GLOBAL),
                              (EU_NORTH1, EU_NORTH1), (EU_NORTH1 + "/", EU_NORTH1)):
            self.assertEqual(na._validated_endpoint(url), expected, url)

    def test_lookalike_and_unsafe_urls_are_rejected(self):
        rejected = (
            "https://api.tokenfactory.eu-north1.nebius.com.evil.com/v1",
            "https://evil.com/api.tokenfactory.eu-north1.nebius.com/v1",
            "https://eu-north1.nebius.com/v1",
            "https://api.tokenfactory.us-central1.nebius.com/v1",
            "https://tokenfactory.eu-north1.nebius.com/v1",
            "http://api.tokenfactory.eu-north1.nebius.com/v1",
            "https://api.tokenfactory.eu-north1.nebius.com/v2",
            "https://api.tokenfactory.eu-north1.nebius.com/v1/x",
            "https://api.tokenfactory.eu-north1.nebius.com",
            "https://user:pw@api.tokenfactory.eu-north1.nebius.com/v1",
            "https://api.tokenfactory.eu-north1.nebius.com@evil.com/v1",
            "https://api.tokenfactory.eu-north1.nebius.com:443/v1",
            "https://api.tokenfactory.eu-north1.nebius.com/v1?x=1",
            "https://api.tokenfactory.eu-north1.nebius.com/v1#frag",
            "https://API.TOKENFACTORY.EU-NORTH1.NEBIUS.COM/v1",
            " https://api.tokenfactory.eu-north1.nebius.com/v1",
            "https://api.tokenfactory.eu-north1.nebius.com/v1 ",
            "https://api.tokenfactory.eu-north1.nebius.com./v1",
        )
        for url in rejected:
            with self.assertRaisesRegex(na.NebiusRouteError, "not allowlisted", msg=url):
                na._validated_endpoint(url)

    def test_global_default_is_unchanged(self):
        self.assertEqual(na.DEFAULT_BASE_URL, GLOBAL)
        env = {"CARECANOPY_ENABLE_NEBIUS": "1", "NEBIUS_API_KEY": CANARY_KEY, "NEBIUS_MODEL_ID": MODEL}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(na._config()[2], GLOBAL)

    def test_eu_north1_is_opt_in_via_base_url(self):
        env = {"CARECANOPY_ENABLE_NEBIUS": "1", "NEBIUS_API_KEY": CANARY_KEY,
               "NEBIUS_MODEL_ID": MODEL, "NEBIUS_BASE_URL": EU_NORTH1 + "/"}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(na._config()[2], EU_NORTH1)

    def test_endpoint_labels_are_controlled(self):
        self.assertEqual(na._endpoint_label(GLOBAL + "/chat/completions"), "global")
        self.assertEqual(na._endpoint_label(EU_NORTH1 + "/chat/completions"), "eu-north1")
        self.assertEqual(na._endpoint_label("https://evil.example/v1"), "other")


class EndpointDiagnosticLabelTests(unittest.TestCase):
    def _http_400_message(self, base):
        url = base + "/chat/completions"
        body = json.dumps({"error": {"type": "invalid_request_error", "message": "secret-ish text"}})
        error = HTTPError(url, 400, "provider message MUST NOT DISPLAY", {}, io.BytesIO(body.encode()))
        payload = {"model": MODEL, "messages": [{"role": "user", "content": "SYNTHETIC"}], "max_tokens": 512}
        with patch.object(na, "urlopen", side_effect=error):
            with self.assertRaises(na.NebiusRouteError) as raised:
                na._post_one(CANARY_KEY, url, payload)
        return str(raised.exception)

    def test_http_400_reports_endpoint_label_only(self):
        for base, label in ((GLOBAL, "endpoint=global"), (EU_NORTH1, "endpoint=eu-north1")):
            message = self._http_400_message(base)
            self.assertIn(label, message)
            for leak in (CANARY_KEY, "Bearer", "https://", "tokenfactory", "secret-ish",
                         "MUST NOT DISPLAY"):
                self.assertNotIn(leak, message)


if __name__ == "__main__":
    unittest.main()
