from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List

from openai import APIConnectionError, APIStatusError, AuthenticationError, AsyncOpenAI, OpenAIError
from pydantic import BaseModel, Field

from .models import PolicyVerdict, SourceCitation, SupportScenario
from .tracing import traceable, wrap_openai


ROOT = Path(__file__).resolve().parents[3]
PROMPT_DIR = ROOT / "prompts"

PROMPT_VERSIONS = [
    "triage_agent_20260508.md",
    "customer_response_20260508.md",
    "policy_guardrail_20260508.md",
    "dispatch_summary_20260508.md",
]


class AiConfigurationError(RuntimeError):
    pass


class AiGenerationError(RuntimeError):
    pass


class ModelResolutionDraft(BaseModel):
    issue_type: str = Field(description="Short machine-readable issue category.")
    confidence: float = Field(description="Confidence from 0 to 1 based on provided evidence.")
    reasoning_summary: str = Field(description="Brief explanation of how the model used RAG, tools, and policy.")
    customer_findings_line: str = Field(
        description=(
            "One short support-rep chat line saying what was checked and found. "
            "Use past tense. Stay inside the customer's stated issue."
        )
    )
    customer_next_step_line: str = Field(
        description=(
            "One short support-rep chat line saying the next step. "
            "If dispatch needs approval, say technician review or approval, not scheduled dispatch. "
            "Do not mention credits, billing, refunds, or compensation unless the customer explicitly asked."
        )
    )
    technician_brief: str | None = Field(
        description=(
            "Field technician brief as compact markdown bullets with labels, or null when dispatch is not relevant. "
            "Use at most 6 bullets. Write for the technician receiving the brief. "
            "Use Field checks instead of Recommended checks when describing on-site work. "
            "Do not tell the technician to dispatch themselves."
        )
    )


def configured_model() -> str:
    return os.getenv("OPENAI_MODEL", "gpt-5.1")


def configured_reasoning_effort() -> str:
    return os.getenv("OPENAI_REASONING_EFFORT", "minimal")


def configured_prompt_cache_key() -> str:
    return os.getenv("OPENAI_PROMPT_CACHE_KEY", "comcast-resolution-copilot-20260508")


def _read_prompt(name: str) -> str:
    return (PROMPT_DIR / name).read_text(encoding="utf-8")


def _system_prompt() -> str:
    return "\n\n".join(
        [
            _read_prompt("triage_agent_20260508.md"),
            _read_prompt("customer_response_20260508.md"),
            _read_prompt("dispatch_summary_20260508.md"),
            (
                "Architecture rule: MCP tool results and deterministic policy verdicts are more authoritative "
                "than model inference. Do not promise credits, outage status, dispatch scheduling, or fix times "
                "unless the supplied evidence explicitly allows it."
            ),
        ]
    )


def _customer_asked_for_credit(scenario: SupportScenario) -> bool:
    text = " ".join([scenario.customer_message, *scenario.transcript]).lower()
    return any(term in text for term in ("credit", "refund", "bill", "billing", "compensation", "money back"))


def _token_usage(response: Any) -> dict[str, int | None]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {"input_tokens": None, "cached_input_tokens": None, "output_tokens": None, "total_tokens": None}
    if hasattr(usage, "model_dump"):
        usage_data = usage.model_dump()
    elif isinstance(usage, dict):
        usage_data = usage
    else:
        usage_data = usage.__dict__
    input_details = usage_data.get("input_tokens_details") or {}
    return {
        "input_tokens": usage_data.get("input_tokens"),
        "cached_input_tokens": input_details.get("cached_tokens"),
        "output_tokens": usage_data.get("output_tokens"),
        "total_tokens": usage_data.get("total_tokens"),
    }


