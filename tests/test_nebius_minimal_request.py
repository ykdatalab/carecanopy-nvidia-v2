"""Minimal (probe-compatible) Nebius request profile regression tests (offline)."""
import json
import os
import unittest
from unittest.mock import patch
from app.nebius_agent import route_case_nebius, NebiusRouteError


def _case():
    return {
        "synthetic_patient_id": "SYN-DEV04",
        "current_report": "The patient reports worsening synthetic shoulder pain affecting daily activities.",
        "patient_context": {
            "age": 70, "weeks_post_stroke": 12,
            "baseline": "Walks with a quad cane",
            "approved_plan": "Walking and balance programme",
            "previous_unverified_reports": [],
        },
    }


def _response():
    return {
        "model": "nvidia/Nemotron-3_5-Lightning",
        "choices": [{
            "finish_reason": "stop",
            "message": {"content": json.dumps({
                "route": "ESCALATE",
                "escalation_type": "NON_URGENT_PROFESSIONAL",
                "rationale": "New worsening symptoms need professional assessment.",
                "evidence": ["Synthetic symptom report"],
                "missing_information": [], "protocol_triggers": [],
                "immediate_medical_guidance": False,
                "specialist_notification": False,
                "create_review_packet": True
            })},
        }],
        "usage": {"prompt_tokens": 100, "completion_tokens": 100},
    }


class NebiusMinimalRequestTests(unittest.TestCase):
    def setUp(self):
        env = {
            "CARECANOPY_ENABLE_NEBIUS": "1",
            "NEBIUS_API_KEY": "unit-test-placeholder-only",
            "NEBIUS_MODEL_ID": "nvidia/Nemotron-3_5-Lightning",
            "NEBIUS_BASE_URL": "https://api.tokenfactory.nebius.com/v1",
            "CARECANOPY_NEBIUS_JSON_MODE": "0",
            "CARECANOPY_NEBIUS_MINIMAL_REQUEST": "1",
        }
        self.env_patch = patch.dict(os.environ, env)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    def test_minimal_payload_and_response_still_validated(self):
        seen = []
        def t(k,u,p):
            seen.append(p)
            return _response()
        route_case_nebius(_case(), transport=t)
        self.assertEqual(len(seen), 1)
        self.assertEqual(set(seen[0]), {"model", "messages", "max_tokens"})
        self.assertEqual(seen[0]["max_tokens"], 512)

    def test_rejects_invalid_json_without_retry(self):
        count = []
        def t(k,u,p):
            count.append(True)
            return {"choices":[{"finish_reason":"stop", "message":{"content":"not JSON"}}]}
        with self.assertRaises(NebiusRouteError):
            route_case_nebius(_case(), transport=t)
        self.assertEqual(len(count), 1)

    def test_rejects_non_normal_finish_without_retry(self):
        count = []
        def t(k,u,p):
            count.append(True)
            x = _response()
            x["choices"][0]["finish_reason"] = "length"
            return x
        with self.assertRaises(NebiusRouteError):
            route_case_nebius(_case(), transport=t)
        self.assertEqual(len(count), 1)

    def test_blocks_conflicting_json_mode_before_transport(self):
        with patch.dict(os.environ, {"CARECANOPY_NEBIUS_JSON_MODE":"1"}):
            def t(*a):
                raise AssertionError("network must not be called")
            with self.assertRaises(NebiusRouteError):
                route_case_nebius(_case(), transport=t)

    def test_rejects_invalid_minimal_profile_value(self):
        with patch.dict(os.environ, {"CARECANOPY_NEBIUS_MINIMAL_REQUEST":"yes"}):
            with self.assertRaises(NebiusRouteError):
                route_case_nebius(_case(), transport=lambda *a: _response())


