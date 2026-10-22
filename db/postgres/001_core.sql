CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS brands (
    brand_id text PRIMARY KEY,
    club_id  text NOT NULL,
    name     text NOT NULL,
    aliases  text[] NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS brands_club_idx ON brands (club_id);

CREATE TABLE IF NOT EXISTS stadiums (
    stadium_id text PRIMARY KEY,
    club_id    text,
    name       text NOT NULL,
    notes      text
);

CREATE TABLE IF NOT EXISTS board_maps (
    stadium_id    text NOT NULL REFERENCES stadiums (stadium_id),
    board_id      text NOT NULL,
    side          text NOT NULL CHECK (side IN ('near', 'far', 'left', 'right', 'unknown')),
    position_from double precision,
    position_to   double precision,
    notes         text,
    PRIMARY KEY (stadium_id, board_id)
);

CREATE TABLE IF NOT EXISTS contracts (
    contract_id    bigserial PRIMARY KEY,
    club_id        text NOT NULL,
    brand_id       text NOT NULL REFERENCES brands (brand_id),
    surface_type   text CHECK (surface_type IS NULL OR surface_type IN
                   ('led_perimeter', 'static_board', 'shirt', 'banner', 'backdrop', 'other')),
    target_seconds double precision NOT NULL CHECK (target_seconds >= 0)
);
-- surface_type NULL means "any surface"; coalesce makes NULL participate in uniqueness.
CREATE UNIQUE INDEX IF NOT EXISTS contracts_uniq
    ON contracts (club_id, brand_id, (coalesce(surface_type, '')));

CREATE TABLE IF NOT EXISTS labels (
    label_id     bigserial PRIMARY KEY,
    frame_id     text NOT NULL,
    bbox         jsonb NOT NULL,
    brand_id     text,
    surface_type text,
    labeller     text NOT NULL
);
CREATE INDEX IF NOT EXISTS labels_frame_idx ON labels (frame_id);

CREATE TABLE IF NOT EXISTS evidence (
    frame_id text PRIMARY KEY,
    match_id text NOT NULL,
    t        double precision NOT NULL,
    jpeg     bytea NOT NULL,
    width    integer NOT NULL,
    height   integer NOT NULL
);
CREATE INDEX IF NOT EXISTS evidence_match_t_idx ON evidence (match_id, t)
