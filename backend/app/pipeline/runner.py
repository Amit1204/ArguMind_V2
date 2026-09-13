"""Execute one run end to end and persist the outcome."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from app.config import Settings
from app.llm.base import LLMProvider
from app.pipeline.context import PipelineContext
from app.pipeline.graph import build_pipeline, initial_state
from app.pipeline.schemas import RunUsage
from app.pipeline.store import RunStore
from app.sources.service import SourceSearchService

log = logging.getLogger(__name__)


class PipelineRunner:
    def __init__(
        self,
        settings: Settings,
        provider: LLMProvider,
        sources: SourceSearchService,
        store: RunStore,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self.provider = provider
        self.sources = sources
        self.store = store
        self.clock = clock

    def run(self, run_id: str, question: str, request_id: str | None = None) -> str:
        """Run the pipeline for an existing (queued) run; returns the final status."""
        self.store.mark_running(run_id)
        ctx = PipelineContext(
            settings=self.settings,
            provider=self.provider,
            sources=self.sources,
            store=self.store,
            run_id=run_id,
            request_id=request_id,
            clock=self.clock,
        )
        started = self.clock()
        try:
            final = build_pipeline(ctx).invoke(
                initial_state(question), config={"recursion_limit": 50}
            )
        except Exception as exc:
            latency_ms = int((self.clock() - started) * 1000)
            log.exception("run %s failed", run_id)
            self.store.finish_run(
                run_id,
                "failed",
                answer=None,
                confidence=None,
                outcome_reason="pipeline error",
                iteration_count=0,
                usage=RunUsage(**ctx.ledger.summary()),
                latency_ms=latency_ms,
                error=f"{type(exc).__name__}: {exc}"[:2000],
            )
            return "failed"

        latency_ms = int((self.clock() - started) * 1000)
        status = final.get("status") or "failed"
        answer = final.get("answer") or {}
        self.store.finish_run(
            run_id,
            status,
            answer=answer.get("answer"),
            confidence=answer.get("confidence"),
            outcome_reason=final.get("outcome_reason"),
            iteration_count=int(final.get("iteration", 0)),
            usage=RunUsage(**ctx.ledger.summary()),
            latency_ms=latency_ms,
        )
        log.info(
            "run %s %s in %dms (%d llm calls)",
            run_id,
            status,
            latency_ms,
            ctx.ledger.calls,
            extra={"run_id": run_id, "status": status},
        )
        return status

    def execute(
        self, question: str, request_id: str | None = None, client_id: str | None = None
    ) -> str:
        """Create a run and execute it synchronously; returns the run id."""
        run_id = self.store.create_run(question, request_id, client_id)
        self.run(run_id, question, request_id)
        return run_id
