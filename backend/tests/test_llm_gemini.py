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


def test_requests_are_paced_process_wide_when_min_interval_is_set() -> None:
    """Three back-to-back calls with a 2 s minimum interval: the first goes out
    at once, the next two wait for the remainder of the interval. A retry after
    a 429 is paced too, so a burst of retries cannot hammer the quota."""
    fake = {"now": 100.0}
    slept: list[float] = []

    def sleep(seconds: float) -> None:
        slept.append(round(seconds, 3))
        fake["now"] += seconds

    def clock() -> float:
        return fake["now"]

    models = FakeModels(
        {"fast-model": [_response(), _response(), _client_error(429, "quota"), _response()]}
    )
    provider = GeminiProvider(
        "key",
        MODELS,
        max_retries=1,
        client=SimpleNamespace(models=models),
        sleep=sleep,
        min_interval_seconds=2.0,
        clock=clock,
    )
    request = LLMRequest("s", "p", tier=ModelTier.FAST)
    provider.complete(request)  # t=100, no wait
    fake["now"] += 0.5  # the call itself took 0.5 s
    provider.complete(request)  # t=100.5 -> waits 1.5 s
    fake["now"] += 3.0  # long gap: no wait needed
    provider.complete(request)  # 429 -> backoff -> retry is paced again
    assert len(models.calls) == 4
    # 1.5 s pacing wait, then the 429 backoff (~1 s with jitter), then a pacing
    # wait for whatever remains of the 2 s interval after that backoff.
    assert slept[0] == 1.5
    assert 0.8 <= slept[1] <= 1.2 and abs(slept[1] + slept[2] - 2.0) < 1e-6

    unpaced = GeminiProvider(
        "key", MODELS, client=SimpleNamespace(models=FakeModels({"fast-model": [_response()] * 2})),
        sleep=lambda s: slept.append(-1.0), min_interval_seconds=0,
    )  # fmt: skip
    unpaced.complete(request)
    unpaced.complete(request)
    assert -1.0 not in slept


def test_rate_limit_retry_honours_the_providers_hint_with_a_cap() -> None:
    from app.llm.gemini import RATE_LIMIT_HINT_CAP_SECONDS, retry_hint_seconds

    body = (
        "You exceeded your current quota. * Quota exceeded for metric: "
        "generate_content_free_tier_requests, limit: 15, model: fast-model\n"
        "Please retry in 43.728327417s."
    )
    assert retry_hint_seconds(body) == 43.728327417
    assert retry_hint_seconds("no hint here") is None

    slept: list[float] = []
    models = FakeModels({"fast-model": [_client_error(429, body), _response()]})
    provider = GeminiProvider(
        "key", MODELS, max_retries=1, client=SimpleNamespace(models=models), sleep=slept.append
    )
    provider.complete(LLMRequest("s", "p", tier=ModelTier.FAST))
    assert slept == [RATE_LIMIT_HINT_CAP_SECONDS]  # 43.7 s hint capped to 30 s

    slept.clear()
    models = FakeModels(
        {"fast-model": [_client_error(429, "quota; Please retry in 7.2s"), _response()]}
    )
    provider = GeminiProvider(
        "key", MODELS, max_retries=1, client=SimpleNamespace(models=models), sleep=slept.append
    )
    provider.complete(LLMRequest("s", "p", tier=ModelTier.FAST))
    assert slept == [7.7]  # hint + 0.5 s margin


def _daily_quota_error(model: str) -> gerrors.ClientError:
    return gerrors.ClientError(
        429,
        {
            "error": {
                "message": "You exceeded your current quota. Please retry in 6.2s.",
                "status": "RESOURCE_EXHAUSTED",
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                        "violations": [
                            {
                                "quotaMetric": "generativelanguage.googleapis.com/"
                                "generate_content_free_tier_requests",
                                "quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
                                "quotaValue": "20",
                                "quotaDimensions": {"model": model},
                            }
                        ],
                    },
                    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "6s"},
                ],
            }
        },
    )


def test_daily_quota_parks_the_model_and_falls_back_without_retrying() -> None:
    """A per-day quota 429 is not transient: no retry, no sleep, straight to the
    fallback tier; later calls skip the parked model until the park expires."""
    from app.llm.gemini import daily_quota_violation

    assert daily_quota_violation(_daily_quota_error("standard-model").details) == (
        "GenerateRequestsPerDayPerProjectPerModel-FreeTier (limit 20)"
    )
    assert daily_quota_violation({"error": {"details": []}}) is None
    assert daily_quota_violation("not a dict") is None

    fake = {"now": 0.0}
    slept: list[float] = []
    models = FakeModels(
        {
            "standard-model": [_daily_quota_error("standard-model"), _response()],
            "fast-model": [_response(), _response()],
        }
    )
    provider = GeminiProvider(
        "key",
        MODELS,
        max_retries=2,
        client=SimpleNamespace(models=models),
        sleep=slept.append,
        clock=lambda: fake["now"],
        daily_quota_park_seconds=3600,
    )
    request = LLMRequest("s", "p", tier=ModelTier.STANDARD, purpose="consensus")
    first = provider.complete(request)
    assert first.usage.model == "fast-model" and first.usage.fallback is True
    assert slept == []  # no backoff for a daily quota
    assert [m for m, _ in models.calls] == ["standard-model", "fast-model"]
    assert provider.parked_models() == {"standard-model": 3600.0}

    fake["now"] = 1800.0
    second = provider.complete(request)  # parked: the standard model is not even tried
    assert second.usage.fallback is True
    assert [m for m, _ in models.calls] == ["standard-model", "fast-model", "fast-model"]

    fake["now"] = 3601.0
    third = provider.complete(request)  # park expired: tried again and it works
    assert third.usage.model == "standard-model" and third.usage.fallback is False
    assert provider.parked_models() == {}

    # the fast tier has no fallback: a parked fast model raises at once
    fast_only = GeminiProvider(
        "key",
        MODELS,
        client=SimpleNamespace(models=FakeModels({"fast-model": [_daily_quota_error("f")]})),
        sleep=slept.append,
    )
    with pytest.raises(LLMRateLimitError, match="daily quota"):
        fast_only.complete(LLMRequest("s", "p", tier=ModelTier.FAST))
    with pytest.raises(LLMRateLimitError, match="parked"):
        fast_only.complete(LLMRequest("s", "p", tier=ModelTier.FAST))
    assert slept == []


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
