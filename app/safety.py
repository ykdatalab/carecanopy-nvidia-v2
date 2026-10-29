import re

from app.schemas import EscalationType, Route, RoutingDecision


def _contains_any(text: str, patterns: list[str]) -> bool:
    """Return True when any regex pattern matches the supplied text."""
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


# v2 candidate: isolate explicitly NEGATED or remote historical chest-pain
# mentions. Does not attempt general clinical NLP, and must NOT be used to
# override a separate active symptom or clinical assessment.
def _active_chest_pain_mentioned(text: str) -> bool:
    """Only exclude narrow, explicit absence/remote-history constructions.

    An ambiguous chest-pain mention is retained as a safety trigger.
    This helper cannot establish that a patient is medically safe.
    """
    for match in re.finditer(r"\bchest pain\b", text, flags=re.I):
        before = text[max(0, match.start() - 110): match.start()]
        after = text[match.end(): match.end() + 90]
        # Nearest sentence / contrast clause only.
        clause_before = re.split(r"[.!?;\n]|\bbut\b|\bhowever\b|\byet\b", before, flags=re.I)[-1]
        negation = re.search(
            r"\b(?:denies|denied|deny|no|without|negative for|absence of)\s+"
            r"(?:(?:any|current|new|ongoing|reported|recent)\s+){0,2}$",
            clause_before,
            flags=re.I,
        )
        remote_before = re.search(r"\bremote history of\s+$", clause_before, flags=re.I)
        remote_after = re.match(
            r"\s+(?:from\s+)?(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|many)\s+years? ago\b",
            after,
            re.I,
        )
        # Keep as an alert if the same sentence says the symptom recurred.
        recurrence = re.search(r"\b(?:again|recurred|currently|now|today)\b", after, flags=re.I)
        if not (negation or ((remote_before or remote_after) and not recurrence)):
            return True
    return False


def detect_hard_boundary(
    current_report: str,
) -> tuple[EscalationType | None, list[str]]:
    """
    Detect only high-confidence safety boundaries.

    This is intentionally conservative and limited.
    It is NOT a replacement for clinical reasoning.
    """

    text = current_report.lower()
    triggers: list[str] = []

    # ---------------------------------------------------------
    # 1. URGENT MEDICAL: acute neurological change
    # ---------------------------------------------------------
    acute_terms = [
        r"\bsudden\b",
        r"\bsuddenly\b",
        r"\bnew\b",
        r"\bthis morning\b",
        r"\bjust developed\b",
    ]

    neurological_terms = [
        r"\bnew weakness\b",
        r"\b(?:new|sudden|suddenly|acute) (?:right|left) (?:arm|leg|side) weakness\b",
        r"\bweakness in (the )?(right|left) (arm|leg|side)\b",
        r"\bfacial droop\b",
        r"\bslurred speech\b",
        r"\bspeech change\b",
        r"\bdifficulty speaking\b",
        r"\b(?:cannot|can't|unable to) speak\b",
        r"\bface (?:is |was )?droop(?:ing|ed|s)?\b",
        r"\bnew numbness\b",
        r"\bsudden vision\b",
        r"\bloss of balance\b",
        r"\bloss of coordination\b",
    ]

    if _contains_any(text, acute_terms) and _contains_any(
        text, neurological_terms
    ):
        triggers.append("U1")
        return EscalationType.URGENT_MEDICAL, triggers

    # ---------------------------------------------------------
    # 2. URGENT MEDICAL: chest / breathing concern
    # ---------------------------------------------------------
    if _active_chest_pain_mentioned(text) or _contains_any(
        text,
        [
            r"\bsevere shortness of breath\b",
            r"\bsevere breathlessness\b",
        ],
    ):
        triggers.append("U4")
        return EscalationType.URGENT_MEDICAL, triggers

    # ---------------------------------------------------------
    # 3. URGENT MEDICAL: fall / significant injury
    # ---------------------------------------------------------
    fall_terms = [
        r"\bfell\b",
        r"\bfall\b",
        r"\bslipped\b",
    ]

    serious_injury_terms = [
        r"\bhit (his|her|their|the) head\b",
        r"\bhit the back of (his|her|their) head\b",
        r"\bhead impact\b",
        r"\blost consciousness\b",
        r"\bloss of consciousness\b",
        r"\bcannot bear weight\b",
        r"\bcan't bear weight\b",
        r"\bunable to bear weight\b",
    ]

    if _contains_any(text, fall_terms) and _contains_any(
        text, serious_injury_terms
    ):
        triggers.append("U2")
        return EscalationType.URGENT_MEDICAL, triggers

    # ---------------------------------------------------------
    # 4. URGENT / PROMPT MEDICAL: possible DVT
    # ---------------------------------------------------------
    unilateral_leg_terms = [
        r"\bone[- ]sided (calf|leg) swelling\b",
        r"\bunilateral (calf|leg) swelling\b",
        r"\bright calf.*swollen\b",
        r"\bleft calf.*swollen\b",
    ]

    dvt_terms = [
        r"\bwarm\b",
        r"\bwarmth\b",
        r"\bred\b",
        r"\bredness\b",
        r"\btender\b",
        r"\btenderness\b",
        r"\bpain\b",
    ]

    if _contains_any(text, unilateral_leg_terms) and _contains_any(
        text, dvt_terms
    ):
        triggers.append("U3")
        return EscalationType.URGENT_MEDICAL, triggers

    # ---------------------------------------------------------
    # 5. SCOPE BOUNDARY: programme modification
    # ---------------------------------------------------------
    programme_change_terms = [
        r"\bincrease (the )?(exercise )?intensity\b",
        r"\bincrease (the )?load\b",
        r"\bdouble (the )?(exercise )?intensity\b",
        r"\bmake (the )?(exercise|programme|program|sessions?) harder\b",
        r"\badd hill walking\b",
        r"\badd (a |an )?new exercise\b",
        r"\bchange (the )?exercise\b",
        r"\bprogress (the )?(exercise|programme|program)\b",
    ]

    if _contains_any(text, programme_change_terms):
        triggers.append("N1")
        return EscalationType.SCOPE_BOUNDARY, triggers

    # ---------------------------------------------------------
    # 6. SCOPE BOUNDARY: medication decision
    # ---------------------------------------------------------
    medication_terms = [
        r"\bextra (blood[- ]pressure )?pill\b",
        r"\bextra medication\b",
        r"\bchange (the )?dose\b",
        r"\bincrease (the )?dose\b",
        r"\breduce (the )?dose\b",
        r"\bstop (the )?medication\b",
        r"\bchange medication timing\b",
    ]

    if _contains_any(text, medication_terms):
        triggers.append("N2")
        return EscalationType.SCOPE_BOUNDARY, triggers

    # ---------------------------------------------------------
    # 7. SCOPE BOUNDARY: diagnosis request
    # ---------------------------------------------------------
    if _contains_any(
        text,
        [
            r"\bdiagnose\b",
            r"\bmake a diagnosis\b",
            r"\bwhat diagnosis\b",
        ],
    ):
        triggers.append("N3")
        return EscalationType.SCOPE_BOUNDARY, triggers

    # ---------------------------------------------------------
    # 8. SCOPE BOUNDARY: discharge / permanent stop
    # ---------------------------------------------------------
    if _contains_any(
        text,
        [
            r"\bdischarge (the )?patient\b",
            r"\bend (the )?(rehabilitation|rehab) programme\b",
            r"\bpermanently stop\b",
        ],
    ):
        triggers.append("N4")
        return EscalationType.SCOPE_BOUNDARY, triggers

    # ---------------------------------------------------------
    # 9. SCOPE BOUNDARY: bypass human review
    # ---------------------------------------------------------
    if _contains_any(
        text,
        [
            r"\bbypass (the )?(specialist|review)\b",
            r"\bcancel (the )?specialist review\b",
            r"\bclose (the )?case without review\b",
            r"\boverride (the )?specialist review\b",
        ],
    ):
        triggers.append("N5")
        return EscalationType.SCOPE_BOUNDARY, triggers

    # ---------------------------------------------------------
    # 10. SCOPE BOUNDARY: falsifying records
    # ---------------------------------------------------------
    if _contains_any(
        text,
        [
            r"\bfalsify\b",
            r"\bpretend (it|the session|the visit) was completed\b",
            r"\brecord .* as completed even though\b",
            r"\bmark .* as completed even though\b",
        ],
    ):
        triggers.append("N6")
        return EscalationType.SCOPE_BOUNDARY, triggers

    return None, []


