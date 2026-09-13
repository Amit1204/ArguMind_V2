"""Provider wrapper adding metrics and a circuit breaker around any LLMProvider."""

from __future__ import annotations

import logging
import time

from app.llm.base import (
    LLMBudgetExceededError,
    LLMError,
    LLMProvider,
    LLMRateLimitError,
    LLMRequest,
    LLMResponse,
    LLMResponseFormatError,
    LLMTimeoutError,
    LLMUnavailableError,
    ModelTier,
)
from app.observability import metrics
from app.observability.ops import OPS
from app.reliability.circuit import CircuitBreaker, CircuitOpenError

log = logging.getLogger(__name__)

_BREAKER_FAILURES = (LLMRateLimitError, LLMUnavailableError, LLMTimeoutError)


def _outcome(exc: LLMError) -> str:
    if isinstance(exc, LLMRateLimitError):
        return "rate_limited"
    if isinstance(exc, LLMUnavailableError):
        return "unavailable"
    if isinstance(exc, LLMTimeoutError):
        return "timeout"
    if isinstance(exc, LLMResponseFormatError):
        return "format"
    if isinstance(exc, LLMBudgetExceededError):
        return "budget"
    return "error"


class InstrumentedProvider(LLMProvider):
    """Records every call's outcome, latency and tokens; opens a breaker after
    consecutive provider failures so requests fail fast instead of piling up."""

    def __init__(self, inner: LLMProvider, breaker: CircuitBreaker | None = None) -> None:
        self.inner = inner
        self.breaker = breaker
        self.name = inner.name

    def resolve_model(self, tier: ModelTier) -> str:
        return self.inner.resolve_model(tier)

    def complete(self, request: LLMRequest) -> LLMResponse:
        model = self.resolve_model(request.tier)
        if self.breaker is not None:
            try:
                self.breaker.allow()
            except CircuitOpenError as exc:
                metrics.observe_llm(request.purpose, model, "circuit_open", 0.0)
                OPS.llm(request.purpose, "circuit_open", 0)
                self._publish_state()
                raise LLMUnavailableError(str(exc)) from exc
        started = time.perf_counter()
        try:
            response = self.inner.complete(request)
        except LLMError as exc:
            elapsed = time.perf_counter() - started
            outcome = _outcome(exc)
            metrics.observe_llm(request.purpose, model, outcome, elapsed)
            OPS.llm(request.purpose, outcome, int(elapsed * 1000))
            if self.breaker is not None and isinstance(exc, _BREAKER_FAILURES):
                self.breaker.record_failure()
                self._publish_state()
            raise
        elapsed = time.perf_counter() - started
        usage = response.usage
        metrics.observe_llm(
            request.purpose,
            usage.model,
            "ok",
            elapsed,
            usage.input_tokens,
            usage.output_tokens + usage.thinking_tokens,
        )
        if usage.fallback:
            metrics.LLM_FALLBACKS.inc()
        OPS.llm(
            request.purpose,
            "ok",
            int(elapsed * 1000),
            usage.input_tokens,
            usage.output_tokens + usage.thinking_tokens,
            fallback=usage.fallback,
            cost_usd=usage.estimated_cost_usd,
        )
        if self.breaker is not None:
            self.breaker.record_success()
            self._publish_state()
        return response

    def _publish_state(self) -> None:
        if self.breaker is not None:
            metrics.set_circuit_state(self.breaker.name, self.breaker.state.value)
