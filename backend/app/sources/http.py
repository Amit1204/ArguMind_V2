"""Small HTTP fetcher with timeouts and bounded retries for the source APIs."""

from __future__ import annotations

import logging
import ssl
import threading
import time
from collections.abc import Callable

import httpx

from app.reliability.retry import RetryPolicy, backoff_delay, parse_retry_after

log = logging.getLogger(__name__)

USER_AGENT = "ArguMind/0.1 (research assistant; https://github.com/Amit1204)"
# 406 is included on evidence: arXiv's CDN edge intermittently answered
# "406 Not Acceptable" with an empty body to identical requests that succeeded
# seconds later (2026-09-18, for about half an hour, two of three requests).
# It behaves like a transient, so it is retried like one.
_RETRYABLE = {406, 429, 500, 502, 503, 504}


class SourceError(Exception):
    """Base class for source-client failures the pipeline can record."""


class SourceUnavailableError(SourceError):
    """The upstream API kept failing (5xx, 429) after retries."""


class SourceTimeoutError(SourceError):
    pass


class SourceResponseError(SourceError):
    """The upstream answered but the payload could not be parsed."""


def tls_context(max_version: str | None) -> ssl.SSLContext | bool:
    """Default verification (True) or a verifying context capped at TLS 1.2.

    Interoperability setting, not a security choice: arXiv's CDN edge answers
    an empty 406 to TLS 1.3 handshakes that offer ALPN from this image's
    OpenSSL 3.5 build (a bot-fingerprint rule that also hits other Python
    clients; verified 2026-09-23 with cache-miss probes: TLS 1.3 + ALPN -> 406,
    TLS 1.2 -> 200, TLS 1.3 without ALPN -> 200, older OpenSSL -> 200). httpx
    always offers ALPN, so the client is capped at TLS 1.2 for that host only.
    Certificate verification and hostname checks are unchanged.
    """
    if not max_version or max_version == "1.3":
        return True
    if max_version != "1.2":
        raise ValueError(f"unsupported TLS max version: {max_version!r}")
    ctx = ssl.create_default_context()
    ctx.maximum_version = ssl.TLSVersion.TLSv1_2
    return ctx


class HttpFetcher:
    def __init__(
        self,
        timeout_seconds: float = 15.0,
        policy: RetryPolicy | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        min_interval_seconds: float = 0.0,
        rate_limit_backoff_seconds: float = 0.0,
        clock: Callable[[], float] = time.monotonic,
        verify: ssl.SSLContext | bool = True,
    ) -> None:
        """`min_interval_seconds` spaces requests for APIs with a politeness rule
        (arXiv asks for one request every 3 s); `rate_limit_backoff_seconds` is
        the floor for the wait after a 429, whatever the backoff policy says;
        `verify` is httpx's verification setting (True or a custom SSLContext,
        see `tls_context`)."""
        self._policy = policy or RetryPolicy(attempts=3, base_delay=0.5, max_delay=5.0)
        self._client = httpx.Client(
            timeout=timeout_seconds,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json, application/atom+xml"},
            transport=transport,
            follow_redirects=True,
            verify=verify,
        )
        self._sleep = sleep
        self._min_interval = min_interval_seconds
        self._rate_limit_floor = rate_limit_backoff_seconds
        self._clock = clock
        self._last_request_at: float | None = None
        self._lock = threading.Lock()

    def _throttle(self) -> None:
        if self._min_interval <= 0:
            return
        with self._lock:
            now = self._clock()
            if self._last_request_at is not None:
                remaining = self._min_interval - (now - self._last_request_at)
                if remaining > 0:
                    self._sleep(remaining)
            self._last_request_at = self._clock()

    def get(self, url: str, params: dict[str, str | int] | None = None) -> httpx.Response:
        attempt = 0
        while True:
            attempt += 1
            self._throttle()
            try:
                response = self._client.get(url, params=params)
            except httpx.TimeoutException as exc:
                if attempt >= self._policy.attempts:
                    raise SourceTimeoutError(f"{url}: timed out after {attempt} attempts") from exc
                self._wait(attempt, url, "timeout", None)
                continue
            except httpx.HTTPError as exc:
                if attempt >= self._policy.attempts:
                    raise SourceUnavailableError(f"{url}: {type(exc).__name__}") from exc
                self._wait(attempt, url, type(exc).__name__, None)
                continue

            if response.status_code in _RETRYABLE:
                if attempt >= self._policy.attempts:
                    raise SourceUnavailableError(
                        f"{url}: HTTP {response.status_code} after {attempt} attempts"
                    )
                self._wait(attempt, url, f"HTTP {response.status_code}", response)
                continue
            if response.status_code >= 400:
                raise SourceError(f"{url}: HTTP {response.status_code}")
            return response

    def _wait(self, attempt: int, url: str, reason: str, response: httpx.Response | None) -> None:
        retry_after = parse_retry_after(response.headers.get("Retry-After")) if response else None
        delay = retry_after if retry_after is not None else backoff_delay(attempt, self._policy)
        if response is not None and response.status_code == 429:
            delay = max(delay, self._rate_limit_floor)
        log.warning("%s %s; retry %d in %.1fs", url, reason, attempt, delay)
        self._sleep(delay)

    def close(self) -> None:
        self._client.close()
