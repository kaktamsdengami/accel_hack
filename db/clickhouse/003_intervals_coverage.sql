-- Interval (app/contracts.py). board_id is stored as '' when there is no board (keeps the sort key non-null).
CREATE TABLE IF NOT EXISTS sponsorpulse.intervals
(
    match_id        String,
    brand_id        String,
    surface_type    LowCardinality(String),
    side            LowCardinality(String),
    board_id        String DEFAULT '',
    t_start         Float64,
    t_end           Float64,
    duration_s      Float64,
    avg_area_pct    Float32,
    max_area_pct    Float32,
    avg_center_dist Float32,
    is_closeup      Bool,
    is_replay       Bool,
    computed_at     DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(computed_at)
ORDER BY (match_id, brand_id, surface_type, side, board_id, t_start);

-- CoverageSecond (app/contracts.py). One row per (match, second); latest write wins.
CREATE TABLE IF NOT EXISTS sponsorpulse.coverage
(
    match_id    String,
    second      UInt32,
    status      LowCardinality(String),
    reason      LowCardinality(Nullable(String)),
    computed_at DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(computed_at)
ORDER BY (match_id, second)
