# ArguMind

Research questions answered with evidence. ArguMind gathers papers and
reference articles, extracts claims with their stance, builds a citation graph
in which agreement and disagreement are explicit, resolves conflicts without
hiding the losing view, and produces an answer whose every citation is
verified against the evidence it collected. When the evidence does not support
a conclusion, it says so.

Built as a forward-deployed-engineering portfolio project: a ground-up rebuild
of an earlier prototype ([why](docs/decisions/ADR-001-rebuild-and-scope.md)),
to the same standard as the
[AI Business Analyst & Operations Copilot](https://github.com/Amit1204/AI-Analyst-Operations-Copilot):
reproducible, tested, observable, honestly documented.

> **Status: all eight phases complete, baseline recorded.** The 30-case
> benchmark against Gemini and live arXiv/Wikipedia scores **25/30 (83.3 %)**,
> with 100 % on injection resistance, validation, outcome and cost bounds;
> the five failures are classified in [`docs/evaluation.md` §4](docs/evaluation.md#4-baseline)
> (a stance-frame defect accounts for two of them). The four defects behind
> them are **fixed and unit-tested**, and a live canary on the worst failing
> case now passes; the full re-run could not be completed because of
> upstream outages (arXiv edge TLS rule, then Gemini flash-lite degradation),
> documented in [§4.1](docs/evaluation.md#41-after-the-fixes-partial-verification-re-run-not-completed).
> Everything below is **implemented** unless marked otherwise.

---

## Contents

1. [What it does](#1-what-it-does)
2. [How it works](#2-how-it-works)
3. [What changed from the first version](#3-what-changed-from-the-first-version)
4. [Technology stack](#4-technology-stack)
5. [Quick start](#5-quick-start)
6. [Configuration](#6-configuration)
7. [Testing and CI](#7-testing-and-ci)
8. [Operations](#8-operations)
9. [Evaluation](#9-evaluation)
10. [Security](#10-security)
11. [Project structure](#11-project-structure)
12. [Phases](#12-phases)
13. [Limitations](#13-limitations)
14. [License](#14-license)

---

## 1. What it does

Ask a question such as *"Do large language models understand language?"* in
the web UI at http://localhost:3100, or through the API, and receive:

- a direct answer with `[source_id]` citations that resolve to the sources
  actually retrieved in that run, rendered as links;
- the claims behind it, each marked as supporting, refuting or neutral, with
  the source, evidence type and the source's own confidence;
- the conflicts the evidence contains, how each was resolved, and the
  minority view that lost;
- an interactive citation graph;
- the caveats: which source was unavailable, what fell back, what was removed;
- or an explicit **inconclusive** verdict with the critic's reasons, instead
  of a confident answer over missing evidence.

Every run is persisted stage by stage, so progress is visible while it
executes and the whole chain of evidence can be audited afterwards.

```bash
curl -s -X POST "localhost:8100/api/v1/runs?wait=true" -H 'Content-Type: application/json' \
  -d '{"question":"Do large language models understand language?"}' | python3 -m json.tool
```

## 2. How it works

A LangGraph state machine runs ten stages per question
([`docs/pipeline.md`](docs/pipeline.md)):

```
plan ─▶ gather ─▶ extract ─▶ build_graph ─▶ resolve_conflicts ─▶ cluster ─▶ consensus ─▶ critic
                                                        retry once (broadened) ◀───────────┤
                                                                       answer ─▶ verify ◀──┘
```

| Stage | What it does | Doc |
|-------|--------------|-----|
| plan | 1-3 sub-questions; the first restates the question as a testable proposition | |
| gather | arXiv and Wikipedia per sub-question: typed clients, retries, politeness throttle, database-backed cache, per-source failure isolation | [`evidence.md`](docs/evidence.md) |
| extract | claims with stance and deterministic ids, one structured model call per source | [`evidence.md`](docs/evidence.md) |
| build graph | stance becomes `supports` / `refutes` edges, so conflicts exist by construction | [`graph.md`](docs/graph.md) |
| resolve conflicts | authority × recency × evidence-type × confidence scoring; model arbitration only when close; minority reports; `supersedes` edges | [`graph.md`](docs/graph.md) |
| cluster | TF-IDF topic clusters, `extends` edges, contested topics | |
| consensus | overall assessment, strength, agreements, disagreements, gaps | |
| critic | rule-based gate: enough claims from enough sources? pass, retry or inconclusive | |
| answer | cited Markdown; templates when inconclusive or when the model fails | |
| verify | citations not retrieved in this run are removed and listed; uncited answers flagged | |

Model access goes through a small provider layer (Gemini free tier, or a
deterministic mock for tests and key-less runs) with structured output, tier
fallback, a daily request budget, cost accounting and a circuit breaker.
Design records: [`docs/decisions/`](docs/decisions/) (ten ADRs).

## 3. What changed from the first version

The [original ArguMind](https://github.com/Amit1204/ArguMind) proved the idea
and not the engineering. A review found that its headline feature never ran
(no code created a `refutes` edge, so conflict detection had nothing to find),
claim ids collided across papers, source ids changed every process, and there
were no tests, no CI, no evaluation and no reproducible environment.

| | First version | This rebuild |
|--|---------------|--------------|
| Conflicts | never detected | by construction from stance edges; tested |
| Identifiers | model-chosen `claim_1`, Python `hash()` | deterministic: arXiv id, URL digest, `source#n` |
| Model layer | LangChain chains, silent parse failures | typed provider, schema-validated JSON, recorded errors |
| Outcome when evidence is thin | confident answer | `inconclusive` with reasons |
| Citations | trusted | verified against the run's sources; invented ones removed |
| Progress | blocking request | persisted stages, polled by the UI |
| Tests / CI | none | 157 unit tests, integration test, full-stack smoke, image builds |
| Evaluation | none | 30-case benchmark with deterministic graders |
| Operations | none | metrics, operations card, circuit breakers, rate limits, Grafana overlay |
| Environment | Streamlit on Hugging Face | Docker Compose: FastAPI, PostgreSQL, React behind nginx |

## 4. Technology stack

| Layer | Choice | Version |
|-------|--------|---------|
| API | FastAPI / Starlette / uvicorn | 0.141 / 1.6 / 0.32 |
| Orchestration | LangGraph (state machine only, no LangChain chains) | 1.2 |
| Model | Google Gemini via `google-genai`, or a deterministic mock | 2.23, free tier |
| Sources | arXiv API, Wikipedia API via httpx + defusedxml | 0.28, 0.7 |
| Database | PostgreSQL, SQLAlchemy, psycopg | 16, 2.0, 3.2 |
| Graph | NetworkX | 3.6 |
| Frontend | React, Vite, TypeScript, react-router; nginx | 18, 8, 5.6, 7 |
| Metrics | prometheus-client; optional Prometheus + Grafana overlay | 0.21 |
| Tooling | pytest, ruff, GitHub Actions | 9.0, 0.7.4 |

## 5. Quick start

Requirements on the host: Git, Docker (with Compose v2) and a browser. No
Python or Node installation is needed.

```bash
git clone https://github.com/Amit1204/ArguMind_V2.git ArguMind && cd ArguMind
cp .env.example .env            # optional; every value has a working default
docker compose up --build       # first run: 1-2 minutes
```

| URL | What |
|-----|------|
| http://localhost:3100 | Web UI: Overview, Ask, Runs |
| http://localhost:8100/docs | API documentation (Swagger UI) |
| http://localhost:8100/ready | Readiness: database, schema, model provider |
| http://localhost:8100/metrics | Prometheus metrics |

For real answers, create a free Gemini key at https://aistudio.google.com
and set `LLM_API_KEY` in `.env`. Without a key the stack still runs with
`LLM_PROVIDER=mock`: every screen works, claims are derived mechanically from
sentences and the answer is canned, which is what the tests and CI use.

Stop with `docker compose down` (keeps the data volume) or
`docker compose down -v` (wipes it; migrations re-run on next start). Ports
are 3100 / 8100 / 5433 so this stack can run next to the copilot's
3000 / 8000 / 5432; change them in `.env`.

A ten-minute scripted tour is in [`docs/demo.md`](docs/demo.md).

## 6. Configuration

Everything is configured by environment variables; `.env.example` lists every
one with its default and a comment. Secrets (`LLM_API_KEY`) are read from the
environment only, never logged, and `.env` is git-ignored.

## 7. Testing and CI

Everything runs inside Docker:

```bash
make test          # backend unit tests + migration job tests
make lint          # ruff
make format        # ruff format (rewrites files)
```

Unit tests (157 backend, 5 migration) never need a database, network or API
key: source clients are tested against recorded arXiv and Wikipedia
responses through an httpx mock transport, the Gemini provider against a fake
SDK client, and the whole pipeline end to end with the mock model, canned
sources and an in-memory run store, including the failure drills (source
down, budget exhausted, open circuit, invented citations, time budget). An
opt-in integration test runs the pipeline against PostgreSQL:

```bash
docker compose run --rm -e RUN_INTEGRATION_TESTS=1 backend python -m pytest -q tests/test_integration_db.py
```

CI ([`.github/workflows/`](.github/workflows/)) runs lint and format checks,
both unit suites, the frontend type-check and build, a full-stack smoke test
(readiness, schema version, metrics, request ids, error envelope, frontend
proxy, idempotent migrations, the integration test) and builds every image.

## 8. Operations

[`docs/observability.md`](docs/observability.md) and
[`docs/reliability.md`](docs/reliability.md). In short: request ids in every
response, log line and error body; JSON logs; Prometheus metrics for HTTP,
runs, stages, model calls, source calls and circuit breakers; an operations
summary on the Overview page; per-client rate limit and queue cap with
`Retry-After`; circuit breakers per source and for the model; a run time
budget with deterministic fallbacks. Failure drills were run live and are
recorded with their numbers (arXiv throttling: gather went from 95.6 s to
0.77 s once the circuit opened).

```bash
docker compose -f docker-compose.yml -f docker-compose.observability.yml up -d
# Grafana http://localhost:3101 (admin/admin), Prometheus http://localhost:9091
```

## 9. Evaluation

[`docs/evaluation.md`](docs/evaluation.md). A 30-case benchmark in seven
categories (settled, contested, comparative, no-evidence, speculative,
prompt-injection, validation) graded deterministically on outcome, evidence,
conflicts surfaced with a minority view, citation fidelity, answer text and
confidence, injection resistance, and cost. Reports with regressions against
the previous run are committed under `evaluation/reports/`.

```bash
make evaluate EVAL_ARGS="--tag baseline --pause 15"   # against the live stack; needs a model key
make evaluate EVAL_ARGS="--tag baseline --pause 15 --resume"   # continue after the daily quota
```

**Baseline (2026-09-14/17, Gemini free tier + live arXiv):** 25/30 passed
(83.3 %), p50 61 s, 413 model calls.

| Dimension | Rate | | Category | Passed |
|-----------|-----:|-|----------|-------:|
| Outcome | 100 % | | settled | 6/6 |
| Evidence | 86.7 % | | contested | 5/7 |
| Conflicts + minority view | 60.0 % | | comparative | 1/2 |
| Citation fidelity | 96.3 % | | no_evidence | 3/4 |
| Answer and confidence | 85.7 % | | speculative | 2/3 |
| Injection resistance | 100 % | | injection | 5/5 |
| Cost and latency | 100 % | | validation | 3/3 |

The five failures are all pipeline findings, none provider- or
source-caused: a **stance-frame defect** (the extractor judges stance against
the planner's sub-question instead of the original proposition, so contested
questions produce no `refutes` edge; 2 cases), a citation-regex gap for
comma-separated citations (1), and two confidence-calibration cases
(inconclusive run and forecast question reporting high confidence). The
free-tier quota lessons that shaped the runner are in
[`docs/evaluation.md` §3](docs/evaluation.md#3-running) and
[`docs/reliability.md`](docs/reliability.md).

**After the fixes (partial):** all four defects were fixed with unit tests
(169 backend tests). A live canary on `contested-005` now detects and
resolves the conflict it missed, and the three cases cleanly re-graded pass
(`evaluation/reports/after-fixes-partial.json`). The full 30-case re-run was
attempted on four days and stopped each time by an upstream problem: arXiv's
CDN rejecting the image's TLS 1.3 handshake (fixed with a documented TLS 1.2
cap for that client), then a Gemini flash-lite degradation. The recorded
number stays 25/30; the attempts, causes and the resume command are in
[`docs/evaluation.md` §4.1](docs/evaluation.md#41-after-the-fixes-partial-verification-re-run-not-completed).

## 10. Security

[`docs/security.md`](docs/security.md): threat model, controls (input
validation, untrusted-data prompts, citation verification, fixed-host source
clients, `defusedxml`, parameterised SQL, secrets handling, error envelope,
rate limits, loopback-only databases and dashboards, non-root containers) and
the results of `pip-audit` and `npm audit` (no known vulnerabilities on
2026-09-13). Authentication is deliberately not built: this is a single-user
local tool; do not expose the ports beyond localhost without adding it.

## 11. Project structure

```
ArguMind/
├── backend/            FastAPI service
│   └── app/            api/, pipeline/, llm/, sources/, evidence/, graph/, reasoning/,
│                       reliability/, observability/, evaluation/, services/
├── database/           migrate.py, migrations/, tests/, Dockerfile
├── frontend/           Vite + React SPA, nginx.conf, Dockerfile
├── docs/               specification, architecture, pipeline, evidence, graph, frontend,
│                       observability, reliability, evaluation, security, demo,
│                       interview questions, decisions/ (ADR-001..010)
├── evaluation/reports/ committed benchmark reports
├── observability/      Prometheus config, Grafana provisioning and dashboard
├── .github/workflows/  test.yml, build.yml
├── docker-compose.yml  canonical local environment
├── docker-compose.observability.yml   optional Prometheus + Grafana overlay
├── Makefile            convenience targets (all run inside Docker)
└── .env.example        every configurable value with a safe default
```

## 12. Phases

| Phase | Scope | Status |
|-------|-------|--------|
| 1 | Foundation: Compose, migrations, API skeleton, observability basics, UI shell, CI | Complete |
| 2 | Source clients (arXiv, Wikipedia), model layer (Gemini + mock), claim extraction with stance | Complete |
| 3 | Citation graph with real `refutes` edges, conflict detection and resolution | Complete |
| 4 | LangGraph pipeline, critic loop, verified answer, run persistence and API | Complete |
| 5 | Ask, Evidence, Graph and Runs pages | Complete |
| 6 | Pipeline metrics, circuit breakers, rate limits, operations card, Grafana overlay | Complete |
| 7 | Benchmark with deterministic graders and committed reports | Complete; baseline 25/30 recorded |
| 8 | Security review, final docs, demo script, publication | Complete |

The phase plan with acceptance criteria is in
[`docs/specification.md`](docs/specification.md); each phase ended with a
completion report and a commit.

## 13. Limitations

- **Free-tier speed and quota.** A question costs roughly 13-30 model calls
  and 30-120 seconds (baseline p50 61 s), mostly paced and rate-limited API
  calls. The local daily budget makes the quota visible; the Ask page shows
  each stage as it runs.
- **arXiv politeness.** arXiv allows about one request every three seconds
  and answers bursts with HTTP 429 that can persist for a long time. The
  client throttles itself, the circuit breaker stops repeated attempts, and
  a throttled arXiv is a caveat while Wikipedia results still return. But
  Wikipedia lead sections rarely provide two sources with a stance, so
  without arXiv most runs are honestly inconclusive.
- **Off-topic pages.** Wikipedia search sometimes returns unrelated pages;
  extraction correctly yields no claims from them, but they cost model calls.
- **Stance is judged against the sub-question** (baseline finding). When the
  planner phrases a sub-question as "what evidence challenges X?", a paper
  that challenges X is labelled `supports`, so some contested questions
  produce no `refutes` edge and no conflict. The fix is to judge stance
  against the original proposition; it is the first follow-up.
- **Free-tier daily caps.** Gemini's free tier allows 20 requests/day on the
  standard model and 500/day on the fast model, so synthesis mostly runs on
  the fast tier and a full benchmark needs two days (`--resume`).
- **Conflicts are per sub-question.** Topic clusters flag pairwise
  disagreement, but the resolver does not yet act on cluster-level conflicts.
- **Rules, not judgement.** The critic judges sufficiency and coherence, not
  explanation quality; the benchmark graders judge structure and honesty,
  not prose. The resolver's authority priors, recency curve and evidence
  weights are explicit constants chosen by judgement, not fitted.
- **Single process.** Circuit-breaker, rate-limit and operations state live
  in one backend process and reset on restart; several replicas would need
  shared state (Redis) and a job queue, both documented, not built. No
  distributed tracing.
- **No authentication, no TLS.** A single-user local tool by design.
- **Partial failures.** A failed run keeps its completed stages but not the
  evidence gathered after the last persisted stage. Progress granularity is
  one stage.
- **Frontend tests.** None; the frontend is type-checked in CI and verified
  by walkthrough.
- **Cloud deployment** is out of scope by decision; Docker Compose on one
  host is the target.

## 14. License

MIT. See [`LICENSE`](LICENSE).
