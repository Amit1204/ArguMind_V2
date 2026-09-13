"""Search all configured sources for one question, with caching, per-source
isolation, metrics and a circuit breaker per source kind."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Protocol

from app.observability import metrics
from app.observability.ops import OPS
from app.reliability.circuit import CircuitBreaker, CircuitOpenError
from app.sources.cache import SourceCache
from app.sources.http import SourceError
from app.sources.ids import cache_key
from app.sources.models import Source, SourceKind

log = logging.getLogger(__name__)


class SourceClient(Protocol):
    kind: SourceKind

    def search(self, question: str, max_results: int) -> list[Source]: ...


@dataclass(slots=True)
class SearchOutcome:
    kind: SourceKind
    sources: list[Source] = field(default_factory=list)
    cached: bool = False
    error: str | None = None  # a failed source is reported, never fatal for the run


class SourceSearchService:
    def __init__(
        self,
        clients: list[SourceClient],
        cache: SourceCache,
        ttl: timedelta = timedelta(hours=24),
        defaults: dict[SourceKind, int] | None = None,
        breakers: dict[SourceKind, CircuitBreaker] | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._clients = {c.kind: c for c in clients}
        self._cache = cache
        self._ttl = ttl
        self._defaults = defaults or {SourceKind.ARXIV: 5, SourceKind.WIKIPEDIA: 3}
        self._breakers = breakers or {}
        self._clock = clock

    @property
    def kinds(self) -> list[SourceKind]:
        return list(self._clients)

    def breaker_states(self) -> dict[str, dict[str, object]]:
        return {k.value: b.snapshot() for k, b in self._breakers.items()}

    def search(
        self, kind: SourceKind, question: str, max_results: int | None = None
    ) -> SearchOutcome:
        client = self._clients.get(kind)
        if client is None:
            return SearchOutcome(kind=kind, error=f"no client configured for {kind.value}")
        limit = max_results or self._defaults.get(kind, 5)
        key = cache_key(kind.value, question, limit)
        cached = self._cache.get(key)
        if cached is not None:
            self._observe(kind, "cached")
            return SearchOutcome(kind=kind, sources=cached, cached=True)

        breaker = self._breakers.get(kind)
        if breaker is not None:
            try:
                breaker.allow()
            except CircuitOpenError as exc:
                self._observe(kind, "circuit_open")
                self._publish(kind, breaker)
                return SearchOutcome(kind=kind, error=str(exc))

        started = self._clock()
        try:
            sources = client.search(question, limit)
        except SourceError as exc:
            elapsed = self._clock() - started
            log.warning("%s search failed after %.1fs: %s", kind.value, elapsed, exc)
            self._observe(kind, "error", elapsed)
            if breaker is not None:
                breaker.record_failure()
                self._publish(kind, breaker)
            return SearchOutcome(kind=kind, error=str(exc))
        elapsed = self._clock() - started
        self._observe(kind, "ok", elapsed)
        if breaker is not None:
            breaker.record_success()
            self._publish(kind, breaker)
        self._cache.set(key, kind.value, question, sources, self._ttl)
        return SearchOutcome(kind=kind, sources=sources)

    def search_all(
        self, question: str, limits: dict[SourceKind, int] | None = None
    ) -> list[SearchOutcome]:
        limits = limits or {}
        return [self.search(kind, question, limits.get(kind)) for kind in self._clients]

    @staticmethod
    def _observe(kind: SourceKind, outcome: str, seconds: float | None = None) -> None:
        metrics.observe_source(kind.value, outcome, seconds)
        OPS.source(kind.value, outcome, int(seconds * 1000) if seconds is not None else None)

    @staticmethod
    def _publish(kind: SourceKind, breaker: CircuitBreaker) -> None:
        metrics.set_circuit_state(breaker.name, breaker.state.value)
