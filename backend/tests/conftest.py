from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings() -> Settings:
    # An unreachable database URL: unit tests must never depend on a live database.
    return Settings(
        app_env="test",
        log_level="WARNING",
        log_format="text",
        llm_provider="mock",
        database_url="postgresql+psycopg://nobody:nothing@127.0.0.1:1/none",
    )


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
