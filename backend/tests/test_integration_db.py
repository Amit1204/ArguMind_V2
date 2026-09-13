"""Opt-in integration tests against a real PostgreSQL (RUN_INTEGRATION_TESTS=1).

Run inside Compose, where DATABASE_URL points at the migrated database:
    docker compose run --rm -e RUN_INTEGRATION_TESTS=1 backend \
        python -m pytest -q tests/test_integration_db.py
"""

from __future__ import annotations

import os

import pytest

from app.config import Settings
from app.db import get_session_factory
from app.llm.mock import MockProvider
from app.pipeline.detail import build_graph_response, build_run_detail
from app.pipeline.runner import PipelineRunner
from app.pipeline.store import DbRunStore
from tests.pipeline_fakes import FakeSourceService

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION_TESTS") != "1", reason="needs a live database"
)


@pytest.fixture
def settings() -> Settings:
    return Settings(app_env="test", llm_provider="mock", log_format="text")


def test_run_round_trips_through_postgres(settings: Settings) -> None:
    store = DbRunStore(get_session_factory(settings))
    runner = PipelineRunner(settings, MockProvider(), FakeSourceService(), store)
    before = store.count_runs()
    run_id = runner.execute("Do large language models understand language?", request_id="int-1")

    detail = build_run_detail(store, run_id)
    assert detail is not None
    assert detail.status == "answered" and detail.request_id == "int-1"
    assert [s.name for s in detail.stages][:3] == ["plan", "gather", "extract"]
    assert len(detail.sources) == 3  # unique per run in the sources table
    assert {c.stance.value for c in detail.claims} >= {"supports", "refutes"}
    assert detail.resolutions and detail.usage.llm_calls > 0
    assert detail.finished_at is not None and detail.latency_ms is not None

    graph = build_graph_response(store, run_id)
    assert graph is not None and graph.summary["refutes_edges"] >= 1
    assert (
        any(e["edge_type"] == "extends" for e in graph.edges) or graph.summary["extends_edges"] == 0
    )

    assert store.count_runs() == before + 1
    assert store.list_runs(1, 0)[0].id == run_id
    assert store.get_run("00000000-0000-0000-0000-000000000000") is None
