# ADR-009: In-process circuit breakers per dependency and a per-client rate limit

**Status:** Accepted (Phase 6)

## Problem

Two dependencies fail in ways that hurt the whole system, not just one run:
arXiv throttles a client for minutes after a burst and answers slowly while
the penalty decays, and the model provider's free tier returns 429s once the
daily quota is gone. Phase 4 bounded the damage inside a run (failure memo,
gather budget); nothing stopped the next run from paying the same price
again. Separately, nothing stopped one client from starting runs faster than
two workers can finish them.

## Decisions

1. **A circuit breaker per source kind and one for the model provider**,
   in process, keyed by name (`source:arxiv`, `source:wikipedia`,
   `llm:gemini`). Three consecutive failures open a circuit for 120 seconds;
   then one probe call is allowed and its outcome closes or re-opens it. An
   open source circuit makes the gather stage report that source as
   unavailable instantly, with the seconds until the next probe in the
   caveat. An open model circuit turns model calls into `LLMUnavailableError`
   immediately, so the deterministic fallbacks engage without waiting.
   Budget-exhausted and format errors do not count: they are not the
   dependency's fault.

2. **A fixed-window rate limit of 5 runs per minute per client** (the
   `client_id` in the request, else the caller's IP), answered with `429`,
   a JSON envelope carrying the request id, and `Retry-After`. A queue cap
   (`RUN_QUEUE_MAX`) answers `503` with `Retry-After` when the worker pool is
   saturated, instead of accepting work it cannot start.

3. **Every error response carries the request id**, so a caveat in the UI, a
   log line, a metric label and a support conversation refer to the same
   identifier.

## Alternatives considered

- Shared state in Redis for breakers and limits: correct for several
  replicas, but this stack runs one backend process by design (ADR-007);
  the copilot project documents the Redis path and it applies unchanged here.
- Per-run retries only (no breaker): every run after an outage pays the full
  timeout again; observed in Phase 4 as a 200-second inconclusive run.
- Token-bucket limits: smoother, but a fixed window is enough at five runs a
  minute and is trivially explainable.

## Consequences

- A recovering dependency is probed once every 120 seconds; the first run
  after recovery may still see a stale "open" caveat for one source if it
  starts before the probe.
- Breaker and limiter state is lost on restart, which is acceptable: a
  restart is also when a stuck dependency is most likely to have changed.
- The `argumind_circuit_state` gauge and the operations endpoint expose the
  state, so a demo can show a circuit opening and closing.
