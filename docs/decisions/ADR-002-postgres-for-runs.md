# ADR-002: Persist every run in PostgreSQL

**Status:** Accepted (Phase 1)

## Problem

A research answer is only trustworthy if the evidence behind it can be
inspected later. The first ArguMind kept everything in process memory: a
result vanished when the page was refreshed, the `/graph/{id}` endpoint was a
placeholder, and no two runs could be compared. The rebuild also needs a place
for source-lookup caching, model-call accounting and benchmark results.

## Alternatives

1. **PostgreSQL 16** as the single datastore for runs, stages, sources,
   claims, graph edges, source cache and model-call records.
2. SQLite: simple, but a single writer, awkward in a multi-container stack and
   not credible for an enterprise story.
3. Files on disk (JSON per run): no querying, no integrity, no concurrency.
4. A graph database (Neo4j) for the citation graph plus a relational store for
   the rest: two systems for graphs of a few dozen nodes.

## Decision

Option 1. One PostgreSQL instance, a plain `postgres:16-alpine` image, a
forward-only SQL migration job (`database/migrate.py`) that runs on every
`docker compose up`, and a schema that stores each run with everything it
produced (`database/migrations/0001_core.sql`).

## Reasoning

- Constraints in the schema encode the invariants the pipeline must respect:
  stance is one of three values, edge types are one of four, confidence is in
  [0, 1], a claim must reference a source retrieved in the same run.
- NetworkX handles graph algorithms in memory per run; the database stores the
  edges. The graphs are small (tens of nodes), so a graph database would solve
  a problem that does not exist.
- One connection pool, one backup, one identity to secure; the same reasoning
  as the copilot's ADR-001, which makes the two projects easy to explain
  together.
- The migration job is the single mechanism for both fresh volumes and
  upgrades, so there is no separate "init scripts" path to keep in sync.

## Consequences

- Every phase that adds tables adds a numbered, idempotent migration and bumps
  the required version the readiness check looks for.
- The migration job must be idempotent; a test enforces that every `CREATE`
  is guarded.
- Old runs accumulate; a retention job is designed but not built (see the
  limitations section of the README once runs exist).
