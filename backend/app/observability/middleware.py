"""ASGI middleware: request id in, request id out, one structured access line, metrics."""

from __future__ import annotations

import logging
import time

from app.observability import metrics
from app.observability.context import (
    REQUEST_ID_HEADER,
    accept_request_id,
    bind_request,
    clear_request,
)

log = logging.getLogger("app.http")
_UNMETERED = {"/metrics"}


class RequestContextMiddleware:
    def __init__(self, app) -> None:  # noqa: ANN001 - ASGI app
        self.app = app

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001 - ASGI signature
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = accept_request_id(_header(scope, REQUEST_ID_HEADER.lower()))
        scope.setdefault("state", {})["request_id"] = request_id
        token = bind_request(request_id)
        started = time.perf_counter()
        status_holder = {"status": 500}

        async def send_with_header(message) -> None:  # noqa: ANN001
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                headers = message.setdefault("headers", [])
                headers.append((REQUEST_ID_HEADER.lower().encode(), request_id.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_with_header)
        finally:
            elapsed = time.perf_counter() - started
            path = scope.get("path", "")
            route = getattr(scope.get("route"), "path", None) or _fallback_route(path)
            status = status_holder["status"]
            method = scope.get("method", "?")
            if path not in _UNMETERED:
                metrics.observe_http(method, route, status, elapsed)
                log.info(
                    "%s %s -> %d in %dms",
                    method,
                    path,
                    status,
                    int(elapsed * 1000),
                    extra={
                        "event": "http_request",
                        "method": method,
                        "path": path,
                        "route": route,
                        "status": status,
                        "duration_ms": int(elapsed * 1000),
                        "client": (scope.get("client") or ("?",))[0],
                    },
                )
            clear_request(token)


def _header(scope, name: str) -> str | None:  # noqa: ANN001
    for key, value in scope.get("headers", []):
        if key.decode().lower() == name:
            return value.decode()
    return None


def _fallback_route(path: str) -> str:
    """Keep the route label bounded when no route matched (404s, static)."""
    if path in ("/health", "/ready", "/docs", "/openapi.json"):
        return path
    return "unmatched"
