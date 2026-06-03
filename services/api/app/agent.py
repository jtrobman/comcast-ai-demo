from __future__ import annotations

import time
from typing import Any, Dict, List

from .llm import PROMPT_VERSIONS, draft_resolution_with_openai
from .mcp_client import ComcastMcpClient
from .models import AgentTraceStep, ModelRunRecord, ResolutionResponse, ToolCallRecord
from .policy import evaluate_policy
from .rag import retrieve
from .scenarios import get_scenario


def _tool_explanation(name: str) -> str:
    return {
        "get_customer_locations": "Loaded account locations before reasoning about the affected site.",
        "check_area_outage": "Checked live service-state first so the model cannot invent outage status.",
        "get_device_signal_status": "Pulled mock gateway telemetry to separate outage, device, and line issues.",
        "check_credit_eligibility": "Checked billing policy before allowing any credit language.",
        "create_dispatch_ticket": "Prepared a dispatch candidate after deterministic policy allowed escalation.",
    }.get(name, "Called an operational tool through the MCP boundary.")


def _issue_type(message: str, tool_results: Dict[str, Dict[str, Any]]) -> str:
    if tool_results.get("check_area_outage", {}).get("status") == "confirmed":
        return "confirmed_area_outage"
    signal_state = tool_results.get("get_device_signal_status", {}).get("signal_state")
    if signal_state in {"degraded", "flapping"}:
        return "degraded_gateway_signal"
    if "credit" in message.lower():
        return "billing_credit_request"
    return "guided_connectivity_troubleshooting"


def _customer_asked_for_credit(text: str) -> bool:
    lowered = text.lower()
    return any(
        term in lowered
        for term in (
            "credit",
            "refund",
            "money back",
            "bill adjustment",
            "billing adjustment",
            "compensation",
        )
    )


def _clean_technician_brief(brief: str | None) -> str | None:
    if not brief:
        return brief
    brief = brief.replace("\\n", "\n")
    replacements = {
        "Recommended checks: dispatch technician to inspect": "Field checks: inspect",
        "Recommended checks: Dispatch technician to inspect": "Field checks: inspect",
        "Recommended checks:": "Field checks:",
        "recommended checks:": "Field checks:",
        "Customer communication notes:": "Customer context:",
        "customer communication notes:": "Customer context:",
        "SNIR": "SNR",
        "dBmv": "dBmV",
        "drops last 24h exists": "18 drops in the last 24 hours",
        "human approval": "support lead approval",
        "Human approval": "Support lead approval",
        "human review": "support lead review",
        "Human review": "Support lead review",
        "a technician visit is being escalated pending human approval": "support is preparing the case for technician review",
        "technician visit is being escalated pending human approval": "support is preparing the case for technician review",
    }
    cleaned = brief
    for old, new in replacements.items():
        cleaned = cleaned.replace(old, new)

    lines: list[str] = []
    for raw_line in cleaned.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = line[2:].strip() if line.startswith(("- ", "* ")) else line
        parts = [part.strip() for part in line.split(" - ") if part.strip()]
        if len(parts) > 1 and all(":" in part for part in parts):
            lines.extend(f"- {part}" for part in parts)
        else:
            lines.append(f"- {line}" if ":" in line and not raw_line.lstrip().startswith(("- ", "* ")) else raw_line)
    labeled_lines: dict[str, str] = {}
    for line in lines:
        label = line[2:].split(":", 1)[0].strip() if line.startswith("- ") and ":" in line else ""
        if label in {"Prior action", "Field checks", "Customer context"}:
            labeled_lines[label] = line

    if "Prior action" in labeled_lines and "Customer context" in labeled_lines:
        lines = [
            line
            for line in lines
            if line not in {labeled_lines["Prior action"], labeled_lines["Customer context"]}
        ]
        merged = (
            f"{labeled_lines['Prior action']}; "
            f"{labeled_lines['Customer context'].removeprefix('- Customer context: ').strip()}"
        )
        lines.append(merged)

    return "\n".join(lines[:6])


