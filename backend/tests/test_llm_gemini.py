"""GeminiProvider error mapping and tier fallback, with a fake SDK client (no network)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from google.genai import errors as gerrors

from app.llm.base import (
    LLMRateLimitError,
    LLMRequest,
    LLMResponseFormatError,
    LLMTimeoutError,
    LLMUnavailableError,
    ModelTier,
)
from app.llm.gemini import GeminiProvider

MODELS = {ModelTier.FAST: "fast-model", ModelTier.STANDARD: "standard-model"}


def _response(text: str = '{"ok": true}', prompt=10, out=4, thoughts=0):  # noqa: ANN001
    return SimpleNamespace(
        text=text,
        candidates=[SimpleNamespace(finish_reason="STOP")],
        usage_metadata=SimpleNamespace(
            prompt_token_count=prompt, candidates_token_count=out, thoughts_token_count=thoughts
        ),
    )


def _client_error(code: int, message: str) -> gerrors.ClientError:
    return gerrors.ClientError(code, {"error": {"message": message, "status": "X"}})


def _server_error(code: int, message: str) -> gerrors.ServerError:
    return gerrors.ServerError(code, {"error": {"message": message, "status": "X"}})


class FakeModels:
    def __init__(self, script: dict[str, list]) -> None:
        self.script = {k: list(v) for k, v in script.items()}
        self.calls: list[tuple[str, object]] = []

    def generate_content(self, model: str, contents: str, config):  # noqa: ANN001
        self.calls.append((model, config))
        outcome = self.script[model].pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def provider_with(script: dict[str, list]) -> tuple[GeminiProvider, FakeModels]:
    models = FakeModels(script)
    client = SimpleNamespace(models=models)
    provider = GeminiProvider("key", MODELS, max_retries=1, client=client, sleep=lambda _s: None)
    return provider, models


def test_success_reports_usage_and_tier() -> None:
    provider, models = provider_with(
        {"standard-model": [_response(prompt=100, out=20, thoughts=5)]}
    )
    response = provider.complete(LLMRequest("s", "p", tier=ModelTier.STANDARD, purpose="x"))
    assert response.text == '{"ok": true}'
    assert (
        response.usage.input_tokens,
        response.usage.output_tokens,
        response.usage.thinking_tokens,
    ) == (100, 20, 5)
    assert response.usage.model == "standard-model" and response.usage.fallback is False
    # standard tier asks for thinking; JSON mode is only set when a schema is requested
    config = models.calls[0][1]
    assert config.thinking_config is not None and config.response_mime_type is None


def test_rate_limit_retries_then_falls_back_to_fast_tier() -> None:
    provider, models = provider_with(
        {
            "standard-model": [_client_error(429, "quota"), _client_error(429, "quota")],
            "fast-model": [_response()],
        }
    )
    response = provider.complete(LLMRequest("s", "p", tier=ModelTier.STANDARD))
    assert response.usage.model == "fast-model" and response.usage.fallback is True
    assert [m for m, _ in models.calls] == ["standard-model", "standard-model", "fast-model"]


def test_fast_tier_has_no_fallback_and_raises_rate_limit() -> None:
    provider, _ = provider_with({"fast-model": [_client_error(429, "quota")] * 2})
    with pytest.raises(LLMRateLimitError):
        provider.complete(LLMRequest("s", "p", tier=ModelTier.FAST))


def test_server_errors_become_unavailable_after_retries() -> None:
    provider, _ = provider_with(
        {"fast-model": [_server_error(503, "busy"), _server_error(503, "busy")]}
    )
    with pytest.raises(LLMUnavailableError):
        provider.complete(LLMRequest("s", "p", tier=ModelTier.FAST))


def test_timeouts_are_mapped() -> None:
    class FakeReadTimeoutError(Exception):
        pass

    provider, _ = provider_with(
        {"fast-model": [FakeReadTimeoutError("slow"), FakeReadTimeoutError("slow")]}
    )
    with pytest.raises(LLMTimeoutError):
        provider.complete(LLMRequest("s", "p", tier=ModelTier.FAST))


def test_schema_rejection_retries_without_schema() -> None:
    from pydantic import BaseModel

    class Out(BaseModel):
        ok: bool

    provider, models = provider_with(
        {"fast-model": [_client_error(400, "Unsupported JSON schema field"), _response()]}
    )
    response = provider.complete(LLMRequest("s", "p", tier=ModelTier.FAST, response_schema=Out))
    assert response.parse(Out).ok is True
    first, second = (c for _, c in models.calls)
    assert first.response_json_schema is not None and second.response_json_schema is None
    assert second.response_mime_type == "application/json"


def test_empty_response_is_a_format_error() -> None:
    provider, _ = provider_with({"fast-model": [_response(text="")]})
    with pytest.raises(LLMResponseFormatError):
        provider.complete(LLMRequest("s", "p", tier=ModelTier.FAST))
