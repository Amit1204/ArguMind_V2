"""Prometheus metrics. A dedicated registry keeps tests that build several apps
from colliding on duplicate collector names."""

from __future__ import annotations

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
)

REGISTRY = CollectorRegistry()

HTTP_REQUESTS = Counter(
    "argumind_http_requests_total",
    "HTTP requests handled, by method, route template and status code.",
    ["method", "route", "status"],
    registry=REGISTRY,
)
HTTP_LATENCY = Histogram(
    "argumind_http_request_duration_seconds",
    "HTTP request latency by route template.",
    ["route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120),
    registry=REGISTRY,
)


def observe_http(method: str, route: str, status: int, seconds: float) -> None:
    HTTP_REQUESTS.labels(method=method, route=route, status=str(status)).inc()
    HTTP_LATENCY.labels(route=route).observe(seconds)


def render() -> tuple[bytes, str]:
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
