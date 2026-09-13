"""Local daily request budget: refuse before the provider does.

The free tier has a hard daily quota; a loop that goes wrong should fail here,
visibly, instead of burning the whole day's allowance. In-process for now
(one backend replica); a shared store is the Phase 6 option.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, date, datetime

from app.llm.base import LLMBudgetExceededError


class DailyRequestBudget:
    def __init__(self, limit: int, today: Callable[[], date] | None = None) -> None:
        if limit < 1:
            raise ValueError("limit must be positive")
        self.limit = limit
        self._today = today or (lambda: datetime.now(UTC).date())
        self._day = self._today()
        self._used = 0
        self._lock = threading.Lock()

    def _roll(self) -> None:
        day = self._today()
        if day != self._day:
            self._day, self._used = day, 0

    @property
    def used(self) -> int:
        with self._lock:
            self._roll()
            return self._used

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    def consume(self) -> None:
        with self._lock:
            self._roll()
            if self._used >= self.limit:
                raise LLMBudgetExceededError(
                    f"daily LLM request budget of {self.limit} reached; resets at 00:00 UTC"
                )
            self._used += 1
