# Observability (Phase 6)

Everything here is **implemented**. The aim is that any answer, caveat,
slow run or failed dependency can be traced from the UI to a request id, a
log line, a metric and a persisted stage record.

## Request ids and logs

- Every response carries `X-Request-ID` (a well-formed caller-supplied id is
  honoured). Every error response body includes `request_id`.
- Logs are JSON, one object per line, with `timestamp`, `level`, `logger`,
  `message`, `service`, `request_id` and structured extras (`event`, `route`,
  `status`, `duration_ms`, `run_id`, `stage`). `LOG_FORMAT=text` switches to
  plain lines for local reading.
- The run id appears in stage and run log lines, so `docker compose logs
  backend | grep <run_id>` shows one run's story.

## Metrics (`GET /metrics`, Prometheus format)

| Metric | Labels | Meaning |
|--------|--------|---------|
| `argumind_http_requests_total` | method, route, status | requests by route template |
| `argumind_http_request_duration_seconds` | route | request latency histogram |
| `argumind_runs_total` | status | finished runs (answered, inconclusive, failed) |
| `argumind_run_duration_seconds` | status | run wall-clock histogram |
| `argumind_runs_active` | — | runs executing now |
| `argumind_stages_total` | stage, status | stage outcomes (ok, failed, skipped, timeout) |
| `argumind_stage_duration_seconds` | stage | stage latency histogram |
| `argumind_llm_calls_total` | purpose, outcome | model calls: ok, rate_limited, unavailable, timeout, format, budget, circuit_open, error |
| `argumind_llm_call_duration_seconds` | model | successful call latency |
| `argumind_llm_tokens_total` | model, direction | input and output (incl. thinking) tokens |
| `argumind_llm_fallbacks_total` | — | calls served by a lower tier |
| `argumind_source_calls_total` | kind, outcome | source lookups: ok, cached, error, circuit_open |
| `argumind_source_call_duration_seconds` | kind | uncached lookup latency |
| `argumind_circuit_state` | name | 0 closed, 1 half-open, 2 open |
| `argumind_rate_limited_total` | — | requests refused with 429 |

Labels are bounded sets; user input never becomes a label.

## Operations summary (`GET /api/v1/system/operations`)

The human-readable roll-up since the backend started, shown on the Overview
page's Operations card: runs by status and active count, p50/p95 run latency,
per-stage counts and p50, model calls by outcome and purpose, tokens,
fallbacks, estimated cost, source calls by kind and outcome with p50, circuit
states with seconds to the next probe, rate-limit hits, and the configured
limits. It is in-process (last 500 samples per series) and needs no extra
infrastructure.

## Optional overlay: Prometheus and Grafana

```bash
docker compose -f docker-compose.yml -f docker-compose.observability.yml up -d
```

| Service | URL | Notes |
|---------|-----|-------|
| Prometheus | http://localhost:9091 | scrapes `backend:8000/metrics` every 15 s, 7-day retention |
| Grafana | http://localhost:3101 | admin / admin; datasource and the "ArguMind" dashboard are provisioned |

The dashboard has runs finished and active, run p95, rate limits, runs by
status, stage p50 durations, model calls by outcome, source calls by kind and
outcome, circuit-breaker state timeline, and tokens by direction.

## Failure drills

Each drill was run against the local stack; the observed behaviour is what
the tests also assert.

**Source throttled or down (arXiv returning 429 or timing out).**
The gather stage records the failure as a caveat and continues with the
other source; after three consecutive failures across runs the
`source:arxiv` circuit opens for 120 s, and the next runs' gather stages
report *circuit 'source:arxiv' is open; retry in Ns* in a few milliseconds
instead of waiting up to 90 s. `argumind_source_calls_total{kind="arxiv",
outcome="circuit_open"}` counts the refusals; `argumind_circuit_state
{name="source:arxiv"}` reads 2. After 120 s one probe is allowed; success
closes the circuit.

Observed on 2026-09-13 with arXiv throttling this network (mock model):

| Run | arXiv outcome | gather duration | circuit after the run |
|-----|---------------|-----------------|-----------------------|
| 1 | HTTP 429 after 3 attempts | 27.4 s | closed, 1 failure |
| 2 | timed out after 3 attempts | 58.4 s | closed, 2 failures |
| 3 | timed out after 3 attempts | 95.6 s | **open**, retry in 120 s |
| 4 | *circuit 'source:arxiv' is open; retry in 120s* | **0.77 s** | open |

Prometheus showed `argumind_circuit_state{name="source:arxiv"} 2` and one
`circuit_open` source call; Wikipedia kept answering throughout.

**Model quota exhausted (daily budget or provider 429s).**
With the local budget exhausted, every model call raises immediately with
outcome `budget`: planning falls back to the original question, extraction
yields no claims, consensus is tallied, and the run ends `inconclusive` with
caveats naming each fallback. With provider 429s, three consecutive failures
open `llm:gemini`; further calls fail fast as `circuit_open` and the same
fallbacks apply. In both cases no run fails with a 500 and no answer is
fabricated.

**Client sends runs too fast.** The sixth run within a minute from the same
`client_id` gets `429` with `Retry-After` and a JSON body carrying the
request id; `argumind_rate_limited_total` increments and the Operations card
shows the count. Observed: seven immediate `POST /runs` from one client gave
`202, 202, 202, 202, 202, 429, 429`, the refusals carrying `Retry-After: 59`
and `{"detail": "rate limit: at most 5 runs per minute per client",
"request_id": "..."}`; the accepted runs executed on the two workers while
the API stayed responsive.

## Not here

- Distributed tracing. Request ids and per-stage records cover the single
  backend process; the copilot project shows the OpenTelemetry pattern for a
  multi-service system, which would apply if sources became separate services.
