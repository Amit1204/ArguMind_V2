# ADR-010: A small benchmark graded deterministically from the run's own records

**Status:** Accepted (Phase 7)

## Problem

"It seems to work" is not evidence. The system needs a repeatable way to
measure whether it answers settled questions, surfaces disagreement on
contested ones, refuses to invent evidence, resists instructions smuggled
into the question, and stays within cost bounds, and to detect regressions
when prompts, thresholds or models change.

## Decision

A benchmark of about thirty questions in seven categories (`settled`,
`contested`, `comparative`, `no_evidence`, `speculative`, `injection`,
`validation`), stored as YAML with typed expectations, run through the real
API (`POST /runs?wait=true`, one client id per case) and graded by
deterministic functions over the run detail the pipeline already persists:

| Dimension | What is checked |
|-----------|-----------------|
| outcome | HTTP status and run status among the expected ones |
| evidence | minimum sources and claims; required stances present |
| conflicts | a conflict was detected when expected, and every decided one kept a minority view |
| citations | the verifier removed nothing (no invented source) and answered runs carry a verified citation |
| answer | required phrases, forbidden phrases, confidence ceiling |
| safety | injected text absent from sub-questions and answer; a planted source id never accepted |
| cost | model calls and latency within bounds |

Reports (Markdown and JSON) are committed; each run is compared with the
previous `latest.json` to list regressions and fixes.

## Alternatives considered

- An LLM judge for answer quality: non-deterministic, costs budget on a free
  tier, and hard to audit. It could be added as one more dimension later.
- Reference answers with exact matching: research answers have no single
  correct text; the facts that can be checked are structural (evidence,
  stances, conflicts, citations, outcome).
- Grading from logs instead of the API: the API detail is the contract the UI
  uses, so grading it also tests the contract.

## Consequences

- The graders judge honesty and structure, not prose quality. A fluent wrong
  answer with a valid citation passes `citations` and can fail only if a
  content expectation catches it.
- Results depend on live sources: an arXiv outage turns contested questions
  into inconclusive ones, which the report shows as outcome and evidence
  failures with the caveats attached. The source cache makes repeat runs
  cheaper and more comparable.
- The mock provider passes plumbing checks only; the benchmark is meaningful
  against the real model, and the committed baseline says which it used.
