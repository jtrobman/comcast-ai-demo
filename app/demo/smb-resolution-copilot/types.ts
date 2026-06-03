export type Scenario = {
  id: string;
  title: string;
  customer_name: string;
  account_id: string;
  location_id: string;
  customer_message: string;
  transcript: string[];
};

export type Citation = {
  source_id: string;
  title: string;
  url: string;
  excerpt: string;
  score: number;
  score_kind?: string;
};

export type ToolCall = {
  name: string;
  arguments: Record<string, unknown>;
  result: Record<string, unknown>;
  explanation: string;
};

export type Policy = {
  status: "pass" | "revise" | "block";
  decision: string;
  reasons: string[];
  required_human_approval: boolean;
};

export type TraceStep = {
  label: string;
  status: "complete" | "warning" | "blocked";
  detail: string;
};

export type ModelRun = {
  provider: string;
  model: string;
  response_id: string | null;
  input_tokens: number | null;
  cached_input_tokens?: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  prompt_versions: string[];
  reasoning_summary: string | null;
  generated: boolean;
};

export type Resolution = {
  scenario: Scenario;
  issue_type: string;
  confidence: number;
  citations: Citation[];
  tool_calls: ToolCall[];
  policy: Policy;
  customer_response: string;
  technician_brief: string | null;
  model_run: ModelRun | null;
  trace: TraceStep[];
  metrics: {
    latency_ms: number;
    citation_coverage: number;
    tool_success_rate: number;
    ai_generated: boolean;
    ai_provider: string;
    ai_model: string;
    input_tokens?: number | null;
    cached_input_tokens?: number | null;
    output_tokens?: number | null;
    total_tokens?: number | null;
    prompt_versions: string[];
  };
};

export type EvalCase = {
  id: string;
  passed: boolean;
  checks: Record<string, boolean>;
  notes: string;
};

export type EvalRun = {
  suite: string;
  pass_rate: number;
  total: number;
  passed: number;
  cases: EvalCase[];
};
