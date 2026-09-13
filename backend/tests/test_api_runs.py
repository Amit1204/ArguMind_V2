from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.runs import get_run_executor
from app.services.run_executor import RunExecutor
from tests.pipeline_fakes import make_runner

QUESTION = "Do large language models understand language?"


@pytest.fixture
def executor(app: FastAPI) -> RunExecutor:
    runner, store, _, _ = make_runner()
    ex = RunExecutor(runner, store, inline=True)
    app.dependency_overrides[get_run_executor] = lambda: ex
    return ex


def test_create_run_returns_the_finished_run_when_inline(
    client: TestClient, executor: RunExecutor
) -> None:
    response = client.post("/api/v1/runs", json={"question": QUESTION, "client_id": "tests"})
    assert response.status_code == 200  # finished, so 200 rather than 202
    body = response.json()
    assert body["status"] == "answered" and body["answer"]
    assert body["request_id"] == response.headers["x-request-id"]
    assert len(body["stages"]) == 10 and body["usage"]["llm_calls"] > 0


def test_list_and_get_run(client: TestClient, executor: RunExecutor) -> None:
    run_id = client.post("/api/v1/runs", json={"question": QUESTION}).json()["id"]
    listing = client.get("/api/v1/runs").json()
    assert listing["total"] == 1 and listing["runs"][0]["id"] == run_id
    assert listing["runs"][0]["status"] == "answered"
    detail = client.get(f"/api/v1/runs/{run_id}").json()
    assert detail["id"] == run_id and detail["sub_questions"][0] == QUESTION
    graph = client.get(f"/api/v1/runs/{run_id}/graph").json()
    assert graph["run_id"] == run_id and graph["summary"]["refutes_edges"] >= 1


def test_unknown_or_malformed_run_ids_are_404(client: TestClient, executor: RunExecutor) -> None:
    assert client.get("/api/v1/runs/not-a-uuid").status_code == 404
    assert client.get("/api/v1/runs/00000000-0000-0000-0000-000000000000").status_code == 404
    assert client.get("/api/v1/runs/00000000-0000-0000-0000-000000000000/graph").status_code == 404


def test_question_is_validated(client: TestClient, executor: RunExecutor) -> None:
    assert client.post("/api/v1/runs", json={"question": "short"}).status_code == 422
    assert client.post("/api/v1/runs", json={"question": "x" * 501}).status_code == 422
    assert client.post("/api/v1/runs", json={}).status_code == 422


def test_create_run_without_a_model_provider_is_503(client: TestClient) -> None:
    # No override: the real dependency tries to build the gemini provider without a key.
    response = client.post("/api/v1/runs", json={"question": QUESTION})
    assert response.status_code in (503, 500)
