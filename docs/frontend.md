# Frontend (Phase 5)

A React + Vite + TypeScript single-page app served by nginx, which also
proxies `/api`, `/health` and `/ready` to the backend so the browser stays
same-origin. No component library, no chart or graph library: the citation
graph is hand-drawn SVG, the answer renderer handles the Markdown subset the
model is asked to produce, and the whole bundle stays small and auditable.

## Pages

| Route | Page | What it shows |
|-------|------|---------------|
| `/` | Overview | readiness checks, system status (schema, provider, runs by status), the five latest runs |
| `/ask` | Ask | question box with example chips; after submit, the run's stages appear as they complete (polled every 2 s), then the tabs below |
| `/runs` | Runs | paginated history: question, status, confidence, time, model calls, started |
| `/runs/:id` | Run | the same tabbed view as Ask for any persisted run |

## The run view

| Tab | Content |
|-----|---------|
| Answer | status, confidence, time, model calls, tokens, iterations; the answer with every verified `[source_id]` citation rendered as a chip linking to the source (an unknown id is shown in red, which cannot happen after verification but is handled); caveats; consensus (overall, strength, agreements, disagreements, open questions); critic verdict with its issues; citation verification summary |
| Evidence | sub-questions; sources table (id, title with link, kind, year, authority, sub-question); claims grouped as supporting / refuting / neutral with source, evidence type and confidence; conflicts with a support-versus-refute bar, margin, method, reasoning and the minority view; topic clusters with contested flags |
| Graph | layered SVG: sub-questions left, claims centre coloured by stance, sources right; `supports` green and `refutes` red edges to the sub-question, `extends` dashed grey and `supersedes` orange arcs between claims, light provenance lines from source to claim; click a node to focus its neighbourhood; hover for full text |
| Stages | timeline of every stage with status, a one-line result summary, duration and model calls; while a run executes, the remaining stages are listed as pending and the current one pulses |

## Design choices

- **Polling, not streaming.** `POST /runs` returns at once; the page polls
  `GET /runs/{id}` while the status is queued or running. Stages are persisted
  as they finish, so progress is real, survives a page reload, and needs no
  WebSocket or SSE infrastructure (ADR-008).
- **Nothing is invented in the UI.** Every number, badge and label comes
  from the run detail; the UI never derives a verdict of its own.
- **Honest placeholders.** During Phase 1-4 the Ask and Runs routes said what
  was planned and when, instead of showing a fake screen.
- **Dependency-free graph.** A deterministic layered layout is enough for the
  tens of nodes a run produces, renders identically everywhere and needs no
  physics simulation to settle.

## Development

The image builds the bundle with `npm ci && npm run build` (which type-checks
first) and serves it with nginx. For hot reload on the host, `npm run dev`
proxies API calls to `localhost:8100`; this is optional, nothing requires Node
on the host. There are no frontend unit tests; type-checking in CI and the
manual walkthrough in `docs/demo.md` (Phase 8) are the verification.
