"""Run benchmark cases against the API and grade them."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from app.evaluation.dataset import Case
from app.evaluation.graders import FAIL, DimensionResult, grade_case

log = logging.getLogger(__name__)

# ask(case) -> (http status, JSON body or None, retry-after seconds or None)
Ask = Callable[[Case], tuple[int, dict[str, Any] | None, float | None]]


@dataclass(slots=True)
class CaseResult:
    id: str
    category: str
    question: str
    http_status: int
    status: str | None
    passed: bool
    dimensions: list[dict[str, str]]
    latency_ms: int
    llm_calls: int
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float
    sources: int
    claims: int
    conflicts: int
    confidence: float | None
    caveats: int
    run_id: str | None
    answer: str | None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EvaluationRunner:
    def __init__(
        self,
        ask: Ask,
        pause_seconds: float = 0.0,
        transient_statuses: tuple[int, ...] = (429, 502, 503),
        transient_wait: float = 30.0,
        sleep: Callable[[float], None] = time.sleep,
        on_result: Callable[[CaseResult], None] | None = None,
    ) -> None:
        self._ask = ask
        self._pause = pause_seconds
        self._transient = transient_statuses
        self._transient_wait = transient_wait
        self._sleep = sleep
        self._on_result = on_result

    def run_case(self, case: Case) -> CaseResult:
        started = time.perf_counter()
        http_status, body, retry_after = self._ask(case)
        retried = False
        if http_status in self._transient:
            wait = min(retry_after or self._transient_wait, 120.0)
            log.warning(
                "%s: HTTP %s from the API; retrying once in %.0fs", case.id, http_status, wait
            )
            self._sleep(wait)
            started = time.perf_counter()
            http_status, body, retry_after = self._ask(case)
            retried = True
        latency_ms = int((time.perf_counter() - started) * 1000)
        run = body if (http_status == 200 and isinstance(body, dict) and "status" in body) else None
        if run is not None and run.get("latency_ms") is not None:
            latency_ms = int(run["latency_ms"])
        dimensions = grade_case(case, http_status, run, latency_ms)
        usage = (run or {}).get("usage", {})
        result = CaseResult(
            id=case.id,
            category=case.category,
            question=case.question,
            http_status=http_status,
            status=(run or {}).get("status"),
            passed=all(d.status != FAIL for d in dimensions),
            dimensions=[_dim(d) for d in dimensions],
            latency_ms=latency_ms,
            llm_calls=int(usage.get("llm_calls", 0)),
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            estimated_cost_usd=float(usage.get("estimated_cost_usd", 0.0)),
            sources=len((run or {}).get("sources", [])),
            claims=len((run or {}).get("claims", [])),
            conflicts=len((run or {}).get("resolutions", [])),
            confidence=(run or {}).get("confidence"),
            caveats=len((run or {}).get("caveats", [])),
            run_id=(run or {}).get("id"),
            answer=((run or {}).get("answer") or "")[:600] or None,
            extra={
                "retried_transient": retried,
                "sub_questions": (run or {}).get("sub_questions", []),
                "critic": ((run or {}).get("critic") or {}).get("issues", []),
                "error_detail": None
                if run is not None
                else (body or {}).get("detail")
                if isinstance(body, dict)
                else body,
            },
        )
        if self._on_result:
            self._on_result(result)
        return result

    def run(self, cases: list[Case]) -> list[CaseResult]:
        results: list[CaseResult] = []
        for index, case in enumerate(cases):
            result = self.run_case(case)
            results.append(result)
            mark = "PASS" if result.passed else "FAIL"
            failed = [d["name"] for d in result.dimensions if d["status"] == FAIL]
            log.info(
                "%s %-18s %-13s %6d ms%s",
                mark,
                case.id,
                result.status or f"HTTP {result.http_status}",
                result.latency_ms,
                f"  failed: {', '.join(failed)}" if failed else "",
            )
            if self._pause and index < len(cases) - 1:
                self._sleep(self._pause)
        return results


def _dim(d: DimensionResult) -> dict[str, str]:
    return {"name": d.name, "status": d.status, "detail": d.detail}
