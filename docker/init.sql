-- ai-dev-swarm — initial schema
--
-- Mounted into the Postgres container at /docker-entrypoint-initdb.d/.
-- Idempotent: every CREATE uses IF NOT EXISTS so a re-run is a no-op.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS projects (
    id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    name          text        NOT NULL,
    spec          jsonb       NOT NULL,
    state         text        NOT NULL,
    github_repo   text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS projects_state_idx ON projects (state);

CREATE TABLE IF NOT EXISTS milestones (
    id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id    uuid        NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    ordinal       integer     NOT NULL,
    title         text        NOT NULL,
    spec          jsonb       NOT NULL,
    state         text        NOT NULL,
    retry_count   integer     NOT NULL DEFAULT 0,
    commit_hash   text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, ordinal)
);

CREATE INDEX IF NOT EXISTS milestones_project_idx ON milestones (project_id);
CREATE INDEX IF NOT EXISTS milestones_state_idx   ON milestones (state);

CREATE TABLE IF NOT EXISTS token_log (
    id            bigserial   PRIMARY KEY,
    project_id    uuid        REFERENCES projects   (id) ON DELETE SET NULL,
    milestone_id  uuid        REFERENCES milestones (id) ON DELETE SET NULL,
    role          text        NOT NULL,
    model         text        NOT NULL,
    input_tokens  integer     NOT NULL,
    output_tokens integer     NOT NULL,
    cost_usd      numeric(10, 4) NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS token_log_project_idx ON token_log (project_id);
CREATE INDEX IF NOT EXISTS token_log_day_idx     ON token_log ((created_at::date));

-- Idea-level dedup memory (pgvector). 1536 dims = OpenAI / Anthropic
-- v3-text-embedding compatible; adjust if a different model is used.
CREATE TABLE IF NOT EXISTS idea_embeddings (
    project_id    uuid        PRIMARY KEY REFERENCES projects (id) ON DELETE CASCADE,
    embedding     vector(1536) NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idea_embeddings_cosine_idx
    ON idea_embeddings USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);
