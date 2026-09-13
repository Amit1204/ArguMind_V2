from __future__ import annotations

from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.health import run_readiness_checks
from app.api.system import provide_system_status
from app.schemas.system import ReadinessCheck, SystemStatusResponse


def test_health_reports_service_and_version(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "ArguMind"
    assert body["environment"] == "test"


def test_every_response_carries_a_request_id(client: TestClient) -> None:
    response = client.get("/health")
    assert len(response.headers["x-request-id"]) >= 8


def test_well_formed_incoming_request_id_is_echoed(client: TestClient) -> None:
    response = client.get("/health", headers={"X-Request-ID": "trace-abc-12345"})
    assert response.headers["x-request-id"] == "trace-abc-12345"


def test_malformed_incoming_request_id_is_replaced(client: TestClient) -> None:
    response = client.get("/health", headers={"X-Request-ID": "bad id with spaces"})
    assert response.headers["x-request-id"] != "bad id with spaces"


def test_ready_is_503_when_a_required_check_fails(app: FastAPI, client: TestClient) -> None:
    app.dependency_overrides[run_readiness_checks] = lambda: [
        ReadinessCheck(name="database", ok=True),
        ReadinessCheck(name="schema", ok=False, detail="no migrations applied"),
        ReadinessCheck(name="llm_provider", ok=False, optional=True),
    ]
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"


def test_ready_ignores_optional_checks(app: FastAPI, client: TestClient) -> None:
    app.dependency_overrides[run_readiness_checks] = lambda: [
        ReadinessCheck(name="database", ok=True),
        ReadinessCheck(name="schema", ok=True, detail="schema at 0001_core"),
        ReadinessCheck(name="llm_provider", ok=False, optional=True),
    ]
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_ready_without_a_database_is_503_not_500(client: TestClient) -> None:
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["checks"][0]["name"] == "database"


def test_system_status_without_a_database_is_503_with_retry_after(client: TestClient) -> None:
    response = client.get("/api/v1/system/status")
    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"
    assert response.json()["detail"] == "database unavailable"


def test_system_status_shape(app: FastAPI, client: TestClient) -> None:
    app.dependency_overrides[provide_system_status] = lambda: SystemStatusResponse(
        service="ArguMind",
        version="0.1.0",
        environment="test",
        database_ok=True,
        schema_version="0001_core",
        migrations=["0001_core"],
        runs_total=0,
        runs_by_status={},
        llm_provider="mock",
        llm_configured=True,
        timestamp=datetime.now(UTC),
    )
    body = client.get("/api/v1/system/status").json()
    assert body["schema_version"] == "0001_core"
    assert body["llm_configured"] is True


def test_metrics_endpoint_counts_requests(client: TestClient) -> None:
    client.get("/health")
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "argumind_http_requests_total" in response.text
    assert 'route="/health"' in response.text


def test_unhandled_errors_are_json_with_request_id(app: FastAPI, client: TestClient) -> None:
    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("kaboom")

    response = client.get("/boom", headers={"X-Request-ID": "trace-boom-0001"})
    assert response.status_code == 500
    assert response.json() == {"detail": "internal server error", "request_id": "trace-boom-0001"}
    assert response.headers["x-request-id"] == "trace-boom-0001"
    assert "kaboom" not in response.text


def test_cors_exposes_the_request_id_header(client: TestClient) -> None:
    response = client.get("/health", headers={"Origin": "http://localhost:3100"})
    assert response.headers["access-control-allow-origin"] == "http://localhost:3100"
    assert "x-request-id" in response.headers["access-control-expose-headers"].lower()
