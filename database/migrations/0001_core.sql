-- ArguMind core schema: one row per pipeline run plus everything the run
-- produced, so any answer can be inspected and reproduced later.
-- Idempotent: safe to re-run on a volume that already has these objects.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'run_status') THEN
        CREATE TYPE run_status AS ENUM
            ('queued', 'running', 'answered', 'inconclusive', 'failed');
    END IF;
END
$$;

-- One pipeline execution for one question.
CREATE TABLE IF NOT EXISTS runs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id          TEXT,
    client_id           TEXT,
    question            TEXT NOT NULL CHECK (length(question) BETWEEN 1 AND 2000),
    question_hash       TEXT NOT NULL,
    status              run_status NOT NULL DEFAULT 'queued',
    outcome_reason      TEXT,
    answer              TEXT,
    confidence          NUMERIC(4, 3) CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
    iteration_count     INTEGER NOT NULL DEFAULT 0 CHECK (iteration_count >= 0),
    llm_calls           INTEGER NOT NULL DEFAULT 0 CHECK (llm_calls >= 0),
    input_tokens        INTEGER NOT NULL DEFAULT 0 CHECK (input_tokens >= 0),
    output_tokens       INTEGER NOT NULL DEFAULT 0 CHECK (output_tokens >= 0),
    estimated_cost_usd  NUMERIC(10, 6) NOT NULL DEFAULT 0 CHECK (estimated_cost_usd >= 0),
    latency_ms          INTEGER CHECK (latency_ms IS NULL OR latency_ms >= 0),
    error               TEXT,
    started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at         TIMESTAMPTZ,
    CHECK (finished_at IS NULL OR finished_at >= started_at)
);
CREATE INDEX IF NOT EXISTS runs_started_at_idx ON runs (started_at DESC);
CREATE INDEX IF NOT EXISTS runs_question_hash_idx ON runs (question_hash);
CREATE INDEX IF NOT EXISTS runs_status_idx ON runs (status);

-- Every stage the graph executed, including retries, with timing and usage.
CREATE TABLE IF NOT EXISTS run_stages (
    id              BIGSERIAL PRIMARY KEY,
    run_id          UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    attempt         INTEGER NOT NULL DEFAULT 1 CHECK (attempt >= 1),
    status          TEXT NOT NULL CHECK (status IN ('ok', 'failed', 'skipped', 'timeout')),
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    duration_ms     INTEGER CHECK (duration_ms IS NULL OR duration_ms >= 0),
    llm_calls       INTEGER NOT NULL DEFAULT 0,
    input_tokens    INTEGER NOT NULL DEFAULT 0,
    output_tokens   INTEGER NOT NULL DEFAULT 0,
    detail          JSONB NOT NULL DEFAULT '{}'::jsonb,
    error           TEXT
);
CREATE INDEX IF NOT EXISTS run_stages_run_id_idx ON run_stages (run_id, id);

-- Retrieved sources (papers, articles). source_id is deterministic:
-- the arXiv identifier, or a stable hash of the canonical URL.
CREATE TABLE IF NOT EXISTS sources (
    id                  BIGSERIAL PRIMARY KEY,
    run_id              UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    source_id           TEXT NOT NULL,
    kind                TEXT NOT NULL CHECK (kind IN ('arxiv', 'wikipedia', 'semantic_scholar', 'web')),
    title               TEXT NOT NULL,
    url                 TEXT NOT NULL,
    authors             JSONB NOT NULL DEFAULT '[]'::jsonb,
    published_year      INTEGER CHECK (published_year IS NULL OR published_year BETWEEN 1900 AND 2100),
    summary             TEXT NOT NULL DEFAULT '',
    authority           NUMERIC(3, 2) NOT NULL DEFAULT 0.50 CHECK (authority BETWEEN 0 AND 1),
    sub_question_index  INTEGER,
    retrieved_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, source_id)
);
CREATE INDEX IF NOT EXISTS sources_run_id_idx ON sources (run_id);

-- Claims extracted from sources. Stance is relative to the sub-question, which
-- is what makes disagreement detectable in the citation graph.
CREATE TABLE IF NOT EXISTS claims (
    id                  BIGSERIAL PRIMARY KEY,
    run_id              UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    claim_id            TEXT NOT NULL,
    source_id           TEXT NOT NULL,
    sub_question_index  INTEGER,
    text                TEXT NOT NULL,
    stance              TEXT NOT NULL CHECK (stance IN ('supports', 'refutes', 'neutral')),
    confidence          NUMERIC(4, 3) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    evidence_type       TEXT,
    UNIQUE (run_id, claim_id),
    FOREIGN KEY (run_id, source_id) REFERENCES sources (run_id, source_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS claims_run_id_idx ON claims (run_id);

-- Citation graph edges between sources, claims and sub-questions.
CREATE TABLE IF NOT EXISTS citation_edges (
    id              BIGSERIAL PRIMARY KEY,
    run_id          UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    source_node     TEXT NOT NULL,
    target_node     TEXT NOT NULL,
    edge_type       TEXT NOT NULL CHECK (edge_type IN ('supports', 'refutes', 'extends', 'supersedes')),
    weight          NUMERIC(4, 3) NOT NULL DEFAULT 1 CHECK (weight BETWEEN 0 AND 1),
    explanation     TEXT NOT NULL DEFAULT '',
    UNIQUE (run_id, source_node, target_node, edge_type)
);
CREATE INDEX IF NOT EXISTS citation_edges_run_id_idx ON citation_edges (run_id);

-- Cache of external source lookups, keyed by kind + normalised query.
CREATE TABLE IF NOT EXISTS source_cache (
    cache_key   TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    query       TEXT NOT NULL,
    payload     JSONB NOT NULL,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS source_cache_expires_at_idx ON source_cache (expires_at);

-- Every model call, for cost, latency and quota accounting.
CREATE TABLE IF NOT EXISTS llm_calls (
    id              BIGSERIAL PRIMARY KEY,
    run_id          UUID REFERENCES runs(id) ON DELETE CASCADE,
    stage           TEXT,
    purpose         TEXT NOT NULL,
    tier            TEXT NOT NULL,
    model           TEXT NOT NULL,
    ok              BOOLEAN NOT NULL,
    input_tokens    INTEGER NOT NULL DEFAULT 0,
    output_tokens   INTEGER NOT NULL DEFAULT 0,
    latency_ms      INTEGER NOT NULL DEFAULT 0,
    error           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS llm_calls_run_id_idx ON llm_calls (run_id);
CREATE INDEX IF NOT EXISTS llm_calls_created_at_idx ON llm_calls (created_at DESC);