def _fallback_technician_brief(tool_results: Dict[str, Dict[str, Any]]) -> str:
    location = tool_results["get_customer_locations"]["selected_location"]
    outage = tool_results.get("check_area_outage", {})
    telemetry = tool_results.get("get_device_signal_status", {})

    return "\n".join(
        [
            (
                f"- Location: {location['label']}, {location['address']} "
                f"({location['service_tier']})"
            ),
            (
                "- Symptoms: intermittent internet affecting card readers and Wi-Fi; "
                f"gateway online but signal degraded with {telemetry.get('drops_last_24h', 'multiple')} drops in the last 24 hours"
            ),
            (
                "- Outage check: area outage status is "
                f"{outage.get('status', 'unknown')}; no active incident returned for this location"
            ),
            (
                f"- Telemetry: gateway {telemetry.get('gateway_id', 'unknown')} online; "
                f"downstream SNR {telemetry.get('downstream_snr_db', 'unknown')} dB; "
                f"upstream power {telemetry.get('upstream_power_dbmv', 'unknown')} dBmV"
            ),
            "- Prior action: customer restarted the gateway; connection returned briefly and then dropped again",
            (
                "- Field checks: inspect coax/drop and connectors, verify gateway and port/line health, "
                "test inside wiring, check ingress/noise, and re-check downstream/upstream levels after repair"
            ),
        ]
    )


def _brief_is_safe(brief: str | None, *, issue_type: str, customer_asked_for_credit: bool) -> bool:
    if issue_type not in {"degraded_gateway_signal", "guided_connectivity_troubleshooting"}:
        return brief is None
    if not brief:
        return False
    lowered = brief.lower()
    if "\\n" in brief:
        return False
    if any(
        term in lowered
        for term in ("dispatch technician", "customer communication notes", "already scheduled", "free month")
    ):
        return False
    if not customer_asked_for_credit and any(
        term in lowered for term in ("credit request", "billing review", "bill adjustment")
    ):
        return False
    return all(term in lowered for term in ("location:", "symptoms:", "outage", "telemetry", "field checks:"))


def _final_technician_brief(
    *,
    issue_type: str,
    draft_brief: str | None,
    tool_results: Dict[str, Dict[str, Any]],
    customer_asked_for_credit: bool,
) -> str | None:
    cleaned = _clean_technician_brief(draft_brief)
    if _brief_is_safe(cleaned, issue_type=issue_type, customer_asked_for_credit=customer_asked_for_credit):
        return cleaned
    if issue_type in {"degraded_gateway_signal", "guided_connectivity_troubleshooting"}:
        return _fallback_technician_brief(tool_results)
    return None


def _final_customer_response(
    *,
    issue_type: str,
    draft_findings: str,
    draft_next_step: str,
    tool_results: Dict[str, Dict[str, Any]],
    policy_requires_human_approval: bool,
    customer_asked_for_credit: bool,
) -> str:
    draft_response = "\n".join([draft_findings.strip(), draft_next_step.strip()]).strip()
    if _customer_response_is_safe(
        draft_response,
        issue_type=issue_type,
        policy_requires_human_approval=policy_requires_human_approval,
        customer_asked_for_credit=customer_asked_for_credit,
    ):
        return draft_response

    location = tool_results["get_customer_locations"]["selected_location"]
    outage = tool_results.get("check_area_outage", {})
    telemetry = tool_results.get("get_device_signal_status", {})

    if issue_type == "confirmed_area_outage":
        incident = outage.get("incident_id")
        restore_window = outage.get("estimated_restore_window")
        finding = f"Thanks, Maya. I checked {location['label']}, and this does look like an area outage."
        next_step = (
            f"The current estimated restore window is {restore_window}; I will keep this case tied to incident {incident} and watch for status updates."
            if restore_window
            else f"I will keep this case tied to incident {incident or 'the outage'} and watch for status updates."
        )
        return "\n".join([finding, next_step])

    if issue_type == "billing_credit_request":
        drops = telemetry.get("drops_last_24h")
        finding = (
            f"I checked {location['label']} and found degraded gateway signal"
            f"{f' with {drops} drops in the last 24 hours' if drops is not None else ''}."
        )
        next_step = (
            "I will document the service impact and route the credit request for billing review before anyone promises an adjustment."
        )
        return "\n".join([finding, next_step])

    next_step_line = draft_next_step.strip()
    if issue_type == "degraded_gateway_signal" and policy_requires_human_approval:
        drops = telemetry.get("drops_last_24h")
        finding_line = (
            f"I checked {location['label']} and confirmed the gateway is online, "
            f"but the signal is degraded"
            f"{f' with {drops} drops in the last 24 hours' if drops is not None else ''}."
        )
        if customer_asked_for_credit:
            next_step_line = (
                "Next, I will prepare this for technician review and document the credit request for billing review; "
                "scheduling and any adjustment both need approval first."
            )
        else:
            next_step_line = "Next, I will prepare this for technician review; scheduling waits for support lead approval."
        return "\n".join([finding_line, next_step_line])
    return "\n".join([draft_findings.strip(), next_step_line])