def apply_safety_gate(
    case: dict,
    decision: RoutingDecision,
) -> RoutingDecision:
    """
    Enforce deterministic CareCanopy safety boundaries after LLM reasoning.

    Precedence:
    URGENT_MEDICAL > SCOPE_BOUNDARY > LLM decision
    """

    current_report = case.get("current_report", "")

    hard_escalation_type, hard_triggers = detect_hard_boundary(
        current_report
    )

    # Hard deterministic override
    if hard_escalation_type is not None:
        is_urgent = (
            hard_escalation_type == EscalationType.URGENT_MEDICAL
        )

        gate_reason = (
            f"Deterministic safety boundary triggered: "
            f"{', '.join(hard_triggers)}."
        )

        return decision.model_copy(
            update={
                "route": Route.ESCALATE,
                "escalation_type": hard_escalation_type,
                "protocol_triggers": list(
                    dict.fromkeys(
                        decision.protocol_triggers + hard_triggers
                    )
                ),
                "immediate_medical_guidance": is_urgent,
                "specialist_notification": True,
                "create_review_packet": True,
                "safety_gate_triggered": True,
                "safety_gate_reason": gate_reason,
            }
        )

    # ---------------------------------------------------------
    # Enforce internally consistent workflow flags
    # ---------------------------------------------------------

    if decision.route in {Route.ROUTINE, Route.CLARIFY}:
        return decision.model_copy(
            update={
                "escalation_type": None,
                "immediate_medical_guidance": False,
                "specialist_notification": False,
                "create_review_packet": False,
                "safety_gate_triggered": False,
                "safety_gate_reason": None,
            }
        )

    if decision.route == Route.ESCALATE:
        is_urgent = (
            decision.escalation_type
             == EscalationType.URGENT_MEDICAL
        )

        return decision.model_copy(
            update={
                "immediate_medical_guidance": is_urgent,
                "specialist_notification": True,
                "create_review_packet": True,
                "safety_gate_triggered": False,
                "safety_gate_reason": None,
            }
        )

    return decision