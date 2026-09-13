from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.sources import get_source_service
from app.sources.cache import MemorySourceCache
from app.sources.http import SourceUnavailableError
from app.sources.models import Source, SourceKind
from app.sources.service import SourceSearchService


class FakeClient:
    def __init__(self, kind: SourceKind, results: list[Source] | None = None, error=None) -> None:  # noqa: ANN001
        self.kind, self.results, self.error = kind, results or [], error

    def search(self, question: str, max_results: int) -> list[Source]:
        if self.error:
            raise self.error
        return self.results[:max_results]


def install_fake_service(app: FastAPI) -> None:
    arxiv = FakeClient(
        SourceKind.ARXIV,
        [
            Source(
                source_id="arxiv:2301.12345",
                kind=SourceKind.ARXIV,
                title="Paper",
                url="https://arxiv.org/abs/2301.12345",
                summary="s",
                authority=0.75,
            )
        ],
    )
    wiki = FakeClient(SourceKind.WIKIPEDIA, error=SourceUnavailableError("wikipedia down"))
    service = SourceSearchService([arxiv, wiki], MemorySourceCache())
    app.dependency_overrides[get_source_service] = lambda: service


def test_search_all_kinds_isolates_failures(app: FastAPI, client: TestClient) -> None:
    install_fake_service(app)
    body = client.get("/api/v1/sources/search", params={"q": "language models"}).json()
    assert body["total"] == 1
    by_kind = {g["kind"]: g for g in body["groups"]}
    assert by_kind["arxiv"]["sources"][0]["source_id"] == "arxiv:2301.12345"
    assert by_kind["wikipedia"]["error"] == "wikipedia down"


def test_search_one_kind_and_cache_flag(app: FastAPI, client: TestClient) -> None:
    install_fake_service(app)
    first = client.get(
        "/api/v1/sources/search", params={"q": "language models", "kind": "arxiv"}
    ).json()
    second = client.get(
        "/api/v1/sources/search", params={"q": "language models", "kind": "arxiv"}
    ).json()
    assert first["groups"][0]["cached"] is False and second["groups"][0]["cached"] is True


def test_search_validates_input(client: TestClient) -> None:
    assert client.get("/api/v1/sources/search", params={"q": "ab"}).status_code == 422
    assert (
        client.get("/api/v1/sources/search", params={"q": "abc", "kind": "google"}).status_code
        == 422
    )
    assert client.get("/api/v1/sources/search", params={"q": "abc", "limit": 50}).status_code == 422
