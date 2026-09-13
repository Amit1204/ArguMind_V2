"""Exponential backoff with jitter, shared by the source clients and the model layer.

Phase 2 ships the policy and delay computation; the circuit breakers and the
whole-run deadline that build on it arrive in Phase 6.
"""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    attempts: int = 3  # total tries, including the first
    base_delay: float = 0.5
    multiplier: float = 2.0
    max_delay: float = 8.0
    jitter: float = 0.2  # +/- fraction of the computed delay


def backoff_delay(attempt: int, policy: RetryPolicy, rng: random.Random | None = None) -> float:
    """Delay before retry number `attempt` (1 = first retry)."""
    if attempt < 1:
        raise ValueError("attempt starts at 1")
    delay = min(policy.base_delay * (policy.multiplier ** (attempt - 1)), policy.max_delay)
    if policy.jitter:
        spread = delay * policy.jitter
        delay += (rng or random).uniform(-spread, spread)  # noqa: S311 - not security related
    return max(0.0, delay)


def parse_retry_after(value: str | None, cap: float = 10.0) -> float | None:
    """Seconds from a Retry-After header when it is a plain number; capped."""
    if not value:
        return None
    try:
        seconds = float(value.strip())
    except ValueError:
        return None
    return min(max(seconds, 0.0), cap)
