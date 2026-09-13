"""Google Gemini provider built on the official `google-genai` SDK."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from google import genai
from google.genai import errors as gerrors
from google.genai import types as gtypes

from app.llm.base import (
    LLMConfigurationError,
    LLMError,
    LLMProvider,
    LLMRateLimitError,
    LLMRequest,
    LLMResponse,
    LLMResponseFormatError,
    LLMTimeoutError,
    LLMUnavailableError,
    LLMUsage,
    ModelTier,
)
from app.llm.pricing import PriceTable
from app.reliability.retry import RetryPolicy, backoff_delay

log = logging.getLogger(__name__)

_BACKOFF = RetryPolicy(attempts=3, base_delay=1.0, multiplier=2.0, max_delay=8.0, jitter=0.2)

# When a tier's model is unavailable after retries, degrade one tier rather than fail.
FALLBACK_TIER: dict[ModelTier, ModelTier] = {ModelTier.STANDARD: ModelTier.FAST}


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(
        self,
        api_key: str,
        models: dict[ModelTier, str],
        timeout_seconds: float = 45.0,
        max_retries: int = 2,
        price_table: PriceTable | None = None,
        client: genai.Client | None = None,
        thinking_level: str | None = "low",
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key and client is None:
            raise LLMConfigurationError("LLM_API_KEY is required for the gemini provider")
        missing = [tier.value for tier in ModelTier if not models.get(tier)]
        if missing:
            raise LLMConfigurationError(f"no model configured for tiers: {missing}")
        self._models = models
        self._max_retries = max_retries
        # Thinking tokens count against max_output_tokens on Gemini 3 models, so an
        # unbounded budget can truncate the JSON we asked for. Standard tier only.
        self._thinking_level = (thinking_level or "").strip().lower() or None
        self._prices = price_table or PriceTable({})
        self._sleep = sleep
        self._client = client or genai.Client(
            api_key=api_key,
            http_options=gtypes.HttpOptions(timeout=int(timeout_seconds * 1000)),
        )

    def resolve_model(self, tier: ModelTier) -> str:
        return self._models[tier]

    # ------------------------------------------------------------------ public
    def complete(self, request: LLMRequest) -> LLMResponse:
        tier = request.tier
        model = self.resolve_model(tier)
        try:
            return self._complete_with_model(request, model, tier)
        except (LLMUnavailableError, LLMRateLimitError) as exc:
            fallback_tier = FALLBACK_TIER.get(tier)
            fallback_model = self.resolve_model(fallback_tier) if fallback_tier else None
            if not fallback_model or fallback_model == model:
                raise
            log.warning(
                "%s unavailable for %s (%s); falling back to %s",
                model,
                request.purpose,
                type(exc).__name__,
                fallback_model,
            )
            response = self._complete_with_model(request, fallback_model, fallback_tier)
            response.usage.fallback = True
            response.usage.attempts += self._max_retries + 1
            return response

    # ---------------------------------------------------------------- internals
    def _build_config(
        self, request: LLMRequest, tier: ModelTier, with_schema: bool, with_thinking: bool
    ) -> gtypes.GenerateContentConfig:
        config = gtypes.GenerateContentConfig(
            system_instruction=request.system,
            temperature=request.temperature,
            max_output_tokens=request.max_output_tokens,
            # We never pass tools; disabling AFC silences the SDK's per-call warning.
            automatic_function_calling=gtypes.AutomaticFunctionCallingConfig(disable=True),
        )
        if with_thinking and self._thinking_level and tier is not ModelTier.FAST:
            config.thinking_config = gtypes.ThinkingConfig(thinking_level=self._thinking_level)
        if request.response_schema is not None:
            config.response_mime_type = "application/json"
            if with_schema:
                config.response_json_schema = request.response_schema.model_json_schema()
        return config

    def _complete_with_model(self, request: LLMRequest, model: str, tier: ModelTier) -> LLMResponse:
        with_schema = True
        with_thinking = True
        attempts = 0
        started = time.perf_counter()
        while True:
            attempts += 1
            try:
                response = self._client.models.generate_content(
                    model=model,
                    contents=request.prompt,
                    config=self._build_config(request, tier, with_schema, with_thinking),
                )
                break
            except gerrors.ClientError as exc:
                code = getattr(exc, "code", None) or 400
                message = str(getattr(exc, "message", exc))
                if code == 429:
                    if attempts <= self._max_retries:
                        self._backoff(attempts, model, "rate limited")
                        continue
                    raise LLMRateLimitError(f"{model}: {message}") from exc
                if code == 400 and with_thinking and "thinking" in message.lower():
                    log.warning("%s rejected thinking_level; retrying without it", model)
                    with_thinking = False
                    continue
                if code == 400 and with_schema and "schema" in message.lower():
                    # Some models reject parts of JSON Schema; fall back to JSON mode
                    # without a schema and let LLMResponse.parse() validate.
                    log.warning("%s rejected response schema; retrying without it", model)
                    with_schema = False
                    continue
                raise LLMError(f"{model}: HTTP {code} {message}") from exc
            except gerrors.ServerError as exc:
                if attempts <= self._max_retries:
                    self._backoff(attempts, model, "server error")
                    continue
                raise LLMUnavailableError(f"{model}: {getattr(exc, 'message', exc)}") from exc
            except Exception as exc:  # SDK surfaces httpx timeouts as plain exceptions
                if "timeout" in type(exc).__name__.lower() or "timed out" in str(exc).lower():
                    if attempts <= self._max_retries:
                        self._backoff(attempts, model, "timeout")
                        continue
                    raise LLMTimeoutError(f"{model}: request timed out") from exc
                raise LLMError(f"{model}: {type(exc).__name__}: {exc}") from exc

        latency_ms = int((time.perf_counter() - started) * 1000)
        text = response.text or ""
        if not text.strip():
            finish = None
            if response.candidates:
                finish = getattr(response.candidates[0], "finish_reason", None)
            raise LLMResponseFormatError(
                f"{model} returned an empty response (finish_reason={finish})"
            )

        meta = response.usage_metadata
        input_tokens = int(getattr(meta, "prompt_token_count", 0) or 0)
        output_tokens = int(getattr(meta, "candidates_token_count", 0) or 0)
        thinking_tokens = int(getattr(meta, "thoughts_token_count", 0) or 0)
        usage = LLMUsage(
            provider=self.name,
            model=model,
            tier=tier.value,
            purpose=request.purpose,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            thinking_tokens=thinking_tokens,
            latency_ms=latency_ms,
            estimated_cost_usd=self._prices.estimate(
                model, input_tokens, output_tokens + thinking_tokens
            ),
            attempts=attempts,
        )
        log.info(
            "llm call purpose=%s model=%s latency_ms=%d in=%d out=%d thinking=%d attempts=%d",
            request.purpose,
            model,
            latency_ms,
            input_tokens,
            output_tokens,
            thinking_tokens,
            attempts,
        )
        return LLMResponse(text=text, usage=usage, raw=response)

    def _backoff(self, attempt: int, model: str, reason: str) -> None:
        delay = backoff_delay(attempt, _BACKOFF)
        log.warning("%s %s; retry %d in %.1fs", model, reason, attempt, delay)
        self._sleep(delay)
