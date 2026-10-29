"""No-network regression for Nemotron JSON guard hotfix. No real keys."""
import json
import os
import unittest
from unittest.mock import patch
from app.nebius_agent import NebiusRouteError, route_case_nebius

def case():
    return {
        "synthetic_patient_id": "SYN-DEV04",
        "current_report": "Synthetic: gradual shoulder discomfort, worsening over a week; activities limited.",
        "patient_context": {
            "age": 70,
            "weeks_post_stroke": 12,
            "baseline": "Walks with a quad cane",
            "approved_plan": "Walking and balance programme",
            "previous_unverified_reports": [],
        },
    }

def response(content, reason="stop", completion=500):
    return {
        "model": "nvidia/Nemotron-3_5-Lightning", "id": "offline-fake",
        "choices": [{"finish_reason": reason, "message": {"content": content}}],
        "usage": {"prompt_tokens": 200, "completion_tokens": completion},
    }

def valid():
    return json.dumps({
        "route": "ESCALATE", "escalation_type": "NON_URGENT_PROFESSIONAL",
        "rationale": "New worsening pain limits activity; request professional review.",
        "evidence": ["gradual shoulder discomfort", "activities limited"],
        "missing_information": [], "protocol_triggers": [],
        "immediate_medical_guidance": False,
        "specialist_notification": True, "create_review_packet": True,
    })

class JsonGuardTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.dict(os.environ, {
            "CARECANOPY_ENABLE_NEBIUS": "1",
            "NEBIUS_API_KEY": "dummy-unit-key-not-real",
            "NEBIUS_MODEL_ID": "nvidia/Nemotron-3_5-Lightning",
        }, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_json_mode_one_call_and_safe_meta(self):
        payloads = []
        def transport(key, url, payload):
            payloads.append(payload)
            return response(valid())
        decision, evidence = route_case_nebius(case(), transport=transport)
        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0]["response_format"], {"type": "json_object"})
        self.assertEqual(payloads[0]["max_tokens"], 2048)
        self.assertFalse(payloads[0]["store"])
        self.assertEqual(decision.route.value, "ESCALATE")
        self.assertEqual(evidence.completion_tokens, 500)

    def test_json_mode_can_be_disabled_explicitly(self):
        os.environ["CARECANOPY_NEBIUS_JSON_MODE"] = "0"
        payloads = []
        route_case_nebius(case(), transport=lambda k,u,p: (payloads.append(p), response(valid()))[1])
        self.assertNotIn("response_format", payloads[0])

    def test_invalid_json_never_becomes_a_route(self):
        called=[]
        with self.assertRaisesRegex(NebiusRouteError, "not valid standalone JSON") as ex:
            route_case_nebius(case(), transport=lambda k,u,p: (called.append(1), response("The answer is routine. {bad}"))[1])
        self.assertEqual(len(called), 1)
        self.assertIn("visible_chars=", str(ex.exception))
        self.assertNotIn("The answer", str(ex.exception))

    def test_truncated_output_fail_closed(self):
        with self.assertRaisesRegex(NebiusRouteError, "finish_reason=length"):
            route_case_nebius(case(), transport=lambda *a: response(valid(),reason="length",completion=2048))

    def test_filtered_completion_blocks_well_formed_json(self):
        with self.assertRaisesRegex(NebiusRouteError, "finish_reason=content_filter"):
            route_case_nebius(case(), transport=lambda *a: response(valid(), reason="content_filter"))

    def test_tool_call_finish_blocks_well_formed_json(self):
        with self.assertRaisesRegex(NebiusRouteError, "finish_reason=tool_calls"):
            route_case_nebius(case(), transport=lambda *a: response(valid(), reason="tool_calls"))

    def test_missing_finish_reason_blocks_well_formed_json(self):
        r = response(valid())
        del r["choices"][0]["finish_reason"]
        with self.assertRaisesRegex(NebiusRouteError, "finish_reason=unknown"):
            route_case_nebius(case(), transport=lambda *a: r)

    def test_unknown_finish_reason_blocks_well_formed_json(self):
        with self.assertRaisesRegex(NebiusRouteError, "finish_reason=unknown"):
            route_case_nebius(case(), transport=lambda *a: response(valid(), reason="unexpected"))

    def test_normal_stop_still_passes_to_gate(self):
        # With no network and a valid JSON object, ordinary path remains intact.
        decision, _ = route_case_nebius(case(), transport=lambda *a: response(valid(), reason="stop"))
        self.assertEqual(decision.route.value, "ESCALATE")

    def test_bad_env_no_call(self):
        os.environ["CARECANOPY_NEBIUS_JSON_MODE"] = "garbage"
        called=[]
        with self.assertRaisesRegex(NebiusRouteError, "must be 0 or 1"):
            route_case_nebius(case(), transport=lambda *a: called.append(1))
        self.assertFalse(called)

    def test_refusal_is_safe_failure(self):
        r=response(valid())
        r["choices"][0]["message"]["refusal"]="refused"
        with self.assertRaisesRegex(NebiusRouteError,"refused"):
            route_case_nebius(case(), transport=lambda *a:r)

    def test_boundary_override_is_preserved(self):
        c=case();c["current_report"] = "She suddenly cannot speak and one side of the face is drooping now."
        s=json.dumps({"route":"ROUTINE", "rationale":"Wrong unsafe proposal"})
        decision,_=route_case_nebius(c, transport=lambda *a: response(s))
        self.assertEqual(decision.route.value,"ESCALATE")
        self.assertTrue(decision.safety_gate_triggered)

if __name__ == "__main__": unittest.main()
