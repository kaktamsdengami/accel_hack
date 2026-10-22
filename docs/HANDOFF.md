# HANDOFF: live state of the build

Update this file before you stop working. Newest entry on top. Keep it short and factual.

## Current state (updated 2026-10-22 ~13:00 by the orchestrator at 83% usage; merges at ~13:55 by a cloud session)

- Phase: **V1 slice + recognition.** Merged on main: T00, T01, T03, T04, T05, T06, T10 (both parts), T11,
  T12 part 1 (#40), T14 part 1 (#42), T20 part 1.
  CI green. Pipeline works live on Aiven with the SYNTHETIC match: `python scripts/fake_stream.py` ->
  `python -m workers aggregate` -> `API_MOCK=0 uvicorn app.main:app` (or `API_MOCK=1` for mock data).
- **Decisions:** vision provider = **OpenAI** (Anthropic credits may never arrive; `VISION_PROVIDER` switches).
  Reference ("gold") count = **independent labelling by Claude** at 1 fps (no club hand count exists) ->
  say "independent reference labelling", never "club hand count". Submission form drives deliverables (T30 #34):
  one public Google Slide is all that is shown on stage; 3-min MP4 demo.
- **In flight (agent worktrees, branches pushed when done; check `gh pr list`):**
  - T07 `t07-surfaces` (#8): OWLv2/GroundingDINO on Modal. **Blocked on GPU: Modal needs a payment method**
    (human adds card + claims $100); meanwhile CPU deploy, `SURFACES_GPU` env switches back to T4.
  - T08 `t08-vision-identify` (#9): OpenAI vision reader, `--discover` writes data/sponsors.draft.csv.
  - **T12 todo:** the Runtime app still tracks branch `t12-runtime-deploy`; someone with `.env` must run
    `python scripts/runtime_deploy.py --branch main` (the cloud session has no secrets).
  - T07/T08 branches were never pushed: if their worktrees are lost, restart them from the issues.
  - Reference labeller (no git): writes data/gold/reference_intervals.csv, sponsors.reference.csv, LABELLING.md.
- **Data (git-ignored):** data/videos/"5min .mp4" (club segment; note the space) and ucl.mp4 (top-league, internal
  tests only, never public). No sponsor list from the club yet.
- **Next after those land:** merge PRs (orchestrator may merge own PRs after green CI); wire real readers into the
  router (T07+T08+T09 memory reader with Modal embeddings + Keenable logo bootstrap); T15 curator; T20 part 2 baselines
  vs data/gold reference; T21 numbers; T17 analyst (OpenAI); T23 slide; T29 MP4; T30 submit.
- **Dashboard (T13):** Lovable repo `kaktamsdengami/screenshot-match` (branch main, synced by Lovable; do not edit
  it locally while Lovable works on it). Its `src/data/api-mock.json` is identical to `docs/api-mock.json`; every path
  in its `src/api.ts` exists in our API. Blocker for live mode: `Dashboard.tsx` hardcodes `MATCH_ID = "m-demo-1"`,
  the deployed API serves `m-synthetic` -> it must take the match from `GET /matches`. Live switch prompt for
  Lovable: see the log entry "Lovable live switch". The cloud session cannot reach `*.aiven.app` (proxy 403), so the
  deployed URL was not re-checked from there.
- Human: Modal billing; confirm sponsor draft; optional 10-frame spot check of the reference labels.
- Local setup: `.venv` with `pip install -e ".[dev,vision,llm,gpu]"`; Wi-Fi drops PyPI downloads, retry per package.

## Log

### 2026-10-22: Lovable live switch (cloud session, merges of #40/#42)
- Prompt for Lovable (after the Runtime app is redeployed from main, or now, it serves the same code):
  "Set VITE_API_BASE to https://01a16333-2589-7299-babc-33f2830bf66a-8000.eur-1.aiven.app and turn mock mode off.
  Do not hardcode MATCH_ID: load GET /matches and use the first match (or a picker). If match.title contains
  SYNTHETIC, show a SYNTHETIC badge instead of LIVE/RECORDED. Keep the MOCK DATA badge for mock mode. Keep all labels in English. Replace the broadcast photo placeholder with
  the evidence JPEG (GET /evidence/{frame_id}.jpg) of the frame being shown; when no frame_id is known, show a plain
  'no frame yet' panel, not a stock photo."
- Human decisions 2026-10-22: dashboard stays in English; broadcast panel shows pipeline evidence frames, not the club
  clip (no public-use permission yet). Gap: the API has no "latest frame" endpoint and the SYNTHETIC match writes no
  evidence rows, so the panel is empty on m-synthetic until a real ingest runs (or someone adds that endpoint).
- The numbers on the current Lovable screen (38 % Claude share, $0.41 of $3.00, ...) are MOCK values from
  docs/api-mock.json: never quote them as results.

### 2026-10-22: T06 live ingest worker (Claude Opus 5.5)
- Done: `vision/preprocess.py` (HSV-histogram shot cuts, overlay mask = configured rects + static regions learned across shots, pHash dedup groups, `is_replay` hook = False), `workers/ingest.py` (`python -m workers ingest --video V --match-id M [--speed 1] [--source RECORDED] [--overlay x,y,w,h] [--dry-run]`), `scripts/make_test_video.py` (generated test match, no real footage). Publishes `Frame` to Kafka `frames`; representative frames: <=640 px JPEG in Postgres `evidence`, masked full-res JPEG in `data/frames/<match_id>/<frame_no>.jpg` (ignored by version control) for the readers. Registers the Match in Valkey like fake_stream.
- Notes: frame_id = `<match>:<sample index>`, `t` = video clock of the decoded frame (25 fps source: 0.52 for the 0.5 s sample). Shots are only encoded in `shot_id` (no Shot topic). `is_representative` = first frame of a near-duplicate group. Only tested on generated video; thresholds (CUT_THRESHOLD 0.45, HASH_MAX_DIST 6, static std 9) need re-tuning on real footage. CI now installs `.[dev,vision]`; vision tests skip without cv2.
- Checks: `ruff check .` ok, `python -m pytest -q` 107 passed; `pytest tests/test_preprocess.py -m live` passed (10 s clip into real Kafka/Postgres/Valkey).
- Next: replay detector, real-footage tuning, reader consumers of `data/frames/`.

### 2026-10-22: T14 part 1, router policy + worker with stub readers (Claude Opus 5.5)
- Done: `vision/readers.py` (BrandReader / SurfaceDetector protocols, FrameGroup, Brand, ReaderResult, Stub*
  readers and SYNTHETIC fixtures), `agents/router_policy.py` (escalation memory -> Claude -> GPT, budget guard,
  per-stadium thresholds, `learn_thresholds` grid search for T15), `workers/router.py` (`python -m workers router`),
  `db/postgres/003_router_thresholds.sql` (applied on Aiven), Valkey key `sp:<m>:router` in db/valkey_keys.md.
- Checks: `ruff check .` clean; `python -m pytest -q` 118 passed; `-m live tests/test_router.py` 1 passed (20 frames ->
  10 decisions + 40 detections, $0.03 cap: 2 stub Claude calls then 8 budget_cap groups; stub prices, not spend).
- Not done (part 2): real readers (T07/T08/T09 implement the protocols, then add a `live` set in
  `workers/router.build_readers`); sharpness/replay are not in the contracts yet (see PR); a budget-capped group is
  unmeasured while boxes memory read confidently in it are still credited.
- Branch: t14-router.

### 2026-10-22: T12 part 1, deployed on Aiven Runtime with synthetic data (Claude Opus 5.5)
- Done: live at https://01a16333-2589-7299-babc-33f2830bf66a-8000.eur-1.aiven.app (Runtime app `sponsorpulse`,
  startup-10-256, google-europe-west4, $0.00959/h). `deploy/runtime.py` = one container: API + aggregation
  worker thread + `DEMO_SYNTHETIC=1` loop of the INVENTED `m-synthetic` (60 s pass, 10 s pause, state reset).
  `scripts/runtime_deploy.py` creates/updates the app via the Aiven API (public repo, so no GitHub app needed);
  secrets from `.env`, Kafka PEMs as base64 env vars. `docs/DEPLOY.md` has env list, redeploy, checks.
- Checks: deployed `/health` 200, `/matches` lists m-synthetic, report clock 8 -> 14 -> 20 s, SSE stats events.
  `ruff check .` clean, `pytest -q` 103 passed.
- Branch / PR: t12-runtime-deploy, PR #40. The app tracks branch t12-runtime-deploy: after merge run
  `python scripts/runtime_deploy.py --branch main`.
- Notes: `workers/aggregate.run` got an optional `agg=` argument (T10 file). Worker state is in memory, so
  "restart does not lose data" is NOT met yet. Power the app off after the hackathon.

### 2026-10-22: T10 part 2, aggregation worker on Aiven (Claude Opus 5.5)
- Done: `workers/aggregate.py` (`python -m workers aggregate`: Kafka detections/decisions/frames -> ClickHouse raw,
  intervals, coverage; Valkey snapshot `sp:<m>:stats`, pub/sub `sp:live:<m>`, decisions/alerts lists, match hash
  `sp:match:<m>`), `app/stores.py` (shared clients), `app/labels.py` (single `surface_label`, used by aggregate and
  eval `pred_label`), `scripts/fake_stream.py` (SYNTHETIC `m-synthetic`, invented data), tests incl. one live e2e.
- Choices: shots are not on Kafka, so they are derived from detections (is_replay always False until the router
  publishes shots); coverage covers elapsed seconds only; pace/alerts need `duration_s`; stale absorbed intervals are
  removed with ClickHouse lightweight DELETE; the pub/sub payload is the raw MatchReport JSON (as the API expects).
- Branch: t10-aggregate-worker. Contracts unchanged.

### 2026-10-22: T11 API (Claude Opus 5.5)
- Done: `app/api/{deps,routes,stream}.py`, routers included in `app/main.py`, `tests/test_api.py`. All CONTRACTS section 6
  endpoints, validated by contracts models. `API_MOCK=1` serves `docs/api-mock.json` (simulated SSE, clock_t +1 s/tick).
- Valkey inputs: report `sp:<id>:stats`, matches `sp:match:<id>` hash (fallback: match inside stats snapshots),
  pub/sub `sp:live:<id>` (stats events), list `sp:<id>:decisions` polled 1 s (decision events). Keep-alive every 15 s.
  ClickHouse: decisions, intervals, coverage. Postgres: `evidence`. `?max_events=N` closes the stream (curl/tests).
- Stubs: chat (answer says analyst arrives in T17), replay (202, `started: false`). Clients live in `app/api/deps.py`; swap for `app/stores.py` (T10 part 2).
- Not verified: alerts are not streamed (no `alert` events yet); brand pace/target come from the snapshot only.
- Branch: t11-api.

### 2026-10-22: T10 part 1, pure aggregation logic (Claude Opus 5.5)
- Done: `workers/aggregate_logic.py` (intervals, brand/surface stats, pace, LED timeline, coverage
  ledger/summary) + `tests/test_intervals.py`. No DB, no Valkey, db/ untouched.
- Left for part 2 (after T05): write intervals/ledger to ClickHouse, Valkey live counters, wiring in
  `workers/aggregate.py`.
- Choices: `contracts` = dict brand_id -> target seconds per match; gap tolerance = 1 sample
  (kwarg); `BrandStats.seconds` includes replays (pacing excludes them); shirt label from
  `board_id="home:chest"`.
- Branch: t10-intervals-logic.

### 2026-10-22: T05 data layer schemas (Claude Code)
- Done: `db/clickhouse/*.sql`, `db/postgres/*.sql`, `db/kafka_topics.py`, `db/valkey_keys.md`, `scripts/migrate.py`, tests.
  `python -m scripts.migrate` ran twice on Aiven without error (Kafka topics created via kafka-python admin, 1 partition).
- Notes: embedding dimension is the constant `EMBEDDING_DIM` in `scripts/migrate.py` (env override, default 768);
  an existing `logo_memory` column is not altered, so decide SigLIP vs CLIP before data is stored (else drop the table).
  ClickHouse tables are ReplacingMergeTree (dedupe on re-insert); views `brand_seconds` / `surface_seconds` are plain views using FINAL.
  Extra tables beyond the issue: ClickHouse `decisions`, Postgres `evidence`.
- Checks: `python -m pytest -q` 9 passed; `-m live` 1 passed; `ruff check .` clean.

### 2026-10-22: T20 part 1, metrics + harness (Claude Code)
- Done: eval/metrics.py (CONTRACTS section 7), eval/gold.py (intervals or totals CSV), eval/harness.py
  (run, `--compare`, `--reproduce`, optional `--chart`), eval/runs/README.md (formats), tests/test_metrics.py.
- Checks: `ruff check .` clean, `python -m pytest -q` 16 passed. CLI demo on synthetic fixtures only; no record committed.
- Branch: t20-eval-harness. Not done (part 2): baselines run, real records after T02 (gold, footage) and T14.
- Notes: sponsor list defaults to brands in gold (`--sponsors` extends it). Mixed gold (some totals) uses the totals variant.

### 2026-10-22: T01 contracts (Claude Code)
- Done: docs/CONTRACTS.md, app/contracts.py, docs/api-mock.json (invented mock numbers), tests.
- Decision: no images on Kafka (free plan throughput); evidence JPEGs in Postgres `evidence`.
- Checks: `.venv/Scripts/python -m pytest -q tests/test_contracts.py` → 3 passed.
- Next step: T04.

### 2026-10-22: T03 Aiven (Claude Code)
- Done: `scripts/aiven_env.py --write` fills `.env` + Kafka certs; `scripts/smoke_aiven.py` → all
  four services OK, pgvector 0.8.7 enabled. Gotcha: the service list API redacts passwords, use the
  detail call with `include_secrets=true` (the script does).
- Services: PG developer-1 fra, ClickHouse startup-8 GCP Paris (the only paid one, power off after
  the hackathon), Kafka free-0, Valkey free-1 ams. Runtime visible in the console, not tested yet.

### 2026-10-22: planning (orchestrator: Claude Code, desktop session)
- Wrote PLAN.md, AGENTS.md, CLAUDE.md, this file. Created labels, milestones M1–M4 and issues.
- Decisions: rewrite from scratch (earlier prototype is reference only); recognition by
  appearance (surface detector + embeddings + Claude vision), not OCR; per-surface metric v1 =
  type + side + LED rotation, v2 = board IDs via homography.
- Usage ladder for development: Claude subscription → $90 extra Claude usage in the app →
  Codex (OpenAI key) → Lovable for `web/`. Anthropic API $100 is reserved for the product.

## Template for a new entry

```
### <date time>: Txx <title> (<agent/model>)
- Done:
- Checks run (commands + result):
- Branch / PR:
- Next step:
- Blockers / notes:
```
