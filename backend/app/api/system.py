"""System status for the frontend Overview page."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
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
