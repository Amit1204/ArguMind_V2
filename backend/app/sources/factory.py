"""Assemble the source search service from settings."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

from sqlalchemy.orm import Session

from app.config import Settings
from app.reliability.retry import RetryPolicy
from app.sources.arxiv import ArxivClient
from app.sources.cache import DbSourceCache, MemorySourceCache, SourceCache
from app.sources.http import HttpFetcher
from app.sources.models import SourceKind
from app.sources.service import SourceSearchService
from app.sources.wikipedia import WikipediaClient


def build_source_service(
    settings: Settings, session_factory: Callable[[], Session] | None = None
) -> SourceSearchService:
    policy = RetryPolicy(attempts=settings.source_retry_attempts, base_delay=0.5, max_delay=5.0)
    # arXiv's terms ask for one request every 3 s and answer bursts with 429s
    # that persist for a while, so it gets its own throttled fetcher.
    arxiv_fetcher = HttpFetcher(
        timeout_seconds=settings.arxiv_timeout_seconds,
        policy=policy,
        min_interval_seconds=settings.arxiv_min_interval_seconds,
        rate_limit_backoff_seconds=max(5.0, settings.arxiv_min_interval_seconds),
    )
    wikipedia_fetcher = HttpFetcher(timeout_seconds=settings.source_timeout_seconds, policy=policy)
    cache: SourceCache = DbSourceCache(session_factory) if session_factory else MemorySourceCache()
    return SourceSearchService(
        clients=[ArxivClient(arxiv_fetcher), WikipediaClient(wikipedia_fetcher)],
        cache=cache,
        ttl=timedelta(hours=settings.source_cache_ttl_hours),
        defaults={
            SourceKind.ARXIV: settings.arxiv_max_results,
            SourceKind.WIKIPEDIA: settings.wikipedia_max_results,
        },
    )
