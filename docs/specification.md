# ArguMind v2 — Master Specification

ArguMind answers research questions with evidence: it gathers papers and
reference articles, extracts claims with their stance, builds a citation graph
in which agreement and disagreement are explicit, resolves conflicts without
hiding the losing view, and produces an answer whose every citation is
verified against the evidence it collected.

This document is the contract for the rebuild. It fixes scope, architecture,
quality rules and the phase plan. Work proceeds strictly phase by phase; each
phase ends with a Phase Completion Report and a commit.

> **Status (2026-09-17):** all eight phases delivered and published at
> https://github.com/Amit1204/ArguMind_V2. The baseline benchmark against
> Gemini and live arXiv is recorded in `docs/evaluation.md` §4: 25/30
> (83.3 %), failures classified, follow-ups listed.

## 1. Why a rebuild

The first ArguMind (github.com/Amit1204/ArguMind) proved the idea but not the
engineering. The assessment on 2026-09-13 found that the headline feature
never executed (no `refutes` edge was ever created, so conflict resolution had
nothing to resolve), that there were no tests, no CI, no evaluation, colliding
claim ids, non-deterministic ids, unpinned dependencies and no reproducible
environment. The rebuild keeps the ideas and replaces the implementation.

## 2. Goals and non-goals

Goals

- A research-evidence pipeline whose intermediate artefacts (claims, stances,
  graph, conflicts, consensus, critic verdict) are real, inspectable and tested.
- Grounded answers: every citation in the answer refers to a retrieved source;
  unverifiable statements are flagged, never silently kept.
- Honest uncertainty: an explicit "inconclusive" outcome with reasons.
- Reproducibility: deterministic ids, pinned dependencies, persisted runs, a
  mock model for tests and CI.
- Operability: request ids, structured logs, metrics, timeouts, breakers.
- Proof: unit and integration tests, CI, a benchmark with committed reports.

Non-goals (by decision, 2026-09-13)

- No Hugging Face Spaces, no Streamlit. The UI is a React SPA served by nginx.
- No cloud deployment. Docker Compose on a single host is the deployment story.
- No model training, no GPU.
- No paid APIs: arXiv, Wikipedia and Gemini free tier only (Semantic Scholar
  optional, key-less).

## 3. Architecture

```
Browser ── React SPA (nginx, :3100) ──/api──▶ FastAPI backend (:8100)
                                                 │
                    ┌────────────────────────────┼──────────────────────────┐
                    ▼                            ▼                          ▼
             LangGraph pipeline            PostgreSQL 16              Gemini (or mock)
   plan → decompose → gather →        runs, stages, sources,       provider-agnostic
   extract claims (+stance) →         claims, graph, answers,      layer: tiers, retry,
   graph → resolve conflicts →        benchmark results            budget, cost
   cluster → consensus → critic →
   answer (citation-verified)
                    │
                    ▼
        arXiv API · Wikipedia API · (Semantic Scholar)   — cached per run and per query
```

Backend layout (Python 3.12):

```
backend/app/
  main.py            create_app: middleware, routers, exception handlers
  config.py          Settings from environment only, safe local defaults
  db.py              SQLAlchemy engine/session
  api/               health, runs, graph, system, metrics
  llm/               provider protocol, gemini, mock, factory, budget, cost
  sources/           arxiv, wikipedia, (semantic_scholar): typed clients, caching
  pipeline/          LangGraph state, nodes, prompts, graph builder
  evidence/          claim schema, deterministic ids, stance, dedup
  graph/             citation graph model (NetworkX), conflicts, serialisation
  reasoning/         conflict resolver, clustering, consensus, critic, verifier
  observability/     request context, JSON logging, Prometheus metrics
  reliability/       retry, circuit breaker, timeouts, rate limiter
  evaluation/        benchmark dataset, graders, runner, reports
backend/tests/
database/            schema.sql, migrations/, init scripts
frontend/            Vite + React + TypeScript SPA, nginx.conf, Dockerfile
docs/                architecture, pipeline, graph, evaluation, ADRs, demo
evaluation/reports/  committed benchmark reports
```

Key design rules

- Configuration by environment only; every value has a working local default;
  secrets never in code or git.
- Deterministic identifiers: `source_id` is the arXiv id or a stable hash of
  the URL; `claim_id` is `{source_id}#{n}`. No Python `hash()` anywhere.
- The model returns structured JSON validated by Pydantic; any parse failure is
  a recorded stage error, never a silent fallback that pretends to be a result.
- Stance is a first-class field on every claim (`supports`, `refutes`,
  `neutral`) relative to the sub-question, so the graph really contains
  `refutes` edges and conflicts are detectable by construction.
- Every run is persisted with its stages, sources, claims, graph, answer,
  token usage, estimated cost and latency, and is retrievable by id.
- The answer stage may only cite `source_id`s present in the run; a verifier
  removes or flags anything else and records what it did.
- One pipeline instance per process; per-stage timeouts; a whole-run budget;
  provider fallback and a circuit breaker instead of multi-minute sleeps.

## 4. Stack and pinned versions

