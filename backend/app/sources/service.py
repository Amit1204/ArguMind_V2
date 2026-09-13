"""Search all configured sources for one question, with caching and per-source isolation."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Protocol

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
    ) -> None:
        self._clients = {c.kind: c for c in clients}
        self._cache = cache
        self._ttl = ttl
        self._defaults = defaults or {SourceKind.ARXIV: 5, SourceKind.WIKIPEDIA: 3}

    @property
    def kinds(self) -> list[SourceKind]:
        return list(self._clients)

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
            return SearchOutcome(kind=kind, sources=cached, cached=True)
        try:
            sources = client.search(question, limit)
        except SourceError as exc:
            log.warning("%s search failed: %s", kind.value, exc)
            return SearchOutcome(kind=kind, error=str(exc))
        self._cache.set(key, kind.value, question, sources, self._ttl)
        return SearchOutcome(kind=kind, sources=sources)

    def search_all(
        self, question: str, limits: dict[SourceKind, int] | None = None
    ) -> list[SearchOutcome]:
        limits = limits or {}
        return [self.search(kind, question, limits.get(kind)) for kind in self._clients]
