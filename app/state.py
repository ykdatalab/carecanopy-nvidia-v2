from copy import deepcopy
from datetime import datetime, timezone


def _timestamp() -> str:
    """Return the current UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


class WorkflowStateStore:
    """
    Simple in-memory workflow state store for local development.

    This will later be replaced by DynamoDB persistence.
    """

    def __init__(self):
        self._cases: dict[str, dict] = {}

    def _initial_state(self, case_id: str) -> dict:
        return {
            "case_id": case_id,
            "status": "NEW",
            "routine_followup_completed": False,
            "clarification_requested": False,
            "clarification_questions": [],
            "urgent_medical_guidance_issued": False,
            "clinical_team_notified": False,
            "review_required": False,
            "review_packet": None,
            "updated_at": _timestamp(),
            "audit_log": [],
        }

    def apply_actions(
        self,
        case: dict,
        action_result: dict,
    ) -> dict:
        """
        Apply workflow actions to the stored case state.
        """

        case_id = case.get("id", "UNKNOWN")

        if case_id not in self._cases:
            self._cases[case_id] = self._initial_state(case_id)

        state = self._cases[case_id]

        # Final workflow status
        state["status"] = action_result["status"]

        # Apply each concrete action
        for action in action_result.get("actions", []):
            action_name = action.get("action")

            if action_name == "COMPLETE_ROUTINE_FOLLOWUP":
                state["routine_followup_completed"] = True

            elif action_name == "REQUEST_CLARIFICATION":
                state["clarification_requested"] = True
                state["clarification_questions"] = action.get(
                    "questions",
                    [],
                )

            elif action_name == "ISSUE_IMMEDIATE_MEDICAL_GUIDANCE":
                state["urgent_medical_guidance_issued"] = True

            elif action_name == "NOTIFY_CLINICAL_TEAM":
                state["clinical_team_notified"] = True

            elif action_name == "CREATE_REVIEW_PACKET":
                state["review_required"] = True

            state["audit_log"].append(
                {
                    "timestamp": _timestamp(),
                    "action": action_name,
                    "source": "CareCanopy",
                }
            )

        # Attach review packet when one exists
        if action_result.get("review_packet") is not None:
            state["review_packet"] = action_result["review_packet"]

        state["updated_at"] = _timestamp()

        return deepcopy(state)

    def get_case(self, case_id: str) -> dict | None:
        """
        Return the current state of one case.
        """

        state = self._cases.get(case_id)

        if state is None:
            return None

        return deepcopy(state)