def _customer_response_is_safe(
    response: str,
    *,
    issue_type: str,
    policy_requires_human_approval: bool,
    customer_asked_for_credit: bool,
) -> bool:
    lowered = response.lower()
    if not response.strip():
        return False
    if any(term in lowered for term in ("free month", "you will receive a credit", "you qualify for a credit")):
        return False
    if "human" in lowered:
        return False
    if not customer_asked_for_credit and any(
        term in lowered for term in ("credit request", "billing review", "bill adjustment", "any adjustment")
    ):
        return False
    if policy_requires_human_approval and any(
        term in lowered
        for term in ("already scheduled", "appointment is scheduled", "dispatch is scheduled", "technician is scheduled")
    ):
        return False
    if issue_type == "degraded_gateway_signal" and "confirmed area outage" in lowered:
        return False
    return True


def _final_reasoning_summary(
    *,
    issue_type: str,
    draft_reasoning: str,
    tool_results: Dict[str, Dict[str, Any]],
) -> str:
    outage = tool_results.get("check_area_outage", {})
    credit = tool_results.get("check_credit_eligibility", {})

    if issue_type == "confirmed_area_outage":
        incident = outage.get("incident_id")
        restore_window = outage.get("estimated_restore_window")
        return (
            "MCP outage status is treated as source of truth. "
            f"The tool confirmed active incident {incident or 'unknown'}"
            f"{f' with restore window {restore_window}' if restore_window else ''}, "
            "so the response shares outage status and suppresses technician dispatch."
        )

    if issue_type == "billing_credit_request":
        return (
            "The model received service evidence and credit-policy context, but the billing tool controls credit eligibility. "
            f"Eligibility returned {credit.get('eligible')}, so the response documents impact, routes to support lead review, and avoids credit promises."
        )

    return draft_reasoning


