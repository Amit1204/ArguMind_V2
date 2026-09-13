# ADR-004: A small provider-agnostic model layer with a deterministic mock

**Status:** Accepted (Phase 2)

## Problem

Every pipeline stage that reasons needs a model call, and every one of those
calls must return structured data the next stage can trust. The first
ArguMind reached the model through LangChain wrappers whose failures were
silent: a prompt template swallowed JSON braces, a string parser returned
prose, a rate-limit helper slept for minutes inside a web request. Tests
could not run without a key.

## Alternatives

1. LangChain model wrappers and output parsers (as before).
2. Call the Gemini SDK directly from each stage.
3. **One `LLMProvider` interface** (`complete(LLMRequest) -> LLMResponse`) with
   two implementations, Gemini and a deterministic mock, behind a factory,
   plus a local daily budget and optional price table.

## Decision

Option 3, ported from the AI Business Analyst Copilot where it proved itself:

- `LLMRequest` carries system prompt, user prompt, routing tier (`fast` for
  extraction and classification, `standard` for synthesis), an optional
  Pydantic response schema, and a `purpose` string used for logging,
  accounting and mock routing.
- `GeminiProvider` asks for JSON mode with the schema, retries 429 and 5xx
  with jittered backoff, falls back one tier when a model stays unavailable,
  degrades gracefully when a model rejects the schema or thinking config, and
  maps every failure to a typed `LLMError` subclass.
- `LLMResponse.parse(schema)` validates with Pydantic and tolerates code
  fences; anything else is an `LLMResponseFormatError` the stage records.
- `MockProvider` derives plausible structured output from the prompt text
  alone (sentence split, negation words → `refutes`), so the pipeline runs
  in CI and on a laptop without a key or network. Tests script it per purpose
  or inject failures.
- `BudgetedProvider` consumes a `DailyRequestBudget` before each call so a
  loop cannot exhaust the free-tier quota; `UsageLedger` accumulates tokens
  and estimated cost per run.

## Reasoning

- Failures become data: every stage can record "model returned no valid
  JSON" instead of pretending.
- A single seam to swap providers, and one place for retries, fallback,
  budget and cost. Groq or another provider is one new class.
- The same design across both portfolio projects is easy to explain and
  shows it was a deliberate method.

## Consequences

- No LangChain runtime dependency at all; LangGraph is used only for the
  state machine (ADR-003).
- Circuit breakers around the provider are Phase 6; today a hard outage is
  retried a bounded number of times and then reported.
- Structured-output fidelity is bounded by the model; the extractor still
  deduplicates and caps claims, and assigns ids itself (ADR-005).
