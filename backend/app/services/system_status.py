"""Readiness checks and the system status summary shown on the Overview page."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import ping
from app.schemas.system import ReadinessCheck, SystemStatusResponse

# The migration the current code requires. Bumped whenever a phase adds one.
REQUIRED_MIGRATION = "0001_core"


def applied_migrations(session: Session) -> list[str]:
    rows = session.execute(text("SELECT version FROM schema_migrations ORDER BY version")).all()
    return [row[0] for row in rows]


def readiness_checks(session: Session, settings: Settings) -> list[ReadinessCheck]:
    checks = [ReadinessCheck(name="database", ok=ping(session))]
    migrations = applied_migrations(session)
    checks.append(
        ReadinessCheck(
            name="schema",
            ok=REQUIRED_MIGRATION in migrations,
            detail=f"schema at {migrations[-1]}" if migrations else "no migrations applied",
        )
    )
    checks.append(
        ReadinessCheck(
            name="llm_provider",
            ok=settings.llm_configured,
            detail=f"{settings.llm_provider}: "
            + ("configured" if settings.llm_configured else "LLM_API_KEY not set"),
            optional=True,
        )
    )
    return checks


def build_system_status(session: Session, settings: Settings) -> SystemStatusResponse:
    migrations = applied_migrations(session)
    by_status = {
        str(status): int(count)
        for status, count in session.execute(
            text("SELECT status, COUNT(*) FROM runs GROUP BY status")
        ).all()
    }
    return SystemStatusResponse(
        service=settings.app_name,
        version=settings.app_version,
        environment=settings.app_env,
        database_ok=True,
        schema_version=migrations[-1] if migrations else None,
        migrations=migrations,
        runs_total=sum(by_status.values()),
        runs_by_status=by_status,
        llm_provider=settings.llm_provider,
        llm_configured=settings.llm_configured,
        timestamp=datetime.now(UTC),
    )
