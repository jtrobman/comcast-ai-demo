from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class SupportScenario(BaseModel):
    id: str
    title: str
    customer_name: str
    account_id: str
    location_id: str
    customer_message: str
    transcript: List[str]


class SourceCitation(BaseModel):
    source_id: str
    title: str
    url: str
    excerpt: str
    score: float
    score_kind: str = "lexical_overlap"


class ToolCallRecord(BaseModel):
    name: str
    arguments: Dict[str, Any]
    result: Dict[str, Any]
    explanation: str


class PolicyVerdict(BaseModel):
    status: Literal["pass", "revise", "block"]
    decision: str
    reasons: List[str]
    required_human_approval: bool = False


class AgentTraceStep(BaseModel):
    label: str
    status: Literal["complete", "warning", "blocked"]
    detail: str


class ModelRunRecord(BaseModel):
    provider: str
    model: str
    response_id: Optional[str] = None
    input_tokens: Optional[int] = None
    cached_input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    prompt_versions: List[str]
    reasoning_summary: Optional[str] = None
    generated: bool = False


class ResolutionResponse(BaseModel):
    scenario: SupportScenario
    issue_type: str
    confidence: float
    citations: List[SourceCitation]
    tool_calls: List[ToolCallRecord]
    policy: PolicyVerdict
    customer_response: str
    technician_brief: Optional[str] = None
    model_run: Optional[ModelRunRecord] = None
    trace: List[AgentTraceStep]
    metrics: Dict[str, Any] = Field(default_factory=dict)


class ResolveRequest(BaseModel):
    scenario_id: str = "intermittent_signal"
    customer_message: Optional[str] = None


class EvalCaseResult(BaseModel):
    id: str
    passed: bool
    checks: Dict[str, bool]
    notes: str


class EvalRunResponse(BaseModel):
    suite: str
    pass_rate: float
    total: int
    passed: int
    cases: List[EvalCaseResult]
