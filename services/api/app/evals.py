from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable

import yaml

from .agent import resolve_scenario
from .models import EvalCaseResult, EvalRunResponse, ResolutionResponse


ROOT = Path(__file__).resolve().parents[3]
EVAL_DIR = ROOT / "data" / "evals"
SUITE_FILE = EVAL_DIR / "resolution_eval_suite_20260508.yaml"


def _load_suite() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    suite = yaml.safe_load(SUITE_FILE.read_text(encoding="utf-8"))
    cases: list[dict[str, Any]] = []
    for relative_path in suite["case_files"]:
        case_file = EVAL_DIR / relative_path
        category_doc = yaml.safe_load(case_file.read_text(encoding="utf-8"))
        for case in category_doc["cases"]:
            cases.append(
                {
                    **case,
                    "category": category_doc["category"],
                    "case_file": str(case_file.relative_to(ROOT)),
                }
            )
    return suite, cases


def _response_context(response: ResolutionResponse) -> dict[str, Any]:
    payload = response.model_dump()
    payload["citations"] = {
        "items": [citation.model_dump() for citation in response.citations],
        "source_ids": [citation.source_id for citation in response.citations],
    }
    payload["tools"] = {
        "items": [call.model_dump() for call in response.tool_calls],
        "names": [call.name for call in response.tool_calls],
        "transport_warnings": [
            call.result["_transport_warning"]
            for call in response.tool_calls
            if "_transport_warning" in call.result
        ],
    }
    payload["tool_results"] = {call.name: call.result for call in response.tool_calls}
    payload["model_run"] = response.model_run.model_dump() if response.model_run else {}
    return payload


def _value_at_path(payload: dict[str, Any], path: str) -> Any:
    value: Any = payload
    for part in path.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        else:
            return None
    return value


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) > 0
    return True


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, set):
        return list(value)
    return [value]


def _contains(value: Any, expected: Any) -> bool:
    if isinstance(value, str):
        return str(expected).lower() in value.lower()
    return expected in _as_list(value)


def _not_contains_any(value: Any, forbidden_values: Iterable[Any]) -> bool:
    if isinstance(value, str):
        lowered = value.lower()
        return all(str(item).lower() not in lowered for item in forbidden_values)
    values = set(_as_list(value))
    return all(item not in values for item in forbidden_values)


def _evaluate_assertion(payload: dict[str, Any], assertion: dict[str, Any]) -> bool:
    value = _value_at_path(payload, assertion["path"])

    if "equals" in assertion:
        return value == assertion["equals"]
    if "contains" in assertion:
        return _contains(value, assertion["contains"])
    if "not_contains_any" in assertion:
        return _not_contains_any(value, assertion["not_contains_any"])
    if "min_length" in assertion:
        return len(_as_list(value)) >= int(assertion["min_length"])
    if "length" in assertion:
        return len(_as_list(value)) == int(assertion["length"])
    if "includes" in assertion:
        return assertion["includes"] in _as_list(value)
    if "includes_all" in assertion:
        values = set(_as_list(value))
        return set(assertion["includes_all"]).issubset(values)
    if "includes_any" in assertion:
        values = set(_as_list(value))
        return bool(values & set(assertion["includes_any"]))
    if "excludes" in assertion:
        return assertion["excludes"] not in _as_list(value)
    if "all_start_with" in assertion:
        values = _as_list(value)
        return len(values) > 0 and all(str(item).startswith(assertion["all_start_with"]) for item in values)
    if "exists" in assertion:
        exists = _has_value(value)
        return exists if assertion["exists"] else not exists
    if "not_in" in assertion:
        return value not in assertion["not_in"]

    raise ValueError(f"Unsupported assertion operator in {assertion['name']}")


async def run_eval_suite() -> EvalRunResponse:
    suite, case_specs = _load_suite()
    responses: dict[tuple[str, str | None], ResolutionResponse] = {}
    results: list[EvalCaseResult] = []

    for spec in case_specs:
        scenario_id = spec["scenario_id"]
        customer_message = spec.get("customer_message")
        cache_key = (scenario_id, customer_message)
        if cache_key not in responses:
            responses[cache_key] = await resolve_scenario(scenario_id, customer_message)

        payload = _response_context(responses[cache_key])
        checks = {
            assertion["name"]: _evaluate_assertion(payload, assertion)
            for assertion in spec["assertions"]
        }
        results.append(
            EvalCaseResult(
                id=spec["id"],
                passed=all(checks.values()),
                checks=checks,
                notes=spec["notes"],
            )
        )

    passed = sum(1 for case in results if case.passed)
    return EvalRunResponse(
        suite=suite["suite"],
        pass_rate=round(passed / len(results), 3),
        total=len(results),
        passed=passed,
        cases=results,
    )