class MinimalMaxTokensOptInTests(unittest.TestCase):
    """Opt-in 2048 output budget for the minimal profile; default stays 512."""

    BASE_ENV = {
        "CARECANOPY_ENABLE_NEBIUS": "1",
        "NEBIUS_API_KEY": "unit-test-placeholder-only",
        "NEBIUS_MODEL_ID": "nvidia/Nemotron-3_5-Lightning",
        "NEBIUS_BASE_URL": "https://api.tokenfactory.eu-north1.nebius.com/v1",
        "CARECANOPY_NEBIUS_JSON_MODE": "0",
        "CARECANOPY_NEBIUS_MINIMAL_REQUEST": "1",
    }

    def _run(self, env, response=None):
        seen = []

        def transport(_key, url, payload):
            seen.append((url, payload))
            return response if response is not None else _response()
        with patch.dict(os.environ, env, clear=True):
            result = route_case_nebius(_case(), transport=transport)
        return result, seen

    def test_default_minimal_is_still_512(self):
        _, seen = self._run(self.BASE_ENV)
        self.assertEqual(len(seen), 1)
        self.assertEqual(set(seen[0][1]), {"model", "messages", "max_tokens"})
        self.assertEqual(seen[0][1]["max_tokens"], 512)

    def test_opt_in_2048_keeps_three_fields_and_minimal_label(self):
        env = dict(self.BASE_ENV, CARECANOPY_NEBIUS_MINIMAL_MAX_TOKENS="2048")
        (decision, provenance), seen = self._run(env)
        self.assertEqual(len(seen), 1)
        url, payload = seen[0]
        self.assertEqual(url, "https://api.tokenfactory.eu-north1.nebius.com/v1/chat/completions")
        self.assertEqual(set(payload), {"model", "messages", "max_tokens"})
        self.assertEqual(payload["max_tokens"], 2048)
        self.assertNotIn("response_format", payload)
        self.assertEqual(provenance.public_record()["request_profile"], "minimal")
        self.assertEqual(decision.route.value, "ESCALATE")

    def test_invalid_values_rejected_before_transport(self):
        for value in ("1024", "4096", "abc", "", " 2048", "2048 ", "512.0", "-2048"):
            env = dict(self.BASE_ENV, CARECANOPY_NEBIUS_MINIMAL_MAX_TOKENS=value)

            def transport(*_a):
                raise AssertionError("network must not be called")
            with patch.dict(os.environ, env, clear=True):
                with self.assertRaisesRegex(NebiusRouteError, "must be 512 or 2048", msg=repr(value)):
                    route_case_nebius(_case(), transport=transport)

    def test_opt_in_without_minimal_profile_is_rejected(self):
        for json_mode in ("0", "1"):
            env = dict(self.BASE_ENV, CARECANOPY_NEBIUS_MINIMAL_REQUEST="0",
                       CARECANOPY_NEBIUS_JSON_MODE=json_mode,
                       CARECANOPY_NEBIUS_MINIMAL_MAX_TOKENS="2048")

            def transport(*_a):
                raise AssertionError("network must not be called")
            with patch.dict(os.environ, env, clear=True):
                with self.assertRaisesRegex(NebiusRouteError, "applies only to the minimal profile"):
                    route_case_nebius(_case(), transport=transport)

    def test_default_standard_payloads_unchanged(self):
        for json_mode, extra in (("1", {"response_format"}), ("0", set())):
            env = dict(self.BASE_ENV, CARECANOPY_NEBIUS_MINIMAL_REQUEST="0",
                       CARECANOPY_NEBIUS_JSON_MODE=json_mode)
            _, seen = self._run(env)
            payload = seen[0][1]
            self.assertEqual(set(payload), {"model", "messages", "max_tokens", "temperature",
                                            "stream", "store"} | extra)
            self.assertEqual(payload["max_tokens"], 2048)

    def test_validation_still_strict_at_2048(self):
        env = dict(self.BASE_ENV, CARECANOPY_NEBIUS_MINIMAL_MAX_TOKENS="2048")
        truncated = _response()
        truncated["choices"][0]["finish_reason"] = "length"
        truncated["usage"]["completion_tokens"] = 2048
        with self.assertRaisesRegex(NebiusRouteError, "finish_reason=length"):
            self._run(env, truncated)
        not_json = _response()
        not_json["choices"][0]["message"]["content"] = "Here is my routing answer."
        with self.assertRaisesRegex(NebiusRouteError, "not valid standalone JSON"):
            self._run(env, not_json)

    def test_default_minimal_has_no_thinking_field(self):
        _, seen = self._run(self.BASE_ENV)
        self.assertNotIn("chat_template_kwargs", seen[0][1])

    def test_disable_thinking_adds_exactly_one_field(self):
        env = dict(self.BASE_ENV, CARECANOPY_NEBIUS_MINIMAL_MAX_TOKENS="2048",
                   CARECANOPY_NEBIUS_DISABLE_THINKING="1")
        (decision, provenance), seen = self._run(env)
        self.assertEqual(len(seen), 1)
        payload = seen[0][1]
        self.assertEqual(set(payload), {"model", "messages", "max_tokens", "chat_template_kwargs"})
        self.assertEqual(payload["chat_template_kwargs"], {"enable_thinking": False})
        self.assertIs(payload["chat_template_kwargs"]["enable_thinking"], False)
        self.assertEqual(payload["max_tokens"], 2048)
        for absent in ("reasoning_effort", "extra_body", "thinking_token_budget", "response_format"):
            self.assertNotIn(absent, payload)
        self.assertEqual(provenance.public_record()["request_profile"], "minimal_no_thinking")
        self.assertEqual(decision.route.value, "ESCALATE")

    def test_disable_thinking_invalid_values_rejected_before_transport(self):
        for value in ("yes", "true", "2", "", " 1", "1 ", "False"):
            env = dict(self.BASE_ENV, CARECANOPY_NEBIUS_DISABLE_THINKING=value)

            def transport(*_a):
                raise AssertionError("network must not be called")
            with patch.dict(os.environ, env, clear=True):
                with self.assertRaisesRegex(NebiusRouteError, "DISABLE_THINKING must be 0 or 1",
                                            msg=repr(value)):
                    route_case_nebius(_case(), transport=transport)

    def test_disable_thinking_requires_minimal_profile(self):
        for json_mode in ("0", "1"):
            env = dict(self.BASE_ENV, CARECANOPY_NEBIUS_MINIMAL_REQUEST="0",
                       CARECANOPY_NEBIUS_JSON_MODE=json_mode, CARECANOPY_NEBIUS_DISABLE_THINKING="1")

            def transport(*_a):
                raise AssertionError("network must not be called")
            with patch.dict(os.environ, env, clear=True):
                with self.assertRaisesRegex(NebiusRouteError, "DISABLE_THINKING applies only"):
                    route_case_nebius(_case(), transport=transport)
        conflicting = dict(self.BASE_ENV, CARECANOPY_NEBIUS_JSON_MODE="1",
                           CARECANOPY_NEBIUS_DISABLE_THINKING="1")
        with patch.dict(os.environ, conflicting, clear=True):
            with self.assertRaises(NebiusRouteError):
                route_case_nebius(_case(), transport=lambda *a: (_ for _ in ()).throw(
                    AssertionError("network must not be called")))

    def test_validation_still_strict_with_thinking_disabled(self):
        env = dict(self.BASE_ENV, CARECANOPY_NEBIUS_MINIMAL_MAX_TOKENS="2048",
                   CARECANOPY_NEBIUS_DISABLE_THINKING="1")
        truncated = _response()
        truncated["choices"][0]["finish_reason"] = "length"
        with self.assertRaisesRegex(NebiusRouteError, "finish_reason=length"):
            self._run(env, truncated)
        for bad in ("Here is my answer.", json.dumps({"route": "ROUTINE"}),
                    json.dumps({"route": "ESCALATE", "escalation_type": None, "rationale": "x"})):
            reply = _response()
            reply["choices"][0]["message"]["content"] = bad
            with self.assertRaises(NebiusRouteError):
                self._run(env, reply)

    def test_profile_labels(self):
        from app.nebius_agent import _request_profile_from_payload as label
        base = {"model": "m", "messages": []}
        self.assertEqual(label(dict(base, max_tokens=512)), "minimal")
        self.assertEqual(label(dict(base, max_tokens=2048)), "minimal")
        self.assertEqual(label(dict(base, max_tokens=1024)), "standard")
        self.assertEqual(label(dict(base, max_tokens="2048")), "standard")
        self.assertEqual(label(dict(base, max_tokens=2048, temperature=0)), "standard")
        self.assertEqual(label(dict(base, max_tokens=2048, response_format={})), "standard_json")
        no_think = dict(base, max_tokens=2048, chat_template_kwargs={"enable_thinking": False})
        self.assertEqual(label(no_think), "minimal_no_thinking")
        for kwargs in ({"enable_thinking": True}, {"enable_thinking": 0},
                       {"enable_thinking": False, "x": 1}, "enable_thinking=false"):
            self.assertEqual(label(dict(base, max_tokens=2048, chat_template_kwargs=kwargs)), "standard")
        self.assertEqual(label(dict(no_think, max_tokens=1024)), "standard")
