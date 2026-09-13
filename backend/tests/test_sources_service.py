from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.sources.cache import MemorySourceCache
from app.sources.http import SourceUnavailableError
from app.sources.models import Source, SourceKind
from app.sources.service import SourceSearchService


def make_source(n: int, kind: SourceKind = SourceKind.ARXIV) -> Source:
    return Source(
        source_id=f"arxiv:2301.0000{n}" if kind is SourceKind.ARXIV else f"wikipedia:{n:016x}",
        kind=kind,
        title=f"Paper {n}",
        url=f"https://example.org/{n}",
        summary="Some summary text.",
        authority=0.75,
    )


class FakeClient:
    def __init__(self, kind: SourceKind, results=None, error: Exception | None = None) -> None:  # noqa: ANN001
        self.kind = kind
        self.results = results or []
        self.error = error
        self.calls = 0

    def search(self, question: str, max_results: int) -> list[Source]:
        self.calls += 1
        if self.error:
            raise self.error
        return self.results[:max_results]


def test_search_hits_cache_on_repeat() -> None:
    client = FakeClient(SourceKind.ARXIV, [make_source(1), make_source(2)])
    service = SourceSearchService([client], MemorySourceCache())
    first = service.search(SourceKind.ARXIV, "q", 5)
    second = service.search(SourceKind.ARXIV, "Q  ", 5)
    assert not first.cached and second.cached
    assert [s.source_id for s in second.sources] == ["arxiv:2301.00001", "arxiv:2301.00002"]
    assert client.calls == 1


def test_cache_entries_expire() -> None:
    clock = {"now": datetime(2026, 9, 13, tzinfo=UTC)}
    cache = MemorySourceCache(now=lambda: clock["now"])
    client = FakeClient(SourceKind.ARXIV, [make_source(1)])
    service = SourceSearchService([client], cache, ttl=timedelta(hours=1))
    service.search(SourceKind.ARXIV, "q", 5)
    clock["now"] += timedelta(hours=2)
    assert not service.search(SourceKind.ARXIV, "q", 5).cached
    assert client.calls == 2


def test_failed_source_is_isolated_and_not_cached() -> None:
    broken = FakeClient(SourceKind.ARXIV, error=SourceUnavailableError("arxiv down"))
    wiki = FakeClient(SourceKind.WIKIPEDIA, [make_source(7, SourceKind.WIKIPEDIA)])
    service = SourceSearchService([broken, wiki], MemorySourceCache())
    outcomes = service.search_all("q")
    assert outcomes[0].error == "arxiv down" and outcomes[0].sources == []
    assert len(outcomes[1].sources) == 1
    service.search(SourceKind.ARXIV, "q")
    assert broken.calls == 2  # failures are retried next time, not cached


def test_unknown_kind_is_reported() -> None:
    service = SourceSearchService([FakeClient(SourceKind.ARXIV)], MemorySourceCache())
    outcome = service.search(SourceKind.WIKIPEDIA, "q")
    assert outcome.error and "no client" in outcome.error
