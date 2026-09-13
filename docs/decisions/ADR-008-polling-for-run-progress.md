# ADR-008: Poll persisted stages for run progress instead of streaming

**Status:** Accepted (Phase 5)

## Problem

A run takes one to three minutes on the free tier. The user needs to see
that something is happening and what, and the interface must survive a page
reload or a second browser tab.

## Alternatives

1. Block the HTTP request until the run finishes (the first ArguMind's
   Streamlit approach): no progress, one worker held per run, fragile through
   proxies.
2. Server-sent events or WebSockets streaming stage events from the worker.
3. **Return immediately and poll `GET /runs/{id}`**, which reads the stages
   the pipeline persists as each one completes.

## Decision

Option 3. `POST /runs` answers `202` with the queued run; the Ask page polls
every two seconds while the status is `queued` or `running`, renders the
completed stages with their summaries, lists the remaining ones as pending,
and switches to the answer tabs when the run finishes. `?wait=true` remains
for scripts.

## Reasoning

- The persisted stage records already exist for auditability; polling them
  costs one cheap query every two seconds per open page, against a stage
  cadence of several seconds. Nothing new has to be built or kept in sync.
- Progress is durable: reloading, sharing the run URL or opening it from the
  Runs page shows the same state, because the state lives in the database
  rather than in a connection.
- Streaming would require a channel from worker threads to request handlers,
  reconnection logic, and a fallback for proxies. That is real complexity for
  a two-second improvement in latency of feedback.

## Consequences

- Progress granularity is one stage; sub-stage progress (for example "source
  4 of 9 analysed") would need the extract node to write partial records.
- Many concurrent open pages would multiply small reads; a 30-second status
  cache exists for the system endpoint and the same pattern applies if needed.
