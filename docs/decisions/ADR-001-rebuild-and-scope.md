# ADR-001: Rebuild ArguMind from scratch, with an explicit scope

**Status:** Accepted (Phase 1, 2026-09-13)

## Problem

The first ArguMind (github.com/Amit1204/ArguMind) demonstrated an appealing
idea: answer research questions with a citation graph that makes agreement and
disagreement explicit. A review of the code found that the idea was not
actually delivered:

- No code path ever created a `refutes` or `supersedes` edge, so conflict
  detection always returned nothing and the conflict resolver, minority
  reports and "inconclusive" reasoning about contradictions were dead paths.
- Claim ids came from the model as `claim_1`, `claim_2` per paper, so claims
  from different papers overwrote each other in the graph; web source ids used
  Python's `hash()`, which changes between processes.
- No tests, no CI, no evaluation, unpinned dependencies, a Streamlit UI that
  bypassed the API and rebuilt the whole pipeline per click, and a retry helper
  that slept for minutes inside a web request when a daily quota was exhausted.

## Alternatives

1. Fix the existing repository incrementally.
2. Rebuild in a new repository, keeping the ideas and salvaging the pieces that
   are sound (the citation-graph model, the arXiv and Wikipedia clients, the
   rate-limit hint parser as a test case), each rewritten with tests.
3. Abandon the project and present only the AI Business Analyst Copilot.

## Decision

Option 2. A fresh repository, built phase by phase to the same standard as the
copilot, with these scope decisions made by the owner:

- **No Hugging Face Spaces and no Streamlit.** The UI is a React SPA served by
  nginx; the API is FastAPI; the whole stack runs with `docker compose up`.
- **No cloud deployment.** Docker Compose on a single host is the deployment
  story, as for the copilot.
- **Gemini free tier** is the primary model provider, through a
  provider-agnostic layer with a mock implementation so tests and CI run
  without a key.
- **LangGraph stays** for the pipeline state machine (see ADR-003); LangChain
  chains and LangChain model wrappers do not.

## Reasoning

- Incremental fixes would keep the LangChain-shaped structure and unpinned
  dependencies that caused most of the problems, for a codebase of only three
  thousand lines. Rewriting is cheaper than untangling.
- A second project built to the same discipline shows that the copilot's
  quality was a method, not a one-off.
- Keeping the old repository untouched preserves an honest before-and-after.

## Consequences

- Nothing may be described as implemented unless it runs in Docker and has a
  test; docs distinguish implemented from designed (see `docs/specification.md`).
- The first three phases deliberately build no user-facing feature; they build
  the foundation the first version lacked.
