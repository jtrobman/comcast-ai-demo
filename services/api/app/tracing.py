from __future__ import annotations

"""LangSmith tracing helpers.

Tracing activates only when the langsmith SDK sees LANGSMITH_TRACING=true and a
valid LANGSMITH_API_KEY (both read automatically from the environment). When
langsmith is not installed, or tracing is disabled, ``traceable`` and
``wrap_openai`` are transparent no-ops, so the demo runs unchanged without
LangSmith configured. This mirrors the direct-fallback resilience used by the
MCP client.
"""

from typing import Any, Callable, TypeVar

F = TypeVar("F", bound=Callable[..., Any])

try:
    from langsmith import traceable as _traceable
    from langsmith.wrappers import wrap_openai as _wrap_openai

    LANGSMITH_AVAILABLE = True

    def traceable(*args: Any, **kwargs: Any) -> Any:
        return _traceable(*args, **kwargs)

    def wrap_openai(client: Any) -> Any:
        return _wrap_openai(client)

except Exception:  # pragma: no cover - langsmith is an optional dependency
    LANGSMITH_AVAILABLE = False

    def traceable(*args: Any, **kwargs: Any) -> Any:
        # Support both bare @traceable and parameterized @traceable(...) usage.
        if len(args) == 1 and callable(args[0]) and not kwargs:
            return args[0]

        def decorator(func: F) -> F:
            return func

        return decorator

    def wrap_openai(client: Any) -> Any:
        return client
