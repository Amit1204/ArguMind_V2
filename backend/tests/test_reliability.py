from __future__ import annotations

import pytest

from app.reliability.circuit import BreakerRegistry, CircuitBreaker, CircuitOpenError, CircuitState
from app.reliability.ratelimit import RateLimiter


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


# ---------------------------------------------------------------- circuit breaker


def test_breaker_opens_after_threshold_and_recovers_via_half_open_probe() -> None:
    clock = Clock()
    breaker = CircuitBreaker("test", failure_threshold=3, recovery_seconds=60, clock=clock)
    assert breaker.state is CircuitState.CLOSED
    breaker.record_failure()
    breaker.record_failure()
    breaker.allow()  # still closed after two failures
    breaker.record_failure()
    assert breaker.state is CircuitState.OPEN
    with pytest.raises(CircuitOpenError) as excinfo:
        breaker.allow()
    assert 59 <= excinfo.value.retry_after <= 60
    clock.t += 61
    assert breaker.state is CircuitState.HALF_OPEN
    breaker.allow()  # one probe allowed
    with pytest.raises(CircuitOpenError):
        breaker.allow()  # a second concurrent probe is refused
    breaker.record_success()
    assert breaker.state is CircuitState.CLOSED and breaker.consecutive_failures == 0


def test_failed_probe_reopens_the_breaker() -> None:
    clock = Clock()
    breaker = CircuitBreaker("test", failure_threshold=1, recovery_seconds=10, clock=clock)
    breaker.record_failure()
    clock.t += 11
    breaker.allow()
    breaker.record_failure()
    assert breaker.state is CircuitState.OPEN
    assert breaker.snapshot()["state"] == "open"
    assert breaker.snapshot()["retry_after_seconds"] == 10.0


def test_success_resets_the_failure_count() -> None:
    breaker = CircuitBreaker("test", failure_threshold=2)
    breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()
    assert breaker.state is CircuitState.CLOSED


def test_registry_shares_breakers_by_name() -> None:
    registry = BreakerRegistry()
    a = registry.get("source:arxiv", failure_threshold=2)
    b = registry.get("source:arxiv")
    assert a is b
    a.record_failure()
    a.record_failure()
    assert registry.states()["source:arxiv"]["state"] == "open"
    assert CircuitBreaker.__init__  # noqa: B018 - keep the import meaningful
    with pytest.raises(ValueError):
        CircuitBreaker("bad", failure_threshold=0)


# ---------------------------------------------------------------- rate limiter


def test_rate_limiter_fixed_window() -> None:
    clock = Clock()
    limiter = RateLimiter(2, window_seconds=60, clock=clock)
    assert limiter.check("a").allowed and limiter.check("a").allowed
    refused = limiter.check("a")
    assert not refused.allowed and refused.retry_after_seconds == 60.0
    assert limiter.check("b").allowed  # other clients are independent
    clock.t += 61
    assert limiter.check("a").allowed
    with pytest.raises(ValueError):
        RateLimiter(0)
