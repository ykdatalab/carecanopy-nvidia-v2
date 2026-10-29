import json

from strands import Agent
from strands.models import BedrockModel

from app.prompts import CARECANOPY_SYSTEM_PROMPT
from app.schemas import RoutingDecision

from app.safety import apply_safety_gate


MODEL_ID = "global.anthropic.claude-sonnet-4-5-20250929-v1:0"
AWS_REGION = "ap-northeast-2"


def build_agent() -> Agent:
    """Create a fresh CareCanopy agent."""

    model = BedrockModel(
        model_id=MODEL_ID,
        region_name=AWS_REGION,
        temperature=0.1,
        max_tokens=1200,
    )

    return Agent(
        model=model,
        system_prompt=CARECANOPY_SYSTEM_PROMPT,
        callback_handler=None,
    )


def route_case(case: dict) -> RoutingDecision:
    """
    Route one development case.

    A fresh agent is created for each case so that previous cases
    do not influence the next decision.
    """

    agent = build_agent()

    case_payload = {
        "patient_context": case["patient_context"],
        "current_report": case["current_report"],
    }

    prompt = f"""
Review the following community rehabilitation follow-up case.

CASE:
{json.dumps(case_payload, indent=2, ensure_ascii=False)}

Return a CareCanopy routing decision that follows the protocol exactly.

Important:
- Do not invent missing clinical facts.
- ROUTINE requires positive support from the supplied information.
- If a specific missing observation could change the route, use CLARIFY.
- If an urgent trigger or clear scope boundary is already present,
  do not delay escalation for clarification.
"""

    result = agent(
        prompt,
        structured_output_model=RoutingDecision,
    )

    decision = result.structured_output

    if decision is None:
        raise RuntimeError("CareCanopy returned no structured routing decision.")

    final_decision = apply_safety_gate(case, decision)

    return final_decision   