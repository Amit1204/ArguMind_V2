"""Circuit breaker: fail fast for a while after consecutive failures (ADR-009).

closed    normal operation; consecutive failures are counted
open      calls are refused until `recovery_seconds` have passed
half_open one trial call is allowed; success closes, failure re-opens
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from enum import Enum


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    def __init__(self, name: str, retry_after: float) -> None:
        self.name = name
        self.retry_after = max(0.0, retry_after)
        super().__init__(f"circuit '{name}' is open; retry in {self.retry_after:.0f}s")


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        failure_threshold: int = 3,
        recovery_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be positive")
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._failures = 0
        self._opened_at: float | None = None
        self._trial_in_flight = False

    @property
    def state(self) -> CircuitState:
        with self._lock:
            return self._state_locked()

    def _state_locked(self) -> CircuitState:
        if self._opened_at is None:
            return CircuitState.CLOSED
        if self._clock() - self._opened_at >= self.recovery_seconds:
            return CircuitState.HALF_OPEN
        return CircuitState.OPEN

    @property
    def consecutive_failures(self) -> int:
        with self._lock:
            return self._failures

    def retry_after(self) -> float:
        with self._lock:
            if self._opened_at is None:
                return 0.0
            return max(0.0, self.recovery_seconds - (self._clock() - self._opened_at))

    def allow(self) -> None:
        """Raise CircuitOpenError unless a call may proceed."""
        with self._lock:
            state = self._state_locked()
            if state is CircuitState.CLOSED:
                return
            if state is CircuitState.HALF_OPEN and not self._trial_in_flight:
                self._trial_in_flight = True  # exactly one probe at a time
                return
            raise CircuitOpenError(
                self.name, self.recovery_seconds - (self._clock() - (self._opened_at or 0))
            )

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None
            self._trial_in_flight = False

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            self._trial_in_flight = False
            if self._opened_at is not None or self._failures >= self.failure_threshold:
                self._opened_at = self._clock()

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            state = self._state_locked()
            return {
                "state": state.value,
                "consecutive_failures": self._failures,
                "retry_after_seconds": round(
                    max(0.0, self.recovery_seconds - (self._clock() - self._opened_at)), 1
                )
                if self._opened_at is not None and state is CircuitState.OPEN
                else 0.0,
            }


class BreakerRegistry:
    """Named breakers shared across the process; the operations endpoint lists them."""

    def __init__(self) -> None:
        self._breakers: dict[str, CircuitBreaker] = {}
        self._lock = threading.Lock()

    def get(
        self, name: str, failure_threshold: int = 3, recovery_seconds: float = 60.0
    ) -> CircuitBreaker:
        with self._lock:
            breaker = self._breakers.get(name)
            if breaker is None:
                breaker = CircuitBreaker(name, failure_threshold, recovery_seconds)
                self._breakers[name] = breaker
            return breaker

    def states(self) -> dict[str, dict[str, object]]:
        with self._lock:
            return {name: b.snapshot() for name, b in sorted(self._breakers.items())}

    def reset(self) -> None:
        with self._lock:
            self._breakers.clear()


REGISTRY = BreakerRegistry()
