"""Liveness and readiness probes.

GET /health  - process is alive; never touches the database.
GET /ready   - database reachable and schema migrated; 503 otherwise.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.common import get_app_settings
from app.config import Settings
from app.db import get_session
from app.schemas.system import HealthResponse, ReadinessCheck, ReadinessResponse
from app.services.system_status import readiness_checks

log = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


def run_readiness_checks(
    session: Session = Depends(get_session), settings: Settings = Depends(get_app_settings)
) -> list[ReadinessCheck]:
    """Dependency so tests can substitute check results without a database."""
    try:
        return readiness_checks(session, settings)
    except SQLAlchemyError as exc:
        # Log the error type server-side; never echo connection details to clients.
        log.warning("readiness check failed: %s", type(exc).__name__)
        return [ReadinessCheck(name="database", ok=False, detail=type(exc).__name__)]


@router.get("/health", response_model=HealthResponse)
def health(settings: Settings = Depends(get_app_settings)) -> HealthResponse:
    return HealthResponse(
        service=settings.app_name,
        version=settings.app_version,
        environment=settings.app_env,
        timestamp=datetime.now(UTC),
    )


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessResponse}},
)
def ready(
    response: Response, checks: list[ReadinessCheck] = Depends(run_readiness_checks)
) -> ReadinessResponse:
    all_ok = all(c.ok for c in checks if not c.optional)
    if not all_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(
        status="ready" if all_ok else "not_ready",
        checks=checks,
        timestamp=datetime.now(UTC),
    )
