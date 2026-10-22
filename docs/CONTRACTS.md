# CONTRACTS v1

Shared shapes for every worker, agent, the API and the Lovable dashboard. Code version:
`app/contracts.py` (Pydantic v2). Sample payloads: `docs/api-mock.json`. A change here needs a
note in the issue and in `docs/HANDOFF.md`, and bumps `CONTRACTS_VERSION`.

Product name: **SponsorPulse** (working name).

## 1. Conventions

- **Time:** `t` = seconds from the start of the match video (float, match clock of the source).
  Wall-clock timestamps are UTC ISO 8601 and only used for latency.
- **Boxes:** `BBox` = normalised `x, y, w, h` in 0–1, origin top-left of the frame.
- **IDs:** strings. `match_id` like `m-<slug>`, `brand_id` like `b-<slug>`, frame ids
  `<match_id>:<frame_no>`.
- **Money:** USD floats. **Confidence:** 0–1, calibrated per reader in T14/T20.
- **Source label:** every match is `LIVE` or `RECORDED`; the dashboard always shows it.

## 2. Where data lives

| Data | Store | Why |
|---|---|---|
| `Frame`, `Detection`, `DecisionLog` events | Kafka topics `frames`, `detections`, `decisions` | Live stream between workers, agents and the dashboard. Small JSON only: the free Kafka plan has low throughput, **no images on Kafka**. |
| Evidence images (≤ 640 px JPEG of frames with detections) | Postgres `evidence` (bytea) | Needed by the dashboard, the detective and the analyst. |
| Detections, intervals, coverage, agent traces | ClickHouse | Per-second analytics and evaluation. |
| Brands, stadiums, board maps, contracts, logo memory (vectors), labels | Postgres + pgvector | Relational + similarity search. |
| Live counters, pacing | Valkey | Sub-second dashboard updates. |

## 3. Enums

- `Source`: `LIVE`, `RECORDED`
- `Camera`: `main`, `close`, `other`, `replay`
- `SurfaceType`: `led_perimeter`, `static_board`, `shirt`, `banner`, `backdrop`, `other`
- `Side`: `near`, `far`, `left`, `right`, `unknown`
- `Reader`: `memory`, `claude`, `gpt`, `ocr`, `own_detector`
- `CoverageStatus`: `measured`, `unmeasured`
- `UnmeasuredReason`: `no_frame`, `budget_cap`, `low_confidence`, `replay_excluded`, `reader_error`
- `PaceStatus`: `ahead`, `on_track`, `behind`, `no_target`
- `AlertKind`: `under_pace`, `budget`, `coverage_gap`, `conflict`

## 4. Pipeline records

| Model | Fields (type) | Produced by → consumed by |
|---|---|---|
| `Match` | match_id, club_id, stadium_id, title, source, duration_s?, sampled_fps, started_at | ingest → all |
| `Shot` | shot_id, match_id, t_start, t_end, camera, is_replay | ingest → router, aggregate |
| `Frame` | frame_id, match_id, shot_id, group_id, t, width, height, is_representative | ingest → surface detector, router |
| `SurfaceBox` | box_id, frame_id, bbox, surface_type, side, board_id?, score | surface detector → router |
| `Detection` | detection_id, match_id, frame_id, shot_id, t, bbox, surface_type, side, board_id?, brand_id? (null = unknown), confidence, reader, area_pct, center_dist, sharpness, occluded, below_threshold | router → aggregate, detective |
| `DecisionLog` | decision_id, match_id, shot_id, group_id, t, readers_tried, chosen_reader, confidence, cost_usd, latency_ms, reason, budget_left_usd | router → dashboard, eval |
| `Interval` | match_id, brand_id, surface_type, side, board_id?, t_start, t_end, duration_s, avg_area_pct, max_area_pct, avg_center_dist, is_closeup, is_replay | aggregate → API, analyst, eval |
| `CoverageSecond` | match_id, second, status, reason? | aggregate → detective, report |

Rules (implemented in T10):
- A brand's time is the **union** of its intervals across surfaces; per-surface time is counted
  separately, so per-surface totals can exceed the brand total.
- `below_threshold` = `area_pct < MIN_AREA_PCT` (default 0.1) or `sharpness < MIN_SHARPNESS`.
  Below-threshold detections are stored but **not credited**.
