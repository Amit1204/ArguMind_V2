# Interview questions and answers

Questions an interviewer is likely to ask about ArguMind, with the answers
the code supports and where to look.

## The rebuild

**Why did you rebuild your own project from scratch?**
Because reviewing it honestly showed that its headline feature never ran: no
code path created a `refutes` edge, so the conflict resolver, minority
reports and "I don't know" logic were dead. It also had no tests, no CI, no
evaluation, colliding claim ids and unpinned dependencies. Fixing that in
place would have meant fighting the framework-shaped structure that caused
most of it. The rebuild kept the ideas and delivered them with the same
discipline as the copilot project: phases, tests, honest docs (ADR-001).

**What was the single most important design change?**
Making stance a first-class field on every claim and turning it into
`supports` and `refutes` edges to the sub-question. Conflicts are then
detected by construction: a question with both kinds of edge from different
sources is a conflict, and the tests assert that the graph really contains
`refutes` edges (ADR-005, ADR-006).

## Architecture

**Why LangGraph, and why not LangChain?**
LangGraph is used for exactly one thing it is good at: a typed state machine
with a conditional retry edge. Every node is a plain function tested without
the framework. LangChain chains and model wrappers were where the first
version's silent failures lived, so model access goes through a small
provider interface with structured output validated by Pydantic; any parse
failure is a recorded stage error (ADR-003, ADR-004).

**Walk me through one run.**
Plan splits the question into sub-questions; gather searches arXiv and
Wikipedia per sub-question through a cached, throttled, circuit-protected
service; extract asks the model for claims with stance per source; build
graph and resolve conflicts score both sides and ask the model to arbitrate
only when the margin is close; cluster groups related claims and adds
`extends` edges; consensus summarises; the rule-based critic passes, retries
once with a broadened search, or declares the evidence inconclusive; answer
writes the cited Markdown; verify removes any citation not retrieved in this
run. Every stage is persisted as it completes, so the UI polls progress
(`docs/pipeline.md`).

**Why is the critic rule-based rather than a model?**
It decides sufficiency, not quality: enough claims taking a position from
enough independent sources, a consensus formed, conflicts not all unresolved.
Rules make that explainable ("only 1 source provides evidence, need 2"),
reproducible for the benchmark, and free (ADR-007).

**How do you stop the answer from citing papers that do not exist?**
The answer may only cite ids listed in the prompt; the verifier then checks
every `[source_id]` against the sources retrieved in that run, rewrites
claim-id citations to their source, removes anything else and records what it
removed. An answer with no verifiable citation is flagged and its confidence
halved. The benchmark's citation dimension fails if anything was removed.

**Why PostgreSQL for a graph?**
The graphs are tens of nodes; NetworkX handles the algorithms in memory and
the database stores runs, stages, sources, claims and edges with constraints
that enforce the invariants (stance values, edge types, confidence ranges,
a claim must reference a source from the same run). One datastore, one
backup, the same reasoning as the copilot (ADR-002).

## Reliability and operations

**What happens when arXiv throttles you?**
Within a run: the failure becomes a caveat, the other source continues, and
a source that failed once is not tried again in that run; gathering may use
at most half the time budget. Across runs: three consecutive failures open a
circuit for two minutes and the next runs skip arXiv in milliseconds with a
caveat that says so. I measured it: gather went from 95.6 s to 0.77 s
(`docs/observability.md`, ADR-009).

**And when the model quota is gone?**
The local daily budget refuses first; the model circuit opens after
consecutive provider failures; plan, consensus and answer fall back to
deterministic templates, stages are marked failed with caveats, and the run
ends `inconclusive` rather than with a 500 or a fabricated answer. There is a
test for each of those drills.

**How would you find out why a specific answer was weak?**
Open the run: every stage's detail, the sources, claims with stance, the
conflict resolutions with their reasoning, the critic's issues and the
caveats are persisted. The request id in the response header matches the
JSON log lines and the error envelope.

## Evaluation

**How do you know it works?**
A 30-case benchmark graded deterministically from the run's own records:
outcome, evidence, conflicts surfaced with a minority view, citation
fidelity, answer text, injection resistance, cost. Reports are committed
with regressions against the previous run. The harness is tested; the first
baseline against the real model and live arXiv is recorded in
`docs/evaluation.md` once run (ADR-010).

**What can the benchmark not tell you?**
Prose quality. A fluent wrong answer with a real citation passes the
structural dimensions. An LLM-judged dimension is the obvious extension, kept
out for cost and determinism.

## Trade-offs and next steps

**What is deliberately not built?**
Authentication, TLS, shared breaker state across replicas, a persistent job
queue, sub-stage checkpoints, cloud deployment. Each is listed with the
reason in the README's limitations and `docs/security.md`.

**What would you do first with another week?**
Record the baseline and fix what it finds; add a relevance filter for
off-topic Wikipedia pages (they cost model calls and add nothing); resolve
conflicts within topic clusters, not only per sub-question; add
authentication before letting anyone else reach the ports.
