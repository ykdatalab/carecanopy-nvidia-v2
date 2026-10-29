from copy import deepcopy
from datetime import datetime, timezone

import boto3


TABLE_NAME = "CareCanopyWorkflowStates"
AWS_REGION = "ap-northeast-2"


def _timestamp() -> str:
    """Return the current UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


class DynamoWorkflowStateStore:
    """
    DynamoDB-backed workflow state store for CareCanopy.
    """

    def __init__(
        self,
        table_name: str = TABLE_NAME,
        region_name: str = AWS_REGION,
    ):
        dynamodb = boto3.resource(
            "dynamodb",
            region_name=region_name,
        )

        self.table = dynamodb.Table(table_name)

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
        Apply workflow actions and persist the resulting state
        to DynamoDB.
        """

        case_id = case.get("id", "UNKNOWN")

        existing_state = self.get_case(case_id)

        if existing_state is None:
            state = self._initial_state(case_id)
        else:
            state = existing_state

        state["status"] = action_result["status"]

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

        if action_result.get("review_packet") is not None:
            state["review_packet"] = action_result["review_packet"]

        state["updated_at"] = _timestamp()

        self.table.put_item(Item=state)

        return deepcopy(state)

    def get_case(self, case_id: str) -> dict | None:
        """
        Retrieve one workflow state from DynamoDB.
        """

        response = self.table.get_item(
            Key={
                "case_id": case_id,
            }
        )

        item = response.get("Item")

        if item is None:
            return None

        return deepcopy(item)