import json

from app.safety import apply_safety_gate
from app.schemas import Route, RoutingDecision


PROBES_PATH = "data/boundary_probes.json"


def load_probes() -> list[dict]:
    with open(
        PROBES_PATH,
        "r",
        encoding="utf-8",
    ) as file:
        return json.load(file)


def main():
    probes = load_probes()
    results = []

    print("=" * 72)
    print("CARECANOPY DETERMINISTIC BOUNDARY PROBES")
    print("=" * 72)
    print(
        "Test condition: simulated LLM incorrectly proposes ROUTINE "
        "for every case."
    )

    for probe in probes:
        # Deliberately unsafe simulated LLM output.
        simulated_llm_decision = RoutingDecision(
            route=Route.ROUTINE,
            rationale=(
                "Simulated unsafe LLM proposal for deterministic "
                "safety testing."
            ),
        )

        final_decision = apply_safety_gate(
            probe,
            simulated_llm_decision,
        )

        actual_escalation = (
            final_decision.escalation_type.value
            if final_decision.escalation_type
            else None
        )

        trigger_match = (
            probe["expected_trigger"]
            in final_decision.protocol_triggers
        )

        escalation_match = (
            actual_escalation
            == probe["expected_escalation_type"]
        )

        passed = (
            final_decision.route == Route.ESCALATE
            and final_decision.safety_gate_triggered
            and escalation_match
            and trigger_match
        )

        results.append(
            {
                "id": probe["id"],
                "title": probe["title"],
                "passed": passed,
                "route": final_decision.route.value,
                "escalation_type": actual_escalation,
                "trigger": probe["expected_trigger"],
                "safety_gate_triggered": (
                    final_decision.safety_gate_triggered
                ),
            }
        )

        status = "PASS" if passed else "FAIL"

        print()
        print(
            f"{probe['id']} — {probe['title']}"
        )
        print(
            f"Simulated LLM proposal: ROUTINE"
        )
        print(
            f"Final route: {final_decision.route.value}"
        )
        print(
            f"Escalation: {actual_escalation}"
        )
        print(
            f"Protocol triggers: "
            f"{final_decision.protocol_triggers}"
        )
        print(
            f"Safety gate: "
            f"{final_decision.safety_gate_triggered}"
        )
        print(
            f"Result: {status}"
        )

    print()
    print("=" * 72)
    print("BOUNDARY PROBE SUMMARY")
    print("=" * 72)

    passed_count = sum(
        1 for result in results
        if result["passed"]
    )

    for result in results:
        status = (
            "PASS"
            if result["passed"]
            else "FAIL"
        )

        print(
            f"{result['id']}: {status} | "
            f"{result['escalation_type']} | "
            f"{result['trigger']} | "
            f"Safety gate: "
            f"{result['safety_gate_triggered']}"
        )

    print()
    print(
        f"Result: {passed_count}/{len(results)} "
        f"boundary probes passed."
    )


if __name__ == "__main__":
    main()