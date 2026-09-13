"""In-process operations summary since start: counts and p50/p95 latencies.

Prometheus is the scrape surface; this is the human-readable roll-up shown on
the Overview page without any extra infrastructure.
"""

from __future__ import annotations

import threading
from collections import defaultdict, deque

MAX_SAMPLES = 500


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((pct / 100) * (len(ordered) - 1))))
    return ordered[index]


class OpsSummary:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self.runs_by_status: dict[str, int] = defaultdict(int)
            self.run_latency: deque[float] = deque(maxlen=MAX_SAMPLES)
            self.stage_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
            self.stage_latency: dict[str, deque[float]] = defaultdict(
                lambda: deque(maxlen=MAX_SAMPLES)
            )
            self.llm_by_outcome: dict[str, int] = defaultdict(int)
            self.llm_by_purpose: dict[str, int] = defaultdict(int)
            self.llm_latency: deque[float] = deque(maxlen=MAX_SAMPLES)
            self.llm_input_tokens = 0
            self.llm_output_tokens = 0
            self.llm_fallbacks = 0
            self.llm_cost_usd = 0.0
            self.source_calls: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
            self.source_latency: dict[str, deque[float]] = defaultdict(
                lambda: deque(maxlen=MAX_SAMPLES)
            )
            self.rate_limited = 0
            self.active_runs = 0

    # ---------------------------------------------------------------- record
    def run_started(self) -> None:
        with self._lock:
            self.active_runs += 1

    def run_finished(self, status: str, latency_ms: int) -> None:
        with self._lock:
            self.active_runs = max(0, self.active_runs - 1)
            self.runs_by_status[status] += 1
            self.run_latency.append(latency_ms)

    def stage(self, name: str, status: str, duration_ms: int) -> None:
        with self._lock:
            self.stage_counts[name][status] += 1
            self.stage_latency[name].append(duration_ms)

    def llm(
        self,
        purpose: str,
        outcome: str,
        latency_ms: int,
        input_tokens: int = 0,
        output_tokens: int = 0,
        fallback: bool = False,
        cost_usd: float = 0.0,
    ) -> None:
        with self._lock:
            self.llm_by_outcome[outcome] += 1
            self.llm_by_purpose[purpose] += 1
            if outcome == "ok":
                self.llm_latency.append(latency_ms)
                self.llm_input_tokens += input_tokens
                self.llm_output_tokens += output_tokens
                self.llm_cost_usd += cost_usd
            if fallback:
                self.llm_fallbacks += 1

    def source(self, kind: str, outcome: str, latency_ms: int | None = None) -> None:
        with self._lock:
            self.source_calls[kind][outcome] += 1
            if latency_ms is not None and outcome in ("ok", "error"):
                self.source_latency[kind].append(latency_ms)

    def rate_limit_hit(self) -> None:
        with self._lock:
            self.rate_limited += 1

    # -------------------------------------------------------------- snapshot
    def snapshot(self) -> dict:
        with self._lock:
            runs = list(self.run_latency)
            llm = list(self.llm_latency)
            return {
                "runs": {
                    "total": sum(self.runs_by_status.values()),
                    "by_status": dict(self.runs_by_status),
                    "active": self.active_runs,
                    "latency_ms_p50": percentile(runs, 50),
                    "latency_ms_p95": percentile(runs, 95),
                },
                "stages": {
                    name: {
                        "count": sum(counts.values()),
                        "failed": counts.get("failed", 0),
                        "skipped": counts.get("skipped", 0) + counts.get("timeout", 0),
                        "latency_ms_p50": percentile(list(self.stage_latency[name]), 50),
                    }
                    for name, counts in sorted(self.stage_counts.items())
                },
                "llm": {
                    "calls": sum(self.llm_by_outcome.values()),
                    "by_outcome": dict(self.llm_by_outcome),
                    "by_purpose": dict(self.llm_by_purpose),
                    "latency_ms_p50": percentile(llm, 50),
                    "latency_ms_p95": percentile(llm, 95),
                    "input_tokens": self.llm_input_tokens,
                    "output_tokens": self.llm_output_tokens,
                    "fallbacks": self.llm_fallbacks,
                    "estimated_cost_usd": round(self.llm_cost_usd, 6),
                },
                "sources": {
                    kind: {
                        **dict(outcomes),
                        "latency_ms_p50": percentile(list(self.source_latency[kind]), 50),
                    }
                    for kind, outcomes in sorted(self.source_calls.items())
                },
                "rate_limited": self.rate_limited,
            }


OPS = OpsSummary()
