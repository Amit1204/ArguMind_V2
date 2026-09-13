"""FastAPI application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import health, runs, sources, system
from app.api import metrics as metrics_api
from app.config import Settings, get_settings
from app.db import dispose_engines
from app.observability.context import REQUEST_ID_HEADER
from app.observability.logging import configure_logging
from app.observability.middleware import RequestContextMiddleware

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    log.info(
        "%s %s starting", settings.app_name, settings.app_version,
        extra={"environment": settings.app_env, "llm_provider": settings.llm_provider},
    )  # fmt: skip
    yield
    executor = getattr(app.state, "run_executor", None)
    if executor is not None:
        executor.shutdown()
    dispose_engines()
    log.info("%s stopped", settings.app_name)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_format)

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=(
            "Research questions answered with evidence: retrieved sources, claims with "
            "stance, a citation graph with explicit agreement and disagreement, and answers "
            "whose citations are verified."
        ),
        lifespan=lifespan,
    )
    app.state.settings = settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[REQUEST_ID_HEADER],
    )
    # Added last so it is outermost: every response, including errors, carries the id.
    app.add_middleware(RequestContextMiddleware)

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        # This handler runs outside the request middleware, so set the header here.
        return JSONResponse(
            status_code=500,
            content={"detail": "internal server error", "request_id": request_id},
            headers={REQUEST_ID_HEADER: request_id} if request_id else None,
        )

    app.include_router(health.router)
    app.include_router(metrics_api.router)
    app.include_router(system.router)
    app.include_router(sources.router)
    app.include_router(runs.router)
    return app


app = create_app()
