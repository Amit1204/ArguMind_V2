"""Fixed-window rate limiter keyed by client (in-process; one backend replica)."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RateDecision:
    allowed: bool
    remaining: int
    retry_after_seconds: float


class RateLimiter:
    def __init__(
        self, limit: int, window_seconds: float = 60.0, clock: Callable[[], float] = time.monotonic
    ) -> None:
        if limit < 1:
            raise ValueError("limit must be positive")
        self.limit = limit
        self.window = window_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._windows: dict[str, tuple[float, int]] = {}  # key -> (window start, count)

    def check(self, key: str) -> RateDecision:
        now = self._clock()
        with self._lock:
            start, count = self._windows.get(key, (now, 0))
            if now - start >= self.window:
                start, count = now, 0
            if count >= self.limit:
                self._windows[key] = (start, count)
                return RateDecision(False, 0, round(self.window - (now - start), 1))
            self._windows[key] = (start, count + 1)
            # Opportunistic cleanup of stale keys keeps memory bounded.
            if len(self._windows) > 10_000:
                self._windows = {k: v for k, v in self._windows.items() if now - v[0] < self.window}
            return RateDecision(True, self.limit - count - 1, 0.0)
