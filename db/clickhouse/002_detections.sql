-- Detection (app/contracts.py). bbox is flattened. Re-inserting the same detection_id is deduplicated
-- at merge time (ReplacingMergeTree); views below use FINAL so they are correct before merges.
CREATE TABLE IF NOT EXISTS sponsorpulse.detections
(
    detection_id    String,
    match_id        String,
    frame_id        String,
    shot_id         String,
    t               Float64,
    bbox_x          Float32,
    bbox_y          Float32,
    bbox_w          Float32,
    bbox_h          Float32,
    surface_type    LowCardinality(String),
    side            LowCardinality(String),
    board_id        Nullable(String),
    brand_id        Nullable(String),
    confidence      Float32,
    reader          LowCardinality(String),
    area_pct        Float32,
    center_dist     Float32,
    sharpness       Float32,
    occluded        Bool,
    below_threshold Bool,
    ingested_at     DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(ingested_at)
ORDER BY (match_id, t, detection_id);

CREATE TABLE IF NOT EXISTS sponsorpulse.decisions
(
    decision_id     String,
    match_id        String,
    shot_id         String,
    group_id        String,
    t               Float64,
    readers_tried   Array(LowCardinality(String)),
    chosen_reader   LowCardinality(String),
    confidence      Float32,
    cost_usd        Float64,
    latency_ms      Float64,
    reason          String,
    budget_left_usd Float64,
    ingested_at     DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(ingested_at)
ORDER BY (match_id, t, decision_id)
