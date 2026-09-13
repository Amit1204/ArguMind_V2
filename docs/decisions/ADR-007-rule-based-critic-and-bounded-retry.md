# ADR-007: A rule-based critic, a bounded retry, and asynchronous run execution

**Status:** Accepted (Phase 4)

## Problem

Three decisions shape how a run ends. Who decides whether the evidence is
good enough to answer? How many times may the pipeline go back for more? And
should the HTTP request block for the one to three minutes a run takes on the
free tier?

The first ArguMind used a model call as the critic (non-deterministic, costs
budget, its "retry" appended the words "survey review overview" to every
query), allowed two retries, and blocked the request while the pipeline ran.

## Decisions

1. **The critic is rule-based.** It counts claims that take a position and the
   distinct sources providing them, reads the consensus strength and how many
   conflicts stayed unresolved, and returns `pass`, `retry` or `inconclusive`
   with the issues that led there. Thresholds are settings
   (`MIN_EVIDENCE_CLAIMS`, `MIN_EVIDENCE_SOURCES`). No model call.

2. **At most `MAX_ITERATIONS` retries (default 1), and the retry is a
   broadened search:** the original question with doubled result limits,
   instead of narrower sub-questions with extra words appended. A retry is
   refused once the time budget is exhausted.

3. **Inconclusive is a first-class outcome.** When the evidence is thin after
   the retry, or no source takes a position, the run ends as `inconclusive`
   with the critic's reasons and no answer model call. The pipeline never
   writes a confident-sounding answer over missing evidence.

4. **Runs execute asynchronously on a bounded worker pool.** `POST /runs`
   creates the row and returns `202`; stages are persisted as they finish so
   `GET /runs/{id}` shows progress. `?wait=true` blocks with a bound for
   clients that prefer it (CLI, benchmark).

## Reasoning

- A deterministic gate is explainable ("only 1 source provides evidence, need
  2") and reproducible, which the Phase 7 benchmark needs. The model is used
  where judgement is required (extraction, arbitration, consensus, answer),
  not where counting suffices.
- Appending terms to an arXiv `AND` query narrows it; the old retry made
  results worse. Doubling limits on the original question is the opposite.
- Free-tier runs are slow. Holding a connection open for minutes wastes a
  worker and breaks through proxies; persisting stages makes the wait useful.

## Consequences

- The critic cannot judge explanation quality; it judges sufficiency and
  coherence. That is honest about what rules can do.
- One retry doubles the source and extraction calls of a thin run, bounded by
  the time budget.
- Two backend replicas would each have their own worker pool; a shared job
  queue is the documented scaling path, not built.
