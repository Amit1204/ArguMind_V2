"""Runs API: start a pipeline run, follow its progress, inspect its evidence and graph."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from app.observability import metrics
from app.observability.ops import OPS
from app.pipeline.detail import build_graph_response, build_run_detail, run_summary
from app.reliability.ratelimit import RateLimiter
from app.schemas.runs import GraphResponse, RunCreate, RunDetail, RunList
from app.services.run_executor import RunExecutor

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/runs", tags=["runs"])


def get_run_executor(request: Request) -> RunExecutor:
    """Built lazily on first use: provider, source service, store and worker pool."""
    executor = getattr(request.app.state, "run_executor", None)
    if executor is None:
        from app.api.sources import get_source_service
        from app.db import get_session_factory
        from app.llm.base import LLMConfigurationError
        from app.llm.factory import build_provider
        from app.pipeline.runner import PipelineRunner
        from app.pipeline.store import DbRunStore

        settings = request.app.state.settings
        store = DbRunStore(get_session_factory(settings))
        try:
            provider = build_provider(settings)
        except LLMConfigurationError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"model provider not configured: {exc}",
            ) from exc
        runner = PipelineRunner(settings, provider, get_source_service(request), store)
        executor = RunExecutor(runner, store, workers=settings.run_workers)
        request.app.state.run_executor = executor
    return executor


def get_rate_limiter(request: Request) -> RateLimiter:
    limiter = getattr(request.app.state, "run_rate_limiter", None)
    if limiter is None:
        limiter = RateLimiter(request.app.state.settings.rate_limit_runs_per_minute)
        request.app.state.run_rate_limiter = limiter
    return limiter


def _client_key(request: Request, body: RunCreate) -> str:
    if body.client_id:
        return f"client:{body.client_id}"
    host = request.client.host if request.client else "unknown"
    return f"ip:{host}"


@router.post("", response_model=RunDetail, status_code=status.HTTP_202_ACCEPTED)
def create_run(
    body: RunCreate,
    request: Request,
    response: Response,
    wait: bool = Query(default=False, description="Block until the run finishes (bounded)"),
    executor: RunExecutor = Depends(get_run_executor),
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> RunDetail:
    settings = request.app.state.settings
    decision = limiter.check(_client_key(request, body))
    if not decision.allowed:
        metrics.RATE_LIMITED.inc()
        OPS.rate_limit_hit()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"rate limit: at most {limiter.limit} runs per minute per client",
            headers={"Retry-After": str(max(1, int(decision.retry_after_seconds)))},
        )
    if executor.active >= settings.run_queue_max:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="too many runs in progress; try again shortly",
            headers={"Retry-After": "30"},
        )
    request_id = getattr(request.state, "request_id", None)
    run_id = executor.submit(body.question, request_id, body.client_id)
    if wait:
        executor.wait(run_id, timeout=settings.run_timeout_seconds + 30)
    detail = build_run_detail(executor.store, run_id)
    if detail is None:  # pragma: no cover - the row was just created
        raise HTTPException(status_code=500, detail="run was not persisted")
    if detail.status not in ("queued", "running"):
        response.status_code = status.HTTP_200_OK
    return detail


@router.get("", response_model=RunList)
def list_runs(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    executor: RunExecutor = Depends(get_run_executor),
) -> RunList:
    rows = executor.store.list_runs(limit, offset)
    return RunList(
        runs=[run_summary(r) for r in rows],
        total=executor.store.count_runs(),
        limit=limit,
        offset=offset,
    )


@router.get("/{run_id}", response_model=RunDetail)
def get_run(run_id: str, executor: RunExecutor = Depends(get_run_executor)) -> RunDetail:
    detail = build_run_detail(executor.store, _validate_id(run_id))
    if detail is None:
        raise HTTPException(status_code=404, detail="run not found")
    return detail


@router.get("/{run_id}/graph", response_model=GraphResponse)
def get_run_graph(run_id: str, executor: RunExecutor = Depends(get_run_executor)) -> GraphResponse:
    graph = build_graph_response(executor.store, _validate_id(run_id))
    if graph is None:
        raise HTTPException(status_code=404, detail="run not found")
    return graph


def _validate_id(run_id: str) -> str:
    try:
        return str(uuid.UUID(run_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc
