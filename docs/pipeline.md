# The pipeline (Phase 4)

One run answers one question. The LangGraph state machine below is
**implemented**; every stage is recorded and persisted as it completes, so a
run can be followed live and audited afterwards.

```
plan ─▶ gather ─▶ extract ─▶ build_graph ─▶ resolve_conflicts ─▶ cluster ─▶ consensus ─▶ critic
                                                                                          │
                          ┌─────────── retry (broadened gather, at most MAX_ITERATIONS) ◀─┤
                          ▼                                                               │ pass / inconclusive
                        gather ...                                                        ▼
                                                                                        answer ─▶ verify ─▶ END
```

## Stages

| Stage | Model calls | What it does | Recorded detail |
|-------|-------------|--------------|-----------------|
| `plan` | 1 (fast) | Splits the question into 1-3 sub-questions; the first restates the question as a testable proposition. Falls back to the original question if the model fails. | sub-questions, complexity |
| `gather` | 0 | Searches arXiv and Wikipedia per sub-question through the cached source service. A failing source becomes a caveat, never a run failure. On retry: the original question with doubled limits. | counts per kind, cache hits, errors |
| `extract` | 1 per (source, sub-question) (fast) | Claim extraction with stance (ADR-005). Extraction failures are counted and skipped. | claims by stance, failures |
| `build_graph` | 0 | Citation graph with `supports`/`refutes` edges (ADR-006). | graph summary |
| `resolve_conflicts` | 0-1 per close conflict (standard) | Heuristic scoring, model arbitration when close, minority reports, `supersedes` edges. | resolutions |
| `cluster` | 0 | TF-IDF topic clusters; `extends` edges between related claims from different sources; contested clusters flagged. Edges persisted. | clusters, contested labels |
| `consensus` | 1 (standard) | Overall assessment, strength, agreements, disagreements, gaps, confidence. Deterministic tally when no source takes a position or the model fails. | strength, confidence, method |
| `critic` | 0 | Rule-based gate (ADR-007): enough claims with a stance from enough sources? Consensus formed? Decides `pass`, `retry` or `inconclusive`. | verdict, issues |
| `answer` | 1 (standard) | Cited Markdown answer; a template for the inconclusive case (no model call); a template assembled from consensus and claims if the model fails. | method |
| `verify` | 0 | Keeps only citations to sources retrieved in this run, rewrites claim-id citations to their source, removes the rest, flags uncited answers and halves their confidence. | citations found/valid/removed, caveats |

Every stage record carries status (`ok`, `failed`, `skipped`, `timeout`),
timing, the model calls and tokens it consumed, its detail and any error.

## Time budget

`RUN_TIMEOUT_SECONDS` (default 180) is checked between units of work.
Gathering may use at most `GATHER_BUDGET_FRACTION` of it (default 0.5) so
reasoning always has time left; once the whole budget is exceeded, remaining
sources are not analysed, consensus is tallied without the model, the critic
cannot request a retry, and the answer falls back to the template. Each
shortcut leaves a caveat.

A source that fails for one sub-question is not tried again for the later
sub-questions of the same run. This was added after a live run in which
arXiv, throttling this network, absorbed the entire budget across three
sub-questions and left nothing for extraction; the run was correctly
reported as inconclusive with caveats, but it should have analysed the
Wikipedia sources it had. Cross-run protection (a circuit breaker per
source) is Phase 6.

## Persistence

| Table | Written by |
|-------|------------|
| `runs` | created as `queued`, `running` when execution starts, finished with status, answer, confidence, tokens, cost, latency, error |
| `run_stages` | every stage, as it completes (progress is visible while the run is executing) |
| `sources`, `claims` | `gather` and `extract`, unique per run |
| `citation_edges` | `cluster` (replaces the run's edges, including `supersedes`) |
| `source_cache` | the source service |

Resolutions, clusters, consensus, critic verdict, verification result and
caveats live in the corresponding stage's `detail` JSON, so no extra tables
were needed. The API view (`RunDetail`) assembles them.

## API

| Method | Path | Notes |
|--------|------|-------|
| `POST` | `/api/v1/runs` | body `{question, client_id?}`; returns `202` with the queued/running run, or `200` with the finished run when `?wait=true` (bounded by the time budget) |
| `GET` | `/api/v1/runs` | latest runs, paginated |
| `GET` | `/api/v1/runs/{id}` | full detail: stages, sub-questions, sources, claims, resolutions, clusters, consensus, critic, verification, caveats, usage |
| `GET` | `/api/v1/runs/{id}/graph` | nodes and edges rebuilt from the persisted evidence |

Runs execute on a bounded worker pool (`RUN_WORKERS`, default 2) so the API
stays responsive; a `503` is returned when no model provider is configured.

```bash
curl -s -X POST "localhost:8100/api/v1/runs?wait=true" -H 'Content-Type: application/json' \
  -d '{"question":"Do large language models understand language?"}' | python3 -m json.tool
```

## Failure behaviour, by design

| Failure | Behaviour |
|---------|-----------|
| A source API is down or throttled | caveat; other sources continue |
| Extraction fails for a source | source skipped, counted in the stage detail |
| Model fails in plan / consensus / answer | deterministic fallback, stage marked `failed`, caveat |
| Model returns invented citations | removed by `verify`, listed in the verification result, caveat |
| Answer has no verifiable citation | caveat, confidence halved |
| No evidence takes a position | `inconclusive` with the critic's reasons; no answer model call |
| Unexpected exception | run `failed` with the error; completed stages remain |

## Not here yet

- Metrics per stage, circuit breakers, rate limits (Phase 6). Retries and
  the time budget exist; a hard provider outage is still retried per call.
- Frontend pages for runs (Phase 5); today the API and `docs` UI are the way in.
