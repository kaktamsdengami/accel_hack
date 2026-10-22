# AGENTS.md: rules for every coding agent (Claude Code, Codex, Lovable, others)

You are joining a one-day hackathon build. Nobody on the team writes code by hand, and agents are
swapped when usage limits run out. **Everything you need is in this repo; do not rely on chat
history.**

## Read first, in this order

1. `docs/HANDOFF.md`: the live state (what is done, in progress, next, blocked).
2. `docs/PLAN.md`: product, architecture, resources, task map.
3. `docs/CONTRACTS.md` (after T01): data and API contracts. Do not change them without noting it
   in HANDOFF.md and the issue.
4. Your GitHub issue `[Txx]`: objective, dependencies, owned files, acceptance checks.

## Project in one line

Live sponsor-exposure measurement for football broadcasts: a deterministic pipeline plus agents
(router, memory curator, coverage detective, sponsor analyst) on Aiven data services.

## Stack (proposed; T01 ratifies)

- Python 3.12, FastAPI, Pydantic v2; workers as plain Python processes.
- Aiven: Kafka, ClickHouse, PostgreSQL + pgvector, Valkey; deploy on Aiven Runtime.
- Modal for GPU models (surface detector, embeddings, pitch keypoints).
- Anthropic API for vision and agents; OpenAI only for the second opinion.
- `web/` is built in Lovable (React); backend code does not live in `web/`.
- Tests: pytest. Lint: ruff. CI: GitHub Actions.

## Layout (target)

```
app/          FastAPI app and shared contracts (app/contracts.py)
workers/      ingest, router, aggregation, curator, detective
vision/       surface detector client, Claude/GPT identifiers, embedding reader
agents/       agent prompts and tool definitions
db/           migrations for ClickHouse, Postgres, Kafka topics, Valkey keys
eval/         harness, baselines, run records (eval/runs/), RESULTS.md
web/          Lovable dashboard
docs/         PLAN, CONTRACTS, HANDOFF, figures
```

## Working rules

- One issue = one branch `txx-short-name` = one PR that links the issue. Do not close issues;
  the human or the orchestrating session closes them after checking.
- Touch only the files your issue owns. Shared files (`app/contracts.py`, `docker-compose.yml`,
  CI, `AGENTS.md`, `docs/CONTRACTS.md`) need a note in the issue before editing.
- Commit small and push after every working step, including WIP. A cut-off session must lose
  minutes, not hours.
- Before you stop (done, blocked, or near a usage limit): update `docs/HANDOFF.md` and push.
- Secrets live in `.env` (git-ignored); keep `.env.example` current. Never commit keys,
  broadcast footage, the club's sponsor list or hand counts. Those live in `data/` (git-ignored).
- Every number in the README or pitch must come from a run record in `eval/runs/`. Mark demo
  content LIVE or RECORDED. Never invent results; a target is not a measurement.
- Product API spend: respect the per-match budget guard; log tokens and cost for every model call.
- Prefer deterministic code; use an LLM only where a decision under uncertainty is needed.

## Definition of done (every task)

Acceptance checks of the issue pass, with the exact commands and output written in the PR;
HANDOFF.md updated; branch pushed; PR open and linked.

## Handoff prompt (copy-paste for the next agent)

> Read AGENTS.md and docs/HANDOFF.md. Take issue Txx in kaktamsdengami/accel_hack-, work on branch
> txx-<name>, implement only that issue, meet its acceptance checks, update docs/HANDOFF.md, push,
> and open a PR linked to the issue. Do not close the issue.
