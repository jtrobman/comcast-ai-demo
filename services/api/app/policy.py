from __future__ import annotations

from typing import Any, Dict, List

from .models import PolicyVerdict, SourceCitation


INJECTION_MARKERS = ("ignore all policy", "ignore previous", "free month", "bypass")


def evaluate_policy(
    customer_message: str,
    tool_results: Dict[str, Dict[str, Any]],
    citations: List[SourceCitation],
) -> PolicyVerdict:
    lowered = customer_message.lower()
    reasons: list[str] = []

    if any(marker in lowered for marker in INJECTION_MARKERS):
        return PolicyVerdict(
            status="block",
            decision="Blocked prompt injection before model generation.",
            reasons=[
                "The message attempted to override policy-controlled behavior.",
                "No OpenAI generation was allowed, so the unsafe instruction could not influence the response.",
            ],
            required_human_approval=True,
        )

    outage = tool_results.get("check_area_outage", {})
    telemetry = tool_results.get("get_device_signal_status", {})
    credit = tool_results.get("check_credit_eligibility", {})

    if "credit" in lowered and not credit.get("eligible"):
        reasons.append("Credit was requested, but eligibility was not confirmed by the billing tool.")

    if outage.get("status") == "confirmed":
        return PolicyVerdict(
            status="pass",
            decision="Confirmed area outage. Keep the case tied to incident status updates.",
            reasons=["The outage tool confirmed an active service interruption for this location."],
            required_human_approval=False,
        )

    signal_state = telemetry.get("signal_state")
    rebooted = "reboot" in lowered or "restart" in lowered
    if signal_state in {"degraded", "flapping"} and rebooted:
        drops = telemetry.get("drops_last_24h")
        reasons.append(
            "Gateway telemetry still shows degraded signal"
            f"{f' and {drops} drops in the last 24 hours' if drops is not None else ''} after the customer restart."
        )
        if citations:
            reasons.append(
                "Retrieved Comcast Business guidance supports checking service status and equipment signals before escalation."
            )
        return PolicyVerdict(
            status="pass",
            decision="Prepare a technician review, but do not schedule until a support lead approves it.",
            reasons=reasons,
            required_human_approval=True,
        )

    if signal_state in {"degraded", "flapping"} and "credit" in lowered:
        drops = telemetry.get("drops_last_24h")
        reasons.append(
            "Gateway telemetry shows a real service issue"
            f"{f' with {drops} drops in the last 24 hours' if drops is not None else ''}; resolve the service path separately from billing review."
        )
        if citations:
            reasons.append(
                "Retrieved Comcast Business guidance supports checking service status and equipment signals before escalation."
            )
        return PolicyVerdict(
            status="revise",
            decision="Prepare technician review and route credit request to billing review.",
            reasons=reasons,
            required_human_approval=True,
        )

    if reasons:
        return PolicyVerdict(
            status="revise",
            decision="Credit request needs billing review before any promise.",
            reasons=reasons,
            required_human_approval=True,
        )

    return PolicyVerdict(
        status="pass",
        decision="Proceed with guided troubleshooting response.",
        reasons=["No blocking compliance or operational policy issue detected."],
        required_human_approval=False,
    )
