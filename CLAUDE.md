# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Comcast Business-inspired AI support-operations demo built to showcase applied-AI consulting patterns: RAG, MCP tools, agent orchestration, a deterministic policy gate, guardrails, a traceable UX, and a separate executable eval suite. All customer data, telemetry, and tool responses are mocked; nothing connects to production Comcast systems.

Two processes: a **Next.js 16 / React 19 frontend** (single demo page) talking to a **FastAPI backend** that orchestrates OpenAI generation behind deterministic guardrails, with a stdio **MCP server** providing the operational tools.

## Commands

Run from the repo root unless noted. The frontend uses npm; the Python service uses `uv`.

```bash
npm install                       # frontend deps
cd services/api && uv sync        # backend deps (then cd ../..)

npm run api                       # start FastAPI (port 8000) — loads .env via `uv --env-file`
npm run api:dev                   # same, with --reload
npm run dev                       # start Next.js dev server (port 3000)
npm run build                     # frontend production build + TypeScript check
npm run lint                      # eslint

# Backend compile check
cd services/api && uv run python -m compileall app && cd ../..

# Run the eval suite (API must be running in another terminal). Uses OpenAI tokens.
curl -s http://127.0.0.1:8000/evals/run | python3 -m json.tool
```

Demo URL: `http://localhost:3000/demo/smb-resolution-copilot`

There is no Python unit-test runner; the eval suite (`/evals/run`) is the verification path for backend behavior. To run a single eval category, temporarily narrow `case_files` in `data/evals/resolution_eval_suite_20260508.yaml`.

## Configuration

Copy `.env.example` to `.env`. Key vars: `OPENAI_API_KEY` (required — the app surfaces a 503 config error rather than faking a response if missing), `OPENAI_MODEL`, `OPENAI_REASONING_EFFORT`, `VOYAGE_API_KEY` (optional — enables embedding-based RAG, otherwise falls back to lexical scoring), `CORS_ORIGINS`, `NEXT_PUBLIC_API_URL`. Note `.env`, `.htaccess`, `data/cache/`, and `project_notes.md` are gitignored.

## Architecture

The core idea: **deterministic logic wraps the model on both sides.** Tool results and policy verdicts are authoritative; the LLM only drafts copy within bounds, and its output is re-validated and overridden when unsafe. Understand this before editing backend behavior.

The whole pipeline lives in `services/api/app/agent.py::resolve_scenario`, invoked by `POST /resolve`. Order of operations:

1. **Scenario load** — `scenarios.py` holds 4 hardcoded demo scenarios (`intermittent_signal`, `confirmed_outage`, `credit_request`, `prompt_injection`); the request may override `customer_message`.
2. **RAG** — `rag.py::retrieve` ranks the markdown corpus in `data/corpus/*.md` (front-matter `source_type: operational_rag`). Voyage embeddings + cosine + domain-hint boosts when `VOYAGE_API_KEY` is set; transparent lexical-overlap scoring otherwise. Embeddings cached in `data/cache/`.
3. **MCP tool calls** — a fixed `tool_plan` calls tools via `mcp_client.py::ComcastMcpClient`, which spawns `services/mcp_server/server.py` over **stdio** and falls back to calling `services/mcp_server/tools.py` functions **directly in-process** if stdio fails (resilience for the demo). `create_dispatch_ticket` is only added conditionally after policy allows it.
4. **Deterministic issue typing** (`_issue_type`) and **policy gate** (`policy.py::evaluate_policy`) — the policy gate runs *before* the model. It blocks prompt-injection markers outright (model is never called, `status="block"`), and computes pass/revise/block plus `required_human_approval` from tool results, not model output.
5. **Model generation** — only if not blocked. `llm.py::draft_resolution_with_openai` uses the OpenAI **Responses API** with structured parse (`ModelResolutionDraft` pydantic schema), prompt caching, and `store=False`. The system prompt is assembled from `prompts/*.md`.
6. **Post-generation guardrails** — `agent.py` does NOT trust the draft as-is. `_final_customer_response`, `_final_technician_brief`, and `_final_reasoning_summary` run the draft through `_*_is_safe` checks and substitute deterministic fallbacks built from tool data when the model strays (e.g. promises credits, mentions "human", schedules dispatch without approval, uses forbidden labels). `_clean_technician_brief` normalizes wording. **This is where most behavioral correctness lives** — if model output looks wrong in the UI, the fix is usually here or in the prompts, not in raising model effort.

`models.py` defines all pydantic request/response shapes (`ResolutionResponse` is the full traced payload sent to the UI). The frontend (`app/demo/smb-resolution-copilot/page.tsx`, types in `types.ts`) renders the trace, citations, tool calls, policy verdict, and token/cost metrics.

### Observability (LangSmith)
`tracing.py` exposes `traceable` and `wrap_openai` that degrade to no-ops when `langsmith` is absent or `LANGSMITH_TRACING != true` (same resilience ethos as the MCP fallback). Pipeline functions are decorated to form the trace tree: `resolve_scenario` (chain) → `rag_retrieve` (retriever) → `mcp_call_tool` (tool, one span per call) → `policy_gate` (chain) → `draft_resolution` (chain) wrapping the instrumented OpenAI client. `run_eval_suite` is also traced. Config is env-only (`LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`, `LANGSMITH_ENDPOINT`) — no code changes needed to toggle. When adding a new pipeline stage, decorate it with `@traceable(run_type=..., name=...)` from `tracing.py` (never import `langsmith` directly, so the no-op fallback holds).

### Evals are a separate path
`evals.py::run_eval_suite` (`GET /evals/run`) loads YAML cases from `data/evals/cases/*.yaml` (indexed by `resolution_eval_suite_*.yaml`), runs the *real* `resolve_scenario` per case, and checks assertions against a flattened response payload via path-based operators (`contains`, `not_contains_any`, `includes_all`, `exists`, etc. — see `_evaluate_assertion`). It is not part of `/resolve`. When you change agent/policy behavior, update the corresponding eval YAML.

## Conventions

- Dated artifacts use a `_YYYYMMDD` suffix (prompts, policies, eval files). `PROMPT_VERSIONS` in `llm.py` must list the prompt files actually composed into the system prompt; the same list is mirrored in `page.tsx` for display — keep them in sync.
- The MCP tool layer is split: `server.py` is thin `@mcp.tool()` wrappers; the actual mock logic and data live in `tools.py`. Add new operational behavior there.
- Backend modules use `from __future__ import annotations` and resolve repo root via `Path(__file__).resolve().parents[N]` — preserve the parent depth when moving files.
