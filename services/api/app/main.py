from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[3]
load_dotenv(ROOT / ".env", override=True)

from .agent import resolve_scenario
from .evals import run_eval_suite
from .llm import AiConfigurationError, AiGenerationError
from .models import EvalRunResponse, ResolutionResponse, ResolveRequest
from .scenarios import SCENARIOS


def _cors_origins() -> list[str]:
    configured = os.getenv("CORS_ORIGINS", "")
    origins = [origin.strip() for origin in configured.split(",") if origin.strip()]
    return origins or ["http://localhost:3000", "http://127.0.0.1:3000"]


app = FastAPI(title="Comcast Business Resolution Copilot API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/scenarios")
async def scenarios() -> list[dict[str, str]]:
    return [{"id": scenario.id, "title": scenario.title} for scenario in SCENARIOS.values()]


@app.post("/resolve", response_model=ResolutionResponse)
async def resolve(request: ResolveRequest) -> ResolutionResponse:
    try:
        return await resolve_scenario(request.scenario_id, request.customer_message)
    except AiConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except AiGenerationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/evals/run", response_model=EvalRunResponse)
async def evals() -> EvalRunResponse:
    return await run_eval_suite()
