"""Source search endpoint: try the retrieval layer directly (also used by the UI later)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from app.sources.models import Source, SourceKind
from app.sources.service import SourceSearchService

router = APIRouter(prefix="/api/v1/sources", tags=["sources"])


def get_source_service(request: Request) -> SourceSearchService:
    """Built lazily on first use so the app can start without touching the network."""
    service = getattr(request.app.state, "source_service", None)
    if service is None:
        from app.db import get_session_factory
        from app.sources.factory import build_source_service

        settings = request.app.state.settings
        service = build_source_service(settings, get_session_factory(settings))
        request.app.state.source_service = service
    return service


class SourceGroup(BaseModel):
    kind: SourceKind
    cached: bool
    error: str | None = None
    sources: list[Source] = Field(default_factory=list)


class SourceSearchResponse(BaseModel):
    query: str
    groups: list[SourceGroup]
    total: int


@router.get("/search", response_model=SourceSearchResponse)
def search_sources(
    q: str = Query(min_length=3, max_length=500, description="Research question or topic"),
    kind: Literal["all", "arxiv", "wikipedia"] = "all",
    limit: int = Query(default=5, ge=1, le=10),
    service: SourceSearchService = Depends(get_source_service),
) -> SourceSearchResponse:
    kinds = service.kinds if kind == "all" else [SourceKind(kind)]
    outcomes = [service.search(k, q, limit) for k in kinds]
    groups = [
        SourceGroup(kind=o.kind, cached=o.cached, error=o.error, sources=o.sources)
        for o in outcomes
    ]
    return SourceSearchResponse(query=q, groups=groups, total=sum(len(g.sources) for g in groups))
