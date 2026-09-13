# ADR-003: LangGraph for the state machine, no LangChain chains

**Status:** Accepted (Phase 1, implemented from Phase 4)

## Problem

The pipeline is a graph with a loop: plan, decompose, gather, extract, build
the citation graph, resolve conflicts, cluster, synthesise, criticise, and
either retry gathering or answer. The copilot implemented its orchestrator by
hand. The first ArguMind used LangGraph for the graph and LangChain for
prompts, output parsing and model access, and most of its fragility came from
the LangChain layer: prompt templates that swallowed JSON braces, string
parsers that failed silently, provider wrappers that hid errors.

## Alternatives

1. Hand-written orchestrator, as in the copilot.
2. LangGraph `StateGraph` for control flow, with plain Python nodes that call
   our own model layer and Pydantic-validated structured output.
3. LangGraph plus LangChain chains and model wrappers, as before.
4. Another agent framework.

## Decision

Option 2. LangGraph owns the state, edges, conditional retry edge and
per-run execution; every node is an ordinary function that uses the
project's own `llm` package (Gemini or mock) and returns typed state
updates. No `langchain` prompt templates, output parsers or `Chat*` wrappers.

## Reasoning

- The state machine is exactly what LangGraph is good at: typed state,
  reducers for accumulating fields, conditional edges, bounded loops, and a
  visualisable graph. Writing that by hand again would add nothing new to the
  portfolio, and using it here gives a contrast with the copilot's approach
  that is worth discussing.
- Structured output belongs to the model layer: the Gemini SDK returns JSON
  matching a schema, Pydantic validates it, and a parse failure is a recorded
  stage error. That removes the class of bugs the previous version had.
- The framework surface is kept to one package with one job, so a version
  bump or replacement touches one module.

## Consequences

- LangGraph is a pinned dependency from Phase 4; its version is recorded in
  `backend/requirements.txt` when installed.
- Nodes must stay framework-agnostic: they take and return plain data, so
  they are unit-tested without LangGraph.
- Because the mock model layer is deterministic, the whole graph runs in CI
  without a key.
