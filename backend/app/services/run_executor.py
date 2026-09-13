"""Background execution of runs with a bounded worker pool.

POST /runs returns as soon as the run row exists; the pipeline executes on a
worker thread and persists each stage, so GET /runs/{id} shows progress.
Callers that prefer a synchronous answer can wait with a bounded timeout.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError

from app.pipeline.runner import PipelineRunner
from app.pipeline.store import RunStore

log = logging.getLogger(__name__)


class RunExecutor:
    def __init__(
        self, runner: PipelineRunner, store: RunStore, workers: int = 2, inline: bool = False
    ) -> None:
        self.runner = runner
        self.store = store
        self._pool = (
            None if inline else ThreadPoolExecutor(max_workers=workers, thread_name_prefix="run")
        )
        self._futures: dict[str, Future] = {}
        self._lock = threading.Lock()

    def submit(self, question: str, request_id: str | None, client_id: str | None) -> str:
        run_id = self.store.create_run(question, request_id, client_id)
        if self._pool is None:
            self.runner.run(run_id, question, request_id)
            return run_id
        future = self._pool.submit(self._execute, run_id, question, request_id)
        with self._lock:
            self._futures[run_id] = future
        return run_id

    def _execute(self, run_id: str, question: str, request_id: str | None) -> None:
        try:
            self.runner.run(run_id, question, request_id)
        finally:
            with self._lock:
                self._futures.pop(run_id, None)

    def wait(self, run_id: str, timeout: float) -> bool:
        """True when the run finished within the timeout (or was not running)."""
        with self._lock:
            future = self._futures.get(run_id)
        if future is None:
            return True
        try:
            future.result(timeout=timeout)
            return True
        except TimeoutError:
            return False
        except Exception:  # the runner records failures itself
            return True

    @property
    def active(self) -> int:
        with self._lock:
            return len(self._futures)

    def shutdown(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
