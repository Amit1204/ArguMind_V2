# Reliability (Phase 6)

How the system behaves when its dependencies misbehave. Everything listed is
**implemented** and covered by tests that need no network.

## Controls, from the outside in

| Layer | Control | Setting | Behaviour |
|-------|---------|---------|-----------|
| API | rate limit per client | `RATE_LIMIT_RUNS_PER_MINUTE` (5) | `429` + `Retry-After` + JSON envelope with request id |
| API | queue cap | `RUN_QUEUE_MAX` (6) | `503` + `Retry-After` when the worker pool is saturated |
| API | bounded workers | `RUN_WORKERS` (2) | runs execute off the request thread; progress is persisted |
| Run | time budget | `RUN_TIMEOUT_SECONDS` (180), `GATHER_BUDGET_FRACTION` (0.5) | gathering stops at half the budget; later stages skip work with caveats |
| Run | in-run failure memo | — | a source that fails once is not retried in that run |
| Run | bounded retry | `MAX_ITERATIONS` (1) | one broadened gather, refused when the budget is spent |
| Sources | retries with jittered backoff | `SOURCE_RETRY_ATTEMPTS` (3) | 429 and 5xx retried, `Retry-After` honoured (capped) |
| Sources | politeness throttle | `ARXIV_MIN_INTERVAL_SECONDS` (3), `ARXIV_TIMEOUT_SECONDS` (30) | one arXiv request per 3 s, 5 s floor after a 429 |
| Sources | circuit breaker per kind | `CIRCUIT_FAILURE_THRESHOLD` (3), `CIRCUIT_RECOVERY_SECONDS` (120) | open circuit → instant per-source error, one probe after recovery |
| Sources | lookup cache | `SOURCE_CACHE_TTL_HOURS` (24) | identical searches never leave the database |
| Model | request pacing | `LLM_MIN_INTERVAL_SECONDS` (4) | one provider request per 4 s process-wide (15/min, the free tier's per-model limit), shared by concurrent runs; added after the first live benchmark tripped the quota and opened the model circuit on every case |
| Model | retries with jittered backoff | `LLM_MAX_RETRIES` (2) | 429, 5xx and timeouts; a 429's "retry in Ns" hint is honoured, capped at 30 s so one call cannot eat the run budget |
| Model | tier fallback | — | standard → fast when a model stays unavailable |
| Model | daily-quota parking | — | a 429 whose body names a per-day quota (`...PerDay...` in `QuotaFailure`) is not retried: the model is parked for 60 min and the tier fallback applies at once. Found live: the free tier allows **20 requests/day** for the standard model, and its 429 still says "retry in 6 s" |
| Model | daily request budget | `LLM_DAILY_REQUEST_LIMIT` (1000) | refused locally before the provider's quota is hit |
| Model | circuit breaker | same thresholds | open circuit → calls fail fast as unavailable |
| Model | schema/thinking degradation | — | retry without response schema or thinking config when a model rejects them |
| Stages | deterministic fallbacks | — | plan → original question; consensus → tally; answer → template; critic never calls the model |
| Answer | citation verification | — | invented sources removed; uncited answers flagged, confidence halved |
| Outcome | `inconclusive` | `MIN_EVIDENCE_CLAIMS` (2), `MIN_EVIDENCE_SOURCES` (2) | thin evidence ends the run honestly, without an answer model call |

## Degradation table

| Failure | Run outcome | Visible where |
|---------|-------------|---------------|
| one source throttled | answered or inconclusive from the other source; caveat per sub-question | caveats, gather stage detail, source metrics |
| both sources down | inconclusive: "only 0 sources provide evidence" | caveats, critic issues |
| model rate limited briefly | retried, then lower tier; fallback counted | `argumind_llm_fallbacks_total`, run usage |
| model quota exhausted | plan/consensus/answer fall back, extraction yields nothing → inconclusive | caveats, stage statuses `failed`, llm outcome `budget` |
| model circuit open | same as above, but instantly | llm outcome `circuit_open`, circuit state |
| invented citation | removed, listed in verification | verification, caveats |
| unexpected exception | run `failed` with the error; completed stages kept | run error, `argumind_runs_total{status="failed"}` |
| database unavailable | `/ready` 503; run creation fails with 503 and `Retry-After` | readiness, error envelope |

## What is deliberately not built

- Shared breaker and limiter state across replicas (one backend process by
  design; Redis is the documented path).
- Persistent job queue: a restart loses queued runs (they remain `queued` in
  the database and are listed as such).
- Sub-stage checkpoints: a run that fails mid-extraction keeps its stages but
  not the partial claims after the last persisted stage.
