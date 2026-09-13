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

> **Status: Phase 1 (Foundation) complete.** Docker Compose environment,
> PostgreSQL schema with forward-only migrations, FastAPI service with health,
> readiness, system status and Prometheus metrics, request ids and JSON logs,
> a React shell with an Overview page, and CI (lint, unit tests, frontend
> build, full-stack smoke test, image builds). The pipeline itself is not
> built yet; each section below states what is **implemented** versus
> **planned**. The phase plan is in [`docs/specification.md`](docs/specification.md).

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

**Implemented today (Phase 1):** the environment, database, API skeleton and
UI shell. **Planned:** everything above, phase by phase (see the roadmap).

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
| Model | Google Gemini via `google-genai` (Phase 2) | free tier |
| Orchestration | LangGraph (Phase 4) | pinned at install |
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
| http://localhost:3100 | Web UI (Overview page) |
| http://localhost:8100/docs | API documentation (Swagger UI) |
| http://localhost:8100/ready | Readiness: database, schema, model provider |
| http://localhost:8100/metrics | Prometheus metrics |

The stack runs without a model key (`LLM_PROVIDER=mock`, or leave the key
blank: readiness then reports the provider as an optional, not configured
check). A Gemini key is only needed once the pipeline exists (Phase 2+):
create one at https://aistudio.google.com and set `LLM_API_KEY` in `.env`.

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

Unit tests never need a database, network or API key. CI
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
| 2 | Source clients (arXiv, Wikipedia), model layer (Gemini + mock), claim extraction with stance | Planned |
| 3 | Citation graph with real `refutes` edges, conflict detection and resolution | Planned |
| 4 | LangGraph pipeline, critic loop, verified answer, run persistence and API | Planned |
| 5 | Ask, Evidence, Graph and Runs pages | Planned |
| 6 | Pipeline metrics, retries, circuit breakers, run deadline, rate limits | Planned |
| 7 | Benchmark with deterministic graders and committed reports | Planned |
| 8 | Security review, final docs, demo script, publication | Planned |

## 9. Limitations

- Nothing answers questions yet: Phase 1 is infrastructure only.
- Cloud deployment is out of scope by decision; Docker Compose is the target.
- Free-tier model quotas will bound how many runs per day are possible; the
  request budget and cost accounting arrive in Phase 2 so the limit is
  visible rather than surprising.

## 10. License

MIT. See [`LICENSE`](LICENSE).