| Layer | Choice |
|-------|--------|
| Language | Python 3.12, TypeScript 5.6 |
| API | FastAPI 0.141 / Starlette 1.6 / uvicorn 0.32 |
| Orchestration | LangGraph (pin at install; no LangChain chains or LangChain LLM wrappers) |
| Model | google-genai 2.23 (Gemini free tier), tiers fast = gemini-3.5-flash-lite, standard = gemini-3-flash-preview; `mock` provider for tests |
| Database | PostgreSQL 16, SQLAlchemy 2.0, psycopg 3 |
| Graph | NetworkX 3 |
| Metrics | prometheus-client |
| Frontend | React 18, Vite 8, react-router 7, graph rendering with d3-force or a hand-rolled SVG layout |
| Tooling | pytest 9, httpx, ruff 0.7.4; GitHub Actions |
| Ports (host) | frontend 3100, backend 8100, postgres 127.0.0.1:5433 (chosen so the copilot stack can run at the same time) |

## 5. Phase plan

Each phase: implement, test, verify inside Docker, update docs, commit, report.

**Phase 1 — Foundation.** Compose environment (postgres, db-migrate, backend,
frontend placeholder), schema and forward-only migrations, FastAPI skeleton
with `/health`, `/ready`, `/api/v1/system/status`, settings, request-id
middleware and JSON logging, ruff + pytest, GitHub Actions (lint, unit,
frontend build, compose smoke), README skeleton, architecture doc, ADR-001
(rebuild and scope), ADR-002 (Postgres for runs), ADR-003 (LangGraph).
Acceptance: `docker compose up --build` gives healthy services; CI green.

**Phase 2 — Sources and model layer.** Typed arXiv and Wikipedia clients
(stdlib or httpx, retries, timeouts, per-query cache table), LLM provider
protocol with Gemini and mock implementations, structured-output helper,
daily request budget and cost accounting, claim extraction with stance into
Pydantic models, deterministic ids. Acceptance: unit tests with recorded
fixtures for both sources; extraction tested against the mock; ADR-004
(provider layer), ADR-005 (deterministic ids and stance).

**Phase 3 — Citation graph and conflict resolution.** Graph model with
`supports`, `refutes`, `extends`, `supersedes` edges, conflict detection,
resolver (recency, source authority, evidence count, model arbitration with
recorded reasoning), minority reports, serialisation. Acceptance: the graph
produced from fixture claims contains `refutes` edges and the resolver
produces a non-empty report; property tests on the graph; ADR-006.

**Phase 4 — Full pipeline and persistence.** LangGraph state machine: plan,
decompose, gather, extract, graph, resolve, cluster (TF-IDF, deterministic),
consensus, critic with bounded retry, answer, verify. Runs persisted; `POST
/api/v1/runs`, `GET /api/v1/runs/{id}`, `GET /api/v1/runs/{id}/graph`, run
list; per-stage timings, tokens, cost. Acceptance: end-to-end run with the mock
model in CI; integration test against Postgres; docs/pipeline.md; ADR-007
(critic loop bounds).

**Phase 5 — Frontend.** Pages: Ask (question, progress, answer with clickable
citations), Evidence (sources, claims with stance, conflicts and minority
reports), Graph (interactive citation graph with edge types), Runs (history),
Overview (readiness and operations). Same-origin `/api` via nginx.
Acceptance: `npm run build` in CI; manual walkthrough recorded in docs/demo.md.

**Phase 6 — Observability and reliability.** Prometheus metrics (runs, stage
latency, model calls, tokens, source calls, errors), `/metrics`, operations
summary endpoint and card, retry with jitter, circuit breakers for each source
and the model, run deadline, rate limit per client, JSON error envelope with
request id, optional Prometheus/Grafana overlay. Acceptance: failure drills
documented (source down, model quota exhausted) with observed behaviour.

**Phase 7 — Evaluation.** Benchmark of about 30 questions in categories:
settled questions, contested questions, questions with no evidence, questions
that must be inconclusive, prompt-injection attempts, citation-fidelity
checks. Deterministic graders: outcome, citation validity, stance coverage,
conflict surfaced, no fabricated source, latency and cost. Runner with
resume; Markdown and JSON reports committed; baseline documented with failures
classified. Acceptance: `make evaluate` produces a report; ADR-008.

**Phase 8 — Final polish.** Security review (input limits, SSRF-safe source
clients, dependency audit), docs pass (README, architecture status table,
limitations, interview notes, demo script), dependency pins verified, final
Compose validation from an empty volume, publish to the new GitHub repository.

## 6. Quality rules

- Nothing is described as implemented unless it runs in Docker and has a test.
- Tests do not need a network or an API key; live-provider tests are opt-in.
- Every commit passes ruff and the unit suite.
- `.env` is never committed; `.env.example` lists every variable.
- Docs distinguish IMPLEMENTED from DESIGNED, and list limitations honestly.

## 7. Phase Completion Report format

```
Phase N — <name>
Status: complete | partial (what is missing and why)
Implemented: bullet list
Tests: counts and how they were run
Verification: commands run and observed results
Known issues: honest list
Docs updated: files
Next phase: name and first steps
```
