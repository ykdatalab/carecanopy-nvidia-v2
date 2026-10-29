from datetime import datetime, timezone

from app.schemas import EscalationType, Route, RoutingDecision


def _timestamp() -> str:
    """Return the current UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def execute_actions(
    case: dict,
    decision: RoutingDecision,
) -> dict:
    """
    Convert the final CareCanopy routing decision
    into concrete workflow actions.

    This layer does not make clinical decisions.
    It executes the already-finalised routing result.
    """

    case_id = case.get("id", "UNKNOWN")

    result = {
        "case_id": case_id,
        "route": decision.route.value,
        "status": None,
        "actions": [],
        "review_packet": None,
        "executed_at": _timestamp(),
    }

    # ---------------------------------------------------------
    # ROUTINE
    # ---------------------------------------------------------
    if decision.route == Route.ROUTINE:
        result["status"] = "ROUTINE_COMPLETED"

        result["actions"].append(
            {
                "action": "COMPLETE_ROUTINE_FOLLOWUP",
                "description": (
                    "Routine follow-up completed within the "
                    "existing specialist-approved plan."
                ),
            }
        )

        return result

    # ---------------------------------------------------------
    # CLARIFY
    # ---------------------------------------------------------
    if decision.route == Route.CLARIFY:
        result["status"] = "AWAITING_CLARIFICATION"

        result["actions"].append(
            {
                "action": "REQUEST_CLARIFICATION",
                "questions": decision.missing_information,
            }
        )

        return result

    # ---------------------------------------------------------
    # ESCALATE
    # ---------------------------------------------------------
    if decision.route == Route.ESCALATE:
        result["status"] = "ESCALATED"

        # Urgent medical dual path
        if (
            decision.escalation_type
            == EscalationType.URGENT_MEDICAL
        ):
            result["actions"].append(
                {
                    "action": "ISSUE_IMMEDIATE_MEDICAL_GUIDANCE",
                    "description": (
                        "Direct the case to the appropriate "
                        "urgent or emergency medical pathway."
                    ),
                }
            )

        if decision.specialist_notification:
            result["actions"].append(
                {
                    "action": "NOTIFY_CLINICAL_TEAM",
                    "description": (
                        "Notify the rehabilitation specialist "
                        "or responsible clinical team."
                    ),
                }
            )

        if decision.create_review_packet:
            result["review_packet"] = {
                "case_id": case_id,
                "escalation_type": (
                    decision.escalation_type.value
                    if decision.escalation_type
                    else None
                ),
                "rationale": decision.rationale,
                "evidence": decision.evidence,
                "protocol_triggers": decision.protocol_triggers,
                "safety_gate_triggered": (
                    decision.safety_gate_triggered
                ),
                "safety_gate_reason": (
                    decision.safety_gate_reason
                ),
            }

            result["actions"].append(
                {
                    "action": "CREATE_REVIEW_PACKET",
                    "description": (
                        "Create an evidence packet for "
                        "authorised human review."
                    ),
                }
            )

        return result

    raise ValueError(
        f"Unsupported CareCanopy route: {decision.route}"
    )