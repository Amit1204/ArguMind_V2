"""Prometheus scrape endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Response

from app.observability import metrics

router = APIRouter(tags=["observability"])


@router.get("/metrics", include_in_schema=False)
def prometheus_metrics() -> Response:
    body, content_type = metrics.render()
    return Response(content=body, media_type=content_type)
