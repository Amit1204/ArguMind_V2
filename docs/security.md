# Security review (Phase 8)

A review of what an attacker could do to a local ArguMind deployment and
what stops them. Everything marked implemented has a test or a documented
verification; the last section lists what is deliberately not built.

## Threat model

ArguMind is a single-user research tool run with Docker Compose on one
machine. Inputs an attacker can influence: the question text, the content of
retrieved sources (arXiv abstracts, Wikipedia extracts), and network access
to the published ports. It holds one secret, the model API key.

## Controls

| Area | Control | Where |
|------|---------|-------|
| Question input | 10-500 characters after whitespace normalisation; blank or padded questions rejected with 422 before any source or model call | `RunCreate`, `test_evaluation.py` |
| Prompt injection via the question | every prompt states that the question and source text are untrusted data; the planner restates the question as a proposition; the benchmark's injection category checks that planted text never reaches sub-questions or the answer and that a planted source id is never cited | `pipeline/prompts.py`, `evidence/prompts.py`, `evaluation/benchmark/injection.yaml` |
| Prompt injection via sources | source summaries are passed as data under a labelled section; the model can only return schema-validated JSON (claims with stance), never actions | `evidence/extractor.py`, ADR-004 |
| Fabricated citations | the verifier removes any `[source_id]` not retrieved in the run, records what it removed, and halves confidence when nothing verifiable remains | `pipeline/verify.py` |
| Fabricated answers | `inconclusive` is a first-class outcome; the answer model is not called when the critic says the evidence is insufficient | `pipeline/critic.py`, ADR-007 |
| SSRF | source clients call two fixed hosts (`export.arxiv.org`, `en.wikipedia.org`); no user-supplied URL is ever fetched; source URLs are built from ids and titles, not taken from responses | `sources/arxiv.py`, `sources/wikipedia.py` |
| XML parsing | `defusedxml` for the arXiv Atom feed | `sources/arxiv.py` |
| SQL | parameterised statements only; the model never writes SQL; CHECK constraints enforce stance, edge type and confidence ranges | `pipeline/store.py`, `database/migrations/0001_core.sql` |
| Secrets | `LLM_API_KEY` is a `SecretStr`, never logged or serialised; `.env` is git-ignored; a scan of tracked files found no secrets | `config.py`, `.gitignore` |
| Error responses | JSON envelope with a request id and a generic message; stack traces stay in the server log | `main.py` |
| Abuse and cost | per-client rate limit (429), queue cap (503), daily model request budget, circuit breakers, run time budget | `api/runs.py`, `llm/budget.py`, `reliability/` |
| Browser | same-origin API via nginx; CORS restricted to the frontend origin; the answer is rendered as text nodes and citation chips, never as HTML; external links use `rel="noreferrer"` | `frontend/nginx.conf`, `AnswerText.tsx` |
| Network exposure | PostgreSQL, Prometheus and Grafana bind to 127.0.0.1 only; the backend and frontend publish one port each | `docker-compose*.yml` |
| Containers | backend and migration images run as non-root users; pinned base images and dependencies | Dockerfiles |
| Dependencies | `pip-audit` on both requirement files and `npm audit` (prod and dev): no known vulnerabilities on 2026-09-13 | this review |
| Labels and cardinality | metric labels are bounded sets; user input never becomes a label | `observability/metrics.py` |

## Verified on 2026-09-13

```
pip-audit -r backend/requirements.txt     No known vulnerabilities found
pip-audit -r database/requirements.txt    No known vulnerabilities found
npm audit (prod and dev)                  found 0 vulnerabilities
git ls-files | grep -iE 'key|secret|token' ... no secret-bearing files tracked
```

## Not implemented, by design, and what it would take

- **Authentication and authorisation.** Anyone who can reach port 3100 or
  8100 can start runs and read every run. This is acceptable for a
  single-user local tool and unacceptable for anything shared. Adding it
  would mirror the copilot project: JWT login, roles, per-user rate limits
  and run ownership. Do this before exposing the ports beyond localhost.
- **TLS.** Compose publishes plain HTTP on the host; a reverse proxy with
  certificates would sit in front of nginx.
- **Grafana credentials.** The overlay ships `admin/admin` for local use and
  binds to loopback; change `GRAFANA_ADMIN_PASSWORD` before sharing.
- **Secrets management.** The key lives in `.env`; a secrets manager is the
  hosted-deployment path, out of scope by decision.
- **Content moderation.** Questions are not screened for harmful topics; the
  system retrieves and summarises public research.
