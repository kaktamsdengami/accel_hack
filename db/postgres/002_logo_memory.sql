-- {{EMBEDDING_DIM}} is substituted by scripts/migrate.py (constant EMBEDDING_DIM, default 768 = SigLIP).
-- The dimension of an existing column is NOT altered: changing it needs a re-embed and a manual migration.
CREATE TABLE IF NOT EXISTS logo_memory (
    id           bigserial PRIMARY KEY,
    club_id      text NOT NULL,
    stadium_id   text REFERENCES stadiums (stadium_id),
    brand_id     text NOT NULL REFERENCES brands (brand_id),
    embedding    vector({{EMBEDDING_DIM}}) NOT NULL,
    crop         bytea,
    source       text NOT NULL CHECK (source IN ('bootstrap', 'claude_confirmed', 'human')),
    status       text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'stale', 'conflict')),
    reason       text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    hits         integer NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS logo_memory_club_brand_idx ON logo_memory (club_id, brand_id, status);
CREATE INDEX IF NOT EXISTS logo_memory_embedding_idx
    ON logo_memory USING hnsw (embedding vector_cosine_ops)
