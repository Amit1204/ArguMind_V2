"""System status for the frontend Overview page."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.common import get_app_settings
from app.config import Settings
from app.db import get_session
from app.schemas.system import SystemStatusResponse
from app.services.system_status import build_system_status

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/system", tags=["system"])


def provide_system_status(
    session: Session = Depends(get_session), settings: Settings = Depends(get_app_settings)
) -> SystemStatusResponse:
    try:
        return build_system_status(session, settings)
    except SQLAlchemyError as exc:
        log.error("system status query failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="database unavailable",
            headers={"Retry-After": "5"},
        ) from exc


@router.get("/status", response_model=SystemStatusResponse)
def system_status(
    payload: SystemStatusResponse = Depends(provide_system_status),
) -> SystemStatusResponse:
    return payload


@router.get("/operations")
def system_operations(request: Request) -> dict[str, Any]:
    """In-process operational summary since start (the Overview page's card).

    Prometheus-format metrics for scraping live at /metrics; this is the
    human-readable roll-up: runs by status, p50/p95 latencies, stage outcomes,
    model calls and tokens, source calls, circuit states, rate limiting.
    """
    from app.observability.ops import OPS
    from app.reliability.circuit import REGISTRY as BREAKERS

    settings: Settings = request.app.state.settings
    data = OPS.snapshot()
    executor = getattr(request.app.state, "run_executor", None)
    data["runs"]["active"] = executor.active if executor is not None else data["runs"]["active"]
    data["circuits"] = BREAKERS.states()
    data["limits"] = {
        "runs_per_minute_per_client": settings.rate_limit_runs_per_minute,
        "run_queue_max": settings.run_queue_max,
        "run_workers": settings.run_workers,
        "run_timeout_seconds": settings.run_timeout_seconds,
        "llm_daily_request_limit": settings.llm_daily_request_limit,
        "circuit_failure_threshold": settings.circuit_failure_threshold,
        "circuit_recovery_seconds": settings.circuit_recovery_seconds,
    }
    return data
