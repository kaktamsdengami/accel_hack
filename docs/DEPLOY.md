# Deploy on Aiven Runtime

Live app (project `bogomolov-d9e7`, Runtime app `sponsorpulse`):
**https://01a16333-2589-7299-babc-33f2830bf66a-8000.eur-1.aiven.app**
(`/health`, `/matches`, `/matches/m-synthetic/report`, `/matches/m-synthetic/stream`, `/docs`).
While `DEMO_SYNTHETIC=1` it shows the INVENTED match `m-synthetic` (scripts/fake_stream.py): never quote it as a
measurement.

## What runs

| Piece | Where |
|---|---|
| Image | `Dockerfile` at the repo root (Runtime manifest = this Dockerfile; `EXPOSE 8000` is the HTTP port) |
| Entry | `python -m deploy.runtime`: uvicorn (API, main thread) + aggregation worker thread + optional SYNTHETIC demo thread |
| Plan | `startup-10-256` (smallest, 256 MB, $0.00959/h, ~$0.23/day), cloud `google-europe-west4` |
| Data | the existing Aiven services (PG, ClickHouse, Kafka, Valkey) via secret env vars; no Runtime service integrations, so those services are not modified |

One container on purpose: three Python processes do not fit in 256 MB; one process with threads uses
~100 MB (measured locally). If uvicorn exits the container exits and Runtime restarts it
(`restart_policy: any`); a crashed worker thread is restarted with back-off.

Runtime does not redeploy on `git push`; see Redeploy below.

## Environment variables

Set by `scripts/runtime_deploy.py` from `.env` and `data/certs/` (kind `secret` unless noted):

| Name | Value |
|---|---|
| `PG_URI`, `VALKEY_URI`, `KAFKA_BOOTSTRAP` | from `.env` (Runtime integration names `DATABASE_URL`, `VALKEY_URL`, `KAFKA_BOOTSTRAP_SERVERS` also work) |
| `CLICKHOUSE_HOST`, `CLICKHOUSE_PORT`, `CLICKHOUSE_USER`, `CLICKHOUSE_PASSWORD` | from `.env` |
| `KAFKA_CA_B64`, `KAFKA_CERT_B64`, `KAFKA_KEY_B64` | base64 of `data/certs/kafka-ca.pem`, `kafka-service.cert`, `kafka-service.key` |
| `DEMO_SYNTHETIC` (variable) | `1` loops the synthetic match (60 s pass + 10 s pause, then reset); `0` = API + worker only |
| `CORS_ORIGINS` (variable) | `*` |
| `AGG_GROUP` (variable) | `aggregate-runtime`: own Kafka consumer group, so a laptop worker (`aggregate`) does not steal its partitions |

Kafka certificates: Runtime env values are single-line (max 4096 chars), so the PEMs travel as base64.
At start-up `deploy/runtime.py` decodes them into a private temp dir (mode 600) and sets `KAFKA_CA_PATH`,
`KAFKA_CERT_PATH`, `KAFKA_KEY_PATH`. PEM text in `KAFKA_CA_CERT` / `KAFKA_ACCESS_CERT` / `KAFKA_ACCESS_KEY`
(the names a Runtime Kafka integration uses; literal `\n` allowed) works too. Nothing secret is in Git.

Optional demo knobs: `DEMO_MATCH_ID` (m-synthetic), `DEMO_DURATION_S` (60), `DEMO_SPEED` (1),
`DEMO_PAUSE_S` (10), `DEMO_START_DELAY_S` (15). `RUN_WORKER=0` disables the worker, `PORT` defaults to 8000.
Later (readers, agents): add `ANTHROPIC_API_KEY` etc. to `env_vars()` in `scripts/runtime_deploy.py`.

## Deploy / redeploy (API, no clicks; needs `AIVEN_TOKEN` + `AIVEN_PROJECT` in `.env`)

The repo is public, so Runtime clones it by URL: no GitHub app connection is needed.

```bash
python scripts/aiven_env.py --write                     # refresh .env + data/certs from Aiven (once)
python scripts/runtime_deploy.py                        # dry run: plan, price, env names
python scripts/runtime_deploy.py --create --branch main # create (BILLED per hour while powered on)
python scripts/runtime_deploy.py --status               # state, branch, public URL
python scripts/runtime_deploy.py --branch main          # switch branch (redeploys)
python scripts/runtime_deploy.py --update-env --demo 0  # replace env vars from .env (redeploys)
```

`--update-env` and `--branch` PUT the whole app config (env values read back from the API are redacted,
so they are always rebuilt from `.env`). The app currently tracks branch `t12-runtime-deploy`; after the
PR is merged run `python scripts/runtime_deploy.py --branch main`.

To deploy the latest commit of the same branch: console > project `bogomolov-d9e7` > **Runtime** > `sponsorpulse`
> **Overview** > **Actions** > **Deploy latest commit**. (No documented API call for this. Per the Aiven docs,
editing env vars redeploys the previously deployed commit; whether `--branch` builds the new branch's latest
commit is not verified yet.)

Logs: console > Runtime > `sponsorpulse` > **Logs**.
Power off after the hackathon: console > Runtime > `sponsorpulse` > **Actions** > **Power off app**
(powered-off apps are deleted after 180 days).

## Check it is up

```bash
U=https://01a16333-2589-7299-babc-33f2830bf66a-8000.eur-1.aiven.app
curl -s $U/health                                  # {"status":"ok","contracts":"1.0"}
curl -s $U/matches                                 # includes m-synthetic while DEMO_SYNTHETIC=1
curl -s $U/matches/m-synthetic/report              # clock_t grows 0 -> 60, then restarts
curl -sN "$U/matches/m-synthetic/stream?max_events=2"   # SSE stats events
```

## Local run of the same entrypoint

`docker compose up --build` (uses `.env` + `data/certs`), or without Docker:
`DEMO_SYNTHETIC=1 PORT=8765 AGG_GROUP=aggregate-local python -m deploy.runtime`.
Note: the deployed demo rewrites `m-synthetic` every 70 s; use another `--match-id` for local experiments.

## Known gaps (later T12 parts)

- The worker keeps match state in memory; after a restart it resumes from committed Kafka offsets with
  empty state, so a long real match would be re-aggregated only from new messages (acceptance check
  "restart does not lose data" is not met yet; rebuild from ClickHouse is the fix).
- The deployed worker also aggregates other agents' test matches on the shared Kafka topics (writes are
  idempotent, so this is harmless).
