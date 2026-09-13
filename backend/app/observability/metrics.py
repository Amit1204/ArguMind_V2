"""Prometheus metrics for HTTP, runs, stages, model calls, source calls and breakers.

A dedicated registry keeps tests that build several apps from colliding on
duplicate collector names. Labels are bounded: stage names, purposes, models
and source kinds are small fixed sets; user input never becomes a label.
"""

from __future__ import annotations

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

REGISTRY = CollectorRegistry()

_LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120)
_RUN_BUCKETS = (1, 5, 10, 20, 30, 45, 60, 90, 120, 180, 300, 600)

HTTP_REQUESTS = Counter(
    "argumind_http_requests_total",
    "HTTP requests handled, by method, route template and status code.",
    ["method", "route", "status"],
    registry=REGISTRY,
)
HTTP_LATENCY = Histogram(
    "argumind_http_request_duration_seconds",
    "HTTP request latency by route template.",
    ["route"],
    buckets=_LATENCY_BUCKETS,
    registry=REGISTRY,
)

RUNS = Counter(
    "argumind_runs_total", "Pipeline runs finished, by final status.", ["status"], registry=REGISTRY
)
RUN_DURATION = Histogram(
    "argumind_run_duration_seconds",
    "Wall-clock duration of finished runs.",
    ["status"],
    buckets=_RUN_BUCKETS,
    registry=REGISTRY,
)
RUNS_ACTIVE = Gauge("argumind_runs_active", "Runs currently executing.", registry=REGISTRY)

STAGES = Counter(
    "argumind_stages_total",
    "Pipeline stages executed, by stage and outcome.",
    ["stage", "status"],
    registry=REGISTRY,
)
STAGE_DURATION = Histogram(
    "argumind_stage_duration_seconds",
    "Duration of pipeline stages.",
    ["stage"],
    buckets=_LATENCY_BUCKETS,
    registry=REGISTRY,
)

LLM_CALLS = Counter(
    "argumind_llm_calls_total",
    "Model calls, by purpose and outcome "
    "(ok, rate_limited, unavailable, timeout, format, budget, circuit_open, error).",
    ["purpose", "outcome"],
    registry=REGISTRY,
)
LLM_LATENCY = Histogram(
    "argumind_llm_call_duration_seconds",
    "Model call latency by model.",
    ["model"],
    buckets=_LATENCY_BUCKETS,
    registry=REGISTRY,
)
LLM_TOKENS = Counter(
    "argumind_llm_tokens_total",
    "Tokens consumed, by model and direction (input, output).",
    ["model", "direction"],
    registry=REGISTRY,
)
LLM_FALLBACKS = Counter(
    "argumind_llm_fallbacks_total",
    "Calls served by a lower tier after the requested model failed.",
    registry=REGISTRY,
)

SOURCE_CALLS = Counter(
    "argumind_source_calls_total",
    "Source lookups, by kind and outcome (ok, cached, error, circuit_open).",
    ["kind", "outcome"],
    registry=REGISTRY,
)
SOURCE_LATENCY = Histogram(
    "argumind_source_call_duration_seconds",
    "Source lookup latency by kind (uncached calls).",
    ["kind"],
    buckets=_LATENCY_BUCKETS,
    registry=REGISTRY,
)

CIRCUIT_STATE = Gauge(
    "argumind_circuit_state",
    "Circuit breaker state: 0 closed, 1 half-open, 2 open.",
    ["name"],
    registry=REGISTRY,
)
RATE_LIMITED = Counter(
    "argumind_rate_limited_total", "Requests refused by the rate limiter.", registry=REGISTRY
)
RETRIES = Counter(
    "argumind_retries_total",
    "Retries performed, by target (llm, source).",
    ["target"],
    registry=REGISTRY,
)

_STATE_VALUE = {"closed": 0, "half_open": 1, "open": 2}


def observe_http(method: str, route: str, status: int, seconds: float) -> None:
    HTTP_REQUESTS.labels(method=method, route=route, status=str(status)).inc()
    HTTP_LATENCY.labels(route=route).observe(seconds)


def observe_run(status: str, seconds: float) -> None:
    RUNS.labels(status=status).inc()
    RUN_DURATION.labels(status=status).observe(seconds)


def observe_stage(stage: str, status: str, seconds: float) -> None:
    STAGES.labels(stage=stage, status=status).inc()
    STAGE_DURATION.labels(stage=stage).observe(seconds)


def observe_llm(
    purpose: str,
    model: str,
    outcome: str,
    seconds: float,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> None:
    LLM_CALLS.labels(purpose=purpose, outcome=outcome).inc()
    if outcome == "ok":
        LLM_LATENCY.labels(model=model).observe(seconds)
        LLM_TOKENS.labels(model=model, direction="input").inc(input_tokens)
        LLM_TOKENS.labels(model=model, direction="output").inc(output_tokens)


def observe_source(kind: str, outcome: str, seconds: float | None = None) -> None:
    SOURCE_CALLS.labels(kind=kind, outcome=outcome).inc()
    if seconds is not None and outcome in ("ok", "error"):
        SOURCE_LATENCY.labels(kind=kind).observe(seconds)


def set_circuit_state(name: str, state: str) -> None:
    CIRCUIT_STATE.labels(name=name).set(_STATE_VALUE.get(state, 0))


def render() -> tuple[bytes, str]:
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST


def sample(name: str, labels: dict[str, str] | None = None) -> float | None:
    """Read one sample value (tests and the operations summary)."""
    return REGISTRY.get_sample_value(name, labels or {})
