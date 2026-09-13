from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_defaults_boot_without_environment() -> None:
    settings = Settings()
    assert settings.app_env == "development"
    assert settings.llm_provider == "gemini"
    assert settings.cors_origin_list == ["http://localhost:3100"]


def test_environment_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    monkeypatch.setenv("CORS_ORIGINS", "http://a.example, http://b.example")
    settings = Settings()
    assert settings.llm_provider == "mock"
    assert settings.log_level == "DEBUG"
    assert settings.cors_origin_list == ["http://a.example", "http://b.example"]


def test_llm_configured_requires_a_key_for_gemini() -> None:
    assert Settings(llm_provider="gemini", llm_api_key="").llm_configured is False
    assert Settings(llm_provider="gemini", llm_api_key="k").llm_configured is True
    assert Settings(llm_provider="mock").llm_configured is True


def test_secret_is_not_printed() -> None:
    settings = Settings(llm_api_key="super-secret-value")
    assert "super-secret-value" not in repr(settings)
    assert "super-secret-value" not in settings.model_dump_json()


@pytest.mark.parametrize(
    "field, value",
    [
        ("llm_provider", "openai"),
        ("log_level", "loud"),
        ("app_env", "prod"),
        ("run_timeout_seconds", 5),
    ],
)
def test_invalid_values_are_rejected(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        Settings(**{field: value})
