"""Safe, offline-only HTTP 400 diagnostics and effective request-profile tests.

Zero Nebius API requests. HTTPError objects and completion responses are fakes.
"""
import io
import json
import os
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from app import nebius_agent as na


URL = "https://api.tokenfactory.nebius.com/v1/chat/completions"
MODEL = "nvidia/Nemotron-3_5-Lightning"


def fake_http(code, payload):
    if isinstance(payload, (dict, list)):
        payload = json.dumps(payload).encode("utf-8")
    return HTTPError(URL, code, "provider message MUST NOT DISPLAY", {}, io.BytesIO(payload))


def payload(minimal=True):
    p = {"model": MODEL, "messages": [{"role": "user", "content": "SYNTHETIC"}], "max_tokens": 512}
    if not minimal:
        p.update({"store": False, "temperature": 0, "max_tokens": 2048, "response_format": {"type": "json_object"}})
    return p


def call_error(error, outgoing=None):
    with patch.object(na, "urlopen", side_effect=error) as mock:
        with unittest.TestCase().assertRaises(na.NebiusRouteError) as raised:
            na._post_one("DUMMY_NOT_A_REAL_KEY", URL, outgoing or payload())
    return str(raised.exception), mock.call_count


class HTTP400DiagnosticsTests(unittest.TestCase):
    def test_canonical_openai_error_metadata(self):
        result, calls = call_error(fake_http(400, {"error": {
            "type": "invalid_request_error", "code": "unsupported_parameter",
            "param": "body.response_format", "message": "response_format not supported",
        }}), payload(minimal=False))
        self.assertEqual(calls, 1)
        self.assertIn("request_profile=standard_json", result)
        self.assertIn("json_format_requested=yes", result)
        self.assertIn("provider_type=invalid_request_error", result)
        self.assertIn("provider_code=unsupported_parameter", result)
        self.assertIn("provider_param=response_format", result)
        self.assertIn("message_mentions=response_format", result)

    def test_minimal_mode_derived_from_actual_payload_not_env(self):
        with patch.dict(os.environ, {"CARECANOPY_NEBIUS_MINIMAL_REQUEST": "0", "CARECANOPY_NEBIUS_JSON_MODE": "1"}):
            result, calls = call_error(fake_http(400, {"error": {"param": "max_tokens"}}), payload(minimal=True))
        self.assertEqual(calls, 1)
        self.assertIn("request_profile=minimal", result)
        self.assertIn("json_format_requested=no", result)
        self.assertIn("provider_param=max_tokens", result)

    def test_unknown_nested_error_is_never_echoed(self):
        secret = "sk-EXAMPLE_SECRET_DO_NOT_LOG"
        patient_text = "Synthetic patient shoulder pain details private test"
        result, _ = call_error(fake_http(400, {"error": {
            "code": secret, "type": patient_text, "param": secret,
            "message": f"Please don't print {secret} {patient_text} in response_format",
        }}))
        self.assertNotIn(secret, result)
        self.assertNotIn(patient_text, result)
        self.assertNotIn("Please", result)
        self.assertIn("provider_code=other", result)
        self.assertIn("provider_type=other", result)
        self.assertIn("provider_param=other", result)
        self.assertIn("message_mentions=response_format", result)

    def test_pydantic_detail_loc_yields_approved_parameter(self):
        result, _ = call_error(fake_http(400, {"detail": [{
            "loc": ["body", "max_tokens"], "msg": "invalid max_tokens", "type": "value_error"
        }]}))
        self.assertIn("provider_param=max_tokens", result)
        self.assertIn("message_mentions=max_tokens", result)

    def test_chat_template_kwargs_rejection_is_labelled_without_leaks(self):
        outgoing = dict(payload(minimal=True), max_tokens=2048,
                        chat_template_kwargs={"enable_thinking": False})
        result, calls = call_error(fake_http(400, {"error": {
            "type": "invalid_request_error", "code": "extra_forbidden",
            "param": "body.chat_template_kwargs",
            "message": "chat_template_kwargs is not permitted sk-LEAK-CANARY",
        }}), outgoing)
        self.assertEqual(calls, 1)
        self.assertIn("request_profile=minimal_no_thinking", result)
        self.assertIn("provider_param=chat_template_kwargs", result)
        self.assertIn("message_mentions=chat_template_kwargs", result)
        self.assertNotIn("sk-LEAK-CANARY", result)
        self.assertNotIn("not permitted", result)

    def test_unexpected_loc_is_suppressed(self):
        result, _ = call_error(fake_http(400, {"detail": [{"loc": ["body", "private_email"], "msg": ""}]}))
        self.assertIn("provider_param=unavailable", result)
        self.assertNotIn("private_email", result)

    def test_non_json_provider_body_is_never_printed(self):
        result, _ = call_error(fake_http(400, b"<html>Secret key sk-UNSAFE</html>"))
        self.assertIn("error_body=non_json", result)
        self.assertNotIn("sk-UNSAFE", result)

    def test_oversized_provider_body_bounded(self):
        result, _ = call_error(fake_http(400, b"x" * (na._HTTP_ERROR_MAX_BYTES + 300)))
        self.assertIn("error_body=oversized", result)
        self.assertNotIn("xxxx", result)

    def test_json_array_body_safe(self):
        result, _ = call_error(fake_http(400, ["sensitive", "error"]))
        self.assertIn("error_body=unexpected_shape", result)
        self.assertNotIn("sensitive", result)

    def test_400_message_does_not_misattribute_json_mode(self):
        result, _ = call_error(fake_http(400, {"error": {"message": "Malformed request"}}))
        self.assertNotIn("potentially JSON mode", result)
        self.assertIn("message_mentions=none", result)
        self.assertIn("no automatic retry", result)

    def test_401_preserves_existing_protection(self):
        result, calls = call_error(fake_http(401, {"error": {"message": "secret"}}))
        self.assertEqual(calls, 1)
        self.assertIn("Nebius access/billing blocked (HTTP 401)", result)
        self.assertNotIn("secret", result)

    def test_direct_public_record_fields_use_safe_profile(self):
        meta = na.NebiusProvenance("Nebius Token Factory", MODEL, None, 10, 20, 1.2,
                                   request_profile="minimal", json_format_requested=False)
        record = meta.public_record()
        self.assertEqual(record["request_profile"], "minimal")
        self.assertIs(record["json_format_requested"], False)

    def test_unknown_param_and_errors_are_fixed_labels(self):
        result, _ = call_error(fake_http(400, {"error": {
            "param": "unknown.property.here.malicious", "type": "none-of-your-business",
            "code": "user@example.org", "message": "patient@example.org",
        }}))
        self.assertIn("provider_param=other", result)
        self.assertIn("provider_type=other", result)
        self.assertIn("provider_code=other", result)
        self.assertNotIn("@example.org", result)


if __name__ == "__main__":
    unittest.main()