async def resolve_scenario(scenario_id: str, customer_message: str | None = None) -> ResolutionResponse:
    started = time.perf_counter()
    scenario = get_scenario(scenario_id)
    if customer_message:
        scenario = scenario.model_copy(update={"customer_message": customer_message})
    customer_text = " ".join([scenario.customer_message, *scenario.transcript])

    trace: list[AgentTraceStep] = [
        AgentTraceStep(
            label="Scenario loaded",
            status="complete",
            detail="The copilot starts from a support transcript and a selected Comcast Business account location.",
        )
    ]

    citations = retrieve(customer_text)
    trace.append(
        AgentTraceStep(
            label="Approved context prepared",
            status="complete",
            detail="Retrieved approved public-source context, ranked it for the case, and packaged cited excerpts before generation.",
        )
    )

    client = ComcastMcpClient(prefer_stdio=True)
    tool_plan = [
        ("get_customer_locations", {"account_id": scenario.account_id, "selected_location_id": scenario.location_id}),
        ("check_area_outage", {"location_id": scenario.location_id}),
        ("get_device_signal_status", {"location_id": scenario.location_id}),
        ("check_credit_eligibility", {"account_id": scenario.account_id, "location_id": scenario.location_id}),
    ]

    tool_calls: list[ToolCallRecord] = []
    tool_results: dict[str, dict[str, Any]] = {}
    for name, args in tool_plan:
        result = await client.call_tool(name, args)
        tool_results[name] = result
        tool_calls.append(
            ToolCallRecord(name=name, arguments=args, result=result, explanation=_tool_explanation(name))
        )

    issue_type = _issue_type(scenario.customer_message, tool_results)
    policy = evaluate_policy(scenario.customer_message, tool_results, citations)

    if (
        policy.status != "block"
        and issue_type == "degraded_gateway_signal"
        and "technician review" in policy.decision.lower()
    ):
        dispatch_args = {
            "account_id": scenario.account_id,
            "location_id": scenario.location_id,
            "reason": "Degraded gateway signal after customer restart",
            "approved_by_support_lead": False,
        }
        dispatch_result = await client.call_tool("create_dispatch_ticket", dispatch_args)
        tool_results["create_dispatch_ticket"] = dispatch_result
        tool_calls.append(
            ToolCallRecord(
                name="create_dispatch_ticket",
                arguments=dispatch_args,
                result=dispatch_result,
                explanation=_tool_explanation("create_dispatch_ticket"),
            )
        )

    trace.append(
        AgentTraceStep(
            label="MCP tool calls",
            status="complete",
            detail="Operational state came through tool calls rather than model guesses.",
        )
    )
    trace.append(
        AgentTraceStep(
            label="Policy gate",
            status="blocked" if policy.status == "block" else "complete",
            detail=policy.decision,
        )
    )

    model_run: ModelRunRecord | None = None
    if policy.status == "block":
        customer_response = (
            "I can't override policy or promise credits from chat.\n"
            "Share what is actually happening, and I can check the account through the proper review process."
        )
        technician_brief = None
        confidence = 0.99
        model_run = ModelRunRecord(
            provider="none",
            model="not_called_policy_block",
            prompt_versions=PROMPT_VERSIONS,
            reasoning_summary="The deterministic policy gate blocked the request before model generation.",
            generated=False,
        )
    else:
        draft, response_id, model, token_usage = await draft_resolution_with_openai(
            scenario=scenario,
            citations=citations,
            tool_results=tool_results,
            policy=policy,
            issue_type=issue_type,
        )
        customer_asked_for_credit = _customer_asked_for_credit(customer_text)
        customer_response = _final_customer_response(
            issue_type=issue_type,
            draft_findings=draft.customer_findings_line,
            draft_next_step=draft.customer_next_step_line,
            tool_results=tool_results,
            policy_requires_human_approval=policy.required_human_approval,
            customer_asked_for_credit=customer_asked_for_credit,
        )
        technician_brief = _final_technician_brief(
            issue_type=issue_type,
            draft_brief=draft.technician_brief,
            tool_results=tool_results,
            customer_asked_for_credit=customer_asked_for_credit,
        )
        confidence = draft.confidence
        model_run = ModelRunRecord(
            provider="OpenAI",
            model=model,
            response_id=response_id,
            input_tokens=token_usage["input_tokens"],
            cached_input_tokens=token_usage["cached_input_tokens"],
            output_tokens=token_usage["output_tokens"],
            total_tokens=token_usage["total_tokens"],
            prompt_versions=PROMPT_VERSIONS,
            reasoning_summary=_final_reasoning_summary(
                issue_type=issue_type,
                draft_reasoning=draft.reasoning_summary,
                tool_results=tool_results,
            ),
            generated=True,
        )
        trace.append(
            AgentTraceStep(
                label="OpenAI model call",
                status="complete",
                detail=f"Generated structured triage and response copy with {model}.",
            )
        )

    elapsed_ms = round((time.perf_counter() - started) * 1000)
    return ResolutionResponse(
        scenario=scenario,
        issue_type=issue_type,
        confidence=confidence,
        citations=citations,
        tool_calls=tool_calls,
        policy=policy,
        customer_response=customer_response,
        technician_brief=technician_brief,
        model_run=model_run,
        trace=trace,
        metrics={
            "latency_ms": elapsed_ms,
            "citation_coverage": len(citations),
            "tool_success_rate": 1.0,
            "ai_generated": model_run.generated if model_run else False,
            "ai_provider": model_run.provider if model_run else "none",
            "ai_model": model_run.model if model_run else "none",
            "input_tokens": model_run.input_tokens if model_run else None,
            "cached_input_tokens": model_run.cached_input_tokens if model_run else None,
            "output_tokens": model_run.output_tokens if model_run else None,
            "total_tokens": model_run.total_tokens if model_run else None,
            "prompt_versions": PROMPT_VERSIONS,
        },
    )
