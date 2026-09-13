"""Run-scoped services and stage recording shared by all nodes."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.config import Settings
from app.evidence.extractor import ClaimExtractor
from app.llm.base import LLMProvider, UsageLedger
from app.pipeline.schemas import StageRecord, StageStatus
from app.pipeline.store import RunStore
from app.reasoning.conflicts import ConflictResolver
from app.sources.service import SourceSearchService

log = logging.getLogger(__name__)


class StageRun:
    """Mutable handle a node uses to describe what happened in its stage."""

    def __init__(self) -> None:
        self.status: StageStatus = "ok"
        self.detail: dict = {}
        self.error: str | None = None

    def fail(self, message: str) -> None:
        self.status = "failed"
        self.error = message[:1000]

    def skip(self, reason: str, timeout: bool = False) -> None:
        self.status = "timeout" if timeout else "skipped"
        self.detail["reason"] = reason


@dataclass
class PipelineContext:
    settings: Settings
    provider: LLMProvider
    sources: SourceSearchService
    store: RunStore
    run_id: str
    request_id: str | None = None
    ledger: UsageLedger = field(default_factory=UsageLedger)
    clock: Callable[[], float] = time.monotonic
    started: float = field(default=0.0)
    stages: list[StageRecord] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.started:
            self.started = self.clock()

    # ------------------------------------------------------------ time budget
    def elapsed_seconds(self) -> float:
        return self.clock() - self.started

    def expired(self) -> bool:
        return self.elapsed_seconds() >= self.settings.run_timeout_seconds

    def gather_expired(self) -> bool:
        """Gathering gets only a share of the budget so reasoning always has time left."""
        limit = self.settings.run_timeout_seconds * self.settings.gather_budget_fraction
        return self.elapsed_seconds() >= limit

    # --------------------------------------------------------------- services
    def extractor(self) -> ClaimExtractor:
        return ClaimExtractor(
            self.provider, self.ledger, max_claims=self.settings.max_claims_per_source
        )

    def resolver(self) -> ConflictResolver:
        return ConflictResolver(self.provider, self.ledger)

    # ------------------------------------------------------------- recording
    @contextmanager
    def stage(self, name: str, attempt: int = 1) -> Iterator[StageRun]:
        run = StageRun()
        started_at = datetime.now(UTC)
        t0 = self.clock()
        calls0, in0, out0 = self.ledger.calls, self.ledger.input_tokens, self.ledger.output_tokens
        try:
            yield run
        except Exception as exc:
            run.fail(f"{type(exc).__name__}: {exc}")
            raise
        finally:
            finished_at = datetime.now(UTC)
            record = StageRecord(
                name=name,
                attempt=attempt,
                status=run.status,
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=int((self.clock() - t0) * 1000),
                llm_calls=self.ledger.calls - calls0,
                input_tokens=self.ledger.input_tokens - in0,
                output_tokens=self.ledger.output_tokens - out0,
                detail=run.detail,
                error=run.error,
            )
            self.stages.append(record)
            try:
                self.store.save_stage(self.run_id, record)
            except Exception:  # persistence must never take the pipeline down
                log.exception("could not persist stage %s for run %s", name, self.run_id)
            log.info(
                "stage %s %s in %dms (llm calls %d)",
                name,
                record.status,
                record.duration_ms,
                record.llm_calls,
                extra={"run_id": self.run_id, "stage": name, "attempt": attempt},
            )
