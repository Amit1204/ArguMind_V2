# ArguMind

Research questions answered with evidence. ArguMind gathers papers and
reference articles, extracts claims with their stance, builds a citation graph
in which agreement and disagreement are explicit, resolves conflicts without
hiding the losing view, and produces an answer whose every citation is
verified against the evidence it collected. When the evidence does not support
a conclusion, it says so.

Built as a forward-deployed-engineering portfolio project, a ground-up rebuild
of an earlier prototype ([why](docs/decisions/ADR-001-rebuild-and-scope.md)),
to the same standard as the
[AI Business Analyst & Operations Copilot](https://github.com/Amit1204/AI-Analyst-Operations-Copilot):
reproducible, tested, observable, honestly documented.

> **Status: Phase 6 (Observability and reliability) complete.** The system
> answers questions end to end, in the browser and through the API, and it is
> now **observable and self-protecting**: metrics for runs, stages, model
> calls, source calls and circuit breakers on `/metrics`, an operations
> summary on the Overview page, a request id in every log line and error
> response, an optional Prometheus and Grafana overlay, **circuit breakers**
> per source and for the model, a **per-client rate limit** and a queue cap
> with `Retry-After`, and documented **failure drills**. The **web UI** has an Ask page
> that shows each pipeline stage as it completes, a run view with the cited
> answer, the evidence (sources, claims by stance, conflicts with minority
> views, topic clusters), an interactive **citation graph** and the stage
> timeline, plus a run history. Underneath, a **LangGraph pipeline**
> plans sub-questions, gathers from **arXiv and Wikipedia** (typed clients,
> retries, throttle, database-backed cache), extracts **claims with stance**
> and deterministic ids through a **provider-agnostic model layer** (Gemini
> free tier or a deterministic mock), builds a **citation graph** in which
> disagreement is explicit and **conflicts are detected by construction**,
> resolves them with deterministic scoring plus model arbitration only when
> close, clusters topics, forms a consensus, passes a **rule-based critic**
> that may trigger one broadened retry or declare the evidence
> **inconclusive**, writes a cited answer and **verifies every citation**
> against the sources retrieved in that run. Every run is **persisted** stage
> by stage in PostgreSQL and exposed through a runs API. Foundation from the
> earlier phases: Docker Compose, forward-only migrations, health and
> readiness, request ids, JSON logs, Prometheus metrics, a React shell and CI.
> Each section below states what is **implemented** versus **planned**; the
> phase plan is in [`docs/specification.md`](docs/specification.md).

---

## Contents

1. [What it will do](#1-what-it-will-do)
2. [Architecture](#2-architecture)
3. [Technology stack](#3-technology-stack)
4. [Quick start](#4-quick-start)
5. [Configuration](#5-configuration)
6. [Testing](#6-testing)
7. [Project structure](#7-project-structure)
8. [Roadmap](#8-roadmap)
9. [Limitations](#9-limitations)
10. [License](#10-license)

---

## 1. What it will do

Ask a question such as *"Do large language models truly understand
language?"* or *"Does intermittent fasting improve longevity in humans?"* and
receive:

- an answer with numbered citations that resolve to the sources actually
  retrieved in that run;
- the claims behind it, each marked as supporting, refuting or neutral;
- the conflicts the evidence contains, how each was resolved, and the
  minority view that lost;
- a citation graph you can inspect;
- or an explicit *inconclusive* verdict with the reasons.

**Implemented today (Phases 1-5):** all of the above, in the web UI at
http://localhost:3100 (Ask, Runs, Overview; see
[`docs/frontend.md`](docs/frontend.md)) and through the API. From the
command line, ask a question and wait for the answer:

```bash
curl -s -X POST "localhost:8100/api/v1/runs?wait=true" -H 'Content-Type: application/json' \
  -d '{"question":"Do large language models understand language?"}' | python3 -m json.tool
```

The response holds the answer with `[source_id]` citations, the sub-questions,
every source and claim with its stance, the conflicts and how they were
resolved (with the minority view), the topic clusters, the consensus, the
critic's verdict, the citation verification, the caveats, and every stage
with its timing and model usage. `GET /api/v1/runs/{id}/graph` returns the
citation graph. Details: [`docs/pipeline.md`](docs/pipeline.md),
[`docs/evidence.md`](docs/evidence.md), [`docs/graph.md`](docs/graph.md).

Operations: [`docs/observability.md`](docs/observability.md) and
[`docs/reliability.md`](docs/reliability.md). Optional dashboards:

```bash
docker compose -f docker-compose.yml -f docker-compose.observability.yml up -d
# Grafana http://localhost:3101 (admin/admin), Prometheus http://localhost:9091
```

**Planned:** the benchmark (Phase 7), final review and publication (Phase 8).

## 2. Architecture

See [`docs/architecture.md`](docs/architecture.md) for the diagrams and the
implemented-versus-designed table. In one paragraph: a React SPA served by
nginx calls a FastAPI backend on the same origin; the backend runs a LangGraph
pipeline whose nodes call a provider-agnostic model layer (Gemini free tier,
or a deterministic mock) and key-less source APIs (arXiv, Wikipedia); every
run and everything it produced is persisted in PostgreSQL.

Design records: [`docs/decisions/`](docs/decisions/).

## 3. Technology stack

| Layer | Choice | Version |
|-------|--------|---------|
| API | FastAPI / Starlette / uvicorn | 0.141 / 1.6 / 0.32 |
| Database | PostgreSQL, SQLAlchemy, psycopg | 16, 2.0, 3.2 |
| Model | Google Gemini via `google-genai`, or a deterministic mock | 2.23, free tier |
| Sources | arXiv API, Wikipedia API via httpx + defusedxml | 0.28, 0.7 |
| Graph | NetworkX | 3.6 |
| Orchestration | LangGraph (state machine only, no LangChain chains) | 1.2 |
| Frontend | React, Vite, TypeScript, react-router | 18, 8, 5.6, 7 |
| Metrics | prometheus-client | 0.21 |
| Tooling | pytest, ruff, GitHub Actions | 9.0, 0.7.4 |

## 4. Quick start

Requirements on the host: Git, Docker (with Compose v2) and a browser. No
Python or Node installation is needed.

```bash
git clone <this repository> ArguMind && cd ArguMind
cp .env.example .env            # optional; every value has a working default
docker compose up --build       # first run: 1-2 minutes
```

Then:

| URL | What |
|-----|------|
| http://localhost:3100 | Web UI (Overview, Ask, Runs) |
| http://localhost:8100/docs | API documentation (Swagger UI) |
| http://localhost:8100/ready | Readiness: database, schema, model provider |
| http://localhost:8100/metrics | Prometheus metrics |

The stack runs without a model key (`LLM_PROVIDER=mock`, or leave the key
blank: readiness then reports the provider as an optional, not configured
check). For real extraction, create a free Gemini key at
https://aistudio.google.com and set `LLM_API_KEY` in `.env`. A quick
end-to-end check of retrieval plus extraction:

```bash
docker compose run --rm backend python -m app.evidence.cli "Do LLMs understand language?" --arxiv 2 --wikipedia 1
```

Stop with `docker compose down` (keeps the data volume) or
`docker compose down -v` (wipes it; migrations re-run on next start).

Ports are 3100 / 8100 / 5433 so this stack can run next to the copilot's
3000 / 8000 / 5432. Change them in `.env`.

## 5. Configuration

Everything is configured by environment variables; `.env.example` lists every
one with its default and a comment. Secrets (`LLM_API_KEY`) are read from the
environment only, never logged, and `.env` is git-ignored.

## 6. Testing

Everything runs inside Docker:

```bash
make test          # backend unit tests + migration job tests
make lint          # ruff
make format        # ruff format (rewrites files)
```

Equivalent commands without `make`:

```bash
docker compose run --rm --no-deps backend python -m pytest -q
docker compose run --rm --no-deps db-migrate python -m pytest -q
```

Unit tests (130 backend, 5 migration) never need a database, network or API
key: source clients are tested against recorded arXiv and Wikipedia
responses through an httpx mock transport, the Gemini provider against a fake
SDK client, and the whole pipeline end to end with the mock model, canned
sources and an in-memory run store. An opt-in integration test runs the
pipeline against PostgreSQL:

```bash
docker compose run --rm -e RUN_INTEGRATION_TESTS=1 backend python -m pytest -q tests/test_integration_db.py
```

CI
([`.github/workflows/test.yml`](.github/workflows/test.yml)) additionally
starts the whole stack and checks readiness, the migrated schema version,
metrics, request-id headers, the frontend proxy, and that re-running the
migration job is a no-op.

## 7. Project structure

```
ArguMind/
├── backend/            FastAPI service (app/, tests/, Dockerfile)
├── database/           migrate.py, migrations/, tests/, Dockerfile
├── frontend/           Vite + React SPA, nginx.conf, Dockerfile
├── docs/               specification, architecture, decisions/
├── .github/workflows/  test.yml, build.yml
├── docker-compose.yml  canonical local environment
├── Makefile            convenience targets (all run inside Docker)
└── .env.example        every configurable value with a safe default
```

## 8. Roadmap

| Phase | Scope | Status |
|-------|-------|--------|
| 1 | Foundation: Compose, migrations, API skeleton, observability basics, UI shell, CI | **Complete** |
| 2 | Source clients (arXiv, Wikipedia), model layer (Gemini + mock), claim extraction with stance | **Complete** |
| 3 | Citation graph with real `refutes` edges, conflict detection and resolution | **Complete** |
| 4 | LangGraph pipeline, critic loop, verified answer, run persistence and API | **Complete** |
| 5 | Ask, Evidence, Graph and Runs pages | **Complete** |
| 6 | Pipeline metrics, retries, circuit breakers, run deadline, rate limits | **Complete** |
| 7 | Benchmark with deterministic graders and committed reports | Planned |
| 8 | Security review, final docs, demo script, publication | Planned |

## 9. Limitations

- Runs are slow on the free tier: a question costs roughly 8-20 model calls
  (one per source for extraction plus plan, consensus, answer and any
  arbitration) and 30-120 seconds, mostly waiting on rate-limited APIs. The
  Ask page shows each stage as it completes.
- The frontend has no unit tests; it is type-checked in CI and verified by
  walkthrough. Progress granularity is one pipeline stage.
- Circuit breaker, rate limit and operations state live in the single
  backend process and reset on restart; several replicas would need shared
  state (Redis), which is documented, not built. There is no distributed
  tracing: request ids and persisted stages cover the one-process design.
- Conflicts are detected per sub-question, the proposition every claim's
  stance was judged against; topic clusters flag pairwise disagreement within
  a topic but the resolver does not yet act on cluster-level conflicts.
- The critic judges sufficiency and coherence with rules; it cannot judge
  explanation quality. Two backend replicas would each have their own worker
  pool: a shared job queue is documented, not built.
- Failed runs keep the stages that completed but not the evidence gathered
  after the last persisted stage.
- The resolver's authority priors, recency curve and evidence-type weights
  are explicit constants chosen by judgement, not fitted; the Phase 7
  benchmark is where they get challenged.
- The arXiv API allows roughly one request every three seconds per client
  and answers bursts with HTTP 429 for a while. The client throttles itself
  and backs off, and a throttled arXiv is reported as a per-source error
  while Wikipedia results still return. Wikipedia lead sections are
  descriptive, so their claims are mostly `neutral`; the discriminating
  evidence comes from papers.
- Cloud deployment is out of scope by decision; Docker Compose is the target.
- Free-tier model quotas bound how many runs per day are possible; the local
  daily request budget makes the limit visible rather than surprising.

## 10. License

MIT. See [`LICENSE`](LICENSE).
