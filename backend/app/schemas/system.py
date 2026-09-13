"""Response models for health, readiness and system status."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str
    version: str
    environment: str
    timestamp: datetime


class ReadinessCheck(BaseModel):
    name: str
    ok: bool
    detail: str | None = None
    # Optional checks are reported but do not flip readiness (e.g. the LLM key).
    optional: bool = False


class ReadinessResponse(BaseModel):
    status: str
    checks: list[ReadinessCheck]
    timestamp: datetime


class SystemStatusResponse(BaseModel):
    service: str
    version: str
    environment: str
    database_ok: bool
    schema_version: str | None = Field(default=None, description="Latest applied migration")
    migrations: list[str] = Field(default_factory=list)
    runs_total: int = 0
    runs_by_status: dict[str, int] = Field(default_factory=dict)
    llm_provider: str
    llm_configured: bool
    timestamp: datetime