@traceable(run_type="chain", name="draft_resolution")
async def draft_resolution_with_openai(
    *,
    scenario: SupportScenario,
    citations: List[SourceCitation],
    tool_results: Dict[str, Dict[str, Any]],
    policy: PolicyVerdict,
    issue_type: str,
) -> tuple[ModelResolutionDraft, str | None, str, dict[str, int | None]]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise AiConfigurationError("OPENAI_API_KEY is missing. Add it to .env before running the AI demo.")

    model = configured_model()
    # wrap_openai instruments the OpenAI client so the underlying Responses API
    # call (token usage, latency, prompt/response) shows up as a child span in
    # LangSmith. It is a no-op when tracing is disabled.
    client = wrap_openai(AsyncOpenAI(api_key=api_key))
    payload = {
        "scenario": scenario.model_dump(),
        "retrieved_sources": [citation.model_dump() for citation in citations],
        "mcp_tool_results": tool_results,
        "deterministic_issue_type": issue_type,
        "deterministic_policy": {
            "status": policy.status,
            "decision": policy.decision,
            "reasons": policy.reasons,
            "requires_support_lead_approval": policy.required_human_approval,
        },
        "customer_asked_for_credit_or_billing_help": _customer_asked_for_credit(scenario),
        "required_output_behavior": {
            "use_tools_as_source_of_truth": True,
            "cite_public_guidance_when_relevant": True,
            "do_not_override_policy": True,
            "do_not_schedule_dispatch_without_support_lead_approval": True,
            "do_not_promise_billing_credit_without_eligibility": True,
            "do_not_discuss_credit_unless_customer_asked": True,
            "customer_response_exact_short_lines": 2,
            "customer_response_use_past_tense_for_completed_checks": True,
            "dispatch_language_must_say_review_or_approval_not_scheduled": True,
            "confirmed_outage_updates_do_not_require_support_lead_approval": True,
            "confirmed_outage_do_not_create_technician_brief": True,
            "credit_request_separate_service_findings_from_billing_approval": True,
            "technician_brief_written_for_assigned_technician": True,
            "technician_brief_do_not_say_dispatch_technician": True,
            "technician_brief_use_field_checks_label": True,
            "technician_brief_customer_context_not_customer_communication_notes": True,
            "technician_brief_max_bullets": 6,
        },
    }

    try:
        response = await client.responses.parse(
            model=model,
            instructions=_system_prompt(),
            input=[
                {
                    "role": "user",
                    "content": (
                        "Draft a Comcast Business-inspired support resolution using only the supplied evidence. "
                        "The customer response must feel like the next chat message from the support rep. "
                        "Return customer_findings_line and customer_next_step_line as two separate short chat lines. "
                        "Use past tense for checks already completed, and do not bring up unrelated policy topics. "
                        "If dispatch needs approval, say dispatch review or approval, not scheduled dispatch. "
                        "Use support lead approval language when approval is required; avoid generic person labels. "
                        "Format the technician brief as short markdown bullets. "
                        "In the technician brief, use Field checks for technician tasks and Customer context for business impact. "
                        "Do not write 'dispatch technician' or 'customer communication notes' in the technician brief. "
                        "Return the structured object exactly as requested.\n\n"
                        f"{json.dumps(payload, indent=2)}"
                    ),
                }
            ],
            text_format=ModelResolutionDraft,
            max_output_tokens=2400,
            prompt_cache_key=configured_prompt_cache_key(),
            prompt_cache_retention="in_memory",
            reasoning={"effort": configured_reasoning_effort()},
            text={"verbosity": "low"},
            store=False,
        )
    except AuthenticationError as exc:
        raise AiGenerationError("OpenAI rejected the API key. Update OPENAI_API_KEY in .env and restart the API.") from exc
    except APIConnectionError as exc:
        raise AiGenerationError("OpenAI could not be reached. Check network connectivity and try again.") from exc
    except APIStatusError as exc:
        if exc.status_code in {400, 404}:
            raise AiGenerationError(
                f"OpenAI returned HTTP {exc.status_code}. Check OPENAI_MODEL in .env and confirm the key has access to that model."
            ) from exc
        raise AiGenerationError(f"OpenAI returned HTTP {exc.status_code}. Check the backend logs for details.") from exc
    except OpenAIError as exc:
        raise AiGenerationError(f"OpenAI generation failed: {exc.__class__.__name__}. Check the backend logs for details.") from exc

    if response.output_parsed is None:
        status = getattr(response, "status", "unknown")
        incomplete_details = getattr(response, "incomplete_details", None)
        raise AiGenerationError(
            f"OpenAI returned no parsed structured output. status={status}; incomplete_details={incomplete_details}"
        )

    return response.output_parsed, getattr(response, "id", None), model, _token_usage(response)