- `is_closeup` = `max_area_pct ≥ CLOSEUP_AREA_PCT` (default 3.0).
- Replays are credited separately (`is_replay`) and excluded from live pacing.

## 5. Dashboard and report records

| Model | Fields |
|---|---|
| `BrandStats` | brand_id, brand_name, seconds, pct_of_match, closeup_seconds, replay_seconds, avg_area_pct, surfaces, target_seconds?, projected_seconds?, pace |
| `SurfaceStats` | surface_label, surface_type, side, board_id?, brand_id, brand_name, seconds, pct_of_match, avg_area_pct, closeup_seconds |
| `LedSlot` | surface_label, brand_id, brand_name, t_start, t_end |
| `CoverageSummary` | measured_pct, measured_seconds, unmeasured_seconds, by_reason {reason: seconds} |
| `CostSummary` | usd_total, usd_per_match_minute, claude_call_share, calls_by_reader {reader: n}, budget_usd, budget_left_usd |
| `Alert` | alert_id, match_id, t, kind, message, brand_id? |
| `MatchReport` | match, clock_t, brands[], surfaces[], led_timeline[], coverage, cost, alerts[] |
| `LiveUpdate` (SSE) | event: `stats` \| `decision` \| `alert`, data: `MatchReport` \| `DecisionLog` \| `Alert` |
| `ChatRequest` | question |
| `Citation` | brand_id?, surface_label?, t_start, t_end, frame_id? |
| `ChatAnswer` | answer, citations[], sql[], not_measured_note? |

`surface_label` is human readable: `"LED perimeter · far"`, `"Board A · near"`, `"Home shirt · chest"`.

## 6. HTTP API (FastAPI, JSON, CORS open to the Lovable origin)

| Method | Path | Returns |
|---|---|---|
| GET | `/health` | `{"status": "ok", "contracts": "1.0"}` |
| GET | `/matches` | `Match[]` |
| GET | `/matches/{match_id}/report` | `MatchReport` (current state, also after the match) |
| GET | `/matches/{match_id}/brands/{brand_id}` | `{"brand": BrandStats, "surfaces": SurfaceStats[], "intervals": Interval[]}` |
| GET | `/matches/{match_id}/stream` | SSE of `LiveUpdate`; `stats` about once per second, `decision`/`alert` as they happen |
| GET | `/matches/{match_id}/decisions?limit=50` | `DecisionLog[]` newest first |
| GET | `/matches/{match_id}/coverage` | `CoverageSecond[]` (compressed into runs is allowed later) |
| GET | `/evidence/{frame_id}.jpg` | JPEG |
| POST | `/matches/{match_id}/chat` | body `ChatRequest` → `ChatAnswer` |
| POST | `/matches/{match_id}/replay` | start replaying a recorded segment as a live stream (admin) |

## 7. Metrics (definitions used by T20/T21 and the pitch)

Gold = the club's hand count: per brand, a list of visible intervals (or totals only).

- **Capture %** = Σ_brands overlap(pred intervals, gold intervals) / Σ_brands gold seconds.
  If gold has totals only: Σ min(pred_s, gold_s) / Σ gold_s (stated as the weaker variant).
- **False-credit %** = Σ pred seconds not overlapping gold / Σ pred seconds (intervals gold only).
- **MAE s/brand** = mean over sponsor-list brands of |pred_s − gold_s|.
- **Per-surface error** = same MAE computed per (surface_label, brand).
- **Cost per match-minute** = (model API spend + Modal GPU spend) / processed match minutes.
- **Claude-call share** = groups read by `claude` / groups read by any reader.
- **Latency** = wall time from `Frame` published to the `stats` SSE that includes it; p50 and p95.
- Evaluation runs on the held-out window only; tuning never uses it.

## 8. Kafka messages

JSON, UTF-8, key = `match_id`. Topic → model: `frames` → `Frame`, `detections` → `Detection`,
`decisions` → `DecisionLog`. Consumers must ignore unknown fields (forward compatible).

## 9. Config defaults (env, overridable)

`SAMPLED_FPS=2`, `MIN_AREA_PCT=0.1`, `MIN_SHARPNESS=50`, `CLOSEUP_AREA_PCT=3.0`,
`MATCH_BUDGET_USD=3`, `MEMORY_MAX_DISTANCE` and `MEMORY_MIN_MARGIN` learned in T14/T15.
