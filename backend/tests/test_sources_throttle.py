"""Politeness controls for rate-limited source APIs (arXiv: one request per 3 s)."""

from __future__ import annotations

import httpx

from app.reliability.retry import RetryPolicy
from app.sources.http import HttpFetcher


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(round(seconds, 3))
        self.now += seconds


def make(handler, clock: FakeClock, **kwargs) -> HttpFetcher:  # noqa: ANN001, ANN003
    return HttpFetcher(
        timeout_seconds=1,
        policy=RetryPolicy(attempts=3, base_delay=0.5, jitter=0.0),
        transport=httpx.MockTransport(handler),
        sleep=clock.sleep,
        clock=clock,
        **kwargs,
    )


def ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, text="ok")


def test_min_interval_spaces_consecutive_requests() -> None:
    clock = FakeClock()
    fetcher = make(ok, clock, min_interval_seconds=3.0)
    fetcher.get("https://x.test/a")
    clock.now += 1.0  # only one second has passed
    fetcher.get("https://x.test/b")
    assert clock.slept == [2.0]  # waited the remaining two seconds
    clock.now += 10.0
    fetcher.get("https://x.test/c")
    assert clock.slept == [2.0]  # long gap: no wait


def test_no_interval_means_no_waiting() -> None:
    clock = FakeClock()
    fetcher = make(ok, clock)
    fetcher.get("https://x.test/a")
    fetcher.get("https://x.test/b")
    assert clock.slept == []


def test_429_waits_at_least_the_rate_limit_floor() -> None:
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        return httpx.Response(429) if state["n"] == 1 else httpx.Response(200, text="ok")

    clock = FakeClock()
    fetcher = make(handler, clock, rate_limit_backoff_seconds=5.0)
    assert fetcher.get("https://x.test/").text == "ok"
    assert clock.slept == [5.0]  # policy would have said 0.5 s


def test_retry_after_header_still_wins_when_longer() -> None:
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "8"})
        return httpx.Response(200, text="ok")

    clock = FakeClock()
    make(handler, clock, rate_limit_backoff_seconds=5.0).get("https://x.test/")
    assert clock.slept == [8.0]
