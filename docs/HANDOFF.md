# HANDOFF: live state of the build

Update this file before you stop working. Newest entry on top. Keep it short and factual.

## Current state

- Phase: **V0 setup.**
- Done: T00, T01 (#32), T03 (#31), T04 (#33, CI green). Issues T01–T30 on GitHub (T30 = submission form).
- In progress: none.
- Next: T05 (schemas), T10 (intervals), T20 (eval harness) in parallel worktrees. T13 can start in Lovable from `docs/api-mock.json`.
  Human: T02 (footage, sponsor list, hand count, permission).
- Blocked: T06/T07/T08 need footage from T02. T08 also needs the Anthropic API key (credits not
  arrived yet).
- Local setup: `python -m venv .venv`, then `pip install "psycopg[binary]" clickhouse-connect redis
  kafka-python python-dotenv pydantic pytest` (T04 will move this into pyproject). Venue Wi-Fi drops
  PyPI downloads: install one package at a time with retries.

## Log

### 2026-10-22: T10 part 1, pure aggregation logic (Claude Opus 5.5)
- Done: `workers/aggregate_logic.py` (intervals, brand/surface stats, pace, LED timeline, coverage
  ledger/summary) + `tests/test_intervals.py`. No DB, no Valkey, db/ untouched.
- Left for part 2 (after T05): write intervals/ledger to ClickHouse, Valkey live counters, wiring in
  `workers/aggregate.py`.
- Choices: `contracts` = dict brand_id -> target seconds per match; gap tolerance = 1 sample
  (kwarg); `BrandStats.seconds` includes replays (pacing excludes them); shirt label from
  `board_id="home:chest"`.
- Branch: t10-intervals-logic.

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
