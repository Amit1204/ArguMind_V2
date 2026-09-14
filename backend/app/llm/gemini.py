"""Google Gemini provider built on the official `google-genai` SDK."""

from __future__ import annotations

import logging
import re
import threading
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

# Gemini 429 bodies end with "Please retry in 43.7s". Honour it, but cap the wait:
# a run has a time budget, and a minute-long sleep inside one call is worse than
# losing that source and letting the run continue on the others.
_RETRY_HINT = re.compile(r"retry in ([0-9]+(?:\.[0-9]+)?)\s*s", re.IGNORECASE)
RATE_LIMIT_HINT_CAP_SECONDS = 30.0


def retry_hint_seconds(message: str) -> float | None:
    match = _RETRY_HINT.search(message or "")
    return float(match.group(1)) if match else None


# A model whose *daily* free-tier quota is spent stays spent until the provider's
# reset; its 429 still says "retry in 6s", which is misleading. Park the model
# for this long and let the tier fallback take over at once, instead of burning
# retries (and the run's time budget) on every call.
DAILY_QUOTA_PARK_SECONDS = 3600.0


def daily_quota_violation(details: object) -> str | None:
    """Return a description of a per-day quota violation found in a 429 body
    (google.rpc.QuotaFailure with a quotaId such as
    `GenerateRequestsPerDayPerProjectPerModel-FreeTier`), else None."""
    if not isinstance(details, dict):
        return None
    error = details.get("error") if isinstance(details.get("error"), dict) else details
    for detail in error.get("details") or []:
        if not isinstance(detail, dict):
            continue
        for violation in detail.get("violations") or []:
            if not isinstance(violation, dict):
                continue
            quota_id = str(violation.get("quotaId", ""))
            if "perday" in quota_id.lower():
                return f"{quota_id} (limit {violation.get('quotaValue', '?')})"
    return None


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
        min_interval_seconds: float = 0.0,
        clock: Callable[[], float] = time.monotonic,
        daily_quota_park_seconds: float = DAILY_QUOTA_PARK_SECONDS,
    ) -> None:
        """`min_interval_seconds` spaces requests across all threads of this
        process. The free tier enforces a per-minute quota; a run's extraction
        loop alone can exceed it, and every 429 costs retries and, after three,
        opens the model circuit for two minutes. Pacing the client is cheaper
        than tripping the quota and waiting it out."""
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
        self._clock = clock
        self._min_interval = max(0.0, float(min_interval_seconds))
        self._pace_lock = threading.Lock()
        self._last_request_at: float | None = None
        self._park_seconds = max(0.0, float(daily_quota_park_seconds))
        self._parked_until: dict[str, float] = {}  # model -> clock time
        self._client = client or genai.Client(
            api_key=api_key,
            http_options=gtypes.HttpOptions(timeout=int(timeout_seconds * 1000)),
        )

    def resolve_model(self, tier: ModelTier) -> str:
        return self._models[tier]

    # ------------------------------------------------------------------ public
    def parked_models(self) -> dict[str, float]:
        """Models whose daily quota is spent, with seconds until they are retried."""
        now = self._clock()
        return {m: round(until - now, 1) for m, until in self._parked_until.items() if until > now}

    def _is_parked(self, model: str) -> bool:
        until = self._parked_until.get(model)
        if until is None:
            return False
        if self._clock() >= until:
            del self._parked_until[model]
            log.info("%s: daily-quota park expired; trying it again", model)
            return False
        return True

    def _park(self, model: str, reason: str) -> None:
        if self._park_seconds <= 0:
            return
        self._parked_until[model] = self._clock() + self._park_seconds
        log.warning(
            "%s: daily quota exhausted (%s); parking it for %.0f min and using the fallback tier",
            model,
            reason,
            self._park_seconds / 60,
        )

    def complete(self, request: LLMRequest) -> LLMResponse:
        tier = request.tier
        model = self.resolve_model(tier)
        try:
            if self._is_parked(model):
                raise LLMRateLimitError(f"{model}: daily quota exhausted (parked)")
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
            self._pace()
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
                    daily = daily_quota_violation(getattr(exc, "details", None))
                    if daily:
                        self._park(model, daily)
                        raise LLMRateLimitError(f"{model}: daily quota exhausted: {daily}") from exc
                    if attempts <= self._max_retries:
                        self._backoff(
                            attempts, model, "rate limited", hint=retry_hint_seconds(message)
                        )
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

    def _pace(self) -> None:
        """Hold the caller until `min_interval_seconds` have passed since the
        previous request left this process. Callers queue on the lock, so N
        concurrent runs share one provider-wide rate rather than each having
        their own."""
        if self._min_interval <= 0:
            return
        with self._pace_lock:
            now = self._clock()
            if self._last_request_at is not None:
                remaining = self._min_interval - (now - self._last_request_at)
                if remaining > 0:
                    self._sleep(remaining)
                    now = self._clock()
            self._last_request_at = now

    def _backoff(self, attempt: int, model: str, reason: str, hint: float | None = None) -> None:
        if hint is not None and hint > 0:
            delay = min(hint + 0.5, RATE_LIMIT_HINT_CAP_SECONDS)
            reason = f"{reason} (provider asks for {hint:.0f}s)"
        else:
            delay = backoff_delay(attempt, _BACKOFF)
        log.warning("%s %s; retry %d in %.1fs", model, reason, attempt, delay)
        self._sleep(delay)
