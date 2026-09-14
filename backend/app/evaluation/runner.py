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
# model_circuit() -> seconds until the backend's model circuit breaker may close
# (0.0 when it is closed / half-open and calls are allowed through)
ModelCircuit = Callable[[], float]

DEGRADED_STATUSES = frozenset({"inconclusive", "failed"})


class ProviderExhausted(RuntimeError):
    """The model provider rejected every call of a case even after the circuit
    rerun (typically a spent daily quota). Grading further cases would measure
    the quota, not the pipeline; the run stops and the checkpoint stays resumable."""

    def __init__(self, case_id: str) -> None:
        super().__init__(f"{case_id}: no successful model call even after the circuit rerun")
        self.case_id = case_id


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
        model_circuit: ModelCircuit | None = None,
        circuit_wait_cap: float = 600.0,
    ) -> None:
        self._ask = ask
        self._pause = pause_seconds
        self._transient = transient_statuses
        self._transient_wait = transient_wait
        self._sleep = sleep
        self._on_result = on_result
        self._model_circuit = model_circuit
        self._circuit_wait_cap = circuit_wait_cap
        self.aborted_at: str | None = None  # case id that stopped the run, if any

    def _circuit_retry_after(self) -> float:
        """Seconds until the model circuit allows calls; 0.0 if closed or unknown."""
        if self._model_circuit is None:
            return 0.0
        try:
            return max(0.0, float(self._model_circuit()))
        except Exception as exc:  # noqa: BLE001 - a probe failure must not stop the run
            log.warning("model circuit probe failed (%s); assuming closed", exc)
            return 0.0

    def _wait_for_model_circuit(self, case_id: str) -> float:
        """Block while the backend's model circuit is open, so a case is not run
        against a provider that is guaranteed to reject every call. Returns the
        seconds waited (capped)."""
        waited = 0.0
        while waited < self._circuit_wait_cap:
            retry_after = self._circuit_retry_after()
            if retry_after <= 0:
                break
            wait = min(retry_after + 2.0, self._circuit_wait_cap - waited)
            log.warning("%s: model circuit open; waiting %.0fs before the case", case_id, wait)
            self._sleep(wait)
            waited += wait
        return waited

    def _ask_once(self, case: Case) -> tuple[int, dict[str, Any] | None, float | None, int, bool]:
        """One attempt with the transient-status retry. Returns
        (http status, body, retry-after, latency ms, retried_transient)."""
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
        return http_status, body, retry_after, latency_ms, retried

    @staticmethod
    def _looks_degraded(run: dict[str, Any] | None) -> bool:
        """A run that ended inconclusive/failed without a single successful model
        call was starved by the model circuit breaker, not judged on evidence."""
        if run is None or run.get("status") not in DEGRADED_STATUSES:
            return False
        return int((run.get("usage") or {}).get("llm_calls", 0)) == 0

    def run_case(self, case: Case) -> CaseResult:
        circuit_wait = self._wait_for_model_circuit(case.id)
        http_status, body, retry_after, latency_ms, retried = self._ask_once(case)
        run = body if (http_status == 200 and isinstance(body, dict) and "status" in body) else None
        retried_circuit = False
        # The case ran while the provider was unavailable: the outcome says nothing
        # about the pipeline. Wait for the circuit to close and run it once more.
        if run is not None and (self._looks_degraded(run) or self._circuit_retry_after() > 0):
            log.warning(
                "%s: run %s with %s model calls while the model circuit was open; rerunning once",
                case.id,
                run.get("status"),
                (run.get("usage") or {}).get("llm_calls", 0),
            )
            circuit_wait += self._wait_for_model_circuit(case.id)
            http_status, body, retry_after, latency_ms, retried2 = self._ask_once(case)
            retried = retried or retried2
            retried_circuit = True
            run = (
                body if (http_status == 200 and isinstance(body, dict) and "status" in body) else None
            )
            if self._looks_degraded(run):
                raise ProviderExhausted(case.id)
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
                "retried_circuit": retried_circuit,
                "circuit_wait_seconds": round(circuit_wait, 1),
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
            try:
                result = self.run_case(case)
            except ProviderExhausted as exc:
                self.aborted_at = exc.case_id
                log.error(
                    "%s; stopping after %d graded case(s). The model provider is exhausted "
                    "(daily quota?) - resume later with --resume.",
                    exc,
                    len(results),
                )
                break
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
