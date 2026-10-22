# Valkey key layout

Valkey holds live, disposable state for the dashboard. Everything can be rebuilt from ClickHouse, so
no key is a source of truth. All keys are prefixed `sp:`; `{match}` = `match_id`, `{brand}` = `brand_id`.
Time is in match-clock seconds (see docs/CONTRACTS.md).

| Key | Type | Fields / value | Writer -> reader |
|---|---|---|---|
| `sp:meta:schema_version` | string | migration version, set by `scripts/migrate.py` | migrate |
| `sp:{match}:clock` | string | latest match-clock `t` processed (float) | aggregation -> API |
| `sp:{match}:brands` | set | brand_ids seen in this match | aggregation -> API |
| `sp:{match}:brand:{brand}` | hash | `seconds`, `closeup_seconds`, `replay_seconds`, `area_sum`, `area_n`, `surfaces` | aggregation -> API |
| `sp:{match}:surface:{brand}` | hash | field `<surface_type>\|<side>\|<board_id or ->` -> credited seconds | aggregation -> API |
| `sp:{match}:coverage` | hash | `measured_s`, `unmeasured_s`, `unmeasured:<reason>` -> seconds | aggregation -> API |
| `sp:{match}:pace:{brand}` | hash | `target_seconds`, `projected_seconds`, `status` (ahead/on_track/behind/no_target) | analyst -> API |
| `sp:{match}:budget` | hash | `limit_usd`, `spent_usd`, `left_usd`, `calls:<reader>` -> int | router -> API |
| `sp:{match}:alerts` | list | JSON `Alert`, newest first, `LTRIM` to 100 | agents -> API |
| `sp:{match}:decisions` | list | JSON `DecisionLog`, newest first, `LTRIM` to 200 | router -> API |
| `sp:{match}:stats` | string | latest `MatchReport` JSON (TTL 1 h) for SSE `stats` | aggregation -> API |

Rules:
- Counters use `HINCRBYFLOAT` / `HINCRBY`, so workers can run in parallel.
- Per-match keys get `EXPIRE` of 7 days at match creation; `sp:meta:*` never expires.
- Values are plain UTF-8; JSON values follow `app/contracts.py`.
