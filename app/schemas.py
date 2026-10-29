from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Route(str, Enum):
    """Top-level CareCanopy routing decision."""
    ROUTINE = "ROUTINE"
    CLARIFY = "CLARIFY"
    ESCALATE = "ESCALATE"


class EscalationType(str, Enum):
    """Internal reason category when route == ESCALATE."""
    URGENT_MEDICAL = "URGENT_MEDICAL"
    NON_URGENT_PROFESSIONAL = "NON_URGENT_PROFESSIONAL"
    SCOPE_BOUNDARY = "SCOPE_BOUNDARY"


class RoutingDecision(BaseModel):
    """
    Structured result returned by the CareCanopy reasoning layer.

    Clinical authority remains with qualified healthcare professionals.
    """

    route: Route = Field(
        description="Final routing decision: ROUTINE, CLARIFY, or ESCALATE."
    )

    escalation_type: Optional[EscalationType] = Field(
        default=None,
        description=(
            "Required only when route is ESCALATE. "
            "URGENT_MEDICAL, NON_URGENT_PROFESSIONAL, or SCOPE_BOUNDARY."
        ),
    )

    rationale: str = Field(
        description=(
            "Brief reason for the routing decision, grounded only in "
            "the patient context, current report, and protocol."
        )
    )

    evidence: list[str] = Field(
        default_factory=list,
        description="Concrete observations from the report/history supporting the decision.",
    )

    missing_information: list[str] = Field(
        default_factory=list,
        description=(
            "Only the minimum missing observations required for safe routing. "
            "Normally populated when route is CLARIFY."
        ),
    )

    protocol_triggers: list[str] = Field(
        default_factory=list,
        description=(
            "Relevant protocol rule IDs or categories, such as "
            "U1, S4, N1, or 'scope boundary'."
        ),
    )

    immediate_medical_guidance: bool = Field(
        default=False,
        description=(
            "True only for URGENT_MEDICAL escalation requiring immediate "
            "medical/emergency guidance."
        ),
    )

    specialist_notification: bool = Field(
        default=False,
        description=(
            "Whether the rehabilitation specialist or responsible clinical team "
            "must be notified."
        ),
    )

    create_review_packet: bool = Field(
        default=False,
        description="Whether an evidence packet for human review must be created.",
    )
    
    safety_gate_triggered: bool = Field(
        default=False,
        description=(
            "True when deterministic safety logic overrides or constrains "
            "the LLM routing decision."
        ),
    )

    safety_gate_reason: Optional[str] = Field(
        default=None,
        description=(
            "Short explanation of the deterministic safety rule that was applied."
        ),
    )