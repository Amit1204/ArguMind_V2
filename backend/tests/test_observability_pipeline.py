"""Metrics, the operations summary, breakers and rate limits wired into the pipeline and API."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.runs import get_rate_limiter, get_run_executor
from app.llm.base import LLMRateLimitError, LLMRequest, LLMUnavailableError
from app.llm.instrumented import InstrumentedProvider
from app.llm.mock import MockProvider
from app.observability import metrics
from app.observability.ops import OPS, percentile
from app.reliability.circuit import CircuitBreaker
from app.reliability.ratelimit import RateLimiter
from app.services.run_executor import RunExecutor
from app.sources.cache import MemorySourceCache
from app.sources.http import SourceUnavailableError
from app.sources.models import SourceKind
from app.sources.service import SourceSearchService
from tests.pipeline_fakes import make_runner
from tests.test_sources_service import FakeClient, make_source

QUESTION = "Do large language models understand language?"


@pytest.fixture(autouse=True)
def _reset_ops() -> None:
    OPS.reset()


def test_percentile_helper() -> None:
    assert percentile([], 50) is None
    assert percentile([5, 1, 3], 50) == 3
    assert percentile([1, 2, 3, 4, 100], 95) == 100


def test_a_run_records_run_stage_and_llm_metrics() -> None:
    runner, store, _, provider = make_runner()
    before = metrics.sample("argumind_runs_total", {"status": "answered"}) or 0.0
    runner.execute(QUESTION)
    assert metrics.sample("argumind_runs_total", {"status": "answered"}) == before + 1
    assert (metrics.sample("argumind_stages_total", {"stage": "plan", "status": "ok"}) or 0) >= 1
    snap = OPS.snapshot()
    assert snap["runs"]["total"] == 1 and snap["runs"]["by_status"] == {"answered": 1}
    assert snap["runs"]["latency_ms_p50"] is not None and snap["runs"]["active"] == 0
    assert set(snap["stages"]) >= {"plan", "gather", "extract", "verify"}
    assert snap["stages"]["plan"]["count"] == 1


def test_instrumented_provider_records_outcomes_and_opens_breaker() -> None:
    inner = MockProvider()
    inner.fail_with("plan", LLMRateLimitError("quota"))
    breaker = CircuitBreaker("llm:test", failure_threshold=2, recovery_seconds=60)
    provider = InstrumentedProvider(inner, breaker)
    request = LLMRequest("s", "QUESTION:\nq?\n", purpose="plan")
    for _ in range(2):
        with pytest.raises(LLMRateLimitError):
            provider.complete(request)
    assert breaker.state.value == "open"
    with pytest.raises(LLMUnavailableError, match="circuit"):
        provider.complete(request)  # fails fast without touching the inner provider
    assert len(inner.calls) == 2
    snap = OPS.snapshot()["llm"]
    assert snap["by_outcome"] == {"rate_limited": 2, "circuit_open": 1}
    assert metrics.sample("argumind_circuit_state", {"name": "llm:test"}) == 2.0
    # a successful call is recorded with tokens
    ok = InstrumentedProvider(MockProvider())
    ok.complete(LLMRequest("s", "SOURCES:\n- [arxiv:1] x\n", purpose="answer"))
    assert OPS.snapshot()["llm"]["input_tokens"] > 0


def test_source_breaker_opens_after_consecutive_failures_and_short_circuits() -> None:
    clock = {"t": 0.0}
    broken = FakeClient(SourceKind.ARXIV, error=SourceUnavailableError("429"))
    breaker = CircuitBreaker(
        "source:arxiv", failure_threshold=2, recovery_seconds=120, clock=lambda: clock["t"]
    )
    service = SourceSearchService(
        [broken],
        MemorySourceCache(),
        breakers={SourceKind.ARXIV: breaker},
        clock=lambda: clock["t"],
    )
    service.search(SourceKind.ARXIV, "q1")
    service.search(SourceKind.ARXIV, "q2")
    assert breaker.state.value == "open"
    outcome = service.search(SourceKind.ARXIV, "q3")
    assert outcome.error and "circuit 'source:arxiv' is open" in outcome.error
    assert broken.calls == 2  # third search never reached the client
    assert OPS.snapshot()["sources"]["arxiv"]["circuit_open"] == 1
    clock["t"] += 121
    broken.error = None
    broken.results = [make_source(1)]
    recovered = service.search(SourceKind.ARXIV, "q4")
    assert recovered.sources and breaker.state.value == "closed"
    assert service.breaker_states()["arxiv"]["state"] == "closed"


def test_rate_limit_and_queue_cap_return_json_errors_with_retry_after(
    app: FastAPI, client: TestClient
) -> None:
    runner, store, _, _ = make_runner()
    executor = RunExecutor(runner, store, inline=True)
    limiter = RateLimiter(1)  # one shared instance, as app.state holds in production
    app.dependency_overrides[get_run_executor] = lambda: executor
    app.dependency_overrides[get_rate_limiter] = lambda: limiter
    first = client.post("/api/v1/runs", json={"question": QUESTION, "client_id": "c1"})
    assert first.status_code == 200
    second = client.post("/api/v1/runs", json={"question": QUESTION, "client_id": "c1"})
    assert second.status_code == 429
    assert second.headers["retry-after"].isdigit()
    body = second.json()
    assert (
        body["detail"].startswith("rate limit")
        and body["request_id"] == second.headers["x-request-id"]
    )
    other = client.post("/api/v1/runs", json={"question": QUESTION, "client_id": "c2"})
    assert other.status_code == 200
    assert OPS.snapshot()["rate_limited"] == 1


def test_error_envelope_carries_request_id_on_404(app: FastAPI, client: TestClient) -> None:
    runner, store, _, _ = make_runner()
    app.dependency_overrides[get_run_executor] = lambda: RunExecutor(runner, store, inline=True)
    response = client.get(
        "/api/v1/runs/00000000-0000-0000-0000-000000000000",
        headers={"X-Request-ID": "trace-404-0001"},
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "run not found", "request_id": "trace-404-0001"}


def test_operations_endpoint_shape(app: FastAPI, client: TestClient) -> None:
    runner, store, _, _ = make_runner()
    app.dependency_overrides[get_run_executor] = lambda: RunExecutor(runner, store, inline=True)
    client.post("/api/v1/runs", json={"question": QUESTION})
    body = client.get("/api/v1/system/operations").json()
    assert body["runs"]["total"] == 1 and "latency_ms_p95" in body["runs"]
    assert body["llm"]["calls"] > 0 and "by_purpose" in body["llm"]
    assert "circuits" in body and body["limits"]["runs_per_minute_per_client"] >= 1
    assert isinstance(body["stages"], dict)


def test_exhausted_model_budget_degrades_to_inconclusive_without_a_500() -> None:
    """Failure drill: the daily budget is gone before the run starts."""
    from app.llm.budget import DailyRequestBudget
    from app.llm.factory import BudgetedProvider
    from app.pipeline.detail import build_run_detail

    budget = DailyRequestBudget(1)
    budget.consume()
    provider = BudgetedProvider(MockProvider(), budget)
    runner, store, _, _ = make_runner(provider=provider)
    detail = build_run_detail(store, runner.execute(QUESTION))
    assert detail is not None
    assert detail.status == "inconclusive" and detail.error is None
    by_name = {s.name: s for s in detail.stages}
    assert by_name["plan"].status == "failed" and by_name["extract"].status == "failed"
    assert detail.claims == [] and detail.usage.llm_calls == 0
    assert any("Planning failed" in c for c in detail.caveats)
    assert OPS.snapshot()["llm"]["by_outcome"].get("budget", 0) >= 2


def test_open_model_circuit_makes_fallbacks_engage_immediately() -> None:
    from app.pipeline.detail import build_run_detail

    breaker = CircuitBreaker("llm:drill", failure_threshold=1, recovery_seconds=600)
    breaker.record_failure()  # already open
    inner = MockProvider()
    runner, store, _, _ = make_runner(provider=inner)
    runner.provider = InstrumentedProvider(inner, breaker)
    detail = build_run_detail(store, runner.execute(QUESTION))
    assert detail is not None and detail.status == "inconclusive"
    assert inner.calls == []  # nothing reached the provider
    assert OPS.snapshot()["llm"]["by_outcome"] == {"circuit_open": OPS.snapshot()["llm"]["calls"]}


def test_metrics_exposition_includes_pipeline_series(client: TestClient) -> None:
    runner, _, _, _ = make_runner()
    runner.execute(QUESTION)
    text = client.get("/metrics").text
    for name in (
        "argumind_runs_total",
        "argumind_stage_duration_seconds",
        "argumind_llm_calls_total",
        "argumind_source_calls_total",
        "argumind_runs_active",
    ):
        assert name in text
