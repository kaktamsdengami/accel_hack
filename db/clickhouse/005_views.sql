-- Credited detections only: brand known and not below threshold. Plain views (computed at query time)
-- so they stay correct when detections are re-inserted; FINAL collapses duplicates.
-- Per brand-second: credited detections of a brand in each second.
CREATE VIEW IF NOT EXISTS sponsorpulse.brand_seconds AS
SELECT
    match_id,
    brand_id,
    toUInt32(floor(t)) AS second,
    count() AS detections,
    max(area_pct) AS max_area_pct,
    avg(area_pct) AS avg_area_pct
FROM sponsorpulse.detections FINAL
WHERE brand_id IS NOT NULL AND NOT below_threshold
GROUP BY match_id, brand_id, second;

-- Per surface-second: same, split by surface (type, side, board).
CREATE VIEW IF NOT EXISTS sponsorpulse.surface_seconds AS
SELECT
    match_id,
    brand_id,
    surface_type,
    side,
    ifNull(board_id, '') AS board_id,
    toUInt32(floor(t)) AS second,
    count() AS detections,
    max(area_pct) AS max_area_pct,
    avg(area_pct) AS avg_area_pct
FROM sponsorpulse.detections FINAL
WHERE brand_id IS NOT NULL AND NOT below_threshold
GROUP BY match_id, brand_id, surface_type, side, board_id, second
